"""Cross-check the computer in a second simulator: Yosys `sim` running the Verilog that
`alchitry build` generated (build/source/*.v).

Why: the Alchitry simulator (2.0.57) doesn't update every flip-flop on a clock edge at once.
It works through the instances in a name-dependent order, so a flip-flop in one instance can
sample another instance's value from after the edge (see notes 2026-10-15). A passing cpu_tb
can therefore hide a timing bug, and a failing one can be a simulator artefact. Yosys's
simulator updates all flip-flops together, and it runs the hardware Verilog, block RAM
included (SIM_RAM = 0).

For every program in tools/programs.py, the computer is wrapped in a small Verilog harness:
reset for the first two clocks (like cpu_tb), debug_sel stepping through the registers, and
io_clk = clk with the testbenches' UART timing. Then:
  - lockstep programs: control state and pc after every clock against rv32sim, at this core's
    clock counts (2, store 3, load 4), and the final register file;
  - programs that print: run to halt, decode uart_tx here (8 clocks per bit), compare the bytes
    and the final register file.

Usage: python3 tools/xsim_computer.py [VERILOG_DIR]
  Default build/source, after `alchitry build` (it refuses stale Verilog). Another directory
  (e.g. a mutated copy, to check this script can fail) is used as is.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import asm
import gen_cpu_tb
import programs
import rv32sim

ROOT = Path(__file__).resolve().parent.parent
YOSYS = "/usr/lib/alchitry/alchitry-labs/lib/app/tools/oss-cad-suite/bin/yosys"
VERILOG = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "source"
STATE = {"FETCH": 0, "EXECUTE": 1, "MEM_ACCESS": 2, "WRITEBACK": 3}   # control_unit's enum order
BIT = gen_cpu_tb.TB_UART_CLK_FREQ // gen_cpu_tb.TB_UART_BAUD
SIGNALS = ["cycle", "debug_pc", "debug_state", "debug_reg", "uart_tx"]

HARNESS = f"""
module xsim_top #(parameter PROGRAM_ID = 0) (
    input clk,
    output [15:0] cycle_out,
    output [31:0] debug_pc,
    output [1:0] debug_state,
    output [31:0] debug_reg,
    output uart_tx
  );
  reg [15:0] cycle = 0;
  always @(posedge clk) cycle <= cycle + 1;
  assign cycle_out = cycle;
  computer #(.PROGRAM_ID(PROGRAM_ID), .SIM_RAM(0), .UART_CLK_FREQ({gen_cpu_tb.TB_UART_CLK_FREQ}),
             .UART_BAUD({gen_cpu_tb.TB_UART_BAUD})) dut (
    .clk(clk), .rst(cycle < 2), .io_clk(clk), .io_rst(cycle < 2), .debug_sel(cycle[3:0]),
    .uart_tx(uart_tx), .debug_pc(debug_pc), .debug_reg(debug_reg), .debug_state(debug_state));
endmodule
"""


def check_fresh():
    newest = max(p.stat().st_mtime for p in (ROOT / "source").rglob("*") if p.suffix in (".luc", ".v"))
    if not (VERILOG / "computer.v").exists() or (VERILOG / "computer.v").stat().st_mtime < newest:
        sys.exit("build/source is older than source/: run `alchitry build` first")


def run(pid, clocks):
    """Simulate; return one dict of SIGNALS per clock, sampled just after each rising edge."""
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "xsim_top.v"
        harness.write_text(HARNESS)
        vcd = Path(tmp) / "out.vcd"
        files = " ".join(str(p) for p in sorted(VERILOG.glob("*.v"))) + f" {harness}"
        r = subprocess.run([YOSYS, "-q", "-p",
                            f"read_verilog -sv {files}; chparam -set PROGRAM_ID {pid} xsim_top; "
                            f"hierarchy -top xsim_top; proc; flatten; opt_clean; "
                            f"sim -clock clk -n {clocks} -vcd {vcd}"],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(r.stdout + r.stderr)
        return parse_vcd(vcd.read_text())


def parse_vcd(text):
    ids, values, samples = {}, {}, []
    lines = iter(text.splitlines())
    scope = []
    for line in lines:            # header: top-level signal ids
        w = line.split()
        if not w:
            continue
        if w[0] == "$scope":
            scope.append(w[2])
        elif w[0] == "$upscope":
            scope.pop()
        elif w[0] == "$var" and len(scope) == 1:
            name = "cycle" if w[4] == "cycle_out" else w[4]
            if name in SIGNALS or name == "clk":
                ids[w[3]] = name
        elif w[0] == "$enddefinitions":
            break
    rose = False
    for line in lines:
        if line.startswith("#"):
            if rose:
                samples.append(dict(values))
            rose = False
            continue
        if line.startswith("b"):
            v, i = line[1:].split()
        elif line and line[0] in "01xz":
            v, i = line[0], line[1:]
        else:
            continue
        if i in ids:
            name = ids[i]
            val = int(v.replace("x", "0").replace("z", "0"), 2)
            if name == "clk" and val and not values.get("clk"):
                rose = True
            values[name] = val
    if rose:
        samples.append(dict(values))
    return samples


def expected_trace(name, words):
    """[(state, pc or None)] per clock from the release of reset, and the model at the end."""
    model = rv32sim.Cpu(words)
    trace = [("FETCH", 0)]
    steps, halted_for = 0, 0
    while True:
        word = words.get(model.pc, asm.NOP)
        model.step()
        steps += 1
        states = gen_cpu_tb.STATES[gen_cpu_tb.clocks_for(word)]
        trace += [(s, None) for s in states[:-1]] + [(states[-1], model.pc)]
        if name == "LARSON":
            if steps == gen_cpu_tb.LARSON_STEPS:
                return trace, model
        elif word == asm.HALT:
            halted_for += 1
            if halted_for == 2:
                return trace, model


def final_regs(samples, model):
    """Compare x0..x15 from the last 16 clocks (debug_sel = cycle[3:0])."""
    errors = []
    for s in samples[-16:]:
        r = s["cycle"] & 15
        if s["debug_reg"] != model.x[r]:
            errors.append(f"x{r}: got {s['debug_reg']:08x} want {model.x[r]:08x}")
    return errors


def check_lockstep(pid, name, words):
    trace, model = expected_trace(name, words)
    samples = run(pid, len(trace) + 1 + 16)[1:]     # [0] is after the first edge, still in reset
    errors = []
    for i, (state, pc) in enumerate(trace):
        s = samples[i]
        if s["debug_state"] != STATE[state] or (pc is not None and s["debug_pc"] != pc):
            errors.append(f"clock {i}: state {s['debug_state']} pc {s['debug_pc']:08x}, "
                          f"want {STATE[state]} ({state}) {'' if pc is None else f'{pc:08x}'}")
            break
        if s["uart_tx"] != 1:
            errors.append(f"clock {i}: uart_tx low in a program that doesn't print")
            break
    if name != "LARSON":     # halted: pc and registers stay put while debug_sel walks them
        errors += final_regs(samples[len(trace):], model)
    return errors, f"{len(trace)} clocks"


def check_uart(pid, name, words):
    model = gen_cpu_tb.run_to_halt(words)
    want = model.uart_out
    clocks = len(want) * gen_cpu_tb.UART_CLOCKS_PER_BYTE * 3 + 1000
    samples = run(pid, clocks)
    got, i = [], 0
    line = [s["uart_tx"] for s in samples]
    while i < len(line) - 10 * BIT:          # start bit: sample each bit in its middle
        if line[i] == 0 and i > 2:
            mid = i + BIT // 2
            if line[mid] == 0:
                bits = [line[mid + BIT * (k + 1)] for k in range(8)]
                got.append(sum(b << k for k, b in enumerate(bits)))
                if line[mid + 9 * BIT] != 1:
                    return [f"byte {len(got) - 1}: no stop bit"], ""
                i = mid + 9 * BIT
        i += 1
    errors = [] if got == want else [f"bytes: got {bytes(got)!r} want {bytes(want)!r}"]
    end = [s for s in samples if s["debug_pc"] == model.pc and s["debug_state"] == 0]
    if not end:
        errors.append(f"never halted at {model.pc:#06x}")
    else:
        errors += final_regs(samples[-16:], model)
    return errors, f"{len(got)} bytes"


def main():
    if len(sys.argv) == 1:
        check_fresh()
    failures = 0
    for pid, (name, prog) in enumerate(programs.PROGRAMS.items()):
        words = asm.assemble(prog)
        prints = name != "LARSON" and gen_cpu_tb.run_to_halt(words).uart_out
        errors, size = (check_uart if prints else check_lockstep)(pid, name, words)
        print(f"{name}: {'OK' if not errors else 'FAIL'} ({size})")
        for e in errors[:5]:
            print(f"    {e}")
        failures += bool(errors)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

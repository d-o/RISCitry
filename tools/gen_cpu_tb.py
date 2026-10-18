"""Generate source/memory/programs.luc and source/tb/cpu_tb.luc from tools/programs.py, and
source/computer/memory_map.luc from tools/memmap.py. Through memimage.py it also writes main_ram's initial contents: source/memory/main_ram_bram.v (hardware)
and source/memory/program_image.luc (simulation model).

programs.luc: every program as a constant array in `global Programs` (the board uses one,
the testbenches use all of them).

cpu_tb.luc: one testbench per program. Each runs the program on tools/rv32sim.py and checks the
computer (the cpu core wired to its ROM and RAM): the control state after every clock, and after every instruction the pc and the
destination register when the instruction writes one (x0 included, which must stay 0). Each check comes exactly as many clocks after the
last as this multi-cycle core should take (2, or 3 for stores, 4 for loads), so a missing or
extra cycle is a failure. After the program halts, all 16 registers are compared.

Programs that print to the UART poll its STATUS register, and rv32sim has no UART timing, so the
loop counts differ and lockstep checking is impossible. Their testbench instead runs the computer
until it has halted and every expected byte has arrived, decodes the serial line with Alchitry's
uart_rx, compares the bytes with rv32sim's output, then compares all 16 registers. One that prints
and never halts (the board's demo) is checked by its first programs.PRINT_BYTES[(name, switches)] bytes only.

A program that reads the switch register gets one testbench per setting in programs.SWITCHES
(cpu_<name>_sw<value>_tb).

Usage: python3 tools/gen_cpu_tb.py
"""
from pathlib import Path

import asm
import memimage
import memmap
import programs
import rv32sim

ROOT = Path(__file__).resolve().parent.parent
MAX_STEPS = 400            # a test program that doesn't reach halt by then is a bug
MAX_PRINT_STEPS = 100000    # ... or a forever-printing one that hasn't printed PRINT_BYTES
# control_unit's state after each clock of an instruction, by the instruction's clock count
STATES = {2: ["EXECUTE", "FETCH"], 3: ["EXECUTE", "MEM_ACCESS", "FETCH"],
          4: ["EXECUTE", "MEM_ACCESS", "WRITEBACK", "FETCH"]}


def states_after(model):
    """The control states after each clock of the instruction model.step() just ran (a WFI that
    waited stays in EXECUTE for model.stall extra clocks)."""
    if model.stall:
        return ["EXECUTE"] * (model.clocks_taken - 1) + ["FETCH"]
    return STATES[model.clocks_taken]
WRITES_RD = {0x37, 0x17, 0x6F, 0x67, 0x03, 0x13, 0x33}
# UART timing in the testbenches: io_clk is the testbench clock, 8 clocks per bit (uart_rx needs
# at least 4).
TB_UART_CLK_FREQ = 8
TB_UART_BAUD = 1
UART_CLOCKS_PER_BYTE = 10 * TB_UART_CLK_FREQ // TB_UART_BAUD   # start + 8 data + stop


def clocks_for(word, trapped=False):
    """Clocks this core spends on an instruction (rv32sim.clocks). One that traps or is interrupted
    (rv32sim's model.trapped after the step) is taken in EXECUTE: 2, even a load or store."""
    return rv32sim.clocks(word, trapped)


def writes_rd(word, trapped=False):
    """The register the instruction writes, or None (a trapping instruction writes nothing)."""
    if trapped or not rv32sim.legal(word):
        return None
    zicsr = word & 0x7F == 0x73 and (word >> 12) & 7 not in (0, 4)
    return (word >> 7) & 31 if word & 0x7F in WRITES_RD or zicsr else None


def new_model(name, words, switches=0, timer_rate=1, tick_div=1):
    return rv32sim.Cpu(words, traps_ok=name in programs.EXPECTS_TRAPS, switches=switches,
                       button_events=programs.BUTTON_EVENTS.get(name, ()), timer_rate=timer_rate,
                       plat_events=programs.PLAT_EVENTS.get(name, ()),
                       tick_div=tick_div)


def button_lines(name, clock, indent):
    """Testbench lines applying the button events scheduled before post-reset edge `clock`."""
    return [f"{indent}buttons = 4h{levels:X}   // button event" for c, levels in
            programs.BUTTON_EVENTS.get(name, ()) if c == clock]


def cases():
    """(program name, switch setting) for every testbench."""
    for name in programs.PROGRAMS:
        for sw in programs.SWITCHES.get(name, [0]):
            yield name, sw


def case_name(name, sw):
    return name.lower() + (f"_sw{sw}" if name in programs.SWITCHES else "")


def kind(name, words, sw):
    """"lockstep", "uart" (prints, then halts) or "uart_forever" (prints, never halts)."""
    if name in programs.RUNS_FOREVER:
        model = new_model(name, words, sw)
        for _ in range(programs.forever_steps(name, sw)):
            model.step()
        return "uart_forever" if model.uart_out else "lockstep"
    return "uart" if run_to_halt(name, words, sw).uart_out else "lockstep"


def run_to_bytes(name, words, sw, n):
    """Run rv32sim until the program has printed n bytes; return the model."""
    model = new_model(name, words, sw)
    for _ in range(MAX_PRINT_STEPS):
        model.step()
        if len(model.uart_out) >= n:
            return model
    raise RuntimeError(f"{name}: fewer than {n} bytes after {MAX_PRINT_STEPS} instructions")


def render_programs(assembled):
    out = ["// GENERATED by tools/gen_cpu_tb.py from tools/programs.py. Do not edit; rerun the script.",
           "// Lucid array literals put the LAST element at index 0, so each program is listed",
           "// from its highest address down to address 0.",
           "global Programs {",
           f"    enum Id {{ {', '.join(assembled)} }}",
           ""]
    for name, (words, text) in assembled.items():
        rows = asm.to_list(words)
        out.append(f"    const {name} = {{")
        for i in reversed(range(len(rows))):
            addr = i * 4
            comma = "," if i else ""
            listing = text.get(addr, "(nop)")
            out.append(f"        32h{rows[i]:08X}{comma}  // {addr:#06x}: {listing}")
        out.append("    }")
    out.append("}")
    return "\n".join(out) + "\n"


def run_to_halt(name, words, sw=0):
    """Run rv32sim until the program's halt has executed twice; return the model."""
    model = new_model(name, words, sw)
    halted_for = 0
    for _ in range(MAX_PRINT_STEPS):       # (a lockstep program is held to MAX_STEPS where it is rendered)
        word = model.fetch()
        model.step()
        if word == asm.HALT:
            halted_for += 1
            if halted_for == 2:
                return model
    raise RuntimeError(f"no halt after {MAX_PRINT_STEPS} instructions")


# Clock-enable tests: these programs run again with the computer enabled on one clock in
# CE_EVERY. The trace must be the ce = 1 trace stretched: nothing moves on a disabled clock (the
# state is checked after each one) and every enabled clock does exactly what a clock did before.
# Chosen to cover the RAM and its read register (MEMORY), the prefetch (SELFMOD), the timer and
# the buttons with interrupts (TIMER, IRQ). The timer is the exception to "nothing moves": it
# counts real time, every clock, so TIMER reads it at three ticks per cpu clock; TIMER runs again
# with the timer in STOPTIME mode (counting cpu clocks only: suffix _ce3s). IRQ sets mtimecmp to 0,
# so it can't tell the two modes apart.
CE_CASES = [("MEMORY", 0), ("SELFMOD", 0), ("TIMER", 0), ("TIMER", 1), ("IRQ", 0), ("WFI", 0)]   # (name, STOPTIME)
CE_EVERY = 3


def header(name, sw, extra_sigs=(), suffix="", stoptime=0):
    return [
        f"testbench cpu_{case_name(name, sw)}{suffix}_tb {{",
        "    sig clk",
        "    sig io_clk             // the UART's clock: same rate as clk, opposite phase (see tick_clock)",
        "    sig rst",
        "    sig sel[4]",
        "    sig switches[8]",
        "    sig buttons[4]",
        "    sig plat_irq[2]        // platform interrupt lines (a test input until the UART drives them)",
        "    sig ce                 // the computer's clock enable",
        *extra_sigs,
        "",
        f"    computer dut(#PROGRAM_ID(Programs.Id.{name}), #SIM_RAM(1), "
        f"#UART_CLK_FREQ({TB_UART_CLK_FREQ}), #UART_BAUD({TB_UART_BAUD}),"
        + (" #TIMER_STOPTIME(1)," if stoptime else ""),
        "                 .clk(clk), .rst(rst), .io_clk(io_clk), .io_rst(rst), .switches(switches),",
        "                 .buttons(buttons), .plat_irq(plat_irq), .ce(ce),",
        "                 .debug_sel(sel))",
        "",
        "    // silent: the simulator keeps every $tick()'s whole-design state (the RAM model included)",
        "    // for its waveform view, and across the suite that exhausts its memory.",
        "    // io_clk rises when clk falls: on the board the two clocks are unrelated, and edges at the",
        "    // same instant in two domains are where this simulator's update order bites (one byte in",
        "    // most runs of the long printing tests was sent twice with io_clk = clk)",
        "    fun tick_clock() {",
        "        clk = 1",
        "        io_clk = 0",
        "        $silent_tick()",
        "        clk = 0",
        "        io_clk = 1",
        "        $silent_tick()",
        "    }",
        "",
        "    fun expect_pc(pc[32]) {",
        "        if (dut.debug_pc != pc)",
        '            $print("pc: got %h want %h", dut.debug_pc, pc)',
        "        $assert(dut.debug_pc == pc)",
        "        $assert(dut.uart_tx)       // idle high unless the program prints",
        "    }",
        "",
        "    fun expect_state(state[Cpu.STATE_BITS]) {",
        "        if (dut.debug_state != state)",
        '            $print("state: got %d want %d", dut.debug_state, state)',
        "        $assert(dut.debug_state == state)",
        "        $assert(!dut.sleeping || dut.debug_state == Cpu.State.EXECUTE)   // only a waiting WFI sleeps",
        "    }",
        "",
        "    fun expect_reg(r[4], value[32]) {",
        "        sel = r",
        "        $silent_tick()",
        "        if (dut.debug_reg != value)",
        '            $print("x%d: got %h want %h", r, dut.debug_reg, value)',
        "        $assert(dut.debug_reg == value)",
        "    }",
        "",
        "    fun expect_display(value[33]) {   // owned, the four characters",
        "        if (c{dut.display_owned, dut.display_chars} != value)",
        '            $print("display: got %h want %h", c{dut.display_owned, dut.display_chars}, value)',
        "        $assert(c{dut.display_owned, dut.display_chars} == value)",
        "    }",
        "",
    ]


RESET = [
    "        $tick_clock()",
    "        $tick_clock()",
    "        rst = 0",
    "        $expect_pc(32h00000000)",
]


def plat_lines(name, clock, indent):
    """Testbench lines applying the platform-line events scheduled before post-reset edge `clock`."""
    return [f"{indent}plat_irq = 2h{levels:X}   // platform line event" for c, levels in
            programs.PLAT_EVENTS.get(name, ()) if c == clock]


def button_lines_in_loop(name):
    """In a free-running loop: apply each button event when the post-reset clock count hits it."""
    return [f"                if (clock_count == {c}) buttons = 4h{levels:X};   // button event"
            for c, levels in programs.BUTTON_EVENTS.get(name, ())]


def plat_lines_in_loop(name):
    return [f"                if (clock_count == {c}) plat_irq = 2h{levels:X};   // platform line event"
            for c, levels in programs.PLAT_EVENTS.get(name, ())]


def render_uart_testbench(name, words, text, sw, forever=False):
    """Free-run until halted with every byte received; check bytes, then registers. forever: run
    until the first PRINT_BYTES bytes have arrived and check those only."""
    if forever:
        n = programs.PRINT_BYTES[(name, sw)]
        model = run_to_bytes(name, words, sw, n)
        out = model.uart_out[:n]
    else:
        model = run_to_halt(name, words, sw)
        out = model.uart_out
        halt_pc = model.pc
    n = len(out)
    # the serial line (3x the time the bytes need) plus the clocks the program itself computes for
    # (the model has no UART wait; C_COST spends 66k clocks on arithmetic)
    max_clocks = n * UART_CLOCKS_PER_BYTE * 3 + 1000 + model.cycle
    expected = ", ".join(f"8h{b:02X}" for b in reversed(out))   # last element is index 0
    shown = "".join(chr(b) if 32 <= b < 127 else f"\\x{b:02x}" for b in out)
    lines = header(name, sw, ["    sig received[8]", f"    sig expected[{n}][8]", "    sig clock_count[32]"])
    at = next(i for i, l in enumerate(lines) if ".debug_sel(sel))" in l) + 1
    lines.insert(at, f"    uart_rx rx(#CLK_FREQ({TB_UART_CLK_FREQ}), #BAUD({TB_UART_BAUD}), "
                     ".clk(clk), .rst(rst), .rx(dut.uart_tx))")
    lines += [
        "    test run {",
        "        clk = 0",
        "        io_clk = 0",
        "        sel = 0",
        f"        switches = 8h{sw:02X}",
        "        buttons = 0",
        "        plat_irq = 0",
        "        ce = 1",
        "        rst = 1",
        "        received = 0",
        "        clock_count = 0",
        f"        expected = {{{expected}}}   // \"{shown}\"",
        "        // hold reset for an extra bit time: uart_tx's output register has no reset and starts",
        "        // low for two clocks, and uart_rx's 3-deep input buffer (not reset either) would",
        "        // take that for a start bit unless the idle-high line has filled it first",
        f"        repeat(i, {TB_UART_CLK_FREQ // TB_UART_BAUD}) {{",
        "            $tick_clock()",
        "        }",
        *RESET,
        *([f"        // run until the first {n} bytes have been received (at most {max_clocks} clocks)",
           f"        repeat(i, {max_clocks}) {{",
           f"            if (received != {n}) {{"] if forever else
          [f"        // run until halted at {halt_pc:#06x} with all {n} bytes received (at most {max_clocks} clocks)",
           f"        repeat(i, {max_clocks}) {{",
           f"            if (received != {n} || dut.debug_pc != 32h{halt_pc:08X} || dut.debug_state != Cpu.State.FETCH) {{"]),
        *button_lines_in_loop(name),
        *plat_lines_in_loop(name),
        "                $tick_clock()",
        "                clock_count = clock_count + 1",
        "                if (rx.new_data) {",
        f"                    $assert(received < {n})",
        "                    if (rx.data != expected[received])",
        '                        $print("byte %d: got %h want %h", received, rx.data, expected[received])',
        "                    $assert(rx.data == expected[received])",
        "                    received = received + 1",
        "                }",
        "            }",
        "        }",
        f"        if (received != {n})",
        f'            $print("received %d of {n} bytes", received)',
        f"        $assert(received == {n})",
    ]
    if not forever:
        lines += [
            f"        $expect_pc(32h{halt_pc:08X})",
            "        $expect_state(Cpu.State.FETCH)",
            "        // final register file",
            *[f"        $expect_reg(4d{r}, 32h{model.x[r]:08X})" for r in range(16)],
        ]
    lines += ["    }", "}"]
    return lines, f"{n} bytes"


def render_testbench(name, words, text, sw, ce_every=1, stoptime=0):
    # the timer runs on every clock (real time), so with ce on one clock in N it ticks N times
    # per cpu clock; in STOPTIME mode it counts cpu clocks
    model = new_model(name, words, sw, timer_rate=1 if stoptime else ce_every)
    suffix = "" if ce_every == 1 else f"_ce{ce_every}" + ("s" if stoptime else "")
    lines = header(name, sw, suffix=suffix, stoptime=stoptime) + [
        "    test run {",
        "        clk = 0",
        "        io_clk = 0",
        "        sel = 0",
        f"        switches = 8h{sw:02X}",
        "        buttons = 0",
        "        plat_irq = 0",
        "        ce = 1",
        "        rst = 1",
        *RESET,
    ]
    steps, halted_for, clock = 0, 0, 0
    shown = "FETCH"                        # the state after reset
    while True:
        pc = model.pc
        word = model.fetch()
        model.step()
        steps += 1
        lines.append(f"        // {pc:#06x}: {text.get(pc, 'nop')}")
        for state in states_after(model):
            lines += button_lines(name, clock, "        ")
            lines += plat_lines(name, clock, "        ")
            if ce_every > 1:               # disabled clocks first: nothing may move
                for _ in range(ce_every - 1):
                    lines += ["        ce = 0", "        $tick_clock()", f"        $expect_state(Cpu.State.{shown})"]
                lines.append("        ce = 1")
            lines += ["        $tick_clock()", f"        $expect_state(Cpu.State.{state})"]
            shown = state
            clock += 1
        lines.append(f"        $expect_pc(32h{model.pc:08X})")
        if word & 0x7F == 0x23:            # after every store: only stores move the display
            lines.append(f"        $expect_display(33h{model.display:09X})")
        rd = writes_rd(word, model.trapped)
        if rd is not None:
            lines.append(f"        $expect_reg(4d{rd}, 32h{model.x[rd]:08X})")
        if name in programs.RUNS_FOREVER:
            if steps == programs.forever_steps(name, sw):
                break
        elif word == asm.HALT:
            halted_for += 1
            if halted_for == 2:            # run halt twice: it must keep pc where it is
                break
        if steps > MAX_STEPS and name not in programs.RUNS_FOREVER:
            raise RuntimeError(f"{name}: no halt after {MAX_STEPS} instructions")
    lines.append("        // final register file and display")
    lines += [f"        $expect_reg(4d{r}, 32h{model.x[r]:08X})" for r in range(16)]
    lines.append(f"        $expect_display(33h{model.display:09X})")
    lines += ["    }", "}"]
    return lines, f"{steps} instructions"


def main():
    assembled = {name: asm.assemble_with_listing(prog) for name, prog in programs.PROGRAMS.items()}
    (ROOT / "source" / "memory" / "programs.luc").write_text(render_programs(assembled))
    (ROOT / "source" / "computer" / "memory_map.luc").write_text(memmap.render_luc())
    (ROOT / "source" / "memory" / "program_image.luc").write_text(memimage.render_program_image(assembled))
    (ROOT / "source" / "memory" / "main_ram_bram.v").write_text(memimage.render_main_ram_v(assembled))

    out = ["// GENERATED by tools/gen_cpu_tb.py from tools/programs.py, checked against tools/rv32sim.py.",
           "// Do not edit; rerun the script after changing a program."]
    summary = []
    for name, sw in cases():
        words, text = assembled[name]
        k = kind(name, words, sw)
        if name in programs.XSIM_ONLY:
            continue
        if k == "lockstep":
            tb, size = render_testbench(name, words, text, sw)
        else:
            tb, size = render_uart_testbench(name, words, text, sw, forever=k == "uart_forever")
        out += [""] + tb
        summary.append(f"{case_name(name, sw)}: {size}")
        for ce_name, stoptime in CE_CASES:
            if ce_name == name:
                tb, size = render_testbench(name, words, text, sw, ce_every=CE_EVERY, stoptime=stoptime)
                out += [""] + tb
                summary.append(f"{case_name(name, sw)}_ce{CE_EVERY}{'s' if stoptime else ''}: {size}")
    (ROOT / "source" / "tb" / "cpu_tb.luc").write_text("\n".join(out) + "\n")
    print("wrote programs.luc, memory_map.luc and cpu_tb.luc (" + ", ".join(summary) + ")")


if __name__ == "__main__":
    main()

"""Check the LED dimmer's generated Verilog (build/source/led_dimmer.v), not the Alchitry model of it.

Two checks, because the Alchitry simulator passed a design whose Verilog had a spilled 10th bit
(`c{1b0, a} + c{1b0, b}` is 10 wide, so the 9-bit slice assignment overwrote the next channel's
bit 0 and Yosys warned "select out of bounds" and "found logic loop", 2026-10-19):
  1. Yosys: read, proc, `check -assert` (no logic loops, no out-of-range selects, no undriven wires);
  2. Verilator runs a testbench against it: all 24 channels at once with 24 different duties
     (0, 1, 2, 127, 128, 129, 253, 254, 255 and others), in PWM and sigma-delta mode at
     prescale n = 0 and 3: over one full period (255 ticks of 2^n clocks) every LED must be on for
     exactly duty * 2^n clocks; then disabled, every LED is dark.
Usage: python3 tools/check_dimmer_verilog.py [VERILOG_DIR]   (default build/source, after `alchitry build`)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
YOSYS = "/usr/lib/alchitry/alchitry-labs/lib/app/tools/oss-cad-suite/bin/yosys"
DUTIES = [0, 1, 2, 127, 128, 129, 253, 254, 255, 3, 64, 100, 200, 250, 5, 17, 33, 66, 99, 130, 160, 190, 220, 240]

TB = """
module tb;
  reg clk = 0, rst = 1, ce = 1, sel_write = 0;
  reg [3:0] be = 0;
  reg [2:0] waddr = 0, raddr = 0;
  reg [31:0] wdata = 0;
  wire [31:0] rdata;
  wire [23:0] leds;
  wire enabled;
  led_dimmer dut(.clk(clk), .rst(rst), .ce(ce), .sel_write(sel_write), .be(be), .waddr(waddr),
                 .wdata(wdata), .raddr(raddr), .rdata(rdata), .leds(leds), .enabled(enabled));
  always #5 clk = ~clk;
  reg [7:0] duty [0:23];
  integer cnt [0:23];
  integer errors = 0, i, w, mode, n;

  task wr(input [2:0] word, input [3:0] lanes, input [31:0] data);
    begin
      @(negedge clk); sel_write = 1; waddr = word; be = lanes; wdata = data;
      @(negedge clk); sel_write = 0; be = 0;
    end
  endtask

  initial begin
    %DUTIES%
    repeat (3) @(negedge clk);
    rst = 0;
    for (mode = 0; mode < 2; mode = mode + 1)
      for (n = 0; n < 4; n = n + 3) begin
        wr(6, 4'b0001, 0);
        for (w = 0; w < 6; w = w + 1)
          wr(w, 4'b1111, {duty[4*w+3], duty[4*w+2], duty[4*w+1], duty[4*w]});
        wr(6, 4'b0001, 1 | (n << 1) | (mode << 4));
        repeat (255 * (1 << n) + 300) @(negedge clk);
        for (i = 0; i < 24; i = i + 1) cnt[i] = 0;
        repeat (255 * (1 << n)) begin
          @(negedge clk);
          for (i = 0; i < 24; i = i + 1) cnt[i] = cnt[i] + leds[i];
        end
        for (i = 0; i < 24; i = i + 1)
          if (cnt[i] != duty[i] * (1 << n)) begin
            $display("FAIL mode %0d n %0d led %0d duty %0d: %0d clocks on, want %0d", mode, n, i, duty[i], cnt[i], duty[i] * (1 << n));
            errors = errors + 1;
          end
        wr(6, 4'b0001, 0);
        repeat (4) @(negedge clk);
        if (leds != 0 || enabled) begin
          $display("FAIL mode %0d n %0d: not dark when disabled (%h)", mode, n, leds);
          errors = errors + 1;
        end
      end
    if (errors == 0) $display("DIMMER OK");
    $finish;
  end
endmodule
"""


def main():
    verilog = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "source"
    dut = verilog / "led_dimmer.v"
    check = subprocess.run([YOSYS, "-p", f"read_verilog -sv \"{dut}\"; proc; check -assert"],
                           capture_output=True, text=True)
    if check.returncode:
        print("YOSYS CHECK FAILED\n" + "\n".join(l for l in (check.stdout + check.stderr).splitlines()
                                                if "Warning" in l or "ERROR" in l)[:3000])
        return 1
    print("yosys check: no loops, no out-of-range selects")
    with tempfile.TemporaryDirectory() as tmp:
        tb = Path(tmp) / "tb.sv"
        tb.write_text(TB.replace("%DUTIES%", "\n    ".join(f"duty[{i}] = {d};" for i, d in enumerate(DUTIES))))
        build = subprocess.run(["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-lint", "-Wno-style",
                                "--top-module", "tb", "-Mdir", str(Path(tmp) / "obj"), str(tb), str(dut)],
                               capture_output=True, text=True)
        if build.returncode:
            print("verilator failed:\n" + build.stderr[-3000:])
            return 1
        run = subprocess.run([str(Path(tmp) / "obj" / "Vtb")], capture_output=True, text=True)
    print(run.stdout.strip())
    return 0 if "DIMMER OK" in run.stdout else 1


if __name__ == "__main__":
    sys.exit(main())

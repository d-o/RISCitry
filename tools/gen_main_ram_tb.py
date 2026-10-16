"""Generate source/tb/main_ram_tb.luc: main_ram's simulation model against the assembled program.

Checks: the program image reads back word by word (0 past its end); byte-lane writes overlay the
image without touching other lanes or words; a read of the address being written that edge
returns the old word (read-before-write) and the next read the new one; the read and write ports
are independent. A second testbench repeats the basics at the full size the computer will use.

(main_ram's hardware half, main_ram_bram.v, can't be simulated here: tools/check_main_ram.py
checks its contents through Yosys.)

Usage: python3 tools/gen_main_ram_tb.py
"""
from pathlib import Path

import asm
import memimage
import programs

ROOT = Path(__file__).resolve().parent.parent
PROGRAM = "LARSON"     # has a .org gap (nops) and ends with a non-nop word
SMALL_WORDS = 64
FULL_WORDS = 2048


def h(v):
    return f"32h{v:08X}"


def bench(name, words, body):
    bits = (words - 1).bit_length()
    return [
        f"testbench {name} {{",
        "    sig clk",
        f"    sig raddr[{bits}]",
        f"    sig waddr[{bits}]",
        "    sig wdata[32]",
        "    sig be[4]",
        "",
        f"    main_ram dut(#WORDS({words}), #PROGRAM_ID(Programs.Id.{PROGRAM}), #SIM_MODEL(1),",
        "                 .clk(clk), .ce(1), .raddr(raddr), .waddr(waddr), .wdata(wdata), .be(be))",
        "",
        "    // settle first: inputs changed in the same step as the rising edge would be missed",
        "    fun tick_clock() {",
        "        $silent_tick()",
        "        clk = 1",
        "        $silent_tick()",
        "        clk = 0",
        "        $tick()",
        "    }",
        "",
        "    // present addr on the read port for one clock, then check what comes back",
        f"    fun expect_word(addr[{bits}], value[32]) {{",
        "        raddr = addr",
        "        $tick_clock()",
        "        if (dut.rdata != value)",
        '            $print("word %d: got %h want %h", addr, dut.rdata, value)',
        "        $assert(dut.rdata == value)",
        "    }",
        "",
        "    test run {",
        "        clk = 0",
        "        raddr = 0",
        "        waddr = 0",
        "        wdata = 0",
        "        be = 0",
        *body,
        "    }",
        "}",
    ]


def write(addr, value, lanes):
    return [f"        waddr = {addr}", f"        wdata = {h(value)}", f"        be = 4b{lanes:04b}",
            "        $tick_clock()", "        be = 0"]


def main():
    img = memimage.image(asm.assemble(programs.PROGRAMS[PROGRAM]))
    n = len(img)
    small = [f"        // the {PROGRAM} image, then 0 past its end"]
    small += [f"        $expect_word({i}, {h(img[i])})" for i in range(n)]
    small += [f"        $expect_word({i}, {h(0)})" for i in range(n, n + 3)]

    small += ["        // a whole word past the program"] + write(n + 5, 0xCAFEF00D, 0b1111)
    small += [f"        $expect_word({n + 5}, {h(0xCAFEF00D)})"]

    lane_word = 1
    merged = (img[lane_word] & ~0x0000FF00) | 0x0000CC00
    small += [f"        // lane 1 only, inside the program: the other lanes keep the image"]
    small += write(lane_word, 0xAABBCCDD, 0b0010)
    small += [f"        $expect_word({lane_word}, {h(merged)})",
              f"        $expect_word({lane_word + 1}, {h(img[lane_word + 1])})"]
    merged2 = (merged & ~0xFF000000) | 0x11000000
    small += write(lane_word, 0x11223344, 0b1000)
    small += [f"        $expect_word({lane_word}, {h(merged2)})"]

    rw = 5
    small += [f"        // read and write word {rw} in the same edge: the old word comes back, then the new",
              f"        raddr = {rw}", f"        waddr = {rw}", f"        wdata = {h(0x12345678)}", "        be = 4b1111",
              "        $tick_clock()", "        be = 0",
              f"        if (dut.rdata != {h(img[rw])})",
              f'            $print("read during write: got %h want old %h", dut.rdata, {h(img[rw])})',
              f"        $assert(dut.rdata == {h(img[rw])})",
              f"        $expect_word({rw}, {h(0x12345678)})"]

    small += ["        // independent ports: write one word while reading another",
              "        raddr = 3", f"        waddr = {SMALL_WORDS - 1}", f"        wdata = {h(0x0BADF00D)}",
              "        be = 4b1111", "        $tick_clock()", "        be = 0",
              f"        $assert(dut.rdata == {h(img[3])})",
              f"        $expect_word({SMALL_WORDS - 1}, {h(0x0BADF00D)})",
              f"        $expect_word(3, {h(img[3])})"]

    full = ["        // full size: the image's first and last words, then the top word"]
    full += [f"        $expect_word(0, {h(img[0])})", f"        $expect_word({n - 1}, {h(img[n - 1])})",
             f"        $expect_word({FULL_WORDS - 1}, {h(0)})"]
    full += write(FULL_WORDS - 1, 0x600DCAFE, 0b1111)
    full += [f"        $expect_word({FULL_WORDS - 1}, {h(0x600DCAFE)})", f"        $expect_word(0, {h(img[0])})"]

    out = ["// GENERATED by tools/gen_main_ram_tb.py from tools/programs.py. Do not edit; rerun the script.", ""]
    out += bench("main_ram_tb", SMALL_WORDS, small) + [""] + bench("main_ram_full_tb", FULL_WORDS, full)
    dest = ROOT / "source" / "tb" / "main_ram_tb.luc"
    dest.write_text("\n".join(out) + "\n")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()

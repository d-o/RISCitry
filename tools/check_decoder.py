"""Check rv32sim.legal() against the hardware decoder: Yosys evaluates the decoder.v that
`alchitry build` generated on a few thousand instruction words (random, every opcode with random
fields, register fields mostly inside RV32E, and edge cases), and ctrl.illegal must agree with
legal() on every one. rv32sim and the generators use legal() to decide which instructions are
NOPs and how many clocks each takes, so a drift between the two would make wrong tests.

Usage: python3 tools/check_decoder.py        (after `alchitry build`)
"""
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import rv32sim

ROOT = Path(__file__).resolve().parent.parent
YOSYS = "/usr/lib/alchitry/alchitry-labs/lib/app/tools/oss-cad-suite/bin/yosys"
VERILOG = ROOT / "build" / "source"
OPCODES = [0x37, 0x17, 0x6F, 0x67, 0x63, 0x03, 0x23, 0x13, 0x33, 0x0F, 0x73]
ILLEGAL_BIT = 0              # ctrl's first field, illegal, is its top bit (index 0 of the bit string)


def words():
    rnd = random.Random(1)
    out = [rnd.getrandbits(32) for _ in range(1000)]
    out += [(rnd.getrandbits(25) << 7) | rnd.choice(OPCODES) for _ in range(2500)]
    out += [(rnd.getrandbits(7) << 25) | (rnd.getrandbits(4) << 20) | (rnd.getrandbits(4) << 15)
            | (rnd.getrandbits(3) << 12) | (rnd.getrandbits(4) << 7) | rnd.choice(OPCODES) for _ in range(500)]
    csrs = list(rv32sim.CSR_WRITABLE) + list(rv32sim.CSR_CONSTANT) + [0xC00, 0x7C0, 0x302, 0xFFF]
    out += [(rnd.choice(csrs) << 20) | (rnd.getrandbits(13) << 7) | 0x73 for _ in range(1000)]  # Zicsr
    out += [0, 0xFFFFFFFF, 0x73, 0x00100073, 0x00200073, 0x0000000F, 0x0000100F,
            0x30200073, 0x10500073, 0x10200073, 0x30200073 | (1 << 15)]
    return out


def main():
    ws = words()
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "dec.ys"
        script.write_text(f"read_verilog -sv {VERILOG / 'decoder.v'} {VERILOG / 'lucid_globals.v'}\n"
                          "hierarchy -top decoder\nproc\nflatten\n"
                          + "".join(f"eval -set instruction 32'h{w:08x} -show ctrl\n" for w in ws))
        r = subprocess.run([YOSYS, "-s", str(script)], capture_output=True, text=True)
    results = re.findall(r"ctrl = \d+'([01]+)", r.stdout)
    if r.returncode or len(results) != len(ws):
        sys.exit(f"yosys failed or gave {len(results)} results for {len(ws)} words\n{r.stderr[-2000:]}")
    bad = [(w, b) for w, b in zip(ws, results) if (b[ILLEGAL_BIT] == "0") != rv32sim.legal(w)]
    print(f"{len(ws)} words ({sum(map(rv32sim.legal, ws))} legal): {len(bad)} disagree")
    for w, b in bad[:10]:
        print(f"  {w:08x}: decoder says {'illegal' if b[ILLEGAL_BIT] == '1' else 'legal'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

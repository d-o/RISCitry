"""Check source/memory/main_ram_bram.v, which the Alchitry simulator can't run.

1. For every program: elaborate the Verilog with Yosys at that PROGRAM_ID, read each lane's
   memory init back out, and compare the words with the assembled program (0 elsewhere).
2. Synthesise it for the iCE40 (synth_ice40) and report the block RAMs and logic it maps to,
   so a RAM that silently became flip-flops, or grew glue logic, shows up.

Usage: python3 tools/check_main_ram.py [WORDS]    (default 2048)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import asm
import memimage
import programs

ROOT = Path(__file__).resolve().parent.parent
YOSYS = "/usr/lib/alchitry/alchitry-labs/lib/app/tools/oss-cad-suite/bin/yosys"
VERILOG = ROOT / "source" / "memory" / "main_ram_bram.v"


def yosys(script):
    r = subprocess.run([YOSYS, "-q", "-p", script], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stdout + r.stderr)
    return r.stdout


def lane_inits(pid, words):
    """{lane number: [byte per word]} from the elaborated memories' INIT parameters."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ram.json"
        yosys(f"read_verilog -sv \"{VERILOG}\"; chparam -set PROGRAM_ID {pid} -set WORDS {words} main_ram_bram; "
              f"hierarchy -top main_ram_bram; proc; memory_collect; write_json \"{out}\"")
        module = json.loads(out.read_text())["modules"]["main_ram_bram"]
    lanes = {}
    for cell in module["cells"].values():
        if not cell["type"].startswith("$mem"):
            continue
        name = cell["parameters"]["MEMID"].lstrip("\\")
        bits = cell["parameters"]["INIT"][::-1]                     # LSB first: word 0 first
        if "x" in bits[:words * 8]:
            raise RuntimeError(f"PROGRAM_ID {pid}: {name} has undefined (x) initial bits")
        lanes[int(name[-1])] = [int(bits[i * 8:(i + 1) * 8][::-1] or "0", 2) for i in range(words)]
    return lanes


def main():
    words = int(sys.argv[1]) if len(sys.argv) > 1 else 2048
    failures = 0
    for pid, (name, prog) in enumerate(programs.PROGRAMS.items()):
        img = memimage.image(asm.assemble(prog))
        expect = img + [0] * (words - len(img))
        lanes = lane_inits(pid, words)
        got = [lanes[0][i] | lanes[1][i] << 8 | lanes[2][i] << 16 | lanes[3][i] << 24 for i in range(words)]
        bad = [i for i in range(words) if got[i] != expect[i]]
        print(f"{name}: {len(img)} words, {'OK' if not bad else f'{len(bad)} words differ, first at {bad[0]}'}")
        failures += bool(bad)

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "stat.txt"
        yosys(f"read_verilog -sv \"{VERILOG}\"; chparam -set WORDS {words} main_ram_bram; "
              f"synth_ice40 -top main_ram_bram; tee -q -o \"{report}\" stat")
        stat = report.read_text()
    cells = {line.split()[1]: int(line.split()[0]) for line in stat.splitlines()
             if len(line.split()) == 2 and line.split()[0].isdigit() and line.split()[1].startswith("SB_")}
    print(f"synth_ice40, {words} words: {cells}")
    if cells.get("SB_RAM40_4K", 0) != max(4, words * 32 // 4096):
        print("unexpected block RAM count")
        failures += 1
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

"""Build C programs for the core and hand them to tools/programs.py.

    python3 tools/cbuild.py [name ...]     compile c/<name>.c (all c/*.c by default) with
                                           riscv64-unknown-elf-gcc -march=rv32e -mabi=ilp32e
Each program is c/crt0.S + c/<name>.c + libgcc, linked by c/linker.ld at address 0 (the reset
pc), and written to c/images/<name>.bin (the RAM image) and c/images/<name>.lst (the
disassembly). Both are committed, so generating the tests (gen_cpu_tb.py) needs no compiler:
programs.py calls items(name), which turns the .bin into a program for tools/asm.py.

Every instruction word the compiler and libgcc produced (everything below _etext) is checked
against rv32sim.legal(), i.e. the decoder: no RV32E violation, compressed or M instruction
gets through. Needs the Ubuntu packages gcc-riscv64-unknown-elf and binutils-riscv64-unknown-elf.
"""
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import memmap
import rv32sim

ROOT = Path(__file__).resolve().parent.parent
C_DIR = ROOT / "c"
IMAGES = C_DIR / "images"
PREFIX = "riscv64-unknown-elf-"
ORIGINS = {"demo": 0x800, "fader": 0x1000}   # where each program is linked; the others own the machine, from 0
ARCH = ["-mabi=ilp32e"]
CFLAGS = ["-march=rv32e_zicsr", *ARCH, "-Os", "-ffreestanding", "-fno-pic", "-mstrict-align",
          "-Wall", "-Wextra", "-Werror"]


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def build(name):
    """Compile and link c/<name>.c; return the RAM image bytes (also written to c/images)."""
    origin = ORIGINS.get(name, 0)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        elf = tmp / f"{name}.elf"
        # crt0 uses csrw, which needs Zicsr in the assembler; the link below says plain rv32e,
        # which is what selects the rv32e/ilp32e libgcc.
        run(PREFIX + "gcc", "-march=rv32e_zicsr", *ARCH, "-c", str(C_DIR / "crt0.S"), "-o", str(tmp / "crt0.o"))
        run(PREFIX + "gcc", *CFLAGS, "-c", str(C_DIR / f"{name}.c"), "-o", str(tmp / f"{name}.o"))
        run(PREFIX + "gcc", "-march=rv32e", *ARCH, "-nostdlib", "-T", str(C_DIR / "linker.ld"),
            f"-Wl,--defsym=__origin={origin}", f"-Wl,-Map={tmp / 'map'}", str(tmp / "crt0.o"), str(tmp / f"{name}.o"), "-lgcc", "-o", str(elf))
        run(PREFIX + "objcopy", "-O", "binary", str(elf), str(tmp / "image.bin"))
        image = (tmp / "image.bin").read_bytes()
        etext = int(next(line.split()[0] for line in run(PREFIX + "nm", str(elf)).splitlines()
                         if line.endswith(" _etext")), 16)
        listing = run(PREFIX + "objdump", "-d", "-M", "no-aliases,numeric", str(elf)).replace(str(elf), f"{name}.elf")
        sizes = run(PREFIX + "size", str(elf))
        symbols = {line.split()[2]: int(line.split()[0], 16) for line in run(PREFIX + "nm", str(elf)).splitlines()
                   if len(line.split()) == 3}
        # (section name, size, address) of every section that takes room in RAM
        sections = [(f[1], int(f[2], 16), int(f[3], 16)) for f in
                    (line.split() for line in run(PREFIX + "objdump", "-h", str(elf)).splitlines()
                     if len(line.split()) >= 7 and line.split()[0].isdigit())]
    # .data and .sdata (initialised writable data): linker.ld reserves room for a pristine copy right
    # after them (_edata ...), and crt0 copies it down on every start, because a reset leaves the last
    # run's values in RAM. The copy is appended here. It costs as much RAM again as the data itself.
    data_start, data_end = symbols["_data_start"], symbols["_edata"]
    data_bytes = data_end - data_start
    image += bytes(-len(image) % 4)
    if len(image) != data_end - origin:
        raise SystemExit(f"{name}: image is {len(image)} bytes but .data ends at {data_end:#x}: nothing else may follow it")
    image += image[data_start - origin:data_end - origin]
    # Nothing but the reserved copy may sit on the pristine copy of .data, and crt0 must zero every
    # .sbss/.bss byte (_bss_start.._bss_end). The linker once put .sbss right on the copy, outside the
    # zeroed range (2026-10-23): `phase` was the "pristine" value of `dir`, so a warm start gave the
    # fader a random direction.
    for sname, size, vma in sections:
        if size == 0 or sname == ".datacopy":
            continue
        if vma < data_end + data_bytes and vma + size > data_end and data_bytes:
            raise SystemExit(f"{name}: section {sname} ({vma:#x}..{vma + size:#x}) overlaps .data's pristine copy "
                             f"({data_end:#x}..{data_end + data_bytes:#x})")
        if sname in (".sbss", ".bss") and (vma < symbols["_bss_start"] or vma + size > symbols["_bss_end"]):
            raise SystemExit(f"{name}: section {sname} ({vma:#x}..{vma + size:#x}) is not inside the zeroed range "
                             f"{symbols['_bss_start']:#x}..{symbols['_bss_end']:#x}")
    words = struct.unpack(f"<{len(image) // 4}I", image)
    bad = [f"{origin + 4 * i:#x}: {w:#010x}" for i, w in enumerate(words) if origin + 4 * i < etext and not rv32sim.legal(w)]
    if bad:
        raise SystemExit(f"{name}: instructions the core doesn't decode: {bad[:5]}")
    if origin + len(image) > memmap.RAM_BYTES:
        raise SystemExit(f"{name}: image is {len(image)} bytes at {origin:#x}, RAM is {memmap.RAM_BYTES}")
    IMAGES.mkdir(exist_ok=True)
    (IMAGES / f"{name}.bin").write_bytes(image)
    (IMAGES / f"{name}.lst").write_text(listing)
    print(f"{name}: {len(image)} bytes at {origin:#x}, code {etext - origin} bytes, {(etext - origin) // 4} instructions, all legal; "
          f".data {data_bytes} bytes (+{data_bytes} for its pristine copy)\n{sizes}", end="")
    return image


def items(name):
    """The program for tools/asm.py: the committed image as words, to be placed at ORIGINS[name]
    (0 unless the caller puts an .org before it)."""
    image = (IMAGES / f"{name}.bin").read_bytes()
    words = struct.unpack(f"<{len(image) // 4}I", image)
    return [(".word", *words)]


def main():
    names = sys.argv[1:] or sorted(p.stem for p in C_DIR.glob("*.c"))
    for name in names:
        build(name)


if __name__ == "__main__":
    main()

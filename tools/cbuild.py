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
ORIGINS = {"demo": 0x800}       # where each program is linked; the others own the machine, from 0
ARCH = ["-mabi=ilp32e"]
CFLAGS = ["-march=rv32e", *ARCH, "-Os", "-ffreestanding", "-fno-pic", "-mstrict-align",
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
    image += bytes(-len(image) % 4)
    words = struct.unpack(f"<{len(image) // 4}I", image)
    bad = [f"{origin + 4 * i:#x}: {w:#010x}" for i, w in enumerate(words) if origin + 4 * i < etext and not rv32sim.legal(w)]
    if bad:
        raise SystemExit(f"{name}: instructions the core doesn't decode: {bad[:5]}")
    if origin + len(image) > memmap.RAM_BYTES:
        raise SystemExit(f"{name}: image is {len(image)} bytes at {origin:#x}, RAM is {memmap.RAM_BYTES}")
    IMAGES.mkdir(exist_ok=True)
    (IMAGES / f"{name}.bin").write_bytes(image)
    (IMAGES / f"{name}.lst").write_text(listing)
    print(f"{name}: {len(image)} bytes at {origin:#x}, code {etext - origin} bytes, {(etext - origin) // 4} instructions, all legal\n{sizes}", end="")
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

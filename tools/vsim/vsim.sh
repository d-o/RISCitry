#!/bin/bash
# Build (if needed) and start vsim: the board's Verilog under Verilator with the Io front panel.
#   tools/vsim/vsim.sh [vsim options]      e.g. --scale 14, --unthrottled, --quiet
# Needs: `alchitry build` run first (vsim simulates build/source/*.v), verilator, libsdl2-dev,
# ImageMagick (convert) and unzip; the board artwork comes from the installed Alchitry Labs jar.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SRC="$ROOT/build/source"
OBJ="$ROOT/build/vsim"

if [ ! -f "$SRC/basic_riscve_16k16k.v" ]; then
    echo "vsim: no $SRC/basic_riscve_16k16k.v: run alchitry build $ROOT/basic-riscve-16k16k.alp first" >&2
    exit 1
fi
newest_src=$(find "$ROOT/source" -name '*.luc' -o -name '*.v' | xargs stat -c %Y | sort -n | tail -1)
if [ "$(stat -c %Y "$SRC/basic_riscve_16k16k.v")" -lt "$newest_src" ]; then
    echo "vsim: build/source is older than source/: rerun alchitry build (simulating the old Verilog)" >&2
fi

# Rebuild when the Verilog or the harness is newer than the binary.
bin="$OBJ/Vbasic_riscve_16k16k"
if [ ! -x "$bin" ] || [ -n "$(find "$SRC" "$HERE" -newer "$bin" -name '*.v' -o -newer "$bin" -name '*.cpp' | head -1)" ]; then
    mkdir -p "$OBJ"
    # -GPLL=0: sys_clock passes clk straight through (Verilator can't run SB_PLL40_CORE), so one
    # clk is one 25 MHz sys_clk. WIDTH: Lucid's generated Verilog pads and truncates freely
    # (Yosys accepts it; the Lucid checker has already passed the source). UNOPTFLAT: a few wide
    # signals in cpu.v (alu result, csr read data, ...) have bits that feed other bits through
    # logic; not a real loop (nextpnr would refuse one), only slower for Verilator to evaluate.
    verilator --cc --exe --build -j 0 -O3 --x-assign fast --x-initial fast \
        -Wno-WIDTH -Wno-UNOPTFLAT --top-module basic_riscve_16k16k -GPLL=0 \
        -CFLAGS "-O2 $(sdl2-config --cflags)" -LDFLAGS "$(sdl2-config --libs)" \
        --Mdir "$OBJ" -o Vbasic_riscve_16k16k \
        "$HERE/sb_pll40_core.v" "$SRC"/*.v "$HERE/vsim.cpp" >"$OBJ/build.log" 2>&1 || {
            grep -E '^%(Error|Warning)' "$OBJ/build.log" | head -20 >&2
            echo "vsim: verilator failed, see $OBJ/build.log" >&2
            exit 1
        }
    if grep -q '^%Warning' "$OBJ/build.log"; then
        grep '^%Warning' "$OBJ/build.log" >&2
    fi
fi

exec "$bin" "$@"

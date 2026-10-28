# Zephyr support (draft)

Out-of-tree module: `west build -b alchitry_cu_io app -- -DZEPHYR_EXTRA_MODULES=/path/to/riscitry`.

| Zephyr | Here |
|---|---|
| SoC `soc/riscitry/riscitry` + `dts/riscv/riscitry/riscitry.dtsi` | `computer` + `cpu` |
| Board `boards/riscitry/alchitry_cu_io` | the Cu + Io top (`riscitry.luc`) |
| (none) | `machine` (run/step sequencer; Zephyr never sees it) |

`riscitry-memmap.dtsi` is generated from `tools/memmap.py`: `python3 tools/gen_dts.py` (`--check` for CI).

## Status: builds (2026-10-29)
Zephyr 4.4.99 (T2 workspace `~/Documents/z-workspaces`), SDK 1.0.1:

    source .venv/bin/activate; export ZEPHYR_SDK_INSTALL_DIR=~/.local/zephyr-sdk-1.0.1
    west build -b alchitry_cu_io zephyr/samples/basic/minimal -d OUT -- -DZEPHYR_EXTRA_MODULES=~/Documents/riscitry

Configures, compiles and links as `rv32e_zicsr_zifencei` / `ilp32e` (the SDK's rv32e multilib needs zifencei
in the march, hence `RISCV_ISA_EXT_ZIFENCEI`). Not run on hardware or in any simulator.

| config | RAM used |
|---|---|
| `samples/basic/minimal` as is | 18,480 B (does **not** fit 8 KB, or the 16 KB the HX8K has) |
| + `MULTITHREADING=n`, stacks 512/512/256, no assert/printk/banner/timeslicing | 7,216 B of 8,192 |

## Open issues
- **RAM**: 8 KB holds code and data. Only a single-threaded, trimmed minimal fits (table above); anything with threads, printk or a driver needs RAM grown to 16 KB (all 32 BRAMs).
- **Loading**: no flash. The RAM is preloaded from the bitstream, so `zephyr.bin` must go through the
  `memimage.py` / `cbuild.py` path into `main_ram_bram.v`, then a rebuild. No XIP, no `zephyr,flash`.
- **Drivers to write**: UART (`riscitry,uart`; RX read pops), counter, buttons, LED dimmer, display.
  Only the timer uses a stock driver (`riscv,machine-timer`, mtime +0 / mtimecmp +8, 1 MHz).
- **mtvec**: BASE is 128-byte aligned in `csr_file.luc`; Zephyr's direct-mode `__irq_wrapper` must be placed accordingly.
- **No PLIC**: interrupts are cpu-local (7 timer, 11 buttons, 16 UART RX, 17 counter), `NUM_IRQS` 18.
- **No M extension**: libgcc multiply/divide. `mcycle`/`minstret` not implemented.
- `zifencei` is in the march only to pick the rv32e multilib: the core traps FENCE.I as illegal, so nothing may execute it (no icache, no XIP).

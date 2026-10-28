# Zephyr support (draft)

Out-of-tree module: `west build -b alchitry_cu_io app -- -DZEPHYR_EXTRA_MODULES=/path/to/riscitry`.

| Zephyr | Here |
|---|---|
| SoC `soc/riscitry/riscitry` + `dts/riscv/riscitry/riscitry.dtsi` | `computer` + `cpu` |
| Board `boards/riscitry/alchitry_cu_io` | the Cu + Io top (`riscitry.luc`) |
| (none) | `machine` (run/step sequencer; Zephyr never sees it) |

`riscitry-memmap.dtsi` is generated from `tools/memmap.py`: `python3 tools/gen_dts.py` (`--check` for CI).

## Status: written, not built
Only checked so far: the board `.dts` passes `cpp` + `dtc`. No Zephyr tree or `west` was on this machine
(only the SDK tarball), so the Kconfig, hardware-model-v2 files and bindings are unverified.

## Open issues
- **RAM**: 8 KB holds code and data. A Zephyr kernel image probably doesn't fit; RAM may need to grow (16 KB).
- **Loading**: no flash. The RAM is preloaded from the bitstream, so `zephyr.bin` must go through the
  `memimage.py` / `cbuild.py` path into `main_ram_bram.v`, then a rebuild. No XIP, no `zephyr,flash`.
- **Drivers to write**: UART (`riscitry,uart`; RX read pops), counter, buttons, LED dimmer, display.
  Only the timer uses a stock driver (`riscv,machine-timer`, mtime +0 / mtimecmp +8, 1 MHz).
- **mtvec**: BASE is 128-byte aligned in `csr_file.luc`; Zephyr's direct-mode `__irq_wrapper` must be placed accordingly.
- **No PLIC**: interrupts are cpu-local (7 timer, 11 buttons, 16 UART RX, 17 counter), `NUM_IRQS` 18.
- **No M extension**: libgcc multiply/divide. `mcycle`/`minstret` not implemented.
- Confirm the Zephyr version supports `RISCV_ISA_RV32E`.

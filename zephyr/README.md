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
in the march, hence `RISCV_ISA_EXT_ZIFENCEI`). Runs in rv32sim, in xsim, and on the board (see below).

| config (16 KB RAM) | RAM used |
|---|---|
| `samples/basic/minimal` as is | 18,480 B: does **not** fit |
| `samples/hello` (`samples/hello/prj.conf`: printk, threads, small stacks, nano cbprintf, no TLS) | 14,912 B of 16,384 |

`samples/hello` prints through `drivers/serial/uart_riscitry.c` (polling); in `tools/rv32sim.py` (RAM zero-filled, 1 us
mtime) it prints `Hello from Zephyr on alchitry_cu_io/riscitry` after 5,504 instructions, and all 2,815 code words are
legal for the decoder. **It is no longer a program in `tools/programs.py`**: Alchitry Labs ran out of memory building
two Zephyr images (about 9,300 RAM init statements; it unrolls them), so only `samples/tick` is registered. Its
image is the superset (the same greeting, then ticks). `images/hello.bin` stays as the minimal sample's build.

`samples/tick` (15,696 B of 16,384) sleeps `k_msleep(n)` and prints `tick N` per period, so every line is a
timer interrupt through Zephyr's trap handler (`mtvec = _isr_wrapper`, direct mode), `mret`, and a `WFI` that the
machine timer ends. The period is the **middle DIP bank in milliseconds, 500 when it is 0** (so xsim can run it at
1 ms). Registered as `ZEPHYR_TICK`, image `zephyr/images/tick.bin` (committed like `c/images`; hardware-only: its
Lucid arrays are stubs), with switch cases 0 and 1:
`python3 tools/xsim_computer.py --only zephyr_tick_sw0,zephyr_tick_sw1` runs the generated Verilog (sw0: the 36-byte
greeting, 500 ms; sw1: greeting and three ticks, 60 bytes; about 3 minutes; a changed `tick` word fails it). rv32sim
gives up on a `WFI` longer than 1,000,000 clocks, so the 500 ms case only checks the greeting.
**On the board** (flashed 2026-10-31, `PROGRAM_ID` temporarily `ZEPHYR_TICK` in `riscitry.luc`): ticks arrive at the DIP
value in ms (31.19 ms mean over 317 ticks with the DIPs at about 31).
Rebuild: `west build -b alchitry_cu_io zephyr/samples/tick ...`, copy `zephyr.bin` to `zephyr/images/tick.bin`,
rerun `tools/gen_cpu_tb.py`, `alchitry build`.

## Open issues
- **RAM**: 16 KB (all 32 BRAMs), code and data together. The tick sample leaves 0.7 KB.
- **Loading**: no flash. The RAM is preloaded from the bitstream: `zephyr.bin` goes into `main_ram_bram.v`
  through `programs.py`, then an `alchitry build`. No XIP, no `zephyr,flash`. Not yet selectable from the boot menu.
- **Drivers**: UART is polling only (no RX interrupt yet). Still to write: counter, buttons, LED dimmer, display.
  The timer uses the stock driver (`riscv,machine-timer`, mtime +0 / mtimecmp +8, 1 MHz).
- **mtvec**: the 128-byte alignment is for vectored mode only; Zephyr runs direct mode with `_isr_wrapper` at 0x14. Fine (checked, and exercised by `tick`).
- **No PLIC**: interrupts are cpu-local (7 timer, 11 buttons, 16 UART RX, 17 counter), `NUM_IRQS` 18.
- **No M extension**: libgcc multiply/divide. `mcycle`/`minstret` not implemented.
- `zifencei` is in the march only to pick the rv32e multilib: the core traps FENCE.I as illegal, so nothing may execute it (no icache, no XIP).

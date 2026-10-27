# RISCitry

A small RISC-V (RV32E) soft core written in [Lucid V2](https://alchitry.com/tutorials/references/lucid-reference/),
for the [Alchitry](https://alchitry.com) Cu (Lattice iCE40 HX8K) with the Io board. It runs real C on a
25 MHz clock, takes interrupts, sleeps in `wfi`, talks over a 1 Mbaud serial port and dims 24 LEDs.

It is a demonstration project. The aim was to build a CPU, make it run real code, and find where the design
breaks. The journey, including the wrong turns, is written up in my
[blog series](https://dean.sellers.id.au).

> **Status: hobby project.** It is tested against its own Python model and its own programs. It has **not**
> been run through the `riscv-arch-test` compliance suite, so "RV32E" here means "RV32E-ish, and
> checked as hard as one person could". It meets 25 MHz timing with modest margin (fmax about 28 to 30 MHz
> in the current build). Not affiliated with, or endorsed by, Alchitry or RISC-V International. Use it in
> production at your own ris~~c~~k.

The name *RISCitry* is chosen to acknowledge the [Alchitry](https://alchitry.com) ecosystem that it is built for.

## What it is

- **Core.** RV32E base ISA, multi-cycle (FETCH, EXECUTE, then MEM_ACCESS and WRITEBACK for loads and
  stores), not pipelined, so no branch penalty. Two clocks for most instructions, three for a store, four
  for a load. Hardwired decoder, no M extension (`*` `/` `%` go through libgcc).
- **Privileged.** Machine mode with Zicsr, `mret`, `wfi` (the machine really stops the cpu clock while it
  waits), synchronous traps (illegal, `ecall`, `ebreak`, misaligned), a machine timer interrupt and
  external interrupts (buttons, UART receiver, a counter).
- **Memory.** One 8 KB von Neumann RAM in block RAM, preloaded with the program. Misaligned accesses trap.
- **Peripherals** (memory-mapped, map in `tools/memmap.py`): UART TX and RX, machine timer (1 us ticks),
  buttons, a timer/counter, a 24-channel LED dimmer (PWM or sigma-delta), a 4-character 7-segment display
  and a switch register.
- **Boot menu.** The Io's middle DIP bank picks a program: a Larson scanner (several ways of timing it),
  serial hello, a button/serial interrupt demo, a scrolling 7-segment string, and a C demo that prints a
  clocks-per-instruction table and echoes the serial port (with a fading Larson scanner on top).
- **Size.** About 6,300 of 7,680 logic cells (81%), 16 of 32 block RAMs, one PLL.

## Layout

| Path | What |
|---|---|
| `riscitry.alp`, `source/` | The Alchitry Labs project: `board/` (pins, front panel), `machine/` (clock sequencer), `computer/` (bus, devices), `cpu/` (the core), `memory/`, `tb/` (testbenches) |
| `c/` | C programs, `crt0.S`, linker script; `c/images/` holds the built `.bin`/`.lst` so the generators need no compiler |
| `tools/` | Assembler, encoder, instruction-set simulator (`rv32sim.py`), testbench generators, checkers, serial soak scripts |
| `tools/vsim/` | An interactive simulator: the design under Verilator with a drawing of the Cu and Io. **GPL-3.0** |

Several source files are **generated** (`source/tb/cpu_tb.luc`, `source/memory/programs.luc`,
`program_image.luc`, `main_ram_bram.v`, `source/computer/memory_map.luc`). Their headers say so. Edit
`tools/programs.py` or `tools/memmap.py` and run `python3 tools/gen_cpu_tb.py`.

## Building

You need Alchitry Labs V2 (from [alchitry.com](https://alchitry.com)) (developed on 2.0.57). It provides the
`alchitry` command, the Lucid components the project references (`reset_conditioner`, `edge_detector`,
`uart_tx`, `uart_rx`) and the board constraint files; none of those are in this repo.

```
alchitry check  /abs/path/to/riscitry.alp
alchitry build  /abs/path/to/riscitry.alp     # exits 0 even if timing fails: read the output
alchitry load -p /abs/path/to/riscitry.alp --flash
```

Look for `PASS at 25.00 MHz` on the last `M_sysclk_clk_out` line. The CLI needs absolute paths, and the
project must not be open in the IDE.

For the C programs you also need `riscv64-unknown-elf-gcc` with the `rv32e`/`ilp32e` multilib
(Debian/Ubuntu: `gcc-riscv64-unknown-elf`, `binutils-riscv64-unknown-elf`), then
`python3 tools/cbuild.py [name]`. `python3 tools/rv32.py` self-tests the encoder, and checks it against the
GNU assembler when that is installed.

## Testing and simulation

There are four independent checks, because the Alchitry simulator has some limitations (flip-flops in different
module instances do not update together, so a result is a sample, not a verdict):

1. **Alchitry testbenches** (`alchitry sim`): unit tests and one lockstep test per program, checked against
   the Python model after every instruction. Run them one testbench at a time under a timeout.
2. **`tools/rv32sim.py`**: the model. Also what the generators use to say what should happen.
3. **`tools/xsim_computer.py`**: the generated Verilog under Yosys (all flip-flops together).
4. **`tools/vsim/vsim.sh`**: Verilator plus the Io front panel. Click the switches, watch the LEDs, read
   the serial port on a pty. Needs `verilator`, `libsdl2-dev`, ImageMagick, `unzip` and the installed
   Alchitry Labs (the board artwork is read from its jar at start-up, nothing of theirs is stored here).

`tools/check_decoder.py`, `check_main_ram.py` and `check_dimmer_verilog.py` check the generated Verilog
against the model. On the board, `tools/serial_soak.py`, `serial_echo_soak.py` and `serial_edges.py` soak
the serial port (needs `pyserial`).

## Known limits

- Not compliance-tested; CSR set is a subset (no `mcycle`/`minstret`); a fetch from I/O traps as illegal
  instruction, not as an access fault.
- 25 MHz margin has not been characterised across seeds, and button bounce and 7-segment flicker are
  unmeasured.
- The serial echo loop is about 80 ns a byte slower than line rate, so a burst longer than about 350
  bytes at 1 Mbaud drops bytes (a throughput limit, not corruption).
- `c/cost.h` computes `t * TIMEBASE` in 32 bits before dividing, which wraps for slow runs.

## How it was made

Written with [Claude Code](https://claude.com/claude-code) as a pair programmer. Every architectural decision and the shape (even some of the keystrokes) of the code are mine, and so are its mistakes, which are in the blog on purpose.

## Licence

MIT, except `tools/vsim/`, which is GPL-3.0-or-later. See `LICENSE` and `tools/vsim/LICENSE`. The compiled
images in `c/images/` include libgcc routines under its runtime library exception.

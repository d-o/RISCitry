"""Programs for the core: the board's image (a boot menu: Larson, a printing demo) and one test
program per instruction group.

Each test program ends in `halt` (jal x0, 0), except those in RUNS_FOREVER. gen_cpu_tb.py runs
every program on rv32sim and checks the hardware against it after each instruction (programs
that print: by their serial output and final state instead, since polling makes their timing
differ). Registers are x0-x15 only (RV32E). Code and data share the RAM (see memmap.py).
"""
import cbuild
import memmap
import rv32

# Board demo. Kept at its original addresses so the ROM words are unchanged.
# The turnaround jal x1 links pc + 4 = 0x80 (LED 7), so the jump itself is a display step.
LARSON = [
    "boot:",
    ("jal", 0, "turn"),
    (".org", 0x48),
    "right:",
    *[("srli", 1, 1, 1)] * 7,
    "left:",
    *[("slli", 1, 1, 1)] * 6,
    "turn:",
    ("jal", 1, "right"),
]

# Every OP and OP-IMM operation, with values that separate signed from unsigned,
# arithmetic from logical shifts, and SUB from ADD. Writing x0 must have no effect.
ALU = [
    ("lui", 2, 0x12345),
    ("addi", 2, 2, 0x678),          # x2 = 0x12345678
    ("addi", 3, 0, -1),             # x3 = 0xFFFFFFFF
    ("addi", 4, 0, 5),
    ("addi", 5, 0, 7),
    ("add", 6, 4, 5),
    ("sub", 7, 4, 5),               # 5 - 7 = 0xFFFFFFFE (the simulator-bug case)
    ("sub", 7, 5, 4),
    ("sll", 8, 4, 5),
    ("slt", 9, 3, 4),               # -1 < 5 signed
    ("sltu", 10, 3, 4),             # 0xFFFFFFFF < 5 unsigned: no
    ("xor", 11, 2, 3),
    ("srl", 12, 3, 4),
    ("sra", 13, 3, 4),
    ("sra", 13, 2, 4),              # positive value: same as srl
    ("or", 14, 4, 5),
    ("and", 15, 4, 5),
    ("slti", 6, 3, 0),
    ("sltiu", 7, 4, -1),            # imm sign-extends to 0xFFFFFFFF, compared unsigned
    ("xori", 8, 2, -1),             # bitwise not
    ("ori", 9, 0, 0x7FF),
    ("andi", 10, 3, 0x0F0),
    ("slli", 11, 4, 31),
    ("srli", 12, 3, 28),
    ("srai", 13, 3, 4),
    ("srai", 14, 2, 4),
    ("addi", 15, 2, -0x800),        # most negative 12-bit immediate (sets bit 30 in the word)
    ("addi", 0, 0, 5),              # x0 stays 0
    ("add", 1, 0, 0),
    ("halt",),
]

# Every branch condition both taken and not taken, then a counted loop (backward branch).
# x1 collects a bit for each instruction that should run; skipped ones would set other bits.
BRANCHES = [
    ("addi", 2, 0, -1),
    ("addi", 3, 0, 1),
    ("beq", 3, 3, "b1"),            # taken
    ("ori", 1, 1, 0x001),
    "b1:",
    ("bne", 3, 3, "b2"),            # not taken
    ("ori", 1, 1, 0x002),
    "b2:",
    ("blt", 2, 3, "b3"),            # -1 < 1: taken
    ("ori", 1, 1, 0x004),
    "b3:",
    ("bltu", 2, 3, "b4"),           # 0xFFFFFFFF < 1 unsigned: not taken
    ("ori", 1, 1, 0x008),
    "b4:",
    ("bge", 3, 2, "b5"),            # 1 >= -1: taken
    ("ori", 1, 1, 0x010),
    "b5:",
    ("bgeu", 3, 2, "b6"),           # 1 >= 0xFFFFFFFF unsigned: not taken
    ("ori", 1, 1, 0x020),
    "b6:",
    ("beq", 2, 3, "b7"),            # not taken
    ("ori", 1, 1, 0x040),
    "b7:",
    ("addi", 4, 0, 3),
    "loop:",
    ("addi", 5, 5, 1),
    ("addi", 4, 4, -1),
    ("bne", 4, 0, "loop"),          # three passes
    ("halt",),
]

# JAL/JALR call and return, AUIPC, LUI, and JALR clearing bit 0 of its target.
JUMPS = [
    ("auipc", 2, 0),                # x2 = pc of this instruction
    ("auipc", 3, 1),                # x3 = pc + 0x1000
    ("jal", 1, "func"),             # call
    ("addi", 6, 0, 6),              # runs after return
    ("lui", 7, 0xABCDE),
    ("addi", 8, 0, 0),
    ("jal", 0, "skip"),
    ("addi", 8, 0, 1),              # skipped
    "skip:",
    ("auipc", 9, 0),
    ("jalr", 10, 9, 13),            # target = pc_of_auipc + 13 -> odd, bit 0 cleared -> +12
    ("addi", 11, 0, 1),             # skipped (lands one past it)
    ("addi", 12, 0, 12),
    ("halt",),
    "func:",
    ("addi", 5, 0, 5),
    ("jalr", 0, 1, 0),              # ret
]

# SW then LW, positive and negative offsets, overwrites, x0 as data. Code and data share the
# RAM, so the data lives at DATA, clear of the program; every load reads an address stored first.
DATA = 0x400
MEMORY = [
    ("addi", 2, 0, DATA),           # base address
    ("lui", 3, 0xDEADC),
    ("addi", 3, 3, -0x111),         # x3 = 0xDEADBEEF
    ("sw", 3, 2, 0),
    ("sw", 2, 2, 4),
    ("lw", 4, 2, 0),
    ("lw", 5, 2, 4),
    ("add", 6, 4, 5),               # use a loaded value straight away
    ("sw", 0, 2, 0),                # overwrite with zero
    ("lw", 7, 2, 0),
    ("addi", 8, 2, 8),
    ("sw", 3, 8, -4),               # negative offset: DATA + 4
    ("lw", 9, 2, 4),
    ("lui", 11, memmap.RAM_BYTES >> 12),    # x11 = end of RAM
    ("sw", 6, 11, -4),              # top word of the RAM
    ("lw", 10, 11, -4),
    ("halt",),
]

# Byte and halfword loads/stores through mem_align and the byte-laned RAM: every width at every
# offset, sign vs zero extension, partial stores read back as words, a negative offset, and a byte
# store into a never-written word (the other bytes read as 0).
BYTES = [
    ("addi", 2, 0, DATA + 0x80),    # base address
    ("lui", 3, 0x80818),
    ("addi", 3, 3, -0xFF),          # x3 = 0x80817F01: bytes 01 7F 81 80
    ("sw", 3, 2, 0),
    ("lb", 4, 2, 0),                # 0x01
    ("lb", 5, 2, 1),                # 0x7F
    ("lb", 6, 2, 2),                # 0x81 -> 0xFFFFFF81
    ("lb", 7, 2, 3),                # 0x80 -> 0xFFFFFF80
    ("lbu", 8, 2, 2),               # 0x00000081
    ("lbu", 9, 2, 3),               # 0x00000080
    ("lh", 10, 2, 0),               # 0x7F01
    ("lh", 11, 2, 2),               # 0x8081 -> 0xFFFF8081
    ("lhu", 12, 2, 2),              # 0x00008081
    ("lhu", 13, 2, 0),
    ("addi", 14, 0, -1),            # x14 = 0xFFFFFFFF
    ("sw", 0, 2, 4),
    ("sb", 14, 2, 4),               # word +4 = 0x000000FF
    ("sb", 3, 2, 6),                # byte 0x01 at offset 2 -> 0x000100FF
    ("lw", 15, 2, 4),
    ("sh", 14, 2, 6),               # upper half -> 0xFFFF00FF
    ("lw", 15, 2, 4),
    ("sh", 3, 2, 4),                # lower half 0x7F01 -> 0xFFFF7F01
    ("lw", 1, 2, 4),
    ("lb", 1, 2, 5),                # 0x7F
    ("addi", 5, 2, 8),
    ("sb", 0, 5, -1),               # base + 7: top byte of word +4 -> 0x00FF7F01
    ("lw", 6, 2, 4),
    ("sb", 14, 2, 9),               # never-written word +8: byte 1 -> 0x0000FF00
    ("lw", 7, 2, 8),
    ("lbu", 8, 2, 9),
    ("halt",),
]

# Memory-mapped UART: poll STATUS, then store to TXDATA, with both sw and sb. A byte stored to
# lane 1 of TXDATA must not be sent. RAM word 0 (this program's first instruction) is read back
# at the end: UART stores mustn't reach the RAM (its low address bits match). Expected output:
# "Hi!\n".
UART = [
    ("addi", 12, 0, 0x5A),
    ("lui", 2, memmap.UART_BASE >> 12),        # x2 = UART base
    ("addi", 10, 0, ord("H")),
    ("jal", 1, "putc"),
    ("addi", 10, 0, ord("i")),
    ("jal", 1, "putc"),
    ("addi", 11, 0, ord("X")),
    ("jal", 1, "idle"),                        # not busy, so a wrongly decoded store would send
    ("sb", 11, 2, memmap.UART_TXDATA - memmap.UART_BASE + 1),   # lane 1: not sent
    ("addi", 10, 0, ord("!")),
    ("jal", 1, "putc_sb"),
    ("addi", 10, 0, ord("\n")),
    ("jal", 1, "putc"),
    ("lw", 13, 0, 0),                          # still the first instruction
    ("halt",),
    "putc:",                                   # send x10 with sw, once STATUS says not busy
    ("lw", 5, 2, memmap.UART_STATUS - memmap.UART_BASE),
    ("bne", 5, 0, "putc"),
    ("sw", 10, 2, memmap.UART_TXDATA - memmap.UART_BASE),
    ("jalr", 0, 1, 0),
    "idle:",                                   # wait until STATUS says not busy
    ("lw", 5, 2, memmap.UART_STATUS - memmap.UART_BASE),
    ("bne", 5, 0, "idle"),
    ("jalr", 0, 1, 0),
    "putc_sb:",                                # the same with sb
    ("lw", 5, 2, memmap.UART_STATUS - memmap.UART_BASE),
    ("bne", 5, 0, "putc_sb"),
    ("sb", 10, 2, memmap.UART_TXDATA - memmap.UART_BASE),
    ("jalr", 0, 1, 0),
]

# Code and data share the RAM: print a .string that lives right after the code, walking it with
# lbu (loads from the same memory the instructions come from). Expected output: the string.
STRING = [
    ("lui", 2, memmap.UART_BASE >> 12),        # x2 = UART base
    ("addi", 3, 0, "msg"),                     # x3 -> message (a label is its address)
    "next:",
    ("lbu", 10, 3, 0),
    ("beq", 10, 0, "done"),                    # the .string's terminating 0
    "wait:",
    ("lw", 5, 2, memmap.UART_STATUS - memmap.UART_BASE),
    ("bne", 5, 0, "wait"),
    ("sb", 10, 2, memmap.UART_TXDATA - memmap.UART_BASE),
    ("addi", 3, 3, 1),
    ("jal", 0, "next"),
    "done:",
    ("halt",),
    "msg:",
    (".string", "Hello from RAM!\n"),
]

# Tables in the program image: sum .words with lw, read .bytes with sign and zero extension, and
# jump through a table of code addresses (lw then jalr).
TABLE = [
    ("addi", 3, 0, "words"),                   # x3 -> word table
    ("addi", 4, 0, 4),                         # 4 entries
    ("addi", 5, 0, 0),                         # sum
    "sum:",
    ("lw", 6, 3, 0),
    ("add", 5, 5, 6),
    ("addi", 3, 3, 4),
    ("addi", 4, 4, -1),
    ("bne", 4, 0, "sum"),                      # x5 = 0x80001111
    ("addi", 9, 0, "bytes"),
    ("lb", 7, 9, 0),                           # 0xFE -> 0xFFFFFFFE
    ("lbu", 8, 9, 1),                          # 0x80
    ("lh", 11, 9, 2),                          # 0x1234
    ("addi", 13, 0, "jumps"),
    ("lw", 12, 13, 4),                         # address of "two"
    ("jalr", 1, 12, 0),
    "one:",
    ("addi", 14, 0, 1),                        # skipped
    ("halt",),
    "two:",
    ("addi", 14, 0, 2),
    ("halt",),
    "words:",
    (".word", 1, 0x10, 0x100, 0x80001000),
    "bytes:",
    (".byte", 0xFE, 0x80, 0x34, 0x12),
    "jumps:",
    (".word", "one", "two"),
]

# Self-modifying code. A store over an instruction two ahead takes effect before it's fetched.
# A store over the very next instruction doesn't: the core prefetched it on the store's last
# edge (read-before-write), so the old word runs once and the new one on the second pass (legal
# without FENCE.I; see rv32sim's docstring). The final loads read the new words back as data.
SELFMOD = [
    ("lw", 6, 0, "new1"),
    ("sw", 6, 0, "t1"),
    ("addi", 0, 0, 0),                         # gap: t1 isn't fetched yet when the store lands
    "t1:",
    ("addi", 7, 0, 1),                         # replaced: runs as addi x7, x0, 11
    ("addi", 8, 0, 0),                         # pass counter
    ("addi", 9, 0, 0),                         # sum of x10 over both passes
    ("lw", 6, 0, "new2"),
    ("sw", 6, 0, "t2"),                        # over the next instruction, already prefetched
    "t2:",
    ("addi", 10, 0, 1),                        # pass 1: the old word (1); pass 2: the new (2)
    ("add", 9, 9, 10),
    ("addi", 8, 8, 1),
    ("addi", 11, 0, 2),
    ("blt", 8, 11, "t2"),                      # x9 = 3 with prefetch (4 without)
    ("lw", 12, 0, "t1"),                       # the code now holds the new words
    ("lw", 13, 0, "t2"),
    ("halt",),
    "new1:",
    (".word", rv32.addi(7, 0, 11)),
    "new2:",
    (".word", rv32.addi(10, 0, 2)),
]

# A trap handler for the programs below: records mcause (x13), mtval (x12) and mstatus (x15),
# counts traps (x14), and returns past the trapping instruction (mepc + 4).
TRAP_HANDLER = [
    "handler:",
    ("csrrs", 13, 0x342, 0),
    ("csrrs", 12, 0x343, 0),
    ("csrrs", 15, 0x300, 0),                   # inside the handler: MIE = 0, MPIE = the old MIE
    ("addi", 14, 14, 1),
    ("csrrs", 11, 0x341, 0),
    ("addi", 11, 11, 4),
    ("csrrw", 0, 0x341, 11),
    ("mret",),
]

# Illegal encodings trap (cause 2, mtval = the word) and do nothing else: nothing written, no
# store, no jump or branch. FENCE and WFI are legal no-ops. Then it jumps to the UART: devices
# answer loads only, so every fetch there reads 0 and traps as illegal (mepc = the I/O address).
ILLEGAL = [
    ("addi", 4, 4, 1),                              # counts runs of word 0: a fetch from I/O
                                                    # that aliased onto RAM would run it again
    ("addi", 3, 0, "handler"),
    ("csrrw", 0, 0x305, 3),                         # mtvec = handler
    ("addi", 5, 0, 5),
    ("addi", 6, 0, 6),
    ("sw", 6, 0, DATA),
    (".word", 0x00000000),                          # all zeros
    (".word", 0xFFFFFFFF),                          # all ones
    (".word", rv32.i_type(0x13, 5, 1, 5, 0x401)),   # slli x5, x5, 1 with funct7 = 0x20
    (".word", rv32.i_type(0x03, 5, 3, 0, DATA)),    # ld x5, DATA(x0): RV64 only
    (".word", rv32.s_type(0x23, 3, 0, 5, DATA)),    # sd x5, DATA(x0): RV64 only, mustn't store
    (".word", rv32.b_type(0x63, 2, 5, 6, -8)),      # branch funct3 = 2 (loops back if taken)
    (".word", rv32.i_type(0x67, 1, 1, 0, 0)),       # jalr funct3 = 1 (would jump to 0)
    (".word", rv32.addi(0, 5, 1) | (16 << 7)),      # addi x16, x5, 1: RV32E has no x16
    (".word", rv32.r_type(0x33, 5, 1, 5, 6, 0x20)), # sll with funct7 = 0x20
    (".word", rv32.encode("csrrw", 5, 6, imm=0xC00)),  # csrrw x5, cycle, x6: not implemented
    (".word", rv32.encode("csrrw", 5, 6, imm=0xF11)),  # csrrw x5, mvendorid, x6: read-only
    (".word", 0x0000100F),                          # fence.i: Zifencei not implemented
    ("fence",),                                     # (no WFI here since it really waits: nothing to wake it)
    ("lw", 7, 0, DATA),                             # still 6: the sd didn't store
    ("lui", 2, memmap.UART_BASE >> 12),
    ("jalr", 1, 2, 0),                              # run from the UART: a trap per word
    *TRAP_HANDLER,
]

# The other synchronous traps. ECALL (cause 11) and EBREAK (3) with mtval 0; MIE = 1 before the
# first, so the handler sees MPIE = 1, MIE = 0 and MRET restores MIE. Misaligned loads (4) and
# stores (6) with mtval = the address: no register written, memory unchanged; an aligned halfword
# is fine. A JALR to an address with bit 1 set traps (0, mtval = target) without writing rd; one
# with only bit 0 set doesn't (bit 0 is cleared). A taken branch to a misaligned target traps; a
# branch that isn't taken doesn't, whatever its target.
TRAPS = [
    ("addi", 3, 0, "handler"),
    ("csrrw", 0, 0x305, 3),
    ("csrrsi", 0, 0x300, 0x08),                # MIE = 1
    ("ecall",),
    ("csrrs", 2, 0x300, 0),                    # x2 = 0x1888: MRET put MIE back
    ("ebreak",),
    ("addi", 5, 0, 0x55),
    ("sw", 5, 0, DATA),
    ("lw", 6, 0, DATA + 2),                    # misaligned word: x6 stays 0
    ("lh", 6, 0, DATA + 1),                    # misaligned halfword
    ("lhu", 7, 0, DATA + 2),                   # aligned halfword: x7 = 0
    ("sw", 3, 0, DATA + 1),                    # misaligned stores: memory unchanged
    ("sh", 3, 0, DATA + 3),
    ("lw", 8, 0, DATA),                        # still 0x55
    ("addi", 9, 0, "land"),
    ("addi", 9, 9, 2),
    ("jalr", 1, 9, 0),                         # target land + 2: traps, x1 stays 0
    ("addi", 9, 9, -1),
    ("jalr", 1, 9, 0),                         # land + 1: bit 0 cleared, lands on land
    ("addi", 10, 0, 99),                       # skipped
    "land:",
    (".word", rv32.b_type(0x63, 0, 0, 0, 6)),  # beq x0, x0, +6: taken, misaligned: traps
    (".word", rv32.b_type(0x63, 1, 0, 0, 6)),  # bne x0, x0, +6: not taken: no trap
    ("halt",),
    *TRAP_HANDLER,
]

SCROLL_TEXT = "    HELLO FROM AN RV32E SOFT CORE ON THE ALCHITRY CU    "   # 4 spaces each end

# What the board boots. A stub at 0 reads the switch register (the middle DIP bank) once after
# reset and jumps through a table: bits 2:0 pick the program, so change the DIPs and press reset.
#   0  Larson scanner on x1 (at its original addresses: its turnaround jal links 0x80 = LED 7)
#   1  hello: a banner on the serial port, then a Larson sweep on x1 and a numbered line per sweep
#   2  buttons: a banner, then Larson on x1; each press of a direction button interrupts and the
#      handler prints "buttons N" (N = the pending mask in hex: 1 up, 2 down, 4 left, 8 right),
#      plus " lost M" when presses of buttons M came while they were still pending (overrun)
#   3  the timing ladder, rung 1: Larson with a delay loop between steps
#   4  rung 2: Larson stepping when a polled mtime passes a deadline
#   5  rung 3: Larson stepped by the timer interrupt (x13 = direction); the main loop idles
#   6  scroll: SCROLL_TEXT across the 7-segment display. The display device holds four characters
#      (the board has the font and scans the digits); each timer interrupt, every
#      (switches[7:3] << 15) + 128 mtime ticks, moves the text one character and writes the four
#   7  C: c/demo.c, linked at 0x800 (cbuild.ORIGINS): `Hello from C!`, a multiply/divide/remainder, then a table
#      of clocks per call for mul, div and mod at three operand sizes (measured with mtime); then it halts
# Rungs 1-3 step every (switches[7:3] << 13) + 64 mtime ticks (the "dial"; 1 us on the board:
# dial 31 is 254 ms, ~4 steps a second). Rung 1 converts ticks to delay-loop passes with the
# timer's timebase word (clocks per tick), calibrated for the cpu at full speed: at the fast
# setting all three keep the same pace; at the slow one (~24 Hz cpu) rung 1 crawls, a million
# times slower, while rungs 2-3 keep real time as well as a slow cpu can (they overload).
# The serial port is the Cu's USB (FTDI) at 1 Mbaud, 8N1. Registers: x1 LEDs, x2 device base,
# x3 string pointer / menu index, x4 menu target / digit count, x5 putc's link, x6 puts/hex's
# link, x7 STATUS, x8 sweep count, x9 hex shift register, x10 the character.
BOARD = [
    ("lui", 2, memmap.SWITCHES_BASE >> 12),
    ("lw", 3, 2, memmap.SWITCHES_BASE & 0xFFF),
    ("andi", 3, 3, 7),
    ("slli", 3, 3, 2),
    ("lw", 4, 3, "menu"),                      # menu + 4 * choice
    ("jalr", 0, 4, 0),
    "menu:",
    (".word", "turn", "hello", "buttons_demo", "delay_larson",
              "poll_larson", "irq_larson", "scroll_demo", "c_demo"),
    *LARSON[2:],                               # .org 0x48, right/left/turn as on the old ROM
    "hello:",
    ("lui", 2, memmap.UART_BASE >> 12),
    ("addi", 3, 0, "banner"),
    ("jal", 6, "puts"),
    ("addi", 1, 0, 1),
    "sweep:",
    ("addi", 8, 8, 1),
    ("addi", 3, 0, "sweep_msg"),
    ("jal", 6, "puts"),
    ("jal", 6, "hex"),
    ("addi", 3, 0, "crlf"),
    ("jal", 6, "puts"),
    *[("slli", 1, 1, 1)] * 7,
    *[("srli", 1, 1, 1)] * 7,
    ("jal", 0, "sweep"),
    "buttons_demo:",
    ("lui", 2, memmap.UART_BASE >> 12),
    ("addi", 3, 0, "press_msg"),
    ("jal", 6, "puts"),
    ("lui", 11, memmap.BUTTONS_BASE >> 12),    # x11 = buttons base
    ("addi", 3, 0, "button_handler"),
    ("csrrw", 0, 0x305, 3),
    ("addi", 8, 0, 1),
    ("slli", 8, 8, 11),
    ("csrrs", 0, 0x304, 8),                    # MEIE
    ("csrrsi", 0, 0x300, 0x08),                # MIE
    ("addi", 1, 0, 1),
    "bsweep:",
    *[("slli", 1, 1, 1)] * 7,
    *[("srli", 1, 1, 1)] * 7,
    ("jal", 0, "bsweep"),
    "button_handler:",                         # main only uses x1, so no registers are saved
    ("lw", 12, 11, 0),                         # pending presses [3:0], overrun [7:4]
    ("sw", 12, 11, 0),                         # clear exactly those (a press on this edge wins)
    ("addi", 3, 0, "buttons_msg"),
    ("jal", 6, "puts"),
    ("srli", 13, 12, 4),
    ("andi", 13, 13, 0xF),                     # x13 = buttons pressed again while still pending
    ("andi", 12, 12, 0xF),
    ("lbu", 10, 12, "digits"),
    ("jal", 5, "putc"),
    ("beq", 13, 0, "button_done"),
    ("addi", 3, 0, "lost_msg"),
    ("jal", 6, "puts"),
    ("lbu", 10, 13, "digits"),
    ("jal", 5, "putc"),
    "button_done:",
    ("addi", 3, 0, "crlf"),
    ("jal", 6, "puts"),
    ("mret",),
    # The timing ladder: the same Larson three ways, each step one dial period apart
    # (period: switches 7:3). (1) A delay loop: time is counted in instructions. (2) Polling
    # mtime against a deadline: time comes from a counter, but the cpu still does nothing else.
    # (3) The timer interrupt: the handler takes one step and moves mtimecmp on; the main loop
    # waits in wfi. mtime counts real time (since single-clock step 4), so (2) and (3) keep their
    # pace whatever the clock switches do, and (1) doesn't: the demonstration. At the slow setting
    # (2) and (3) overload instead: a handler takes longer than a period, so they step as fast as
    # the cpu can, which is still slow.
    "delay_larson:",                           # calibrated like a BogoMIPS loop: passes = ticks *
    ("jal", 6, "period"),                      # (clocks per tick) / (4 clocks per pass), so at
    ("srli", 9, 9, 2),                         # full speed it keeps pace with rungs 2-3
    ("lui", 2, memmap.TIMER_BASE >> 12),
    ("lw", 5, 2, memmap.TIMER_TIMEBASE - memmap.TIMER_BASE),   # clocks per tick (no MUL: add it up)
    ("addi", 4, 0, 0),
    "calibrate:",
    ("add", 4, 4, 9),
    ("addi", 5, 5, -1),
    ("bne", 5, 0, "calibrate"),
    ("addi", 9, 4, 0),                         # x9 = passes per step
    ("addi", 1, 0, 1),
    "dsweep:",
    *[("slli", 1, 1, 1), ("jal", 5, "delay")] * 7,
    *[("srli", 1, 1, 1), ("jal", 5, "delay")] * 7,
    ("jal", 0, "dsweep"),
    "delay:",                                  # x9 passes (addi + bne = 4 clocks); returns through x5
    ("addi", 4, 9, 0),
    "delay_loop:",
    ("addi", 4, 4, -1),
    ("bne", 4, 0, "delay_loop"),
    ("jalr", 0, 5, 0),
    "poll_larson:",
    ("jal", 6, "period"),
    ("lui", 2, memmap.TIMER_BASE >> 12),
    ("lw", 7, 2, memmap.TIMER_MTIME - memmap.TIMER_BASE),
    ("add", 7, 7, 9),                          # x7 = deadline of the first step
    ("addi", 1, 0, 1),
    "psweep:",
    *[("slli", 1, 1, 1), ("jal", 5, "tick")] * 7,
    *[("srli", 1, 1, 1), ("jal", 5, "tick")] * 7,
    ("jal", 0, "psweep"),
    "tick:",                                   # wait for the deadline, then move it on one period
    ("lw", 4, 2, memmap.TIMER_MTIME - memmap.TIMER_BASE),
    ("sub", 4, 4, 7),
    ("blt", 4, 0, "tick"),                     # signed difference: fine across the wrap
    ("add", 7, 7, 9),                          # from the deadline, not from now: no drift
    ("jalr", 0, 5, 0),
    "irq_larson:",
    ("jal", 6, "period"),
    ("lui", 2, memmap.TIMER_BASE >> 12),
    ("addi", 3, 0, "larson_tick"),
    ("csrrw", 0, 0x305, 3),                    # mtvec
    ("sw", 0, 2, memmap.TIMER_MTIMECMPH - memmap.TIMER_BASE),  # mtime is still small after boot
    ("lw", 7, 2, memmap.TIMER_MTIME - memmap.TIMER_BASE),
    ("add", 7, 7, 9),
    ("sw", 7, 2, memmap.TIMER_MTIMECMP - memmap.TIMER_BASE),   # first step one period from now
    ("addi", 1, 0, 1),
    ("addi", 13, 0, 1),                        # direction: 1 = left
    ("addi", 8, 0, 0x80),
    ("csrrs", 0, 0x304, 8),                    # MTIE
    ("csrrsi", 0, 0x300, 0x08),                # MIE
    "idle:",
    ("wfi",),
    ("jal", 0, "idle"),
    "larson_tick:",                            # one step per interrupt: where the light is lives
    ("beq", 13, 0, "tick_right"),              # in x1 and x13 now, not in the pc
    ("slli", 1, 1, 1),
    ("addi", 4, 0, 0x80),
    ("bne", 1, 4, "rearm"),
    ("addi", 13, 0, 0),
    ("jal", 0, "rearm"),
    "tick_right:",
    ("srli", 1, 1, 1),
    ("addi", 4, 0, 1),
    ("bne", 1, 4, "rearm"),
    ("addi", 13, 0, 1),
    "rearm:",                                  # mtimecmp += period, 64-bit
    ("lw", 4, 2, memmap.TIMER_MTIMECMP - memmap.TIMER_BASE),
    ("add", 5, 4, 9),
    ("sltu", 4, 5, 4),                         # carry out of the low word
    ("lw", 8, 2, memmap.TIMER_MTIMECMPH - memmap.TIMER_BASE),
    ("add", 8, 8, 4),
    ("sw", 8, 2, memmap.TIMER_MTIMECMPH - memmap.TIMER_BASE),  # high word first: the compare
    ("sw", 5, 2, memmap.TIMER_MTIMECMP - memmap.TIMER_BASE),   # only ever moves later
    ("mret",),
    "period:",                                 # x9 = clocks per step = (switches[7:3] << 13) + 64;
    ("lui", 2, memmap.SWITCHES_BASE >> 12),    # returns through x6
    ("lw", 9, 2, memmap.SWITCHES_BASE & 0xFFF),
    ("srli", 9, 9, 3),
    ("slli", 9, 9, 13),
    ("addi", 9, 9, 64),
    ("jalr", 0, 6, 0),
    "scroll_demo:",
    ("lui", 2, memmap.SWITCHES_BASE >> 12),
    ("lw", 9, 2, memmap.SWITCHES_BASE & 0xFFF),
    ("srli", 9, 9, 3),
    ("slli", 9, 9, 15),
    ("addi", 9, 9, 128),                       # x9 = clocks per scroll step: (dial << 15) + 128
    ("lui", 3, memmap.DISPLAY_BASE >> 12),     # x3 = display
    ("addi", 12, 0, "scroll_text"),            # x12 = the first character shown
    ("jal", 6, "show"),
    ("lui", 2, memmap.TIMER_BASE >> 12),
    ("addi", 4, 0, "scroll_tick"),
    ("csrrw", 0, 0x305, 4),                    # mtvec
    ("sw", 0, 2, memmap.TIMER_MTIMECMPH - memmap.TIMER_BASE),
    ("lw", 7, 2, memmap.TIMER_MTIME - memmap.TIMER_BASE),
    ("add", 7, 7, 9),
    ("sw", 7, 2, memmap.TIMER_MTIMECMP - memmap.TIMER_BASE),
    ("addi", 8, 0, 0x80),
    ("csrrs", 0, 0x304, 8),                    # MTIE
    ("csrrsi", 0, 0x300, 0x08),                # MIE
    ("jal", 0, "idle"),
    "scroll_tick:",                            # one character on
    ("addi", 12, 12, 1),
    ("lbu", 4, 12, 3),
    ("bne", 4, 0, "scroll_show"),              # the window ran past the end: back to the start
    ("addi", 12, 0, "scroll_text"),
    "scroll_show:",
    ("jal", 6, "show"),
    ("jal", 0, "rearm"),                       # mtimecmp += x9, mret (x4, x5, x8 are free here)
    "show:",                                   # the four characters at x12 to the display (byte
    ("lbu", 4, 12, 0),                         # k = digit k from the left); returns through x6
    ("sb", 4, 3, 0),
    ("lbu", 4, 12, 1),
    ("sb", 4, 3, 1),
    ("lbu", 4, 12, 2),
    ("sb", 4, 3, 2),
    ("lbu", 4, 12, 3),
    ("sb", 4, 3, 3),
    ("jalr", 0, 6, 0),
    "puts:",                                   # print the .string at x3; returns through x6
    ("lbu", 10, 3, 0),
    ("beq", 10, 0, "puts_end"),
    ("jal", 5, "putc"),
    ("addi", 3, 3, 1),
    ("jal", 0, "puts"),
    "puts_end:",
    ("jalr", 0, 6, 0),
    "hex:",                                    # print x8 as 8 hex digits; returns through x6
    ("addi", 4, 0, 8),
    ("addi", 9, 8, 0),
    "hex_next:",
    ("srli", 10, 9, 28),
    ("lbu", 10, 10, "digits"),
    ("jal", 5, "putc"),
    ("slli", 9, 9, 4),
    ("addi", 4, 4, -1),
    ("bne", 4, 0, "hex_next"),
    ("jalr", 0, 6, 0),
    "putc:",                                   # send x10 once STATUS says not busy; returns through x5
    ("lw", 7, 2, memmap.UART_STATUS - memmap.UART_BASE),
    ("bne", 7, 0, "putc"),
    ("sb", 10, 2, memmap.UART_TXDATA - memmap.UART_BASE),
    ("jalr", 0, 5, 0),
    "banner:",
    (".string", "\r\nHello from an RV32E soft core on the Alchitry Cu\r\n"),
    "sweep_msg:",
    (".string", "sweep "),
    "press_msg:",
    (".string", "\r\nPress a direction button\r\n"),
    "buttons_msg:",
    (".string", "buttons "),
    "lost_msg:",
    (".string", " lost "),
    "crlf:",
    (".string", "\r\n"),
    "digits:",
    (".string", "0123456789ABCDEF"),
    "scroll_text:",
    (".string", SCROLL_TEXT),
    (".org", 0x800),                           # above the demos (they end below 0x500)
    "c_demo:",
    *cbuild.items("demo"),
]

# Zicsr and MRET: read the constant CSRs, write/set/clear mscratch (rd gets the old value each
# time), the WARL masks of mtvec/mstatus/mie/mepc, a write to misa ignored, then MRET: pc = mepc
# (low bits read 0), MIE = MPIE, MPIE = 1. A WFI with the timer pending and MIE = 0 just completes.
CSR = [
    ("csrrs", 1, 0x301, 0),                    # x1 = misa = 0x40000010 (RV32E)
    ("csrrs", 2, 0xF14, 0),                    # x2 = mhartid = 0
    ("addi", 3, 0, 0x123),
    ("csrrw", 4, 0x340, 3),                    # x4 = 0 (old mscratch); mscratch = 0x123
    ("csrrsi", 5, 0x340, 0x0C),                # x5 = 0x123; mscratch = 0x12F
    ("csrrci", 6, 0x340, 0x03),                # x6 = 0x12F; mscratch = 0x12C
    ("csrrs", 7, 0x340, 0),                    # x7 = 0x12C (x0 source: read only)
    ("addi", 8, 0, -1),
    ("csrrw", 0, 0x305, 8),                    # mtvec: MODE 1 kept, bit 1 reserved
    ("csrrs", 9, 0x305, 0),                    # x9 = 0xFFFFFFFD
    ("csrrw", 0, 0x304, 8),
    ("csrrs", 10, 0x304, 0),                   # x10 = 0x888 (MSIE, MTIE, MEIE)
    ("csrrw", 11, 0x301, 8),                   # x11 = misa; the write is ignored
    ("csrrs", 11, 0x301, 0),                   # still 0x40000010
    ("csrrw", 0, 0x342, 8),                    # mcause: all 32 bits kept ...
    ("csrrs", 3, 0x342, 0),                    # ... x3 = 0xFFFFFFFF
    ("addi", 12, 0, "back"),
    ("addi", 12, 12, 3),
    ("csrrw", 0, 0x341, 12),                   # mepc = back + 3: reads back aligned
    ("csrrs", 13, 0x341, 0),                   # x13 = back
    ("csrrwi", 0, 0x300, 0x08),                # mstatus: MIE = 1, MPIE = 0
    ("csrrs", 2, 0x300, 0),                    # x2 = 0x1808 (MIE set by bit 3 alone)
    ("mret",),
    ("addi", 14, 0, 1),                        # skipped
    "back:",
    ("csrrs", 14, 0x300, 0),                   # x14 = 0x1880: MPP = 3, MPIE = 1, MIE = 0
    ("lui", 1, memmap.TIMER_BASE >> 12),
    ("sw", 0, 1, memmap.TIMER_MTIMECMPH - memmap.TIMER_BASE),
    ("sw", 0, 1, memmap.TIMER_MTIMECMP - memmap.TIMER_BASE),   # mtimecmp = 0: MTIP pending
    ("wfi",),                                  # MTIE (set above) + MTIP wake it at once; MIE = 0: no trap
    ("sw", 8, 1, memmap.TIMER_MTIMECMPH - memmap.TIMER_BASE),  # disarm before MIE is set below
    ("csrrw", 15, 0x300, 8),                   # x15 = 0x1880; mstatus = all ones ...
    ("csrrs", 15, 0x300, 0),                   # ... reads 0x1888
    ("halt",),
]

# The machine timer and its interrupt. mtime ticks every clock in the testbenches (TICK_DIV 1, ce
# every clock), so every value read here and every interrupt's position is exact (rv32sim counts
# the same clocks). Reads of mtime/mtimeh/mtimecmp,
# the 64-bit carry, MTIP visible in mip while MIE = 0 (no interrupt), then three timer interrupts
# 150 ticks apart: the handler records mcause (0x80000007) and mepc, and moves mtimecmp on. (It
# was 40: with the timer at 3 ticks per cpu clock, the _ce3 test, the handler took longer than
# that and was interrupted again at every mret.)
T = memmap.TIMER_BASE
TIMER = [
    ("addi", 3, 0, "timer_handler"),
    ("csrrw", 0, 0x305, 3),                    # mtvec
    ("lui", 2, T >> 12),                       # x2 = timer base
    ("lw", 3, 2, memmap.TIMER_TIMEBASE - T),   # x3 = 1: clocks per tick in the testbenches
    ("lw", 4, 2, memmap.TIMER_MTIME - T),      # mtime as of this load's MEM_ACCESS
    ("lw", 5, 2, memmap.TIMER_MTIMEH - T),     # 0
    ("lw", 6, 2, memmap.TIMER_MTIMECMP - T),   # 0xFFFFFFFF after reset
    ("csrrs", 7, 0x344, 0),                    # mip = 0
    ("addi", 8, 0, -16),
    ("sw", 8, 2, memmap.TIMER_MTIME - T),      # mtime = 0x00000000_FFFFFFF0
    ("lw", 9, 2, memmap.TIMER_MTIMEH - T),     # 0: not wrapped yet
    *[("addi", 0, 0, 0)] * 6,
    ("lw", 9, 2, memmap.TIMER_MTIMEH - T),     # 1: carried into the high word
    ("lw", 8, 2, memmap.TIMER_MTIME - T),
    ("sw", 0, 2, memmap.TIMER_MTIME - T),      # mtime = 0x00000001_00000000 ...
    ("sw", 0, 2, memmap.TIMER_MTIMEH - T),     # ... then 0 (+ the clocks since)
    ("sw", 0, 2, memmap.TIMER_MTIMECMPH - T),
    ("sw", 0, 2, memmap.TIMER_MTIMECMP - T),   # mtimecmp = 0: pending from the next compare
    ("csrrs", 10, 0x344, 0),                   # x10 = 0x80 (MTIP)
    ("addi", 8, 0, 0x80),
    ("csrrs", 0, 0x304, 8),                    # MTIE = 1, but MIE = 0: no interrupt
    ("csrrs", 10, 0x344, 0),                   # still pending
    ("lw", 4, 2, memmap.TIMER_MTIME - T),
    ("addi", 4, 4, 150),
    ("sw", 4, 2, memmap.TIMER_MTIMECMP - T),   # first interrupt ~150 ticks from now
    ("addi", 6, 0, 3),
    ("csrrsi", 0, 0x300, 0x08),                # MIE = 1
    "spin:",
    ("addi", 5, 5, 1),
    ("bne", 14, 6, "spin"),
    ("csrrci", 0, 0x300, 0x08),                # MIE = 0
    ("halt",),
    "timer_handler:",
    ("csrrs", 13, 0x342, 0),                   # 0x80000007
    ("csrrs", 12, 0x341, 0),                   # where the spin loop was interrupted
    ("addi", 14, 14, 1),
    ("lw", 11, 2, memmap.TIMER_MTIMECMP - T),
    ("addi", 11, 11, 150),
    ("sw", 11, 2, memmap.TIMER_MTIMECMP - T),  # next interrupt 150 ticks after this one was due
    ("csrrs", 15, 0x300, 0),                   # 0x1880 inside the handler
    ("mret",),
]

# The buttons device and the external interrupt. Presses come from BUTTON_EVENTS at exact clocks;
# each rising edge latches a pending bit and MEIP interrupts (mcause 0x8000000B). (1) MIE = 1 but
# MEIE = 0: a press shows in mip, nothing is taken; setting MEIE then interrupts at once. (2) The
# timer and a press pending together (MIE = 0 while they arrive): enabling MIE takes the external
# one first, then the timer (the spec's priority). (3) A last press: four interrupts in all. The
# handler records mcause/mepc, clears exactly the pending buttons, or disarms the timer.
B = memmap.BUTTONS_BASE
IRQ = [
    ("addi", 3, 0, "irq_handler"),
    ("csrrw", 0, 0x305, 3),
    ("lui", 2, B >> 12),                       # x2 = buttons base
    ("lui", 3, memmap.TIMER_BASE >> 12),       # x3 = timer base
    ("lw", 4, 2, 0),                           # 0: nothing pending, nothing pressed
    ("addi", 15, 0, 7),                        # the timer's cause code, for the handler
    ("csrrsi", 0, 0x300, 0x08),                # MIE = 1, MEIE = MTIE = 0
    "wait_press:",
    ("csrrs", 7, 0x344, 0),
    ("beq", 7, 0, "wait_press"),               # x7 = 0x800 (MEIP): pending, not taken
    ("addi", 8, 0, 1),
    ("slli", 8, 8, 11),
    ("csrrs", 0, 0x304, 8),                    # MEIE: interrupt 1 at once
    ("csrrci", 0, 0x300, 0x08),                # MIE = 0 while both sources become pending
    ("sw", 0, 3, memmap.TIMER_MTIMECMPH - memmap.TIMER_BASE),
    ("sw", 0, 3, memmap.TIMER_MTIMECMP - memmap.TIMER_BASE),   # MTIP pending
    ("addi", 8, 0, 0x80),
    ("csrrs", 0, 0x304, 8),                    # MTIE
    "wait_second:",
    ("lw", 9, 2, 0),
    ("andi", 9, 9, 0xF),
    ("beq", 9, 0, "wait_second"),              # the second press is pending too
    ("addi", 6, 0, 4),
    ("csrrsi", 0, 0x300, 0x08),                # MIE: external (2), then timer (3)
    "irq_spin:",
    ("addi", 5, 5, 1),
    ("bne", 14, 6, "irq_spin"),                # ... and the third press (4)
    ("csrrci", 0, 0x300, 0x08),
    ("lw", 9, 2, 0),                           # levels [11:8] (the last press still held), none pending
    ("halt",),
    "irq_handler:",
    ("csrrs", 13, 0x342, 0),                   # 0x8000000B, or 0x80000007 for the timer
    ("csrrs", 12, 0x341, 0),
    ("addi", 14, 14, 1),
    ("andi", 11, 13, 0xF),
    ("beq", 11, 15, "irq_timer"),
    ("lw", 10, 2, 0),                          # which buttons (and the levels)
    ("sw", 10, 2, 0),                          # clear exactly those
    ("csrrs", 7, 0x344, 0),                    # mip: MEIP gone (MTIP may still be set)
    ("mret",),
    "irq_timer:",
    ("addi", 11, 0, -1),
    ("sw", 11, 3, memmap.TIMER_MTIMECMP - memmap.TIMER_BASE),  # disarm: mtimecmp = 0x0_FFFFFFFF
    ("mret",),
]

# A second press of the same button around the handler's clear, timed to the clock (BUTTON_EVENTS,
# worked out with rv32sim; the handler is lw pending / sw it back to clear / count / mret):
#  (A) press at 20: interrupt at 22, handler lw at 24, sw at 28, which clears on edge 30. Re-press
#      at 28: its rising edge is detected on edge 30 too, and "set wins" keeps the bit: a second
#      interrupt (37). Clear-wins would lose it.
#  (B) press at 108: interrupt at 110, lw at 112 (reads as of edge 113), sw at 116 (clears on 118).
#      Re-press at 115: its edge (117) finds the bit already set, so nothing changes, and the clear
#      removes both presses: merged, no interrupt for it. This is the press a pending *bit* can't
#      keep: from a button's first press until the handler clears it, further presses of that
#      button are absorbed (see notes 2026-10-16 for what would keep them). It does set the
#      button's overrun bit, which the handler's clear (the value it read) leaves alone.
# Four presses, three interrupts; the program then waits to show no fourth arrives, reads the
# overrun bit (case A set none, case B one), clears it and reads again.
PRESS = [
    ("addi", 3, 0, "press_handler"),
    ("csrrw", 0, 0x305, 3),
    ("lui", 2, B >> 12),
    ("addi", 8, 0, 1),
    ("slli", 8, 8, 11),
    ("csrrs", 0, 0x304, 8),                    # MEIE
    ("csrrsi", 0, 0x300, 0x08),                # MIE
    ("addi", 6, 0, 3),
    "press_wait:",
    ("bne", 14, 6, "press_wait"),
    ("addi", 7, 0, 20),
    "press_more:",                             # time for a fourth interrupt that mustn't come
    ("addi", 7, 7, -1),
    ("bne", 7, 0, "press_more"),
    ("csrrci", 0, 0x300, 0x08),
    ("lw", 9, 2, 0),                           # x9 = 0x10: button 0's overrun, nothing pending
    ("sw", 9, 2, 0),                           # clear it
    ("lw", 11, 2, 0),                          # x11 = 0
    ("halt",),
    "press_handler:",
    ("lw", 10, 2, 0),
    ("sw", 10, 2, 0),
    ("or", 12, 12, 10),                        # every value read: no overrun bit (case A sets none,
                                               # case B's comes after its read)
    ("addi", 14, 14, 1),
    ("mret",),
]

# Vectored mtvec (MODE = 1, BASE 0x100): an interrupt enters at BASE | cause << 2, an exception at
# BASE. (a) the timer (cause 7, slot 0x11C), (b) ECALL (cause 11, slot 0x100: the exception slot,
# which the *external* interrupt's number 11 must not be confused with), (c) a button press
# (BUTTON_EVENTS; external, cause 11, slot 0x12C). Each handler ORs its mcause into a register, so
# the cause and the slot both reach the final registers. Then the platform lines (PLAT_EVENTS, causes
# 16 and 17, slots 0x140 and 0x144; BASE is 128-byte aligned for 32 slots): (d) line 0 masked is
# visible in mip and not taken, unmasked it is; (e) both at once go in order, 16 then 17.
VEC = 0x100
VECTORED = [
    ("addi", 3, 0, VEC | 1),
    ("csrrw", 0, 0x305, 3),                    # mtvec = BASE | MODE 1
    ("csrrs", 9, 0x305, 0),                    # x9 = 0x101
    ("lui", 2, T >> 12),                       # x2 = timer
    ("lui", 11, B >> 12),                      # x11 = buttons
    ("addi", 5, 0, -1),
    ("sw", 0, 2, memmap.TIMER_MTIMECMPH - T),
    ("addi", 8, 0, 0x80),
    ("csrrs", 0, 0x304, 8),                    # MTIE
    ("lw", 4, 2, memmap.TIMER_MTIME - T),
    ("addi", 4, 4, 60),
    ("sw", 4, 2, memmap.TIMER_MTIMECMP - T),
    ("csrrsi", 0, 0x300, 0x08),                # MIE
    "vec_timer_wait:",
    ("beq", 14, 0, "vec_timer_wait"),          # (a) the timer interrupt
    ("ecall",),                                # (b) an exception: the BASE slot
    ("addi", 8, 0, 1),
    ("slli", 8, 8, 11),
    ("csrrs", 0, 0x304, 8),                    # MEIE
    "vec_button_wait:",
    ("beq", 15, 0, "vec_button_wait"),         # (c) the button's external interrupt
    # (d) platform line 0 (PLAT_EVENTS: high from clock 500 to 560) while its mie bit is clear: mip
    # shows it (x1), nothing is taken; unmasking takes it at once (slot 16) and the handler masks it
    "vec_plat_wait0:",
    ("csrrs", 1, 0x344, 0),
    ("srli", 6, 1, 16),
    ("andi", 6, 6, 1),
    ("beq", 6, 0, "vec_plat_wait0"),
    ("addi", 8, 0, 1),
    ("slli", 8, 8, 16),
    ("csrrs", 0, 0x304, 8),                    # unmask line 0
    # (e) both lines high from clock 600: unmask both; the lower cause goes first (slot 16), its
    # handler masks it, MRET, then slot 17. x4 logs the order (0x16 then 0x17 appended per taken slot)
    "vec_plat_wait1:",
    ("csrrs", 1, 0x344, 0),
    ("srli", 6, 1, 17),
    ("andi", 6, 6, 1),
    ("beq", 6, 0, "vec_plat_wait1"),
    ("addi", 8, 0, 3),
    ("slli", 8, 8, 16),
    ("csrrs", 0, 0x304, 8),                    # unmask both
    ("addi", 0, 0, 0),                         # the interrupts land here, not on the halt (the
    ("addi", 0, 0, 0),                         # generators count a fetched halt as halted)
    ("halt",),
    (".org", VEC),
    ("jal", 0, "vec_exception"),               # slot 0: exceptions
    (".org", VEC + 4 * 7),
    ("jal", 0, "vec_timer"),                   # slot 7
    (".org", VEC + 4 * 11),
    ("jal", 0, "vec_external"),                # slot 11
    (".org", VEC + 4 * 16),
    ("jal", 0, "vec_plat0"),                   # slot 16: platform line 0
    ("jal", 0, "vec_plat1"),                   # slot 17: platform line 1
    "vec_timer:",
    ("csrrs", 10, 0x342, 0),                   # x10 = 0x80000007
    ("sw", 5, 2, memmap.TIMER_MTIMECMPH - T),  # disarm
    ("addi", 14, 14, 1),
    ("mret",),
    "vec_exception:",
    ("csrrs", 12, 0x342, 0),                   # x12 = 11
    ("csrrs", 7, 0x341, 0),
    ("addi", 7, 7, 4),
    ("csrrw", 0, 0x341, 7),                    # return after the ECALL
    ("mret",),
    "vec_external:",
    ("csrrs", 13, 0x342, 0),                   # x13 = 0x8000000B
    ("lw", 6, 11, 0),
    ("sw", 6, 11, 0),                          # clear the press
    ("addi", 15, 15, 1),
    ("mret",),
    "vec_plat0:",
    ("csrrs", 2, 0x342, 0),                    # x2 = 0x80000010
    ("addi", 5, 0, 1),
    ("slli", 5, 5, 16),
    ("csrrc", 0, 0x304, 5),                    # mask line 0 (a level: it would take it again)
    ("addi", 6, 0, 0x16),
    ("slli", 4, 4, 8),
    ("or", 4, 4, 6),
    ("mret",),
    "vec_plat1:",
    ("csrrs", 3, 0x342, 0),                    # x3 = 0x80000011
    ("addi", 5, 0, 1),
    ("slli", 5, 5, 17),
    ("csrrc", 0, 0x304, 5),
    ("addi", 6, 0, 0x17),
    ("slli", 4, 4, 8),
    ("or", 4, 4, 6),
    ("mret",),
]

# WFI: the cpu waits in EXECUTE until an interrupt enabled in mie is pending. (a) MIE = 1: the timer
# interrupt (60 ticks ahead) wakes it and is taken with mepc = the WFI + 4. (b) MIE = 0: the timer
# wakes it and it just completes (mip shows MTIP). (c) MIE = 1, MEIE only: a button press
# (BUTTON_EVENTS) wakes it. mtime after each wake goes into x5, x6, x9, so the wait lengths reach
# the final registers; the handler adds each mepc into x10 and counts in x14.
WFI_TEST = [
    ("addi", 3, 0, "wfi_handler"),
    ("csrrw", 0, 0x305, 3),                    # mtvec
    ("lui", 2, T >> 12),                       # x2 = timer
    ("lui", 11, B >> 12),                      # x11 = buttons
    ("sw", 0, 2, memmap.TIMER_MTIMECMPH - T),
    ("addi", 8, 0, 0x80),
    ("csrrs", 0, 0x304, 8),                    # MTIE
    ("lw", 4, 2, memmap.TIMER_MTIME - T),
    ("addi", 4, 4, 60),
    ("sw", 4, 2, memmap.TIMER_MTIMECMP - T),   # (a) an interrupt 60 ticks from now
    ("csrrsi", 0, 0x300, 0x08),                # MIE
    ("wfi",),
    ("lw", 5, 2, memmap.TIMER_MTIME - T),      # x5: after the wake and the handler
    ("csrrci", 0, 0x300, 0x08),                # (b) MIE = 0
    ("lw", 4, 2, memmap.TIMER_MTIME - T),
    ("addi", 4, 4, 60),
    ("sw", 4, 2, memmap.TIMER_MTIMECMP - T),   # low word first: the high word is all ones (disarmed)
    ("sw", 0, 2, memmap.TIMER_MTIMECMPH - T),
    ("wfi",),                                  # wakes on MTIP, no trap
    ("lw", 6, 2, memmap.TIMER_MTIME - T),      # x6
    ("csrrs", 7, 0x344, 0),                    # x7 = mip: 0x80 (MTIP)
    ("addi", 4, 0, -1),
    ("sw", 4, 2, memmap.TIMER_MTIMECMPH - T),  # disarm
    ("csrrc", 0, 0x304, 8),                    # MTIE = 0
    ("addi", 8, 0, 1),
    ("slli", 8, 8, 11),
    ("csrrs", 0, 0x304, 8),                    # (c) MEIE
    ("csrrsi", 0, 0x300, 0x08),                # MIE
    ("wfi",),                                  # a button press wakes it
    ("lw", 9, 2, memmap.TIMER_MTIME - T),      # x9
    ("csrrci", 0, 0x300, 0x08),
    ("halt",),
    "wfi_handler:",
    ("csrrs", 13, 0x342, 0),                   # x13 = mcause (the last one)
    ("csrrs", 12, 0x341, 0),                   # x12 = mepc: the instruction after the WFI
    ("add", 10, 10, 12),
    ("addi", 14, 14, 1),
    ("lw", 15, 11, 0),
    ("sw", 15, 11, 0),                         # clear any button presses
    ("addi", 4, 0, -1),
    ("sw", 4, 2, memmap.TIMER_MTIMECMPH - T),  # disarm the timer
    ("mret",),
]

PROGRAMS = {
    "BOARD": BOARD,
    "LARSON": LARSON,
    "ALU": ALU,
    "BRANCHES": BRANCHES,
    "JUMPS": JUMPS,
    "MEMORY": MEMORY,
    "BYTES": BYTES,
    "UART": UART,
    "STRING": STRING,
    "TABLE": TABLE,
    "SELFMOD": SELFMOD,
    "ILLEGAL": ILLEGAL,
    "CSR": CSR,
    "TRAPS": TRAPS,
    "TIMER": TIMER,
    "IRQ": IRQ,
    "PRESS": PRESS,
    "WFI": WFI_TEST,
    "VECTORED": VECTORED,
    "C_HELLO": cbuild.items("hello"),
    "C_COST": cbuild.items("cost"),     # c/cost.c: clocks per libgcc mul/div/mod, from mtime   # c/hello.c, built by tools/cbuild.py (image committed)
}

# Programs that never halt, and how many instructions to check (lockstep). One that prints is
# checked by its first PRINT_BYTES bytes instead.
RUNS_FOREVER = {"BOARD": 40, "LARSON": 34, "ILLEGAL": 160}
# ... per switch setting where that differs: the timing ladder, a full sweep at the dial's minimum
FOREVER_STEPS = {("BOARD", 3): 600, ("BOARD", 4): 600, ("BOARD", 5): 600,
                 ("BOARD", 6): 400, ("BOARD", 7): 2000}    # scroll: four steps


def forever_steps(name, sw):
    return FOREVER_STEPS.get((name, sw), RUNS_FOREVER[name])


PRINT_BYTES = {("BOARD", 1): 100,      # the banner and the first three numbered lines
               ("BOARD", 2): 57,
               ("BOARD", 7): 23}       # the C demo's greeting; its cost table is checked as C_COST       # the banner and two interrupts (the first with a lost press)
# Programs with no Alchitry testbench: only xsim_computer.py checks them (against rv32sim). C_COST
# prints 308 bytes after 66k clocks of arithmetic: 13 minutes in the Alchitry simulator, and that
# simulator's cross-instance ordering bug (CLAUDE.md) sends a byte twice in most runs of a print
# this long (byte 20 of 308 doubled in one of three shortened runs; xsim passes every time).
XSIM_ONLY = {"C_COST"}
# Programs that read the switch register: one testbench per setting.
SWITCHES = {"BOARD": [0, 1, 2, 3, 4, 5, 6, 7]}
# Programs that trap on purpose (rv32sim raises on a trap anywhere else).
EXPECTS_TRAPS = {"ILLEGAL", "TRAPS", "TIMER", "IRQ", "PRESS", "BOARD", "WFI", "VECTORED"}
# Button presses for programs that use the buttons device: (post-reset clock, levels), levels =
# up | down << 1 | left << 2 | right << 3, applied before that clock's rising edge.
BUTTON_EVENTS = {
    "IRQ": [(30, 0x1), (50, 0x0), (150, 0xC), (170, 0x0), (300, 0x2)],
    "PRESS": [(20, 0x1), (25, 0x0), (28, 0x1), (33, 0x0),      # (A) set wins on the clear edge
              (108, 0x1), (113, 0x0), (115, 0x1), (120, 0x0)],  # (B) merged before the clear
    # only menu entry 2 listens. Two presses of left while the banner is still printing (interrupts
    # not yet on, in the model and on the real UART alike): merged, with the overrun bit set; the
    # handler prints "buttons 4 lost 4". Then up + right together, which in the printing test
    # lands inside that first handler's output and in the model after it: "buttons 9" either way.
    "BOARD": [(100, 0x4), (110, 0x0), (140, 0x4), (150, 0x0), (3000, 0x9), (3100, 0x0)],
    "VECTORED": [(400, 0x1), (420, 0x0)],
    "WFI": [(240, 0x1), (260, 0x0)],          # wakes (c), whose WFI starts at clock 192
}
# The platform interrupt lines (mip/mie bits 16, 17): (post-reset clock, levels), line 0 | line 1 << 1,
# straight into the cpu. A test input until the UART drives them.
PLAT_EVENTS = {
    "VECTORED": [(500, 0x1), (560, 0x0), (600, 0x3), (700, 0x0)],
}

# The program the board runs (instantiated by the top module).
BOARD_PROGRAM = "BOARD"

"""Programs for the core: the board's image (a boot menu: Larson, a printing demo) and one test
program per instruction group.

Each test program ends in `halt` (jal x0, 0), except those in RUNS_FOREVER. gen_cpu_tb.py runs
every program on rv32sim and checks the hardware against it after each instruction (programs
that print: by their serial output and final state instead, since polling makes their timing
differ). Registers are x0-x15 only (RV32E). Code and data share the RAM (see memmap.py).
"""
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

# Illegal encodings act as NOPs: nothing written, no store, no jump or branch. ECALL, EBREAK and
# FENCE are legal no-ops. Then it jumps to the UART: devices answer loads only, so fetches there
# read 0 (illegal, a NOP) and the core walks on through I/O space without sending anything.
ILLEGAL = [
    ("addi", 4, 4, 1),                              # counts runs of word 0: a fetch from I/O
                                                    # that aliased onto RAM would run it again
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
    (".word", rv32.i_type(0x73, 5, 1, 6, 0x300)),   # csrrw x5, mstatus, x6: no CSRs
    (".word", 0x0000100F),                          # fence.i: Zifencei not implemented
    ("ecall",),
    ("ebreak",),
    ("fence",),
    ("lw", 7, 0, DATA),                             # still 6: the sd didn't store
    ("lui", 2, memmap.UART_BASE >> 12),
    ("jalr", 1, 2, 0),                              # run from the UART
]

# What the board boots. A stub at 0 reads the switch register (the middle DIP bank) once after
# reset and jumps through a table: bits 1:0 pick the program, so change the DIPs and press reset.
#   0  Larson scanner on x1 (at its original addresses: its turnaround jal links 0x80 = LED 7)
#   1  hello: a banner on the serial port, then a Larson sweep on x1 and a numbered line per sweep
#   2, 3  Larson (spare)
# The serial port is the Cu's USB (FTDI) at 1 Mbaud, 8N1. Registers: x1 LEDs, x2 device base,
# x3 string pointer / menu index, x4 menu target / digit count, x5 putc's link, x6 puts/hex's
# link, x7 STATUS, x8 sweep count, x9 hex shift register, x10 the character.
BOARD = [
    ("lui", 2, memmap.SWITCHES_BASE >> 12),
    ("lw", 3, 2, memmap.SWITCHES_BASE & 0xFFF),
    ("andi", 3, 3, 3),
    ("slli", 3, 3, 2),
    ("lw", 4, 3, "menu"),                      # menu + 4 * choice
    ("jalr", 0, 4, 0),
    "menu:",
    (".word", "turn", "hello", "turn", "turn"),
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
    "crlf:",
    (".string", "\r\n"),
    "digits:",
    (".string", "0123456789ABCDEF"),
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
}

# Programs that never halt, and how many instructions to check (lockstep). One that prints is
# checked by its first PRINT_BYTES bytes instead.
RUNS_FOREVER = {"BOARD": 40, "LARSON": 34, "ILLEGAL": 30}
PRINT_BYTES = 100              # the banner and the first three numbered lines
# Programs that read the switch register: one testbench per setting.
SWITCHES = {"BOARD": [0, 1]}
# Programs that run illegal instructions on purpose (rv32sim otherwise raises on them).
ILLEGAL_AS_NOP = {"ILLEGAL"}

# The program the board runs (instantiated by the top module).
BOARD_PROGRAM = "BOARD"

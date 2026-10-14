"""Programs for the core: the board demo (Larson) and one test program per instruction group.

Each test program ends in `halt` (jal x0, 0). gen_cpu_tb.py runs every program on rv32sim and
checks the hardware against it after each instruction. Registers are x0-x15 only (RV32E).
"""

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

# SW then LW, positive and negative offsets, overwrites, x0 as data. RAM is 64 words
# (addresses 0..255) and starts unknown, so every load reads an address stored first.
MEMORY = [
    ("addi", 2, 0, 0x40),           # base address
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
    ("sw", 3, 8, -4),               # negative offset: address 0x44
    ("lw", 9, 2, 4),
    ("sw", 6, 0, 252),              # top word of the RAM
    ("lw", 10, 0, 252),
    ("halt",),
]

PROGRAMS = {
    "LARSON": LARSON,
    "ALU": ALU,
    "BRANCHES": BRANCHES,
    "JUMPS": JUMPS,
    "MEMORY": MEMORY,
}

# The program the board runs (instantiated by the top module).
BOARD_PROGRAM = "LARSON"

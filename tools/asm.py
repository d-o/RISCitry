"""A tiny two-pass assembler over rv32.encode, so branch/jump offsets always come from labels.

A program is a list of items:
    "label:"                         defines a label at the current address
    (".org", 0x48)                   moves the current address (gaps are filled with nops)
    ("addi", rd, rs1, imm)           any rv32.INSTRUCTIONS mnemonic, operands in assembly order
    ("beq", rs1, rs2, "label")       branch/jump targets may be labels (pc-relative offset computed)
    ("halt",)                        pseudo-op: jal x0, 0 (spin forever; test runs stop here)

Operand order follows assembly syntax:
    R:   op rd, rs1, rs2         I/SHIFT: op rd, rs1, imm      loads: op rd, rs1, imm (= imm(rs1))
    S:   op rs2, rs1, imm  (sw rs2, imm(rs1))                   B: op rs1, rs2, target
    U:   op rd, imm20             J: op rd, target              fence/ecall/ebreak: no operands
"""
import rv32

NOP = 0x00000013
HALT = rv32.jal(0, 0)


def assemble(program):
    """Return {byte address: word}."""
    return assemble_with_listing(program)[0]


def _text(item):
    """Assembly text for a listing, e.g. 'sw x3, -4(x8)' or 'beq x2, x3, b7'."""
    name, *ops = item
    if name == "halt" or not ops:
        return name
    fmt = rv32.INSTRUCTIONS[name][0]
    x = lambda r: f"x{r}"
    num = lambda v: v if isinstance(v, str) else (f"{v:#x}" if v > 255 else f"-{-v:#x}" if v < -255 else str(v))
    if fmt == "R":
        return f"{name} {x(ops[0])}, {x(ops[1])}, {x(ops[2])}"
    if name in ("lb", "lh", "lw", "lbu", "lhu") or fmt == "S":
        return f"{name} {x(ops[0])}, {num(ops[2])}({x(ops[1])})"
    if fmt in ("I", "SHIFT"):
        return f"{name} {x(ops[0])}, {x(ops[1])}, {num(ops[2])}"
    if fmt == "B":
        return f"{name} {x(ops[0])}, {x(ops[1])}, {num(ops[2])}"
    return f"{name} {x(ops[0])}, {num(ops[1])}"   # U, J


def assemble_with_listing(program):
    """Return ({byte address: word}, {byte address: assembly text})."""
    labels, addr = {}, 0
    for item in program:                       # pass 1: label addresses
        if isinstance(item, str):
            name = item.rstrip(":")
            if name in labels:
                raise ValueError(f"duplicate label {name}")
            labels[name] = addr
        elif item[0] == ".org":
            if item[1] < addr or item[1] % 4:
                raise ValueError(f".org {item[1]:#x} goes backwards or is unaligned")
            addr = item[1]
        else:
            addr += 4

    def target(value, pc):
        return labels[value] - pc if isinstance(value, str) else value

    words, text, addr = {}, {}, 0
    for item in program:                       # pass 2: encode
        if isinstance(item, str):
            continue
        name, *ops = item
        if name == ".org":
            addr = ops[0]
            continue
        if name == "halt":
            word = HALT
        else:
            fmt = rv32.INSTRUCTIONS[name][0]
            if fmt in ("R",):
                rd, rs1, rs2 = ops
                word = rv32.encode(name, rd, rs1, rs2)
            elif fmt in ("I", "SHIFT"):
                if name in ("fence", "ecall", "ebreak"):
                    word = rv32.encode(name)
                else:
                    rd, rs1, imm = ops
                    word = rv32.encode(name, rd, rs1, imm=imm)
            elif fmt == "S":
                rs2, rs1, imm = ops
                word = rv32.encode(name, rs1=rs1, rs2=rs2, imm=imm)
            elif fmt == "B":
                rs1, rs2, dest = ops
                word = rv32.encode(name, rs1=rs1, rs2=rs2, imm=target(dest, addr))
            elif fmt == "U":
                rd, imm = ops
                word = rv32.encode(name, rd, imm=imm)
            else:  # J
                rd, dest = ops
                word = rv32.encode(name, rd, imm=target(dest, addr))
        words[addr] = word
        text[addr] = _text(item)
        addr += 4
    return words, text


def to_list(words):
    """Contiguous word list from address 0, gaps filled with nops."""
    size = max(words) // 4 + 1
    return [words.get(i * 4, NOP) for i in range(size)]

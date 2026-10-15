"""A tiny two-pass assembler over rv32.encode, so branch/jump offsets always come from labels.

A program is a list of items:
    "label:"                         defines a label at the current address
    (".org", 0x48)                   moves the current address (gaps are filled with nops)
    ("addi", rd, rs1, imm)           any rv32.INSTRUCTIONS mnemonic, operands in assembly order
    ("beq", rs1, rs2, "label")       branch/jump targets may be labels (pc-relative offset computed)
    ("halt",)                        pseudo-op: jal x0, 0 (spin forever; test runs stop here)
    (".word", v, ...)                32-bit words; a label gives its address
    (".byte", b, ...)                bytes
    (".string", "text")              the text's bytes then a 0 (like GNU .string/.asciz)
    (".align", 4)                    pad with 0 bytes to a multiple of 4 (instructions and .word
                                     must be word aligned; .byte/.string can leave it unaligned)
Code and data share one address space, so a load/store immediate may also be a label: its
absolute address, which must fit the 12-bit signed immediate (addresses below 0x800, from x0).
Words never touched are nops; bytes of a partly written word that weren't given are 0.

Operand order follows assembly syntax:
    R:   op rd, rs1, rs2         I/SHIFT: op rd, rs1, imm      loads: op rd, rs1, imm (= imm(rs1))
    S:   op rs2, rs1, imm  (sw rs2, imm(rs1))                   B: op rs1, rs2, target
    U:   op rd, imm20             J: op rd, target              fence/ecall/ebreak: no operands
    CSR: op rd, csr, rs1          CSRI: op rd, csr, zimm         mret/wfi: no operands
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


def _size(item, addr):
    """Bytes an item occupies at addr (directives and instructions)."""
    name, *ops = item
    if name == ".word":
        return 4 * len(ops)
    if name == ".byte":
        return len(ops)
    if name == ".string":
        return len(ops[0].encode()) + 1
    if name == ".align":
        return -addr % ops[0]
    return 4


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
            addr += _size(item, addr)

    def target(value, pc):
        return labels[value] - pc if isinstance(value, str) else value

    def absolute(value):
        return labels[value] if isinstance(value, str) else value

    data = {}                                  # byte address -> byte, from data directives
    words, text, addr = {}, {}, 0
    for item in program:                       # pass 2: encode
        if isinstance(item, str):
            continue
        name, *ops = item
        if name == ".org":
            addr = ops[0]
            continue
        if name in (".word", ".byte", ".string", ".align"):
            if name == ".word":
                if addr % 4:
                    raise ValueError(f".word at {addr:#x} is unaligned")
                raw = b"".join((absolute(v) & 0xFFFFFFFF).to_bytes(4, "little") for v in ops)
                shown = ", ".join(v if isinstance(v, str) else f"{v:#010x}" for v in ops)
            elif name == ".byte":
                raw = bytes(v & 0xFF for v in ops)
                shown = ", ".join(f"{v & 0xFF:#04x}" for v in ops)
            elif name == ".string":
                raw = ops[0].encode() + b"\0"
                shown = repr(ops[0])
            else:
                raw = bytes(-addr % ops[0])
                shown = str(ops[0])
            if raw:
                text.setdefault(addr & ~3, f"{name} {shown}")
            for i, b in enumerate(raw):
                data[addr + i] = b
            addr += len(raw)
            continue
        if addr % 4:
            raise ValueError(f"{name} at {addr:#x} is unaligned (use .align 4 after .byte/.string)")
        if name == "halt":
            word = HALT
        else:
            fmt = rv32.INSTRUCTIONS[name][0]
            if fmt in ("R",):
                rd, rs1, rs2 = ops
                word = rv32.encode(name, rd, rs1, rs2)
            elif fmt in ("CSR", "CSRI"):
                rd, csr, src = ops
                word = rv32.encode(name, rd, src, imm=csr)
            elif fmt == "SYS":
                word = rv32.encode(name)
            elif fmt in ("I", "SHIFT"):
                if name in ("fence", "ecall", "ebreak"):
                    word = rv32.encode(name)
                else:
                    rd, rs1, imm = ops
                    word = rv32.encode(name, rd, rs1, imm=absolute(imm))
            elif fmt == "S":
                rs2, rs1, imm = ops
                word = rv32.encode(name, rs1=rs1, rs2=rs2, imm=absolute(imm))
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
    for word_addr in sorted({a & ~3 for a in data}):
        if word_addr in words:
            raise ValueError(f"data overlaps the instruction at {word_addr:#x}")
        words[word_addr] = int.from_bytes(bytes(data.get(word_addr + i, 0) for i in range(4)), "little")
        text.setdefault(word_addr, "(data)")
    return words, text


def to_list(words):
    """Contiguous word list from address 0, gaps filled with nops."""
    size = max(words) // 4 + 1
    return [words.get(i * 4, NOP) for i in range(size)]

"""RV32I instruction encoding/decoding: the single source of truth for hex in this project.

Every ROM word and every generated test vector comes from here. Never hand-encode B/J immediates.
Run this file directly to self-test against known-good encodings.
"""

OPCODES = {
    "LUI": 0x37, "AUIPC": 0x17, "JAL": 0x6F, "JALR": 0x67, "BRANCH": 0x63,
    "LOAD": 0x03, "STORE": 0x23, "OP_IMM": 0x13, "OP": 0x33,
}


def _check_reg(r):
    if not 0 <= r < 32:
        raise ValueError(f"register x{r} out of range")
    return r


def _check_imm(imm, bits, align=1):
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not lo <= imm <= hi:
        raise ValueError(f"immediate {imm} outside {bits}-bit signed range [{lo}, {hi}]")
    if imm % align:
        raise ValueError(f"immediate {imm} not a multiple of {align}")
    return imm & ((1 << bits) - 1)


# --- formats -----------------------------------------------------------------

def r_type(opcode, rd, funct3, rs1, rs2, funct7):
    return ((funct7 << 25) | (_check_reg(rs2) << 20) | (_check_reg(rs1) << 15)
            | (funct3 << 12) | (_check_reg(rd) << 7) | opcode)


def i_type(opcode, rd, funct3, rs1, imm):
    imm = _check_imm(imm, 12)
    return (imm << 20) | (_check_reg(rs1) << 15) | (funct3 << 12) | (_check_reg(rd) << 7) | opcode


def s_type(opcode, funct3, rs1, rs2, imm):
    imm = _check_imm(imm, 12)
    return (((imm >> 5) << 25) | (_check_reg(rs2) << 20) | (_check_reg(rs1) << 15)
            | (funct3 << 12) | ((imm & 0x1F) << 7) | opcode)


def b_type(opcode, funct3, rs1, rs2, imm):
    imm = _check_imm(imm, 13, align=2)
    return ((((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | (_check_reg(rs2) << 20)
            | (_check_reg(rs1) << 15) | (funct3 << 12) | (((imm >> 1) & 0xF) << 8)
            | (((imm >> 11) & 1) << 7) | opcode)


def u_type(opcode, rd, imm20):
    if not 0 <= imm20 < (1 << 20):
        raise ValueError(f"U-type immediate {imm20:#x} must be 20 bits")
    return (imm20 << 12) | (_check_reg(rd) << 7) | opcode


def j_type(opcode, rd, imm):
    imm = _check_imm(imm, 21, align=2)
    return ((((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | (((imm >> 11) & 1) << 20)
            | (((imm >> 12) & 0xFF) << 12) | (_check_reg(rd) << 7) | opcode)


# --- immediate decoding (independent of the encoders, for round-trip checks) ---

def _sext(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def decode_imm(word):
    """Immediate as the spec defines it for this word's format, sign-extended (Python int)."""
    op = word & 0x7F
    if op in (0x13, 0x03, 0x67):
        return _sext(word >> 20, 12)
    if op == 0x23:
        return _sext(((word >> 25) << 5) | ((word >> 7) & 0x1F), 12)
    if op == 0x63:
        return _sext((((word >> 31) & 1) << 12) | (((word >> 7) & 1) << 11)
                     | (((word >> 25) & 0x3F) << 5) | (((word >> 8) & 0xF) << 1), 13)
    if op == 0x6F:
        return _sext((((word >> 31) & 1) << 20) | (((word >> 12) & 0xFF) << 12)
                     | (((word >> 20) & 1) << 11) | (((word >> 21) & 0x3FF) << 1), 21)
    if op in (0x37, 0x17):
        return _sext(word & 0xFFFFF000, 32)
    raise ValueError(f"no immediate for opcode {op:#04x}")


# --- every RV32I instruction: mnemonic -> (format, opcode, funct3, funct7) -----

INSTRUCTIONS = {
    "lui":   ("U", 0x37, None, None),
    "auipc": ("U", 0x17, None, None),
    "jal":   ("J", 0x6F, None, None),
    "jalr":  ("I", 0x67, 0b000, None),
    "beq":   ("B", 0x63, 0b000, None), "bne":  ("B", 0x63, 0b001, None),
    "blt":   ("B", 0x63, 0b100, None), "bge":  ("B", 0x63, 0b101, None),
    "bltu":  ("B", 0x63, 0b110, None), "bgeu": ("B", 0x63, 0b111, None),
    "lb":    ("I", 0x03, 0b000, None), "lh":   ("I", 0x03, 0b001, None),
    "lw":    ("I", 0x03, 0b010, None), "lbu":  ("I", 0x03, 0b100, None),
    "lhu":   ("I", 0x03, 0b101, None),
    "sb":    ("S", 0x23, 0b000, None), "sh":   ("S", 0x23, 0b001, None),
    "sw":    ("S", 0x23, 0b010, None),
    "addi":  ("I", 0x13, 0b000, None), "slti": ("I", 0x13, 0b010, None),
    "sltiu": ("I", 0x13, 0b011, None), "xori": ("I", 0x13, 0b100, None),
    "ori":   ("I", 0x13, 0b110, None), "andi": ("I", 0x13, 0b111, None),
    "slli":  ("SHIFT", 0x13, 0b001, 0x00), "srli": ("SHIFT", 0x13, 0b101, 0x00),
    "srai":  ("SHIFT", 0x13, 0b101, 0x20),
    "add":   ("R", 0x33, 0b000, 0x00), "sub":  ("R", 0x33, 0b000, 0x20),
    "sll":   ("R", 0x33, 0b001, 0x00), "slt":  ("R", 0x33, 0b010, 0x00),
    "sltu":  ("R", 0x33, 0b011, 0x00), "xor":  ("R", 0x33, 0b100, 0x00),
    "srl":   ("R", 0x33, 0b101, 0x00), "sra":  ("R", 0x33, 0b101, 0x20),
    "or":    ("R", 0x33, 0b110, 0x00), "and":  ("R", 0x33, 0b111, 0x00),
    "fence": ("I", 0x0F, 0b000, None),
    "ecall": ("I", 0x73, 0b000, None), "ebreak": ("I", 0x73, 0b000, None),
    # Zicsr: csrrw rd, csr, rs1 (CSR) and csrrwi rd, csr, zimm (CSRI); the last field is the CSR
    # address for both, the zimm sits in the rs1 field.
    "csrrw": ("CSR", 0x73, 0b001, None), "csrrs": ("CSR", 0x73, 0b010, None),
    "csrrc": ("CSR", 0x73, 0b011, None),
    "csrrwi": ("CSRI", 0x73, 0b101, None), "csrrsi": ("CSRI", 0x73, 0b110, None),
    "csrrci": ("CSRI", 0x73, 0b111, None),
    # Privileged, fixed words (funct12 in the last field): no operands.
    "mret": ("SYS", 0x73, 0b000, 0x302), "wfi": ("SYS", 0x73, 0b000, 0x105),
}


def encode(mnemonic, rd=0, rs1=0, rs2=0, imm=0):
    """Encode any RV32I instruction. Unused operands are ignored for that format."""
    fmt, op, f3, f7 = INSTRUCTIONS[mnemonic]
    if mnemonic == "ebreak":
        imm = 1
    if fmt == "R":
        return r_type(op, rd, f3, rs1, rs2, f7)
    if fmt in ("CSR", "CSRI"):           # imm = CSR address; rs1 = register or zimm
        if not 0 <= imm < 4096 or not 0 <= rs1 < 32:
            raise ValueError(f"{mnemonic}: CSR address {imm} or source {rs1} out of range")
        rs1 = _check_reg(rs1) if fmt == "CSR" else rs1
        return (imm << 20) | (rs1 << 15) | (f3 << 12) | (_check_reg(rd) << 7) | op
    if fmt == "SYS":
        return (f7 << 20) | op
    if fmt == "SHIFT":
        if not 0 <= imm < 32:
            raise ValueError(f"shift amount {imm} must be 0..31")
        return i_type(op, rd, f3, rs1, (f7 << 5) | imm)
    if fmt == "I":
        return i_type(op, rd, f3, rs1, imm)
    if fmt == "S":
        return s_type(op, f3, rs1, rs2, imm)
    if fmt == "B":
        return b_type(op, f3, rs1, rs2, imm)
    if fmt == "U":
        return u_type(op, rd, imm)
    return j_type(op, rd, imm)


# --- named shorthands ------------------------------------------------------------

def addi(rd, rs1, imm): return i_type(0x13, rd, 0b000, rs1, imm)
def slli(rd, rs1, sh):  return i_type(0x13, rd, 0b001, rs1, sh)
def srli(rd, rs1, sh):  return i_type(0x13, rd, 0b101, rs1, sh)
def add(rd, rs1, rs2):  return r_type(0x33, rd, 0b000, rs1, rs2, 0x00)
def sub(rd, rs1, rs2):  return r_type(0x33, rd, 0b000, rs1, rs2, 0x20)
def lw(rd, rs1, imm):   return i_type(0x03, rd, 0b010, rs1, imm)
def sw(rs2, rs1, imm):  return s_type(0x23, 0b010, rs1, rs2, imm)
def beq(rs1, rs2, imm): return b_type(0x63, 0b000, rs1, rs2, imm)
def bne(rs1, rs2, imm): return b_type(0x63, 0b001, rs1, rs2, imm)
def jal(rd, imm):       return j_type(0x6F, rd, imm)


def u32(x):
    return x & 0xFFFFFFFF


def _self_test():
    # Encodings that appear in standard RISC-V disassembly, not derived from this file.
    known = {
        0x00100093: addi(1, 0, 1),       # addi ra, zero, 1  (li ra, 1)
        0x00000013: addi(0, 0, 0),       # nop
        0x0000006F: jal(0, 0),           # j .
        0xFE000EE3: beq(0, 0, -4),       # beqz zero, .-4
        0x40B50533: sub(10, 10, 11),     # sub a0, a0, a1
        0x00B50533: add(10, 10, 11),     # add a0, a0, a1
        # This project's Larson ROM.
        0x07C0006F: jal(0, 0x7C),
        0xFCDFF0EF: jal(1, -0x34),
        0x0010D093: srli(1, 1, 1),
        0x00109093: slli(1, 1, 1),
    }
    known.update({
        0x00008067: encode("jalr", 0, 1, imm=0),       # ret
        0x00000073: encode("ecall"),
        0x00100073: encode("ebreak"),
        0x40A5D513: encode("srai", 10, 11, imm=10),    # srai a0, a1, 10
        0x123452B7: encode("lui", 5, imm=0x12345),     # lui t0, 0x12345
        0x0000A503: encode("lw", 10, 1, imm=0),        # lw a0, 0(ra)
        0x00A12223: encode("sw", 0, 2, 10, imm=4),     # sw a0, 4(sp)
        0x30529073: encode("csrrw", 0, 5, imm=0x305),  # csrw mtvec, t0
        0x34102573: encode("csrrs", 10, 0, imm=0x341), # csrr a0, mepc
        0x30046073: encode("csrrsi", 0, 8, imm=0x300), # csrsi mstatus, 8
        0x30200073: encode("mret"),
        0x10500073: encode("wfi"),
    })
    for want, got in known.items():
        assert want == got, f"expected {want:08X}, encoder gave {got:08X}"
    for name in INSTRUCTIONS:
        assert INSTRUCTIONS[name][1] == encode(name, 1, 2, 3, 4) & 0x7F
    for imm in (0, 2, -2, 4094, -4096, 0x800, -0x800, 0xAAA, -0x556):
        assert decode_imm(beq(31, 31, imm)) == imm
    for imm in (0, 2, -2, 0xFFFFE, -0x100000, 0x800, 0x55554, -0x2AAAC):
        assert decode_imm(jal(31, imm)) == imm
    for imm in (0, 1, -1, 2047, -2048, 0x555, -0x556):
        assert decode_imm(addi(31, 31, imm)) == imm
        assert decode_imm(sw(31, 31, imm)) == imm
    print("rv32 self-test passed")


if __name__ == "__main__":
    _self_test()

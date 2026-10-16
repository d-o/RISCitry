"""Generate source/tb/decoder_tb.luc: expected control signals for every RV32I instruction,
written here as a table from the spec (deliberately not derived from decoder.luc), plus illegal encodings.

The decoder outputs are packed into one 24-bit word in the testbench:
  [23] wfi  [22] ebreak  [21] ecall  [20] mret  [19] csr_write  [18] csr  [17:14] alu_op  [13] alu_src_a  [12] alu_src_b  [11:9] imm_type  [8:7] wb_sel
  [6] reg_write  [5] mem_read  [4] mem_write  [3] branch  [2] jump  [1] jalr  [0] illegal

Usage: python3 tools/gen_decoder_tb.py   (rewrites source/tb/decoder_tb.luc)
"""
from pathlib import Path

import rv32

# Mirrors source/cpu/cpu_defs.luc (enum order; struct fields flatten least-significant first).
IMM = {"I": 0, "S": 1, "B": 2, "U": 3, "J": 4}
WB = {"ALU": 0, "MEM": 1, "PC4": 2, "IMM": 3}
ALU = {"add": 0b0000, "sub": 0b1000, "sll": 0b0001, "slt": 0b0010, "sltu": 0b0011,
       "xor": 0b0100, "srl": 0b0101, "sra": 0b1101, "or": 0b0110, "and": 0b0111}

# mnemonic: (alu, src_a_is_pc, src_b_is_imm, imm, wb, flags); flags from rw mr mw br j jr csr cw mret
SPEC = {
    "lui":   ("add", 0, 0, "U", "IMM", "rw"),
    "auipc": ("add", 1, 1, "U", "ALU", "rw"),
    "jal":   ("add", 0, 0, "J", "PC4", "rw j"),
    "jalr":  ("add", 0, 1, "I", "PC4", "rw jr"),
    **{b: ("sub", 0, 0, "B", "ALU", "br") for b in ("beq", "bne", "blt", "bge", "bltu", "bgeu")},
    **{l: ("add", 0, 1, "I", "MEM", "rw mr") for l in ("lb", "lh", "lw", "lbu", "lhu")},
    **{s: ("add", 0, 1, "S", "ALU", "mw") for s in ("sb", "sh", "sw")},
    "addi": ("add", 0, 1, "I", "ALU", "rw"), "slti": ("slt", 0, 1, "I", "ALU", "rw"),
    "sltiu": ("sltu", 0, 1, "I", "ALU", "rw"), "xori": ("xor", 0, 1, "I", "ALU", "rw"),
    "ori": ("or", 0, 1, "I", "ALU", "rw"), "andi": ("and", 0, 1, "I", "ALU", "rw"),
    "slli": ("sll", 0, 1, "I", "ALU", "rw"), "srli": ("srl", 0, 1, "I", "ALU", "rw"),
    "srai": ("sra", 0, 1, "I", "ALU", "rw"),
    **{r: (r, 0, 0, "I", "ALU", "rw")
       for r in ("add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and")},
    "fence": ("add", 0, 0, "I", "ALU", ""),
    "ecall": ("add", 0, 0, "I", "ALU", "ecall"),
    "ebreak": ("add", 0, 0, "I", "ALU", "ebreak"),
    "wfi": ("add", 0, 0, "I", "ALU", "wfi"),
    "mret": ("add", 0, 0, "I", "ALU", "mret"),
    # Zicsr: rd gets the old value; the write is suppressed for set/clear with x0 / zimm 0
    # (vectors below use nonzero sources, plus explicit read-only cases)
    **{c: ("add", 0, 0, "I", "ALU", "rw csr cw") for c in ("csrrw", "csrrs", "csrrc",
                                                         "csrrwi", "csrrsi", "csrrci")},
}
FLAG_BITS = {"rw": 6, "mr": 5, "mw": 4, "br": 3, "j": 2, "jr": 1, "csr": 18, "cw": 19, "mret": 20,
             "ecall": 21, "ebreak": 22, "wfi": 23}
WIDTH = 24
FULL_MASK = (1 << WIDTH) - 1
SIDE_EFFECTS_AND_ILLEGAL = 0b11111 << 18 | 0b1111111  # bits 22..18 and 6..0


def packed(alu, a, b, imm, wb, flags, illegal=0):
    word = (ALU[alu] << 14) | (a << 13) | (b << 12) | (IMM[imm] << 9) | (WB[wb] << 7) | illegal
    for f in flags.split():
        word |= 1 << FLAG_BITS[f]
    return word


def legal_vectors():
    """Each instruction with ordinary operands, with the top legal register (x15),
    and with immediates that set bit 30 (which must not turn addi into sub)."""
    out = []
    for name, spec in SPEC.items():
        fmt = rv32.INSTRUCTIONS[name][0]
        imms = {"R": [0, 0], "SHIFT": [4, 31], "I": [5, -1], "S": [8, -2048], "B": [-4, 0x7C],
                "U": [0x12345, 0xFFFFF], "J": [0x7C, -0x34], "SYS": [0, 0], "CSR": [0, 0], "CSRI": [0, 0]}[fmt]
        if name in ("fence", "ecall", "ebreak", "wfi", "mret"):
            operand_sets = [(0, 0, 0, 0)]
        elif fmt in ("CSR", "CSRI"):   # (rd, source, -, CSR address): mscratch and mtvec
            operand_sets = [(1, 2, 0, 0x340), (15, 15, 0, 0x305)]
        else:
            operand_sets = [(1, 2, 3, imms[0]), (15, 15, 15, imms[1])]
        for rd, rs1, rs2, imm in operand_sets:
            word = rv32.encode(name, rd, rs1, rs2, imm)
            out.append((word, packed(*spec), FULL_MASK, f"{name} rd=x{rd} rs1=x{rs1} rs2=x{rs2} imm={imm}"))
    # Fields that aren't registers for this format may have bit 4 set: still legal.
    out.append((rv32.encode("addi", 1, 2, imm=16), packed(*SPEC["addi"]), FULL_MASK,
                "addi imm=16 sets bit 24 (rs2 position) but rs2 is unused: legal"))
    out.append((rv32.encode("lui", 1, imm=0x1FFFF), packed(*SPEC["lui"]), FULL_MASK,
                "lui with bits 19 and 24 set (rs1/rs2 positions, unused): legal"))
    out.append((rv32.encode("jal", 1, imm=0x7FFFE), packed(*SPEC["jal"]), FULL_MASK,
                "jal with bits 19 and 24 set: legal"))
    read_only = ("add", 0, 0, "I", "ALU", "rw csr")      # a CSR read: no csr_write
    out.append((rv32.encode("csrrs", 1, 0, imm=0xF14), packed(*read_only), FULL_MASK,
                "csrr x1, mhartid (csrrs with x0: read-only CSR, no write): legal"))
    out.append((rv32.encode("csrrci", 1, 0, imm=0x301), packed(*read_only), FULL_MASK,
                "csrrci x1, misa, 0: no write"))
    out.append((rv32.encode("csrrsi", 2, 31, imm=0x341), packed(*SPEC["csrrsi"]), FULL_MASK,
                "csrrsi zimm=31: the rs1 field isn't a register, so not an RV32E violation"))
    return out


def illegal_vectors():
    ill = packed("add", 0, 0, "I", "ALU", "", illegal=1)
    words = [
        (0x00000000, "all-zero word"),
        (0xFFFFFFFF, "all-ones word"),
        (rv32.b_type(0x63, 0b010, 1, 2, 8), "branch funct3=010"),
        (rv32.b_type(0x63, 0b011, 1, 2, 8), "branch funct3=011"),
        (rv32.i_type(0x03, 1, 0b011, 2, 0), "load funct3=011 (ld is RV64)"),
        (rv32.i_type(0x03, 1, 0b110, 2, 0), "load funct3=110 (lwu is RV64)"),
        (rv32.i_type(0x03, 1, 0b111, 2, 0), "load funct3=111"),
        (rv32.s_type(0x23, 0b011, 1, 2, 0), "store funct3=011 (sd is RV64)"),
        (rv32.i_type(0x67, 1, 0b001, 2, 0), "jalr funct3=001"),
        (rv32.i_type(0x13, 1, 0b001, 2, (0x20 << 5) | 3), "slli with funct7=0x20"),
        (rv32.i_type(0x13, 1, 0b101, 2, (0x01 << 5) | 3), "srli with funct7=0x01"),
        (rv32.r_type(0x33, 1, 0b000, 2, 3, 0x01), "mul (M extension, the old SUB-decode bug)"),
        (rv32.r_type(0x33, 1, 0b001, 2, 3, 0x20), "sll with funct7=0x20"),
        (rv32.r_type(0x33, 1, 0b100, 2, 3, 0x20), "xor with funct7=0x20"),
        (rv32.encode("csrrw", 1, 2, imm=0xC00), "csrrw cycle (not implemented)"),
        (rv32.encode("csrrs", 1, 0, imm=0x7C0), "csrr of a custom CSR (not implemented)"),
        (rv32.encode("csrrw", 0, 2, imm=0xF11), "csrw mvendorid (read-only)"),
        (rv32.encode("csrrsi", 0, 1, imm=0xF14), "csrsi mhartid, 1 (read-only, writes)"),
        (rv32.i_type(0x73, 1, 0b100, 2, 0x300), "SYSTEM funct3=100"),
        (rv32.encode("csrrw", 16, 2, imm=0x340), "RV32E: csrrw rd=x16"),
        (rv32.encode("csrrs", 1, 17, imm=0x340), "RV32E: csrrs rs1=x17"),
        (rv32.i_type(0x73, 0, 0b000, 0, 0x102), "sret (no S mode)"),
        (0x30200073 | (1 << 7), "mret with rd=1"),
        (rv32.i_type(0x73, 0, 0b000, 0, 2), "SYSTEM funct3=0 imm=2 (uret, not RV32I)"),
        (rv32.i_type(0x0F, 0, 0b001, 0, 0), "fence.i (Zifencei not supported)"),
        (rv32.encode("add", 16, 1, 2), "RV32E: rd=x16"),
        (rv32.encode("add", 1, 16, 2), "RV32E: rs1=x16"),
        (rv32.encode("add", 1, 2, 31), "RV32E: rs2=x31"),
        (rv32.encode("lui", 20, imm=1), "RV32E: lui rd=x20"),
        (rv32.encode("beq", 0, 17, imm=8), "RV32E: beq rs2=x17"),
        (rv32.encode("sw", 0, 18, 1, imm=0), "RV32E: sw rs1=x18"),
        (rv32.encode("jalr", 1, 31, imm=0), "RV32E: jalr rs1=x31"),
    ]
    return [(w, ill, SIDE_EFFECTS_AND_ILLEGAL, note) for w, note in words]


def render(groups):
    out = [
        "// GENERATED by tools/gen_decoder_tb.py from tools/rv32.py. Do not edit; rerun the script.",
        "// Flattens the decoder's ctrl struct (layout in cpu_defs.luc) and compares it with",
        "// the spec table. Illegal encodings only check the illegal flag and that side effects are off.",
        "testbench decoder_tb {",
        "    sig in_inst[32]",
        f"    sig got[{WIDTH}]",
        "",
        "    decoder dut(.instruction(in_inst))",
        "",
        f"    fun check(inst[32], expected[{WIDTH}], mask[{WIDTH}]) {{",
        "        in_inst = inst",
        "        $tick()",
        "        got = $flatten(dut.ctrl)",
        "        if ((got & mask) != expected)",
        '            $print("inst=%h got=%b want=%b mask=%b", inst, got, expected, mask)',
        "        $assert((got & mask) == expected)",
        "    }",
    ]
    for name, vecs in groups.items():
        out += ["", f"    test {name} {{", "        in_inst = 0", "        got = 0"]
        out += [f"        $check(32h{w:08X}, {WIDTH}b{e:0{WIDTH}b}, {WIDTH}b{m:0{WIDTH}b}) // {note}" for w, e, m, note in vecs]
        out.append("    }")
    out.append("}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    legal = legal_vectors()
    groups = {
        "upper_and_jumps": [v for v in legal if v[3].split()[0] in ("lui", "auipc", "jal", "jalr")],
        "branches": [v for v in legal if v[3].startswith("b")],
        "loads_stores": [v for v in legal if v[3].split()[0] in ("lb", "lh", "lw", "lbu", "lhu", "sb", "sh", "sw")],
        "op_imm": [v for v in legal if v[3].split()[0] in ("addi", "slti", "sltiu", "xori", "ori", "andi",
                                                          "slli", "srli", "srai")],
        "op": [v for v in legal if v[3].split()[0] in ("add", "sub", "sll", "slt", "sltu", "xor", "srl",
                                                      "sra", "or", "and")],
        "system": [v for v in legal if v[3].split()[0] in ("fence", "ecall", "ebreak", "wfi", "mret")],
        "csr": [v for v in legal if v[3].split()[0].startswith("csr")],
        "illegal": illegal_vectors(),
    }
    assert sum(len(g) for g in groups.values()) == len(legal) + len(groups["illegal"]), "vector lost in grouping"
    dest = Path(__file__).resolve().parent.parent / "source" / "tb" / "decoder_tb.luc"
    dest.write_text(render(groups))
    print(f"wrote {dest} ({sum(len(g) for g in groups.values())} checks)")

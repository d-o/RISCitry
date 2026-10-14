"""Minimal RV32E instruction-set simulator: the golden model for CPU-level tests.

It executes one instruction per step and knows nothing about the hardware's FSM, so a test
can compare the core's architectural state (pc, registers) against it after every instruction.
"""
import re
from pathlib import Path

MASK = 0xFFFFFFFF


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def load_rom(luc_path):
    """Read {address: word} from instruction_mem.luc's `14hXXXX: instruction = 32hXXXXXXXX` lines."""
    text = Path(luc_path).read_text()
    pairs = re.findall(r"14h([0-9A-Fa-f]+):\s*instruction\s*=\s*32h([0-9A-Fa-f]+)", text)
    default = re.search(r"default:\s*instruction\s*=\s*32h([0-9A-Fa-f]+)", text)
    return {int(a, 16) * 4: int(w, 16) for a, w in pairs}, int(default.group(1), 16)


class Cpu:
    def __init__(self, rom, default_word=0x00000013):
        self.rom, self.default_word = rom, default_word
        self.pc = 0
        self.x = [0] * 16
        self.mem = {}

    def reg(self, r):
        if r > 15:
            raise ValueError(f"x{r} doesn't exist in RV32E")
        return self.x[r]

    def write(self, rd, value):
        if rd > 15:
            raise ValueError(f"x{rd} doesn't exist in RV32E")
        if rd:
            self.x[rd] = value & MASK

    def step(self):
        w = self.rom.get(self.pc, self.default_word)
        op, rd, f3 = w & 0x7F, (w >> 7) & 31, (w >> 12) & 7
        rs1, rs2, f7 = (w >> 15) & 31, (w >> 20) & 31, w >> 25
        i_imm = _sext(w >> 20, 12)
        next_pc = (self.pc + 4) & MASK

        def alu(f3, a, b, alt):
            sh = b & 31
            return {0: (a - b) if alt else (a + b), 1: a << sh,
                    2: int(_sext(a, 32) < _sext(b, 32)), 3: int(a < b), 4: a ^ b,
                    5: (_sext(a, 32) >> sh) if alt else (a >> sh), 6: a | b, 7: a & b}[f3]

        if op == 0x13:
            self.write(rd, alu(f3, self.reg(rs1), i_imm & MASK, f3 == 5 and f7 == 0x20))
        elif op == 0x33:
            self.write(rd, alu(f3, self.reg(rs1), self.reg(rs2), f7 == 0x20))
        elif op == 0x37:
            self.write(rd, w & 0xFFFFF000)
        elif op == 0x17:
            self.write(rd, self.pc + (w & 0xFFFFF000))
        elif op == 0x6F:
            imm = _sext((((w >> 31) & 1) << 20) | (((w >> 12) & 0xFF) << 12)
                        | (((w >> 20) & 1) << 11) | (((w >> 21) & 0x3FF) << 1), 21)
            self.write(rd, next_pc)
            next_pc = (self.pc + imm) & MASK
        elif op == 0x67:
            target = (self.reg(rs1) + i_imm) & MASK & ~1
            self.write(rd, next_pc)
            next_pc = target
        elif op == 0x63:
            imm = _sext((((w >> 31) & 1) << 12) | (((w >> 7) & 1) << 11)
                        | (((w >> 25) & 0x3F) << 5) | (((w >> 8) & 0xF) << 1), 13)
            a, b = self.reg(rs1), self.reg(rs2)
            taken = {0: a == b, 1: a != b, 4: _sext(a, 32) < _sext(b, 32),
                     5: _sext(a, 32) >= _sext(b, 32), 6: a < b, 7: a >= b}[f3]
            if taken:
                next_pc = (self.pc + imm) & MASK
        elif op == 0x03 and f3 == 2:
            self.write(rd, self.mem.get((self.reg(rs1) + i_imm) & MASK & ~3, 0))
        elif op == 0x23 and f3 == 2:
            s_imm = _sext(((w >> 25) << 5) | ((w >> 7) & 31), 12)
            self.mem[(self.reg(rs1) + s_imm) & MASK & ~3] = self.reg(rs2)
        else:
            raise NotImplementedError(f"pc={self.pc:#x}: {w:08X} not modelled")
        self.pc = next_pc

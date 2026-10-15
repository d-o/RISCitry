"""Minimal RV32E instruction-set simulator: the golden model for CPU-level tests.

It executes one instruction per step and knows nothing about the hardware's FSM, so a test
can compare the core's architectural state (pc, registers) against it after every instruction.
Deliberately strict: anything the core doesn't implement (CSRs, traps) raises, as do data
accesses outside memmap.py's ranges, misaligned accesses, and fetches or loads of words that were
neither preloaded nor written.

One address space (von Neumann, like the hardware's main_ram): the program image is preloaded
into RAM from address 0 (gaps as nops, as assembled), instructions are fetched from that same
memory, so loads can read code and stores can change it.

Prefetch, modelled exactly: the core reads the next instruction on the edge that ends the current
one, and a store's last cycle writes on that same edge with read-before-write. So a store over the
very next instruction doesn't affect it: the old word runs once (legal RISC-V without FENCE.I),
and the new one runs when that address is fetched again.

Illegal instructions (legal() mirrors decoder.luc) raise by default, so a typo in a test program
can't hide. With illegal_nop=True the model does what the core does: an illegal instruction is a
NOP, and a fetch from outside RAM reads 0 (the computer only answers loads from devices).

The UART has no timing here: STATUS always reads 0 (not busy), and each store to TXDATA that
writes byte lane 0 appends that byte to uart_out. The hardware's polling loops therefore run a
different number of times, so UART programs are checked by their output and final state.

The switch register reads back `switches` (bits 7:0, the rest 0); storing to it raises.
"""
import memmap

MASK = 0xFFFFFFFF
NOP = 0x00000013


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def legal(w):
    """Whether decoder.luc accepts the word (RV32E base, FENCE, ECALL, EBREAK)."""
    op, f3, f7 = w & 0x7F, (w >> 12) & 7, w >> 25
    rd, rs1, rs2 = (w >> 7) & 31, (w >> 15) & 31, (w >> 20) & 31
    uses = {0x37: "d", 0x17: "d", 0x6F: "d", 0x67: "d1", 0x63: "12", 0x03: "d1", 0x23: "12",
            0x13: "d1", 0x33: "d12", 0x0F: "", 0x73: ""}
    if op not in uses:
        return False
    bad = {0x67: f3 != 0, 0x63: f3 in (2, 3), 0x03: f3 in (3, 6, 7), 0x23: f3 > 2,
           0x13: (f3 == 1 and f7 != 0) or (f3 == 5 and f7 not in (0, 0x20)),
           0x33: f7 != 0 and not (f7 == 0x20 and f3 in (0, 5)),
           0x0F: f3 != 0, 0x73: (w >> 7) not in (0, 0x2000)}.get(op, False)
    fields = {"d": rd, "1": rs1, "2": rs2}
    return not bad and all(fields[u] < 16 for u in uses[op])


class Cpu:
    def __init__(self, words, illegal_nop=False, switches=0):
        """words: {byte address: instruction or data word} as assembled."""
        self.illegal_nop = illegal_nop
        self.switches = switches & 0xFF
        self.stale = None             # (address, word): the next fetch's prefetched old word
        self.pc = 0
        self.x = [0] * 16
        self.mem = {}                 # word-aligned byte address -> word, preloaded or written
        self.uart_out = []
        size = max(words) // 4 + 1
        if size * 4 > memmap.RAM_BYTES:
            raise ValueError(f"program ({size * 4} bytes) doesn't fit in RAM ({memmap.RAM_BYTES} bytes)")
        for i in range(size):
            self.mem[memmap.RAM_BASE + 4 * i] = words.get(4 * i, NOP)

    def reg(self, r):
        if r > 15:
            raise ValueError(f"x{r} doesn't exist in RV32E")
        return self.x[r]

    def write(self, rd, value):
        if rd > 15:
            raise ValueError(f"x{rd} doesn't exist in RV32E")
        if rd:
            self.x[rd] = value & MASK

    def _ram_addr(self, addr, size):
        """(word address, bit shift) for a little-endian access of `size` bytes."""
        addr &= MASK
        if addr % size:
            raise ValueError(f"pc={self.pc:#x}: misaligned {size}-byte access at {addr:#x} (not supported)")
        return addr & ~3, 8 * (addr & 3)

    def _region(self, addr):
        addr &= MASK
        if memmap.RAM_BASE <= addr < memmap.RAM_BASE + memmap.RAM_BYTES:
            return "ram"
        if memmap.UART_BASE <= addr < memmap.UART_BASE + memmap.UART_BYTES:
            return "uart"
        if memmap.SWITCHES_BASE <= addr < memmap.SWITCHES_BASE + memmap.SWITCHES_BYTES:
            return "switches"
        raise ValueError(f"pc={self.pc:#x}: data address {addr:#x} is unmapped")

    def _load(self, addr, size):
        word_addr, shift = self._ram_addr(addr, size)
        if self._region(addr) == "uart":
            if word_addr != memmap.UART_STATUS:
                raise ValueError(f"pc={self.pc:#x}: load from UART {addr:#x}: only STATUS is readable")
            return 0                                 # never busy: no timing in this model
        if self._region(addr) == "switches":
            return (self.switches >> shift) & ((1 << (8 * size)) - 1)
        if word_addr not in self.mem:
            raise ValueError(f"pc={self.pc:#x}: load from {word_addr:#x}: neither program nor stored")
        return (self.mem[word_addr] >> shift) & ((1 << (8 * size)) - 1)

    def _store(self, addr, size, value):
        word_addr, shift = self._ram_addr(addr, size)
        if self._region(addr) == "uart":
            if word_addr != memmap.UART_TXDATA:
                raise ValueError(f"pc={self.pc:#x}: store to UART {addr:#x}: only TXDATA is writable")
            if shift == 0:                           # only byte lane 0 sends
                self.uart_out.append(value & 0xFF)
            return
        if self._region(addr) == "switches":
            raise ValueError(f"pc={self.pc:#x}: store to the switch register {addr:#x} (read-only)")
        mask = ((1 << (8 * size)) - 1) << shift
        if word_addr == (self.pc + 4) & MASK and word_addr in self.mem:
            self.new_stale = (word_addr, self.mem[word_addr])   # already prefetched: old word runs
        old = self.mem.get(word_addr, 0)   # a partial store into an unwritten word: other bytes are 0
        self.mem[word_addr] = (old & ~mask) | ((value << shift) & mask)

    def fetch(self):
        """The instruction at pc (what step() will execute next)."""
        if self.stale and self.stale[0] == self.pc:
            return self.stale[1]
        in_ram = memmap.RAM_BASE <= self.pc < memmap.RAM_BASE + memmap.RAM_BYTES
        if self.illegal_nop and not in_ram:
            return 0                  # devices and unmapped space answer fetches with 0
        if self.pc % 4 or not in_ram:
            raise ValueError(f"fetch from {self.pc:#x}: not an aligned RAM address")
        if self.pc not in self.mem:
            raise ValueError(f"fetch from {self.pc:#x}: never written (RAM past the program reads 0)")
        return self.mem[self.pc]

    def step(self):
        w = self.fetch()
        self.new_stale = None
        if not legal(w):
            if not self.illegal_nop:
                raise NotImplementedError(f"pc={self.pc:#x}: {w:08X} is illegal on this core")
            self.pc = (self.pc + 4) & MASK                     # the core treats it as a NOP
            self.stale = None
            return
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
        elif op == 0x03 and f3 in (0, 1, 2, 4, 5):
            size = 1 << (f3 & 3)
            value = self._load(self.reg(rs1) + i_imm, size)
            if f3 < 4:                       # LB/LH/LW sign-extend; LBU/LHU zero-extend
                value = _sext(value, 8 * size)
            self.write(rd, value)
        elif op == 0x23 and f3 in (0, 1, 2):
            s_imm = _sext(((w >> 25) << 5) | ((w >> 7) & 31), 12)
            self._store(self.reg(rs1) + s_imm, 1 << f3, self.reg(rs2))
        elif (op == 0x0F and f3 == 0) or w in (0x00000073, 0x00100073):
            pass  # FENCE, ECALL, EBREAK: no-ops on this core (no traps yet)
        else:
            raise NotImplementedError(f"pc={self.pc:#x}: {w:08X} not modelled")
        self.pc = next_pc
        self.stale = self.new_stale

"""Minimal RV32E instruction-set simulator: the golden model for CPU-level tests.

It executes one instruction per step and knows nothing about the hardware's FSM, so a test
can compare the core's architectural state (pc, registers) against it after every instruction.
Deliberately strict: data
accesses outside memmap.py's ranges, misaligned accesses, and fetches or loads of words that were
neither preloaded nor written.

One address space (von Neumann, like the hardware's main_ram): the program image is preloaded
into RAM from address 0 (gaps as nops, as assembled), instructions are fetched from that same
memory, so loads can read code and stores can change it.

Prefetch, modelled exactly: the core reads the next instruction on the edge that ends the current
one, and a store's last cycle writes on that same edge with read-before-write. So a store over the
very next instruction doesn't affect it: the old word runs once (legal RISC-V without FENCE.I),
and the new one runs when that address is fetched again.

Traps, as the core takes them: illegal instruction (legal() mirrors decoder.luc; mtval = the
word), ECALL, EBREAK (mtval 0), a taken branch or jump to a target with bit 1 set (mtval = target),
a misaligned load or store (mtval = address). mepc = the instruction, MPIE = MIE, MIE = 0, pc =
mtvec; the instruction has no other effect. A fetch from outside RAM reads 0 (the computer only
answers loads from devices), so it traps as illegal. Any trap raises unless traps_ok (the program
expects traps), so a typo in a test program can't hide; step() leaves the trap in self.trapped.

The UART has no timing here: STATUS always reads 0 (not busy), and each store to TXDATA that
writes byte lane 0 appends that byte to uart_out. The hardware's polling loops therefore run a
different number of times, so UART programs are checked by their output and final state.

The switch register reads back `switches` (bits 7:0, the rest 0); storing to it raises.

Time: the machine timer's mtime counts the core's clocks (timer.luc), so the model counts them too,
with the core's clocks per instruction (clocks(): 2, store 3, load 4, a trap 2). self.mtime is its
value at the start of an instruction (FETCH). A load of the timer reads it as of MEM_ACCESS (start
+ 2); a store to mtime replaces that half on its last edge. The timer interrupt is checked at the
start of each instruction: MIE && MTIE && mtime >= mtimecmp (both as of that instruction's FETCH:
the core registers the comparison), and taken before the instruction does anything (mcause
0x80000007, mepc = pc, 2 clocks). Timing is exact only for programs without UART polling.

Buttons (buttons.luc): button_events = [(clock, levels)], levels applied before post-reset edge
`clock` (edge 0 is the first instruction's FETCH edge). Modelled edge by edge like the hardware:
two synchroniser flip-flops, a rising edge sets a pending bit, a store's last edge clears the bits
it writes as 1 (a press on that same edge wins). MEIP = any pending, seen in EXECUTE as of the
FETCH edge; a load reads pending (and the synchronised levels) as of MEM_ACCESS. External
interrupts beat the timer (cause 0x8000000B vs 0x80000007).
"""
import memmap

# The CSRs the core implements (mirrors cpu_defs.luc / csr_file.luc): address -> mask of the bits
# that are writable. Others read as their constant (misa) or 0.
CSR_WRITABLE = {0x300: 0x88, 0x304: 0x888, 0x305: 0xFFFFFFFC, 0x340: 0xFFFFFFFF,
                0x341: 0xFFFFFFFC, 0x342: 0xFFFFFFFF, 0x343: 0xFFFFFFFF}
CSR_CONSTANT = {0x301: 0x40000010, 0x310: 0, 0x344: 0, 0xF11: 0, 0xF12: 0, 0xF13: 0, 0xF14: 0}
MSTATUS_FIXED = 0x1800        # MPP = 3: machine mode is the only mode
MRET, WFI = 0x30200073, 0x10500073


def clocks(word, trapped=None):
    """Clocks the core spends on an instruction: FETCH + EXECUTE, plus MEM_ACCESS for a store and
    MEM_ACCESS + WRITEBACK for a load. One that traps (or is interrupted) takes 2."""
    if trapped or not legal(word):
        return 2
    op = word & 0x7F
    return 4 if op == 0x03 else 3 if op == 0x23 else 2


class Trap(Exception):
    """A synchronous exception: the instruction does nothing but this (raised inside step())."""
    def __init__(self, cause, tval):
        super().__init__(f"trap cause {cause}, mtval {tval:#x}")
        self.cause, self.tval = cause, tval

MASK = 0xFFFFFFFF
NOP = 0x00000013


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def legal(w):
    """Whether decoder.luc accepts the word (RV32E base, FENCE, ECALL, EBREAK, MRET, WFI, Zicsr
    on the implemented CSRs)."""
    op, f3, f7 = w & 0x7F, (w >> 12) & 7, w >> 25
    rd, rs1, rs2 = (w >> 7) & 31, (w >> 15) & 31, (w >> 20) & 31
    if op == 0x73 and f3 not in (0, 4):                # Zicsr
        csr = w >> 20
        writes = not (f3 & 2 and rs1 == 0)
        if csr not in CSR_WRITABLE and csr not in CSR_CONSTANT:
            return False
        if csr >> 10 == 3 and writes:                  # read-only address
            return False
        return rd < 16 and (f3 >= 4 or rs1 < 16)       # the zimm forms' rs1 field isn't a register
    uses = {0x37: "d", 0x17: "d", 0x6F: "d", 0x67: "d1", 0x63: "12", 0x03: "d1", 0x23: "12",
            0x13: "d1", 0x33: "d12", 0x0F: "", 0x73: ""}
    if op not in uses:
        return False
    bad = {0x67: f3 != 0, 0x63: f3 in (2, 3), 0x03: f3 in (3, 6, 7), 0x23: f3 > 2,
           0x13: (f3 == 1 and f7 != 0) or (f3 == 5 and f7 not in (0, 0x20)),
           0x33: f7 != 0 and not (f7 == 0x20 and f3 in (0, 5)),
           0x0F: f3 != 0, 0x73: w not in (0x00000073, 0x00100073, MRET, WFI)}.get(op, False)
    fields = {"d": rd, "1": rs1, "2": rs2}
    return not bad and all(fields[u] < 16 for u in uses[op])


class Cpu:
    def __init__(self, words, traps_ok=False, switches=0, button_events=()):
        """words: {byte address: instruction or data word} as assembled. traps_ok: the program
        expects traps (take them as the core does); otherwise any trap raises."""
        self.traps_ok = traps_ok
        self.trapped = None           # the last step's Trap, or None
        self.switches = switches & 0xFF
        self.stale = None             # (address, word): the next fetch's prefetched old word
        self.pc = 0
        self.x = [0] * 16
        self.mem = {}                 # word-aligned byte address -> word, preloaded or written
        self.uart_out = []
        self.csr = {a: 0 for a in CSR_WRITABLE}
        self.mtime = 0                # at the start of the current instruction
        self.mtimecmp = (1 << 64) - 1
        self.timer_write = None       # (word index, value): a store to mtime in this instruction
        self.cycle = 0                # post-reset edges before the current instruction
        self.button_events = sorted(button_events)
        self.btn = {"edge": 0, "sync0": 0, "sync1": 0, "last": 0, "pending": 0}
        self.btn_clear = None         # mask written by a store in this instruction
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
            raise ValueError(f"pc={self.pc:#x}: misaligned {size}-byte access at {addr:#x} (step() traps first)")
        return addr & ~3, 8 * (addr & 3)

    def _region(self, addr):
        addr &= MASK
        if memmap.RAM_BASE <= addr < memmap.RAM_BASE + memmap.RAM_BYTES:
            return "ram"
        if memmap.UART_BASE <= addr < memmap.UART_BASE + memmap.UART_BYTES:
            return "uart"
        if memmap.SWITCHES_BASE <= addr < memmap.SWITCHES_BASE + memmap.SWITCHES_BYTES:
            return "switches"
        if memmap.TIMER_BASE <= addr < memmap.TIMER_BASE + memmap.TIMER_BYTES:
            return "timer"
        if memmap.BUTTONS_BASE <= addr < memmap.BUTTONS_BASE + memmap.BUTTONS_BYTES:
            return "buttons"
        raise ValueError(f"pc={self.pc:#x}: data address {addr:#x} is unmapped")

    def _load(self, addr, size):
        word_addr, shift = self._ram_addr(addr, size)
        if self._region(addr) == "uart":
            if word_addr != memmap.UART_STATUS:
                raise ValueError(f"pc={self.pc:#x}: load from UART {addr:#x}: only STATUS is readable")
            return 0                                 # never busy: no timing in this model
        if self._region(addr) == "switches":
            return (self.switches >> shift) & ((1 << (8 * size)) - 1)
        if self._region(addr) == "buttons":
            self._buttons_to(self.cycle + 1)                        # as of MEM_ACCESS
            value = (self.btn["sync1"] << 8) | self.btn["pending"]
            return (value >> shift) & ((1 << (8 * size)) - 1)
        if self._region(addr) == "timer":
            if size != 4:
                raise ValueError(f"pc={self.pc:#x}: {size}-byte load from the timer (words only)")
            index = (word_addr - memmap.TIMER_BASE) // 4
            value = [self.mtime + 2, self.mtimecmp][index >> 1]     # as of MEM_ACCESS
            return (value >> (32 * (index & 1))) & MASK
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
        if self._region(addr) == "buttons":
            if shift == 0:                           # byte lane 0 carries the clear mask
                self.btn_clear = value & 0xF
            return
        if self._region(addr) == "timer":
            if size != 4:
                raise ValueError(f"pc={self.pc:#x}: {size}-byte store to the timer (the core ignores it)")
            index = (word_addr - memmap.TIMER_BASE) // 4
            if index < 2:
                self.timer_write = (index, value & MASK)
            else:
                half = 32 * (index & 1)
                self.mtimecmp = (self.mtimecmp & ~(MASK << half)) | ((value & MASK) << half)
            return
        mask = ((1 << (8 * size)) - 1) << shift
        if word_addr == (self.pc + 4) & MASK and word_addr in self.mem:
            self.new_stale = (word_addr, self.mem[word_addr])   # already prefetched: old word runs
        old = self.mem.get(word_addr, 0)   # a partial store into an unwritten word: other bytes are 0
        self.mem[word_addr] = (old & ~mask) | ((value << shift) & mask)

    def _level(self, edge):
        """The button levels the testbench applies before post-reset edge `edge`."""
        value = 0
        for clock, levels in self.button_events:
            if clock <= edge:
                value = levels
        return value

    def _buttons_to(self, edge, clear_edge=None, mask=0):
        """Advance the buttons device through post-reset edge `edge` (inclusive)."""
        b = self.btn
        while b["edge"] <= edge:
            e = b["edge"]
            pending = b["pending"]
            if e == clear_edge:
                pending &= ~mask
            pending |= b["sync1"] & ~b["last"]
            b["sync0"], b["sync1"], b["last"], b["pending"] = self._level(e), b["sync0"], b["sync1"], pending
            b["edge"] = e + 1

    def meip(self):
        """mip.MEIP during this instruction's EXECUTE (pending as of its FETCH edge)."""
        self._buttons_to(self.cycle)
        return self.btn["pending"] != 0

    def mtip(self):
        """mip.MTIP as the core sees it during this instruction (registered at its FETCH)."""
        return self.mtime >= self.mtimecmp

    def read_csr(self, csr):
        if csr == 0x344:
            return (int(self.meip()) << 11) | (int(self.mtip()) << 7)
        if csr in CSR_CONSTANT:
            return CSR_CONSTANT[csr]
        return self.csr[csr] | (MSTATUS_FIXED if csr == 0x300 else 0)

    def fetch(self):
        """The instruction at pc (what step() will execute next)."""
        if self.stale and self.stale[0] == self.pc:
            return self.stale[1]
        in_ram = memmap.RAM_BASE <= self.pc < memmap.RAM_BASE + memmap.RAM_BYTES
        if self.traps_ok and not in_ram:
            return 0                  # devices and unmapped space answer fetches with 0 (illegal)
        if self.pc % 4 or not in_ram:
            raise ValueError(f"fetch from {self.pc:#x}: not an aligned RAM address")
        if self.pc not in self.mem:
            raise ValueError(f"fetch from {self.pc:#x}: never written (RAM past the program reads 0)")
        return self.mem[self.pc]

    def step(self):
        w = self.fetch()
        self.new_stale = None
        self.trapped = None
        self.timer_write = None
        self.btn_clear = None
        start = self.mtime
        mstatus, mie = self.csr[0x300], self.csr[0x304]
        try:
            if mstatus & 0x8 and mie & 0x800 and self.meip():
                raise Trap(0x8000000B, 0)    # external interrupt: beats the timer
            if mstatus & 0x8 and mie & 0x80 and self.mtip():
                raise Trap(0x80000007, 0)    # timer interrupt: before the instruction does anything
            self._execute(w)
        except Trap as trap:
            if not self.traps_ok:
                raise ValueError(f"pc={self.pc:#x}: {w:08X} traps ({trap}) in a program that "
                                 "doesn't expect traps") from None
            self.trapped = trap
            mstatus = self.csr[0x300]
            self.csr[0x300] = (mstatus & ~0x88) | ((mstatus & 0x8) << 4)   # MPIE = MIE, MIE = 0
            self.csr[0x341] = self.pc
            self.csr[0x342] = trap.cause
            self.csr[0x343] = trap.tval & MASK
            self.pc = self.csr[0x305]
            self.stale = None
        if self.timer_write:                 # replaced on the store's last edge (start + 2)
            index, value = self.timer_write
            now = start + 2
            half = 32 * index
            self.mtime = (now & ~(MASK << half)) | (value << half)
        else:
            self.mtime = start + clocks(w, self.trapped)
        n = clocks(w, self.trapped)
        if self.btn_clear is not None:       # cleared on the store's last edge
            self._buttons_to(self.cycle + n - 1, clear_edge=self.cycle + 2, mask=self.btn_clear)
        self.cycle += n

    def _execute(self, w):
        if not legal(w):
            raise Trap(2, w)
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
            target = (self.pc + imm) & MASK
            if target & 2:
                raise Trap(0, target)
            self.write(rd, next_pc)
            next_pc = target
        elif op == 0x67:
            target = (self.reg(rs1) + i_imm) & MASK & ~1
            if target & 2:
                raise Trap(0, target)
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
                if next_pc & 2:
                    raise Trap(0, next_pc)
        elif op == 0x03 and f3 in (0, 1, 2, 4, 5):
            size = 1 << (f3 & 3)
            addr = (self.reg(rs1) + i_imm) & MASK
            if addr % size:
                raise Trap(4, addr)
            value = self._load(addr, size)
            if f3 < 4:                       # LB/LH/LW sign-extend; LBU/LHU zero-extend
                value = _sext(value, 8 * size)
            self.write(rd, value)
        elif op == 0x23 and f3 in (0, 1, 2):
            s_imm = _sext(((w >> 25) << 5) | ((w >> 7) & 31), 12)
            addr = (self.reg(rs1) + s_imm) & MASK
            if addr % (1 << f3):
                raise Trap(6, addr)
            self._store(addr, 1 << f3, self.reg(rs2))
        elif w == 0x00000073:
            raise Trap(11, 0)                # ECALL from machine mode
        elif w == 0x00100073:
            raise Trap(3, 0)                 # EBREAK (mtval 0: the spec allows it)
        elif (op == 0x0F and f3 == 0) or w == WFI:
            pass  # FENCE: a no-op on this in-order core; WFI: a legal no-op
        elif w == MRET:
            mstatus = self.csr[0x300]
            self.csr[0x300] = (mstatus & ~0x8) | ((mstatus >> 4) & 0x8) | 0x80   # MIE = MPIE, MPIE = 1
            next_pc = self.csr[0x341]
        elif op == 0x73:
            csr = w >> 20
            old = self.read_csr(csr)
            src = rs1 if f3 & 4 else self.reg(rs1)
            if not (f3 & 2 and rs1 == 0):
                new = {1: src, 2: old | src, 3: old & ~src}[f3 & 3]
                if csr in CSR_WRITABLE:
                    self.csr[csr] = new & CSR_WRITABLE[csr]
            self.write(rd, old)
        else:
            raise NotImplementedError(f"pc={self.pc:#x}: {w:08X} not modelled")
        self.pc = next_pc
        self.stale = self.new_stale

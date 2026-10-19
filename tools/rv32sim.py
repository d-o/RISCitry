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

The UART has no timing here: STATUS reads tx-not-busy, and each store to TXDATA that
writes byte lane 0 appends that byte to uart_out. The hardware's polling loops therefore run a
different number of times, so UART programs are checked by their output and final state.
The receiver has none either: rx_in is the list of bytes the program will be sent, all of them
there from the start. STATUS bit 1 (valid) is set while any remain; a load of RXDATA returns the
next one with bit 8 set and removes it (0 when none is left); the platform line 0 (mip/mie bit 16)
is high while any remain. rx_from: they all appear at that post-reset clock (default 0). A program whose result doesn't depend on when a byte arrives therefore
agrees with the hardware, which gets them off the serial line one at a time. No overrun here.

The switch register reads back `switches` (bits 7:0, the rest 0); storing to it raises.

The 7-segment display (display_chars.luc): self.display = owned << 32 | the four characters (byte
k = digit k from the left), after the current instruction. Any store writes its lanes and sets owned.

Time: the machine timer's mtime counts the core's clocks (timer.luc), so the model counts them too,
with the core's clocks per instruction (clocks(): 2, store 3, load 4, a trap 2). mtime_at(self.raw) is
its value at the start of an instruction (FETCH). A load of the timer reads it as of MEM_ACCESS (start
+ 2); a store to mtime replaces that half on its last edge. The timer interrupt is checked at the
start of each instruction: MIE && MTIE && mtime >= mtimecmp (both as of that instruction's FETCH:
the core registers the comparison), and taken before the instruction does anything (mcause
0x80000007, mepc = pc, 2 clocks). Timing is exact only for programs without UART polling.
WFI waits in EXECUTE, one extra clock at a time (self.stall), until an interrupt enabled in mie
is pending, each extra clock checked like a first EXECUTE (mtip(stall), meip(stall)); with MIE set
that interrupt is taken with mepc = the WFI + 4, otherwise the WFI completes. clocks_taken is the
step's clocks, stall included. A WFI with nothing enabled in mie waits forever (raises here).
timer_rate R: the hardware timer runs on every clock (real time, timer.luc) while the cpu is
enabled on one clock in R (ce; the _ce3 testbenches). Counting from the start of an instruction
(R raw clocks per cpu clock): a load reads mtime at start + 3R - 1 (the clock before MEM_ACCESS's
enabled edge), a store replaces a half on that edge (the other half then holds start + 3R - 1),
MTIP in EXECUTE is the compare of start + 2R - 2 (registered one raw clock before EXECUTE's enabled
edge), and the next instruction starts at start + nR. R = 1 gives the formulas above.
tick_div D: the timer's prescaler ticks mtime every D raw clocks (TICK_DIV; it's also the timebase
word). "mtime at raw clock r" above is then base + r // D, the prescaler counting from reset like
the raw clocks; a store sets base so the written value holds from the clock after the write's
edge (the prescaler isn't reset by it). D = 1: mtime is the raw clock count, as before.

Buttons (buttons.luc): button_events = [(clock, levels)], levels applied before post-reset edge
`clock` (edge 0 is the first instruction's FETCH edge). Modelled edge by edge like the hardware:
two synchroniser flip-flops, a rising edge sets a pending bit, a store's last edge clears the bits
it writes as 1 (a press on that same edge wins); a press while its bit is still pending sets that
button's overrun bit (bits 7:4, cleared the same way). MEIP = any pending, seen in EXECUTE as of the
FETCH edge; a load reads pending (and the synchronised levels) as of MEM_ACCESS. External
interrupts beat the timer (cause 0x8000000B vs 0x80000007).
Platform lines (mip/mie bits 16, 17; causes 0x80000010/0x80000011; the vectored table's slots 16, 17):
plat_events = [(clock, levels)], 2 bits, levels applied like the buttons' (before post-reset edge
`clock` * rate) but straight into the csr_file, no synchroniser: a level is seen by the EXECUTE
whose enabled edge it reaches. Priority after the standard ones: line 0, then line 1.
The buttons run on every raw clock, ce or not (always on, 2026-10-17): with timer_rate R an event at
clock c applies before raw edge c * R, and the cpu sees the device as it was one raw clock before
its own (enabled) edge, so a press between two cpu clocks is caught. R = 1 is the old model.
"""
import memmap

# The CSRs the core implements (mirrors cpu_defs.luc / csr_file.luc): address -> mask of the bits
# that are writable. Others read as their constant (misa) or 0.
CSR_WRITABLE = {0x300: 0x88, 0x304: 0x30888, 0x305: 0xFFFFFFFD, 0x340: 0xFFFFFFFF,
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
    def __init__(self, words, traps_ok=False, switches=0, button_events=(), timer_rate=1, tick_div=1,
                 plat_events=(), rx_in=(), rx_from=0):
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
        self.rx_in = list(rx_in)      # bytes still to be received (see the docstring)
        self.rx_from = rx_from        # ... all of which are there from this post-reset clock on
        self.display = 0
        self.csr = {a: 0 for a in CSR_WRITABLE}
        self.rate = timer_rate        # raw clocks (timer ticks) per cpu clock
        self.tick_div = tick_div      # the timer's timebase word (clocks per tick; 1 in the tests)
        self.raw = 0                  # raw clocks (ce or not) before the current instruction
        self.mtime_base = 0           # mtime at raw clock r = mtime_base + r // tick_div
        self.mtimecmp = (1 << 64) - 1
        self.timer_write = None       # (word index, value): a store to mtime in this instruction
        self.cycle = 0                # post-reset edges before the current instruction
        self.button_events = sorted(button_events)
        self.plat_events = sorted(plat_events)
        self.btn = {"edge": 0, "sync0": 0, "sync1": 0, "last": 0, "pending": 0, "overrun": 0}
        self.btn_clear = None         # mask written by a store in this instruction
        self.cnt = {"edge": 0, "enable": 0, "irq_en": 0, "src": 0, "n": 0, "top": 0xFFFF,
                    "count": 0, "flag": 0, "pre": 0}   # counter.luc, in cpu clocks (see _cnt_to)
        self.cnt_write = None         # (register, value) stored to the counter in this instruction
        self.leds = [0] * 24          # the LED dimmer bank's duty bytes ...
        self.leds_ctrl = 0            # ... and CTRL (enable, n, mode: bits 4:0); the waveforms aren't modelled
        self.stall = 0                # the last step's WFI wait, in extra EXECUTE clocks
        self.clocks_taken = 0         # the last step's clocks (clocks() + stall)
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
        if memmap.DISPLAY_BASE <= addr < memmap.DISPLAY_BASE + memmap.DISPLAY_BYTES:
            return "display"
        if memmap.COUNTER_BASE <= addr < memmap.COUNTER_BASE + memmap.COUNTER_BYTES:
            return "counter"
        if memmap.LEDS_BASE <= addr < memmap.LEDS_BASE + memmap.LEDS_BYTES:
            return "leds"
        raise ValueError(f"pc={self.pc:#x}: data address {addr:#x} is unmapped")

    def _load(self, addr, size):
        word_addr, shift = self._ram_addr(addr, size)
        if self._region(addr) == "uart":
            if word_addr == memmap.UART_STATUS:      # tx never busy (no timing in this model), no overrun
                return ((int(self.rx_ready()) << 1) >> shift) & ((1 << (8 * size)) - 1)
            if word_addr != memmap.UART_RXDATA:
                raise ValueError(f"pc={self.pc:#x}: load from UART {addr:#x}: only STATUS and RXDATA are readable")
            value = (0x100 | self.rx_in.pop(0)) if self.rx_ready() else 0     # the read pops
            return (value >> shift) & ((1 << (8 * size)) - 1)
        if self._region(addr) == "switches":
            return (self.switches >> shift) & ((1 << (8 * size)) - 1)
        if self._region(addr) == "display":
            return ((self.display & MASK) >> shift) & ((1 << (8 * size)) - 1)
        if self._region(addr) == "buttons":
            self._buttons_to(self._sampled_by(self.cycle + 2))      # as of MEM_ACCESS
            value = (self.btn["sync1"] << 8) | (self.btn["overrun"] << 4) | self.btn["pending"]
            return (value >> shift) & ((1 << (8 * size)) - 1)
        if self._region(addr) == "leds":
            word = (word_addr - memmap.LEDS_BASE) // 4
            if word < 6:
                value = int.from_bytes(bytes(self.leds[4 * word:4 * word + 4]), "little")
            else:
                value = self.leds_ctrl if word == 6 else 0
            return (value >> shift) & ((1 << (8 * size)) - 1)
        if self._region(addr) == "counter":
            if size != 4:
                raise ValueError(f"pc={self.pc:#x}: {size}-byte load from the counter (words only)")
            self._cnt_to(self.cycle + 2)                 # as of MEM_ACCESS
            c = self.cnt
            return [c["n"] << 4 | c["src"] << 2 | c["irq_en"] << 1 | c["enable"], c["top"], c["count"],
                    c["flag"]][(word_addr - memmap.COUNTER_BASE) // 4]
        if self._region(addr) == "timer":
            if size != 4:
                raise ValueError(f"pc={self.pc:#x}: {size}-byte load from the timer (words only)")
            index = (word_addr - memmap.TIMER_BASE) // 4
            if index == 4:
                return self.tick_div
            if index > 4:
                raise ValueError(f"pc={self.pc:#x}: load from the timer at {addr:#x}: nothing there")
            value = [self.mtime_at(self.raw + 3 * self.rate - 1), self.mtimecmp][index >> 1]   # as of MEM_ACCESS
            return (value >> (32 * (index & 1))) & MASK
        if word_addr not in self.mem:
            raise ValueError(f"pc={self.pc:#x}: load from {word_addr:#x}: neither program nor stored")
        return (self.mem[word_addr] >> shift) & ((1 << (8 * size)) - 1)

    def _store(self, addr, size, value):
        word_addr, shift = self._ram_addr(addr, size)
        if self._region(addr) == "uart":
            if word_addr == memmap.UART_STATUS:      # a 1 in bit 2 clears overrun: nothing to clear here
                return
            if word_addr != memmap.UART_TXDATA:
                raise ValueError(f"pc={self.pc:#x}: store to UART {addr:#x}: only TXDATA and STATUS are writable")
            if shift == 0:                           # only byte lane 0 sends
                self.uart_out.append(value & 0xFF)
            return
        if self._region(addr) == "switches":
            raise ValueError(f"pc={self.pc:#x}: store to the switch register {addr:#x} (read-only)")
        if self._region(addr) == "display":
            mask = ((1 << (8 * size)) - 1) << shift
            self.display = (1 << 32) | (self.display & MASK & ~mask) | ((value << shift) & mask)
            return
        if self._region(addr) == "buttons":
            if shift == 0:                           # byte lane 0 carries the clear mask
                self.btn_clear = value & 0xFF
            return
        if self._region(addr) == "leds":
            word = (word_addr - memmap.LEDS_BASE) // 4
            for k in range(size):               # byte lanes shift/8 ...
                lane, byte = shift // 8 + k, (value >> (8 * k)) & 0xFF
                if word < 6:
                    self.leds[4 * word + lane] = byte
                elif word == 6 and lane == 0:   # CTRL answers lane 0 only
                    self.leds_ctrl = byte & 0x1F
            return
        if self._region(addr) == "counter":
            if size != 4:
                raise ValueError(f"pc={self.pc:#x}: {size}-byte store to the counter (the core ignores it)")
            self.cnt_write = ((word_addr - memmap.COUNTER_BASE) // 4, value & MASK)
            return
        if self._region(addr) == "timer":
            if size != 4:
                raise ValueError(f"pc={self.pc:#x}: {size}-byte store to the timer (the core ignores it)")
            index = (word_addr - memmap.TIMER_BASE) // 4
            if index >= 4:
                raise ValueError(f"pc={self.pc:#x}: store to the timer at {addr:#x} (read-only; the core ignores it)")
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

    def _sampled_by(self, m):
        """The last raw edge whose result cpu edge m (m cpu clocks after the first instruction's
        FETCH edge counts as 0) sees: the buttons run on every raw clock, the cpu on every
        rate-th, so the device state at the clock before edge m's raw clock. rate 1: m - 1."""
        return m * self.rate + self.rate - 2

    def _level(self, edge):
        """The button levels the testbench applies before raw post-reset edge `edge` (an event at
        clock c is before raw edge c * rate: the buttons are on the free-running clock)."""
        value = 0
        for clock, levels in self.button_events:
            if clock * self.rate <= edge:
                value = levels
        return value

    def _buttons_to(self, edge, clear_edge=None, mask=0):
        """Advance the buttons device through raw post-reset edge `edge` (inclusive). It runs on
        every clock (buttons.luc), not just the cpu's."""
        b = self.btn
        while b["edge"] <= edge:
            e = b["edge"]
            pending, overrun = b["pending"], b["overrun"]
            if e == clear_edge:
                pending &= ~mask & 0xF
                overrun &= ~(mask >> 4) & 0xF
            rise = b["sync1"] & ~b["last"] & 0xF
            overrun |= rise & pending          # a press while still pending: merged, reported
            pending |= rise
            b["sync0"], b["sync1"], b["last"] = self._level(e), b["sync0"], b["sync1"]
            b["pending"], b["overrun"] = pending, overrun
            b["edge"] = e + 1

    def _cnt_to(self, edges, write_at=None, write=None):
        """Advance the counter (counter.luc) until `edges` cpu edges have been applied: its state
        during cpu clock k is the state after k edges. A write lands on edge `write_at`. Source 0
        (the cpu clock) and source 1 (every clock; only modelled with one raw clock per cpu clock)
        count; source 2 (the external input) never does here: the input is tied low."""
        c = self.cnt
        while c["edge"] < edges or (write_at is not None and c["edge"] <= write_at):
            e = c["edge"]
            if c["src"] == 1 and self.rate != 1:
                raise ValueError("counter source 1 (every clock) is only modelled with ce on every clock")
            step = c["src"] in (0, 1)
            mask = (1 << c["n"]) - 1
            tick = c["enable"] and (c["pre"] & mask) == mask and step
            wrap = tick and c["count"] >= c["top"]
            if not c["enable"]:
                c["pre"] = 0
            elif step:
                c["pre"] = (c["pre"] + 1) & 0x7FFF
            if wrap:
                c["count"], c["flag"] = 0, 1
            elif tick:
                c["count"] = (c["count"] + 1) & 0xFFFF
            if write is not None and e == write_at:
                reg, v = write
                if reg == 0:
                    c["enable"], c["irq_en"], c["src"], c["n"] = v & 1, v >> 1 & 1, v >> 2 & 3, v >> 4 & 15
                elif reg == 1:
                    c["top"] = v & 0xFFFF
                elif reg == 2:
                    c["count"] = v & 0xFFFF
                elif v & 1 and not wrap:
                    c["flag"] = 0
            c["edge"] = e + 1

    def meip(self, stall=0):
        """mip.MEIP during this instruction's EXECUTE (pending as of its FETCH edge), or during
        its stall-th extra EXECUTE clock while a WFI waits."""
        self._buttons_to(self._sampled_by(self.cycle + 1 + stall))
        return self.btn["pending"] != 0

    def rx_ready(self, stall=0):
        """A received byte is waiting (the receiver's valid flag)."""
        return bool(self.rx_in) and self.cycle + 1 + stall >= self.rx_from

    def plat(self, stall=0):
        """The platform lines (2 bits) during this instruction's EXECUTE: the level at its enabled
        edge, or at the stall-th extra EXECUTE clock's (a WFI)."""
        edge = (self.cycle + 1 + stall) * self.rate + self.rate - 1
        value = 0
        for clock, levels in self.plat_events:
            if clock * self.rate <= edge:
                value = levels
        self._cnt_to(self.cycle + 1 + stall)
        counter_irq = self.cnt["flag"] & self.cnt["irq_en"]
        return value | int(self.rx_ready(stall)) | counter_irq << 1   # the UART's receiver is line 0, the counter 1

    def mtip(self, stall=0):
        """mip.MTIP as the core sees it during this instruction's EXECUTE (registered one raw
        clock before EXECUTE's enabled edge), or its stall-th extra EXECUTE clock (a WFI)."""
        return self.mtime_at(self.raw + (2 + stall) * self.rate - 2) >= self.mtimecmp

    def mtime_at(self, raw):
        """mtime at raw clock `raw` (counting from the first instruction's start)."""
        return self.mtime_base + raw // self.tick_div

    def read_csr(self, csr):
        if csr == 0x344:
            return (self.plat() << 16) | (int(self.meip()) << 11) | (int(self.mtip()) << 7)
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
        self.cnt_write = None
        start = self.raw
        mstatus, mie = self.csr[0x300], self.csr[0x304]
        self.stall = 0                       # extra EXECUTE clocks a WFI spent waiting
        try:
            while True:
                if mstatus & 0x8 and mie & 0x800 and self.meip(self.stall):
                    raise Trap(0x8000000B, 0)    # external interrupt: beats the timer
                if mstatus & 0x8 and mie & 0x80 and self.mtip(self.stall):
                    raise Trap(0x80000007, 0)    # timer interrupt: before the instruction does anything
                if mstatus & 0x8 and (mie >> 16) & self.plat(self.stall) & 1:
                    raise Trap(0x80000010, 0)    # platform line 0
                if mstatus & 0x8 and (mie >> 16) & self.plat(self.stall) & 2:
                    raise Trap(0x80000011, 0)    # platform line 1
                if w != WFI or (mie & 0x800 and self.meip(self.stall)) or (mie & 0x80 and self.mtip(self.stall)) \
                        or (mie >> 16) & self.plat(self.stall):
                    break                        # (a WFI ends on an interrupt enabled in mie, whatever MIE)
                self.stall += 1
                if self.stall > 1_000_000:
                    raise ValueError(f"pc={self.pc:#x}: WFI never wakes (no interrupt enabled in mie?)")
            self._execute(w)
        except Trap as trap:
            if not self.traps_ok:
                raise ValueError(f"pc={self.pc:#x}: {w:08X} traps ({trap}) in a program that "
                                 "doesn't expect traps") from None
            self.trapped = trap
            mstatus = self.csr[0x300]
            self.csr[0x300] = (mstatus & ~0x88) | ((mstatus & 0x8) << 4)   # MPIE = MIE, MIE = 0
            self.csr[0x341] = (self.pc + 4) & MASK if w == WFI else self.pc   # after a WFI: the next one
            self.csr[0x342] = trap.cause
            self.csr[0x343] = trap.tval & MASK
            self.pc = self.csr[0x305] & ~3
            if self.csr[0x305] & 1 and trap.cause & 0x80000000:       # vectored: BASE | cause << 2
                self.pc |= (trap.cause & 0x1F) << 2
            self.stale = None
        if self.timer_write:                 # replaced on the store's last edge
            index, value = self.timer_write
            now = start + 3 * self.rate - 1
            half = 32 * index
            written = (self.mtime_at(now) & ~(MASK << half)) | (value << half)
            # it holds from the next raw clock (the write overrides that edge's tick)
            self.mtime_base = written - (now + 1) // self.tick_div
        self.raw = start + (clocks(w, self.trapped) + self.stall) * self.rate
        n = clocks(w, self.trapped) + self.stall
        self.clocks_taken = n
        if self.btn_clear is not None:       # cleared on the store's last edge
            last = lambda m: m * self.rate + self.rate - 1       # the raw edge of cpu edge m
            self._buttons_to(last(self.cycle + n - 1), clear_edge=last(self.cycle + 2), mask=self.btn_clear)
        if self.cnt_write is not None:       # taken on the store's last edge
            self._cnt_to(self.cycle + 2, write_at=self.cycle + 2, write=self.cnt_write)
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

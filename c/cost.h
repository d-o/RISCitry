/* What does multiply, divide and remainder cost on this core? It has no instructions for them:
   `*` `/` `%` call libgcc's shift-and-add / shift-and-subtract loops, so the cost depends on the
   operands. Each case is timed with the machine timer (mtime, one tick per TIMEBASE clocks): 16
   calls in a loop, minus the same loop around a function that does nothing, so the loop,
   the call and the operand loads cancel. In the tests a tick is one clock and the figures are
   exact; on the board a tick is 25 clocks, which 16 runs average out. */
#ifndef COST_H
#define COST_H
#include "uart.h"

#define MTIME    (*(volatile unsigned *)0x80002000u)   /* mtime, low word */
#define TIMEBASE (*(volatile unsigned *)0x80002010u)   /* clocks per tick */
#define RUNS 16                                        /* a power of two: the average is a shift */

typedef unsigned (*op_t)(unsigned, unsigned);

static unsigned __attribute__((noinline)) op_nop(unsigned a, unsigned b) { (void)b; return a; }
static unsigned __attribute__((noinline)) op_mul(unsigned a, unsigned b) { return a * b; }
static unsigned __attribute__((noinline)) op_div(unsigned a, unsigned b) { return a / b; }
static unsigned __attribute__((noinline)) op_mod(unsigned a, unsigned b) { return a % b; }

static volatile unsigned sink;                                /* keeps the results alive */

/* ticks for RUNS calls of op(a, b); the operands are read through volatiles so nothing folds */
static unsigned ticks(op_t op, unsigned a, unsigned b) {
    volatile unsigned va = a, vb = b;
    unsigned acc = 0;
    unsigned t0 = MTIME;
    for (int i = 0; i < RUNS; i++) acc ^= op(va, vb);
    unsigned t1 = MTIME;
    sink = acc;
    return t1 - t0;
}

static void report(const char *name, op_t op, unsigned a, unsigned b, unsigned base) {
    unsigned t = ticks(op, a, b) - base;
    puts_(name);
    putc_(' ');
    puth(a);
    putc_(' ');
    puth(b);
    puts_(": ");
    putu((t * TIMEBASE) / RUNS);
    puts_("\r\n");
}

static inline void cost_table(void) {
    unsigned base = ticks(op_nop, 1, 1);
    puts_("clocks per call, tick = ");
    putu(TIMEBASE);
    puts_(" clk\r\n");
    /* the multiplier's bit length sets the cost: 3 bits, 16 bits, 32 bits */
    report("mul", op_mul, 6, 7, base);
    report("mul", op_mul, 40000, 50000, base);
    report("mul", op_mul, 2654435769u, 3735928559u, base);
    /* division and remainder: the quotient's bit length */
    report("div", op_div, 100, 7, base);
    report("div", op_div, 100000, 7, base);
    report("div", op_div, 4000000000u, 7, base);
    report("mod", op_mod, 100, 7, base);
    report("mod", op_mod, 100000, 7, base);
    report("mod", op_mod, 4000000000u, 7, base);
}

#endif

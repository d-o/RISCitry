/* crt0's start-up contract: initialised data is back at its initial values on every start, however the
   program got there. The RAM keeps the last run's contents over a reset (it is only preloaded once), so
   crt0 copies .data from a pristine copy each time. This program changes its initialised variables,
   then jumps back to _start (what a reset does to the program) twice; every pass must print the same
   line: "5 30 2 7". Without the copy it prints 5 30 2 7, 6 99 2 7, 7 99 2 7 (then the bytes changed too).
   mscratch survives the restart (it is not RAM), so it counts the passes. */
#include "uart.h"

static int count = 5;
static int table[4] = {10, 20, 30, 40};
static unsigned char bytes[3] = {1, 2, 3};   /* three bytes: .data's size isn't a word multiple */
static const int ro = 7;                     /* const: stays in place, never copied */

int main(void) {
    unsigned runs;
    __asm__ volatile("csrr %0, mscratch" : "=r"(runs));
    putu(count);
    putc_(' ');
    putu(table[2]);
    putc_(' ');
    putu(bytes[1]);
    putc_(' ');
    putu(ro);
    puts_("\r\n");
    count++;
    table[2] = 99;
    bytes[1] = 77;                           /* a restart must undo all three */
    __asm__ volatile("csrw mscratch, %0" :: "r"(runs + 1));
    if (runs < 2)
        __asm__ volatile("jal x0, _start");
    return 0;
}

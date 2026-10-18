/* The first C program's output: a greeting, then a multiply, a divide and a remainder (which the
   core has no instructions for: libgcc's __mulsi3, __udivsi3 and __umodsi3 do them). */
#ifndef HELLO_H
#define HELLO_H
#include "uart.h"

static inline void hello(void) {
    volatile unsigned a = 6, b = 7, c = 100, d = 7;   /* volatile: the compiler must really compute */
    puts_("Hello from C!\n");
    putu(a * b);
    puts_(" ");
    putu(c / d);
    puts_(" r");
    putu(c % d);
    putc_('\n');
}

#endif

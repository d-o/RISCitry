/* First C program: prints over the UART, and uses multiply, divide and remainder, which the core
   has no instructions for (libgcc's __mulsi3, __udivsi3, __umodsi3 do them with adds and shifts). */
#include "uart.h"

volatile unsigned a = 6, b = 7, c = 100, d = 7;   /* volatile: the compiler must really multiply */

int main(void) {
    puts_("Hello from C!\n");
    putu(a * b);
    puts_(" ");
    putu(c / d);
    puts_(" r");
    putu(c % d);
    putc_('\n');
    return 0;
}

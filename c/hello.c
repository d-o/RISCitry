/* First C program: prints over the UART, and uses multiply, divide and remainder, which the core
   has no instructions for (libgcc's __mulsi3, __udivsi3, __umodsi3 do them with adds and shifts). */
#define UART_TXDATA (*(volatile unsigned *)0x80000000u)
#define UART_STATUS (*(volatile unsigned *)0x80000004u)

static void putc_(char c) {
    while (UART_STATUS & 1) ;
    UART_TXDATA = (unsigned char)c;
}

static void puts_(const char *s) {
    while (*s) putc_(*s++);
}

static void putu(unsigned v) {
    char buf[10];
    int n = 0;
    do { buf[n++] = '0' + v % 10; v /= 10; } while (v);
    while (n) putc_(buf[--n]);
}

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

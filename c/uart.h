/* UART transmit helpers shared by the C programs (memory map: tools/memmap.py). */
#ifndef UART_H
#define UART_H

#define UART_TXDATA (*(volatile unsigned *)0x80000000u)
#define UART_STATUS (*(volatile unsigned *)0x80000004u)
#define UART_RXDATA (*(volatile unsigned *)0x80000008u)   /* bit 8 = a byte arrived, bits 7:0 = it; the read pops */

static inline void putc_(char c) {
    while (UART_STATUS & 1) ;
    UART_TXDATA = (unsigned char)c;
}

/* the next received byte, or -1 if none has arrived */
static inline int getc_nb(void) {
    unsigned v = UART_RXDATA;
    return (v & 0x100) ? (int)(v & 0xFF) : -1;
}

static inline void puts_(const char *s) {
    while (*s) putc_(*s++);
}

static inline void putu(unsigned v) {
    char buf[10];
    int n = 0;
    do { buf[n++] = '0' + v % 10; v /= 10; } while (v);
    while (n) putc_(buf[--n]);
}

/* hex without a divide: the operands of the cost tests are 10 digits in decimal */
static inline void puth(unsigned v) {
    putc_('0');
    putc_('x');
    for (int shift = 28; shift >= 0; shift -= 4) putc_("0123456789ABCDEF"[(v >> shift) & 15]);
}

#endif

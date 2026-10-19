/* The board's boot-menu entry 7: the hello program, then the cost table, then an echo of whatever
   arrives on the serial input (it never halts). Linked at 0x800, above the menu and the other
   demos (cbuild.ORIGINS). With the dial's bit 3 set (switch register bit 3) it hands over to the fading
   Larson scanner at 0x1000 instead. */
#include "cost.h"
#include "devices.h"
#include "hello.h"

int main(void) {
    if (SWITCHES & 8)                       /* the dial's bit 3: the fading scanner (c/fader.c, at 0x1000) */
        ((void (*)(void))0x1000)();
    hello();
    puts_("\r\n");
    cost_table();
    puts_("type: ");
    for (;;) {
        int c = getc_nb();
        if (c >= 0) putc_((char)c);
    }
}

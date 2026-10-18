/* The board's boot-menu entry 7: the hello program, then the cost table, then an echo of whatever
   arrives on the serial input (it never halts). Linked at 0x800, above the menu and the other
   demos (cbuild.ORIGINS). */
#include "cost.h"
#include "hello.h"

int main(void) {
    hello();
    putc_('\n');
    cost_table();
    puts_("type: ");
    for (;;) {
        int c = getc_nb();
        if (c >= 0) putc_((char)c);
    }
}

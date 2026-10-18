/* The board's boot-menu entry 7: the hello program, then the cost table. Linked at 0x800, above
   the menu and the other demos (cbuild.ORIGINS). */
#include "cost.h"
#include "hello.h"

int main(void) {
    hello();
    putc_('\n');
    cost_table();
    return 0;
}

/* The board's boot-menu entry 7 with the dial's bit 3 set (the C demo hands over to this): the fading
   Larson scanner on all 24 LEDs, linked at 0x1000 (cbuild.ORIGINS). */
#include "fader.h"

int main(void) {
    fader_run();
}

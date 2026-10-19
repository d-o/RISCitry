/* The fader as a lockstep test: a frame every 3,000 clocks (the handler needs ~2,000), the head
   moving every frame, from address 0 like every test program. The board's version is fader.c. */
#define FRAME_N 0
#define FRAME_TOP 2999
#define STEP_FRAMES 1
#include "fader.h"

int main(void) {
    fader_run();
}

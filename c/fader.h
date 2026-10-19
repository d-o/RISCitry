/* The fading Larson scanner on the Io's 24 LEDs (notes/pwm-larson-plan.md).

   Two peripherals do the work, so the cpu does almost nothing: the general-purpose counter
   interrupts once per frame (about 100 Hz), and the LED dimmer bank holds one brightness byte per
   LED and does the dimming in hardware, continuously, as PWM or sigma-delta. Each frame this
   handler decays every LED's afterglow (like the incandescent bulbs on the original scanner),
   moves the bright head every STEP_FRAMES frames, and stores 24 gamma-corrected bytes as six words.
   Between frames the cpu sleeps in WFI. The mode (bit 4 of the switch register) is read live:
   flip it and the waveform on the pins changes while what the eye sees does not. */
#ifndef FADER_H
#define FADER_H
#include "devices.h"
#include "gamma.h"

#ifndef FRAME_N
#define FRAME_N 8            /* counter prescale: clock / 256 = 97.7 kHz ... */
#endif
#ifndef FRAME_TOP
#define FRAME_TOP 976        /* ... and 977 counts per frame: 99.95 Hz at 25 MHz */
#endif
#ifndef STEP_FRAMES
#define STEP_FRAMES 3        /* the head moves every third frame; the decay runs every frame */
#endif
#define NLEDS 24
#define LED_PRESCALE 3       /* the dimmer ticks at clock / 8: PWM at 12.3 kHz, sigma-delta edges at <= 1.6 MHz */

static unsigned char level[NLEDS];   /* linear brightness, 0..255 */
static int pos, dir = 1, phase;

static void __attribute__((interrupt("machine"))) frame_isr(void) {
    COUNTER_STATUS = 1;                          /* write 1 to clear first, or the level interrupt re-fires at once */
    for (int i = 0; i < NLEDS; i++)
        level[i] -= level[i] >> 2;               /* afterglow: keeps 3/4 per frame (stops at 3, which gamma maps to 0) */
    if (++phase >= STEP_FRAMES) {
        phase = 0;
        level[pos] = 255;                        /* the head */
        pos += dir;
        if (pos == 0 || pos == NLEDS - 1) dir = -dir;
    }
    for (int w = 0; w < NLEDS / 4; w++) {
        const unsigned char *l = &level[4 * w];
        LEDS_DUTY[w] = (unsigned)gamma_lut[l[0]] | (unsigned)gamma_lut[l[1]] << 8 |
                       (unsigned)gamma_lut[l[2]] << 16 | (unsigned)gamma_lut[l[3]] << 24;
    }
}

/* never returns */
static inline void fader_run(void) {
    __asm__ volatile("csrw mtvec, %0" :: "r"(frame_isr));
    COUNTER_TOP = FRAME_TOP;
    /* source 1: every clock. Source 0 (the cpu clock) would freeze while the cpu sleeps in WFI (the
       machine stops its clock enable) and never wake it: found in vsim, the lockstep tests never sleep */
    COUNTER_CTRL = (FRAME_N << 4) | (1 << 2) | 2 | 1;
    __asm__ volatile("csrs mie, %0" :: "r"(1u << 17));   /* platform line 1: the counter */
    __asm__ volatile("csrsi mstatus, 8");        /* MIE */
    for (;;) {
        LEDS_CTRL = 1 | LED_PRESCALE << 1 | ((SWITCHES >> 4 & 1) << 4);   /* enable, mode from the switch */
        __asm__ volatile("wfi");
    }
}

#endif

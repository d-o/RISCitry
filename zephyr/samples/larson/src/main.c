/* SPDX-License-Identifier: MIT */
#include <zephyr/kernel.h>
#include <zephyr/arch/cpu.h>
#include <zephyr/sys/sys_io.h>
#include "gamma.h"

/* The fading Larson scanner (the same one as c/fader.h) from Zephyr's main thread.
 * The LED dimmer bank holds one brightness byte per LED and does the dimming in hardware, as PWM
 * or as a sigma-delta 1-bit DAC; the thread only decays and moves the head once per frame. */
#define SWITCHES  DT_REG_ADDR(DT_NODELABEL(switches))
#define LEDS      DT_REG_ADDR(DT_NODELABEL(leds))
#define LEDS_CTRL (LEDS + 0x18)

#define NLEDS        24
#define FRAME_MS     10
#define STEP_FRAMES  3		/* the head moves every third frame */
#define DECAY_SHIFT  3		/* an LED keeps 7/8 of its level each frame */
#define LED_PRESCALE 3		/* the dimmer ticks at clock / 8 */

static uint8_t level[NLEDS];	/* linear brightness; .bss */

int main(void)
{
	int pos = 0, dir = 1, phase = 0;

	printk("larson up on %s\n", CONFIG_BOARD_TARGET);
	for (;;) {
		/* enable, prescale, mode from bit 4 of the middle DIP bank (0 PWM, 1 sigma-delta), read live */
		sys_write32(1 | (LED_PRESCALE << 1) | (((sys_read32(SWITCHES) >> 4) & 1) << 4), LEDS_CTRL);

		for (int i = 0; i < NLEDS; i++) {
			level[i] -= level[i] >> DECAY_SHIFT;
		}
		if (++phase >= STEP_FRAMES) {
			phase = 0;
			level[pos] = 255;
			pos += dir;
			if (pos == 0 || pos == NLEDS - 1) {
				dir = -dir;
			}
		}
		for (int w = 0; w < NLEDS / 4; w++) {
			const uint8_t *l = &level[4 * w];

			sys_write32((uint32_t)gamma_lut[l[0]] | (uint32_t)gamma_lut[l[1]] << 8 |
				    (uint32_t)gamma_lut[l[2]] << 16 | (uint32_t)gamma_lut[l[3]] << 24,
				    LEDS + 4 * w);
		}
		/* a relative sleep wakes one tick late (Part 6), so 9 ms makes a 10 ms frame */
		k_msleep(FRAME_MS - 1);
	}
	return 0;
}

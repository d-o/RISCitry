/* SPDX-License-Identifier: MIT */
#include <zephyr/kernel.h>
#include <zephyr/arch/cpu.h>
#include <zephyr/sys/sys_io.h>

#define SWITCHES DT_REG_ADDR(DT_NODELABEL(switches))

/* One line per period, from the machine timer: the first real use of the trap path.
 * The period is the middle DIP bank in milliseconds, 500 when it is 0 (so a simulated run can
 * ask for 1 ms and not wait hundreds of millions of clocks). */
int main(void)
{
	uint32_t ms = sys_read32(SWITCHES) & 0xFF;

	if (ms == 0) {
		ms = 500;
	}
	printk("tick up on %s\n", CONFIG_BOARD_TARGET);
	for (unsigned int n = 1; ; n++) {
		k_msleep(ms);
		printk("tick %u\n", n);
	}
	return 0;
}

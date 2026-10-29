/* SPDX-License-Identifier: MIT */
#include <zephyr/kernel.h>

int main(void)
{
	printk("Hello from Zephyr on %s\n", CONFIG_BOARD_TARGET);
	return 0;
}

/*
 * SPDX-License-Identifier: MIT
 *
 * System clock on the RISCitry mtime/mtimecmp (tools/memmap.py, source/computer/timer.luc): both
 * 64-bit, word access only, not latched across the two words, 1 MHz on the board. Derived from
 * the generic riscv_machine_timer driver, with the arithmetic kept to 32 bits. rv32 has no 64-bit
 * divide, so every `u64 / x` is a libgcc call (__udivdi3, 1.6 KB); here only 64-bit add, subtract
 * and compare remain (inline). The cycle counts are therefore bounded: no wait is longer than
 * CYCLES_MAX (3/4 of 2^32 cycles, about 53 minutes at 1 MHz); a longer sleep wakes early and is
 * re-armed by the kernel.
 */

#define DT_DRV_COMPAT riscitry_timer

#include <zephyr/init.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/timer/system_timer.h>
#include <zephyr/sys_clock.h>
#include <zephyr/irq.h>

#define MTIME_REG    DT_INST_REG_ADDR_BY_NAME(0, mtime)
#define MTIMECMP_REG DT_INST_REG_ADDR_BY_NAME(0, mtimecmp)
#define TIMER_IRQN   DT_INST_IRQN(0)

BUILD_ASSERT(IS_ENABLED(CONFIG_TICKLESS_KERNEL), "timer_riscitry.c is tickless only");
BUILD_ASSERT(CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC % CONFIG_SYS_CLOCK_TICKS_PER_SEC == 0,
	     "whole cycles per tick");

#define CYC_PER_TICK (CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC / CONFIG_SYS_CLOCK_TICKS_PER_SEC)
#define CYCLES_MAX   (UINT32_MAX / 4 * 3)
#define TICKS_MAX    (CYCLES_MAX / CYC_PER_TICK)

static uint64_t last_count;	/* mtime at the last announce, a multiple of CYC_PER_TICK from init */
static uint32_t last_elapsed;	/* ticks reported by sys_clock_elapsed() since then */

static void set_mtimecmp(uint64_t time)
{
	volatile uint32_t *r = (volatile uint32_t *)MTIMECMP_REG;

	/* Not latched: park the high word at max first, so no spurious match in between. */
	r[1] = 0xffffffff;
	r[0] = (uint32_t)time;
	r[1] = (uint32_t)(time >> 32);
}

static uint64_t mtime(void)
{
	volatile uint32_t *r = (volatile uint32_t *)MTIME_REG;
	uint32_t lo, hi;

	do {
		hi = r[1];
		lo = r[0];
	} while (r[1] != hi);

	return ((uint64_t)hi << 32) | lo;
}

static void timer_isr(const void *arg)
{
	ARG_UNUSED(arg);

	k_spinlock_key_t key = sys_clock_lock();

	/* now - last_count is below CYCLES_MAX + latency, so the low word is the whole difference */
	uint32_t dticks = (uint32_t)(mtime() - last_count) / CYC_PER_TICK;

	last_count += (uint32_t)(dticks * CYC_PER_TICK);
	last_elapsed = 0;

	sys_clock_announce_locked(dticks, key);
}

void sys_clock_set_timeout(uint32_t ticks, bool idle)
{
	ARG_UNUSED(idle);

	if (ticks > TICKS_MAX - last_elapsed) {
		ticks = TICKS_MAX - last_elapsed;	/* also covers K_TICKS_FOREVER */
	}
	set_mtimecmp(last_count + (uint32_t)((last_elapsed + ticks) * CYC_PER_TICK));
}

uint32_t sys_clock_elapsed(void)
{
	uint32_t dticks = (uint32_t)(mtime() - last_count) / CYC_PER_TICK;

	last_elapsed = dticks;
	return dticks;
}

uint32_t sys_clock_cycle_get_32(void)
{
	return *(volatile uint32_t *)MTIME_REG;
}

uint64_t sys_clock_cycle_get_64(void)
{
	return mtime();
}

static int sys_clock_driver_init(void)
{
	IRQ_CONNECT(TIMER_IRQN, 0, timer_isr, NULL, 0);
	last_count = mtime();
	set_mtimecmp(last_count + CYC_PER_TICK);
	irq_enable(TIMER_IRQN);
	return 0;
}

SYS_INIT(sys_clock_driver_init, PRE_KERNEL_2, CONFIG_SYSTEM_CLOCK_INIT_PRIORITY);

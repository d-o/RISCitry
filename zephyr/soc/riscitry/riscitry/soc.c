/* SPDX-License-Identifier: MIT */
#include <zephyr/kernel.h>
#include <zephyr/init.h>

/* Nothing to set up: the RAM is preloaded and the clocks are the board's. Hook for
 * riscv_soc_interrupt_init() if the vectored-mtvec alignment (BASE is 128-byte aligned in
 * csr_file.luc) needs handling here. */

# SPDX-License-Identifier: MIT
zephyr_library()
zephyr_library_sources_ifdef(CONFIG_TIMER_RISCITRY timer_riscitry.c)

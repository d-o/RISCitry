# SPDX-License-Identifier: MIT
zephyr_library()
zephyr_library_sources_ifdef(CONFIG_UART_RISCITRY uart_riscitry.c)

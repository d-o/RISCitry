/*
 * SPDX-License-Identifier: MIT
 *
 * UART of the RISCitry soft core. Registers (tools/memmap.py, source/computer/uart.luc):
 *   +0 TXDATA  write: send byte lane 0; dropped while STATUS.TX_BUSY
 *   +4 STATUS  read: bit 0 tx busy, bit 1 rx valid, bit 2 rx overrun; write a 1 to bit 2 to clear overrun
 *   +8 RXDATA  read: bits 7:0 the byte, bit 8 valid; the read pops it, empty reads 0
 * TX_BUSY means a byte is still waiting to be taken by the serialiser (a one-byte buffer), so a
 * write may follow while the previous byte is on the wire.
 */

#define DT_DRV_COMPAT riscitry_uart

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/arch/cpu.h>
#include <zephyr/sys/sys_io.h>

#define UART_TXDATA 0x0
#define UART_STATUS 0x4
#define UART_RXDATA 0x8

#define STATUS_TX_BUSY    BIT(0)
#define STATUS_RX_VALID   BIT(1)
#define STATUS_RX_OVERRUN BIT(2)
#define RXDATA_VALID      BIT(8)

struct uart_riscitry_config {
	mem_addr_t base;
};

static int uart_riscitry_poll_in(const struct device *dev, unsigned char *c)
{
	const struct uart_riscitry_config *cfg = dev->config;
	uint32_t rx = sys_read32(cfg->base + UART_RXDATA);

	if (!(rx & RXDATA_VALID)) {
		return -1;
	}
	*c = rx & 0xff;
	return 0;
}

static void uart_riscitry_poll_out(const struct device *dev, unsigned char c)
{
	const struct uart_riscitry_config *cfg = dev->config;

	while (sys_read32(cfg->base + UART_STATUS) & STATUS_TX_BUSY) {
	}
	sys_write32(c, cfg->base + UART_TXDATA);
}

static int uart_riscitry_err_check(const struct device *dev)
{
	const struct uart_riscitry_config *cfg = dev->config;
	int err = 0;

	if (sys_read32(cfg->base + UART_STATUS) & STATUS_RX_OVERRUN) {
		sys_write32(STATUS_RX_OVERRUN, cfg->base + UART_STATUS);
		err |= UART_ERROR_OVERRUN;
	}
	return err;
}

static DEVICE_API(uart, uart_riscitry_api) = {
	.poll_in = uart_riscitry_poll_in,
	.poll_out = uart_riscitry_poll_out,
	.err_check = uart_riscitry_err_check,
};

#define UART_RISCITRY_INIT(n)                                                                      \
	static const struct uart_riscitry_config uart_riscitry_cfg_##n = {                         \
		.base = DT_INST_REG_ADDR(n),                                                       \
	};                                                                                         \
	DEVICE_DT_INST_DEFINE(n, NULL, NULL, NULL, &uart_riscitry_cfg_##n, PRE_KERNEL_1,           \
			      CONFIG_SERIAL_INIT_PRIORITY, &uart_riscitry_api);

DT_INST_FOREACH_STATUS_OKAY(UART_RISCITRY_INIT)

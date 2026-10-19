/* Register access for the peripherals the C programs use beyond the UART (memory map:
   tools/memmap.py; the hardware: source/computer/counter.luc, led_dimmer.luc). */
#ifndef DEVICES_H
#define DEVICES_H

#define SWITCHES        (*(volatile unsigned *)0x80001000u)   /* the middle DIP bank, bits 7:0 */
#define TIMER_TIMEBASE  (*(volatile unsigned *)0x80002010u)   /* clocks per mtime tick (1 us): 25 on the board, 5 in vsim, 1 in tests */

/* general-purpose timer/counter: CTRL [0] enable, [1] irq enable (platform line 1, mie bit 17),
   [3:2] source (0 cpu clock), [7:4] prescale n; TOP [15:0]; COUNT; STATUS bit 0 = overflow, write 1 to clear */
#define COUNTER_CTRL    (*(volatile unsigned *)0x80005000u)
#define COUNTER_TOP     (*(volatile unsigned *)0x80005004u)
#define COUNTER_COUNT   (*(volatile unsigned *)0x80005008u)
#define COUNTER_STATUS  (*(volatile unsigned *)0x8000500Cu)

/* LED dimmer bank: 24 duty bytes (six words, LED 0 = lowest byte of word 0), then CTRL:
   [0] enable, [3:1] prescale n, [4] mode (0 PWM, 1 sigma-delta) */
#define LEDS_DUTY       ((volatile unsigned *)0x80006000u)
#define LEDS_CTRL       (*(volatile unsigned *)0x80006018u)

#endif

// SPDX-License-Identifier: GPL-3.0-or-later
// Stand-in for the iCE40 PLL primitive so Verilator can elaborate pll_25.v. vsim runs with
// PLL = 0 (sys_clock bypasses the PLL), so this is never instantiated; it only has to exist.
module SB_PLL40_CORE #(
    parameter FEEDBACK_PATH = "SIMPLE",
    parameter DIVR = 4'b0000,
    parameter DIVF = 7'b0000000,
    parameter DIVQ = 3'b000,
    parameter FILTER_RANGE = 3'b000
  ) (
    input REFERENCECLK,
    output PLLOUTGLOBAL,
    output LOCK,
    input RESETB,
    input BYPASS
  );
  assign PLLOUTGLOBAL = REFERENCECLK;
  assign LOCK = 1'b1;
endmodule

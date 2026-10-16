// 25 MHz system clock from the Cu's 100 MHz oscillator, using one of the iCE40's two PLLs.
// Settings from `icepll -i 100 -o 25`: PFD 100 MHz, VCO 800 MHz, divide by 32.
// The simulator doesn't run Verilog, so sys_clock.luc bypasses this in sim.
module pll_25 (
    input clk_in,
    output clk_out,
    output locked
  );

  SB_PLL40_CORE #(
    .FEEDBACK_PATH("SIMPLE"),
    .DIVR(4'b0000),
    .DIVF(7'b0000111),
    .DIVQ(3'b101),
    .FILTER_RANGE(3'b101)
  ) pll (
    .REFERENCECLK(clk_in),
    .PLLOUTGLOBAL(clk_out),
    .LOCK(locked),
    .RESETB(1'b1),
    .BYPASS(1'b0)
  );
endmodule

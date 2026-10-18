/* Start-up for the RV32E core: reset enters here at address 0 (or the board's menu jumps here). */
    .section .text.init
    .globl _start
_start:
    la      sp, _stack_top
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop
    la      t0, trap                # mtvec: a trap halts here (the core's reset value, 0, would
    csrw    mtvec, t0               # silently restart the program)
    la      t0, _bss_start          # zero .bss
    la      t1, _bss_end
1:  bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:  call    main
halt:
    j       halt                    # jal x0, 0: the lockstep tests treat it as the end
    .balign 4
trap:
    j       trap

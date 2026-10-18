/* One 8 KB RAM holds code and data (tools/memmap.py); main_ram's initial contents ARE the program,
   so there is no load address different from the run address and .data needs no copy at start-up. */
OUTPUT_ARCH(riscv)
ENTRY(_start)
/* __origin comes from cbuild.py (--defsym): 0 for a program that owns the machine, 0x800 for the
   board's menu entry 7, which lives above the menu and the other demos */
MEMORY { RAM (rwx) : ORIGIN = __origin, LENGTH = 8K - __origin }

SECTIONS
{
    .text : {
        KEEP(*(.text.init))          /* crt0 first: the reset pc is 0 */
        *(.text .text.*)
        . = ALIGN(4);
        _etext = .;                  /* end of code: cbuild.py checks every word below it is legal */
    } > RAM
    .rodata : { *(.rodata .rodata.* .srodata .srodata.*) . = ALIGN(4); } > RAM
    .data : { *(.data .data.*) } > RAM
    .sdata : {
        __global_pointer$ = . + 0x800;   /* gp-relative addressing reaches 2 KB either side */
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > RAM
    _edata = .;
    .sbss : { _bss_start = .; *(.sbss .sbss.* .scommon) } > RAM
    .bss : { *(.bss .bss.* COMMON) . = ALIGN(4); _bss_end = .; } > RAM
    _stack_top = ORIGIN(RAM) + LENGTH(RAM);   /* the stack grows down from the top of RAM */
    /DISCARD/ : { *(.comment .note* .eh_frame* .riscv.attributes) }
}

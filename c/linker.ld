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
    .data : { . = ALIGN(4); _data_start = .; *(.data .data.*) } > RAM
    .sdata : {
        __global_pointer$ = . + 0x800;   /* gp-relative addressing reaches 2 KB either side */
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > RAM
    _edata = .;
    /* The pristine copy of .data/.sdata sits here, appended to the image by cbuild.py (nothing links into
       it); crt0 copies it down on every start. The room is reserved so .bss doesn't overlap it. */
    . = . + (_edata - _data_start);
    _bss_start = .;                  /* set here, not inside .sbss: an empty section's symbols land earlier */
    .sbss : { *(.sbss .sbss.* .scommon) } > RAM
    .bss : { *(.bss .bss.* COMMON) . = ALIGN(4); } > RAM
    _bss_end = MAX(., _bss_start);   /* (an empty .bss leaves . behind the reserved copy room) */
    _stack_top = ORIGIN(RAM) + LENGTH(RAM);   /* the stack grows down from the top of RAM */
    /DISCARD/ : { *(.comment .note* .eh_frame* .riscv.attributes) }
}

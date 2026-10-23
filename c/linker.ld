/* One 8 KB RAM holds code and data (tools/memmap.py); main_ram's initial contents ARE the program,
   so a section's load address is its run address and nothing is copied from flash at start-up.
   But a reset does NOT reload the RAM: it keeps whatever the last run left. So crt0 restores .data
   from a pristine copy kept just after it (.datacopy, appended to the image by cbuild.py) and zeroes
   .bss on every start. Layout, low to high: .text, .rodata, .data/.sdata (initialised variables),
   .datacopy (their pristine copy), .sbss/.bss (zeroed variables), free RAM, the stack (grows down
   from the top of RAM). Nothing but the copy may sit on .datacopy: cbuild.py checks that. */
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
    /* The reservation is a real output section: a bare `. = . + n` between sections does not move the
       next region-placed section (the linker put .sbss, with `phase`, right on the copy, 2026-10-23). */
    .datacopy (NOLOAD) : { . = . + (ABSOLUTE(_edata) - ABSOLUTE(_data_start)); } > RAM
    _bss_start = .;                  /* set here, not inside .sbss: an empty section's symbols land earlier */
    .sbss : { *(.sbss .sbss.* .scommon) } > RAM
    .bss : { *(.bss .bss.* COMMON) . = ALIGN(4); } > RAM
    _bss_end = MAX(., _bss_start);   /* (an empty .bss leaves . behind the reserved copy room) */
    _stack_top = ORIGIN(RAM) + LENGTH(RAM);   /* the stack grows down from the top of RAM */
    /DISCARD/ : { *(.comment .note* .eh_frame* .riscv.attributes) }
}

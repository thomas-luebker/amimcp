/* fpu.c - amibench's FPU phase, the ONLY file compiled with -m68881.
 *
 * Why a file of its own: building the whole program with -m68881 also links
 * the FPU variant of the C library, whose startup code executes FPU
 * instructions before main() runs. On a 68020/030 without a coprocessor that
 * kills the program before amibench's own AttnFlags check gets a say - an
 * A1200 with an ACA1234 (030, no FPU) printed nothing and blocked amiagent
 * until the machine was reset (2026-11-06). Compiling just this loop for the
 * FPU and linking the integer C library keeps one binary that runs on every
 * 68020+, and main() calls run_fpu() only when AttnFlags reports an FPU.
 *
 * This file must stay free of library calls: no printf, no libm. The loop
 * below compiles to inline FPU instructions; anything that needed a support
 * routine would pull in soft-float helpers from the integer libgcc, which
 * this link does not carry. */

#include <exec/types.h>

volatile double g_fsink;

/* Dependency-chained doubles, so the FPU cannot overlap iterations. The
 * constants converge rather than diverge: values stay finite and normal, and
 * a denormal or a NaN would change the timing of what we are measuring. */
void run_fpu(ULONG reps)
{
    ULONG i;
    double a = 1.5, b = 3.25, c = 7.125;
    for (i = 0; i < reps; i++) {
        a = a * 0.5 + 0.25;
        b = b * 0.25 + a;
        c = c * 0.125 + b;
        a = a + c * 0.5;
    }
    g_fsink = a + b + c;
}

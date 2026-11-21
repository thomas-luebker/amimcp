/* amibench - a small CPU/memory benchmark for comparing Amigas.
 *
 * Built for 68020 so one identical binary runs on an 040, an 060 and Emu68's
 * JIT alike. Timing uses dos.library DateStamp (1/50 s ticks), which is far
 * too coarse for a fixed workload: Emu68 finished an early version's entire
 * 50 MB memcpy phase inside a single tick, while sizing that phase for Emu68
 * would leave a 68060 grinding for minutes.
 *
 * So every phase self-calibrates - it quadruples its rep count until it runs
 * for at least MIN_TICKS - and reports the reps it settled on alongside the
 * ticks they took. That keeps the measured window ~2 s on any machine.
 *
 * No rate arithmetic here and no floating point in the OUTPUT: that would drag
 * in printf's float formatting and change what is being measured, and byte
 * totals overflow 32 bits at these speeds. The host does the division.
 */

#include <exec/types.h>
#include <exec/memory.h>
#include <exec/execbase.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <dos/dos.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

extern struct ExecBase *SysBase;

/* For the Version command. Bump with dist/amibench-X.Y.readme. */
static const char vers[] __attribute__((used)) = "$VER: amibench 2.0 (21.11.2026)";

/* 4 MB per Fast buffer, 8 MB total. Deliberately larger than any cache in
 * play: at 256 KB the whole working set sat inside a Pi's L2, so Emu68 was
 * clocking cache bandwidth while a 68060 with its 8 KB caches clocked DRAM. */
#define BUFSZ      (4UL * 1024UL * 1024UL)

/* The Chip phases want 512 KB each - far past a 68060's 8 KB caches, which is
 * what matters for measuring the bus rather than the cache. But Chip RAM is
 * anything from 512 KB to 2 MB and the OS already sits in it: an A2000 with
 * 1 MB had 982 KB free, so a fixed 2 x 512 KB never allocated there and the
 * Chip phases were silently skipped on every run (2026-11-12 .. 11-21). So
 * the size halves until both buffers fit, down to CHIPSZ_MIN - still 2x the
 * largest cache in play. The size used is printed as chipsz, and the host
 * divides by that, so results from different sizes stay in MB/s. */
#define CHIPSZ_MAX (512UL * 1024UL)
#define CHIPSZ_MIN (16UL * 1024UL)
static ULONG g_chipsz;               /* what was actually allocated, or 0 */

#define MIN_TICKS  100UL             /* 2 s at 50 Hz */
#define MAX_REPS   0x20000000UL      /* cap; the guard below keeps reps*4 sane */

/* AttnFlags bits for "an FPU is present". A 68040/060 reports FPU40; a 020/030
 * with a coprocessor reports 68881 or 68882. Checked at RUNTIME because this
 * binary is compiled for hardware float: executing an FPU instruction on a
 * machine without one traps, so the phase must not run at all there. */
#define HAS_FPU(f) (((f) & (AFF_68881 | AFF_68882 | AFF_FPU40)) != 0)

static ULONG now_ticks(void)
{
    struct DateStamp ds;
    DateStamp(&ds);
    return (ULONG)ds.ds_Minute * 3000UL + (ULONG)ds.ds_Tick;
}

/* Ticks between two samples, tolerating the midnight wrap. */
static ULONG elapsed(ULONG start, ULONG end)
{
    return (end >= start) ? (end - start)
                          : (end + (24UL * 60UL * 3000UL) - start);
}

/* Sinks: volatile so -O2 cannot delete the loops it is meant to time. */
static volatile ULONG g_sink;
static UBYTE *g_src, *g_dst;         /* Fast */
static UBYTE *g_csrc, *g_cdst;       /* Chip */

static void run_int(ULONG reps)
{
    ULONG i, a = 1, b = 3, c = 7;
    for (i = 0; i < reps; i++) {
        a = a * 5UL + 13UL;
        b = (b ^ a) + (b >> 3);
        c = c + (a & 0xFFFFUL) - (b & 0xFFUL);
        a ^= c;
    }
    g_sink = a + b + c;
}

/* The FPU phase lives in fpu.c, the only file compiled with -m68881 - see
 * the comment there for why the rest of the program must not be. */
extern void run_fpu(ULONG reps);


static void run_copy(ULONG reps)
{
    ULONG i;
    for (i = 0; i < reps; i++)
        memcpy(g_dst, g_src, BUFSZ);
    g_sink = g_dst[0] + g_dst[BUFSZ - 1];
}

static void run_set(ULONG reps)
{
    ULONG i;
    for (i = 0; i < reps; i++)
        memset(g_dst, (int)(i & 0xFF), BUFSZ);
    g_sink = g_dst[0];
}

/* The scan loop is written out in full in both readers rather than shared via
 * a helper. Factoring it out cost the call-free inlining AND turned the word
 * count into a runtime argument, which measured 13% slower on Emu68 - stable
 * to 0.7% across runs, so a real change in what was measured, not noise. A
 * benchmark whose numbers move when you tidy its code is not measuring the
 * machine. Keep the duplication. */
static void run_read(ULONG reps)
{
    ULONG i, j, sum = 0;
    const ULONG words = BUFSZ / sizeof(ULONG);
    const ULONG *src = (const ULONG *)g_src;
    for (i = 0; i < reps; i++)
        for (j = 0; j < words; j += 8) {
            sum += src[j]     + src[j + 1] + src[j + 2] + src[j + 3];
            sum += src[j + 4] + src[j + 5] + src[j + 6] + src[j + 7];
        }
    g_sink = sum;
}

/* The Chip phases are the point of this version: everything else allocates
 * MEMF_FAST on purpose, so nothing here measured the chipset bus - the number
 * everyone quotes for that came from SysInfo, not from us. On a PiStorm the
 * Chip RAM is the real A1200 chipset reached across the bus, which is why it
 * can be SLOWER than a real A4000 while Fast RAM is ~70x faster. */
static void run_chipcopy(ULONG reps)
{
    ULONG i;
    for (i = 0; i < reps; i++)
        memcpy(g_cdst, g_csrc, g_chipsz);
    g_sink = g_cdst[0] + g_cdst[g_chipsz - 1];
}

static void run_chipread(ULONG reps)
{
    ULONG i, j, sum = 0;
    const ULONG words = g_chipsz / sizeof(ULONG);
    const ULONG *src = (const ULONG *)g_csrc;
    for (i = 0; i < reps; i++)
        for (j = 0; j < words; j += 8) {
            sum += src[j]     + src[j + 1] + src[j + 2] + src[j + 3];
            sum += src[j + 4] + src[j + 5] + src[j + 6] + src[j + 7];
        }
    g_sink = sum;
}

/* AllocMem'd memory is NOT returned when a program exits - it stays gone
 * until the next reboot. So every way out, the Ctrl-C one included, frees
 * the buffers here first. (The first Ctrl-C version called exit() straight
 * away and leaked 9 MB on the A1200 it was tested on.) */
static void free_all(void)
{
    if (g_csrc) { FreeMem(g_csrc, g_chipsz); g_csrc = NULL; }
    if (g_cdst) { FreeMem(g_cdst, g_chipsz); g_cdst = NULL; }
    if (g_src)  { FreeMem(g_src,  BUFSZ);  g_src  = NULL; }
    if (g_dst)  { FreeMem(g_dst,  BUFSZ);  g_dst  = NULL; }
}

/* Quadruple reps until the phase runs long enough to time honestly, then
 * print the pair the host needs. */
static void phase(const char *name, void (*fn)(ULONG), ULONG start_reps)
{
    ULONG reps = start_reps, t0, t;

    for (;;) {
        t0 = now_ticks();
        fn(reps);
        t = elapsed(t0, now_ticks());
        /* Ctrl-C after every attempt, never inside the timed loop. Without
         * it a run that misbehaves can only be ended by resetting the
         * machine: amiagent's BREAK sends Ctrl-C, and nothing here used to
         * look. Checked before the "long enough" test, so a phase that
         * settles on its first attempt still notices. */
        if (SetSignal(0, SIGBREAKF_CTRL_C) & SIGBREAKF_CTRL_C) {
            printf("*** break in %s\n", name);
            free_all();
            exit(5);
        }
        if (t >= MIN_TICKS)
            break;
        /* Break BEFORE scaling: an early version quadrupled and then let the
         * loop condition end the run, printing a rep count four times the one
         * whose ticks it printed. */
        if (reps > MAX_REPS / 4)
            break;
        reps *= 4;
    }
    printf("%s reps %lu ticks %lu\n", name,
           (unsigned long)reps, (unsigned long)t);
}

int main(void)
{
    UWORD flags = SysBase->AttnFlags;
    int have_chip;

    /* Fast RAM explicitly: MEMF_ANY could land in Chip on a machine short of
     * Fast, which would measure the chipset bus instead of the CPU. */
    g_src = (UBYTE *)AllocMem(BUFSZ, MEMF_FAST | MEMF_CLEAR);
    g_dst = (UBYTE *)AllocMem(BUFSZ, MEMF_FAST | MEMF_CLEAR);
    if (!g_src || !g_dst) {
        printf("amibench: out of Fast memory (need %lu KB)\n",
               (unsigned long)(2 * BUFSZ / 1024));
        free_all();
        return 20;
    }

    /* Halve until both fit. A half-success is freed before the next size,
     * since free_all() only knows one size. */
    for (g_chipsz = CHIPSZ_MAX; g_chipsz >= CHIPSZ_MIN; g_chipsz /= 2) {
        g_csrc = (UBYTE *)AllocMem(g_chipsz, MEMF_CHIP | MEMF_CLEAR);
        g_cdst = (UBYTE *)AllocMem(g_chipsz, MEMF_CHIP | MEMF_CLEAR);
        if (g_csrc && g_cdst)
            break;
        if (g_csrc) { FreeMem(g_csrc, g_chipsz); g_csrc = NULL; }
        if (g_cdst) { FreeMem(g_cdst, g_chipsz); g_cdst = NULL; }
    }
    have_chip = (g_csrc && g_cdst);
    if (!have_chip)
        g_chipsz = 0;

    printf("amibench 3 attnflags %04lx bufsz %lu chipsz %lu minticks %lu fpu %d chip %d\n",
           (unsigned long)flags, (unsigned long)BUFSZ, (unsigned long)g_chipsz,
           (unsigned long)MIN_TICKS, HAS_FPU(flags) ? 1 : 0, have_chip ? 1 : 0);

    phase("int",  run_int,  100000UL);
    if (HAS_FPU(flags))
        phase("fpu", run_fpu, 100000UL);
    else
        printf("fpu skipped (no FPU)\n");

    phase("copy", run_copy,     10UL);
    phase("set",  run_set,      10UL);
    phase("read", run_read,     10UL);

    if (have_chip) {
        phase("chipcopy", run_chipcopy, 10UL);
        phase("chipread", run_chipread, 10UL);
    } else {
        printf("chip skipped (could not allocate 2 x %lu KB of Chip)\n",
               (unsigned long)(CHIPSZ_MIN / 1024));
    }

    free_all();
    return 0;
}

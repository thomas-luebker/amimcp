/* amibench - a small, portable CPU/memory benchmark for comparing Amigas.
 *
 * Built for 68020 so one identical binary runs on an 040, an 060 and Emu68's
 * JIT alike. Timing uses dos.library DateStamp (1/50 s ticks), which is far
 * too coarse for a fixed workload: Emu68 finished the first version's entire
 * 50 MB memcpy phase inside a single tick, while sizing that phase for Emu68
 * would leave a 68060 grinding for minutes.
 *
 * So every phase self-calibrates - it quadruples its rep count until it runs
 * for at least MIN_TICKS - and reports the reps it settled on alongside the
 * ticks they took. That keeps the measured window ~2 s on any machine.
 *
 * No rate arithmetic here and no floating point: that would drag in
 * soft-float or an FPU dependency and change what is being measured. Byte
 * totals also overflow 32 bits at these speeds. The host does the division.
 */

#include <exec/types.h>
#include <exec/memory.h>
#include <exec/execbase.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <dos/dos.h>
#include <string.h>
#include <stdio.h>

extern struct ExecBase *SysBase;

/* 4 MB per buffer, 8 MB total. Deliberately larger than any cache in play:
 * at 256 KB the whole working set sat in a Pi's L2, so Emu68 was clocking
 * cache bandwidth while a 68060 with its 8 KB caches clocked DRAM. */
#define BUFSZ      (4UL * 1024UL * 1024UL)
#define MIN_TICKS  100UL             /* 2 s at 50 Hz */
#define MAX_REPS   0x20000000UL      /* cap; guard below keeps reps*4 in range */

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
static UBYTE *g_src, *g_dst;

static ULONG run_int(ULONG reps)
{
    ULONG i, a = 1, b = 3, c = 7;
    for (i = 0; i < reps; i++) {
        a = a * 5UL + 13UL;
        b = (b ^ a) + (b >> 3);
        c = c + (a & 0xFFFFUL) - (b & 0xFFUL);
        a ^= c;
    }
    g_sink = a + b + c;
    return 0;
}

static ULONG run_copy(ULONG reps)
{
    ULONG i;
    for (i = 0; i < reps; i++)
        memcpy(g_dst, g_src, BUFSZ);
    g_sink = g_dst[0] + g_dst[BUFSZ - 1];
    return 0;
}

static ULONG run_set(ULONG reps)
{
    ULONG i;
    for (i = 0; i < reps; i++)
        memset(g_dst, (int)(i & 0xFF), BUFSZ);
    g_sink = g_dst[0];
    return 0;
}

static ULONG run_read(ULONG reps)
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
    return 0;
}

/* Quadruple reps until the phase runs long enough to time honestly, then
 * print the pair the host needs. A phase that hits MAX_REPS reports what it
 * reached: better a flagged short sample than a silently unscaled one. */
static void phase(const char *name, ULONG (*fn)(ULONG), ULONG start_reps)
{
    ULONG reps = start_reps, t0, t;

    for (;;) {
        t0 = now_ticks();
        fn(reps);
        t = elapsed(t0, now_ticks());
        if (t >= MIN_TICKS)
            break;
        /* Break BEFORE scaling: an earlier version quadrupled and then let
         * the loop condition end the run, printing a rep count four times
         * the one whose ticks it printed. */
        if (reps > MAX_REPS / 4)
            break;
        reps *= 4;
    }
    printf("%s reps %lu ticks %lu\n", name,
           (unsigned long)reps, (unsigned long)t);
}

int main(void)
{
    /* Fast RAM explicitly: MEMF_ANY could land in Chip on a machine that is
     * short of Fast, which would measure the Chip bus rather than the CPU. */
    g_src = (UBYTE *)AllocMem(BUFSZ, MEMF_FAST | MEMF_CLEAR);
    g_dst = (UBYTE *)AllocMem(BUFSZ, MEMF_FAST | MEMF_CLEAR);
    if (!g_src || !g_dst) {
        printf("amibench: out of memory (need %lu KB)\n",
               (unsigned long)(2 * BUFSZ / 1024));
        if (g_src) FreeMem(g_src, BUFSZ);
        if (g_dst) FreeMem(g_dst, BUFSZ);
        return 20;
    }

    printf("amibench 2 attnflags %04lx bufsz %lu minticks %lu\n",
           (unsigned long)SysBase->AttnFlags,
           (unsigned long)BUFSZ, (unsigned long)MIN_TICKS);

    phase("int",  run_int,  100000UL);
    phase("copy", run_copy,     10UL);
    phase("set",  run_set,      10UL);
    phase("read", run_read,     10UL);

    FreeMem(g_src, BUFSZ);
    FreeMem(g_dst, BUFSZ);
    return 0;
}

# amibench

A small CPU/memory benchmark for comparing Amigas, written to answer "how much
faster is machine A than machine B" with numbers that survive scrutiny.

Built for **68020** so one identical binary runs on an 040, an 060 and Emu68's
JIT alike — comparing two different builds would measure the compiler as much
as the machine.

```
m68k-amigaos-gcc -O2 -m68020 -noixemul -Wall -o amibench amibench.c
```

Phases: integer ALU (dependency-chained, so it cannot be pipelined away),
**FPU**, `memcpy`, `memset`, a linear read, and **Chip RAM** copy and read.

> Built with `-m68881`, so it needs an FPU. Without that flag the compiler
> emits soft-float and the FPU phase would time an emulation library instead of
> the coprocessor. The phase is still gated on `AttnFlags` at runtime, because
> executing an FPU instruction on a machine without one traps.

## Three things it does deliberately

**Self-calibrating.** `DateStamp` resolves to 1/50 s. A fixed workload is
useless across a 70× speed range: Emu68 finished an entire 50 MB memcpy phase
inside a *single tick*, while sizing that phase for Emu68 would leave a 68060
grinding for minutes. Each phase quadruples its rep count until it runs ≥2 s.

**Buffers larger than any cache in play.** At 256 KB the whole working set sat
inside a Pi's 1 MB L2, so Emu68 was clocking cache bandwidth against a 68060
clocking DRAM — a flattering, meaningless comparison. 4 MB per buffer.

**No arithmetic and no floating point on the Amiga.** It prints raw reps and
ticks; the host divides. Rate maths would drag in soft-float or an FPU
dependency and change what is being measured, and byte totals overflow 32 bits
at these speeds.

The Fast phases allocate `MEMF_FAST` explicitly — `MEMF_ANY` can land in Chip on
a machine short of Fast and measure the chipset bus instead of the CPU. The Chip
phases measure that bus deliberately, with 512 KB buffers, because there is only
2 MB of Chip and the OS is already in it.

## Results so far

An A1200 + PiStorm32-lite (Emu68, Pi 4 @1.8 GHz) against an A4000/060 at
**100 MHz** (`cpuspeed SPEED 100`), 2026-08-08:

| Phase | A4000 @100 MHz | PiStorm | Ratio |
|---|---:|---:|---:|
| ALU | 5.8 M iter/s | 151.7 M iter/s | 26.0x |
| FPU | 2.9 M iter/s | 57.0 M iter/s | **19.6x** |
| memcpy | 34.5 MB/s | 1.18 GB/s | 34.1x |
| memset | 30.5 MB/s | 1.10 GB/s | 36.1x |
| read | 69.9 MB/s | 4.01 GB/s | 57.3x |
| **Chip memcpy** | **2.4 MB/s** | **2.1 MB/s** | **0.87x** |
| **Chip read** | **4.4 MB/s** | **2.7 MB/s** | **0.62x** |

Two results the Fast-RAM-only version could not have found:

**Chip RAM is where the PiStorm loses.** It is *slower than a real A4000* on
both Chip phases. And the gulf between its own Fast and Chip RAM is enormous -
**561x on copy, 1467x on read**, against 14x and 16x on the A4000. Anything
touching Chip RAM (native screens, audio buffers, floppy work, most unpatched
games and demos) falls off a cliff that simply does not exist on real hardware.
This is measured here rather than quoted from SysInfo, which was the point.

**The FPU advantage is smaller than the integer one** - 19.6x against 26.0x -
so Emu68 translates integer work more efficiently than floating point. Worth
noting SysInfo disagrees sharply: its MFLOPS put the same two machines **42x**
apart, more than double what this measures. Two tools, one machine pair, results
that differ by 2x: at least one of them is measuring something other than what
its label says. Unresolved.

## Reading the output, and its noise floor

Every phase is stable to **under 1%** run to run - except `memset`, which
spreads about **9%**. Treat differences under ~10% as noise unless you have
repeated them.

Compare **rates (reps / time), never raw ticks**. A phase that lands just under
the tick floor quadruples its reps and runs 4x longer for an identical rate: one
`memset` run reported 640 reps / 122 ticks and another 2560 reps / 490 ticks -
the same 1.10 GB/s, and an alarming-looking number if you read only the ticks.

> **Do not tidy the scan loops into a shared helper.** Factoring the read loop
> out into one cost the inlining and made the word count a runtime argument,
> which measured **13% slower on Emu68** - stable to 0.7% across runs, so a real
> change in what was measured, not noise. The duplication between `run_read` and
> `run_chipread` is deliberate. A benchmark whose numbers move when you clean up
> its code is not measuring the machine.

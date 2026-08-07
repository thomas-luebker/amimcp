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
`memcpy`, `memset`, and a linear read.

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

Allocations are `MEMF_FAST` — `MEMF_ANY` can land in Chip on a machine short of
Fast and measure the chipset bus instead of the CPU.

## Results so far

| Phase | A1200 + PiStorm32-lite (Emu68, Pi 4 @1.8 GHz) | A4000/060 @50 MHz | Ratio |
|---|---:|---:|---:|
| ALU | 152.8 M iter/s | 2.8 M iter/s | 54x |
| memcpy | 1.22 GB/s | 17.6 MB/s | 69x |
| memset | 1.12 GB/s | 16.5 MB/s | 68x |
| read | 4.10 GB/s | 35.5 MB/s | 116x |

Geometric mean ~73x. Cross-checks against SysInfo 4.4, which independently puts
the same two machines 50.1x apart on both Dhrystones and MIPS.

**Reproducibility.** The PiStorm figures above are a re-run on 2026-08-07. An
earlier run on 2026-08-05 — different SD card, same machine and same
`config.txt` — gave 151.7 M iter/s, 1.19 GB/s, 1.09 GB/s and 3.98 GB/s, i.e.
**within 0.7–3.1%**. That spread is what the ×4 rep scaling and a 1/50 s timer
cost you, and it is the right order of magnitude to treat as noise: differences
smaller than about 5% between two runs are not differences.

Note that Chip RAM does **not** follow: the PiStorm reaches the real A1200
chipset across its bus and measures 1.54x an A600, against the A4000's 2.25x.

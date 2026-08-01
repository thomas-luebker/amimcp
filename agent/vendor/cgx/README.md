# vendor/cgx — CyberGraphX interface files

`cybergraphics.library` is what makes truecolor and RTG screen capture
possible, and it is **not** part of NDK 3.2. These are the CyberGraphX SDK's
own interface files, so the library offsets amiagent calls are the SDK's
rather than something reverse-engineered.

That distinction matters more than usual here. A wrong library vector offset
does not fail to compile and does not fail cleanly at runtime — it jumps into
the middle of a different function and takes the machine down. On someone's
real Amiga.

| File | What it is |
|---|---|
| `cybergraphics_lib.fd` | The SDK's function-definition file — the source of truth for every offset |
| `clib/cybergraphics_protos.h` | C prototypes (argument types) |
| `cybergraphx/cybergraphics.h` | Structures and constants, including `RECTFMT_*` |
| `inline/cybergraphics.h` | **Generated.** Do not edit by hand |

## Regenerating the inline header

```sh
fd2pragma --infile cybergraphics_lib.fd \
          --clib clib/cybergraphics_protos.h \
          --special 40 --to .
mv cybergraphics.h inline/cybergraphics.h
```

`fd2pragma` ships with bebbo's amiga-gcc. `--special 40` produces the
preprocessor/`LP*`-macro style that NDK 3.2's own `inline/` headers use, so the
result drops straight in alongside them.

Sanity check after regenerating: `ReadPixelArray` must be `LP10(0x78, …)`
(offset -120), and `GetCyberMapAttr` must be `0x60` (-96) — the latter is
widely documented and is a cheap way to confirm the whole table lines up.

Copyright 1996-1998 phase5 digital products. See ../../../NOTICE.

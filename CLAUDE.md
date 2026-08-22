# amimcp — Agent Guide

## Long-term memory: the Obsidian vault

This project's durable state lives in the **Loki** Obsidian vault:

```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Loki/20 - Private/Retro Computing/Projects/amimcp.md
```

**Read that note at the start of a session** — its `## Status` and `## Next Actions` are the current state and the agreed next steps, and it holds the decisions and findings that are not in the code. **Update it when the state changes**: refresh `## Status` (dated), rewrite `## Next Actions`, bump `updated:` in the frontmatter.

This repo's own backlog stays the fine-grained list; the vault note is the durable summary and the cross-project view. Directory-wide rules: `~/Development/CLAUDE.md`.

## Tooling next door — do not rebuild it

Before scripting against a disk image, hand-typing on the Amiga, or hand-rolling
a 68k binary, check `~/Development/` — it is probably already built:

- **Disk images — AmigaDiskKit / `AmigaDiskCLI`** (`~/Development/AmigaDiskKit/`,
  also vendored into Amiga Imager). Pure-Swift MBR + RDB, FFS/OFS, PFS3, ADF,
  FAT32, LHA, ILBM/icons, `Int64` offsets throughout. The CLI is **not on
  `PATH`** — `~/Development/AmigaDiskKit/.build/arm64-apple-macosx/release/AmigaDiskCLI`
  — and does `disk rdb-build` / `rdb-format` / `fs dir|mkdir|copy|extract` /
  `lha create`. Sizes are in **bytes**; partitions are addressed by **name**.

- **68k C — bebbo's amiga-gcc**, built and working on this Mac but **not on
  `PATH`**: `~/opt/amiga/bin/m68k-amigaos-gcc` (GCC 6.5.0b), with NDK, libnix
  and ixemul under `~/opt/amiga/m68k-amigaos/`, plus `fd2pragma`, `fd2sfd` and
  the `ira` disassembler. Verified 2026-08-22. `~/Development/amipkg/Makefile`
  is the reference invocation — note `-lgcc` **after** the objects.

- **Software onto a system — `amipkg`** (`~/Development/amipkg/`): the on-Amiga
  package manager for AmigaOS 3.x — signed catalog, SHA-256 verification,
  CPU-aware dependency resolution, receipts for clean removal; CLI + GadTools
  GUI + MUI GUI. `~/Development/amiga-pkg/` is the catalog gateway that
  validates submissions and publishes the signed index.

- **Whole bootable systems — Amiga Imager** (`~/Development/AmigaImager/`):
  PiStorm/Emu68, Classic and emulator targets through a native Swift engine,
  with `AmigaImagerTools/main.swift` as its CLI.

- **Running one on iOS — Amigo** (`~/Development/Amigo/`): the WinUAE iOS port.
  Its emulated guest is reachable as an amiagent node like any other machine.

Full descriptions: `~/Development/CLAUDE.md`.

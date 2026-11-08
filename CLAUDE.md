# amimcp — Agent Guide

## Long-term memory: the Obsidian vault

This project's durable state lives in the **Loki** Obsidian vault:

```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Loki/20 - Private/Retro Computing/Projects/amimcp.md
```

**Read that note at the start of a session** — its `## Status` and `## Next Actions` are the current state and the agreed next steps, and it holds the decisions and findings that are not in the code. **Update it when the state changes**: refresh `## Status` (dated), rewrite `## Next Actions`, bump `updated:` in the frontmatter.

This repo's own backlog stays the fine-grained list; the vault note is the durable summary and the cross-project view. Directory-wide rules: `~/Development/CLAUDE.md`.

## Amiga tooling — the `amiga` plugin has it

Everything this section used to repeat now lives in one place: the **`amiga`
plugin** (`~/Development/AllAmigaTooling/`), installed at user scope so its
skills load in every directory.

| Need | Skill |
|---|---|
| What already exists and where each binary is | `amiga-tooling` |
| Run something on a real Amiga (amiagent) | `amiga-fleet` |
| Disk images, RDB, FFS/PFS3, ADF, LHA | `amiga-disk` |
| Cross-compile 68k C | `amiga-68k` |
| Build a whole bootable system | `amiga-image-build` |
| Package and publish software | `amiga-package` |
| Cut a release, upload to Aminet | `amiga-release` |
| `.info` icons, classic + GlowIcon | `amiga-icon` |
| Drive a GUI program with ARexx | `amiga-arexx` |
| Releases, downloads, press, feedback | `amiga-dash` |

Nothing here is on `PATH` — `amiga-tooling` is the skill that says where each
binary actually is. Directory-wide rules: `~/Development/CLAUDE.md`.


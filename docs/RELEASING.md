# Releasing amiagent

The release is built on the Mac and verified on a real Amiga. Nothing here is
automated on purpose — the archive is small and the checks are the kind a
script cannot do (does the icon show its tool types, does the thing start).

## 1. Bump and build

- `agent/proto.h` — `AMIAGENT_VERSION`.
- `cd agent && make clean && make` → 68000 build; keep `amiagent`, `amimon`,
  `amimon-mui`.
- `make clean && make CPU=68020` → rename each to `*.020`.
- Copy all six binaries into `dist/stage/amiagent/`.

> **Check the version inside the archive, not just that it packed.** The copy
> into `dist/stage` is manual, so the archive can happily ship the previous
> binary. `strings dist/stage/amiagent/amiagent | grep '^amiagent 0\.'` — or
> ask the running agent for its version once it is installed.

## 2. Icons

`tools/mkicon/mkicon.py` writes them. The `amiagent` tool icon carries the
tool types a Workbench user configures it with, all shipped inactive (the
Workbench convention: wrapped in parentheses), plus a stack:

```sh
python3 tools/mkicon/mkicon.py dist/stage/amiagent/amiagent.info tool \
    --stack 16384 \
    --tt "(TOKEN=put-your-secret-here)" \
    --tt "(PORT=7846)" \
    --tt "(VERBOSE)" \
    --tt "(DONOTWAIT)"
```

## 3. Documents in the archive

- `dist/stage/amiagent/README.txt` — the user-facing one; add a "New in x.y.z"
  section at the top.
- `dist/stage/amiagent/PROTOCOL.md` — copy of the repo's, if it changed.

## 4. Pack

`tools/lhapack` builds the `.lha` (macOS has no LHA *writer* — Homebrew's `lha`
is Lhasa, extract-only). Result goes to `dist/amiagent-x.y.z.lha`.

## 5. Verify on hardware

Install the archive on a real machine and start the agent from it. Both ways:
from a Shell, and by double-clicking the icon. See the fleet in the vault note.

## 6. Publish

### GitHub

Release with the `.lha` attached; update the download link in `README.md`.

### amipkg

Update the catalog entry so `amipkg install amiagent` gets the new version.

### Aminet

Upload `dist/amiagent-x.y.z.lha` and `dist/amiagent-x.y.z.readme` to
`aminet.net/upload`.

> **Set the replace.** The `.readme` must carry a `Replaces:` line naming the
> *previous* upload's full Aminet path, and the upload form's replace field
> must name it too:
>
> ```
> Replaces:     comm/net/amiagent-<previous version>.lha
> ```
>
> Without it Aminet keeps every version ever uploaded side by side instead of
> only the current one, and users downloading "amiagent" get whichever one they
> happened to click. Reported by djbase, 2026-08-10.

The other `.readme` header fields, for reference:

```
Short:        Lets another machine control your Amiga
Uploader:     thomas@amiga-imager.com (Thomas Luebker)
Author:       thomas@amiga-imager.com (Thomas Luebker)
Type:         comm/net
Version:      x.y.z
Replaces:     comm/net/amiagent-<previous>.lha
Architecture: m68k-amigaos >= 2.0
Distribution: Aminet
URL:          https://amiga-imager.com
```

Check afterwards that `aminet.net/search?query=amiagent` lists exactly one
archive.

## 7. Write it down

- Vault note `20 - Private/Retro Computing/Projects/amimcp.md` — `## Status`,
  `## Next Actions`, `updated:`.
- `/Volumes/AmigaShare/amimcp` — copy the new build there straight away; that
  share is how the fleet picks builds up.

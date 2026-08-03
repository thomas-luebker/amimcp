# lhapack

Builds the release archive. macOS has no LHA *writer* — Homebrew's `lha` is
Lhasa, extract-only — so this drives `AmigaDiskKit`'s `LHAWriter`, which
produces headers a real Amiga can read.

    swift build -c release
    .build/release/lhapack ../../dist/amiagent-X.Y.Z.lha \
        license README.txt amiagent amiagent.020

Files are added in the order given and land flat in the archive (no leading
directory), which is what the amipkg recipe's `placeFile` ops expect.
Verify afterwards with `lha l <archive>` and by hashing the extracted binaries
against the ones you built — a release nobody can unpack is worse than none.

#!/usr/bin/env python3
"""Build AmigaOS .info icons for the amiagent archive.

Three roles, three looks - the first version got this wrong by putting
app-style artwork on the drawer and shipping nothing for the files inside, so
the folder looked like an application and opening it showed an empty window:

    drawer   a plain Workbench drawer. A folder should look like a folder.
    tool     the amiagent application icon - a screen with signal bars.
    project  a document, for README.txt and license, with a DefaultTool so
             double-clicking actually opens them.

Binary layout (this order matters - DrawerData is FIRST, not last):

    DiskObject   78 bytes
    DrawerData   56 bytes    drawers only
    Image1       20 + planar
    Image2       20 + planar
    DefaultTool  ULONG len + NUL-terminated string, when set

Icons are 57x14 at depth 2, matching the drawer icons already in SYS:Programs
so they sit on the same grid. Colours are the standard Workbench four:
0 grey, 1 black, 2 white, 3 blue.

After the classic icon a GlowIcon appendix follows: an IFF FORM ICON with a
FACE chunk and two RLE-compressed IMAG chunks (normal + selected), the OS3.5
ColorIcon format. icon.library v44+ (OS 3.5/3.9/3.2, PeterK's replacement)
renders that instead of the planar images - same artwork, but with a real
transparent background and a glow halo on the selected state, so the icon
sits on a patterned Workbench like the system's own GlowIcons do. Old
icon.library stops reading after the classic data and never sees it.

    FORM <size> ICON
      FACE  6 bytes   w-1, h-1, flags (bit0 frameless), aspect, maxpalbytes-1
      IMAG  10 + data transp, ncols-1, flags (1 transp | 2 palette),
                      imgfmt (1 RLE), palfmt (0 raw), depth,
                      imgbytes-1, palbytes-1, image, palette
      IMAG  ...       the selected image

The RLE is ILBM ByteRun1 over a continuous BIT stream: 8-bit control codes
(n<128: copy n+1 items, n>128: repeat next item 257-n times), items depth
bits wide, no alignment until the final byte is zero-padded.
"""

import struct
import sys

W, H, DEPTH = 57, 14, 2
BG, BLACK, WHITE, BLUE = 0, 1, 2, 3
NOPOS = -2147483648


def blank():
    return [[BG] * W for _ in range(H)]


def rect(g, x0, y0, x1, y1, c):
    for y in range(max(0, y0), min(H, y1 + 1)):
        for x in range(max(0, x0), min(W, x1 + 1)):
            g[y][x] = c


def frame(g, x0, y0, x1, y1, c):
    for x in range(max(0, x0), min(W, x1 + 1)):
        if 0 <= y0 < H: g[y0][x] = c
        if 0 <= y1 < H: g[y1][x] = c
    for y in range(max(0, y0), min(H, y1 + 1)):
        if 0 <= x0 < W: g[y][x0] = c
        if 0 <= x1 < W: g[y][x1] = c


def art_drawer(sel):
    """A plain drawer. Deliberately unremarkable - it is a folder."""
    g = blank()
    body, edge = (BLUE, WHITE) if not sel else (WHITE, BLUE)
    rect(g, 6, 1, 14, 2, BLACK)                  # tab
    rect(g, 7, 2, 13, 2, body)
    frame(g, 5, 3, 51, 12, BLACK)                # body
    rect(g, 6, 4, 50, 11, body)
    for x in range(6, 51): g[4][x] = edge        # top highlight
    for y in range(4, 12): g[6][y - y + 6] = g[y][6]
    for y in range(4, 12): g[y][6] = edge        # left highlight
    return g


def art_tool(sel):
    """The application: a screen with signal bars - it is reached over the net."""
    g = blank()
    body, edge = (BLUE, WHITE) if not sel else (WHITE, BLUE)
    frame(g, 2, 2, 30, 11, BLACK)                # screen
    rect(g, 3, 3, 29, 10, body)
    for x in range(4, 29): g[4][x] = edge        # scanline highlight
    rect(g, 12, 12, 20, 12, BLACK)               # stand
    for i, (x, top) in enumerate(((36, 9), (42, 6), (48, 3))):
        frame(g, x, top, x + 3, 12, BLACK)
        rect(g, x + 1, top + 1, x + 2, 11, body)
    return g


def art_monitor(sel):
    """amimon: a monitor screen showing a live readout - rising bars inside it.
    Distinct from the tool icon (signal bars beside a screen) so the watcher and
    the agent do not look identical on the same grid."""
    g = blank()
    body, edge = (BLUE, WHITE) if not sel else (WHITE, BLUE)
    frame(g, 1, 0, 41, 10, BLACK)                # bezel
    rect(g, 2, 1, 40, 9, body)                   # screen face
    for x in range(3, 40): g[1][x] = edge        # top scanline highlight
    # a bar-graph readout climbing across the screen - it shows status
    for x, top in ((6, 7), (13, 6), (20, 5), (27, 4), (34, 3)):
        rect(g, x, top, x + 3, 8, edge)
        frame(g, x, top, x + 3, 8, BLACK)
    rect(g, 19, 11, 23, 11, BLACK)               # neck
    frame(g, 12, 12, 30, 13, BLACK)              # base
    rect(g, 13, 13, 29, 13, body)
    return g


def art_project(sel):
    """A document: page with a folded corner and lines of text."""
    g = blank()
    page, ink = (WHITE, BLUE) if not sel else (BLUE, WHITE)
    frame(g, 16, 0, 40, 13, BLACK)
    rect(g, 17, 1, 39, 12, page)
    # text lines, ragged right like real text
    for y, x1 in ((3, 36), (5, 33), (7, 37), (9, 30), (11, 34)):
        rect(g, 19, y, x1, y, ink)
    # folded top-right corner: blank the triangle, then outline the fold
    for i in range(6):
        for x in range(40 - i, 40):
            g[i][x] = BG
        if 0 <= i < H:
            g[i][40 - i - 1] = BLACK
    return g


# ---- GlowIcon appendix ----------------------------------------------------

# Indices into GLOW_PAL. 0 must stay the transparent colour.
G_T, G_BLK, G_DARK, G_MID, G_LIGHT, G_WHITE, G_GLOW = range(7)

GLOW_PAL = [
    (0xff, 0x00, 0xff),   # 0: transparent, never drawn
    (0x10, 0x10, 0x10),   # 1: outline
    (0x28, 0x48, 0x88),   # 2: body, bottom shade
    (0x3a, 0x6a, 0xb8),   # 3: body, middle
    (0x6a, 0x9a, 0xe0),   # 4: body, top shade
    (0xf0, 0xf4, 0xff),   # 5: highlight / page white
    (0xff, 0xd8, 0x50),   # 6: selection halo
]


def glow_normal(g):
    """The classic art in GlowIcon colours: grey becomes transparent, the flat
    blue body a three-band vertical gradient. Same drawing, better dress."""
    out = [[G_T] * W for _ in range(H)]
    for y in range(H):
        body = G_LIGHT if y < 5 else (G_MID if y < 10 else G_DARK)
        for x in range(W):
            c = g[y][x]
            if c == BLACK:   out[y][x] = G_BLK
            elif c == WHITE: out[y][x] = G_WHITE
            elif c == BLUE:  out[y][x] = body
    return out


def glow_selected(gn):
    """Selected state, the GlowIcons way: the same image with a halo - every
    transparent pixel touching the artwork lights up."""
    out = [row[:] for row in gn]
    for y in range(H):
        for x in range(W):
            if gn[y][x] != G_T:
                continue
            near = any(gn[y + dy][x + dx] != G_T
                       for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                       if 0 <= y + dy < H and 0 <= x + dx < W)
            if near:
                out[y][x] = G_GLOW
    return out


def rle_pack(values, depth):
    """ByteRun1 over a bit stream: 8-bit controls, depth-bit items."""
    stream = []                       # (value, bit width)
    i, n = 0, len(values)
    while i < n:
        run = 1
        while i + run < n and values[i + run] == values[i] and run < 128:
            run += 1
        if run >= 2:
            stream.append((257 - run, 8))
            stream.append((values[i], depth))
            i += run
        else:
            lits = []
            while i < n and len(lits) < 128:
                if i + 1 < n and values[i + 1] == values[i]:
                    break
                lits.append(values[i])
                i += 1
            stream.append((len(lits) - 1, 8))
            stream += [(v, depth) for v in lits]
    buf, acc, nbits = bytearray(), 0, 0
    for v, w in stream:
        acc = (acc << w) | (v & ((1 << w) - 1))
        nbits += w
        while nbits >= 8:
            nbits -= 8
            buf.append((acc >> nbits) & 0xFF)
    if nbits:
        buf.append((acc << (8 - nbits)) & 0xFF)
    return bytes(buf)


def imag_chunk(grid, pal):
    depth = max(1, (len(pal) - 1).bit_length())
    img = rle_pack([p for row in grid for p in row], depth)
    palbytes = bytes(c for rgb in pal for c in rgb)
    body = struct.pack(">BBBBBBHH",
                       0,               # transparent colour index
                       len(pal) - 1,    # palette entries - 1
                       0x01 | 0x02,     # has transparency + has palette
                       1, 0,            # image RLE, palette raw
                       depth,
                       len(img) - 1, len(palbytes) - 1)
    body += img + palbytes
    return b"IMAG" + struct.pack(">I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def glow_form(art):
    normal = glow_normal(art(False))
    face = struct.pack(">BBBBH", W - 1, H - 1,
                       1,               # frameless: transparency, no emboss box
                       0x11,            # 1:1 pixel aspect
                       len(GLOW_PAL) * 3 - 1)
    body = (b"ICON"
            + b"FACE" + struct.pack(">I", len(face)) + face
            + imag_chunk(normal, GLOW_PAL)
            + imag_chunk(glow_selected(normal), GLOW_PAL))
    return b"FORM" + struct.pack(">I", len(body)) + body


def planar(g):
    words = (W + 15) // 16
    out = bytearray()
    for plane in range(DEPTH):
        for y in range(H):
            bits = 0
            for x in range(W):
                if (g[y][x] >> plane) & 1:
                    bits |= 1 << (words * 16 - 1 - x)
            out += bits.to_bytes(words * 2, "big")
    return bytes(out)


def image_header():
    return struct.pack(">hhhhhIBBI", 0, 0, W, H, DEPTH, 1, 0x03, 0x00, 0)


def build(kind, default_tool=None, x=NOPOS, y=NOPOS):
    art = {"drawer": art_drawer, "tool": art_tool, "monitor": art_monitor,
           "project": art_project}[kind]
    do_type = {"drawer": 2, "tool": 3, "monitor": 3, "project": 4}[kind]

    gadget = struct.pack(">IhhhhHHH", 0, 0, 0, W, H, 0x0006, 0x0001, 0x0001)
    gadget += struct.pack(">IIIIIHI", 1, 1, 0, 0, 0, 0, 0)
    gadget = gadget[:44]

    do = struct.pack(">HH", 0xE310, 1) + gadget
    do += bytes([do_type, 0])
    do += struct.pack(">II", 1 if default_tool else 0, 0)      # DefaultTool, ToolTypes
    do += struct.pack(">ii", x, y)
    do += struct.pack(">III", 1 if kind == "drawer" else 0, 0, 0)  # DrawerData, ToolWindow, Stack
    assert len(do) == 78, len(do)

    out = bytearray(do)
    if kind == "drawer":
        nw = struct.pack(">hhhhBBIIIIIIhhhhH", 60, 50, 320, 130, 0xFF, 0xFF,
                         0, 0, 0, 0, 0, 0, 90, 40, 640, 200, 1)
        out += (nw[:48] + struct.pack(">ii", 0, 0) + b"\0" * 56)[:56]
    out += image_header() + planar(art(False))
    out += image_header() + planar(art(True))
    if default_tool:
        s = default_tool.encode("latin-1") + b"\0"
        if len(s) & 1:
            s += b"\0"   # keep the GlowIcon FORM below on an even offset
        out += struct.pack(">I", len(s)) + s
    out += glow_form(art)
    return bytes(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: mkicon.py <out.info> <drawer|tool|monitor|project> [defaultTool]")
        raise SystemExit(2)
    path, kind = sys.argv[1], sys.argv[2]
    tool = sys.argv[3] if len(sys.argv) > 3 else None
    data = build(kind, tool)
    open(path, "wb").write(data)
    print(f"wrote {path}: {len(data)} bytes ({kind}"
          + (f", DefaultTool={tool}" if tool else "") + ")")

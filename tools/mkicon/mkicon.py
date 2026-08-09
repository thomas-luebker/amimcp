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
        out += struct.pack(">I", len(s)) + s
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

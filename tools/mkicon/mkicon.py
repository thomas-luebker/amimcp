#!/usr/bin/env python3
"""Build an AmigaOS drawer icon (.info) for amiagent, from scratch.

Format is the classic DiskObject: 78-byte DiskObject, 56-byte DrawerData
(drawers only), then two Images (20-byte header + planar data each) for the
normal and selected states.

Sized 57x14 at depth 2 to match the drawer icons already in SYS:Programs, so
it sits on the same grid rather than looking like a transplant.

Colours are the standard Workbench 3.x four:
    0 = grey (background)   1 = black (outline)
    2 = white (highlight)   3 = blue (fill)
"""

import struct

W, H, DEPTH = 57, 14, 2
BG, BLACK, WHITE, BLUE = 0, 1, 2, 3


def blank():
    return [[BG] * W for _ in range(H)]


def rect(g, x0, y0, x1, y1, c):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if 0 <= x < W and 0 <= y < H:
                g[y][x] = c


def frame(g, x0, y0, x1, y1, c):
    for x in range(x0, x1 + 1):
        g[y0][x] = c; g[y1][x] = c
    for y in range(y0, y1 + 1):
        g[y][x0] = c; g[y][x1] = c


def draw(selected: bool):
    """A drawer with a signal meter beside it: storage you can reach remotely."""
    g = blank()
    body, edge = (BLUE, WHITE) if not selected else (WHITE, BLUE)

    # Drawer tab (the little lip on top-left)
    rect(g, 3, 1, 9, 2, BLACK)
    rect(g, 4, 2, 8, 2, body)

    # Drawer body
    frame(g, 2, 3, 26, 12, BLACK)
    rect(g, 3, 4, 25, 11, body)
    # highlight along the top and left inner edge - gives it depth at 14px
    for x in range(3, 26):
        g[4][x] = edge
    for y in range(4, 12):
        g[y][3] = edge

    # Signal meter: three bars, rising. Reads as "reachable" at this size.
    for i, (x, top) in enumerate(((32, 9), (38, 6), (44, 3))):
        frame(g, x, top, x + 3, 12, BLACK)
        rect(g, x + 1, top + 1, x + 2, 11, body)

    return g


def planar(g):
    """Grid -> interleaved bitplanes, each row padded to whole 16-bit words."""
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


def image(data_ptr):
    # LeftEdge, TopEdge, Width, Height, Depth, ImageData, PlanePick, PlaneOnOff, NextImage
    return struct.pack(">hhhhhIBBI", 0, 0, W, H, DEPTH, data_ptr, 0x03, 0x00, 0)


def build(current_x=-1, current_y=-1):
    normal, select = planar(draw(False)), planar(draw(True))

    gadget = struct.pack(
        ">IhhhhHHH", 0, 0, 0, W, H,
        0x0006,          # GADGIMAGE | GADGHIMAGE - two-image gadget
        0x0001,          # RELVERIFY
        0x0001,          # BOOLGADGET
    )
    gadget += struct.pack(">IIIIIHI", 1, 1, 0, 0, 0, 0, 0)   # render/select/text/... placeholders
    gadget = gadget[:44]

    NOPOS = -2147483648
    do = struct.pack(">HH", 0xE310, 1) + gadget
    do += bytes([2, 0])                                   # do_Type = drawer, pad
    do += struct.pack(">II", 0, 0)                        # DefaultTool, ToolTypes
    do += struct.pack(">ii", current_x if current_x >= 0 else NOPOS,
                            current_y if current_y >= 0 else NOPOS)
    do += struct.pack(">III", 1, 0, 0)                    # DrawerData ptr, ToolWindow, StackSize
    assert len(do) == 78, len(do)

    # DrawerData: 48-byte NewWindow + dd_CurrentX/Y. The window the drawer opens
    # to; modest and on-screen for any Workbench.
    nw = struct.pack(">hhhhBBIIIIIIhhhhH",
                     40, 40, 300, 120, 0xFF, 0xFF,
                     0, 0, 0, 0, 0, 0,
                     90, 40, 640, 200, 1)
    dd = nw[:48] + struct.pack(">ii", 0, 0)
    dd = (dd + b"\0" * 56)[:56]

    return (do + dd
            + image(1) + normal
            + image(1) + select
            + struct.pack(">IIH", 4, 1, 1))   # trailing block, as real icons carry


if __name__ == "__main__":
    import sys
    x = int(sys.argv[2]) if len(sys.argv) > 2 else -1
    y = int(sys.argv[3]) if len(sys.argv) > 3 else -1
    data = build(x, y)
    open(sys.argv[1], "wb").write(data)
    print(f"wrote {sys.argv[1]}: {len(data)} bytes  ({W}x{H} depth {DEPTH}, pos {x},{y})")

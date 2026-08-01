"""Minimal PNG encoder — stdlib zlib and struct only.

The Amiga sends raw pixels; the compression happens here. Two colour types are
enough for what a classic Amiga can put on a screen: indexed (type 3) for
planar screens up to 8 bitplanes, and truecolour (type 2) for RTG.
"""

from __future__ import annotations

import struct
import zlib

_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _encode(width: int, height: int, bit_depth: int, color_type: int,
            scanlines: bytes, extra: bytes = b"") -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    return (
        _SIG
        + _chunk(b"IHDR", ihdr)
        + extra
        + _chunk(b"IDAT", zlib.compress(scanlines, 9))
        + _chunk(b"IEND", b"")
    )


def indexed_png(width: int, height: int, palette: list[tuple[int, int, int]],
                pixels: bytes) -> bytes:
    """One byte per pixel, each an index into `palette`."""
    if len(pixels) != width * height:
        raise ValueError(f"expected {width * height} pixel bytes, got {len(pixels)}")

    # PNG scanlines each carry a leading filter-type byte; 0 means "none",
    # which is right here since zlib already finds the row-to-row redundancy
    # in the flat colour areas an Amiga screen is mostly made of.
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += pixels[y * width : (y + 1) * width]

    plte = bytearray()
    for r, g, b in palette:
        plte += bytes((r, g, b))

    return _encode(width, height, 8, 3, bytes(rows), _chunk(b"PLTE", bytes(plte)))


def rgb_png(width: int, height: int, pixels: bytes) -> bytes:
    """Three bytes per pixel, R,G,B."""
    if len(pixels) != width * height * 3:
        raise ValueError(f"expected {width * height * 3} pixel bytes, got {len(pixels)}")

    stride = width * 3
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += pixels[y * stride : (y + 1) * stride]

    return _encode(width, height, 8, 2, bytes(rows))


def screenshot_png(shot) -> bytes:
    """Encode an amiga.Screenshot."""
    if shot.palette is None:
        return rgb_png(shot.width, shot.height, shot.pixels)
    return indexed_png(shot.width, shot.height, shot.palette, shot.pixels)

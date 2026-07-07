"""Dependency-free test-image generation for the Vision (VL) test.

Builds small PNGs at runtime (pure Python + stdlib zlib — no Pillow) with KNOWN
content — solid colours, a tiny bitmap-font word/number, and a row of squares —
so a vision-language model's answer can be checked against ground truth. Every
generator returns a ``data:image/png;base64,…`` URI ready to drop into an
OpenAI ``image_url`` message.
"""
from __future__ import annotations

import base64
import struct
import zlib

# ---- named colours (RGB) ----
COLORS = {
    "red": (220, 40, 40), "green": (40, 170, 60), "blue": (40, 90, 210),
    "yellow": (235, 205, 40), "black": (20, 20, 20), "white": (245, 245, 245),
}

# ---- 5×7 bitmap font: only the glyphs the vision probes actually render ----
_FONT = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png_bytes(w: int, h: int, buf: bytes) -> bytes:
    """Encode a w×h 8-bit RGB image (`buf` = w*h*3 bytes) as a PNG."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit, colour-type 2 (RGB)
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)                                     # filter byte: none
        raw.extend(buf[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


class _Canvas:
    def __init__(self, w: int, h: int, bg=(255, 255, 255)):
        self.w, self.h = w, h
        self.buf = bytearray(bytes(bg) * (w * h))

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, color) -> None:
        c = bytes(color)
        for y in range(max(0, y0), min(self.h, y1)):
            base = y * self.w
            for x in range(max(0, x0), min(self.w, x1)):
                i = (base + x) * 3
                self.buf[i:i + 3] = c

    def data_uri(self) -> str:
        png = _png_bytes(self.w, self.h, bytes(self.buf))
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def solid(color, size: int = 96) -> str:
    """A solid-colour square. `color` is a name (see COLORS) or an RGB tuple."""
    rgb = COLORS.get(color, color) if isinstance(color, str) else color
    return _Canvas(size, size, rgb).data_uri()


def text_image(text: str, *, fg="black", bg="white", scale: int = 10,
               pad: int = 16) -> str:
    """Render `text` (chars in _FONT) as a big blocky bitmap-font image."""
    fg_c = COLORS.get(fg, fg) if isinstance(fg, str) else fg
    bg_c = COLORS.get(bg, bg) if isinstance(bg, str) else bg
    text = text.upper()
    gap = scale                                           # inter-glyph gap
    w = pad * 2 + len(text) * (5 * scale) + max(0, len(text) - 1) * gap
    h = pad * 2 + 7 * scale
    cv = _Canvas(w, h, bg_c)
    x = pad
    for ch in text:
        glyph = _FONT.get(ch, _FONT[" "])
        for ry, rowbits in enumerate(glyph):
            for cx, bit in enumerate(rowbits):
                if bit == "1":
                    px = x + cx * scale
                    py = pad + ry * scale
                    cv.fill_rect(px, py, px + scale, py + scale, fg_c)
        x += 5 * scale + gap
    return cv.data_uri()


def squares(n: int, *, color="red", bg="white", box: int = 40, gap: int = 20,
            pad: int = 20) -> str:
    """A single row of `n` solid squares — ground truth for a counting probe."""
    fg_c = COLORS.get(color, color) if isinstance(color, str) else color
    bg_c = COLORS.get(bg, bg) if isinstance(bg, str) else bg
    w = pad * 2 + n * box + max(0, n - 1) * gap
    h = pad * 2 + box
    cv = _Canvas(w, h, bg_c)
    x = pad
    for _ in range(n):
        cv.fill_rect(x, pad, x + box, pad + box, fg_c)
        x += box + gap
    return cv.data_uri()

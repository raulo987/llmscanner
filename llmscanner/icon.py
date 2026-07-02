"""Generate the app icon — a blue 'V' on a transparent background.

Pure-Python (zlib + struct), no Pillow dependency. Renders with supersampled
anti-aliasing and writes a real PNG to llmscanner/assets/icon.png.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

BLUE = (37, 99, 235)  # Tailwind blue-600


def _dist_point_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def render_v_rgba(size: int = 128, sub: int = 4, color=BLUE) -> bytes:
    """Return raw RGBA bytes (size*size*4) of a 'V' glyph with AA edges."""
    lt = (0.18 * size, 0.18 * size)   # left top
    bt = (0.50 * size, 0.82 * size)   # bottom tip
    rt = (0.82 * size, 0.18 * size)   # right top
    half = 0.085 * size               # half stroke width (rounded caps)
    r, g, b = color
    out = bytearray(size * size * 4)
    inv = 1.0 / sub
    samples = sub * sub
    for y in range(size):
        for x in range(size):
            cov = 0
            for sy in range(sub):
                py = y + (sy + 0.5) * inv
                for sx in range(sub):
                    px = x + (sx + 0.5) * inv
                    d = min(_dist_point_seg(px, py, lt[0], lt[1], bt[0], bt[1]),
                            _dist_point_seg(px, py, bt[0], bt[1], rt[0], rt[1]))
                    if d <= half:
                        cov += 1
            i = (y * size + x) * 4
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = (255 * cov) // samples
    return bytes(out)


def _png(rgba: bytes, w: int, h: int) -> bytes:
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0 (none)
        raw.extend(rgba[y * stride:(y + 1) * stride])

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit, RGBA
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def png_bytes(size: int = 128) -> bytes:
    return _png(render_v_rgba(size), size, size)


def asset_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "icon.png"


def write_icon(path: Path | None = None, size: int = 128) -> Path:
    path = Path(path) if path else asset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(size))
    return path


def ensure_icon() -> Path:
    """Return the icon path, generating it on first use if missing."""
    p = asset_path()
    if not p.exists():
        write_icon(p)
    return p


if __name__ == "__main__":
    print("wrote", write_icon())

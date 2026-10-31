"""Render the SafarSaathi bus logo (public/favicon.svg) into PNG app icons, without third-party packages.

    python tools/make_icons.py

Icons are full-bleed squares so the same file works as a normal and a "maskable" (Android adaptive) icon;
the bus is drawn inside the central safe zone.
"""
import struct
import zlib
from pathlib import Path

NAVY, AMBER = (0x1E, 0x3A, 0x8A), (0xFB, 0xBF, 0x24)
OUT = Path(__file__).resolve().parent.parent / "frontend" / "web" / "public"

# Shapes in the 64x64 favicon coordinate space, shrunk toward the centre to sit in the maskable safe zone.
RECTS = [(14, 14, 36, 30, 6, AMBER), (18, 19, 12, 10, 2, NAVY), (34, 19, 12, 10, 2, NAVY)]
CIRCLES = [(22, 48, 4, AMBER), (42, 48, 4, AMBER)]
SCALE = 0.8


def in_round_rect(x, y, rx, ry, w, h, r):
    if not (rx <= x < rx + w and ry <= y < ry + h):
        return False
    cx = min(max(x, rx + r), rx + w - r)
    cy = min(max(y, ry + r), ry + h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def pixel(u, v):
    # u, v in favicon units; undo the centre shrink.
    x, y = 32 + (u - 32) / SCALE, 34 + (v - 34) / SCALE
    color = NAVY
    for rx, ry, w, h, r, c in RECTS:
        if in_round_rect(x, y, rx, ry, w, h, r):
            color = c
    for cx, cy, r, c in CIRCLES:
        if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
            color = c
    return color


def render(size: int) -> bytes:
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            # 2x2 supersampling for smoother edges
            acc = [0, 0, 0]
            for dx in (0.25, 0.75):
                for dy in (0.25, 0.75):
                    c = pixel((px + dx) * 64 / size, (py + dy) * 64 / size)
                    acc = [a + b for a, b in zip(acc, c)]
            row += bytes(a // 4 for a in acc)
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    for size in (192, 512):
        (OUT / f"icon-{size}.png").write_bytes(render(size))
        print(f"wrote icon-{size}.png")
    (OUT / "apple-touch-icon.png").write_bytes(render(180))
    print("wrote apple-touch-icon.png")

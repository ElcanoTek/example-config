"""The report-email header logo must stand out against the header band.

The logo (assets/email-logo.png) sits on the theme's dark header band. A mark
in the brand's own color can vanish there: a red mark on a red band, a blue
mark on a blue band. Nothing in the render or the lint catches that, and it
only shows in a real inbox, so it is measured here instead.

The check uses WCAG's 3:1 for graphics: at least a quarter of the logo's
opaque pixels must reach 3:1 against the band. A share, not an average,
because a mark may sit on an opaque tile in the band's own color (a black
square on a black band), which is fine as long as the mark itself reads; a
mark in the band's color scores close to zero. The PNG is decoded with the
standard library (no Pillow), so the test runs wherever the suite does.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import yaml

BUNDLE_ROOT = Path(__file__).resolve().parents[2]
LOGO_PATH = BUNDLE_ROOT / "assets" / "email-logo.png"
EMAIL_STYLE_PATH = BUNDLE_ROOT / "protocols" / "email-style.yaml"
# The canonical template's header band, before the theme recolors it.
CANONICAL_BAND = "#1A0B1E"
MIN_CONTRAST = 3.0
# Share of opaque pixels that must reach MIN_CONTRAST. Measured 2026-10-22:
# every client logo scores 0.38 or more (the lowest is a mark on an opaque tile
# matching its band), and a red mark on a red band or a blue mark on a blue
# band scores 0.00.
MIN_SHARE = 0.25


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png_rgba(path: Path) -> list[tuple[int, int, int, int]]:
    """Every pixel of an 8-bit, non-interlaced PNG as (r, g, b, a)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    pos, idat, palette, trns = 8, b"", None, None
    width = height = depth = color = interlace = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i : i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
    assert depth == 8 and interlace == 0, f"{path.name}: save it as an 8-bit, non-interlaced PNG"
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = width * channels
    raw = zlib.decompress(idat)
    rows: list[bytearray] = []
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif kind == 4:
                line[i] = (line[i] + _paeth(left, up, corner)) & 0xFF
        rows.append(line)
        previous = line
    pixels = []
    for line in rows:
        for x in range(width):
            px = line[x * channels : (x + 1) * channels]
            if color == 6:
                pixels.append(tuple(px))
            elif color == 2:
                pixels.append((*px, 255))
            elif color == 4:
                pixels.append((px[0], px[0], px[0], px[1]))
            elif color == 0:
                pixels.append((px[0], px[0], px[0], 255))
            else:
                alpha = trns[px[0]] if trns is not None and px[0] < len(trns) else 255
                pixels.append((*palette[px[0]], alpha))
    return pixels


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def header_band() -> tuple[int, int, int]:
    """The default theme's header band color (its override of the canonical band)."""
    protocol = yaml.safe_load(EMAIL_STYLE_PATH.read_text(encoding="utf-8"))
    theme_file = BUNDLE_ROOT / protocol["themes"]["dir"] / f"{protocol['themes']['default']}.yaml"
    overrides = yaml.safe_load(theme_file.read_text(encoding="utf-8")).get("color_overrides") or {}
    band = {str(k).upper(): str(v) for k, v in overrides.items()}.get(CANONICAL_BAND, CANONICAL_BAND)
    return tuple(int(band.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))


def readable_share(path: Path, band: tuple[int, int, int]) -> float:
    """The share of the logo's opaque pixels that reach MIN_CONTRAST on the band."""
    opaque = [px[:3] for px in read_png_rgba(path) if px[3] >= 128]
    assert opaque, f"{path.name} has no opaque pixels"
    return sum(contrast(px, band) >= MIN_CONTRAST for px in opaque) / len(opaque)


def test_email_logo_stands_out_on_the_header_band() -> None:
    band = header_band()
    share = readable_share(LOGO_PATH, band)
    assert share >= MIN_SHARE, (
        f"assets/email-logo.png barely shows on the header band #{''.join(f'{c:02X}' for c in band)}: "
        f"only {share:.0%} of it reaches {MIN_CONTRAST}:1 (needs {MIN_SHARE:.0%}). "
        "Use a light (or white) version of the mark, as for a dark background."
    )


def test_contrast_rule_rejects_a_mark_in_the_band_color(tmp_path: Path) -> None:
    # A mark drawn in the band's own color is the failure this guards against.
    band = header_band()
    width = height = 4

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + bytes((*band, 255)) * width for _ in range(height))
    same = tmp_path / "same.png"
    same.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    assert readable_share(same, band) < MIN_SHARE

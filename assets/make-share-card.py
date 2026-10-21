"""Generate the 1280x640 link-unfurl card named by manifest.yaml's share_image.

Reads its copy, colors and mark from manifest.yaml's `branding:` block
(app_name, login_tagline, colors.dark, logo, share_image) so the card and the
app cannot drift. Run it again after changing any of them:

    python3 assets/make-share-card.py --host fleet.<client domain> [--font-dir DIR] [--mark PNG]

The mark is `branding.logo`. A PNG/WebP/JPEG is read directly; an SVG is
rasterized with `rsvg-convert` (librsvg) when it is installed, or pass a raster
copy with --mark. Fonts: the first of the client's own font (--font-dir, which
must hold <Family>-Bold.ttf and <Family>-Regular.ttf; pass --font to name the
family), Liberation Sans or DejaVu Sans found on the system.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280, 640


def font(dirs: list[Path], names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for d in dirs:
        for n in names:
            p = d / n
            if p.is_file():
                return ImageFont.truetype(str(p), size)
    raise SystemExit(f"no usable font among {names} in {[str(d) for d in dirs]}")


def fitted(d: ImageDraw.ImageDraw, text: str, dirs: list[Path], names: list[str], size: int, width: int):
    """The largest font at or below `size` (down to 60% of it) that fits `text`
    in `width`; past that, the text is shortened with an ellipsis."""
    for s in range(size, int(size * 0.6) - 1, -2):
        f = font(dirs, names, s)
        if d.textlength(text, font=f) <= width:
            return text, f
    while text and d.textlength(text + "…", font=f) > width:
        text = text[:-1].rstrip()
    return text + "…", f


def load_mark(path: Path, size: int) -> Image.Image:
    if path.suffix.lower() == ".svg":
        tool = shutil.which("rsvg-convert")
        if not tool:
            raise SystemExit(f"{path} is an SVG: install librsvg (rsvg-convert) or pass --mark <png>")
        png = subprocess.run(
            [tool, "-w", str(size), "-h", str(size), "--keep-aspect-ratio", str(path)],
            check=True,
            capture_output=True,
        ).stdout
        img = Image.open(io.BytesIO(png))
    else:
        img = Image.open(path)
    img = img.convert("RGBA")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="the deployment's fleet host, e.g. fleet.acmevic.com")
    ap.add_argument("--font-dir", type=Path)
    ap.add_argument("--font", default="", help="font family file prefix in --font-dir, e.g. Montserrat")
    ap.add_argument("--mark", type=Path, help="raster copy of the mark (default: branding.logo)")
    args = ap.parse_args()

    b = yaml.safe_load((ROOT / "manifest.yaml").read_text(encoding="utf-8"))["branding"]
    if not b.get("share_image"):
        raise SystemExit("manifest.yaml branding.share_image is not set")
    dark = b["colors"]["dark"]
    ground, rule, tag, muted = dark["background"], dark["accent"], dark["primary"], dark["text_secondary"]

    dirs = [d for d in (args.font_dir,) if d] + [
        Path("/usr/share/fonts/liberation"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/dejavu-sans-fonts"),
        Path("/usr/share/fonts/truetype/dejavu"),
    ]
    own = [f"{args.font}-Bold.ttf", f"{args.font}-Regular.ttf"] if args.font else []
    bold = own[:1] + ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
    reg = own[1:] + ["LiberationSans-Regular.ttf", "DejaVuSans.ttf"]

    img = Image.new("RGB", (W, H), ground)
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 16, W, H], fill=rule)

    mark_path = args.mark or (ROOT / b["logo"] if b.get("logo") else None)
    if mark_path:
        mark = load_mark(mark_path, 176)
        img.paste(mark, (96, 104 + (176 - mark.height) // 2), mark)

    width = W - 2 * 96
    name, f = fitted(d, b["app_name"], dirs, bold, 96, width)
    d.text((96, 320), name, font=f, fill="#FFFFFF")
    tagline, f = fitted(d, b["login_tagline"].split(".")[0] + ".", dirs, bold, 40, width)
    d.text((96, 436), tagline, font=f, fill=tag)
    host, f = fitted(d, args.host, dirs, reg, 30, width)
    d.text((96, 540), host, font=f, fill=muted)

    out = ROOT / b["share_image"]
    img.save(out, optimize=True)
    print(f"wrote {out.relative_to(ROOT)} ({W}x{H})")


if __name__ == "__main__":
    main()

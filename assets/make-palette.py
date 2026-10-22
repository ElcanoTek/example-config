"""Build this bundle's colors from a client's brand hexes, or check them.

One run writes the three places a bundle carries color, so they stay one
system:

* ``manifest.yaml`` ``branding.colors``: all 19 tokens fleet themes, light and
  dark, with each pair's WCAG 2.x contrast ratio in a comment;
* ``protocols/email_styles/themes/<slug>.yaml``: the hex find-replace theme for
  report emails, which becomes ``themes.default`` in
  ``protocols/email-style.yaml``;
* ``chart_colors`` in ``protocols/email-style.yaml``, for chart PNGs, which the
  email theme never reaches.

Usage:

    python3 assets/make-palette.py --slug acme --primary "#B3122A" \\
        [--secondary "#4A5560"] [--accent "#E8203A"] [--ink "#26221F"] \\
        [--paper "#FBF8F6"] [--dark-background "#0C0A0B"] [--write]
    python3 assets/make-palette.py --check

Without ``--write`` it prints what it would write. A brand color that is too
light or too dark for its role is moved along its own hue (lightness only)
until it passes the floor for how fleet paints it: 4.5:1 for text (fleet
paints ``accent`` as link and highlight text) and for a label on a fill, 3:1 for
``primary`` and ``secondary``, which fleet uses only as fills, borders, checkbox
ticks and active bars, never as text. The original hexes stay in the comments,
so a reviewer can see what was moved and why.

``--check`` reads ``manifest.yaml`` and exits non-zero if any pair falls below
those floors; the bundle's tests run it. Fleet since #1830 also darkens a filled
button under its label at runtime, so a label pair that fails here is already
readable on a current box; the check still holds it, for boxes on older fleet.
"""

from __future__ import annotations

import argparse
import colorsys
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.yaml"
EMAIL_STYLE = ROOT / "protocols" / "email-style.yaml"
THEMES_DIR = ROOT / "protocols" / "email_styles" / "themes"

TEXT = 4.5  # WCAG AA, normal text
UI = 3.0  # WCAG AA, non-text contrast and large text
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
RGBA_RE = re.compile(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

WHITE = "#FFFFFF"
BLACK = "#000000"


# ── color math ──────────────────────────────────────────────────────────────


def norm(h: str) -> str:
    h = h.strip()
    if not h.startswith("#"):
        h = "#" + h
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", h):
        h = "#" + "".join(c * 2 for c in h[1:])
    if not HEX_RE.fullmatch(h):
        raise ValueError(f"not a hex color: {h!r}")
    return h.upper()


def rgb(h: str) -> tuple[int, int, int]:
    h = norm(h)
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def to_hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in (r, g, b))


def luminance(h: str) -> float:
    def lin(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb(h)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, t: float) -> str:
    """t=0 gives a, t=1 gives b."""
    (r1, g1, b1), (r2, g2, b2) = rgb(a), rgb(b)
    return to_hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def with_lightness(h: str, lightness: float) -> str:
    r, g, b = (c / 255 for c in rgb(h))
    hue, _, sat = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb(hue, max(0.0, min(1.0, lightness)), sat)
    return to_hex(r2 * 255, g2 * 255, b2 * 255)


def lightness(h: str) -> float:
    r, g, b = (c / 255 for c in rgb(h))
    return colorsys.rgb_to_hls(r, g, b)[1]


def shift(h: str, delta: float) -> str:
    return with_lightness(h, lightness(h) + delta)


def fit(color: str, against: str, floor: float, direction: str) -> str:
    """Move `color` along its hue ("darker"/"lighter") until it meets `floor`
    against `against`. Returns the color unchanged when it already passes."""
    color = norm(color)
    if contrast(color, against) >= floor:
        return color
    step = -0.005 if direction == "darker" else 0.005
    cur = lightness(color)
    for _ in range(200):
        cur += step
        cand = with_lightness(color, cur)
        if contrast(cand, against) >= floor:
            return cand
    return BLACK if direction == "darker" else WHITE


def rgba(h: str, alpha: float) -> str:
    r, g, b = rgb(h)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def ratio(a: str, b: str) -> str:
    return f"{contrast(a, b):.1f}:1"


# ── palette ─────────────────────────────────────────────────────────────────


@dataclass
class Brand:
    slug: str
    primary: str
    secondary: str
    accent: str
    ink: str
    paper: str
    dark_background: str | None = None


@dataclass
class Token:
    value: str
    comment: str = ""


@dataclass
class Mode:
    tokens: dict[str, Token] = field(default_factory=dict)

    def __getitem__(self, name: str) -> str:
        return self.tokens[name].value

    def set(self, name: str, value: str, comment: str = "") -> None:
        self.tokens[name] = Token(value, comment)


def on_fill(fill: str, dark: str) -> str:
    """The readable foreground on a fill: white when it reaches 4.5:1, else
    the darker candidate."""
    if contrast(WHITE, fill) >= TEXT:
        return WHITE
    return dark


def hover_of(primary: str, on_primary: str, delta: float) -> str:
    """A hover fill one step from primary that keeps its label readable: try
    `delta`, then the opposite direction, else fall back to primary itself."""
    for d in (delta, -delta):
        cand = shift(primary, d)
        if contrast(on_primary, cand) >= TEXT:
            return cand
    return primary


def moved(original: str, final: str) -> str:
    return "" if norm(original) == norm(final) else f" (brand {norm(original)} moved for contrast)"


def light_mode(b: Brand) -> Mode:
    m = Mode()
    bg = norm(b.paper)
    primary = fit(b.primary, bg, UI, "darker")
    on_primary = WHITE if contrast(WHITE, primary) >= TEXT else norm(b.ink)
    if contrast(on_primary, primary) < TEXT:
        primary = fit(primary, WHITE, TEXT, "darker")
        on_primary = WHITE
    hover = hover_of(primary, on_primary, -0.08)
    m.set("primary", primary, f"{ratio(primary, bg)} on the background{moved(b.primary, primary)}")
    m.set("primary_hover", hover, f"{ratio(hover, bg)}; label {ratio(on_primary, hover)}")
    m.set("on_primary", on_primary, f"{ratio(on_primary, primary)} on primary")
    secondary = fit(b.secondary, bg, UI, "darker")
    m.set("secondary", secondary, f"{ratio(secondary, bg)}{moved(b.secondary, secondary)}")
    accent = fit(b.accent, bg, TEXT, "darker")
    m.set("accent", accent, f"{ratio(accent, bg)}{moved(b.accent, accent)}")
    m.set("background", bg)
    m.set("surface_1", WHITE)
    m.set("surface_2", mix(bg, b.ink, 0.05))
    ink = fit(b.ink, bg, 7.0, "darker")
    m.set("text_primary", ink, f"{ratio(ink, bg)}{moved(b.ink, ink)}")
    text2 = fit(mix(ink, bg, 0.2), bg, 7.0, "darker")
    m.set("text_secondary", text2, ratio(text2, bg))
    muted = fit(mix(ink, bg, 0.45), m["surface_2"], TEXT, "darker")
    m.set("text_muted", muted, f"{ratio(muted, bg)}; {ratio(muted, m['surface_2'])} on surface_2")
    m.set("text_disabled", rgba(text2, 0.45))
    m.set("border", "rgba(0, 0, 0, 0.12)")
    m.set("border_strong", rgba(primary, 0.45))
    m.set("border_subtle", "rgba(0, 0, 0, 0.07)")
    m.set("overlay_soft", rgba(ink, 0.05))
    m.set("overlay_strong", rgba(ink, 0.20))
    m.set("rail_hover", "rgba(0, 0, 0, 0.05)")
    m.set("rail_active", rgba(primary, 0.10))
    return m


def dark_mode(b: Brand) -> Mode:
    m = Mode()
    bg = norm(b.dark_background) if b.dark_background else with_lightness(b.ink, 0.045)
    s1, s2 = shift(bg, 0.04), shift(bg, 0.075)
    deep = with_lightness(b.primary, 0.05)
    # Primary is a fill, border and active bar on the dark page (3:1; fleet
    # paints accent, not primary, as text); a dark brand color is lifted along
    # its hue, and the label on it flips to a near-black of the same hue when
    # white no longer reaches 4.5:1.
    primary = fit(b.primary, bg, UI, "lighter")
    on_primary = on_fill(primary, deep)
    if contrast(on_primary, primary) < TEXT:
        primary = fit(primary, deep, TEXT, "lighter")
    hover = hover_of(primary, on_primary, 0.06)
    if contrast(hover, bg) < UI:
        hover = primary
    m.set("primary", primary, f"{ratio(primary, bg)} on the background{moved(b.primary, primary)}")
    m.set("primary_hover", hover, f"{ratio(hover, bg)}; label {ratio(on_primary, hover)}")
    m.set("on_primary", on_primary, f"{ratio(on_primary, primary)} on primary")
    secondary = fit(b.secondary, bg, UI, "lighter")
    m.set("secondary", secondary, f"{ratio(secondary, bg)}{moved(b.secondary, secondary)}")
    accent = fit(b.accent, bg, TEXT, "lighter")
    m.set("accent", accent, f"{ratio(accent, bg)}{moved(b.accent, accent)}")
    m.set("background", bg)
    m.set("surface_1", s1)
    m.set("surface_2", s2)
    m.set("text_primary", WHITE, ratio(WHITE, bg))
    text2 = fit(mix(WHITE, bg, 0.18), s2, 7.0, "lighter")
    m.set("text_secondary", text2, ratio(text2, bg))
    muted = fit(mix(WHITE, bg, 0.42), s2, TEXT, "lighter")
    m.set("text_muted", muted, f"{ratio(muted, bg)}; {ratio(muted, s2)} on surface_2")
    m.set("text_disabled", rgba(text2, 0.45))
    m.set("border", "rgba(255, 255, 255, 0.14)")
    m.set("border_strong", rgba(primary, 0.55))
    m.set("border_subtle", "rgba(255, 255, 255, 0.08)")
    m.set("overlay_soft", "rgba(0, 0, 0, 0.20)")
    m.set("overlay_strong", "rgba(0, 0, 0, 0.40)")
    m.set("rail_hover", "rgba(255, 255, 255, 0.07)")
    m.set("rail_active", rgba(primary, 0.16))
    return m


ORDER = [
    "primary",
    "primary_hover",
    "on_primary",
    "secondary",
    "accent",
    "background",
    "surface_1",
    "surface_2",
    "text_primary",
    "text_secondary",
    "text_muted",
    "text_disabled",
    "border",
    "border_strong",
    "border_subtle",
    "overlay_soft",
    "overlay_strong",
    "rail_hover",
    "rail_active",
]


def colors_block(b: Brand, light: Mode, dark: Mode) -> str:
    lines = [
        "  colors:",
        f"    # Generated by assets/make-palette.py from {b.slug}'s brand hexes:",
        f"    #   primary {norm(b.primary)}, secondary {norm(b.secondary)}, accent {norm(b.accent)},",
        f"    #   ink {norm(b.ink)}, paper {norm(b.paper)}"
        + (f", dark background {norm(b.dark_background)}" if b.dark_background else "")
        + ".",
        "    # Ratios are WCAG 2.x against the mode's background unless noted.",
        "    # Rerun the script rather than hand-editing, so the email theme and",
        "    # chart colors stay in step.",
    ]
    for name, mode in (("dark", dark), ("light", light)):
        lines.append(f"    {name}:")
        for key in ORDER:
            t = mode.tokens[key]
            comment = f"  # {t.comment}" if t.comment else ""
            lines.append(f'      {key}: "{t.value}"{comment}')
    return "\n".join(lines) + "\n"


# ── email theme ─────────────────────────────────────────────────────────────

# Canonical email-template hexes (protocols/email-style.yaml canonical_template)
# and the role each plays. Semantic callout colors are left alone on purpose.


def email_theme(b: Brand, light: Mode, dark: Mode) -> tuple[dict[str, str], dict[str, str], list[str]]:
    paper = light["background"]
    header = (
        light["primary"]
        if contrast(WHITE, light["primary"]) >= TEXT
        else fit(light["text_primary"], WHITE, 7.0, "darker")
    )
    rule = fit(dark["accent"], header, UI, "lighter")
    meta = fit(dark["text_secondary"], header, TEXT, "lighter")
    if contrast(WHITE, light["primary"]) >= TEXT:
        cta = light["primary"]
    elif contrast(WHITE, light["secondary"]) >= TEXT:
        cta = light["secondary"]
    else:
        cta = fit(light["primary"], WHITE, TEXT, "darker")
    surface2 = light["surface_2"]
    border = mix(surface2, light["text_primary"], 0.08)
    muted = fit(light["text_muted"], paper, TEXT, "darker")
    overrides = {
        "#1A0B1E": header,
        "#7272AB": rule,
        "#D2D9DE": WHITE,
        "#BEB6CD": meta,
        "#6262A0": cta,
        "#141824": light["text_primary"],
        "#33415F": light["text_secondary"],
        "#586F7C": muted,
        "#5C6A87": muted,
        "#FAFAFA": WHITE,
        "#EEF3FF": surface2,
        "#F4F6FB": paper,
        "#D7DEEE": border,
        "#E6EBF5": border,
    }
    # A brand value can coincide with the canonical hex it replaces; nudge it by
    # one step in the blue channel (invisible) so the override still applies and
    # every brand-bearing hex stays covered.
    for k, v in overrides.items():
        if k.upper() == v.upper():
            r, g, bl = rgb(v)
            overrides[k] = to_hex(r, g, bl - 1 if bl else 1)
    notes = [
        f"white on the header band ......... {ratio(WHITE, header)}",
        f"accent rule on that band .......... {ratio(rule, header)}",
        f"date + meta text on that band ..... {ratio(meta, header)}",
        f"white on the CTA fill ............. {ratio(WHITE, cta)}",
        f"body text on the container ........ {ratio(light['text_primary'], WHITE)}",
        f"muted + footer text on outer bg ... {ratio(muted, paper)}",
    ]
    # Charts take the FINAL (possibly nudged) override values, so a chart PNG
    # matches the email it sits in exactly.
    o = overrides
    chart = {
        "background": o["#F4F6FB"],
        "primary_series": o["#6262A0"],
        "secondary_series": mix(o["#6262A0"], o["#F4F6FB"], 0.35),
        "band_fill": o["#EEF3FF"],
        "band_edge": mix(o["#6262A0"], o["#F4F6FB"], 0.7),
        "grid": mix(o["#EEF3FF"], o["#141824"], 0.04),
        "axis": o["#D7DEEE"],
        "text": o["#586F7C"],
        "text_strong": o["#141824"],
    }
    return overrides, chart, notes


def theme_yaml(b: Brand, overrides: dict[str, str], notes: list[str]) -> str:
    out = [
        f"theme_id: {b.slug}",
        "description: >-",
        f"  Brand theme `{b.slug}`, generated by assets/make-palette.py from the same",
        "  brand hexes as manifest.yaml branding.colors. Semantic callout colors",
        "  (risk amber, opportunity green, deltas) are deliberately not overridden.",
        "",
        "# Contrast, measured against the surface each value lands on:",
        *[f"#   {n}" for n in notes],
        "",
        "color_overrides:",
        *[f'  "{k}": "{v}"' for k, v in overrides.items()],
    ]
    return "\n".join(out) + "\n"


# ── writing ─────────────────────────────────────────────────────────────────


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def replace_colors(manifest_text: str, block: str) -> str:
    """Replace the `colors:` mapping under `branding:` with `block`.

    The mapping ends at the first non-comment, non-blank line indented less
    than its children (4 spaces); comment lines inside it, at any indent, are
    part of it. Trailing comments indented less than that belong to whatever
    follows, so they are kept."""
    lines = manifest_text.splitlines(keepends=True)
    try:
        b0 = next(i for i, ln in enumerate(lines) if re.match(r"^branding:\s*(#.*)?$", ln))
    except StopIteration:
        raise SystemExit("manifest.yaml: no top-level `branding:`") from None
    start = None
    for i in range(b0 + 1, len(lines)):
        ln, st = lines[i], lines[i].strip()
        if not st or st.startswith("#"):
            continue
        if _indent(ln) == 0:
            break
        if _indent(ln) == 2 and re.match(r"^  colors:\s*(#.*)?$", ln):
            start = i
            break
    if start is None:
        raise SystemExit("manifest.yaml: no `colors:` under `branding:`")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        st = lines[i].strip()
        if st and not st.startswith("#") and _indent(lines[i]) < 4:
            end = i
            break
    while end > start + 1:
        ln, st = lines[end - 1], lines[end - 1].strip()
        if not st or (st.startswith("#") and _indent(ln) < 4):
            end -= 1
        else:
            break
    return "".join(lines[:start]) + block + "".join(lines[end:])


def _section(text: str, name: str) -> tuple[int, int]:
    """Character span of a top-level YAML section, header line included."""
    m = re.search(rf"(?m)^{name}:[^\n]*\n", text)
    if not m:
        raise SystemExit(f"protocols/email-style.yaml: no top-level `{name}:`")
    nxt = re.search(r"(?m)^[A-Za-z_][\w-]*:", text[m.end() :])
    return m.start(), m.end() + nxt.start() if nxt else len(text)


def _sub_in_section(text: str, section: str, pattern: str, repl: str, what: str) -> str:
    start, end = _section(text, section)
    body, n = re.subn(pattern, repl, text[start:end], count=1)
    if n != 1:
        raise SystemExit(f"protocols/email-style.yaml: no `{what}` line")
    return text[:start] + body + text[end:]


def replace_email_style(text: str, slug: str, chart: dict[str, str]) -> str:
    text = _sub_in_section(
        text, "themes", r"""(?m)^(  default: )(["']?)[\w-]*\2""", rf'\g<1>"{slug}"', "themes.default"
    )
    for key, value in chart.items():
        text = _sub_in_section(
            text,
            "chart_colors",
            rf"""(?m)^(  {key}: )(["']?)#[0-9A-Fa-f]{{6}}\2""",
            rf'\g<1>"{value}"',
            f"chart_colors.{key}",
        )
    return text


def build(b: Brand) -> tuple[str, str, dict[str, str]]:
    light, dark = light_mode(b), dark_mode(b)
    block = colors_block(b, light, dark)
    overrides, chart, notes = email_theme(b, light, dark)
    return block, theme_yaml(b, overrides, notes), chart


# ── checking ────────────────────────────────────────────────────────────────


_ALPHA = r"(?:0|1|0?\.\d+|1\.0+)"
RGB_FUNC_RE = re.compile(rf"^rgba?\(\s*(\d{{1,3}})\s*,\s*(\d{{1,3}})\s*,\s*(\d{{1,3}})\s*(?:,\s*{_ALPHA}\s*)?\)$")
HSL_FUNC_RE = re.compile(
    rf"^hsla?\(\s*\d{{1,3}}(?:\.\d+)?(?:deg)?\s*,\s*\d{{1,3}}(?:\.\d+)?%\s*,\s*\d{{1,3}}(?:\.\d+)?%\s*(?:,\s*{_ALPHA}\s*)?\)$"
)


def css_color(value: str) -> bool:
    """Hex, or rgb()/rgba()/hsl()/hsla() with numeric arguments in range."""
    if HEX_RE.fullmatch(value):
        return True
    m = RGB_FUNC_RE.fullmatch(value)
    if m:
        return all(int(c) <= 255 for c in m.groups())
    return bool(HSL_FUNC_RE.fullmatch(value))


# Tokens a text/fill contrast pair reads: these must be plain hex so the ratio
# can be computed; the rest may be any color fleet accepts (washes, borders).
SOLID = {
    "primary",
    "primary_hover",
    "on_primary",
    "secondary",
    "accent",
    "background",
    "surface_1",
    "surface_2",
    "text_primary",
    "text_secondary",
    "text_muted",
}
PAIRS = [
    ("text_primary", "background", TEXT),
    ("text_secondary", "background", TEXT),
    ("text_muted", "background", TEXT),
    ("text_primary", "surface_1", TEXT),
    ("text_muted", "surface_2", TEXT),
    ("on_primary", "primary", TEXT),
    ("on_primary", "primary_hover", TEXT),
    ("primary", "background", UI),
    ("secondary", "background", UI),
    ("accent", "background", TEXT),
]


def check_colors(colors: dict) -> list[str]:
    problems = []
    for mode in ("light", "dark"):
        c = (colors or {}).get(mode) or {}
        missing = [k for k in ORDER if k not in c]
        if missing:
            problems.append(f"{mode}: missing tokens {missing}")
            continue
        bad = False
        for k in ORDER:
            v = str(c[k]).strip()
            if k in SOLID and not HEX_RE.fullmatch(v):
                problems.append(f"{mode}: {k} {v!r} must be a 6-digit hex (its contrast is checked)")
                bad = True
            elif not css_color(v):
                problems.append(f"{mode}: {k} {v!r} is not a hex or rgb()/rgba()/hsl()/hsla() color")
                bad = True
        if bad:
            continue
        for fg, bg, floor in PAIRS:
            a, b = norm(str(c[fg])), norm(str(c[bg]))
            r = contrast(a, b)
            if r < floor - 1e-9:
                problems.append(f"{mode}: {fg} {a} on {bg} {b} is {r:.2f}:1, needs {floor}:1")
    return problems


def check(manifest: Path = MANIFEST) -> list[str]:
    return check_colors(yaml.safe_load(manifest.read_text(encoding="utf-8"))["branding"]["colors"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="check manifest.yaml's colors and exit")
    ap.add_argument("--slug", help="theme id and file name, e.g. acme")
    ap.add_argument("--primary", help="the brand's main color")
    ap.add_argument("--secondary", help="a supporting color (default: a muted primary)")
    ap.add_argument("--accent", help="a highlight color (default: primary)")
    ap.add_argument("--ink", default="#1A1A1A", help="the brand's text color (default #1A1A1A)")
    ap.add_argument("--paper", default="#FAFAFA", help="the brand's light page color (default #FAFAFA)")
    ap.add_argument("--dark-background", help="dark-mode page color (default: a near-black of the ink's hue)")
    ap.add_argument("--write", action="store_true", help="write the files instead of printing them")
    args = ap.parse_args(argv)

    if args.check:
        problems = check()
        for p in problems:
            print(f"✗ {p}")
        if not problems:
            print("✓ manifest.yaml branding.colors meet the contrast floors")
        return 1 if problems else 0

    if not args.slug or not args.primary:
        ap.error("--slug and --primary are required unless --check")
    if not SLUG_RE.fullmatch(args.slug):
        ap.error("--slug must be lowercase letters, digits and hyphens, starting with a letter")
    if args.slug == "default":
        ap.error("--slug default is reserved for the unbranded theme; use the client's slug")
    try:
        primary = norm(args.primary)
        brand = Brand(
            slug=args.slug,
            primary=primary,
            secondary=norm(args.secondary) if args.secondary else mix(primary, norm(args.ink), 0.45),
            accent=norm(args.accent) if args.accent else primary,
            ink=norm(args.ink),
            paper=norm(args.paper),
            dark_background=norm(args.dark_background) if args.dark_background else None,
        )
    except ValueError as e:
        ap.error(str(e))

    block, theme, chart = build(brand)
    theme_path = THEMES_DIR / f"{brand.slug}.yaml"
    if not args.write:
        print("# manifest.yaml branding.colors\n" + block)
        print(f"# {theme_path.relative_to(ROOT)}\n" + theme)
        print("# protocols/email-style.yaml chart_colors")
        for k, v in chart.items():
            print(f'  {k}: "{v}"')
        return 0

    # Build and validate every output before writing any, so a failure leaves
    # all three files as they were.
    new_manifest = replace_colors(MANIFEST.read_text(encoding="utf-8"), block)
    new_style = replace_email_style(EMAIL_STYLE.read_text(encoding="utf-8"), brand.slug, chart)
    problems = check_colors(yaml.safe_load(new_manifest)["branding"]["colors"])
    yaml.safe_load(new_style)
    yaml.safe_load(theme)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        print("nothing written: these brand colors cannot meet the contrast floors")
        return 1
    MANIFEST.write_text(new_manifest, encoding="utf-8")
    theme_path.write_text(theme, encoding="utf-8")
    EMAIL_STYLE.write_text(new_style, encoding="utf-8")
    for p in (MANIFEST, theme_path, EMAIL_STYLE):
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

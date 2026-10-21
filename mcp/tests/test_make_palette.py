"""Tests for assets/make-palette.py, the brand-palette helper.

The helper is what a client fork runs instead of hand-picking 19 tokens, so it
must (1) always produce colors that pass its own contrast floors, whatever brand
hexes it is given, (2) cover every brand-bearing hex of the email template, and
(3) rewrite only the blocks it owns.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

BUNDLE_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_palette", BUNDLE_ROOT / "assets" / "make-palette.py")
mp = importlib.util.module_from_spec(_spec)
sys.modules["make_palette"] = mp
_spec.loader.exec_module(mp)

from test_email_theme import INTENTIONALLY_INHERITED  # noqa: E402

# Deliberately awkward brands: very dark, very light, saturated mid-tone,
# near-gray, and a primary that is almost the page color.
BRANDS = {
    "navy": {"primary": "#083340", "secondary": "#377A99", "accent": "#85B148", "ink": "#252525", "paper": "#F7F9F9"},
    "yellow": {"primary": "#FFD400", "secondary": "#FFE680", "accent": "#FFF200", "ink": "#111111", "paper": "#FFFFFF"},
    "red": {"primary": "#C11001", "secondary": "#425466", "accent": "#FD0221", "ink": "#252422", "paper": "#FAF9F7"},
    "gray": {"primary": "#808080", "secondary": "#999999", "accent": "#777777", "ink": "#333333", "paper": "#F5F5F5"},
    "pale": {"primary": "#F0F0F0", "secondary": "#E0E0E0", "accent": "#FAFAFA", "ink": "#202020", "paper": "#FFFFFF"},
}


def _brand(name: str, **extra: str) -> mp.Brand:
    return mp.Brand(slug=name, **{k: mp.norm(v) for k, v in BRANDS[name].items()}, **extra)


def test_template_manifest_passes_the_check() -> None:
    assert mp.check() == []


@pytest.mark.parametrize("name", sorted(BRANDS))
def test_generated_palette_passes_its_own_check(name: str, tmp_path: Path) -> None:
    block, _, _ = mp.build(_brand(name))
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(mp.replace_colors((BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"), block))
    assert mp.check(manifest) == []
    colors = yaml.safe_load(manifest.read_text())["branding"]["colors"]
    for mode in ("light", "dark"):
        assert set(colors[mode]) == set(mp.ORDER)


@pytest.mark.parametrize("name", sorted(BRANDS))
def test_generated_email_theme_covers_every_brand_hex(name: str) -> None:
    _, theme_text, chart = mp.build(_brand(name))
    theme = yaml.safe_load(theme_text)
    canonical = {
        h.upper()
        for h in re.findall(
            r"#[0-9a-fA-F]{6}", yaml.safe_load(mp.EMAIL_STYLE.read_text(encoding="utf-8"))["canonical_template"]
        )
    }
    keys = {k.upper() for k in theme["color_overrides"]}
    values = theme["color_overrides"].values()
    assert keys <= canonical
    assert not (canonical - keys - set(INTENTIONALLY_INHERITED))
    assert all(re.fullmatch(r"#[0-9A-F]{6}", v) for v in values)
    # White header text and the CTA label must stay readable.
    header = theme["color_overrides"].get("#1A0B1E", "#1A0B1E")
    cta = theme["color_overrides"].get("#6262A0", "#6262A0")
    assert mp.contrast(mp.WHITE, header) >= mp.TEXT
    assert mp.contrast(mp.WHITE, cta) >= mp.TEXT
    assert chart["primary_series"] == cta


def test_dark_brand_color_is_lifted_not_kept_raw() -> None:
    # A dark brand red is under 4.5:1 on a near-black page; it must move along
    # its own hue (still red) rather than ship unreadable links.
    block, _, _ = mp.build(_brand("red", dark_background="#0B0A0A"))
    dark = yaml.safe_load("branding:\n" + block)["branding"]["colors"]["dark"]
    assert mp.contrast(dark["primary"], "#0B0A0A") >= mp.TEXT
    r, g, b = mp.rgb(dark["primary"])
    assert r > g and r > b, "the lifted primary keeps the brand hue"


def test_replace_colors_touches_only_the_colors_block() -> None:
    text = (BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8")
    block, _, _ = mp.build(_brand("navy"))
    new = mp.replace_colors(text, block)
    before, after = yaml.safe_load(text), yaml.safe_load(new)
    assert after["branding"]["colors"] != before["branding"]["colors"]
    for key in before:
        if key != "branding":
            assert after[key] == before[key], key
    for key in before["branding"]:
        if key != "colors":
            assert after["branding"][key] == before["branding"][key], key
    # Idempotent: a second run replaces the generated block, not appends.
    assert mp.replace_colors(new, block) == new


def test_replace_email_style_touches_only_default_and_chart_colors() -> None:
    text = mp.EMAIL_STYLE.read_text(encoding="utf-8")
    _, _, chart = mp.build(_brand("navy"))
    new = mp.replace_email_style(text, "navy", chart)
    before, after = yaml.safe_load(text), yaml.safe_load(new)
    assert after["themes"]["default"] == "navy"
    for k, v in chart.items():
        assert after["chart_colors"][k] == v
    for key in before:
        if key not in {"themes", "chart_colors"}:
            assert after[key] == before[key], key
    assert after["chart_colors"]["positive_marker"] == before["chart_colors"]["positive_marker"]


def test_check_reports_a_failing_pair(tmp_path: Path) -> None:
    text = (BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8")
    bad = re.sub(r'(?m)^(      text_muted: )"#[0-9A-Fa-f]{6}"', r'\1"#CCCCCC"', text)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(bad)
    problems = mp.check(manifest)
    assert any("text_muted" in p for p in problems)


def test_cli_rejects_a_bad_slug_and_hex() -> None:
    with pytest.raises(SystemExit):
        mp.main(["--slug", "Bad Slug", "--primary", "#123456"])
    with pytest.raises(SystemExit):
        mp.main(["--slug", "ok", "--primary", "blue"])


def test_comments_inside_the_colors_block_do_not_end_it() -> None:
    # A hand-edited manifest may carry a comment at any indent between the
    # modes; the block must still be replaced whole, never leaving a second
    # `dark:` behind.
    text = (BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8")
    text = text.replace("    light:\n", "  # a two-space comment between the modes\n    light:\n", 1)
    block, _, _ = mp.build(_brand("navy"))
    new = mp.replace_colors(text, block)
    region = new[new.index("  colors:") : new.index("# ── Model defaults")]
    assert region.count("    dark:\n") == 1
    assert region.count("    light:\n") == 1
    assert "a two-space comment between the modes" not in new
    assert "# ── Model defaults" in new


def test_hover_keeps_its_label_readable() -> None:
    brand = mp.Brand(
        slug="mid",
        primary="#747474",
        secondary="#747474",
        accent="#747474",
        ink="#111111",
        paper="#FFFFFF",
        dark_background="#000000",
    )
    block, _, _ = mp.build(brand)
    colors = yaml.safe_load("branding:\n" + block)["branding"]["colors"]
    for mode in ("light", "dark"):
        c = colors[mode]
        assert mp.contrast(c["on_primary"], c["primary_hover"]) >= mp.TEXT, mode
    assert mp.check_colors(colors) == []


def test_check_rejects_values_it_cannot_measure() -> None:
    colors = yaml.safe_load((BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"))["branding"]["colors"]
    colors["light"]["text_primary"] = "not-a-color"
    colors["dark"]["border"] = "blue"
    problems = mp.check_colors(colors)
    assert any("text_primary" in p for p in problems)
    assert any("border" in p for p in problems)


def test_identity_values_are_nudged_not_dropped() -> None:
    # Paper equal to the canonical outer background used to drop that
    # override and leave the hex uncovered.
    brand = mp.Brand(
        slug="same", primary="#6262A0", secondary="#33415F", accent="#7272AB", ink="#141824", paper="#F4F6FB"
    )
    _, theme_text, _ = mp.build(brand)
    overrides = {k.upper(): v.upper() for k, v in yaml.safe_load(theme_text)["color_overrides"].items()}
    assert "#F4F6FB" in overrides and overrides["#F4F6FB"] != "#F4F6FB"
    assert all(k != v for k, v in overrides.items())


def test_a_failed_write_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, style, themes = tmp_path / "manifest.yaml", tmp_path / "email-style.yaml", tmp_path / "themes"
    themes.mkdir()
    manifest.write_text((BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    style.write_text("themes:\n  dir: x\n")  # no default, no chart_colors: the build must fail
    before = manifest.read_text()
    monkeypatch.setattr(mp, "MANIFEST", manifest)
    monkeypatch.setattr(mp, "EMAIL_STYLE", style)
    monkeypatch.setattr(mp, "THEMES_DIR", themes)
    monkeypatch.setattr(mp, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        mp.main(["--slug", "acme", "--primary", "#083340", "--write"])
    assert manifest.read_text() == before
    assert not list(themes.iterdir())


def test_default_slug_is_reserved() -> None:
    with pytest.raises(SystemExit):
        mp.main(["--slug", "default", "--primary", "#083340", "--write"])


def test_chart_colors_match_the_final_theme_values() -> None:
    # Paper equal to a canonical hex is nudged in the theme; the chart must
    # carry the nudged value too, or the PNG and the email disagree.
    brand = mp.Brand(
        slug="same", primary="#6262A0", secondary="#33415F", accent="#7272AB", ink="#141824", paper="#F4F6FB"
    )
    _, theme_text, chart = mp.build(brand)
    o = {k.upper(): v.upper() for k, v in yaml.safe_load(theme_text)["color_overrides"].items()}
    assert chart["background"].upper() == o["#F4F6FB"]
    assert chart["primary_series"].upper() == o["#6262A0"]
    assert chart["text_strong"].upper() == o["#141824"]


@pytest.mark.parametrize(
    "value",
    ["rgba(banana)", "rgb(300, 0, 0)", "rgba(0, 0, 0, 2)", "hsl(10, 20, 30)", "#12345", "rgba(0,0,0,0.5) x"],
)
def test_check_rejects_malformed_washes(value: str) -> None:
    colors = yaml.safe_load((BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"))["branding"]["colors"]
    colors["light"]["border"] = value
    assert any("border" in p for p in mp.check_colors(colors)), value


@pytest.mark.parametrize("value", ["rgba(0, 0, 0, 0.12)", "rgb(10,20,30)", "hsla(210deg, 20%, 30%, .5)", "#AABBCC"])
def test_check_accepts_wellformed_washes(value: str) -> None:
    colors = yaml.safe_load((BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"))["branding"]["colors"]
    colors["light"]["border"] = value
    assert mp.check_colors(colors) == []

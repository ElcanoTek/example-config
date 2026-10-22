"""Tests for the email themes and the chart palette.

Email themes are applied as a literal hex find-replace over
``canonical_template`` (see protocols/email-style.yaml → rendering.build). That
mechanism fails SILENTLY in both directions:

  * an override keyed on a hex that is not in the template replaces nothing, and
    the email renders in the canonical template purple with no error anywhere;
  * a canonical hex nobody overrides is inherited, which is correct for the
    semantic callout colors and a bug for anything brand-bearing.

So both directions are asserted here, for every theme file, rather than
eyeballed in a test send. The template ships only the unbranded `default`
theme; a client fork adds its own with assets/make-palette.py, and these tests
then hold it to the same contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

BUNDLE_ROOT = Path(__file__).resolve().parents[2]

EMAIL_STYLE_PATH = BUNDLE_ROOT / "protocols" / "email-style.yaml"
THEMES_DIR = BUNDLE_ROOT / "protocols" / "email_styles" / "themes"

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")

# Canonical hexes a brand theme deliberately does NOT override, with the
# reason. Risk (amber) and opportunity (green) callouts plus the delta colors
# encode MEANING, not brand: a brand-blue "risk" callout would read as neutral
# information. Same rule as fleet's non-themable status colors.
INTENTIONALLY_INHERITED = {
    "#FFF5E5": "risk callout background (semantic amber)",
    "#F5DDB5": "risk callout border (semantic amber)",
    "#FFB86C": "risk callout left accent (semantic amber)",
    "#8C5A18": "risk callout heading text (semantic amber)",
    "#EFFAF3": "opportunity callout background (semantic green)",
    "#CDEDD8": "opportunity callout border (semantic green)",
    "#50FA7B": "opportunity callout left accent (semantic green)",
    "#1C5A33": "opportunity heading + positive delta (semantic green)",
    "#7C1F1F": "negative delta text (semantic red)",
}

THEME_FILES = sorted(THEMES_DIR.glob("*.yaml"))
BRAND_THEMES = [p for p in THEME_FILES if p.stem != "default"]


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.fixture(scope="module")
def email_style() -> dict:
    return _load(EMAIL_STYLE_PATH)


@pytest.fixture(scope="module")
def canonical_hexes(email_style: dict) -> set[str]:
    return {h.upper() for h in HEX_RE.findall(email_style["canonical_template"])}


def test_default_theme_file_exists(email_style: dict) -> None:
    # rendering.build loads themes/<default>.yaml by name; a default with no file
    # would fail at render time, inside a task, hours after this change.
    default = email_style["themes"]["default"]
    assert (THEMES_DIR / f"{default}.yaml").is_file()


def test_unbranded_default_theme_is_kept() -> None:
    # Other bundles' docs and render_email fall back to `default`; a fork adds
    # its own theme next to it rather than editing it.
    assert _load(THEMES_DIR / "default.yaml")["color_overrides"] == {}


@pytest.mark.parametrize("path", THEME_FILES, ids=lambda p: p.stem)
def test_theme_id_matches_filename(path: Path) -> None:
    assert _load(path)["theme_id"] == path.stem


@pytest.mark.parametrize("path", THEME_FILES, ids=lambda p: p.stem)
def test_every_override_key_exists_in_the_canonical_template(path: Path, canonical_hexes: set[str]) -> None:
    keys = {k.upper() for k in _load(path)["color_overrides"]}
    orphans = keys - canonical_hexes
    assert not orphans, (
        f"these overrides match nothing in canonical_template and silently do nothing: {sorted(orphans)}"
    )


@pytest.mark.parametrize("path", BRAND_THEMES, ids=lambda p: p.stem)
def test_every_brand_bearing_canonical_hex_is_overridden(path: Path, canonical_hexes: set[str]) -> None:
    keys = {k.upper() for k in _load(path)["color_overrides"]}
    unstyled = canonical_hexes - keys - set(INTENTIONALLY_INHERITED)
    # make-palette.py drops an override whose value equals its key; that hex is
    # then already the brand's color.
    assert not unstyled, (
        f"canonical_template carries these hexes that theme {path.stem!r} neither overrides nor "
        f"documents as intentionally inherited, so they render in the canonical template colors: {sorted(unstyled)}"
    )


@pytest.mark.parametrize("path", THEME_FILES, ids=lambda p: p.stem)
def test_no_override_is_a_no_op(path: Path) -> None:
    for canonical, replacement in _load(path)["color_overrides"].items():
        assert canonical.upper() != replacement.upper(), f"{canonical} is mapped to itself"


@pytest.mark.parametrize("path", THEME_FILES, ids=lambda p: p.stem)
def test_override_values_are_six_digit_hex(path: Path) -> None:
    # The find-replace is literal, so a shorthand (#FFF) or a named color would be
    # substituted verbatim into an inline style AND an Outlook bgcolor attribute.
    for canonical, replacement in _load(path)["color_overrides"].items():
        assert HEX_RE.fullmatch(replacement), f"{canonical} -> {replacement!r} is not a 6-digit hex"


def test_palette_reference_table_matches_the_template(canonical_hexes: set[str]) -> None:
    """The README palette table is the map theme authors work from.

    It had drifted once before: the table still listed the
    old risk-callout amber set (#FFF7E8/#F0D7AF/#FF8847/#8F5A12) while the
    template had moved to #FFF5E5/#F5DDB5/#FFB86C/#8C5A18. A theme author keying
    overrides off the table would have written four overrides that matched
    nothing. Assert the two agree so it cannot drift again.
    """
    readme = (THEMES_DIR.parent / "README.md").read_text(encoding="utf-8")
    documented = {h.upper() for h in re.findall(r"`(#[0-9a-fA-F]{6})`", readme)}
    assert documented == canonical_hexes, (
        f"palette table drifted from canonical_template — "
        f"documented but absent: {sorted(documented - canonical_hexes)}; "
        f"in template but undocumented: {sorted(canonical_hexes - documented)}"
    )


def test_chart_palette_follows_the_default_theme(email_style: dict) -> None:
    """chart_colors drives matplotlib PNGs, which are generated OUTSIDE the HTML
    template, so the theme find-replace never reaches them: the values in the
    file itself must match the default theme's surfaces and CTA color."""
    chart = {k: v.upper() for k, v in email_style["chart_colors"].items() if isinstance(v, str)}
    theme = _load(THEMES_DIR / f"{email_style['themes']['default']}.yaml")
    o = {k.upper(): v.upper() for k, v in theme["color_overrides"].items()}
    assert chart["background"] == o.get("#F4F6FB", "#F4F6FB"), "chart background must match the email's outer bg"
    assert chart["primary_series"] == o.get("#6262A0", "#6262A0"), "chart primary series must be the CTA color"
    assert chart["text_strong"] == o.get("#141824", "#141824"), "chart annotations must use the body text color"

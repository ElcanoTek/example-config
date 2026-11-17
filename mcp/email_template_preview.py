"""Render each email template's sample into `email_templates/previews/<id>.html`.

fleet's Email templates library shows these files beside a template's rules, so
a person sees the email the rules produce. They are rendered by the same
`render_email` path a real send uses (the sample goes through the template
check first), with two substitutions a standalone preview needs: the header
logo is inlined, and each chart is a labelled placeholder, since a preview has
no attachments.

Beside them it writes `previews/styles.json`: for every look a template's Style
field offers, which colors of the preview become which. The library recolors
the example with it as the person picks a style, so they see the look before
anything is written. It is the theme files' own overrides, and for custom
colors the hexes each one replaces (email_render.CUSTOM_COLORS).

Run after editing a template, a theme or the renderer:

    python mcp/email_template_preview.py          # rewrite the previews
    python mcp/email_template_preview.py --check  # exit 1 if any is stale

tests/test_email_templates.py runs the same check, so a stale preview fails CI.
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import email_render

import email_templates

PREVIEWS_DIR = email_templates.TEMPLATES_DIR / "previews"
_CHART_IMG = re.compile(r'<img src="cid:(?!brand-logo")([^"]+)" alt="([^"]*)"')
_HEADER_ARGS = ("client_name", "date", "date_range", "badge_label", "status", "preheader", "headline")
_CHECKED_HEADER_ARGS = ("headline", "status", "badge_label", "preheader", "client_name", "date_range", "date")


def _chart_placeholder(alt: str) -> str:
    """A 1088x420 stand-in for a chart, as a data URI: the size a real chart has."""
    label = html.escape(html.unescape(alt), quote=True)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1088" height="420" viewBox="0 0 1088 420">'
        '<rect width="1088" height="420" fill="#F4F6FB"/>'
        '<g fill="#C5C5DC">'
        '<rect x="120" y="250" width="90" height="110"/><rect x="250" y="210" width="90" height="150"/>'
        '<rect x="380" y="230" width="90" height="130"/><rect x="510" y="170" width="90" height="190"/>'
        '<rect x="640" y="190" width="90" height="170"/><rect x="770" y="140" width="90" height="220"/>'
        '<rect x="900" y="120" width="90" height="240"/></g>'
        '<path d="M165 240 L295 205 L425 215 L555 165 L685 175 L815 130 L945 105" fill="none" '
        'stroke="#7272AB" stroke-width="5"/>'
        '<line x1="90" y1="360" x2="1010" y2="360" stroke="#586F7C" stroke-width="2"/>'
        '<text x="544" y="60" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" font-size="26" '
        f'fill="#33415F">Chart: {label}</text></svg>'
    )
    return "data:image/svg+xml," + quote(svg, safe="")


def plain_theme() -> str:
    """The theme with no color overrides ("" when the bundle has none): the palette the recolor table starts from."""
    themes = email_render.available_themes()
    for theme in sorted(themes, key=lambda name: name != "default"):
        if not email_render._theme_overrides(theme):
            return theme
    return ""


def _sample_theme(spec: Mapping[str, Any]) -> str:
    """The theme an example is rendered in.

    A template with a Style picker is rendered in the plain palette, because
    the recolor table (preview_styles) maps that palette to each look; the
    library then shows the picker's default look, and any other the person
    picks, by recoloring; a theme named in the sample or the template does not
    change that, or the table would be applied to the wrong colors. A template
    without a picker is shown in its own theme.
    """
    if any(isinstance(f, Mapping) and f.get("type") == "palette" for f in spec.get("fields") or []):
        return plain_theme()
    return str(spec["sample"].get("theme") or spec.get("theme") or "")


def render_sample(spec: Mapping[str, Any], colors: Mapping[str, str] | None = None, theme: str | None = None) -> str:
    """The template's sample as render_email produces it. Raises ValueError if the sample does not fit.

    `theme` renders it in that theme instead of the one an example is stored in
    (the tests compare such a render with the recolored example).
    """
    sample = spec["sample"]
    subject = str(sample.get("subject") or "")
    header = {key: str(sample.get(key) or "") for key in _HEADER_ARGS}
    blocks, violations = email_templates.apply_template(
        spec,
        subject,
        sample["blocks"],
        # Every header value render_email checks (ses_sender.render_email), so a
        # sample that passes here is one the tool would build.
        header_values=tuple(header[key] for key in _CHECKED_HEADER_ARGS),
    )
    if violations:
        raise ValueError(f"the sample of template {spec['id']} breaks its own rules: " + " | ".join(violations))
    return email_render.render_email(
        subject=subject,
        blocks=blocks,
        theme=_sample_theme(spec) if theme is None else theme,
        colors=colors,
        **header,
    )


def standalone(page: str) -> str:
    """A rendered email as a page that shows without attachments: logo inlined, charts as placeholders."""
    logo = email_render.brand_logo()
    if logo is not None:
        encoded = base64.b64encode(logo[0].read_bytes()).decode("ascii")
        page = page.replace(f"cid:{email_render.LOGO_CID}", f"data:image/png;base64,{encoded}")
    return _CHART_IMG.sub(lambda m: f'<img src="{_chart_placeholder(m.group(2))}" alt="{m.group(2)}"', page)


def render_preview(spec: Mapping[str, Any], colors: Mapping[str, str] | None = None, theme: str | None = None) -> str:
    """The template's sample as a standalone HTML page. Raises ValueError if the sample does not fit."""
    return standalone(render_sample(spec, colors, theme))


def preview_styles(ids: list[str] | None = None) -> dict[str, Any]:
    """The recolor table for the previews, from the templates' palette fields.

    `options` maps each offered look to its {preview hex: its hex}; `custom`
    maps each custom color's name to the preview hexes it replaces. `ids`
    defaults to the bundle's own templates (the committed table).
    """
    options: dict[str, dict[str, str]] = {}
    custom: dict[str, list[str]] = {}
    for template_id in email_templates.bundle_template_ids() if ids is None else ids:
        add_styles(email_templates.load_template(template_id), options, custom)
    return {"options": dict(sorted(options.items())), "custom": custom}


def add_styles(spec: Mapping[str, Any], options: dict[str, dict[str, str]], custom: dict[str, list[str]]) -> None:
    """Add one template's palette fields to a recolor table. ValueError for a look render_email cannot produce."""
    for field in spec.get("fields") or []:
        if not isinstance(field, Mapping) or field.get("type") != "palette":
            continue
        for option in field.get("options") or []:
            try:
                overrides = email_render._theme_overrides(email_render.resolve_theme(str(option)))
            except email_render.RenderError as exc:
                raise ValueError(f"style option {option!r} is not a theme render_email takes: {exc}") from exc
            options[str(option)] = {k.upper(): v.upper() for k, v in sorted(overrides.items())}
        for name in field.get("custom") or []:
            replaced = email_render.CUSTOM_COLORS.get(str(name).lower())
            if replaced is None:
                known = ", ".join(role.capitalize() for role in email_render.CUSTOM_COLORS)
                raise ValueError(f"custom color {name!r} is not one render_email takes ({known})")
            custom[str(name)] = list(replaced[0])


def _styles_text() -> str:
    return json.dumps(preview_styles(), indent=2) + "\n"


def stale_previews() -> list[str]:
    """Template ids whose committed preview differs from a fresh render, plus orphaned files."""
    stale = []
    ids = email_templates.bundle_template_ids()
    styles = PREVIEWS_DIR / "styles.json"
    if ids and (not styles.is_file() or styles.read_text(encoding="utf-8") != _styles_text()):
        stale.append("styles.json")
    if not ids and styles.is_file():
        stale.append("styles.json (no template)")
    for template_id in ids:
        path = PREVIEWS_DIR / f"{template_id}.html"
        fresh = render_preview(email_templates.load_template(template_id))
        if not path.is_file() or path.read_text(encoding="utf-8") != fresh:
            stale.append(template_id)
    if PREVIEWS_DIR.is_dir():
        stale += [f"{p.stem} (no template)" for p in sorted(PREVIEWS_DIR.glob("*.html")) if p.stem not in ids]
    return stale


def main(argv: list[str]) -> int:
    if "--check" in argv:
        stale = stale_previews()
        if stale:
            print("stale email template previews: " + ", ".join(stale), file=sys.stderr)
            print("run: python mcp/email_template_preview.py", file=sys.stderr)
            return 1
        return 0
    ids = email_templates.bundle_template_ids()
    if not ids:
        print("this bundle ships no email templates; nothing to render")
        return 0
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    for template_id in ids:
        page = render_preview(email_templates.load_template(template_id))
        (PREVIEWS_DIR / f"{template_id}.html").write_text(page, encoding="utf-8")
        print(f"wrote email_templates/previews/{template_id}.html ({len(page)} bytes)")
    (PREVIEWS_DIR / "styles.json").write_text(_styles_text(), encoding="utf-8")
    print("wrote email_templates/previews/styles.json")
    for orphan in PREVIEWS_DIR.glob("*.html"):
        if orphan.stem not in ids:
            print(f"note: {orphan.name} has no template; delete it by hand", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

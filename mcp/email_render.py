"""Deterministic HTML email renderer for the canonical template.

The agent used to build every email by copying `canonical_template` out of
protocols/email-style.yaml token by token: ~9 KB of fixed head/header/footer
HTML before the first word of content, plus the body patterns, then a theme
find-replace, comment stripping and a lint round trip. That is minutes of
model output per send and a steady source of copy drift.

`render_email` takes the content as structured blocks instead and assembles the
HTML here:

* The fixed chrome (head + mobile CSS + Outlook conditionals, header, status
  bar, footer) is cut from `canonical_template` at runtime, so the protocol
  file stays the single source of truth for it.
* The header carries the client's logo (`assets/email-logo.png`, attached by
  the caller as `cid:brand-logo`) beside the client name; a bundle without
  that file gets the name alone.
* Body blocks reproduce the template's pattern sections (and the protocol's
  supplemental components) with the same inline styles;
  tests/test_email_render.py asserts they have not drifted from the protocol.
* The theme's `color_overrides` are applied in one pass, before any
  caller-supplied value is inserted, so user text is never recolored.
* Every caller-supplied value is HTML-escaped. The only markup a caller can
  produce is `**bold**` and line breaks.

The module is pure: no I/O beyond reading the bundle's protocol and theme
files, and no network. `ses_sender.render_email` owns paths and validation.
"""

from __future__ import annotations

import datetime as _dt
import functools
import html
import os
import re
import struct
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_PATH = BUNDLE_ROOT / "protocols" / "email-style.yaml"
# The header logo: a PNG made for the dark header band (2x for retina). PNG
# because Gmail and Outlook do not render SVG. The caller attaches it inline.
LOGO_PATH = BUNDLE_ROOT / "assets" / "email-logo.png"
LOGO_CID = "brand-logo"
LOGO_HEIGHT = 44
LOGO_MAX_WIDTH = 176

MAX_TABLE_COLUMNS = 5
MAX_CTA_TEXT = 25

# Inline styles shared by several blocks, verbatim from canonical_template.
_KICKER_H2 = (
    '<h2 style="margin:0; font-size:12px; line-height:17px; font-weight:700; letter-spacing:0.08em; '
    'text-transform:uppercase; color:#586F7C;">{}</h2>'
)
_BODY_TEXT = "font-size:16px; line-height:24px; color:#141824;"
_MOBILE_LABEL = (
    '<span class="mobile-label" style="display:none; font-size:0; line-height:0; max-height:0; '
    'overflow:hidden;{extra}">{label}</span>'
)
_DELTA_COLORS = {"positive": "#1C5A33", "negative": "#7C1F1F", "neutral": "#586F7C"}
_ZEBRA = ("#FAFAFA", "#F4F6FB")

_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
# Informational comments only. Outlook conditional comments (<!--[if mso]>,
# <!--[if !mso]><!-->, <!--<![endif]-->) are functional and must survive; the
# `>` exclusion keeps the `<!-->` that closes `[if !mso]` from being read as
# the start of a comment running to the next `-->`.
_PLAIN_COMMENT = re.compile(r"<!--(?!\[if|<!\[endif\]|>).*?-->\s*", re.DOTALL)
_DATA_PREVIEW = re.compile(r'\s+data-preview="[^"]*"')
_SLOT = re.compile(r"\x00(\d+)\x00")
_TEMPLATE_TOKEN = re.compile(r"\{\{(\w+)\}\}")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CID = re.compile(r"[A-Za-z0-9_.@\-]+")
_SECTION_BANNER = re.compile(r"<!-- =+\s+([A-Z][A-Z ]*?):")
_LOGO_CELL = re.compile(r'<td class="header-logo"[^>]*>.*?</td>\s*', re.DOTALL)
_LOGO_SIZE_ATTRS = 'width="44" height="44"'
_LOGO_SIZE_STYLE = "width:44px; height:44px;"
_META_ROW = re.compile(r'<tr>\s*<td class="header-meta"[^>]*>\{\{date_range\}\}</td>\s*</tr>\s*')
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class RenderError(ValueError):
    """The blocks or header fields cannot be rendered; the message says why."""


@functools.lru_cache(maxsize=1)
def _protocol() -> dict[str, Any]:
    with PROTOCOL_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def themes_dir() -> Path:
    return BUNDLE_ROOT / _protocol()["themes"]["dir"]


def default_theme() -> str:
    return str(_protocol()["themes"]["default"])


def available_themes() -> list[str]:
    return sorted(p.stem for p in themes_dir().glob("*.yaml"))


@functools.lru_cache(maxsize=8)
def _theme_overrides(theme_id: str) -> dict[str, str]:
    if theme_id not in available_themes():
        raise RenderError(f"Unknown theme {theme_id!r}. Available themes: {', '.join(available_themes())}.")
    with (themes_dir() / f"{theme_id}.yaml").open(encoding="utf-8") as fh:
        theme = yaml.safe_load(fh) or {}
    return {str(k).upper(): str(v) for k, v in (theme.get("color_overrides") or {}).items()}


@functools.lru_cache(maxsize=1)
def _chrome() -> tuple[str, str, str]:
    """Split canonical_template into (head+header, status bar, footer+close).

    Cut at the section banners, so whatever the mirrored template puts in those
    regions is used as is. A banner rename fails loudly here (and in tests)
    rather than rendering a half template.
    """
    template = _protocol()["canonical_template"]
    starts = {m.group(1): m.start() for m in _SECTION_BANNER.finditer(template)}
    missing = [name for name in ("STATUS BAR", "BODY", "FOOTER") if name not in starts]
    if missing:
        raise RuntimeError(f"canonical_template is missing section banners: {', '.join(missing)}")
    # Each cut starts at the line holding the banner so indentation stays intact.
    status, body, footer = (template.rfind("\n", 0, starts[name]) + 1 for name in ("STATUS BAR", "BODY", "FOOTER"))
    return template[:status], template[status:body], template[footer:]


@functools.lru_cache(maxsize=1)
def brand_logo() -> tuple[Path, int, int] | None:
    """(path, width, height) to show the bundle's header logo at, or None.

    Shown LOGO_HEIGHT px tall at its own aspect ratio, capped at
    LOGO_MAX_WIDTH so a long wordmark cannot crowd out the client name. A
    missing or unreadable file means no logo, never a broken image.
    """
    try:
        with LOGO_PATH.open("rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(_PNG_SIGNATURE) or head[12:16] != b"IHDR":
        return None
    px_width, px_height = struct.unpack(">II", head[16:24])
    if not px_width or not px_height:
        return None
    width, height = round(LOGO_HEIGHT * px_width / px_height), LOGO_HEIGHT
    if width > LOGO_MAX_WIDTH:
        width, height = LOGO_MAX_WIDTH, round(LOGO_MAX_WIDTH * px_height / px_width)
    return LOGO_PATH, width, height


def _header_logo(head: str, logo: tuple[Path, int, int] | None) -> str:
    """Size the template's logo cell for this bundle's logo, or drop the cell."""
    cell = _LOGO_CELL.search(head)
    if cell is None:
        raise RuntimeError("canonical_template header has no header-logo cell")
    if logo is None:
        return head[: cell.start()] + head[cell.end() :]
    markup = cell.group(0)
    if _LOGO_SIZE_ATTRS not in markup or _LOGO_SIZE_STYLE not in markup or f"cid:{LOGO_CID}" not in markup:
        raise RuntimeError("canonical_template header-logo cell changed shape; update email_render")
    _, width, height = logo
    markup = markup.replace(_LOGO_SIZE_ATTRS, f'width="{width}" height="{height}"').replace(
        _LOGO_SIZE_STYLE, f"width:{width}px; height:{height}px;"
    )
    return head[: cell.start()] + markup + head[cell.end() :]


class _Slots:
    """Placeholders for caller values, filled only after theming and cleanup.

    Builders emit `\\x00n\\x00` where a value goes. The theme's hex
    find-replace and the comment/data-preview strip therefore never see user
    text, and a value can never be re-substituted as another value's token.
    """

    def __init__(self) -> None:
        self.values: list[str] = []

    def raw(self, markup: str) -> str:
        self.values.append(markup)
        return f"\x00{len(self.values) - 1}\x00"

    def text(self, value: Any) -> str:
        return self.raw(_inline(value))

    def attr(self, value: Any) -> str:
        return self.raw(html.escape(_clean(value), quote=True))

    def fill(self, markup: str) -> str:
        return _SLOT.sub(lambda m: self.values[int(m.group(1))], markup)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return _CONTROL.sub("", str(value)).strip()


def _inline(value: Any) -> str:
    """Escape a caller value; allow **bold** and line breaks, nothing else."""
    escaped = html.escape(_clean(value), quote=True)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return escaped.replace("\r\n", "\n").replace("\n", "<br>")


def _plain(value: Any) -> str:
    """A caller value with the bold markers removed, for preheaders and titles."""
    return _BOLD.sub(r"\1", " ".join(_clean(value).split()))


def _require(block: Mapping[str, Any], key: str, where: str) -> Any:
    value = block.get(key)
    if value is None or (isinstance(value, (str, list)) and not value):
        raise RenderError(f"{where}: '{key}' is required.")
    return value


def _string_list(value: Any, key: str, where: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, Sequence):
        raise RenderError(f"{where}: '{key}' must be a list of strings.")
    return [item for item in value if _clean(item)]


def _row(padding: str, inner: str) -> str:
    return f'<tr>\n  <td class="body-pad" style="padding:{padding};">\n{inner}\n  </td>\n</tr>\n'


def _layout_table(rows: str, extra_style: str = "", bgcolor: str = "") -> str:
    bg = f' bgcolor="{bgcolor}"' if bgcolor else ""
    style = "width:100%; border-collapse:collapse;" + (f" {extra_style}" if extra_style else "")
    return (
        f'    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"{bg} style="{style}">\n'
        f"{rows}    </table>"
    )


# ── Blocks ─────────────────────────────────────────────────────────────────
# Each builder returns the <tr> rows for one block of the email-container
# table. `top` is the block's top padding: the first block sits 28px under the
# status bar, as in the template; later blocks use the pattern's 20px.


def _block_text(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    paragraphs = _string_list(block.get("paragraphs", block.get("text")), "paragraphs", where)
    bullets = _string_list(block.get("bullets"), "bullets", where)
    if not paragraphs and not bullets:
        raise RenderError(f"{where}: give 'paragraphs' (or 'text') and/or 'bullets'.")
    rows = ""
    if _clean(block.get("heading")):
        rows += f'      <tr>\n        <td style="padding:0 0 8px 0;">{_KICKER_H2.format(slots.text(block["heading"]))}</td>\n      </tr>\n'
    for paragraph in paragraphs:
        rows += f'      <tr>\n        <td style="{_BODY_TEXT} padding:0 0 12px 0;">{slots.text(paragraph)}</td>\n      </tr>\n'
    if bullets:
        items = "".join(
            "          <tr>\n"
            f'            <td width="18" valign="top" style="{_BODY_TEXT} padding:0 0 6px 0;">&bull;</td>\n'
            f'            <td valign="top" style="{_BODY_TEXT} padding:0 0 6px 0;">{slots.text(item)}</td>\n'
            "          </tr>\n"
            for item in bullets
        )
        rows += (
            '      <tr>\n        <td style="padding:0 0 6px 0;">\n'
            '          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%; border-collapse:collapse;">\n'
            f"{items}          </table>\n        </td>\n      </tr>\n"
        )
    return _row(f"{top}px 28px 0 28px", _layout_table(rows))


def _block_metrics(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    items = _require(block, "items", where)
    lines = []
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise RenderError(f"{where}: items[{i}] must be an object with 'label' and 'value'.")
        label = _require(item, "label", f"{where} items[{i}]")
        value = _require(item, "value", f"{where} items[{i}]")
        lines.append(f"<strong>{slots.text(label)}:</strong> {slots.text(value)}")
    heading = slots.text(block.get("heading") or "Key Metrics")
    rows = (
        f'      <tr>\n        <td style="padding:16px 18px 6px 18px;">{_KICKER_H2.format(heading)}</td>\n      </tr>\n'
        f'      <tr>\n        <td style="padding:0 18px 16px 18px; {_BODY_TEXT}">\n'
        + "<br>\n".join(f"          {line}" for line in lines)
        + "\n        </td>\n      </tr>\n"
    )
    return _row(
        f"{top}px 28px 0 28px", _layout_table(rows, "background-color:#F4F6FB; border:1px solid #D7DEEE;", "#F4F6FB")
    )


def _block_headline_metrics(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    items = _require(block, "items", where)
    if len(items) > 5:
        raise RenderError(
            f"{where}: headline_metrics holds 3-5 KPIs; got {len(items)}. Move the rest to a 'metrics' block or a table."
        )
    kpis = ""
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise RenderError(f"{where}: items[{i}] must be an object with 'label' and 'value'.")
        label = _require(item, "label", f"{where} items[{i}]")
        value = _require(item, "value", f"{where} items[{i}]")
        trend = _clean(item.get("trend") or "neutral").lower()
        if trend not in _DELTA_COLORS:
            raise RenderError(f"{where} items[{i}]: trend must be one of {', '.join(_DELTA_COLORS)}.")
        delta = slots.text(item.get("delta") or "")
        kpis += (
            "              <tr>\n"
            f'                <td style="padding:6px 0; font-size:14px; line-height:20px; color:#586F7C; width:45%;">{slots.text(label)}</td>\n'
            f'                <td style="padding:6px 0; font-size:22px; line-height:28px; font-weight:700; color:#141824; text-align:right;">{slots.text(value)}</td>\n'
            '                <td style="padding:6px 0 6px 8px; font-size:14px; line-height:20px; font-weight:700; text-align:left; width:25%;">\n'
            f'                  <span style="color:{_DELTA_COLORS[trend]};">{delta}</span>\n'
            "                </td>\n"
            "              </tr>\n"
        )
    heading = slots.text(block.get("heading") or "Headline Metrics")
    rows = (
        f'      <tr>\n        <td style="padding:16px 18px 8px 18px;">\n          {_KICKER_H2.format(heading)}\n        </td>\n      </tr>\n'
        '      <tr>\n        <td style="padding:0 18px 4px 18px;">\n'
        '          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">\n'
        f"{kpis}          </table>\n        </td>\n      </tr>\n"
    )
    return _row(
        f"{top}px 28px 0 28px", _layout_table(rows, "background-color:#F4F6FB; border:1px solid #D7DEEE;", "#F4F6FB")
    )


_CALLOUTS = {
    "risk": ("Risk", "#FFF5E5", "#FFB86C", "#F5DDB5", "#8C5A18"),
    "opportunity": ("Opportunity", "#EFFAF3", "#50FA7B", "#CDEDD8", "#1C5A33"),
}


def _block_callout(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    kind = _clean(block.get("kind")).lower()
    if kind not in _CALLOUTS:
        raise RenderError(f"{where}: callout 'kind' must be 'risk' or 'opportunity'.")
    default_title, bg, accent, border, title_color = _CALLOUTS[kind]
    paragraphs = _string_list(block.get("paragraphs", block.get("text")), "text", where)
    if not paragraphs:
        raise RenderError(f"{where}: 'text' is required.")
    body = "<br><br>".join(slots.text(p) for p in paragraphs)
    title = slots.text(block.get("title") or default_title)
    rows = (
        f'      <tr>\n        <td style="padding:14px 16px 6px 16px;"><h2 style="margin:0; font-size:16px; line-height:24px; font-weight:700; color:{title_color};">{title}</h2></td>\n      </tr>\n'
        f'      <tr>\n        <td style="padding:0 16px 14px 16px; {_BODY_TEXT}">{body}</td>\n      </tr>\n'
    )
    borders = (
        f"background-color:{bg}; border-left:4px solid {accent}; border-top:1px solid {border}; "
        f"border-right:1px solid {border}; border-bottom:1px solid {border};"
    )
    return _row(f"{top}px 28px 0 28px", _layout_table(rows, borders, bg))


def _column_widths(columns: list[Mapping[str, Any]], where: str) -> list[int]:
    given = [col.get("width") for col in columns]
    for i, width in enumerate(given):
        if width is not None and (isinstance(width, bool) or not isinstance(width, int) or width <= 0):
            raise RenderError(f"{where}: columns[{i}].width must be a positive whole percentage.")
    missing = [i for i, w in enumerate(given) if w is None]
    remaining = 100 - sum(w for w in given if w is not None)
    if not missing:
        if remaining != 0:
            raise RenderError(f"{where}: column widths must sum to 100; they sum to {100 - remaining}.")
        return [int(w) for w in given]
    if remaining < len(missing):
        raise RenderError(f"{where}: the given column widths leave {remaining}% for {len(missing)} more columns.")
    widths = list(given)
    if len(columns) > 1 and missing[0] == 0 and len(missing) == len(columns):
        # Nothing given: the dimension column gets the room it needs for names.
        widths[0] = 40 if len(columns) >= 3 else 50
        remaining -= widths[0]
        missing = missing[1:]
    share, extra = divmod(remaining, len(missing))
    for n, i in enumerate(missing):
        widths[i] = share + (1 if n < extra else 0)
    return [int(w) for w in widths]


def _table_cell(cell: Any, label: str, align: str, bg: str, first: bool, slots: _Slots, where: str) -> str:
    classes = ' class="campaign-name"' if first else ""
    align_attr = "" if align == "left" else f' align="{align}"'
    color, weight, label_extra = "#141824", "", ""
    if isinstance(cell, Mapping) and "share" in cell:
        try:
            pct = float(cell["share"])
        except (TypeError, ValueError):
            raise RenderError(f"{where}: 'share' must be a number between 0 and 100.") from None
        if not 0 <= pct <= 100:
            raise RenderError(f"{where}: 'share' must be between 0 and 100; got {pct}.")
        content = (
            _MOBILE_LABEL.format(extra="", label=label)
            + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%; border-collapse:collapse;"><tr>'
            '<td bgcolor="#E6EBF5" style="background-color:#E6EBF5; height:8px; line-height:8px; font-size:0;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;" width="{round(pct)}%"><tr>'
            '<td bgcolor="#7272AB" style="background-color:#7272AB; height:8px; line-height:8px; font-size:0;">&nbsp;</td>'
            "</tr></table></td></tr></table>"
            f'<div style="font-size:12px; line-height:16px; color:#586F7C; margin-top:4px;">{pct:.1f}%</div>'
        )
    else:
        if isinstance(cell, Mapping):
            trend = _clean(cell.get("trend")).lower()
            if trend:
                if trend not in _DELTA_COLORS:
                    raise RenderError(f"{where}: trend must be one of {', '.join(_DELTA_COLORS)}.")
                color, weight, label_extra = _DELTA_COLORS[trend], " font-weight:700;", " color:#586F7C;"
            text = slots.text(cell.get("text", ""))
        else:
            text = slots.text(cell)
        content = _MOBILE_LABEL.format(extra=label_extra, label=label) + text
    return (
        f'          <td{classes}{align_attr} bgcolor="{bg}" style="background-color:{bg}; color:{color}; '
        f'font-size:15px; line-height:22px;{weight} padding:10px 8px; word-break:break-word;">{content}</td>\n'
    )


def _block_table(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    raw_columns = _require(block, "columns", where)
    columns = [col if isinstance(col, Mapping) else {"label": col} for col in raw_columns]
    if len(columns) > MAX_TABLE_COLUMNS:
        raise RenderError(
            f"{where}: {len(columns)} columns; the 600px layout holds at most {MAX_TABLE_COLUMNS}. "
            "Split it into two tables (e.g. Performance and Efficiency) or drop the least useful column."
        )
    labels = []
    for i, col in enumerate(columns):
        labels.append(slots.text(_require(col, "label", f"{where} columns[{i}]")))
    aligns = []
    for i, col in enumerate(columns):
        align = _clean(col.get("align") or ("left" if i == 0 else "right")).lower()
        if align not in {"left", "right", "center"}:
            raise RenderError(f"{where}: columns[{i}].align must be left, right or center.")
        aligns.append(align)
    widths = _column_widths(columns, where)
    rows = block.get("rows") or []
    if not isinstance(rows, Sequence) or isinstance(rows, str) or not rows:
        raise RenderError(f"{where}: 'rows' must be a non-empty list of rows, each a list of cells.")

    head = "".join(
        f'          <th scope="col" align="{align}" width="{width}%" bgcolor="#EEF3FF" style="background-color:#EEF3FF; '
        f'color:#33415F; font-size:13px; line-height:18px; font-weight:700; padding:10px 8px; word-break:break-word;">{label}</th>\n'
        for label, align, width in zip(labels, aligns, widths, strict=True)
    )
    body = ""
    for r, row in enumerate(rows):
        if not isinstance(row, Sequence) or isinstance(row, str) or len(row) != len(columns):
            raise RenderError(f"{where}: rows[{r}] must be a list of {len(columns)} cells, one per column.")
        bg = _ZEBRA[r % 2]
        cells = "".join(
            _table_cell(cell, label, align, bg, c == 0, slots, f"{where} rows[{r}][{c}]")
            for c, (cell, label, align) in enumerate(zip(row, labels, aligns, strict=True))
        )
        body += f"        <tr>\n{cells}        </tr>\n"

    heading_row = ""
    if _clean(block.get("heading")):
        heading_row = f'<tr>\n  <td class="body-pad" style="padding:{top}px 28px 0 28px;">{_KICKER_H2.format(slots.text(block["heading"]))}</td>\n</tr>\n'
        table_top = 8
    else:
        table_top = top
    return (
        heading_row + "<tr>\n"
        f'  <td class="table-pad" style="padding:{table_top}px 28px 0 28px;">\n'
        '    <table class="data-table" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%; border-collapse:collapse; border:1px solid #D7DEEE; table-layout:fixed;">\n'
        f"      <thead>\n        <tr>\n{head}        </tr>\n"
        f'        <tr>\n          <td colspan="{len(columns)}" height="1" bgcolor="#D7DEEE" style="height:1px; line-height:1px; '
        'mso-line-height-rule:exactly; font-size:1px; background-color:#D7DEEE; padding:0;">&nbsp;</td>\n        </tr>\n'
        f"      </thead>\n      <tbody>\n{body}      </tbody>\n    </table>\n  </td>\n</tr>\n"
    )


def _block_chart(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    cid = _clean(_require(block, "cid", where))
    if cid.lower().startswith("cid:"):
        cid = cid[4:]
    if not _CID.fullmatch(cid):
        raise RenderError(f"{where}: 'cid' may use only letters, digits and . _ @ - (got {cid!r}).")
    alt = _require(block, "alt", where)
    rows = ""
    if _clean(block.get("heading")):
        rows += f'      <tr>\n        <td style="padding:0 0 8px 0;">{_KICKER_H2.format(slots.text(block["heading"]))}</td>\n      </tr>\n'
    rows += (
        '      <tr>\n        <td style="padding:0 0 12px 0; text-align:center;">\n'
        f'          <img src="cid:{slots.attr(cid)}" alt="{slots.attr(alt)}" width="544" style="display:block; width:544px; '
        'max-width:100%; height:auto; border:1px solid #D7DEEE; margin:0 auto;">\n'
        "        </td>\n      </tr>\n"
    )
    if _clean(block.get("caption")):
        rows += f'      <tr>\n        <td style="font-size:12px; line-height:17px; color:#586F7C; padding:0 0 4px 0;">{slots.text(block["caption"])}</td>\n      </tr>\n'
    return _row(f"{top}px 28px 0 28px", _layout_table(rows))


def _block_cta(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    url = _clean(_require(block, "url", where))
    if not url.lower().startswith("https://") or any(ch.isspace() for ch in url):
        raise RenderError(f"{where}: 'url' must be a real https:// link the user or workflow gave you.")
    text = _clean(_require(block, "text", where))
    if len(text) > MAX_CTA_TEXT:
        raise RenderError(f"{where}: button text is {len(text)} characters; keep it to {MAX_CTA_TEXT}.")
    href, label = slots.attr(url), slots.text(text)
    return (
        "<tr>\n"
        f'  <td style="padding:{max(top, 24)}px 28px 0 28px;" align="center">\n'
        "    <!--[if mso]>\n"
        f'    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{href}" style="height:44px; v-text-anchor:middle; width:220px;" arcsize="0%" fillcolor="#6262A0" stroke="f">\n'
        '      <v:textbox inset="0,0,0,0">\n'
        f"        <center style=\"font-family:'Segoe UI', Arial, sans-serif; font-size:14px; font-weight:700; color:#FAFAFA;\">{label}</center>\n"
        "      </v:textbox>\n"
        "    </v:roundrect>\n"
        "    <![endif]-->\n"
        "    <!--[if !mso]><!-->\n"
        '    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">\n'
        "      <tr>\n"
        '        <td align="center" bgcolor="#6262A0" style="background-color:#6262A0; padding:12px 32px; border:1px solid #6262A0;">\n'
        f'          <a href="{href}" target="_blank" style="font-family:\'Segoe UI\', Arial, sans-serif; font-size:16px; line-height:20px; font-weight:700; color:#FAFAFA; text-decoration:none; display:inline-block;">{label}</a>\n'
        "        </td>\n"
        "      </tr>\n"
        "    </table>\n"
        "    <!--<![endif]-->\n"
        "  </td>\n"
        "</tr>\n"
    )


def _block_closing(block: Mapping[str, Any], slots: _Slots, top: int, where: str) -> str:
    paragraphs = _string_list(block.get("paragraphs", block.get("text")), "text", where)
    if not paragraphs:
        raise RenderError(f"{where}: 'text' is required.")
    heading = ""
    if _clean(block.get("heading")):
        heading = (
            '<h2 style="margin:0 0 8px 0; font-size:16px; line-height:24px; font-weight:700; color:#33415F;">'
            f"{slots.text(block['heading'])}</h2>"
        )
    body = "<br><br>".join(slots.text(p) for p in paragraphs)
    rows = f'      <tr>\n        <td style="font-size:16px; line-height:24px; color:#33415F;">{heading}{body}</td>\n      </tr>\n'
    return _row(f"{max(top, 24)}px 28px 28px 28px", _layout_table(rows))


_BUILDERS: dict[str, Callable[[Mapping[str, Any], _Slots, int, str], str]] = {
    "text": _block_text,
    "metrics": _block_metrics,
    "headline_metrics": _block_headline_metrics,
    "callout": _block_callout,
    "table": _block_table,
    "chart": _block_chart,
    "cta": _block_cta,
    "closing": _block_closing,
}
BLOCK_TYPES = tuple(_BUILDERS)

# Bottom breathing room when the last block is not a closing (which carries its
# own 28px), so the footer hairline never butts against content.
_SPACER = '<tr>\n  <td height="28" style="height:28px; line-height:28px; mso-line-height-rule:exactly; font-size:1px; padding:0;">&nbsp;</td>\n</tr>\n'


def _indent(markup: str, spaces: int) -> str:
    pad = " " * spaces
    return "".join(pad + line if line.strip() else line for line in markup.splitlines(keepends=True))


def _apply_theme(markup: str, overrides: Mapping[str, str]) -> str:
    """One-pass hex find-replace, so a mapping chain (A->B, B->C) never cascades."""
    if not overrides:
        return markup
    return _HEX.sub(lambda m: overrides.get(m.group(0).upper(), m.group(0)), markup)


def _first_text(blocks: Sequence[Mapping[str, Any]]) -> str:
    for block in blocks:
        for key in ("paragraphs", "text", "bullets"):
            value = block.get(key)
            items = [value] if isinstance(value, str) else value if isinstance(value, Sequence) else []
            for item in items:
                if _plain(item):
                    return _plain(item)
    return ""


def _preheader(explicit: Any, blocks: Sequence[Mapping[str, Any]], subject: str) -> str:
    text = _plain(explicit) or _first_text(blocks) or subject
    if len(text) <= 100:
        return text
    cut = text[:97].rsplit(" ", 1)[0].rstrip(",;:.")
    return f"{cut}..."


def render_email(
    *,
    subject: str,
    blocks: Sequence[Mapping[str, Any]],
    theme: str | None = None,
    client_name: str | None = None,
    date: str | None = None,
    date_range: str | None = None,
    badge_label: str | None = None,
    status: str | None = None,
    preheader: str | None = None,
    logo: bool = True,
) -> str:
    """Render a complete, themed HTML email. Raises RenderError on bad input.

    With `logo` (the default) and a bundle logo present, the header shows it
    as cid:brand-logo; the caller must attach brand_logo()'s file under that
    cid. The subject is the document <title> only: the inbox already shows
    it, so the header leads with the client instead.
    """
    subject_text = _clean(subject)
    if not subject_text:
        raise RenderError("'subject' is required.")
    if not isinstance(blocks, Sequence) or isinstance(blocks, str) or not blocks:
        raise RenderError(f"'blocks' must be a non-empty list. Block types: {', '.join(BLOCK_TYPES)}.")
    client = _clean(client_name) or os.environ.get("SES_TENANT_LABEL", "").strip()
    if not client:
        raise RenderError("'client_name' is required (no SES_TENANT_LABEL is configured).")
    overrides = _theme_overrides(_clean(theme) or default_theme())

    slots = _Slots()
    body = ""
    for i, block in enumerate(blocks):
        where = f"blocks[{i}]"
        if not isinstance(block, Mapping):
            raise RenderError(f"{where} must be an object with a 'type'.")
        kind = _clean(block.get("type")).lower()
        builder = _BUILDERS.get(kind)
        if builder is None:
            raise RenderError(f"{where}: unknown type {block.get('type')!r}. Block types: {', '.join(BLOCK_TYPES)}.")
        body += builder(block, slots, 28 if i == 0 else 20, f"{where} ({kind})")
    if _clean(blocks[-1].get("type")).lower() != "closing":
        body += _SPACER

    head, status_bar, footer = _chrome()
    show_status = bool(_clean(badge_label) or _clean(status))
    if show_status and not (_clean(badge_label) and _clean(status)):
        raise RenderError("Give both 'badge_label' and 'status' for the status strip, or neither to omit it.")
    if not _clean(date_range):
        head, removed = _META_ROW.subn("", head)
        if removed != 1:
            raise RuntimeError("canonical_template header has no header-meta row")
    head = _header_logo(head, brand_logo() if logo else None)
    values = {
        "preheader_text": slots.text(_preheader(preheader, blocks, subject_text)),
        "date": slots.text(_clean(date) or _dt.datetime.now(_dt.UTC).date().isoformat()),
        "client_name": slots.text(client),
        "date_range": slots.text(date_range),
        "badge_label": slots.text(badge_label),
        "status": slots.text(status),
    }
    document = head + (status_bar if show_status else "") + _indent(body, 14) + "\n" + footer
    document = re.sub(r"<title>.*?</title>", f"<title>{slots.attr(subject_text)}</title>", document, count=1)

    def _token(match: re.Match[str]) -> str:
        if match.group(1) not in values:
            raise RuntimeError(f"canonical_template has an unhandled placeholder {{{{{match.group(1)}}}}}")
        return values[match.group(1)]

    document = _TEMPLATE_TOKEN.sub(_token, document)
    document = _PLAIN_COMMENT.sub("", document)
    document = _DATA_PREVIEW.sub("", document)
    document = _apply_theme(document, overrides)
    return slots.fill(document)

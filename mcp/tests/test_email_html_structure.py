"""Shipped canonical_template + components pass the Outlook rules.

2026-09-21: a scheduled weekly report burned through its 10M-token
ceiling regenerating the canonical template three times to silence
EO001/EO002 — warnings the TEMPLATE ITSELF produced (CSS-only cell fills,
single-side td borders on the header, status bar, table cells and
footer). The shipped template and every component block must produce
zero EO findings, so a warning always means the agent's copy drifted
and never that the reference markup is wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from email_lint import validate

BUNDLE_ROOT = Path(__file__).resolve().parents[2]

_EMAIL_STYLE_YAML = BUNDLE_ROOT / "protocols" / "email-style.yaml"
_TEMPLATE_TOKEN = re.compile(r"\{\{[^}]+\}\}")


def _shipped_block_html(block: str) -> str:
    spec = yaml.safe_load(_EMAIL_STYLE_YAML.read_text(encoding="utf-8"))
    html = _TEMPLATE_TOKEN.sub("x", spec[block])
    if block == "canonical_template":
        return html
    # Component blocks are fragments meant to sit inside the template; wrap
    # them the way the rendering steps splice them in: headline_metrics_html
    # is a <tr> for the container table, share_of_total_bar_html is the
    # inner content of a data-table cell.
    if block == "share_of_total_bar_html":
        html = f"<tr><td>{html}</td></tr>"
    return f'<html><body><table role="presentation" width="100%">{html}</table></body></html>'


@pytest.mark.parametrize("block", ["canonical_template", "headline_metrics_html", "share_of_total_bar_html"])
def test_shipped_template_blocks_pass_outlook_rules(block: str) -> None:
    findings = validate(_shipped_block_html(block), content_type="text/html")
    # The reference markup carries demo prose and data-preview markers on
    # purpose (EL1xx template-leakage rules fire until the agent replaces
    # them); every OTHER rule — the Outlook pair, and the HTML-structure
    # rules such as an inconsistent column count from a misplaced row —
    # must stay silent on what we ship.
    structural = [f for f in findings if not f.rule.startswith("EL1")]
    assert structural == [], f"shipped {block} must lint clean apart from demo-leakage, got:\n  - " + "\n  - ".join(
        f.format() for f in structural
    )


def test_canonical_template_keeps_filled_stripe_and_dividers() -> None:
    # The accent stripe and hairlines used to be single-side borders on the
    # header/status/footer cells; they are filled rows now. Pin the shape so
    # a future template refresh does not quietly reintroduce the borders.
    html = _shipped_block_html("canonical_template")
    assert 'height="4" bgcolor="#7272AB"' in html
    assert html.count('height="1" bgcolor="#D7DEEE"') == 4
    # Every filled stripe/hairline cell forces its height in Word, which
    # otherwise treats line-height as a minimum and fattens 1px rows.
    for cell in re.findall(r'<td[^>]*height="[14]"[^>]*>', html):
        assert "mso-line-height-rule:exactly" in cell, cell
    # The header/body separator lives inside <thead> (hidden with the header
    # on mobile), so a theme that maps the header and first-row fills to the
    # same hex still gets a visible boundary.
    thead = html[html.index("<thead>") : html.index("</thead>")]
    assert 'colspan="4" height="1" bgcolor="#D7DEEE"' in thead
    # The lower status-bar hairline must sit in the OUTER container column,
    # not inside the two-cell badge table (Codex on #159: there it renders
    # as a one-cell row under a two-cell row and only spans the badge).
    below = html.index("Hairline below the status bar")
    assert html.rindex("</table>", 0, below) > html.rindex('class="status-pad"', 0, below)
    assert "border-top:4px" not in html
    assert not re.search(r"<(td|th)[^>]*border-(top|bottom|left|right):", html)


def test_protocol_says_to_remove_data_preview_attributes() -> None:
    """EL101 is an error, and the header cells copied verbatim carry data-preview.

    Without an explicit instruction, an email built exactly as the protocol says
    is refused by send_email on its first try.
    """
    doc = yaml.safe_load(_EMAIL_STYLE_YAML.read_text(encoding="utf-8"))
    build = " ".join(doc["rendering"]["build"])
    assert "data-preview" in build
    assert any("data-preview" in rule for rule in doc["rules"])


def test_template_without_data_preview_has_no_el101() -> None:
    template = yaml.safe_load(_EMAIL_STYLE_YAML.read_text(encoding="utf-8"))["canonical_template"]
    stripped = re.sub(r'\s+data-preview="[^"]*"', "", template)
    assert "data-preview" not in stripped
    assert not [f for f in validate(stripped, content_type="text/html") if f.rule == "EL101"]

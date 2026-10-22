"""Tests for the deterministic email renderer and the render_email tool.

The renderer replaces the agent copying canonical_template by hand, so these
tests hold it to the same bar the protocol sets for a hand-built email: every
block lints clean, caller text is escaped and never recolored, and the markup
does not drift from protocols/email-style.yaml.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import email_render
import pytest
import yaml
from email_lint import partition_findings, validate
from ses_sender import render_email, send_email

PROTOCOL = yaml.safe_load(email_render.PROTOCOL_PATH.read_text(encoding="utf-8"))
MANIFEST = yaml.safe_load((email_render.BUNDLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
# This deployment's client name, as the sender's manifest entry sets it.
CLIENT = next(s for s in MANIFEST["mcp_servers"] if s["name"] == "ses_outbound")["env"]["SES_TENANT_LABEL"]
DEFAULT_THEME = PROTOCOL["themes"]["default"]
BRANDED_THEMES = [t for t in email_render.available_themes() if email_render._theme_overrides(t)]
STYLE_ATTR = re.compile(r'style="([^"]*)"')

EVERY_BLOCK: list[dict[str, Any]] = [
    {
        "type": "text",
        "heading": "Summary",
        "paragraphs": ["Spend finished at **$12,340.56**, up 4.0% week over week.", "Second paragraph."],
        "bullets": ["Video pacing recovered", "Display CPMs held flat"],
    },
    {
        "type": "headline_metrics",
        "heading": "Topline",
        "items": [
            {"label": "Spend", "value": "$12,340.56", "delta": "+4.0%", "trend": "positive"},
            {"label": "CPM", "value": "$9.10", "delta": "-1.2%", "trend": "negative"},
            {"label": "Impressions", "value": "1.36M"},
        ],
    },
    {
        "type": "metrics",
        "items": [{"label": "Impressions", "value": "1,356,221"}, {"label": "Clicks", "value": "2,114"}],
    },
    {"type": "callout", "kind": "risk", "text": "Weekend delivery dipped 18% on display."},
    {"type": "callout", "kind": "opportunity", "title": "Upside", "text": "CA inventory stayed efficient."},
    {
        "type": "table",
        "heading": "By channel",
        "columns": ["Channel", "Spend", "Share of Total", {"label": "WoW", "align": "right"}],
        "rows": [
            ["Video", "$7,850.00", {"share": 63.6}, {"text": "+6.1%", "trend": "positive"}],
            ["Display", "$4,490.56", {"share": 36.4}, {"text": "-2.3%", "trend": "negative"}],
        ],
    },
    {"type": "chart", "heading": "Trend", "cid": "spend_chart", "alt": "Daily spend", "caption": "Last 7 days"},
    {"type": "cta", "text": "Open dashboard", "url": "https://example.com/dash?a=1&b=2"},
    {"type": "closing", "heading": "Next steps", "text": ["Hold video budgets.", "— Victoria\nClient team"]},
]


def _render(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "subject": "Weekly delivery review",
        "client_name": CLIENT,
        "blocks": [{"type": "text", "paragraphs": ["Hi team, this is a test."]}],
    }
    kwargs.update(overrides)
    return email_render.render_email(**kwargs)


def _findings(markup: str, subject: str = "Weekly delivery review"):
    return partition_findings(validate(markup, subject=subject, content_type="text/html"))


# ── Lint-clean output ──────────────────────────────────────────────────────


@pytest.mark.parametrize("theme", email_render.available_themes())
def test_every_block_lints_clean_in_every_theme(theme: str) -> None:
    markup = _render(
        blocks=EVERY_BLOCK, theme=theme, date_range="Sep 29 - Oct 5", badge_label="On track", status="Pacing 98%"
    )
    errors, warnings = _findings(markup)
    assert errors == []
    assert warnings == []


@pytest.mark.parametrize("block", EVERY_BLOCK, ids=lambda b: b["type"])
def test_each_block_alone_lints_clean(block: dict[str, Any]) -> None:
    errors, warnings = _findings(_render(blocks=[block]))
    assert errors == []
    assert warnings == []


def test_short_note_is_small_and_clean() -> None:
    markup = _render()
    assert _findings(markup) == ([], [])
    # The point of the renderer: a two-line note no longer costs a full
    # template's worth of model output. Generous ceiling, not a benchmark.
    assert len(markup) < 10_000


def test_cleanup_matches_the_protocol_rules() -> None:
    markup = _render(blocks=EVERY_BLOCK)
    assert "{{" not in markup
    assert "data-preview" not in markup
    assert "Email-Safe Theme Reference" not in markup
    # Informational comments are stripped; Outlook conditionals are functional and stay.
    comments = re.findall(r"<!--.*?-->", markup, re.DOTALL)
    assert comments
    assert all(c.startswith(("<!--[if", "<!--<![endif]")) or c.endswith("<![endif]-->") for c in comments)
    assert "<!--[if !mso]><!-->" in markup
    assert '<a href="https://example.com/dash?a=1&amp;b=2"' in markup


# ── Escaping ───────────────────────────────────────────────────────────────


def test_caller_values_are_escaped() -> None:
    markup = _render(
        subject='Q3 <b>review</b> & "notes"',
        blocks=[
            {"type": "text", "heading": "<img src=x>", "paragraphs": ['<script>alert("x")</script>']},
            {"type": "table", "columns": ["<i>Channel</i>"], "rows": [["</td></tr></table>"]]},
        ],
    )
    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup
    assert "<img src=x>" not in markup
    assert "<i>Channel</i>" not in markup
    assert "&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;" in markup
    assert "Q3 &lt;b&gt;review&lt;/b&gt; &amp; &quot;notes&quot;" in markup


def test_bold_and_line_breaks_are_the_only_markup() -> None:
    markup = _render(blocks=[{"type": "text", "paragraphs": ["Spend was **$5.00**.\n— Victoria"]}])
    assert "Spend was <strong>$5.00</strong>.<br>— Victoria" in markup


@pytest.mark.parametrize("theme", BRANDED_THEMES)
def test_caller_text_is_never_recolored_by_the_theme(theme: str) -> None:
    canonical = next(iter(email_render._theme_overrides(theme)))
    markup = _render(theme=theme, blocks=[{"type": "text", "paragraphs": [f"Brand hex {canonical} stays as typed."]}])
    assert f"Brand hex {canonical} stays as typed." in markup
    assert f'bgcolor="{canonical}"' not in markup


def test_values_cannot_inject_slot_markers() -> None:
    markup = _render(blocks=[{"type": "text", "paragraphs": ["before \x000\x00 after"]}])
    assert "before 0 after" in markup


# ── Header and theme ───────────────────────────────────────────────────────


@pytest.mark.parametrize("theme", BRANDED_THEMES)
def test_branded_theme_replaces_every_overridden_hex(theme: str) -> None:
    overrides = email_render._theme_overrides(theme)
    markup = _render(theme=theme, blocks=EVERY_BLOCK, badge_label="On track", status="Pacing")
    present = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", markup)}
    assert present.isdisjoint(overrides)


def test_default_theme_is_the_protocol_default() -> None:
    assert email_render.default_theme() == DEFAULT_THEME
    assert _render() == _render(theme=DEFAULT_THEME)


def test_unknown_theme_lists_the_available_ones() -> None:
    with pytest.raises(email_render.RenderError, match=DEFAULT_THEME):
        _render(theme="nope")


def test_status_strip_is_optional_but_needs_both_fields() -> None:
    assert "Status:" not in _render()
    assert "Status: Pacing 98%" in _render(badge_label="On track", status="Pacing 98%")
    with pytest.raises(email_render.RenderError, match="both"):
        _render(badge_label="On track")


def test_header_meta_without_date_range_has_no_dangling_separator() -> None:
    assert f"{CLIENT} | " not in _render()
    assert f"{CLIENT} | Sep 29 - Oct 5" in _render(date_range="Sep 29 - Oct 5")


def test_client_name_defaults_to_the_tenant_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SES_TENANT_LABEL", CLIENT)
    assert f">{CLIENT}</td>" in _render(client_name=None)
    monkeypatch.delenv("SES_TENANT_LABEL")
    with pytest.raises(email_render.RenderError, match="client_name"):
        _render(client_name=None)


def test_preheader_defaults_to_the_first_paragraph() -> None:
    long = "word " * 40
    markup = _render(blocks=[{"type": "text", "paragraphs": [long]}])
    preheader = re.search(r"overflow:hidden;\">\s*(.*?) &zwnj;", markup).group(1)
    assert preheader.startswith("word word")
    assert len(preheader) <= 100
    assert "Explicit preview" in _render(preheader="Explicit preview")


# ── Block validation ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([], "non-empty"),
        ([{"type": "poem", "text": "x"}], "unknown type"),
        ([{"type": "text"}], "paragraphs"),
        ([{"type": "callout", "kind": "info", "text": "x"}], "risk"),
        ([{"type": "table", "columns": ["A", "B", "C", "D", "E", "F"], "rows": [list("abcdef")]}], "at most 5"),
        ([{"type": "table", "columns": ["A", "B"], "rows": [["only one"]]}], "2 cells"),
        (
            [
                {
                    "type": "table",
                    "columns": [{"label": "A", "width": 60}, {"label": "B", "width": 30}],
                    "rows": [["a", "b"]],
                }
            ],
            "sum to 100",
        ),
        ([{"type": "table", "columns": ["A", "B"], "rows": [["a", {"share": 140}]]}], "between 0 and 100"),
        ([{"type": "table", "columns": ["A", "B"], "rows": [["a", {"text": "x", "trend": "up"}]]}], "trend"),
        ([{"type": "cta", "text": "Go", "url": "http://example.com"}], "https://"),
        ([{"type": "cta", "text": "x" * 26, "url": "https://example.com"}], "25"),
        ([{"type": "chart", "cid": "bad cid!", "alt": "x"}], "cid"),
        ([{"type": "headline_metrics", "items": [{"label": str(i), "value": "1"} for i in range(6)]}], "3-5"),
    ],
)
def test_invalid_blocks_say_what_to_fix(blocks: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(email_render.RenderError, match=message):
        _render(blocks=blocks)


def test_column_widths_default_and_fill_the_remainder() -> None:
    def widths(columns: list[Any]) -> list[int]:
        markup = _render(blocks=[{"type": "table", "columns": columns, "rows": [["x"] * len(columns)]}])
        return [int(w) for w in re.findall(r'<th scope="col" align="\w+" width="(\d+)%"', markup)]

    assert widths(["A"]) == [100]
    assert widths(["A", "B"]) == [50, 50]
    assert widths(["A", "B", "C", "D", "E"]) == [40, 15, 15, 15, 15]
    assert widths([{"label": "A", "width": 55}, "B", "C"]) == [55, 23, 22]


def test_table_mobile_labels_match_the_headers() -> None:
    markup = _render(blocks=[EVERY_BLOCK[5]])
    headers = re.findall(r"<th [^>]*>(.*?)</th>", markup)
    labels = re.findall(r'<span class="mobile-label"[^>]*>(.*?)</span>', markup)
    assert labels == headers * 2


# ── Drift from the protocol ────────────────────────────────────────────────

# Styles the renderer adds that the protocol has no pattern for, with the reason.
RENDERER_ONLY_STYLES = {
    # Bullet list rows: the protocol has no list pattern and forbids bare <ul>
    # between container rows, so bullets are a two-cell layout table.
    "font-size:16px; line-height:24px; color:#141824; padding:0 0 6px 0;",
    "padding:0 0 6px 0;",
    # Bottom spacer when the last block is not a closing, which carries its own 28px.
    "height:28px; line-height:28px; mso-line-height-rule:exactly; font-size:1px; padding:0;",
}


def _protocol_styles() -> set[str]:
    sources = [
        PROTOCOL["canonical_template"],
        PROTOCOL["headline_metrics_html"],
        PROTOCOL["chart_image_html"],
        PROTOCOL["share_of_total_bar_html"],
    ]
    return {style for source in sources for style in STYLE_ATTR.findall(source)}


def _normalize(style: str) -> str:
    # Padding/width numbers vary by position (first block 28px, later 20px;
    # computed column widths) and colors by row (zebra fill x trend color), so
    # compare the declarations here and the palette in the test below.
    style = re.sub(r"#[0-9A-Fa-f]{6}", "H", " ".join(style.split()))
    return re.sub(r"\d+(px|%)", "N", style)


def test_rendered_styles_come_from_the_protocol() -> None:
    markup = _render(theme="default", blocks=EVERY_BLOCK, date_range="Sep 29 - Oct 5", badge_label="On", status="Ok")
    allowed = {_normalize(s) for s in _protocol_styles() | RENDERER_ONLY_STYLES}
    # Non-first table cells carry word-break too: the protocol requires it on
    # any cell that can hold a long string, and the renderer cannot tell which.
    allowed |= {a.replace(" padding:N N;", " padding:N N; word-break:break-word;") for a in allowed}
    drifted = sorted({s for s in STYLE_ATTR.findall(markup) if _normalize(s) not in allowed})
    assert drifted == []


def test_rendered_colors_come_from_the_protocol() -> None:
    markup = _render(theme="default", blocks=EVERY_BLOCK, badge_label="On", status="Ok")
    sources = "".join(
        PROTOCOL[key]
        for key in ("canonical_template", "headline_metrics_html", "chart_image_html", "share_of_total_bar_html")
    )
    palette = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", sources)}
    assert {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", markup)} <= palette


def test_chrome_is_cut_verbatim_from_the_canonical_template() -> None:
    template = PROTOCOL["canonical_template"]
    css = re.search(r"<style>.*?</style>", template, re.DOTALL).group(0)
    markup = _render(theme="default", badge_label="On", status="Ok")
    assert css in markup
    assert "Victoria | Campaign Intelligence" in markup
    assert "Generated by Victoria" in markup
    assert markup.count("<!--[if mso]>") == template.count("<!--[if mso]>") - 1  # minus the CTA's own


# ── The MCP tool ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_render_email_tool_writes_a_file_send_email_accepts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SES_FROM_EMAIL", "victoria@example.com")
    monkeypatch.setenv("SES_TENANT_LABEL", CLIENT)
    monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)
    captured: dict[str, Any] = {}

    async def fake_send(request):
        captured.update(request)
        return {"status_code": 200, "message_id": "ses-message-id"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send)

    rendered = await render_email(
        subject="Test email", blocks=[{"type": "text", "paragraphs": ["Hi team."]}], output_dir=str(tmp_path)
    )
    assert rendered["valid"] is True, rendered
    assert rendered["errors"] == []
    path = Path(rendered["content_file"])
    assert path.parent == tmp_path
    assert path.name.startswith("email-") and path.suffix == ".html"

    sent = await send_email(to_email="alice@example.com", subject="Test email", content_file=str(path))
    assert sent["status"] == "queued", sent
    assert captured["Content"]["Simple"]["Body"]["Html"]["Data"] == path.read_text(encoding="utf-8")


def test_subject_headline_matches_the_sent_subject() -> None:
    markup = _render(subject="**Q3**  delivery <review>")
    assert "**Q3**  delivery &lt;review&gt;</h1>" in markup


@pytest.mark.asyncio
async def test_render_email_tool_file_is_owner_only(tmp_path: Path) -> None:
    rendered = await render_email(
        subject="Private", client_name=CLIENT, blocks=[{"type": "text", "text": "x"}], output_dir=str(tmp_path)
    )
    assert os.stat(rendered["content_file"]).st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_render_email_tool_reports_chart_cids(tmp_path: Path) -> None:
    rendered = await render_email(
        subject="Chart", client_name=CLIENT, blocks=[EVERY_BLOCK[6]], output_dir=str(tmp_path)
    )
    assert rendered["inline_cids"] == ["spend_chart"]


@pytest.mark.asyncio
async def test_render_email_tool_never_overwrites(tmp_path: Path) -> None:
    kwargs = {"subject": "Same", "client_name": CLIENT, "blocks": [{"type": "text", "text": "x"}]}
    first = await render_email(output_dir=str(tmp_path), **kwargs)
    second = await render_email(output_dir=str(tmp_path), **kwargs)
    assert first["content_file"] != second["content_file"]
    assert len(os.listdir(tmp_path)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output_dir", "message"),
    [("", "required"), ("/etc", "outside allowed"), ("{tmp}/missing", "not an existing directory")],
)
async def test_render_email_tool_rejects_bad_output_dirs(tmp_path: Path, output_dir: str, message: str) -> None:
    result = await render_email(
        subject="x",
        client_name=CLIENT,
        blocks=[{"type": "text", "text": "x"}],
        output_dir=output_dir.format(tmp=tmp_path),
    )
    assert message in result["error"]


@pytest.mark.asyncio
async def test_render_email_tool_returns_render_errors(tmp_path: Path) -> None:
    result = await render_email(subject="x", client_name=CLIENT, blocks=[{"type": "poem"}], output_dir=str(tmp_path))
    assert "unknown type" in result["error"]
    assert os.listdir(tmp_path) == []


def test_tenant_label_is_the_brand_name() -> None:
    # A fork that rebrands but forgets this label sends report emails headed
    # with the template's (or another client's) name.
    assert MANIFEST["branding"]["app_name"] == CLIENT


# The template ships only the unbranded theme, so BRANDED_THEMES can be empty
# and the two theme tests above would run zero cases. Exercise the renderer
# against a generated brand theme as well, whatever the bundle ships.
@pytest.fixture
def synthetic_theme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "make_palette_r", email_render.BUNDLE_ROOT / "assets" / "make-palette.py"
    )
    mp = importlib.util.module_from_spec(spec)
    sys.modules["make_palette_r"] = mp
    spec.loader.exec_module(mp)
    brand = mp.Brand(
        slug="synthetic", primary="#083340", secondary="#377A99", accent="#85B148", ink="#252525", paper="#F7F9F9"
    )
    _, theme_text, _ = mp.build(brand)
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "default.yaml").write_text((email_render.themes_dir() / "default.yaml").read_text(encoding="utf-8"))
    (themes / "synthetic.yaml").write_text(theme_text)
    monkeypatch.setattr(email_render, "themes_dir", lambda: themes)
    return "synthetic"


def test_synthetic_theme_replaces_every_overridden_hex(synthetic_theme: str) -> None:
    overrides = email_render._theme_overrides(synthetic_theme)
    assert overrides
    markup = _render(theme=synthetic_theme, blocks=EVERY_BLOCK, badge_label="On track", status="Pacing")
    present = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", markup)}
    assert present.isdisjoint(overrides)


def test_caller_text_is_never_recolored_by_a_synthetic_theme(synthetic_theme: str) -> None:
    canonical = next(iter(email_render._theme_overrides(synthetic_theme)))
    markup = _render(
        theme=synthetic_theme, blocks=[{"type": "text", "paragraphs": [f"Brand hex {canonical} stays as typed."]}]
    )
    assert f"Brand hex {canonical} stays as typed." in markup

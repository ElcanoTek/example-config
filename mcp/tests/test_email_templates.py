"""Tests for the email templates and the template check in render_email.

A template is a promise that a recurring email has the same sections at the
same length every time. This bundle ships none of its own (the team saves
theirs from a chat), so the template under test is the fixture in
email_template_fixture.py, placed where a bundle template would be. Any
template later added under email_templates/ is held to the same checks.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import email_render
import email_template_preview
import email_template_store
import pytest
import yaml
from email_lint import partition_findings, validate
from email_template_fixture import TEMPLATE_ID as OPT
from email_template_fixture import spec as fixture_spec
from ses_sender import get_email_template, list_email_templates, render_email

import email_templates

SHIPPED = email_templates.bundle_template_ids()


@pytest.fixture(autouse=True)
def _library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A bundle templates directory holding the fixture, with the standard form."""
    library = tmp_path / "email_templates"
    library.mkdir()
    raw = fixture_spec()
    raw["fields"], raw["promptTemplate"] = email_template_store.default_form(OPT, raw["name"])
    (library / f"{OPT}.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(email_templates, "TEMPLATES_DIR", library)
    monkeypatch.setenv("SES_TENANT_LABEL", "Acme")
    monkeypatch.setenv("CUTLASS_ALLOWED_DIRS", str(tmp_path))
    monkeypatch.delenv("EMAIL_TEMPLATES_TEAM_DIR", raising=False)
    return library


def _sample_blocks(template_id: str = OPT) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = email_templates.load_template(template_id)
    return spec, copy.deepcopy(spec["sample"]["blocks"])


def _recolor(page: str, table: dict[str, str]) -> str:
    return re.sub(r"#[0-9A-Fa-f]{6}\b", lambda m: table.get(m.group(0).upper(), m.group(0)), page)


def test_block_types_match_the_renderer() -> None:
    assert set(email_templates.BLOCK_TYPES) == set(email_render.BLOCK_TYPES)


def test_the_bundle_has_a_plain_theme() -> None:
    """Examples are rendered in the theme with no overrides; the recolor table starts from it."""
    plain = email_template_preview.plain_theme()
    assert plain and email_render._theme_overrides(plain) == {}


def test_shipped_templates_are_well_formed_and_their_previews_current(
    _library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is shipped today; a template added under email_templates/ must pass this."""
    monkeypatch.undo()
    monkeypatch.setenv("SES_TENANT_LABEL", "Acme")
    for template_id in SHIPPED:
        raw = yaml.safe_load((email_templates.TEMPLATES_DIR / f"{template_id}.yaml").read_text(encoding="utf-8"))
        assert email_templates.validate_spec(raw) == [], template_id
        assert ("fields" in raw) == ("promptTemplate" in raw), template_id
    assert email_template_preview.stale_previews() == [], "run: python mcp/email_template_preview.py"


def test_template_is_well_formed() -> None:
    assert email_templates.validate_spec(fixture_spec()) == []
    assert email_templates.load_template(OPT)["id"] == OPT


def test_standard_form_offers_recipients_this_bundles_themes_and_an_approval_card() -> None:
    fields, prompt = email_template_store.default_form(OPT, "Weekly status update")
    by_key = {f["key"]: f for f in fields}
    assert by_key["recipient"]["type"] == "recipients"
    style = by_key["style"]
    assert style["type"] == "palette"
    # Every theme this bundle has, its own default first and preselected.
    assert sorted(style["options"]) == email_render.available_themes()
    assert style["default"] == style["options"][0] == email_render.default_theme()
    assert set(style["swatches"]) == set(style["options"])
    for option in style["options"]:
        # Each swatch shows that theme's real header and accent colors.
        overrides = email_render._theme_overrides(option)
        header, accent = (overrides.get(c, c) for c in ("#1A0B1E", "#6262A0"))
        assert [c.upper() for c in style["swatches"][option]] == [header.upper(), accent.upper()]
    assert [label.lower() for label in style["custom"]] == list(email_render.CUSTOM_COLORS)
    assert "Send to: {recipient}" in prompt and "Style: {style}" in prompt
    # Straight to the approval card: no display-only preview, no "shall I send?" turn.
    assert "call mcp_ses_outbound_send_email straight away" in prompt
    assert "nothing goes out until I choose Send" in prompt and "Do not use preview_email" in prompt


def test_sample_obeys_its_template_and_lints_clean() -> None:
    spec, blocks = _sample_blocks()
    sample = spec["sample"]
    ready, violations = email_templates.apply_template(spec, sample["subject"], blocks)
    assert violations == []
    page = email_render.render_email(
        subject=sample["subject"],
        blocks=ready,
        client_name=sample.get("client_name"),
        date=sample.get("date"),
        date_range=sample.get("date_range"),
        badge_label=sample.get("badge_label"),
        status=sample.get("status"),
        headline=sample.get("headline"),
    )
    errors, _ = partition_findings(validate(page, subject=sample["subject"], content_type="text/html"))
    assert errors == []
    for section in spec["sections"]:
        if section.get("heading"):
            assert section["heading"] in page.replace("&#x27;", "'")


def test_recolored_preview_is_the_real_render() -> None:
    """The library recolors the stored example with the recolor table; for every
    look the Style picker offers, that must give exactly what render_email
    produces. Custom colors sit on top of the picker's default look, as they sit
    on top of the default theme in a real render."""
    spec = email_templates.load_template(OPT)
    styles = email_template_preview.preview_styles([OPT])
    stored = email_template_preview.render_preview(spec)
    assert sorted(styles["options"]) == email_render.available_themes()
    for option, table in styles["options"].items():
        assert _recolor(stored, table) == email_template_preview.render_preview(spec, theme=option), option
    assert {name.lower() for name in styles["custom"]} == set(email_render.CUSTOM_COLORS)
    default = email_render.default_theme()
    for picked in ({"Header": "#1F3A5F", "Accent": "#C2410C"}, {"Header": "#3B2A5C"}, {"Accent": "#0B5A44"}):
        table = dict(styles["options"][default])
        table.update({old: new for name, new in picked.items() for old in styles["custom"][name]})
        colors = {name.lower(): new for name, new in picked.items()}
        assert _recolor(stored, table) == email_template_preview.render_preview(spec, colors, default), picked
    # A theme named in the sample does not move the stored example off the plain palette.
    themed = copy.deepcopy(spec)
    themed["sample"]["theme"] = default
    assert email_template_preview.render_preview(themed) == stored


def test_preview_is_self_contained() -> None:
    page = email_template_preview.render_preview(email_templates.load_template(OPT))
    assert page.startswith("<!DOCTYPE html>")
    assert "cid:" not in page
    assert "<script" not in page.lower()


def test_describe_leaves_the_sample_out() -> None:
    described = email_templates.describe(email_templates.load_template(OPT))
    assert "sample" not in described
    assert "Outfitters" not in str({k: v for k, v in described.items() if k != "subject"})
    assert [s["section"] for s in described["sections"]][:2] == ["summary", "glance"]
    assert described["sections"][0]["limits"]["max_bullets"] == 3


def test_missing_section_and_wrong_order_are_reported_together() -> None:
    spec, blocks = _sample_blocks()
    blocks = [b for b in blocks if b["section"] != "watching"]
    blocks[0], blocks[1] = blocks[1], blocks[0]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    text = " ".join(violations)
    assert "'watching'" in text and "required and missing" in text
    assert "out of order" in text


def test_optional_section_may_be_left_out() -> None:
    spec, blocks = _sample_blocks()
    blocks = [b for b in blocks if b["section"] not in ("adjusted", "ask")]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    assert violations == []


def test_length_limits() -> None:
    spec, blocks = _sample_blocks()
    summary = blocks[0]
    summary["bullets"] = [*summary["bullets"], "A fourth bullet."]
    summary["bullets"][0] = " ".join(["word"] * 40)
    blocks[1]["items"] = blocks[1]["items"][:2]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    text = " ".join(violations)
    assert "4 bullets; the limit is 3" in text
    assert "bullet 1 is 40 words" in text
    assert "2 items; at least 3" in text


def test_whole_email_word_budget() -> None:
    spec, blocks = _sample_blocks()
    for block in blocks:
        if block["type"] == "text" and block.get("bullets"):
            block["bullets"] = [" ".join(["word"] * 26) for _ in block["bullets"]]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    assert any("allows 260" in v for v in violations)


def test_bullets_only_section_refuses_a_paragraph() -> None:
    spec, blocks = _sample_blocks()
    next(b for b in blocks if b["section"] == "working")["paragraphs"] = ["A paragraph."]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    assert any("1 paragraphs; the limit is 0" in v for v in violations)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("No optimizations were made this week.", "No optimizations"),
        ("Results for ACM00190 are in.", "ACM00190"),
        ("SJC-004 paused the deal.", "SJC-004"),
    ],
)
def test_never_say(text: str, said: str) -> None:
    spec, blocks = _sample_blocks()
    next(b for b in blocks if b["section"] == "watching")["bullets"] = [text]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    assert any(said in v for v in violations)


def test_never_say_sees_what_the_reader_sees() -> None:
    """Bold markers do not hide a phrase, and a heading the template sets is checked too."""
    spec, blocks = _sample_blocks()
    next(b for b in blocks if b["section"] == "watching")["bullets"] = ["No **optimizations** were made."]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    assert any("No optimizations" in v for v in violations)
    spec, blocks = _sample_blocks()
    spec["never_say"] = ["what we adjusted"]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    assert any("What We Adjusted" in v for v in violations)


@pytest.mark.parametrize("pattern", ["re:(x+)+$", "re:(a|aa)+$", "re:(\\w*)*!", "re:(a)\\1", "re:" + "a" * 201])
def test_a_never_say_pattern_that_could_hang_a_render_is_refused(pattern: str) -> None:
    raw = fixture_spec()
    raw["never_say"] = [pattern]
    assert any("not a valid pattern" in issue for issue in email_templates.validate_spec(raw))


@pytest.mark.parametrize(
    "pattern", ["re:\\b[A-Z]{3}\\d{5}\\b", "re:\\bQ[1-4] \\d{4}\\b", "re:(?:internal|draft) only", "re:\\$\\d+k?"]
)
def test_ordinary_never_say_patterns_are_accepted(pattern: str) -> None:
    raw = fixture_spec()
    raw["never_say"] = [pattern]
    assert email_templates.validate_spec(raw) == []


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"fields": {}, "promptTemplate": "Write it."}, "'fields' must be a non-empty list"),
        (
            {"fields": [{"key": "about", "label": "About", "type": "text"}], "promptTemplate": []},
            "'promptTemplate' must be",
        ),
        ({"fields": ["about"], "promptTemplate": "Write it."}, "fields[0] must be a mapping"),
        ({"fields": [{"key": "a b", "label": "About", "type": "text"}], "promptTemplate": "x"}, "'key' must be"),
        ({"fields": [{"key": "about", "type": "text"}], "promptTemplate": "x"}, "'label' is required"),
        (
            {"fields": [{"key": "about", "label": "About", "type": "slider"}], "promptTemplate": "x"},
            "'type' must be one of",
        ),
        ({"fields": [{"key": "look", "label": "Look", "type": "palette"}], "promptTemplate": "x"}, "needs 'options'"),
        (
            {"fields": [{"key": "about", "label": "About", "type": "text"}], "promptTemplate": "For {who}"},
            "no field has that key",
        ),
    ],
)
def test_a_form_fleet_would_drop_is_reported(form: dict[str, Any], message: str) -> None:
    issues = " ".join(email_templates.validate_spec(fixture_spec(**form)))
    assert message in issues


def test_never_say_matches_whole_phrases_only() -> None:
    spec, blocks = _sample_blocks()
    next(b for b in blocks if b["section"] == "watching")["bullets"] = ["We are overexcited about nothing here."]
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    assert violations == []


def test_unknown_section_wrong_type_and_duplicate() -> None:
    spec, blocks = _sample_blocks()
    blocks.append({"section": "deal_table", "type": "table", "columns": ["Deal"], "rows": [["x"]]})
    blocks.append(copy.deepcopy(blocks[0]))
    blocks[1]["type"] = "metrics"
    _, violations = email_templates.apply_template(spec, "Subject", blocks)
    text = " ".join(violations)
    assert "'deal_table'" in text
    assert "one block per section" in text
    assert "must be a headline_metrics block, not metrics" in text


def test_heading_and_callout_kind_are_set_by_the_template() -> None:
    spec, blocks = _sample_blocks()
    ask = next(b for b in blocks if b["section"] == "ask")
    ask["title"] = "My own title"
    ask["kind"] = "risk"
    ready, violations = email_templates.apply_template(spec, spec["sample"]["subject"], blocks)
    assert violations == []
    rendered = next(b for b in ready if b["type"] == "callout")
    assert rendered["title"] == "Action Needed: Confirm The Budget"
    assert rendered["kind"] == "opportunity"
    assert all("section" not in b for b in ready)


def test_subject_length() -> None:
    spec, blocks = _sample_blocks()
    _, violations = email_templates.apply_template(spec, "S" * 71, blocks)
    assert any("subject is 71 characters" in v for v in violations)


def test_validate_spec_reports_problems() -> None:
    raw = fixture_spec()
    raw["sections"][0]["limits"]["max_rows"] = 3
    raw["sections"][1]["block"] = "banner"
    raw["sections"].append(copy.deepcopy(raw["sections"][0]))
    raw["never_say"].append("re:(")
    raw["colour"] = "blue"
    issues = " ".join(email_templates.validate_spec(raw))
    assert "max_rows does not apply to a text block" in issues
    assert "'block' must be one of" in issues
    assert "duplicate id 'summary'" in issues
    assert "not a valid pattern" in issues
    assert "unknown key(s) colour" in issues


def test_unknown_template_names_the_ones_that_exist() -> None:
    with pytest.raises(email_templates.TemplateError, match=OPT):
        email_templates.load_template("../manifest")


async def test_tools_list_and_get() -> None:
    listed = await list_email_templates()
    assert {t["template"] for t in listed["templates"]} == {OPT}
    assert "problems" not in listed
    one = await get_email_template(OPT)
    assert one["template"] == OPT and one["max_words"] == 260
    assert "error" in await get_email_template("nope")


async def test_a_bundle_with_no_templates_lists_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """This bundle as shipped: nothing of its own, and nothing saved yet."""
    monkeypatch.setattr(email_templates, "TEMPLATES_DIR", tmp_path / "absent")
    assert await list_email_templates() == {"templates": []}
    result = await render_email("Subject", [{"type": "text", "paragraphs": ["Hi"]}], str(tmp_path), template=OPT)
    assert "unknown email template" in result["error"]


async def test_render_email_with_a_template(tmp_path: Path) -> None:
    spec, blocks = _sample_blocks()
    result = await render_email(spec["sample"]["subject"], blocks, str(tmp_path), template=OPT, client_name="Acme")
    assert result.get("error") is None, result
    assert result["valid"] and result["template"] == OPT
    page = Path(result["content_file"]).read_text(encoding="utf-8")
    assert "What We Adjusted" in page


async def test_render_email_refuses_a_misfit_and_writes_nothing(tmp_path: Path) -> None:
    spec, blocks = _sample_blocks()
    out = tmp_path / "out"
    out.mkdir()
    blocks.insert(2, {"type": "table", "heading": "Deals", "columns": ["Deal"], "rows": [["x"]]})
    result = await render_email(spec["sample"]["subject"], blocks, str(out), template=OPT, client_name="Acme")
    assert "does not fit template" in result["error"]
    assert any("has no 'section'" in v for v in result["violations"])
    assert result["template"]["sections"]
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("header", ["client_name", "date_range", "headline", "status", "preheader"])
async def test_render_email_checks_every_header_field(tmp_path: Path, header: str) -> None:
    spec, blocks = _sample_blocks()
    out = tmp_path / "out"
    out.mkdir()
    args = {"client_name": "Acme", "badge_label": "On track", "status": "Pacing to plan"}
    args[header] = "Acme (ACM48213)"
    result = await render_email(spec["sample"]["subject"], blocks, str(out), template=OPT, **args)
    assert "does not fit template" in result["error"], result
    assert any("ACM48213" in v for v in result["violations"])
    assert list(out.iterdir()) == []


async def test_render_email_with_custom_colors(tmp_path: Path) -> None:
    spec, blocks = _sample_blocks()
    args = {"template": OPT, "client_name": "Acme"}
    result = await render_email(spec["sample"]["subject"], blocks, str(tmp_path), colors={"header": "#1f3a5f"}, **args)
    assert result.get("error") is None, result
    assert result["colors_used"] == {"header": "#1F3A5F"}
    assert "#1F3A5F" in Path(result["content_file"]).read_text(encoding="utf-8")
    refused = await render_email(spec["sample"]["subject"], blocks, str(tmp_path), colors={"header": "#FFD400"}, **args)
    assert "too light" in refused["error"]


async def test_render_email_unknown_template(tmp_path: Path) -> None:
    result = await render_email("Subject", [{"type": "text", "paragraphs": ["Hi"]}], str(tmp_path), template="nope")
    assert "unknown email template" in result["error"]


async def test_render_email_without_a_template_is_unchanged(tmp_path: Path) -> None:
    blocks = [{"type": "text", "paragraphs": ["No optimizations this week, " + "word " * 400]}]
    result = await render_email("Subject", blocks, str(tmp_path), client_name="Acme")
    assert result.get("error") is None and "template" not in result

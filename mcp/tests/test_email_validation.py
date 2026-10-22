#!/usr/bin/env python3
"""Regression tests for the email validator shared by this bundle's senders.

Exercises `email_lint` directly (the structured `Finding` API) and the
legacy string-shape shims re-exported by `ses_sender`, so the token-finder
and HTML-structure behavior keeps holding.

Assertions check rule codes (EH001, EC001, …), not substrings of the
human message — that way copy edits to the agent-facing text don't
break the suite. Failure history that motivated each rule lives in the
docstrings of `email_lint._check_*` and in the module-level rule
catalog (`email_lint.RULES`).

Adapted from ElcanoTek/chat `server/mcp/test_template_validation.py`: the
sender half is kept separate from the shared linter and the heavy-dep stubs
are gone — the bundle's venv installs the real wheels (see
mcp/requirements.txt).
"""

from __future__ import annotations

import unittest
from pathlib import Path

import email_lint
import ses_sender

_MCP_DIR = Path(__file__).resolve().parent.parent


# ── Rule catalog sanity ─────────────────────────────────────────────────


class RuleCatalogTests(unittest.TestCase):
    def test_every_rule_has_a_severity(self):
        for code, (severity, _title) in email_lint.RULES.items():
            self.assertIn(severity, ("error", "warning"), f"{code} has bad severity {severity!r}")

    def test_finding_severity_defaults_from_catalog(self):
        self.assertEqual(email_lint.Finding(rule="EH001", message="x").severity, "error")
        self.assertEqual(email_lint.Finding(rule="EC002", message="y").severity, "warning")

    def test_unknown_rule_code_rejected(self):
        with self.assertRaises(ValueError):
            email_lint.Finding(rule="EX999", message="nope")


# ── Token finder regression (chat PR #195) ──────────────────────────────


class _SharedTokenFinderTests:
    """Shared across ses_sender and email_lint to prove both return the same
    shape. History (chat PR #195): the single-brace pattern used to also
    match inside `{{…}}` / `${…}` and surface a misleading single-brace
    fragment — agents stripped valid `{{handlebars}}` thinking they were
    the problem."""

    finder = staticmethod(lambda _content: [])  # overridden

    def test_double_brace_returned_whole(self):
        self.assertEqual(self.finder("Hi {{badge_label}}!"), ["{{badge_label}}"])

    def test_dollar_brace_returned_whole(self):
        self.assertEqual(
            self.finder("subject=${tool:run_python.vars.subject}"),
            ["${tool:run_python.vars.subject}"],
        )

    def test_single_brace_still_detected(self):
        self.assertEqual(self.finder("Hi {first_name}!"), ["{first_name}"])

    def test_single_brace_with_format_spec(self):
        self.assertEqual(self.finder("revenue: {amount:,.2f}"), ["{amount:,.2f}"])

    def test_mixed_returns_distinct_full_tokens(self):
        tokens = self.finder("Hi {first_name}, your badge is {{badge_label}}.")
        self.assertEqual(tokens, ["{first_name}", "{{badge_label}}"])

    def test_clean_html_returns_empty(self):
        self.assertEqual(self.finder("<p>Hello, your report is attached.</p>"), [])

    def test_inner_brace_of_double_brace_not_reported(self):
        self.assertNotIn("{badge_label}", self.finder("Hi {{badge_label}}!"))

    def test_inner_brace_of_dollar_brace_not_reported(self):
        self.assertNotIn("{run_python}", self.finder("${run_python}"))


class EmailLintFinderTests(_SharedTokenFinderTests, unittest.TestCase):
    finder = staticmethod(email_lint.find_unresolved_template_tokens)


class SesSenderFinderTests(_SharedTokenFinderTests, unittest.TestCase):
    finder = staticmethod(ses_sender._find_unresolved_template_tokens)


# ── Legacy validator-integration regressions ────────────────────────────


class ValidatorIntegrationTests(unittest.TestCase):
    """The legacy `_validate_email_body` shim must keep returning the
    full `{{…}}` form in its error message so agents fix the right thing."""

    def test_body_error_surfaces_double_brace(self):
        err = ses_sender._validate_email_body("<p>Hi {{badge_label}}, here's your report.</p>", "text/html")
        self.assertIsNotNone(err)
        self.assertIn("{{badge_label}}", err)

    def test_subject_error_surfaces_dollar_brace(self):
        err = ses_sender._validate_email_subject("${tool:run_python.vars.subject}")
        self.assertIsNotNone(err)
        self.assertIn("${tool:run_python.vars.subject}", err)

    def test_clean_body_passes(self):
        clean = "<p>Hi, your weekly report is attached.</p>"
        self.assertIsNone(ses_sender._validate_email_body(clean, "text/html"))


# ── Foster-parent (EH001 / EH002) and EC001 rgba ────────────────────────


class _SharedStructureTests:
    """Run the structural checks via the legacy `_validate_html` shim
    (so the shim's wiring is exercised) AND via the structured
    `email_lint.validate` (so the rule codes are the stable contract).
    Both should agree."""

    validate = staticmethod(lambda _content: ([], []))  # overridden

    SHELL_OPEN = '<html><body><table class="email-container">'
    SHELL_CLOSE = "</table></body></html>"
    HEADER_ROW = "<tr><td>Header</td></tr>"
    FOOTER_ROW = "<tr><td>Footer</td></tr>"

    def _findings(self, inner: str):
        return email_lint.validate(self.SHELL_OPEN + inner + self.SHELL_CLOSE, content_type="text/html")

    def test_div_direct_child_of_table_is_EH001(self):
        # Real-world regression — an agent appended raw <div> prose
        # between status_bar and footer.
        findings = self._findings(self.HEADER_ROW + '<div style="padding:20px;"><p>Hey, …</p></div>' + self.FOOTER_ROW)
        eh001 = [f for f in findings if f.rule == "EH001"]
        self.assertEqual(len(eh001), 1, [f.format() for f in findings])
        # Legacy shim returns the same error formatted as a string.
        errors, _ = self.validate(
            self.SHELL_OPEN + self.HEADER_ROW + "<div>x</div>" + self.FOOTER_ROW + self.SHELL_CLOSE
        )
        self.assertTrue(any("EH001" in e for e in errors), errors)

    def test_heading_direct_child_of_table_is_EH001(self):
        findings = self._findings(self.HEADER_ROW + "<h2>Pricing</h2>" + self.FOOTER_ROW)
        self.assertIn("EH001", [f.rule for f in findings])

    def test_raw_inner_table_direct_child_of_table_is_EH001(self):
        findings = self._findings(self.HEADER_ROW + "<table><tr><td>Deal</td></tr></table>" + self.FOOTER_ROW)
        self.assertTrue(any(f.rule == "EH001" and "<table>" in f.message for f in findings))

    def test_p_direct_child_of_tbody_is_EH001(self):
        findings = self._findings(
            "<tr><td><table><tbody>"
            "<tr><td>row 1</td></tr><p>orphan</p><tr><td>row 2</td></tr>"
            "</tbody></table></td></tr>"
        )
        self.assertTrue(any(f.rule == "EH001" and "<tbody>" in f.message for f in findings))

    def test_loose_text_between_rows_is_EH002(self):
        # Prose between rows foster-parents above the header.
        findings = self._findings(self.HEADER_ROW + "Hey, got your email." + self.FOOTER_ROW)
        eh002 = [f for f in findings if f.rule == "EH002"]
        self.assertEqual(len(eh002), 1)

    def test_single_violation_reported_once_for_many_children(self):
        children = "".join(f"<p>line {i}</p>" for i in range(20))
        findings = self._findings(self.HEADER_ROW + f"<div>{children}</div>" + self.FOOTER_ROW)
        eh001 = [f for f in findings if f.rule == "EH001"]
        self.assertEqual(len(eh001), 1, f"got {len(eh001)} dedup violations")

    def test_valid_canonical_passes_cleanly(self):
        findings = self._findings(
            "<tr><td>header</td></tr>"
            "<tr><td>status_bar</td></tr>"
            "<tr><td><table><tbody><tr><td>data row</td></tr></tbody></table></td></tr>"
            "<tr><td>footer</td></tr>"
        )
        structural = [f for f in findings if f.rule in ("EH001", "EH002")]
        self.assertEqual(structural, [], f"unexpectedly flagged: {[f.format() for f in structural]}")

    def test_prose_inside_td_passes(self):
        # Wrapped prose is the correct shape — must NOT flag.
        findings = self._findings(
            "<tr><td>header</td></tr>"
            '<tr><td style="padding:20px;">'
            "<p>Hi.</p><h3>Root</h3><div>Body.</div>"
            "</td></tr>"
            "<tr><td>footer</td></tr>"
        )
        foster = [f for f in findings if f.rule in ("EH001", "EH002")]
        self.assertEqual(foster, [], f"prose-in-<td> unexpectedly flagged: {foster}")


class SesSenderStructureTests(_SharedStructureTests, unittest.TestCase):
    validate = staticmethod(ses_sender._validate_html)


class _SharedRgbaTests:
    """rgba() — Outlook strips the alpha and an overlay becomes opaque."""

    validate = staticmethod(lambda _content: ([], []))  # overridden

    SHELL = '<html><body><table class="email-container"><tr><td>{body}</td></tr></table></body></html>'

    def _validate(self, inner: str):
        return self.validate(self.SHELL.format(body=inner))

    def test_rgba_in_style_is_EC001(self):
        errors, _ = self._validate('<p style="background:rgba(0,0,0,0.6);">overlay</p>')
        self.assertTrue(any("EC001" in e for e in errors), errors)

    def test_rgba_with_space_after_keyword_is_EC001(self):
        errors, _ = self._validate('<p style="color:rgba (255,255,255,0.5);">a</p>')
        self.assertTrue(any("EC001" in e for e in errors), errors)

    def test_uppercase_rgba_is_EC001(self):
        errors, _ = self._validate('<p style="color:RGBA(0,0,0,0.5);">a</p>')
        self.assertTrue(any("EC001" in e for e in errors), errors)

    def test_plain_rgb_passes(self):
        errors, _ = self._validate('<p style="color:rgb(255,0,0);">a</p>')
        self.assertFalse(any("EC001" in e for e in errors), errors)

    def test_hex_color_passes(self):
        errors, _ = self._validate('<p style="color:#FF0000;">a</p>')
        self.assertFalse(any("EC001" in e for e in errors), errors)


class SesSenderRgbaTests(_SharedRgbaTests, unittest.TestCase):
    validate = staticmethod(ses_sender._validate_html)


# ── EP001 — pad-cell nesting ─────────────────────────────────────────────


class PadNestingTests(unittest.TestCase):
    """Campaign-health-scan regression from the source repo: an agent
    emitted <td class="status-pad"> with an inner badge <table>, closed
    the inner <tr>, but forgot </table></td></tr>. The next body section
    ended up nested inside the badge table, squeezing the rest of the
    email through a 2-column cell."""

    def _findings(self, inner: str):
        shell = '<html><body><table class="email-container">{body}</table></body></html>'
        return email_lint.validate(shell.format(body=inner), content_type="text/html")

    def test_body_pad_nested_in_status_pad_is_EP001(self):
        findings = self._findings(
            '<tr><td class="status-pad"><table>'
            "<tr><td>BADGE</td><td>Status: ok</td></tr>"
            # MISSING </table></td></tr> here
            '<tr><td class="body-pad"><table><tr><td>body</td></tr></table></td></tr>'
        )
        ep001 = [f for f in findings if f.rule == "EP001"]
        self.assertEqual(len(ep001), 1, [f.format() for f in findings])

    def test_pads_at_top_level_pass(self):
        findings = self._findings(
            '<tr><td class="header-pad">header</td></tr>'
            '<tr><td class="status-pad">'
            "<table><tr><td>BADGE</td><td>ok</td></tr></table>"
            "</td></tr>"
            '<tr><td class="body-pad">body</td></tr>'
            '<tr><td class="footer-pad">footer</td></tr>'
        )
        self.assertNotIn("EP001", [f.rule for f in findings])

    def test_multiple_nested_pads_dedup_to_one_finding(self):
        findings = self._findings(
            '<tr><td class="status-pad"><table>'
            "<tr><td>BADGE</td></tr>"
            '<tr><td class="body-pad">section 1</td></tr>'
            '<tr><td class="body-pad">section 2</td></tr>'
            "</table></td></tr>"
        )
        ep001 = [f for f in findings if f.rule == "EP001"]
        self.assertEqual(len(ep001), 1)

    def test_unclassed_inner_td_inside_pad_passes(self):
        findings = self._findings(
            '<tr><td class="body-pad"><table><tr><td>Metric</td><td>Value</td></tr></table></td></tr>'
        )
        self.assertNotIn("EP001", [f.rule for f in findings])


# ── EO001 / EO002 — Outlook/Word-engine compatibility ───────────────────


class OutlookCompatTests(unittest.TestCase):
    """Deal-sheet email regression from the source repo: recipients on
    Outlook desktop (Word rendering engine) saw a dark header render
    white (its light text invisible), card fills vanish, and a
    border-left accent stripe disappear. The linter had no rule for
    CSS-only cell fills or single-side td borders, so send_email
    returned html_validated=True on markup that broke in Word. Both
    rules are WARNING severity so common templates still send."""

    def _findings(self, inner: str):
        shell = '<html><body><table class="email-container">{body}</table></body></html>'
        return email_lint.validate(shell.format(body=inner), content_type="text/html")

    # EO001 — colored cell/table without a matching bgcolor attribute

    def test_colored_td_without_bgcolor_is_EO001(self):
        findings = self._findings('<tr><td style="background-color:#F4F6FB; padding:12px;">card</td></tr>')
        eo001 = [f for f in findings if f.rule == "EO001"]
        self.assertEqual(len(eo001), 1, [f.format() for f in findings])
        self.assertEqual(eo001[0].severity, "warning")
        self.assertIn("#F4F6FB", eo001[0].message)
        self.assertIn('bgcolor="#F4F6FB"', eo001[0].hint)

    def test_colored_table_without_bgcolor_is_EO001(self):
        findings = self._findings(
            '<tr><td><table style="background-color:#FFF5E5;"><tr><td>x</td></tr></table></td></tr>'
        )
        self.assertTrue(any(f.rule == "EO001" and "<table>" in f.message for f in findings))

    def test_background_shorthand_without_bgcolor_is_EO001(self):
        findings = self._findings('<tr><td style="background:#E6EBF5;">bar</td></tr>')
        self.assertIn("EO001", [f.rule for f in findings])

    def test_matching_bgcolor_passes_EO001(self):
        findings = self._findings(
            '<tr><td bgcolor="#F4F6FB" style="background-color:#F4F6FB; padding:12px;">card</td></tr>'
        )
        self.assertNotIn("EO001", [f.rule for f in findings])

    def test_mismatched_bgcolor_is_EO001(self):
        findings = self._findings('<tr><td bgcolor="#FFFFFF" style="background-color:#F4F6FB;">card</td></tr>')
        eo001 = [f for f in findings if f.rule == "EO001"]
        self.assertEqual(len(eo001), 1, [f.format() for f in findings])
        self.assertIn("does not match", eo001[0].message)

    def test_three_digit_hex_equivalence_passes_EO001(self):
        # `#fff` in CSS and `#FFFFFF` in bgcolor are the same color — no finding.
        findings = self._findings('<tr><td bgcolor="#FFFFFF" style="background-color:#fff;">x</td></tr>')
        self.assertNotIn("EO001", [f.rule for f in findings])

    def test_non_hex_backgrounds_skip_EO001(self):
        # Conservative: only hex-valued fills are checked (named colors /
        # rgb() can't be reliably compared against a bgcolor attribute).
        for style in (
            "background-color:transparent;",
            "background:rgb(255,0,0);",
            "background-color:inherit;",
        ):
            with self.subTest(style=style):
                findings = self._findings(f'<tr><td style="{style}">x</td></tr>')
                self.assertNotIn("EO001", [f.rule for f in findings])

    def test_EO001_deduped_across_repeated_deal_blocks(self):
        rows = "".join('<tr><td style="background-color:#F4F6FB;">deal</td></tr>' for _ in range(12))
        findings = self._findings(rows)
        eo001 = [f for f in findings if f.rule == "EO001"]
        self.assertEqual(len(eo001), 1, f"expected one deduped EO001, got {len(eo001)}")

    # EO002 — single-side CSS border on a table cell

    def test_single_side_border_on_td_is_EO002(self):
        for style in (
            "border-left:3px solid #6262A0;",  # the deal-card accent stripe bug
            "border-top:1px solid #D7DEEE;",  # the footer divider bug
            "border-right:2px solid #000;",
            "border-bottom:1px solid #E6EBF5;",
            "border-left-width:3px;",  # longhand variant
        ):
            with self.subTest(style=style):
                findings = self._findings(f'<tr><td style="{style}">x</td></tr>')
                eo002 = [f for f in findings if f.rule == "EO002"]
                self.assertEqual(len(eo002), 1, [f.format() for f in findings])
                self.assertEqual(eo002[0].severity, "warning")
                self.assertIn("filled cell", eo002[0].hint)

    def test_full_border_shorthand_passes_EO002(self):
        # Uniform four-side borders survive the Word engine — only
        # per-side declarations are flagged.
        findings = self._findings('<tr><td style="border:1px solid #D7DEEE;">x</td></tr>')
        self.assertNotIn("EO002", [f.rule for f in findings])

    def test_border_resets_pass_EO002(self):
        for style in ("border-left:0;", "border-left:0px;", "border-top:none;"):
            with self.subTest(style=style):
                findings = self._findings(f'<tr><td style="{style}">x</td></tr>')
                self.assertNotIn("EO002", [f.rule for f in findings])

    def test_style_block_rules_do_not_fire_EO_rules(self):
        # Only inline style= attributes are inspected. <style>-block rules
        # (e.g. mobile @media overrides) never reach the Word engine — it
        # strips the block — so they are not a defect.
        html = (
            "<html><head><style>"
            "@media only screen and (max-width:480px) {"
            " .data-table td { border-left:1px solid #D7DEEE !important; background-color:#FAFAFA !important; }"
            "}</style></head><body>"
            '<table class="email-container"><tr><td>x</td></tr></table>'
            "</body></html>"
        )
        findings = email_lint.validate(html, content_type="text/html")
        codes = [f.rule for f in findings]
        self.assertNotIn("EO001", codes)
        self.assertNotIn("EO002", codes)

    def test_filled_cell_accent_and_divider_pattern_passes(self):
        # The replacement patterns the hints recommend must themselves
        # lint clean: a narrow filled accent cell + a 1px filled divider row.
        findings = self._findings(
            "<tr><td><table><tr>"
            '<td width="3" bgcolor="#6262A0" style="width:3px; background-color:#6262A0; font-size:1px; line-height:1px;">&nbsp;</td>'
            '<td bgcolor="#F4F6FB" style="background-color:#F4F6FB; padding:12px 16px;">deal</td>'
            "</tr></table></td></tr>"
            '<tr><td height="1" bgcolor="#D7DEEE" style="height:1px; background-color:#D7DEEE; font-size:1px; line-height:1px;">&nbsp;</td></tr>'
        )
        codes = [f.rule for f in findings]
        self.assertNotIn("EO001", codes)
        self.assertNotIn("EO002", codes)


# ── Real-world regression fixtures ──────────────────────────────────────
# `mcp/testdata/*.html` are high-fidelity reproductions of actual bugs
# that hit production in the source repo. If a future refactor breaks
# detection on any of these, that's a regression.


class RealWorldFixtureTests(unittest.TestCase):
    FIXTURE_DIR = _MCP_DIR / "testdata"

    def _load(self, name: str) -> str:
        return (self.FIXTURE_DIR / name).read_text(encoding="utf-8")

    def test_health_scan_real_fixture_fires_EP001(self):
        # The exact failure that prompted EP001: status-pad with a
        # forgotten `</table></td></tr>` swallowed every subsequent row.
        findings = email_lint.validate(
            self._load("health_scan_nested_pad.html"),
            content_type="text/html",
        )
        ep001 = [f for f in findings if f.rule == "EP001"]
        codes = [f.rule for f in findings]
        self.assertGreaterEqual(len(ep001), 1, f"Health-scan regression must fire EP001 — got codes: {codes}")

    def test_weekly_status_real_fixture_fires_EH001(self):
        # Bare `<div>` between component `<tr>`s rendered above the dark
        # header because HTML5 foster-parented it out of the table.
        findings = email_lint.validate(
            self._load("header_div_orphan.html"),
            content_type="text/html",
        )
        eh001 = [f for f in findings if f.rule == "EH001"]
        codes = [f.rule for f in findings]
        self.assertGreaterEqual(len(eh001), 1, f"Weekly-status regression must fire EH001 — got codes: {codes}")
        self.assertTrue(
            any("<div>" in f.message for f in eh001),
            f"EH001 message must mention <div>: {[f.message for f in eh001]}",
        )


if __name__ == "__main__":
    unittest.main()

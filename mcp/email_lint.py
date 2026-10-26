"""Email HTML linter for this bundle's mail-sending MCP server (ses_sender.py).

Single source of truth for HTML / subject / body validation. Hand-ported
from a sibling client bundle (a peer, not an upstream); the rules came from
two other repos. This bundle owns its copy: change it here, with tests, and
never sync it from or to another repo.

Design rules:

* **One non-stdlib dep: html5lib.** Spec-correct HTML5 parsing means
  foster-parenting and unclosed-table detection come from a real
  parser, not a hand-coded state machine. The library is pure-Python,
  has no transitive deps that aren't already pinned, and is stable
  enough that we can ride it without version drama.

* **One rule per function.** Every check is its own small named helper
  that yields zero or more `Finding`s. Adding a new failure mode is one
  function plus one test, never surgery on a 500-line class.

* **Stable rule codes.** Every `Finding` has a 2-letter prefix + 3-digit
  code (`EH001` foster-parent, `EC001` rgba, etc.). Agents reference
  codes in retries; downstream tooling can grep for them. NEVER reuse a
  retired code — append a deprecation note in `RULES` and pick the next
  number.

* **Severity = error | warning.** Errors block sending. Warnings are
  surfaced in the tool response but do not block. No third tier today;
  promote a warning to error rather than inventing `info`.

* **Pure functions, no I/O.** Path-reading, file-loading, and SendGrid
  wiring all live in the caller. This module only walks strings.

Public API:

    from email_lint import (
        validate,                     # html [+ optional subject] → list[Finding]
        Finding,                      # dataclass: rule, severity, message, hint, line
        detect_html_content,          # str → bool (HTML auto-detection)
        resolve_content_type,         # (content, explicit) → (resolved, was_corrected)
        extract_cid_references,       # html → set[str]
        find_unresolved_template_tokens,  # str → sorted list[str]
        format_findings,              # list[Finding] → "  - EH001 (error): ..."
        partition_findings,           # list[Finding] → (errors, warnings)
        RULES,                        # {code: (severity, short title)} for docs / tooling
    )

Backward-compat helpers (kept for the existing callers' single-pass swap):

    validate_html_legacy(content)         → (errors_list[str], warnings_list[str])
    validate_email_subject_legacy(s)      → str | None
    validate_email_body_legacy(s, ctype)  → str | None
    check_template_leakage_legacy(s)      → (errors_list[str], warnings_list[str])

These wrap `validate()` with the old string-list shape; new code should
use the structured `Finding` API.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Final

import html5lib

# ── Rule catalog ────────────────────────────────────────────────────────────
# Stable codes; never reuse a retired number. Two-letter prefix groups by
# concern so a glance at a finding tells you where to look:
#   EH  email HTML structure          (foster-parent, unclosed, deep nest)
#   EI  email image references        (data: URIs, file://, missing cid)
#   EC  email CSS                     (rgba, position:fixed/absolute)
#   EO  email Outlook/Word engine     (bgcolor fallbacks, single-side cell borders)
#   ET  email template tokens         (unresolved {{...}} / ${...} / {...})
#   EL  email leakage / placeholders  (canonical_template demo data)
#   EB  email branding                (the canonical_template frame)
#   EP  email pad-cell heuristics     (canonical_template specific)
RULES: Final[dict[str, tuple[str, str]]] = {
    # Structure (EH)
    "EH001": ("error", "Foster-parented element (invalid table child)"),
    "EH002": ("error", "Loose text foster-parented out of table"),
    "EH003": ("error", "Unclosed HTML tags"),
    "EH004": ("error", "Closing tag with no matching opening tag"),
    "EH005": ("warning", "Tag closed before nested tags"),
    "EH006": ("warning", "Table with inconsistent column counts"),
    "EH007": ("warning", "Table without any <tr> rows"),
    "EH008": ("warning", "Table without any <td>/<th> cells"),
    "EH009": ("warning", "No common content tags found"),
    "EH010": ("warning", "Very deep nesting"),
    "EH011": ("error", "HTML parsing error"),
    "EH012": ("error", "HTML content empty"),
    "EH013": ("error", "Content-type is text/html but no HTML tags found"),
    # Images (EI)
    "EI001": ("error", "Image with empty src"),
    "EI002": ("error", "Image src is a local file:// path"),
    "EI003": ("error", "Image src is a data: URI (fabricated inline)"),
    "EI004": ("error", "Image src must be cid: or http(s)://"),
    "EI005": ("error", "Inline cid: references with no inline_attachments"),
    "EI006": ("error", "Inline cid: references missing from inline_attachments"),
    # CSS (EC)
    "EC001": ("error", "CSS rgba() colors (alpha stripped by Outlook)"),
    "EC002": ("warning", "CSS position:fixed not supported in email"),
    "EC003": ("warning", "CSS position:absolute limited support in email"),
    # Outlook/Word-engine rendering (EO)
    # Warnings, not errors: the canonical_template (mirrored from
    # flag/design-system/email-safe.html, do-not-hand-edit) still carries
    # CSS-only cell fills and single-side td borders, so error severity
    # would block every canonical-template send. Promote once flag ships
    # the hardened template.
    "EO001": ("warning", "Colored cell/table without matching bgcolor attribute"),
    "EO002": ("warning", "Single-side CSS border on a table cell"),
    # Tokens (ET)
    "ET001": ("error", "Unresolved template tokens in body"),
    "ET002": ("error", "Unresolved template tokens in subject"),
    # Leakage / placeholders (EL)
    "EL001": ("error", "Body is a placeholder value"),
    "EL002": ("error", "Body is too short or empty"),
    "EL003": ("error", "Subject is empty"),
    "EL101": ("error", "Template leakage: data-preview attributes survived"),
    "EL102": ("error", "Template leakage: demo prose survived"),
    "EL103": ("error", "Template leakage: ≥3 demo markers co-occur"),
    "EL104": ("warning", "Possible template leakage: 1-2 demo markers found"),
    # Pad-cell heuristics (EP) — canonical_template specific, but the rule
    # is harmless elsewhere (no false positives on hand-rolled HTML).
    "EP001": ("error", "Pad-class <td> nested inside another pad-class <td>"),
    # Branding (EB) — only when the caller asks for it (require_frame=True):
    # ses_sender does for every send; a fragment check does not.
    "EB001": ("error", "HTML email is missing the branded frame"),
    # Mobile responsiveness (EM) — checked when require_frame=True so every
    # full HTML email is mobile-friendly by default.
    "EM001": ("error", "HTML email is missing mobile-responsive @media rules"),
    "EM002": ("error", "Data table is missing mobile-label spans for mobile card stacking"),
    "EM003": ("warning", "Fixed-width .email-container breaks mobile viewports"),
}


@dataclass(frozen=True)
class Finding:
    """One validator hit. Findings compose into the full validate() result.

    `rule` MUST be a key in RULES. `severity` is derived from RULES so the
    caller can't accidentally drift severity from the catalog.
    """

    rule: str
    message: str
    hint: str = ""
    line: int | None = None
    # `severity` defaults from the catalog — set explicitly only in tests.
    severity: str = field(default="")

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(f"Unknown rule code: {self.rule}")
        if not self.severity:
            object.__setattr__(self, "severity", RULES[self.rule][0])

    def format(self) -> str:
        """One-line human form: 'EH001 (error): message — hint'."""
        head = f"{self.rule} ({self.severity}): {self.message}"
        if self.hint:
            return f"{head} — {self.hint}"
        return head


# ── Constants used by checks ────────────────────────────────────────────────

# What content with no HTML in it and no declared type is sent as. Plain
# text: a model that writes a bare sentence is writing a plain note, and
# fleet's pre-staging check (validate_email_content with content and subject
# only) must reach the same verdict as send_email will after approval.
DEFAULT_CONTENT_TYPE = "text/plain"

_HTML_TAG_PATTERN = re.compile(
    r"<\s*(?:html|head|body|div|span|p|br|hr|table|tr|td|th|ul|ol|li|"
    r"h[1-6]|a|img|strong|em|b|i|u|style|script|link|meta|header|footer|"
    r"nav|section|article|aside|main|form|input|button|label|select|"
    r"textarea|iframe|video|audio|canvas|svg|!DOCTYPE)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_ENTITY_PATTERN = re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")

_UNRESOLVED_TEMPLATE_PATTERNS = (
    re.compile(r"\$\{[^{}]+\}"),
    re.compile(r"\{\{[^{}]+\}\}"),
    re.compile(r"(?<![\$\{])\{[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z0-9_.,%+\-<>^]+)?\}(?!\})"),
)

_IMG_SRC_PATTERN = re.compile(r'<img[^>]*src\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_CID_PATTERN = re.compile(r"cid:([A-Za-z0-9_.@\-]+)", re.IGNORECASE)

_PLACEHOLDER_VALUES = frozenset({"html_content", "content", "BODY", "text_content", "PUT_CONTENT_HERE"})

# ── Template leakage detection ──────────────────────────────────────────────
# The shared canonical email template (protocols/email-style.yaml in the
# bundles that ship one; this bundle does not) carries demo content that
# must not reach recipients. The markers stay so a reproduced copy of that
# demo is still caught here. Three tiers by false-positive risk:
#   1. `data-preview="..."` attributes live ONLY on demo rows — survivors
#      mean the agent copy-pasted, not regenerated.
#   2. Full demo-prose sentences are long enough that real analyst output
#      won't collide. Hard block.
#   3. Brand names and specific dollar figures CAN legitimately recur in
#      real reporting. Warn on 1–2 hits; block only if ≥3 co-occur (real
#      data won't collocate "Example US Video" + the exact demo amount +
#      "Sample Brand Display" in one email).
_DATA_PREVIEW_ATTR_PATTERN = re.compile(r'\bdata-preview\s*=\s*"([^"]*)"')

_TIER2_DEMO_PROSE_MARKERS: tuple[str, ...] = (
    "driven primarily by Example US Video pacing cuts that began on Apr 3",
    "biggest production risk is continued weekend underdelivery",
    "immediate opportunity is recovering volume in stable Canada inventory",
    "weekend spend fell to roughly $755/day after pacing above $104,000/day",
    "Canada display inventory stayed efficient, creating room to shift spend",
    "Hold the dark header plus white-body structure, keep all critical colors",
)

_TIER3_DEMO_MARKERS: tuple[str, ...] = (
    "Example US Video",
    "Example CA Display",
    "Sample Brand Display",
    "$589,075.45",
    "63,400,529",
    "$311,402",
    "$405,928",
    "$72,119",
    "$68,004",
    "$54,882",
    "$58,131",
    "$9.29",
)

_TIER3_BLOCK_THRESHOLD = 3

# ── HTML structure constants ────────────────────────────────────────────────

# Section-wrapper classes used on the canonical_template's top-level <td>s.
# A pad cell nested inside another pad cell is the smoking-gun signal that
# an inner badge/utility <table> was never closed. Template-specific but
# harmless elsewhere (no false positives on hand-rolled HTML).
_PAD_CLASSES = frozenset({"header-pad", "status-pad", "body-pad", "footer-pad", "table-pad"})

# Table-family element names — anything that the foster-parent algorithm
# treats as a "this element belongs inside a table" container. When an
# offending start tag is reported by html5lib, its parent context (for
# our EH001 message) is the deepest of these on the open-elements stack.
_TABLE_FAMILY = frozenset({"table", "tbody", "thead", "tfoot", "tr", "colgroup"})

# html5lib error codes that mean "an element was opened where it couldn't
# legally live, so the parser foster-parented it OUT of the table".
_FOSTER_PARENT_START_CODES: Final[frozenset[str]] = frozenset(
    {
        "unexpected-start-tag-implies-table-voodoo",
    }
)
# Nested `<table>` directly inside `<table>` (no `<td>` wrapper) doesn't
# trigger the voodoo code; html5lib emits this code with start==end==table
# instead. Treated as the same EH001 failure.
_NESTED_TABLE_CODE: Final[str] = "unexpected-start-tag-implies-end-tag"
# `<table>` (or other table-context) left open at EOF.
_EOF_IN_TABLE_CODE: Final[str] = "eof-in-table"


# ── HTML5 parser subclass that records error context ─────────────────────────


class _TrackingHTMLParser(html5lib.HTMLParser):
    """html5lib parser subclass that records the context we need for our
    structural rules.

    Two hooks:

    * `parseError` is the html5lib-provided notification hook. We record
      every tree-construction error along with the open-elements stack
      at the moment of error. The stack tells us the table-family
      ancestor the offending tag was trying to live inside, which is
      what our EH001 message needs ("<div> appears as a direct child
      of <tbody>"). html5lib doesn't put this in the error tuple, but
      `self.tree.openElements` IS the live insertion-mode stack at the
      moment `parseError` runs.

    * `processCharacters` on the foster-parenting-prone phases
      (`inTable`, `inTableBody`, `inRow`) is wrapped so we can record
      every non-whitespace character token that lands in a table
      context. html5lib silently foster-parents these — no parseError
      fires — so the wrapping is the only way to detect EH002 against
      the spec algorithm. Wrapping is done by replacing the phase
      instance with a subclass instance (phases use `__slots__`, so we
      can't monkey-patch the bound method directly).
    """

    def __init__(self) -> None:
        super().__init__(
            strict=False,
            tree=html5lib.treebuilders.getTreeBuilder("etree"),
        )
        # (pos, code, datavars, open_elements_stack)
        self.recorded_errors: list[tuple[tuple[int, int], str, dict, list[str]]] = []
        # (pos, text_data) — non-whitespace character tokens that landed
        # in a table-context phase. The same source text often fires the
        # hook on multiple phases as the parser transitions (e.g. inTable
        # then inTableBody for the same character) — caller dedups by pos.
        self.loose_text_events: list[tuple[tuple[int, int], str]] = []
        for phase_name in ("inTable", "inTableBody", "inRow"):
            self._wrap_text_handler(phase_name)

    def _wrap_text_handler(self, phase_name: str) -> None:
        original = self.phases[phase_name]
        phase_cls = type(original)
        parser_ref = self

        class WrappedPhase(phase_cls):  # type: ignore[valid-type,misc]
            def processCharacters(self, token: Any) -> Any:
                data = token.get("data", "") if isinstance(token, dict) else ""
                if data and data.strip():
                    parser_ref.loose_text_events.append((parser_ref.tokenizer.stream.position(), data))
                return super().processCharacters(token)

        # html5lib phases use __slots__, so we have to construct a fresh
        # instance of the subclass rather than reassigning a method on
        # the existing one. Phase.__init__(parser, tree) is the canonical
        # signature.
        self.phases[phase_name] = WrappedPhase(self, self.tree)

    def parseError(self, errorcode: str = "?", datavars: Any = None) -> None:
        if datavars is None:
            datavars = {}
        pos = self.tokenizer.stream.position()
        stack = [_strip_ns(el.name) for el in self.tree.openElements]
        self.recorded_errors.append((pos, errorcode, datavars, stack))
        super().parseError(errorcode, datavars)


def _strip_ns(tag: Any) -> str:
    """html5lib's etree treebuilder namespaces every tag as
    `{http://www.w3.org/1999/xhtml}div`. Strip that for our purposes.

    Returns the empty string for non-element nodes (etree represents
    `<!--comment-->` as a Comment factory callable rather than a tag
    name string, and processing instructions get similar treatment;
    none of our checks care about those).
    """
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _pad_classes(class_attr: str) -> set[str]:
    if not class_attr:
        return set()
    return {token for token in class_attr.split() if token in _PAD_CLASSES}


def _table_parent(stack: list[str]) -> str | None:
    """Walk the open-elements stack from deepest to shallowest and return
    the first table-family element. That's the parent context our EH001
    message references ("<div> appears as a direct child of <{this}>")."""
    for tag in reversed(stack):
        if tag in _TABLE_FAMILY:
            return tag
    return None


def _rule_text_for_parent(parent: str) -> str:
    """Human-readable HTML5 rule the offending child violates. Lets the
    agent map our message back to the spec rule without us pointing at
    URLs that might rot."""
    if parent == "table":
        return "Only <tr>, <thead>, <tbody>, <tfoot>, <caption>, or <colgroup> are valid as direct children of <table>."
    if parent in ("thead", "tbody", "tfoot"):
        return f"Only <tr> rows are valid as direct children of <{parent}>."
    if parent == "tr":
        return "Only <td> or <th> cells are valid as direct children of <tr>."
    if parent == "colgroup":
        return "Only <col> elements are valid as direct children of <colgroup>."
    return ""


# ── Foster-parent detection (EH001) ──────────────────────────────────────────


def _check_foster_parents(parser: _TrackingHTMLParser, dedup: set[tuple[str, str]]) -> Iterator[Finding]:
    """EH001 — element foster-parented out of a table container.

    The weekly-status regression (chat#201): agent
    appended a `<div>` between component `<tr>` rows; html5lib's
    foster-parent algorithm moves it OUT of the table (typically to a
    sibling of the table) and it renders above the dark email header.

    Two error codes map to EH001:

    * `unexpected-start-tag-implies-table-voodoo` — fires for <div>/<p>/
      <h*>/<span>/etc. opened in a table context.
    * `unexpected-start-tag-implies-end-tag` with start==end==table —
      fires when a `<table>` is opened directly inside another `<table>`
      without a `<td>` wrapper. html5lib treats this as an implicit
      `</table>` plus a sibling `<table>`.

    Three layers of dedup keep the agent's error budget sane. Each
    layer was audited against an adversarial case (see commit history
    for the audit script); removing any one of them re-introduces
    spurious findings:

    1. **Top-of-stack must be table-family.** When a foster-parented
       `<div>` contains a `<p>`, html5lib reports both. Layer 1 drops
       the `<p>` because its source-context parent is the already-
       fostered `<div>` (top of stack), not a table-family element.
       The fix for the outer `<div>` resolves the inner `<p>` for free.

       Adversarial case (would over-report without this layer):
         `<table><div><p>0</p><p>1</p></div></table>`
         Without: 2 findings (`div in tbody`, `p in tbody`).
         With:    1 finding  (`div in tbody`). Fix the div, the p is fine.

       Legitimate cases still fire (e.g. `<table><tr><div></div></tr>`
       reports `div in tr` because the `<tr>` IS table-family).

    2. **(parent, child)** — same-shape repeated foster-parents collapse.

       Adversarial case:
         `<table>` + `<p>x</p>` × 5 + `</table>`
         Without: 5 findings (all `p in table`).
         With:    1 finding. Same fix; one error is enough signal.

       Doesn't over-collapse: distinct child tags stay distinct.
         `<table>` + `<p>...</p><div>...</div>` × 3 → 2 findings.

    3. **(child, source-position)** — the implicit-close cascade for a
       nested `<table>` fires `voodoo` + `implies-end-tag` events at
       the SAME source offset. Same source bug; one finding is enough.

       Adversarial case:
         `<table><tr><td>x</td></tr><table>...</table></table>`
         Without: 2 findings (`table in tbody`, `table in table`).
         With:    1 finding. Same source offset, same fix.
    """
    for pos, code, data, stack in parser.recorded_errors:
        child_tag: str | None = None
        if code in _FOSTER_PARENT_START_CODES:
            child_tag = data.get("name")
        elif code == _NESTED_TABLE_CODE and data.get("startName") == "table" and data.get("endName") == "table":
            child_tag = "table"
        if child_tag is None:
            continue
        if not stack or stack[-1] not in _TABLE_FAMILY:
            continue
        parent = _table_parent(stack)
        if parent is None:
            continue
        key = ("EH001", f"{parent}>{child_tag}")
        pos_key = ("EH001", f"{child_tag}@{pos[0]}:{pos[1]}")
        if key in dedup or pos_key in dedup:
            continue
        dedup.add(key)
        dedup.add(pos_key)
        rule_text = _rule_text_for_parent(parent)
        yield Finding(
            rule="EH001",
            message=(
                f"<{child_tag}> appears as a direct child of <{parent}>. "
                f"HTML5 foster-parents invalid table children OUTSIDE the table "
                f"(typically before it), so the content will NOT render where you "
                f"placed it in source — in the canonical email template this means "
                f"a <div>/<p>/<h1>/etc. you appended between component <tr> rows "
                f"will jump above the dark email header. {rule_text}"
            ),
            hint=(
                "Wrap free-form prose or custom tables in `<tr><td>...</td></tr>` "
                "before inserting them into the email-container table."
            ),
            line=pos[0],
        )


# ── Loose text in table (EH002) ──────────────────────────────────────────────


def _check_loose_text(parser: _TrackingHTMLParser, dedup: set[tuple[str, str]]) -> Iterator[Finding]:
    """EH002 — non-whitespace text directly inside a `<table*>` context.

    chat#202: "Hey Alex and Sam, …" appeared between two
    component `<tr>` rows; html5lib foster-parents text OUT of the table
    just like elements, and the prose ended up above the dark email
    header.

    Source: `_TrackingHTMLParser.loose_text_events` — every non-whitespace
    character token the parser processed while in a foster-parenting-
    prone phase (`inTable`, `inTableBody`, `inRow`). Spec-correct: if
    html5lib reaches one of those phases with character data, that data
    IS being foster-parented. No regex; no false positives on text
    inside `<style>` or `<script>` or attribute values.

    One finding per parse — once the agent sees there's loose text in
    the table, that's enough signal to fix it. The same source text
    often fires the hook on multiple phases as the parser transitions,
    so we dedup by source position before reporting.
    """
    if not parser.loose_text_events:
        return
    # The first event by source position is the user-actionable one.
    pos, _data = min(parser.loose_text_events, key=lambda evt: (evt[0][0], evt[0][1]))
    key = ("EH002", "table")
    if key in dedup:
        return
    dedup.add(key)
    yield Finding(
        rule="EH002",
        message=(
            "Loose text content appears as a direct child of <table>. "
            "HTML5 foster-parents text-between-rows OUTSIDE the table, so "
            "any prose written between two component rows will render above "
            "the dark email header."
        ),
        hint="Wrap it in `<tr><td>...</td></tr>`.",
        line=pos[0],
    )


# ── Unclosed structural containers (EH003) ───────────────────────────────────


def _check_unclosed(parser: _TrackingHTMLParser) -> Iterator[Finding]:
    """EH003 — `<table>` (or other table-context) left open at EOF.

    html5lib's `eof-in-table` is the cleanest signal. It doesn't tell us
    *which* element specifically was unclosed, but in practice it always
    means a missing `</table>` (or a missing `</tr>` / `</td>` that
    cascades to a missing `</table>`). The fix is always the same: close
    the open table.
    """
    if any(code == _EOF_IN_TABLE_CODE for _pos, code, _data, _stack in parser.recorded_errors):
        yield Finding(
            rule="EH003",
            message="Unclosed HTML tags: <table>",
            hint="Add the missing </table> (and any missing </tr>/</td>) at the end of the email body.",
        )


# ── Bad close tags (EH004) ───────────────────────────────────────────────────

# html5lib codes that indicate a closing tag with no opener — the agent
# wrote `</div>` (or whatever) more times than they wrote `<div>`, or
# wrote `</span>` where they meant `</div>`. NOT the same as
# `unexpected-end-tag-implies-table-voodoo` (that's foster-parent noise
# we already drop in _check_foster_parents).
_BAD_CLOSE_CODES: Final[frozenset[str]] = frozenset(
    {
        "unexpected-end-tag",
        "end-tag-too-early",
        "expected-one-end-tag-but-got-another",
    }
)


def _check_bad_close(
    parser: _TrackingHTMLParser,
    foster_parent_or_unclosed_fired: bool,
    dedup: set[tuple[str, str]],
) -> Iterator[Finding]:
    """EH004 — closing tag with no matching opener.

    Catches genuine `<div>x</div></div>` and `</span>` typos. NOT
    emitted when EH001 or EH003 already fired in the same parse: those
    are root-cause issues and html5lib cascades 1-3 spurious end-tag
    errors as a consequence of foster-parenting or implicit `</table>`
    insertion. Suppressing here means the agent fixes the structural
    bug first; if a genuine extra-close survives that fix, the next
    validation pass surfaces it cleanly.

    Trade-off accepted: an agent with BOTH a foster-parent AND an
    unrelated extra-close tag in the same draft will see only the
    foster-parent on first pass. They iterate on the structural bug,
    re-validate, and the extra-close surfaces. One bug at a time is
    fine; spurious cascade noise is not.

    Dedup by `(rule, tag)` so a chain of `</td></tr></table>` typos
    doesn't produce three separate findings for the same fix.
    """
    if foster_parent_or_unclosed_fired:
        return
    for pos, code, data, _stack in parser.recorded_errors:
        if code not in _BAD_CLOSE_CODES:
            continue
        tag = data.get("name") or data.get("gotName") or "?"
        key = ("EH004", tag)
        if key in dedup:
            continue
        dedup.add(key)
        yield Finding(
            rule="EH004",
            message=f"Closing tag </{tag}> has no matching opening tag",
            hint=("Either remove the extra closing tag or add the missing opener earlier in the document."),
            line=pos[0],
        )


# ── Pad-cell nesting heuristic (EP001) ───────────────────────────────────────


def _walk_etree(elem: Any, parent_chain: list[tuple[str, dict]]) -> Iterator[tuple[str, dict, list[tuple[str, dict]]]]:
    """Depth-first walk of an html5lib etree. Yields (tag, attrib,
    parent_chain) for each element, with namespace stripped."""
    for child in elem:
        tag = _strip_ns(child.tag)
        attrib = child.attrib
        yield tag, attrib, parent_chain
        yield from _walk_etree(child, [*parent_chain, (tag, attrib)])


def _check_pad_nesting(tree: Any, dedup: set[tuple[str, str]]) -> Iterator[Finding]:
    """EP001 — `*-pad` `<td>` nested inside another `*-pad` `<td>`.

    A Campaign Health Scan regression (commit 33e4468,
    2026-05-21): the agent emitted `<td class="status-pad">` with an
    inner badge `<table>`, closed the inner `<tr>` but forgot
    `</table></td></tr>`. The next `<tr><td class="body-pad">…` body
    section ended up nested inside the badge table — structurally
    valid HTML5, but every subsequent row rendered squeezed inside a
    2-column badge cell.

    Not something html5lib catches (the input IS valid HTML5). Pure
    domain heuristic on the canonical_template's class convention.
    """
    for tag, attrib, chain in _walk_etree(tree, []):
        if tag not in ("td", "th"):
            continue
        own_pads = _pad_classes(attrib.get("class", ""))
        if not own_pads:
            continue
        for ancestor_tag, ancestor_attrib in chain:
            if ancestor_tag not in ("td", "th"):
                continue
            ancestor_pads = _pad_classes(ancestor_attrib.get("class", ""))
            if not ancestor_pads:
                continue
            own_label = " ".join(sorted(own_pads))
            ancestor_label = " ".join(sorted(ancestor_pads))
            key = ("EP001", f"{ancestor_label}>{own_label}")
            if key in dedup:
                break
            dedup.add(key)
            yield Finding(
                rule="EP001",
                message=(
                    f'<td class="{own_label}"> is nested inside another '
                    f'<td class="{ancestor_label}">. The canonical_template\'s *-pad '
                    f"cells (header-pad / status-pad / body-pad / footer-pad / "
                    f"table-pad) are section wrappers that sit DIRECTLY in the "
                    f'<tr> rows of <table class="email-container">. Nesting one '
                    f"inside another almost always means an inner badge or utility "
                    f"<table> inside a *-pad cell was never closed, so subsequent "
                    f"body sections render squeezed inside it instead of at the "
                    f"top level."
                ),
                hint=(
                    "Confirm every inner <table> inside a *-pad <td> is closed with "
                    '</table></td></tr> before the next <tr><td class="body-pad">…'
                    " row begins."
                ),
            )
            break


# ── Outlook/Word-engine compatibility (EO001 / EO002) ────────────────────────
#
# Origin: the deal-sheet email regression (2026-07). Traders on Outlook
# desktop (Word rendering engine) reported the dark header band rendering
# white (making its light text invisible), vanished card fills, and the
# per-deal accent stripe missing entirely. Root causes: (a) CSS-only
# `background-color` cell fills, which Word applies inconsistently — the
# HTML `bgcolor` attribute is the reliable fallback; (b) single-side CSS
# borders (`border-left: 3px solid …`) on `<td>`s, which Word drops or
# mangles. The linter previously had no rule for either, so send_email
# returned html_validated=True on markup that broke in Outlook.

_CSS_BACKGROUND_DECL_PATTERN = re.compile(r"(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)", re.IGNORECASE)
_HEX_COLOR_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_CSS_SIDE_BORDER_PATTERN = re.compile(
    r"(?:^|;)\s*(border-(?:left|right|top|bottom))(?:-width|-style|-color)?\s*:\s*([^;]+)",
    re.IGNORECASE,
)
# `border-left:0` / `border-top:none` are resets, not visible borders.
_BORDER_RESET_PATTERN = re.compile(r"^(?:none|0(?:px)?)\s*(?:;|$|\s)")


def _normalize_hex(value: str) -> str | None:
    """`#abc` / `#AaBbCc` → `#AABBCC`; None if not a 3/6-digit hex color."""
    value = value.strip()
    if not _HEX_COLOR_PATTERN.fullmatch(value):
        return None
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value.upper()


def _css_background_hex(style: str) -> str | None:
    """Return the normalized hex of the winning `background`/`background-color`
    declaration in an inline style, or None when there is no hex-valued fill
    (named colors, rgb(), transparent, gradients are skipped — conservative)."""
    result: str | None = None
    for match in _CSS_BACKGROUND_DECL_PATTERN.finditer(style):
        hex_match = _HEX_COLOR_PATTERN.search(match.group(1))
        # A later non-hex declaration (e.g. `background: transparent`)
        # overrides an earlier hex one — last declaration wins in CSS.
        result = _normalize_hex(hex_match.group(0)) if hex_match else None
    return result


def _check_outlook_compat(tree: Any, dedup: set[tuple[str, str]]) -> Iterator[Finding]:
    """EO001 / EO002 — markup that renders fine in WebKit/Gecko clients but
    breaks in Outlook desktop (Word engine).

    EO001: a `<td>`/`<th>`/`<table>` whose inline style sets a hex
    `background-color` (or `background`) MUST also carry a `bgcolor`
    attribute with the SAME hex — belt and suspenders: bgcolor for Word,
    CSS for everyone else. Fires on a missing bgcolor AND on a mismatched
    one (a mismatch also breaks the theme hex find-replace contract,
    which rewrites both only when the literal hex strings are identical).

    EO002: a single-side CSS border (`border-left/right/top/bottom`, incl.
    the -width/-style/-color longhands) on a `<td>`/`<th>`. Word drops or
    mangles per-side cell borders — accent stripes and hairline dividers
    disappear. The robust pattern is a filled cell: a dedicated narrow
    `<td width="3" bgcolor="#…">&nbsp;</td>` for vertical accents, or a
    `<td height="1" bgcolor="#…">` divider row for horizontal rules.
    `border-left:0` / `border-top:none` resets are ignored, as is the
    four-side `border:` shorthand (uniform borders survive Word). Only
    inline `style=` attributes are inspected — `<style>`-block rules
    (e.g. the mobile @media overrides) never reach Word, which strips
    the block entirely.

    Dedup collapses repeated instances of the same pattern (e.g. one
    finding for twelve identical per-deal card cells).
    """
    for tag, attrib, _chain in _walk_etree(tree, []):
        if tag not in ("td", "th", "table"):
            continue
        style = attrib.get("style", "")
        if not style:
            continue

        css_hex = _css_background_hex(style)
        if css_hex:
            bgcolor_raw = (attrib.get("bgcolor") or "").strip()
            if not bgcolor_raw:
                key = ("EO001", f"missing:{tag}:{css_hex}")
                if key not in dedup:
                    dedup.add(key)
                    yield Finding(
                        rule="EO001",
                        message=(
                            f"<{tag}> sets CSS background-color {css_hex} but has no bgcolor "
                            f"attribute. Outlook desktop (Word engine) applies CSS cell fills "
                            f"inconsistently — the fill can render white/transparent, hiding "
                            f"any light-colored text on it."
                        ),
                        hint=(
                            f'Add bgcolor="{css_hex}" to the element and keep the inline CSS too '
                            f"(bgcolor for Word, CSS for everyone else). Use the identical hex "
                            f"string in both so theme hex find-replace rewrites them together."
                        ),
                    )
            else:
                bgcolor_norm = _normalize_hex(bgcolor_raw) or bgcolor_raw.lower()
                if bgcolor_norm != css_hex:
                    key = ("EO001", f"mismatch:{tag}:{bgcolor_norm}:{css_hex}")
                    if key not in dedup:
                        dedup.add(key)
                        yield Finding(
                            rule="EO001",
                            message=(
                                f'<{tag}> bgcolor="{bgcolor_raw}" does not match its CSS '
                                f"background-color {css_hex} — Word-engine clients show one "
                                f"color, everything else shows the other, and theme hex "
                                f"find-replace only rewrites whichever string it matches."
                            ),
                            hint="Use the identical #RRGGBB hex in both the bgcolor attribute and the inline CSS.",
                        )

        if tag in ("td", "th"):
            for match in _CSS_SIDE_BORDER_PATTERN.finditer(style):
                prop = match.group(1).lower()
                value = match.group(2).strip().lower()
                if _BORDER_RESET_PATTERN.match(value):
                    continue
                key = ("EO002", f"{tag}:{prop}")
                if key in dedup:
                    continue
                dedup.add(key)
                yield Finding(
                    rule="EO002",
                    message=(
                        f"<{tag}> uses a single-side CSS border ({prop}: {value}). Outlook "
                        f"desktop (Word engine) drops or mangles per-side borders on table "
                        f"cells, so accent stripes and hairline dividers disappear."
                    ),
                    hint=(
                        "Use a filled cell instead: a dedicated narrow "
                        '<td width="3" bgcolor="#RRGGBB" style="background-color:#RRGGBB;">&nbsp;</td> '
                        'for a vertical accent stripe, or a <td height="1" bgcolor="#RRGGBB"> '
                        "divider row for a horizontal rule."
                    ),
                )


# ── Column-count consistency (EH006) ─────────────────────────────────────────


def _check_column_consistency(tree: Any) -> Iterator[Finding]:
    """EH006 — every row in a multi-row table should have the same column
    count (respecting colspan). A mismatch is a common signal of a
    forgotten `</td>` or stray cell.
    """

    def find_tables(elem: Any) -> Iterator[Any]:
        for child in elem:
            if _strip_ns(child.tag) == "table":
                yield child
            yield from find_tables(child)

    def collect_rows(elem: Any, out: list[Any]) -> None:
        for child in elem:
            tag = _strip_ns(child.tag)
            if tag == "tr":
                out.append(child)
            elif tag in ("thead", "tbody", "tfoot"):
                collect_rows(child, out)

    for table in find_tables(tree):
        rows: list[Any] = []
        collect_rows(table, rows)
        if len(rows) < 2:
            continue
        cell_counts: list[int] = []
        for row in rows:
            count = 0
            for cell in row:
                if _strip_ns(cell.tag) in ("td", "th"):
                    try:
                        count += max(1, int(cell.attrib.get("colspan", "1")))
                    except ValueError:
                        count += 1
            cell_counts.append(count)
        first = cell_counts[0]
        if first <= 1:
            continue
        mismatched = [c for c in cell_counts[1:] if c != first]
        if mismatched:
            yield Finding(
                rule="EH006",
                message=(
                    f"Table has rows with inconsistent column counts "
                    f"(first row has {first} columns, but later rows "
                    f"have {', '.join(str(c) for c in sorted(set(mismatched)))})"
                ),
                hint="Possible nesting error — check colspan and closing tags.",
            )


# ── Empty-table / missing-content warnings (EH007, EH008, EH009, EH010) ──────


def _check_tag_warnings(tree: Any) -> Iterator[Finding]:
    """EH007/EH008/EH009/EH010 — bulk warnings derived from the
    constructed tree after parsing.

    These are advisory: a table without `<tr>` rows or an email with no
    visible content tags is almost certainly broken, but we don't block
    on them — the agent might be sending a 1x1 tracking pixel or a
    text-only `<pre>` block.
    """
    tag_counts: Counter[str] = Counter()
    max_depth = 0

    def visit(elem: Any, depth: int) -> None:
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        for child in elem:
            tag_counts[_strip_ns(child.tag)] += 1
            visit(child, depth + 1)

    visit(tree, 0)

    visible = {"div", "p", "span", "table", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "li"}
    if not any(tag_counts[t] > 0 for t in visible):
        yield Finding(
            rule="EH009",
            message="No common content tags found (div, p, table, etc.) - email may appear empty",
        )

    if tag_counts["table"] > 0:
        if tag_counts["tr"] == 0:
            yield Finding(rule="EH007", message="Table found without any <tr> rows")
        if tag_counts["td"] == 0 and tag_counts["th"] == 0:
            yield Finding(rule="EH008", message="Table found without any <td> or <th> cells")

    if max_depth > 50:
        yield Finding(
            rule="EH010",
            message=f"Very deep nesting detected ({max_depth} levels) - may cause rendering issues",
        )


# ── Helper functions used by multiple checks ────────────────────────────────


def detect_html_content(content: str) -> bool:
    """Return True if `content` looks like HTML.

    Used by callers to auto-correct a `content_type="text/plain"` arg
    when the body contains HTML tags.
    """
    if not content or not isinstance(content, str):
        return False
    if content.strip().lower().startswith("<!doctype"):
        return True
    if _HTML_TAG_PATTERN.search(content):
        return True
    return bool(_HTML_ENTITY_PATTERN.search(content) and "<" in content and ">" in content)


def resolve_content_type(content: str, explicit_type: str | None) -> tuple[str, bool]:
    """Return `(resolved_content_type, was_auto_corrected)`.

    Auto-corrects `text/plain` → `text/html` when HTML is detected, so a
    caller that forgot to set the type can't accidentally send raw HTML
    source as plain text. Undeclared content without HTML is plain text.
    The reverse (plain text declared as HTML) is kept as declared, and EH013
    then says the declaration is wrong.
    """
    is_html = detect_html_content(content)
    if not explicit_type:
        return ("text/html" if is_html else DEFAULT_CONTENT_TYPE, False)
    explicit_is_html = explicit_type.lower() == "text/html"
    if is_html and not explicit_is_html:
        return ("text/html", True)
    return (explicit_type, False)


def extract_cid_references(html_content: str) -> set[str]:
    """Return the set of cid: identifiers referenced in `<img src="cid:…">`.

    Callers cross-check this against the `inline_attachments` they were
    given; mismatches surface as EI005 / EI006.
    """
    return {m.group(1).strip() for m in _CID_PATTERN.finditer(html_content) if m.group(1).strip()}


def find_unresolved_template_tokens(content: str) -> list[str]:
    """Return sorted list of `${…}`, `{{…}}`, `{name[:fmt]}` survivors.

    PR #195 history: the single-brace pattern used to also match inside
    `{{…}}` / `${…}` and surface a misleading single-brace fragment, so
    agents stripped valid `{{handlebars}}` thinking they were the
    problem. The patterns above are negative-lookbehind / negative-
    lookahead anchored to avoid that.
    """
    matches: set[str] = set()
    for pattern in _UNRESOLVED_TEMPLATE_PATTERNS:
        for match in pattern.finditer(content):
            matches.add(match.group(0))
    return sorted(matches)


# ── Subject checks ──────────────────────────────────────────────────────────


def _check_subject(subject: str) -> Iterator[Finding]:
    if not subject or not subject.strip():
        yield Finding(rule="EL003", message="Email subject is empty.")
        return
    tokens = find_unresolved_template_tokens(subject)
    if tokens:
        sample, suffix = _sample_and_suffix(tokens)
        yield Finding(
            rule="ET002",
            message=f"Unresolved template tokens in subject: {sample}{suffix}",
            hint="Resolve every placeholder before sending; tokens like ${tool:...} or {{name}} are programmatic, not literal text.",
        )


# ── Body checks ─────────────────────────────────────────────────────────────


def _check_body_placeholders(content: str) -> Iterator[Finding]:
    """EL001 — body is literally `html_content` / `BODY` / etc.

    Catches agents that called send_email with the placeholder string
    they were shown in the tool docs instead of real HTML.
    """
    if content.strip() in _PLACEHOLDER_VALUES:
        yield Finding(
            rule="EL001",
            message=f"Email content appears to be a placeholder: '{content.strip()}'",
            hint="Pass the rendered HTML body, not the variable name from the docs.",
        )


def _check_body_length(content: str) -> Iterator[Finding]:
    if not content or len(content.strip()) < 5:
        yield Finding(rule="EL002", message="Email content is too short or empty.")


def _check_body_tokens(content: str) -> Iterator[Finding]:
    tokens = find_unresolved_template_tokens(content)
    if tokens:
        sample, suffix = _sample_and_suffix(tokens)
        yield Finding(
            rule="ET001",
            message=f"Unresolved template tokens found: {sample}{suffix}",
            hint="Resolve every placeholder before sending; recipients should never see raw `{{…}}` or `${…}`.",
        )


def _check_body_html_marker(content: str, content_type: str | None) -> Iterator[Finding]:
    """EH013 — caller said it's HTML but no HTML tags are present.

    Usually means a plaintext blob was passed to a code path that demands
    HTML. The caller (ses_sender) is expected to have already run
    `resolve_content_type` first, so this only fires on explicit lies.
    """
    if (
        content_type == "text/html"
        and not _HTML_TAG_PATTERN.search(content)
        and not content.strip().lower().startswith("<!doctype")
    ):
        yield Finding(
            rule="EH013",
            message="Content type is 'text/html' but no HTML tags were found.",
            hint="Either wrap the prose in HTML tags or pass content_type='text/plain'.",
        )


# ── Branding ────────────────────────────────────────────────────────────────

# The canonical_template's frame: the container table, the branded header
# cell and the footer cell inside it. What sits between them is free.
FRAME_CLASSES: Final[tuple[str, ...]] = ("email-container", "header-pad", "footer-pad")


def _class_tokens(elem: Any) -> set[str]:
    return set(str(elem.get("class") or "").split())


def _missing_frame_parts(content: str) -> list[str]:
    """The frame parts absent from the parsed document, in FRAME_CLASSES order.

    Parsed, not pattern-matched, so class names in a comment or text do not
    count and unquoted attributes do. The header and footer must be cells
    inside the container table, as the template nests them.
    """
    tree = html5lib.parse(content, namespaceHTMLElements=False)
    best = list(FRAME_CLASSES)
    for container in tree.iter():
        if _strip_ns(container.tag) != "table" or "email-container" not in _class_tokens(container):
            continue
        # Each container is judged alone: a header in one table and a footer
        # in another is still no frame.
        cells = [el for el in container.iter() if _strip_ns(el.tag) in {"td", "th"}]
        missing = [name for name in FRAME_CLASSES[1:] if not any(name in _class_tokens(c) for c in cells)]
        if not missing:
            return []
        if len(missing) < len(best):
            best = missing
    return best


def _check_branded_frame(content: str) -> Iterator[Finding]:
    """EB001 — HTML that does not sit in the canonical_template frame.

    A model that skips render_email tends to send a bare page (white
    background, a line of text), which reaches the client unbranded. The
    frame is the container table with the header and footer cells in it;
    any body inside it passes, so a hand-built section is never refused for
    being custom.
    """
    missing = _missing_frame_parts(content)
    if missing:
        yield Finding(
            rule="EB001",
            message=f"HTML email has no branded frame (missing: {', '.join(missing)}).",
            hint=(
                "Build the email with render_email and send the content_file it returns (pass its "
                "inline_attachments too). A section no block covers still goes inside the "
                "canonical_template frame. Only a deliberately unbranded note may skip the frame, "
                "and it is sent as plain text (content_type='text/plain'), never as bare HTML."
            ),
        )


# ── Mobile responsiveness (EM001 / EM002 / EM003 + auto-normalizer) ──────────

_TABLE_OPEN_CLOSE_RE = re.compile(r"<(/?)table\b[^>]*>", re.IGNORECASE)
_TR_BLOCK_RE = re.compile(r"(<tr\b[^>]*>)((?:(?!<tr\b).)*?)(</tr\s*>)", re.IGNORECASE | re.DOTALL)
_TH_BLOCK_RE = re.compile(r"(<th\b[^>]*>)((?:(?!<th\b).)*?)(</th\s*>)", re.IGNORECASE | re.DOTALL)
_TD_BLOCK_RE = re.compile(r"(<td\b[^>]*>)((?:(?!<td\b).)*?)(</td\s*>)", re.IGNORECASE | re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_CLASS_ATTR_RE = re.compile(r'\bclass=(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL)
_STYLE_ATTR_FULL_RE = re.compile(r'\bstyle=(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL)
_WIDTH_ATTR_RE = re.compile(r'\bwidth=(["\']?)(\d+%?)\1', re.IGNORECASE)

_MOBILE_MEDIA_FALLBACK_RULES = """\
          .email-container { width: 100% !important; max-width: 100% !important; }
          .header-pad, .body-pad, .footer-pad { padding-left: 16px !important; padding-right: 16px !important; }
          .status-pad { padding-left: 16px !important; padding-right: 16px !important; }
          .table-pad { padding-left: 12px !important; padding-right: 12px !important; }
          .header-kicker, .header-date { font-size: 11px !important; }
          .header-brand { font-size: 21px !important; line-height: 26px !important; }
          .header-logo img { width: auto !important; height: auto !important; max-width: 132px !important; max-height: 36px !important; }
          .header-meta { font-size: 13px !important; line-height: 19px !important; }
          .mobile-stack { display: block !important; width: 100% !important; box-sizing: border-box !important; margin-bottom: 8px !important; }
          .mobile-stack:last-child { margin-bottom: 0 !important; }
          .data-table { width: 100% !important; max-width: 100% !important; border: 0 !important; border-collapse: separate !important; border-spacing: 0 !important; table-layout: fixed !important; }
          .data-table thead { display: none !important; }
          .data-table tbody, .data-table tr, .data-table td { display: block !important; width: 100% !important; }
          .data-table tr { width: 100% !important; margin-bottom: 12px !important; }
          .data-table tr:last-child { margin-bottom: 0 !important; }
          .data-table td { box-sizing: border-box !important; background-color: #FAFAFA !important; font-size: 15px !important; line-height: 20px !important; padding: 8px 10px !important; text-align: left !important; white-space: normal !important; word-break: break-word !important; border-left: 1px solid #D7DEEE !important; border-right: 1px solid #D7DEEE !important; border-bottom: 1px solid #E6EBF5 !important; }
          .data-table td:first-child { border-top: 1px solid #D7DEEE !important; }
          .data-table td:last-child { border-bottom: 0 !important; }
          .data-table tr td:last-child { border-bottom: 1px solid #D7DEEE !important; }
          .data-table .campaign-name { background-color: #EEF3FF !important; font-weight: 700 !important; }
          .data-table td .mobile-label, .mobile-label { display: block !important; min-width: 0 !important; max-height: none !important; overflow: visible !important; font-size: 13px !important; line-height: 18px !important; font-weight: 700 !important; color: #586F7C !important; margin-bottom: 4px !important; }"""


def _add_class_to_tag(open_tag: str, new_class: str) -> str:
    m = _CLASS_ATTR_RE.search(open_tag)
    if m:
        classes = m.group(2).split()
        if new_class in classes:
            return open_tag
        updated = " ".join([*classes, new_class])
        return open_tag[: m.start()] + f'class={m.group(1)}{updated}{m.group(1)}' + open_tag[m.end() :]
    return open_tag[:-1] + f' class="{new_class}">'


def _remove_class_from_tag(open_tag: str, target_class: str) -> str:
    m = _CLASS_ATTR_RE.search(open_tag)
    if not m:
        return open_tag
    classes = [c for c in m.group(2).split() if c != target_class]
    if len(classes) == len(m.group(2).split()):
        return open_tag
    if not classes:
        return open_tag[: m.start()].rstrip() + open_tag[m.end() :]
    updated = " ".join(classes)
    return open_tag[: m.start()] + f'class={m.group(1)}{updated}{m.group(1)}' + open_tag[m.end() :]


def _default_pct_widths(n: int) -> list[int]:
    if n <= 1:
        return [100]
    first = 24 if n >= 6 else (40 if n >= 3 else 50)
    rem = 100 - first
    share, extra = divmod(rem, n - 1)
    return [first, *(share + (1 if i < extra else 0) for i in range(n - 1))]


def _upgrade_single_data_table(table_html: str) -> str:
    """Upgrade a single data table with .data-table, width %, .campaign-name, and .mobile-label spans."""
    first_gt = table_html.find(">")
    if first_gt == -1:
        return table_html
    open_tag = table_html[: first_gt + 1]
    inner_and_close = table_html[first_gt + 1 :]

    # Extract header labels from the first <tr> that contains <th> cells.
    th_matches = list(_TH_BLOCK_RE.finditer(inner_and_close))
    if not th_matches:
        return table_html

    # Only consider <th> cells belonging to the first header row.
    first_tr_match = _TR_BLOCK_RE.search(inner_and_close)
    if not first_tr_match:
        return table_html
    header_tr_m = None
    for tr_m in _TR_BLOCK_RE.finditer(inner_and_close):
        if _TH_BLOCK_RE.search(tr_m.group(2)):
            header_tr_m = tr_m
            break
    if header_tr_m is None:
        return table_html

    row_ths = list(_TH_BLOCK_RE.finditer(header_tr_m.group(2)))
    if not row_ths:
        return table_html

    headers: list[str] = []
    for th_m in row_ths:
        text = _WHITESPACE_RE.sub(" ", _TAG_STRIP_RE.sub("", th_m.group(2))).strip()
        headers.append(text or "Value")
    num_cols = len(headers)

    # Check if this table already has everything it needs.
    has_data_table_class = "data-table" in open_tag
    has_fixed_layout = "table-layout:fixed" in open_tag.replace(" ", "").lower()
    all_ths_have_pct_width = all(
        bool(_WIDTH_ATTR_RE.search(th_m.group(1)) and "%" in (_WIDTH_ATTR_RE.search(th_m.group(1)).group(2)))  # type: ignore[union-attr]
        for th_m in row_ths
    )
    # Check if all data rows already have mobile-label in every td and no nowrap on wide tables.
    needs_upgrade = not (has_data_table_class and has_fixed_layout and all_ths_have_pct_width)
    if not needs_upgrade:
        tbody_m = re.search(r"<tbody\b[^>]*>(.*?)</tbody\s*>", inner_and_close, re.IGNORECASE | re.DOTALL)
        scan_region = tbody_m.group(1) if tbody_m else inner_and_close[header_tr_m.end() :]
        for tr_m in _TR_BLOCK_RE.finditer(scan_region):
            tr_inner = tr_m.group(2)
            if "colspan=" in tr_inner.lower() and ("height:1px" in tr_inner.replace(" ", "").lower() or 'height="1"' in tr_inner.lower()):
                continue
            tds = list(_TD_BLOCK_RE.finditer(tr_inner))
            if not tds:
                continue
            if any("mobile-label" not in td_m.group(2) for td_m in tds):
                needs_upgrade = True
                break
            if num_cols >= 4 and any("nowrap" in td_m.group(1).lower() for td_m in tds):
                needs_upgrade = True
                break
    if not needs_upgrade:
        return table_html

    # 1. Upgrade <table ...> open tag: ensure class="data-table", width="100%", table-layout:fixed.
    open_tag = _add_class_to_tag(open_tag, "data-table")
    if not _WIDTH_ATTR_RE.search(open_tag):
        open_tag = open_tag[:-1] + ' width="100%">'
    style_m = _STYLE_ATTR_FULL_RE.search(open_tag)
    if style_m:
        style_val = style_m.group(2).strip()
        if "table-layout" not in style_val.lower():
            sep = "" if style_val.endswith(";") else ";"
            style_val = f"{style_val}{sep} table-layout:fixed;"
        open_tag = open_tag[: style_m.start()] + f'style={style_m.group(1)}{style_val}{style_m.group(1)}' + open_tag[style_m.end() :]
    else:
        open_tag = open_tag[:-1] + ' style="width:100%; border-collapse:collapse; table-layout:fixed;">'

    # 2. Upgrade header <tr> <th> cells with percentage widths and word-break.
    widths = _default_pct_widths(num_cols)
    new_ths: list[str] = []
    for idx, th_m in enumerate(row_ths):
        th_open, th_inner, th_close = th_m.group(1), th_m.group(2), th_m.group(3)
        if not _WIDTH_ATTR_RE.search(th_open):
            th_open = th_open[:-1] + f' width="{widths[idx]}%">'
        st_m = _STYLE_ATTR_FULL_RE.search(th_open)
        if st_m:
            st_val = st_m.group(2)
            if num_cols >= 4:
                st_val = re.sub(r"\s*white-space\s*:\s*nowrap\s*;?", "", st_val, flags=re.IGNORECASE)
            if "word-break" not in st_val.lower():
                stripped_st = st_val.strip()
                if not stripped_st:
                    st_val = "word-break:break-word;"
                else:
                    sep = "" if stripped_st.endswith(";") else ";"
                    st_val = f"{stripped_st}{sep} word-break:break-word;"
            th_open = th_open[: st_m.start()] + f'style={st_m.group(1)}{st_val}{st_m.group(1)}' + th_open[st_m.end() :]
        new_ths.append(f"{th_open}{th_inner}{th_close}")
    new_header_tr = f"{header_tr_m.group(1)}{''.join(new_ths)}{header_tr_m.group(3)}"

    # 3. Upgrade data <tr> rows.
    def _upgrade_data_tr(tr_m: re.Match[str]) -> str:
        tr_open, tr_inner, tr_close = tr_m.group(1), tr_m.group(2), tr_m.group(3)
        if _TH_BLOCK_RE.search(tr_inner):
            return tr_m.group(0)
        if "colspan=" in tr_inner.lower() and ("height:1px" in tr_inner.replace(" ", "").lower() or 'height="1"' in tr_inner.lower()):
            return tr_m.group(0)
        # Skip rows that contain nested tables unless they already have mobile-label
        if "<table" in tr_inner.lower():
            return tr_m.group(0)
        tds = list(_TD_BLOCK_RE.finditer(tr_inner))
        if len(tds) != num_cols:
            return tr_m.group(0)
        new_tds: list[str] = []
        for c, td_m in enumerate(tds):
            td_open, td_inner, td_close = td_m.group(1), td_m.group(2), td_m.group(3)
            if c == 0:
                td_open = _add_class_to_tag(td_open, "campaign-name")
            else:
                td_open = _remove_class_from_tag(td_open, "campaign-name")
            st_m = _STYLE_ATTR_FULL_RE.search(td_open)
            if st_m:
                st_val = st_m.group(2)
                if num_cols >= 4:
                    st_val = re.sub(r"\s*white-space\s*:\s*nowrap\s*;?", "", st_val, flags=re.IGNORECASE)
                if "word-break" not in st_val.lower():
                    stripped_st = st_val.strip()
                    if not stripped_st:
                        st_val = "word-break:break-word;"
                    else:
                        sep = "" if stripped_st.endswith(";") else ";"
                        st_val = f"{stripped_st}{sep} word-break:break-word;"
                td_open = td_open[: st_m.start()] + f'style={st_m.group(1)}{st_val}{st_m.group(1)}' + td_open[st_m.end() :]
            if "mobile-label" not in td_inner:
                label_span = (
                    '<span class="mobile-label" style="display:none; font-size:0; line-height:0; '
                    f'max-height:0; overflow:hidden;">{headers[c]}</span>'
                )
                td_inner = f"{label_span}{td_inner}"
            new_tds.append(f"{td_open}{td_inner}{td_close}")
        return f"{tr_open}{''.join(new_tds)}{tr_close}"

    # Replace header_tr and upgrade tbody rows.
    before_header = inner_and_close[: header_tr_m.start()]
    after_header = inner_and_close[header_tr_m.end() :]
    after_header_upgraded = _TR_BLOCK_RE.sub(_upgrade_data_tr, after_header)

    if "<thead" not in before_header.lower() and "<tbody" not in after_header_upgraded.lower():
        close_table_m = re.search(r"</table\s*>\s*$", after_header_upgraded, re.IGNORECASE)
        if close_table_m:
            body_rows = after_header_upgraded[: close_table_m.start()]
            close_tag = after_header_upgraded[close_table_m.start() :]
            return f"{open_tag}{before_header}<thead>{new_header_tr}</thead><tbody>{body_rows}</tbody>{close_tag}"

    return f"{open_tag}{before_header}{new_header_tr}{after_header_upgraded}"


def _upgrade_kpi_card_strips(content: str) -> str:
    """Add class='mobile-stack' to horizontal multi-card KPI rows (2-6 columns of bordered/div cards)."""
    def _check_tr(tr_m: re.Match[str]) -> str:
        tr_open, tr_inner, tr_close = tr_m.group(1), tr_m.group(2), tr_m.group(3)
        if "<th" in tr_inner.lower() or "<table" in tr_inner.lower() or "mobile-label" in tr_inner:
            return tr_m.group(0)
        tds = list(_TD_BLOCK_RE.finditer(tr_inner))
        if not (2 <= len(tds) <= 6):
            return tr_m.group(0)
        # Every td in the strip must be a card cell (has border: in style and <div children, or percentage width + <div)
        for td_m in tds:
            td_open, td_inner = td_m.group(1).lower(), td_m.group(2).lower()
            if "<div" not in td_inner:
                return tr_m.group(0)
            if "border:" not in td_open and "width=" not in td_open and "width:" not in td_open:
                return tr_m.group(0)
        if all("mobile-stack" in td_m.group(1) for td_m in tds):
            return tr_m.group(0)
        rebuilt = []
        last = 0
        for td_m in tds:
            rebuilt.append(tr_inner[last : td_m.start()])
            new_open = _add_class_to_tag(td_m.group(1), "mobile-stack")
            rebuilt.append(f"{new_open}{td_m.group(2)}{td_m.group(3)}")
            last = td_m.end()
        rebuilt.append(tr_inner[last:])
        return f"{tr_open}{''.join(rebuilt)}{tr_close}"

    return _TR_BLOCK_RE.sub(_check_tr, content)


def ensure_mobile_friendly(content: str) -> str:
    """Automatically normalize and upgrade an HTML email for mobile responsiveness.

    Idempotent: emails rendered by `render_email` or `render_deal_sheet_email`
    (and minimal 1-column test fixtures without data tables or `<style>`) are
    returned byte-for-byte unchanged. Hand-built HTML emails that omit
    `.mobile-label` spans, omit or truncate the `@media` block, use a fixed
    `width="600"`/`width="680"` container, or use horizontal multi-column KPI
    card strips are upgraded in place.
    """
    if not content or "email-container" not in content:
        return content

    result = content

    # 1. Normalize fixed-width .email-container and MSO wrapper > 600px.
    def _fix_container_tag(m: re.Match[str]) -> str:
        tag = m.group(0)
        tag = re.sub(r'\bwidth=(["\']?)(?:6\d\d)\1', r'width=\g<1>100%\g<1>', tag, flags=re.IGNORECASE)
        st_m = _STYLE_ATTR_FULL_RE.search(tag)
        if st_m:
            st_val = st_m.group(2)
            st_val = re.sub(r"(?:^|(?<=;))\s*width\s*:\s*6\d\dpx", " width:100%", st_val, flags=re.IGNORECASE).lstrip()
            st_val = re.sub(r"max-width\s*:\s*6[1-9]\dpx", "max-width:600px", st_val, flags=re.IGNORECASE)
            tag = tag[: st_m.start()] + f"style={st_m.group(1)}{st_val}{st_m.group(1)}" + tag[st_m.end() :]
        return tag

    result = re.sub(r"<table\b[^>]*\bemail-container\b[^>]*>", _fix_container_tag, result, flags=re.IGNORECASE)
    result = re.sub(
        r"(<!--\[if mso\]>\s*<table\b[^>]*\bwidth=[\"'])6[1-9]\d([\"'])",
        r"\g<1>600\g<2>",
        result,
        flags=re.IGNORECASE,
    )

    # 2. Upgrade any data tables (tables with class="data-table" or >= 2 <th> cells).
    # Find balanced <table>...</table> spans so nested tables inside cells don't truncate matches.
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for m in _TABLE_OPEN_CLOSE_RE.finditer(result):
        is_close = bool(m.group(1))
        if not is_close:
            stack.append(m.start())
        elif stack:
            start = stack.pop()
            tag_open = result[start : result.find(">", start) + 1]
            table_slice = result[start : m.end()]
            # A data table either carries class="data-table" or has >= 2 <th> cells at its own level (no nested data tables).
            if "data-table" in tag_open or (
                "email-container" not in tag_open
                and len(_TH_BLOCK_RE.findall(table_slice)) >= 2
                and "<table" not in table_slice[len(tag_open) : -len(m.group(0))].lower()
            ):
                spans.append((start, m.end()))

    # Keep only non-overlapping spans and apply from right to left.
    filtered_spans: list[tuple[int, int]] = []
    for s, e in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if not filtered_spans or s >= filtered_spans[-1][1]:
            filtered_spans.append((s, e))
    for s, e in reversed(filtered_spans):
        result = result[:s] + _upgrade_single_data_table(result[s:e]) + result[e:]

    # 3. Upgrade horizontal multi-card KPI strips (e.g. 5-col Spend Snapshot).
    result = _upgrade_kpi_card_strips(result)

    # 4. Ensure <style> has complete @media mobile rules when the email uses <style>, .data-table, or .mobile-stack.
    has_style = "<style" in result.lower()
    needs_table_media = "data-table" in result
    needs_stack_media = "mobile-stack" in result
    if has_style or needs_table_media or needs_stack_media:
        style_m = re.search(r"(<style\b[^>]*>)(.*?)(</style\s*>)", result, flags=re.IGNORECASE | re.DOTALL)
        if style_m:
            style_open, style_css, style_close = style_m.group(1), style_m.group(2), style_m.group(3)
            missing_table_css = needs_table_media and (".data-table" not in style_css or ".mobile-label" not in style_css)
            missing_stack_css = needs_stack_media and (".mobile-stack" not in style_css)
            if missing_table_css or missing_stack_css:
                new_style_css = (
                    "\n        @media only screen and (max-width: 480px) {\n"
                    f"{_MOBILE_MEDIA_FALLBACK_RULES}\n"
                    "        }\n      "
                )
                result = result[: style_m.start()] + f"{style_open}{new_style_css}{style_close}" + result[style_m.end() :]
        elif needs_table_media or needs_stack_media:
            media_block = (
                "<style>\n        @media only screen and (max-width: 480px) {\n"
                f"{_MOBILE_MEDIA_FALLBACK_RULES}\n"
                "        }\n      </style>"
            )
            head_close = re.search(r"</head\s*>", result, flags=re.IGNORECASE)
            if head_close:
                result = result[: head_close.start()] + f"  {media_block}\n    " + result[head_close.start() :]
            else:
                body_open = re.search(r"<body\b", result, flags=re.IGNORECASE)
                if body_open:
                    result = (
                        result[: body_open.start()]
                        + f'<head><meta name="viewport" content="width=device-width, initial-scale=1.0">{media_block}</head>'
                        + result[body_open.start() :]
                    )

    return result


def _check_mobile_friendly(content: str) -> Iterator[Finding]:
    """EM001 / EM002 / EM003 — verify mobile-responsive CSS, .mobile-label spans, and flexible container."""
    tree = html5lib.parse(content, namespaceHTMLElements=False)

    # EM003: Fixed-width .email-container
    for el in tree.iter():
        if _strip_ns(el.tag) == "table" and "email-container" in _class_tokens(el):
            width_attr = (el.get("width") or "").strip()
            style_attr = (el.get("style") or "").lower()
            if (width_attr.isdigit() and int(width_attr) > 480) or re.search(
                r"(?:^|;)\s*width\s*:\s*[5-9]\d\dpx", style_attr
            ):
                yield Finding(
                    rule="EM003",
                    message=f'<table class="email-container"> uses a fixed pixel width ({width_attr or style_attr}) instead of width="100%" with max-width:600px.',
                    hint='Set width="100%" and style="width:100%; max-width:600px; ..." on .email-container so it shrinks cleanly on mobile viewports.',
                )
                break

    # Find data tables (.data-table or tables inside .email-container with >= 2 <th> cells)
    data_tables: list[Any] = []
    for el in tree.iter():
        if _strip_ns(el.tag) != "table" or "email-container" in _class_tokens(el):
            continue
        if "data-table" in _class_tokens(el):
            data_tables.append(el)
        else:
            nested_tables = [t for t in el.iter() if t is not el and _strip_ns(t.tag) == "table"]
            ths = [c for c in el.iter() if _strip_ns(c.tag) == "th"]
            if not nested_tables and len(ths) >= 2:
                data_tables.append(el)

    style_text = " ".join((el.text or "") for el in tree.iter() if _strip_ns(el.tag) == "style")

    # EM001: If the email has data tables (or a <style> block with @media and data tables), it must have @media rules for .data-table and .mobile-label.
    if data_tables and ("@media" not in style_text or ".data-table" not in style_text or ".mobile-label" not in style_text):
        yield Finding(
            rule="EM001",
            message="HTML email contains data tables but is missing the @media (max-width: 480px) rules for .data-table and .mobile-label.",
            hint="Include the complete @media CSS block from canonical_template (or build the email with render_email).",
        )

    # EM002: Every data-table tbody <td> must contain a .mobile-label span.
    missing_label_cells = 0
    for table in data_tables:
        tbodies = [child for child in table if _strip_ns(child.tag) == "tbody"]
        row_parents = tbodies if tbodies else [table]
        for parent in row_parents:
            for tr in parent:
                if _strip_ns(tr.tag) != "tr":
                    continue
                if any(_strip_ns(c.tag) == "th" for c in tr):
                    continue
                tds = [c for c in tr if _strip_ns(c.tag) == "td"]
                if not tds:
                    continue
                if len(tds) == 1 and tds[0].get("colspan") and (
                    tds[0].get("height") == "1" or "height:1px" in (tds[0].get("style") or "").replace(" ", "").lower()
                ):
                    continue
                for td in tds:
                    has_label = any("mobile-label" in _class_tokens(desc) for desc in td.iter())
                    if not has_label:
                        missing_label_cells += 1
    if missing_label_cells > 0:
        yield Finding(
            rule="EM002",
            message=f"Data table is missing <span class=\"mobile-label\"> in {missing_label_cells} <td> cell(s), breaking mobile card stacking.",
            hint="Put <span class=\"mobile-label\" style=\"display:none; font-size:0; line-height:0; max-height:0; overflow:hidden;\">ColumnHeader</span> inside every data-table tbody <td> (or build the email with render_email).",
        )


# ── Image checks ────────────────────────────────────────────────────────────


def _check_images(content: str) -> Iterator[Finding]:
    """Image src must be cid: (with matching inline_attachments) or http(s)://.

    Blocks fabricated `data:` URIs and local `file://` paths, which
    render as broken icons in every real client. Caller is responsible
    for the cid<->inline_attachments cross-check (EI005 / EI006).
    """
    for match in _IMG_SRC_PATTERN.finditer(content):
        src = match.group(1)
        src_stripped = src.strip()
        src_lower = src_stripped.lower()
        if not src_stripped:
            yield Finding(rule="EI001", message="Image tag found with empty src attribute")
        elif src_lower.startswith("file://"):
            yield Finding(
                rule="EI002",
                message=f"Image uses local file path (won't work in email): {src_stripped[:50]}...",
                hint="Local paths are not reachable from recipients' clients. Use cid: or https://.",
            )
        elif src_lower.startswith("data:"):
            yield Finding(
                rule="EI003",
                message=("Image uses a data: URI — fabricated inline images are not allowed."),
                hint=("Reference a real asset via cid: (with a matching inline_attachments entry) or an https:// URL."),
            )
        elif not src_lower.startswith(("cid:", "http://", "https://")):
            yield Finding(
                rule="EI004",
                message=f"Image src must be cid: or http(s):// — got: {src_stripped[:50]}",
                hint="Replace with cid:<inline-id> or an https:// URL.",
            )


# ── CSS checks ──────────────────────────────────────────────────────────────


def _check_css(content: str) -> Iterator[Finding]:
    """Block / warn on CSS properties known to misbehave in email clients.

    Kept hand-rolled rather than data-driven (caniemail JSON) because the
    list is short and stable. Add to this function when a new client
    quirk surfaces — one helper, one rule code, one test.
    """
    lower = content.lower()
    if "rgba(" in lower or "rgba (" in lower:
        yield Finding(
            rule="EC001",
            message=(
                "CSS 'rgba()' colors found — alpha channels are not supported in most "
                "email clients (Outlook strips the alpha and clients render the "
                "underlying color as fully opaque)."
            ),
            hint="Use solid hex (#RRGGBB) or rgb() instead.",
        )
    if "position:fixed" in lower or "position: fixed" in lower:
        yield Finding(
            rule="EC002",
            message="CSS 'position:fixed' found — not supported in most email clients.",
        )
    if "position:absolute" in lower or "position: absolute" in lower:
        yield Finding(
            rule="EC003",
            message="CSS 'position:absolute' found — limited support in email clients.",
        )


# ── Template leakage ─────────────────────────────────────────────────────────


def _check_template_leakage(content: str) -> Iterator[Finding]:
    """Detect canonical_template demo data the agent failed to replace.

    See `_DATA_PREVIEW_ATTR_PATTERN`, `_TIER2_DEMO_PROSE_MARKERS`,
    `_TIER3_DEMO_MARKERS` for the three tiers and their thresholds.
    """
    preview_tokens = sorted({m.group(1) for m in _DATA_PREVIEW_ATTR_PATTERN.finditer(content)})
    if preview_tokens:
        sample, suffix = _sample_and_suffix(preview_tokens)
        yield Finding(
            rule="EL101",
            message=(
                "Template example data detected — `data-preview` attributes from "
                f"canonical_template survived: {sample}{suffix}."
            ),
            hint="Rebuild the email body without copying demo sections.",
        )

    tier2_hits = [m for m in _TIER2_DEMO_PROSE_MARKERS if m in content]
    if tier2_hits:
        sample = "; ".join(f'"{hit[:60]}…"' if len(hit) > 60 else f'"{hit}"' for hit in tier2_hits[:3])
        yield Finding(
            rule="EL102",
            message=(f"Template example data detected — canonical_template demo prose is still in the body: {sample}."),
            hint="Remove the Executive Summary, Risk, Opportunity, and Closing example sections.",
        )

    tier3_hits = [m for m in _TIER3_DEMO_MARKERS if m in content]
    if len(tier3_hits) >= _TIER3_BLOCK_THRESHOLD:
        sample, suffix = _sample_and_suffix(tier3_hits)
        yield Finding(
            rule="EL103",
            message=(
                f"Template example data detected — {len(tier3_hits)} demo markers "
                f"co-occur in the body ({sample}{suffix})."
            ),
            hint="Real data would not collocate these values — regenerate from real reporting.",
        )
    elif tier3_hits:
        sample, _suffix = _sample_and_suffix(tier3_hits, head=5)
        yield Finding(
            rule="EL104",
            message=f"Possible template example data: {sample}.",
            hint="Verify these are real campaign values — if copied from canonical_template, replace them.",
        )


def _sample_and_suffix(items: Iterable[str], head: int = 5) -> tuple[str, str]:
    """Render '..., …, …' + ' (+N more)' suffix for a list of finding samples."""
    seq = list(items)
    sample = ", ".join(seq[:head])
    remainder = len(seq) - min(len(seq), head)
    suffix = f" (+{remainder} more)" if remainder > 0 else ""
    return sample, suffix


# ── HTML aggregate checks ────────────────────────────────────────────────────


def _check_html_structure(content: str) -> Iterator[Finding]:
    """Run all parser-based structural checks.

    Order:

    1. EH001 foster-parent (from html5lib parser.errors)
    2. EH002 loose text (from html5lib tokenizer-phase hook)
    3. EH003 unclosed `<table>` (from html5lib's `eof-in-table`)
    4. EH004 extra/mismatched close tag — ONLY when EH001/EH003 didn't
       fire (they cascade spurious end-tag errors as a consequence; the
       agent fixes the root cause and any genuine EH004 surfaces on the
       next pass)
    5. EP001 pad-nesting (from etree walk — domain heuristic)
    6. EO001/EO002 Outlook/Word-engine compat (from etree walk)
    7. EH006 column consistency (from etree walk)
    8. EH007/8/9/10 tag-count warnings (from etree walk)

    Cascade-suppression for EH004 is the only inter-check dependency;
    everything else is independent and order-only-affects-output-order.
    """
    if not content or not content.strip():
        yield Finding(rule="EH012", message="HTML content is empty")
        return

    parser = _TrackingHTMLParser()
    try:
        tree = parser.parse(content)
    except Exception as exc:  # noqa: BLE001 — surface as a finding, not a crash
        yield Finding(rule="EH011", message=f"HTML parsing error: {exc}")
        return

    dedup: set[tuple[str, str]] = set()
    # EH001 / EH002 / EH003 first so we know whether EH004 should be
    # suppressed as cascade noise. Buffer them so we can keep the
    # documented output order if everything fires.
    structural = list(_check_foster_parents(parser, dedup))
    structural.extend(_check_loose_text(parser, dedup))
    unclosed = list(_check_unclosed(parser))
    root_cause_fired = bool(structural) or bool(unclosed)

    yield from structural
    yield from unclosed
    yield from _check_bad_close(parser, root_cause_fired, dedup)
    yield from _check_pad_nesting(tree, dedup)
    yield from _check_outlook_compat(tree, dedup)
    yield from _check_column_consistency(tree)
    yield from _check_tag_warnings(tree)


# ── Public API ───────────────────────────────────────────────────────────────


def validate(
    content: str,
    *,
    subject: str | None = None,
    content_type: str = "text/html",
    require_frame: bool = False,
) -> list[Finding]:
    """Run every check applicable to `content` (+ `subject` if given).

    Order: subject → body length/placeholders/tokens/html-marker → CSS
    → images → HTML structure (parser walk) → template leakage. Order
    only affects the order of returned findings; severity decisions are
    made per-finding via `RULES`.

    `content_type` defaults to `text/html` so callers that want the full
    HTML pass don't need to pass it. Pass `text/plain` to skip the
    HTML-structure checks for plaintext bodies (still runs token,
    placeholder, leakage checks).

    `require_frame=True` also refuses HTML outside the canonical_template
    frame (EB001). Senders pass it; it stays off by default so a fragment
    (one component block) can be linted on its own.
    """
    findings: list[Finding] = []

    if subject is not None:
        findings.extend(_check_subject(subject))

    findings.extend(_check_body_placeholders(content))
    findings.extend(_check_body_length(content))
    findings.extend(_check_body_tokens(content))
    findings.extend(_check_body_html_marker(content, content_type))

    if content_type == "text/html":
        findings.extend(_check_css(content))
        findings.extend(_check_images(content))
        findings.extend(_check_html_structure(content))
        findings.extend(_check_template_leakage(content))
        if require_frame:
            findings.extend(_check_branded_frame(content))
            findings.extend(_check_mobile_friendly(content))

    return findings


def partition_findings(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Split a finding list into `(errors, warnings)` preserving order."""
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    return errors, warnings


def format_findings(findings: list[Finding]) -> str:
    """Render findings as a bulleted multi-line block for tool responses."""
    if not findings:
        return ""
    return "\n  - ".join(f.format() for f in findings)


# ── Backward-compat shims for the existing callers ──────────────────────────
#
# Old API was `_validate_html(content) -> (errors_list[str], warnings_list[str])`.
# These let ses_sender.py keep its current control flow
# while switching to the structured backend. New code should call
# `validate()` and consume `Finding`s directly.


def validate_html_legacy(content: str) -> tuple[list[str], list[str]]:
    """Old `_validate_html` shape: html-only, returns (errors, warnings) of strings."""
    findings: list[Finding] = []
    if content and content.strip():
        findings.extend(_check_css(content))
        findings.extend(_check_images(content))
        findings.extend(_check_html_structure(content))
    else:
        findings.append(Finding(rule="EH012", message="HTML content is empty"))
    errors, warnings = partition_findings(findings)
    return [f.format() for f in errors], [f.format() for f in warnings]


def validate_email_subject_legacy(subject: str) -> str | None:
    """Old `_validate_email_subject` shape: returns first error string, else None."""
    for finding in _check_subject(subject):
        if finding.severity == "error":
            return finding.format()
    return None


def validate_email_body_legacy(content: str, content_type: str | None) -> str | None:
    """Old `_validate_email_body` shape: returns first error string, else None."""
    for finding in _check_body_placeholders(content):
        if finding.severity == "error":
            return finding.format()
    for finding in _check_body_length(content):
        if finding.severity == "error":
            return finding.format()
    for finding in _check_body_tokens(content):
        if finding.severity == "error":
            return finding.format()
    for finding in _check_body_html_marker(content, content_type):
        if finding.severity == "error":
            return finding.format()
    return None


def check_template_leakage_legacy(content: str) -> tuple[list[str], list[str]]:
    """Old `_check_template_leakage` shape: (errors_list, warnings_list) of strings."""
    findings = list(_check_template_leakage(content))
    errors, warnings = partition_findings(findings)
    return [f.format() for f in errors], [f.format() for f in warnings]

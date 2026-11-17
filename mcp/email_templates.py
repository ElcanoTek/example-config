"""Email templates: the fixed shape of the emails this team sends again and again.

A template is one YAML file in the bundle's `email_templates/` directory. It
names the email's sections in order, the `render_email` block each one is
built from, what belongs in it and how long it may be. `render_email` checks
the blocks it is given against the template before it renders, so a weekly
client update has the same sections at the same length whoever asks for it and
in whichever chat.

The same file is what fleet's Email templates library shows (the sections with
their rules, beside a preview rendered from the template's `sample`), so what a
person reads there is what the renderer enforces.

Templates come from two places. The bundle's own are Git-tracked and reviewed.
Team templates are added from a chat (`email_template_store`, behind an
approval card) into the directory `EMAIL_TEMPLATES_TEAM_DIR` names, which fleet
also reads for its library; they follow exactly the same format and checks. A
team template can never take a bundle template's id.

The module only reads template files. `ses_sender` owns the tools,
`email_render` the HTML, `email_template_store` the writing of team templates.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from re import _parser as _re_parser  # the parsed pattern, for _slow_pattern
from typing import Any

import yaml

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BUNDLE_ROOT / "email_templates"

# render_email's block types (email_render.BLOCK_TYPES; a test holds the two in step).
BLOCK_TYPES = ("text", "metrics", "headline_metrics", "callout", "table", "chart", "cta", "closing")
AUDIENCES = ("client", "internal")
# What each limit counts, and the blocks it can apply to.
LIMITS: dict[str, tuple[str, ...]] = {
    "max_words": ("text", "callout", "closing"),
    "max_paragraphs": ("text", "callout", "closing"),
    "max_bullets": ("text", "callout"),
    "min_bullets": ("text", "callout"),
    "max_words_per_bullet": ("text", "callout"),
    "max_items": ("metrics", "headline_metrics"),
    "min_items": ("metrics", "headline_metrics"),
    "max_rows": ("table",),
    "max_columns": ("table",),
}
# The block key a section's fixed heading is written to.
_HEADING_KEY = {
    "text": "heading",
    "metrics": "heading",
    "headline_metrics": "heading",
    "callout": "title",
    "table": "heading",
    "chart": "heading",
    "closing": "heading",
}
_TEMPLATE_KEYS = {
    "name",
    "description",
    "audience",
    "use_when",
    "based_on",
    "theme",
    "subject",
    "header",
    "max_words",
    "rules",
    "never_say",
    "sections",
    "sample",
    "fields",
    "promptTemplate",
    # Stamped on a team template when it is saved (email_template_store).
    "created_by",
    "created_at",
    "updated_at",
}
_SECTION_KEYS = {"id", "title", "heading", "block", "kind", "required", "instruction", "limits"}
_ID = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
_WORD = re.compile(r"[^\s]+")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
MAX_TEMPLATE_BYTES = 256 << 10
# The longest text a never_say pattern may match, and the size of the pieces a
# long field is searched in (_search).
MAX_MATCH_CHARS = 200
SEARCH_WINDOW = 3 * MAX_MATCH_CHARS


class TemplateError(ValueError):
    """A template file is missing or malformed; the message says how."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _whole(value: Any) -> bool:
    """A whole number, which `true` is not (bool is an int in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if _text(item)]
    return []


def count_words(*values: Any) -> int:
    """Words a reader reads: bold markers are not words, numbers and symbols are."""
    total = 0
    for value in values:
        for item in _strings(value):
            total += len(_WORD.findall(_BOLD.sub(r"\1", item)))
    return total


def _block_prose(block: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(paragraphs, bullets) of a prose block, read the way email_render reads them."""
    kind = _text(block.get("type")).lower()
    if kind == "callout":
        paragraphs = _strings(block.get("paragraphs", block.get("text", block.get("body"))))
    else:
        paragraphs = _strings(block.get("paragraphs", block.get("text")))
    return paragraphs, _strings(block.get("bullets"))


def _require_text(mapping: Mapping[str, Any], keys: Sequence[str], where: str, issues: list[str]) -> None:
    """Report each of `keys` that is present and not text.

    The checks below read values through `_text`, which would turn a mapping or
    a number into a string and let it pass; fleet and the writer expect text.
    """
    for key in keys:
        if mapping.get(key) is not None and not isinstance(mapping[key], str):
            issues.append(f"{where}'{key}' must be text")


def _validate_section(raw: Any, where: str, seen: set[str], issues: list[str]) -> None:
    if not isinstance(raw, Mapping):
        issues.append(f"{where} must be a mapping")
        return
    unknown = sorted(set(raw) - _SECTION_KEYS)
    if unknown:
        issues.append(f"{where}: unknown key(s) {', '.join(unknown)}")
    _require_text(raw, ("id", "title", "heading", "block", "kind", "instruction"), f"{where}: ", issues)
    section_id = _text(raw.get("id"))
    if not _ID.fullmatch(section_id):
        issues.append(f"{where}: 'id' must be lowercase letters, digits, - or _")
    elif section_id in seen:
        issues.append(f"{where}: duplicate id {section_id!r}")
    seen.add(section_id)
    if not _text(raw.get("title")):
        issues.append(f"{where}: 'title' is required")
    if not _text(raw.get("instruction")):
        issues.append(f"{where}: 'instruction' is required")
    block = _text(raw.get("block"))
    if block not in BLOCK_TYPES:
        issues.append(f"{where}: 'block' must be one of {', '.join(BLOCK_TYPES)}")
        return
    if "required" in raw and not isinstance(raw["required"], bool):
        issues.append(f"{where}: 'required' must be true or false")
    if raw.get("heading") is not None and block not in _HEADING_KEY:
        issues.append(f"{where}: a {block} block has no heading")
    if raw.get("heading") == "" and block in ("metrics", "headline_metrics", "callout"):
        issues.append(f"{where}: a {block} block always shows a heading; give one, or leave 'heading' out")
    if block == "callout":
        if _text(raw.get("kind")) not in ("risk", "opportunity"):
            issues.append(f"{where}: a callout section needs kind: risk or opportunity")
    elif raw.get("kind") is not None:
        issues.append(f"{where}: 'kind' applies to a callout only")
    limits = raw.get("limits") or {}
    if not isinstance(limits, Mapping):
        issues.append(f"{where}: 'limits' must be a mapping")
        return
    for key, value in limits.items():
        if key not in LIMITS:
            issues.append(f"{where}: unknown limit {key!r}")
        elif block not in LIMITS[key]:
            issues.append(f"{where}: {key} does not apply to a {block} block")
        elif isinstance(value, bool) or not isinstance(value, int) or value < (1 if key.startswith("min_") else 0):
            issues.append(f"{where}: {key} must be a whole number")
    for what in ("items", "bullets"):
        low, high = limits.get(f"min_{what}"), limits.get(f"max_{what}")
        if isinstance(low, int) and isinstance(high, int) and low > high:
            issues.append(f"{where}: min_{what} {low} is more than max_{what} {high}; no email could fit")


# fleet's form field types (fleet docs/PROMPT-LIBRARY.md). fleet validates the
# form in full when it lists the template; this catches what would make it
# drop the template, before one is saved.
FIELD_TYPES = ("text", "textarea", "select", "number", "daterange", "toggle", "recipients", "palette")
_FIELD_KEY = re.compile(r"\w+")
_PROMPT_TOKEN = re.compile(r"\{(\w+)\}")


def _validate_form(fields: Any, prompt: Any, issues: list[str]) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append("'promptTemplate' must be non-empty text")
    if not isinstance(fields, list) or not fields:
        issues.append("'fields' must be a non-empty list of form fields")
        return
    keys: set[str] = set()
    for i, field in enumerate(fields):
        where = f"fields[{i}]"
        if not isinstance(field, Mapping):
            issues.append(f"{where} must be a mapping with key, label and type")
            continue
        key = field.get("key")
        if not isinstance(key, str) or not _FIELD_KEY.fullmatch(key):
            issues.append(f"{where}: 'key' must be letters, digits and '_'")
        elif key in keys:
            issues.append(f"{where}: duplicate key {key!r}")
        else:
            keys.add(key)
        if not isinstance(field.get("label"), str) or not field["label"].strip():
            issues.append(f"{where}: 'label' is required, as text")
        kind = field.get("type")
        if kind not in FIELD_TYPES:
            issues.append(f"{where}: 'type' must be one of {', '.join(FIELD_TYPES)}")
        elif kind in ("select", "palette"):
            options = field.get("options")
            if not isinstance(options, list) or not options or not all(isinstance(o, str) and o for o in options):
                issues.append(f"{where}: a {kind} field needs 'options', a list of names")
    if isinstance(prompt, str):
        unknown = sorted(set(_PROMPT_TOKEN.findall(prompt)) - keys)
        if unknown:
            issues.append(f"'promptTemplate' uses {{{'}, {'.join(unknown)}}} but no field has that key")


def validate_spec(spec: Any) -> list[str]:
    """Problems with a parsed template file; empty when it is well formed."""
    if not isinstance(spec, Mapping):
        return ["the file must be a YAML mapping"]
    issues: list[str] = []
    unknown = sorted(set(spec) - _TEMPLATE_KEYS)
    if unknown:
        issues.append(f"unknown key(s) {', '.join(unknown)}")
    _require_text(spec, ("name", "description", "audience", "use_when", "based_on", "theme"), "", issues)
    for key in ("name", "description", "use_when"):
        if not _text(spec.get(key)):
            issues.append(f"'{key}' is required")
    if _text(spec.get("audience")) not in AUDIENCES:
        issues.append(f"'audience' must be one of {', '.join(AUDIENCES)}")
    subject = spec.get("subject") or {}
    if (
        not isinstance(subject, Mapping)
        or not isinstance(subject.get("instruction"), str)
        or not subject["instruction"].strip()
    ):
        issues.append("'subject.instruction' is required")
    elif "max_chars" in subject and (not _whole(subject["max_chars"]) or subject["max_chars"] < 10):
        issues.append("'subject.max_chars' must be a whole number of 10 or more")
    header = spec.get("header")
    if header is not None and not (
        isinstance(header, Mapping)
        and set(header) <= {"headline", "status"}
        and all(isinstance(v, str) for v in header.values())
    ):
        issues.append("'header' takes only 'headline' and 'status' instructions, each as text")
    if "max_words" in spec and (not _whole(spec["max_words"]) or spec["max_words"] < 1):
        issues.append("'max_words' must be a whole number of 1 or more")
    for key in ("rules", "never_say"):
        value = spec.get(key)
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)):
            issues.append(f"'{key}' must be a list of non-empty strings")
        elif key == "never_say":
            if len(value or []) > MAX_NEVER_SAY:
                issues.append(f"'never_say' may list at most {MAX_NEVER_SAY} phrases")
            for phrase in (value or [])[:MAX_NEVER_SAY]:
                try:
                    _never_say_pattern(_text(phrase))
                except re.error as exc:
                    issues.append(f"never_say {phrase!r} is not a valid pattern: {exc}")
    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections:
        issues.append("'sections' must be a non-empty list")
    else:
        seen: set[str] = set()
        for i, raw in enumerate(sections):
            _validate_section(raw, f"sections[{i}]", seen, issues)
    if "fields" in spec or "promptTemplate" in spec:
        _validate_form(spec.get("fields"), spec.get("promptTemplate"), issues)
    sample = spec.get("sample")
    if not isinstance(sample, Mapping) or not isinstance(sample.get("blocks"), list):
        issues.append("'sample' is required: the render_email arguments the preview is rendered from")
    return issues


def team_dir() -> Path | None:
    """Where team templates live, or None when this deployment has no such place.

    fleet substitutes the path into the mail connector's env. It must be
    absolute: a relative one would resolve against whatever directory the
    server happened to start in.
    """
    raw = os.environ.get("EMAIL_TEMPLATES_TEAM_DIR", "").strip()
    if not raw or "${" in raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else None


def _ids_in(directory: Path | None) -> list[str]:
    if directory is None or not directory.is_dir() or directory.is_symlink():
        return []
    return sorted(
        p.stem for p in directory.glob("*.yaml") if p.is_file() and not p.is_symlink() and _ID.fullmatch(p.stem)
    )


def bundle_template_ids() -> list[str]:
    return _ids_in(TEMPLATES_DIR)


def team_template_ids() -> list[str]:
    """Team templates, without any that would shadow a bundle template."""
    bundle = set(bundle_template_ids())
    return [template_id for template_id in _ids_in(team_dir()) if template_id not in bundle]


def template_ids() -> list[str]:
    return sorted([*bundle_template_ids(), *team_template_ids()])


def template_source(template_id: str) -> str:
    """ "bundle", "team", or "" for an id that is neither."""
    if template_id in bundle_template_ids():
        return "bundle"
    return "team" if template_id in team_template_ids() else ""


def _template_path(template_id: str) -> Path:
    directory = TEMPLATES_DIR if template_id in bundle_template_ids() else team_dir()
    if directory is None:
        raise TemplateError(f"unknown email template {template_id!r}.")
    return directory / f"{template_id}.yaml"


@functools.lru_cache(maxsize=128)
def _load_cached(path: str, template_id: str, mtime_ns: int, size: int) -> dict[str, Any]:
    del mtime_ns, size  # cache key only: an edited file is read again
    try:
        spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise TemplateError(f"email template {template_id!r} cannot be read: {exc}") from exc
    issues = validate_spec(spec)
    if issues:
        raise TemplateError(f"email template {template_id!r} is not valid: {'; '.join(issues)}")
    spec = dict(spec)
    spec["id"] = template_id
    return spec


def load_template(template_id: str) -> dict[str, Any]:
    """The validated template, or TemplateError naming the templates that exist."""
    wanted = _text(template_id).removesuffix(".yaml")
    if not _ID.fullmatch(wanted) or wanted not in template_ids():
        known = ", ".join(template_ids()) or "none are installed"
        raise TemplateError(f"unknown email template {template_id!r}. Templates: {known}.")
    path = _template_path(wanted)
    try:
        stat = path.stat()
    except OSError as exc:  # removed by another chat since it was listed
        raise TemplateError(f"unknown email template {template_id!r}: it is no longer there.") from exc
    if stat.st_size > MAX_TEMPLATE_BYTES:
        raise TemplateError(f"email template {wanted!r} exceeds {MAX_TEMPLATE_BYTES} bytes")
    return _load_cached(str(path), wanted, stat.st_mtime_ns, stat.st_size)


def summarize(spec: Mapping[str, Any]) -> dict[str, Any]:
    """One catalog row: enough to pick the right template."""
    return {
        "template": spec["id"],
        "name": _text(spec.get("name")),
        "audience": _text(spec.get("audience")),
        "description": _text(spec.get("description")),
        "use_when": _text(spec.get("use_when")),
        "source": template_source(_text(spec.get("id"))) or "bundle",
    }


def describe(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The template as the writer needs it: sections in order, rules, limits.

    The sample is left out on purpose: it is preview data, and example numbers
    handed to a writer end up in real emails.
    """
    sections = []
    for raw in spec["sections"]:
        section: dict[str, Any] = {
            "section": raw["id"],
            "block_type": raw["block"],
            "required": raw.get("required", True),
            "instruction": _text(raw.get("instruction")),
        }
        if raw.get("heading") is not None:
            section["heading"] = f"{_text(raw['heading'])} (set for you)"
        if raw.get("kind"):
            section["kind"] = raw["kind"]
        if raw.get("limits"):
            section["limits"] = dict(raw["limits"])
        sections.append(section)
    out = summarize(spec)
    out["how_to_use"] = (
        f"Call render_email with template='{spec['id']}'. Give the blocks in the order below, each with "
        '"section": "<section id>" and the block type shown. Leave out an optional section that has nothing '
        "true to say; never pad one. Section headings are set for you."
    )
    out["subject"] = dict(spec.get("subject") or {})
    if spec.get("header"):
        out["header"] = dict(spec["header"])
    if spec.get("theme"):
        out["theme"] = spec["theme"]
    if spec.get("max_words"):
        out["max_words"] = spec["max_words"]
    out["rules"] = list(spec.get("rules") or [])
    out["never_say"] = list(spec.get("never_say") or [])
    out["sections"] = sections
    return out


def _all_text(subject: str, blocks: Sequence[Mapping[str, Any]], extra: Sequence[Any]) -> list[str]:
    """Every piece of text the reader sees, one entry per field, bold markers removed.

    Kept apart so a phrase is only found inside one field: joined, the end of
    one field and the start of the next could read as a phrase nobody wrote.
    """
    parts: list[str] = [subject, *(_text(v) for v in extra)]

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Mapping):
            for key, inner in value.items():
                if key not in ("type", "section", "cid", "url", "trend", "tone", "kind", "align"):
                    walk(inner)
        elif isinstance(value, Sequence):
            for inner in value:
                walk(inner)

    walk(list(blocks))
    return [_BOLD.sub(r"\1", part) for part in parts if part.strip()]


def apply_template(
    spec: Mapping[str, Any],
    subject: str,
    blocks: Any,
    *,
    header_values: Sequence[Any] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Check `blocks` against the template.

    Returns (blocks ready for email_render, violations). The returned blocks
    carry each section's fixed heading and callout kind. Every violation is
    reported at once, so one correction pass is enough.
    """
    template_id = spec["id"]
    sections = {raw["id"]: (i, raw) for i, raw in enumerate(spec["sections"])}
    order = ", ".join(sections)
    if not isinstance(blocks, Sequence) or isinstance(blocks, str) or not blocks:
        return [], [f"'blocks' must be a non-empty list, one block per section, in this order: {order}."]

    violations: list[str] = []
    out: list[dict[str, Any]] = []
    used: dict[str, int] = {}
    last_index = -1
    body_words = 0
    for i, raw_block in enumerate(blocks):
        where = f"blocks[{i}]"
        if not isinstance(raw_block, Mapping):
            violations.append(f"{where} must be an object with 'section' and 'type'.")
            continue
        section_id = _text(raw_block.get("section"))
        if section_id not in sections:
            named = f"names section {section_id!r}, which" if section_id else "has no 'section'; it"
            violations.append(f"{where} {named} is not in template {template_id}. Sections: {order}.")
            continue
        index, section = sections[section_id]
        where = f"{where} ({section_id})"
        if section_id in used:
            violations.append(f"{where}: section already given at blocks[{used[section_id]}]; one block per section.")
            continue
        used[section_id] = i
        if index < last_index:
            violations.append(f"{where}: out of order. The template's order is: {order}.")
        last_index = max(last_index, index)
        kind = _text(raw_block.get("type")).lower()
        if kind != section["block"]:
            violations.append(f"{where}: must be a {section['block']} block, not {kind or 'untyped'}.")
            continue

        block = {k: v for k, v in raw_block.items() if k != "section"}
        if section.get("heading") is not None:
            heading = _text(section["heading"])
            block.pop("heading", None)
            block.pop("title", None)
            if heading:
                block[_HEADING_KEY[kind]] = heading
        if kind == "callout":
            block["kind"] = section["kind"]
            block.pop("tone", None)
        out.append(block)

        limits = section.get("limits") or {}
        paragraphs, bullets = _block_prose(block) if kind in ("text", "callout", "closing") else ([], [])
        words = count_words(paragraphs, bullets)
        body_words += words

        def over(key: str, actual: int, what: str, _limits: Mapping[str, Any] = limits, _where: str = where) -> None:
            if key in _limits and actual > _limits[key]:
                violations.append(f"{_where}: {actual} {what}; the limit is {_limits[key]}.")

        over("max_words", words, "words")
        over("max_paragraphs", len(paragraphs), "paragraphs")
        over("max_bullets", len(bullets), "bullets")
        if "min_bullets" in limits and len(bullets) < limits["min_bullets"]:
            violations.append(f"{where}: {len(bullets)} bullets; at least {limits['min_bullets']} are needed.")
        if "max_words_per_bullet" in limits:
            for n, bullet in enumerate(bullets):
                if count_words(bullet) > limits["max_words_per_bullet"]:
                    violations.append(
                        f"{where}: bullet {n + 1} is {count_words(bullet)} words; "
                        f"the limit is {limits['max_words_per_bullet']} per bullet."
                    )
        items = block.get("items") if isinstance(block.get("items"), Sequence) else []
        over("max_items", len(items), "items")
        if "min_items" in limits and len(items) < limits["min_items"]:
            violations.append(f"{where}: {len(items)} items; at least {limits['min_items']} are needed.")
        rows = block.get("rows") if isinstance(block.get("rows"), Sequence) else []
        columns = block.get("columns") if isinstance(block.get("columns"), Sequence) else []
        over("max_rows", len(rows), "rows")
        over("max_columns", len(columns), "columns")

    for section_id, (_, section) in sections.items():
        if section.get("required", True) and section_id not in used:
            violations.append(f"Section {section_id!r} ({section['block']}) is required and missing.")

    if spec.get("max_words") and body_words > spec["max_words"]:
        violations.append(
            f"The email's prose is {body_words} words; template {template_id} allows {spec['max_words']} "
            "(cards, tables and captions are not counted). Cut detail, not sections."
        )
    max_chars = (spec.get("subject") or {}).get("max_chars")
    if max_chars and len(_text(subject)) > max_chars:
        violations.append(f"The subject is {len(_text(subject))} characters; the limit is {max_chars}.")

    # An early pass over what the caller wrote, so the writer hears about a
    # banned phrase together with everything else. The rendered email is
    # checked again (rendered_violations): only it has the renderer's defaults.
    violations += never_say_violations(spec, _all_text(_text(subject), out, header_values))
    return out, violations


def never_say_violations(spec: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    """One violation per banned phrase found in any of `fields`, each searched on its own."""
    found: list[str] = []
    for phrase in spec.get("never_say") or []:
        pattern = _never_say_pattern(_text(phrase))
        match = next((m for field in fields if (m := _search(pattern, field))), None)
        if match:
            found.append(f"The email says {match.group(0)!r}; this template never says {_text(phrase)!r}.")
    return found


def _search(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """`pattern.search(text)`, a piece of `text` at a time, with the same result.

    A field can be as long as the caller likes, and a search costs more than
    its length. Each piece is SEARCH_WINDOW characters of the whole string
    (pos/endpos, so a word boundary still looks at the real neighbours) and
    overlaps the next by MAX_MATCH_CHARS, the longest any pattern can match.
    A match that touches a piece's cut-off end is not taken from that piece:
    the cut may have made it (a word boundary where a word carries on), and if
    it is real the next piece holds it whole.
    """
    size = len(text)
    start = 0
    while True:
        end = min(start + SEARCH_WINDOW, size)
        match = pattern.search(text, start, end)
        while match and match.end() == end and end < size and match.start() < end:
            match = pattern.search(text, match.start() + 1, end)
        if match and (match.end() < end or end == size):
            return match
        if end == size:
            return None
        start = end - MAX_MATCH_CHARS


_INLINE_TAGS = frozenset({"a", "b", "strong", "i", "em", "span", "u", "small", "sup", "sub", "font", "code"})
_UNSEEN_TAGS = ("style", "script", "head", "title")


class _VisibleText(HTMLParser):
    """The text a reader of an HTML email sees, one entry per cell, paragraph or line.

    Inline markup does not break an entry: `<strong>Spend:</strong> $100` reads
    as "Spend: $100". Any other tag does, since it starts a new cell or line.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.runs: list[str] = []
        self._hidden = 0
        self._line: list[str] = []

    def _break(self) -> None:
        text = " ".join("".join(self._line).split())
        self._line = []
        if text:
            self.runs.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _UNSEEN_TAGS:
            self._hidden += 1
        if tag not in _INLINE_TAGS:
            self._break()
        if tag == "img":
            self.runs.extend(value for key, value in attrs if key == "alt" and value and value.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in _UNSEEN_TAGS and self._hidden:
            self._hidden -= 1
        if tag not in _INLINE_TAGS:
            self._break()

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self._line.append(data)

    def close(self) -> None:
        super().close()
        self._break()


def rendered_violations(spec: Mapping[str, Any], subject: str, page: str) -> list[str]:
    """Banned phrases in the email as rendered: what the reader will actually see.

    The renderer adds text the caller never wrote (default headings, today's
    date, the deployment's name) and turns numbers into text (a share of 63.6
    becomes "63.6%"), so the finished page is what the rule is about.
    """
    parser = _VisibleText()
    parser.feed(page)
    parser.close()
    return never_say_violations(spec, [_text(subject), *parser.runs])


MAX_PATTERN_CHARS = 200
# What a never_say pattern may be, so that searching with it is always quick
# and searching a long field piece by piece finds exactly what searching it
# whole would:
#   - a match is at most MAX_MATCH_CHARS long (the pieces overlap by that much);
#   - no repeat is open-ended (+, *, {n,}) or longer than MAX_REPEAT;
#   - the ways its optional and variable-length parts can combine multiply to
#     at most MAX_PATTERN_WAYS (a{0,10}a{0,10} alone is 121);
#   - no ^ or $ (a field is a heading or a paragraph, not a line), no
#     lookaround, no backreference, no repeat inside a repeat.
MAX_REPEAT = 100
MAX_PATTERN_WAYS = 128
MAX_NEVER_SAY = 50
_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")


def _slow_pattern(source: str) -> str:
    """Why a never_say pattern is refused ("" when it is fine).

    Every render searches the email with each pattern, and Python's `re` has no
    time limit, so a pattern that can backtrack a great deal would hang the
    server. The limits above keep every accepted pattern cheap, and simple
    enough that a phrase check loses nothing: `\\bQ[1-4] \\d{4}\\b`,
    `(?:internal|draft) only`, `\\$\\d{1,9}k?` all pass.
    """
    if len(source) > MAX_PATTERN_CHARS:
        return f"a pattern may be at most {MAX_PATTERN_CHARS} characters"
    try:
        tree = _re_parser.parse(source, re.IGNORECASE)
        widest = tree.getwidth()[1]
    except Exception:  # noqa: BLE001 - re.compile accepted it; an unreadable tree is not trusted
        return "the pattern could not be checked; use a simpler one"
    ways = 1

    def problem(items: Any, repeated: bool) -> str:
        nonlocal ways
        for op, av in items:
            name = str(op)
            if name in ("GROUPREF", "GROUPREF_EXISTS"):
                return "a pattern may not use a backreference"
            if name in ("ASSERT", "ASSERT_NOT"):
                return "a pattern may not use lookahead or lookbehind"
            if name == "AT" and str(av) not in ("AT_BOUNDARY", "AT_NON_BOUNDARY"):
                return "a pattern may not use ^ or $; \\b marks the edge of a word"
            inner: list[Any] = []
            repeats = False
            if name in _REPEATS:
                low, high, body = av
                if high > MAX_REPEAT:
                    return f"a repeat needs an upper limit of at most {MAX_REPEAT}: \\d{{1,9}} instead of \\d+"
                repeats = high > 1
                if repeated and repeats:
                    return "a pattern may not repeat a group that itself repeats, as in (x{1,3}){2,5}"
                ways *= high - low + 1
                inner = [body]
            elif name == "BRANCH":
                if repeated:
                    return "a pattern may not repeat a group that has alternatives, as in (a|b){2,5}"
                ways *= len(av[1])
                inner = list(av[1])
            elif name == "SUBPATTERN":
                inner = [av[3]]
            elif name == "ATOMIC_GROUP":
                inner = [av]
            if ways > MAX_PATTERN_WAYS:
                return "a pattern has too many optional or variable-length parts; fix some lengths, as in \\d{4}"
            for child in inner:
                found = problem(child, repeated or repeats)
                if found:
                    return found
        return ""

    found = problem(tree, False)
    if not found and widest > MAX_MATCH_CHARS:
        return f"a pattern may match at most {MAX_MATCH_CHARS} characters"
    return found


@functools.lru_cache(maxsize=256)
def _never_say_pattern(phrase: str) -> re.Pattern[str]:
    """`re:<regex>` is a regular expression; anything else is a whole-phrase, any-case match."""
    if phrase.startswith("re:"):
        source = phrase[3:].strip()
        compiled = re.compile(source, re.IGNORECASE)
        slow = _slow_pattern(source)
        if slow:
            raise re.error(slow)
        return compiled
    if len(phrase) > MAX_MATCH_CHARS // 2:
        raise re.error(f"a phrase may be at most {MAX_MATCH_CHARS // 2} characters")
    words = [re.escape(word) for word in phrase.split()]
    # Up to three spaces or line breaks between words: bounded, like every pattern here (_search).
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s{1,3}".join(words) + r"(?![A-Za-z0-9])", re.IGNORECASE)

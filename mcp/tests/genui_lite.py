"""A faithful Python subset of fleet's ``show_ui`` card validator (``internal/genui``).

fleet validates every ``show_ui`` card at tool-call time (``genui.Validate``)
and refuses one that breaks a rule, so a card template this bundle teaches
the agent must pass it or the agent burns a turn fixing it. This bundle's CI is
Python only, so this module re-implements the rules a template can break:

* the component catalog: names, allowed and required props, prop kinds, enums
  (``components`` mirrors ``spec.go``'s ``components`` map);
* ids: required on inputs, ``[A-Za-z_][A-Za-z0-9_]{0,63}``, not reserved,
  unique across the card;
* the protocol limits (``spec.go`` constants): 128 KiB, 800 components,
  12 levels, 2,000 options, 500 table rows, 20 columns, 6 actions, 500
  display entries, 200 repeater items, 12 tabs, 4 columns, 200 field errors,
  and the 2,000-row card budget (repeaters counted at ``max_items``);
* tables (selectable ones need ``id`` / ``row_key`` / unique string keys,
  cells under declared columns), options, defaults against options, repeater
  nesting and item values, actions (a card with inputs needs a submit),
  ``field_errors`` / ``status_list`` field paths, and the names every
  expression reads.

It does not re-implement the satisfiability check (``satisfy.go``), the
minimum-answer-size bound or every default-value rule; templates are also
validated once by hand with fleet's real validator (see the PR). Pinned
against fleet's own fixture: ``genui_fixtures/fleet_cards.json`` is a copy of
cases from ``internal/genui/testdata/cards.json`` and ``test_genui_lite.py``
requires every valid one to pass and the listed invalid ones to fail.

Source: fleet ``internal/genui/spec.go`` at ``FLEET_REV``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

FLEET_REV = "27c8bc4439a7d65d787fefca532139bd873da0be"

MAX_SPEC_BYTES = 128 << 10
MAX_NODES = 800
MAX_DEPTH = 12
MAX_OPTIONS = 2000
MAX_TABLE_ROWS = 500
MAX_DISPLAY_ITEMS = 500
MAX_CARD_ROWS = 2000
MAX_TABLE_COLUMNS = 20
MAX_ACTIONS = 6
MAX_REPEATER_ITEMS = 200
MAX_LIST_ITEMS = 20000
MAX_STRING_LEN = 20000
MAX_COLUMNS = 4
MAX_TABS = 12
MAX_FIELD_ERRORS = 200
MAX_CHART_SERIES = 8
MAX_CHART_POINTS = 200

# kinds
STRING, TEMPLATE, EXPR, BOOL, NUMBER, INT, ENUM = (
    "string",
    "template",
    "expr",
    "bool",
    "number",
    "int",
    "enum",
)
CHILDREN, OPTIONS, STRING_LIST, NUMBER_LIST, VALUE = (
    "children",
    "options",
    "string_list",
    "number_list",
    "value",
)
URL, DATE, OBJECTS, ROWS, FIELD_PATH, NON_BLANK = (
    "url",
    "date",
    "objects",
    "rows",
    "field_path",
    "non_blank",
)


@dataclass(frozen=True)
class Prop:
    kind: str
    required: bool = False
    enum: tuple[str, ...] = ()
    items: dict[str, Prop] | None = None
    entries: bool = False


def p(kind: str) -> Prop:
    return Prop(kind)


def req(kind: str) -> Prop:
    return Prop(kind, required=True)


def enum(*values: str, required: bool = False) -> Prop:
    return Prop(ENUM, required=required, enum=values)


def objects(items: dict[str, Prop], *, entries: bool = False) -> Prop:
    return Prop(OBJECTS, required=True, items=items, entries=entries)


TONES = ("neutral", "info", "success", "warning", "danger")

INPUT_COMMON = {
    "id": req(STRING),
    "label": p(STRING),
    "help": p(TEMPLATE),
    "required": p(BOOL),
    "disabled": p(BOOL),
    "value": p(VALUE),
}


@dataclass(frozen=True)
class Comp:
    props: dict[str, Prop]
    input: bool = False
    scope: bool = False


def _inp(extra: dict[str, Prop], *, scope: bool = False) -> Comp:
    return Comp({**extra, **INPUT_COMMON}, input=True, scope=scope)


COMPONENTS: dict[str, Comp] = {
    # layout
    "section": Comp(
        {
            "title": p(STRING),
            "description": p(TEMPLATE),
            "children": req(CHILDREN),
            "collapsible": p(BOOL),
            "collapsed": p(BOOL),
        }
    ),
    "columns": Comp({"children": req(CHILDREN)}),
    "tabs": Comp(
        {
            "variant": enum("tabs", "steps"),
            "tabs": objects({"label": req(NON_BLANK), "children": req(CHILDREN)}),
        }
    ),
    "divider": Comp({}),
    # display
    "heading": Comp({"text": req(TEMPLATE)}),
    "text": Comp({"text": req(TEMPLATE), "markdown": p(BOOL), "tone": enum("default", "muted")}),
    "callout": Comp({"tone": enum(*TONES), "title": p(STRING), "text": req(TEMPLATE)}),
    "badges": Comp({"items": objects({"text": req(TEMPLATE), "tone": enum(*TONES)}, entries=True)}),
    "stat": Comp(
        {
            "label": req(STRING),
            "value": req(TEMPLATE),
            "caption": p(TEMPLATE),
            "tone": enum(*TONES),
        }
    ),
    "facts": Comp({"items": objects({"label": req(STRING), "value": req(TEMPLATE)}, entries=True)}),
    "table": Comp(
        {
            "id": p(STRING),
            "label": p(STRING),
            "help": p(TEMPLATE),
            "columns": objects(
                {
                    "key": req(STRING),
                    "label": p(STRING),
                    "align": enum("left", "right", "center"),
                }
            ),
            "rows": req(ROWS),
            "select": enum("none", "single", "multi"),
            "row_key": p(STRING),
            "value": p(VALUE),
            "required": p(BOOL),
            "disabled": p(BOOL),
        }
    ),
    "status_list": Comp(
        {
            "items": objects(
                {
                    "status": enum("pass", "fail", "warn", "info", "pending", required=True),
                    "label": req(TEMPLATE),
                    "detail": p(TEMPLATE),
                    "field": p(FIELD_PATH),
                },
                entries=True,
            )
        }
    ),
    "progress": Comp({"label": p(STRING), "value": req(EXPR), "max": p(NUMBER)}),
    "chart": Comp(
        {
            "kind": enum("bar", "line", required=True),
            "title": p(STRING),
            "labels": req(STRING_LIST),
            "unit": p(STRING),
            "series": objects({"name": req(STRING), "values": req(NUMBER_LIST)}),
        }
    ),
    "code": Comp({"text": req(STRING), "language": p(STRING)}),
    "link": Comp({"text": req(NON_BLANK), "url": req(URL)}),
    "diff": Comp(
        {
            "title": p(STRING),
            "rows": objects(
                {"label": req(STRING), "before": p(STRING), "after": p(STRING)},
                entries=True,
            ),
        }
    ),
    # inputs
    "text_input": _inp(
        {
            "placeholder": p(STRING),
            "multiline": p(BOOL),
            "min_length": p(INT),
            "max_length": p(INT),
            "format": enum("text", "email", "url"),
        }
    ),
    "number": _inp(
        {
            "min": p(NUMBER),
            "max": p(NUMBER),
            "step": p(NUMBER),
            "prefix": p(STRING),
            "suffix": p(STRING),
            "placeholder": p(STRING),
        }
    ),
    "slider": _inp(
        {
            "min": req(NUMBER),
            "max": req(NUMBER),
            "step": p(NUMBER),
            "prefix": p(STRING),
            "suffix": p(STRING),
        }
    ),
    "select": _inp({"options": req(OPTIONS), "placeholder": p(STRING)}),
    "choice": _inp({"options": req(OPTIONS), "variant": enum("segmented", "radio")}),
    "multi_select": _inp(
        {
            "options": p(OPTIONS),
            "allow_custom": p(BOOL),
            "max_items": p(INT),
            "placeholder": p(STRING),
        }
    ),
    "toggle": _inp({}),
    "date": _inp({"min": p(DATE), "max": p(DATE)}),
    "list_input": _inp({"placeholder": p(STRING), "max_items": p(INT), "dedupe": p(BOOL)}),
    "include_exclude": _inp(
        {
            "options": p(OPTIONS),
            "allow_custom": p(BOOL),
            "include_label": p(STRING),
            "exclude_label": p(STRING),
        }
    ),
    "repeater": _inp(
        {
            "fields": req(CHILDREN),
            "item_label": p(TEMPLATE),
            "min_items": p(INT),
            "max_items": p(INT),
            "add_label": p(STRING),
        },
        scope=True,
    ),
}

COMMON_PROPS = {"type": req(STRING), "id": p(STRING), "visible_if": p(EXPR)}

ACTION_PROPS = {
    "id": req(STRING),
    "label": req(STRING),
    "kind": enum("submit", "message"),
    "style": enum("primary", "secondary", "danger"),
    "message": p(STRING),
    "validate": p(BOOL),
    "confirm": p(NON_BLANK),
    "visible_if": p(EXPR),
    "disabled_if": p(EXPR),
}

EXPR_FUNCS = frozenset(
    [
        "len",
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "abs",
        "floor",
        "ceil",
        "round",
        "fixed",
        "number",
        "string",
        "upper",
        "lower",
        "join",
        "contains",
        "empty",
        "unique",
    ]
)
RESERVED = frozenset({"true", "false", "null", "index", "__proto__", "constructor", "prototype"}) | EXPR_FUNCS
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
FIELD_PATH_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*))?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_TOKEN_RE = re.compile(
    r"""\s+|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*|.""",
    re.DOTALL,
)


@dataclass
class _Field:
    id: str
    typ: str
    path: str
    repeater: str
    obj: dict[str, Any]
    items: int = 1


@dataclass
class _V:
    issues: list[str] = field(default_factory=list)
    nodes: int = 0
    rows: int = 0
    fields: dict[str, _Field] = field(default_factory=dict)
    exprs: list[tuple[str, str, str]] = field(default_factory=list)  # (path, src, repeater scope)
    paths: list[tuple[str, str]] = field(default_factory=list)  # (path, target)

    def add(self, path: str, msg: str) -> None:
        self.issues.append(f"{path}: {msg}" if path else msg)


def _is_num(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def _option_values(val: Any) -> list[str] | None:
    if not isinstance(val, list):
        return None
    out = []
    for e in val:
        if isinstance(e, str):
            out.append(e)
        elif isinstance(e, dict) and isinstance(e.get("value"), str):
            out.append(e["value"])
        else:
            return None
    return out


def _initial_items(obj: dict[str, Any]) -> int:
    if isinstance(obj.get("value"), list):
        return len(obj["value"])
    lo = obj.get("min_items")
    if _is_num(lo) and lo > 1:
        return int(min(lo, MAX_REPEATER_ITEMS))
    return 1


def _walk(children: list[Any], visit) -> None:
    for c in children:
        if not isinstance(c, dict):
            continue
        visit(c)
        for k in ("children", "fields"):
            if isinstance(c.get(k), list):
                _walk(c[k], visit)
        if isinstance(c.get("tabs"), list):
            for t in c["tabs"]:
                if isinstance(t, dict) and isinstance(t.get("children"), list):
                    _walk(t["children"], visit)


def _count_components(children: list[Any]) -> int:
    n = [0]
    _walk(children, lambda _c: n.__setitem__(0, n[0] + 1))
    return n[0]


def _choice_options(children: list[Any]) -> int:
    n = [0]

    def visit(c: dict[str, Any]) -> None:
        if c.get("type") in ("choice", "select") and isinstance(c.get("options"), list):
            n[0] += len(c["options"])

    _walk(children, visit)
    return n[0]


def _nested_rows(children: list[Any]) -> int:
    n = [0]

    def visit(c: dict[str, Any]) -> None:
        if c.get("type") in ("table", "diff") and isinstance(c.get("rows"), list):
            n[0] += len(c["rows"])
        elif c.get("type") in ("badges", "facts", "status_list") and isinstance(c.get("items"), list):
            n[0] += len(c["items"])

    _walk(children, visit)
    return n[0]


def _collect_inputs(children: list[Any], into: dict[str, dict[str, Any]]) -> None:
    for c in children:
        if not isinstance(c, dict):
            continue
        spec = COMPONENTS.get(c.get("type"))
        if spec and (spec.input or c.get("type") == "table") and isinstance(c.get("id"), str):
            into[c["id"]] = c
        if c.get("type") == "repeater":
            continue
        if isinstance(c.get("children"), list):
            _collect_inputs(c["children"], into)
        if isinstance(c.get("tabs"), list):
            for t in c["tabs"]:
                if isinstance(t, dict) and isinstance(t.get("children"), list):
                    _collect_inputs(t["children"], into)


def _template_holes(s: str) -> list[str] | None:
    if s.count("{{") != s.count("}}"):
        return None
    return [h.strip() for h in TEMPLATE_RE.findall(s)]


def _expr_names(src: str) -> tuple[list[tuple[str, list[str]]], list[str]] | None:
    """``([(name, members)], [functions])`` an expression reads, or None when it does not tokenize."""
    tokens = [t for t in _TOKEN_RE.findall(src) if not t.isspace()]
    refs: list[tuple[str, list[str]]] = []
    funcs: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"^[A-Za-z_]", tok):
            prev = tokens[i - 1] if i else ""
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if prev == ".":
                i += 1
                continue
            if nxt == "(":
                funcs.append(tok)
            elif tok not in ("true", "false", "null"):
                members = []
                j = i + 1
                while j + 1 < len(tokens) and tokens[j] == "." and re.match(r"^[A-Za-z_]", tokens[j + 1]):
                    members.append(tokens[j + 1])
                    j += 2
                refs.append((tok, members))
        i += 1
    return refs, funcs


def validate(card: Any) -> list[str]:
    """Every issue fleet's ``genui.Validate`` would raise that this subset checks (empty = valid)."""
    raw = card if isinstance(card, bytes | str) else json.dumps(card, ensure_ascii=False)
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(raw_bytes) > MAX_SPEC_BYTES:
        return [f"card is {len(raw_bytes)} bytes; the limit is {MAX_SPEC_BYTES}"]
    try:
        top = json.loads(raw_bytes)
    except ValueError as exc:
        return [f"arguments are not a JSON object: {exc}"]
    if not isinstance(top, dict):
        return ["arguments are not a JSON object"]
    v = _V()
    for k in top:
        if k not in {
            "title",
            "description",
            "components",
            "actions",
            "replaces",
            "field_errors",
        }:
            v.add(k, "unknown top-level key")
    title = top.get("title")
    if not isinstance(title, str) or not title.strip():
        v.add("title", "required: a short card title")
    if "description" in top:
        _check_prop(v, "description", p(TEMPLATE), top["description"], "")
    comps = top.get("components")
    if not isinstance(comps, list) or not comps:
        v.add("components", "required: a non-empty array of components")
        comps = []
    for i, c in enumerate(comps):
        _component(v, f"components[{i}]", c, 1, "")
    _actions(v, top.get("actions"), bool(v.fields))
    if "replaces" in top:
        r = top["replaces"]
        if not isinstance(r, str) or not r.strip() or r != r.strip() or len(r) > 256:
            v.add("replaces", "must be the card_id string of an earlier card")
    _field_errors(v, top.get("field_errors"))
    _resolve(v)
    return v.issues


def _component(v: _V, path: str, raw: Any, depth: int, repeater: str) -> None:
    v.nodes += 1
    if v.nodes == MAX_NODES + 1:
        v.add(path, f"card has more than {MAX_NODES} components")
    if v.nodes > MAX_NODES:
        return
    if depth > MAX_DEPTH:
        v.add(path, f"nested more than {MAX_DEPTH} levels deep")
        return
    if not isinstance(raw, dict):
        v.add(path, 'a component must be an object with a "type"')
        return
    typ = raw.get("type")
    spec = COMPONENTS.get(typ) if isinstance(typ, str) else None
    if spec is None:
        v.add(path, f"unknown component type {typ!r}")
        return
    path = f"{path}({typ})"
    for k, val in raw.items():
        pr = spec.props.get(k) or COMMON_PROPS.get(k)
        if pr is None:
            v.add(f"{path}.{k}", f"unknown property for {typ}")
            continue
        if k in ("type", "id") or pr.kind == VALUE:
            continue
        if pr.kind == CHILDREN:
            if not isinstance(val, list) or not val:
                v.add(f"{path}.{k}", "must be a non-empty array of components")
                continue
            if typ == "columns" and len(val) > MAX_COLUMNS:
                v.add(f"{path}.{k}", f"at most {MAX_COLUMNS} columns")
            inner = raw.get("id", "") if spec.scope else repeater
            for i, kid in enumerate(val):
                _component(v, f"{path}.{k}[{i}]", kid, depth + 1, inner)
            continue
        if k == "tabs":
            _tabs(v, path, val, depth, repeater)
            continue
        scope = raw.get("id", "") if (spec.scope and k == "item_label") else repeater
        _check_prop(v, f"{path}.{k}", pr, val, scope)
    for k, pr in spec.props.items():
        if pr.required and k not in raw:
            v.add(f"{path}.{k}", f"required for {typ}")
    ident = raw.get("id")
    if "id" in raw and not isinstance(ident, str):
        v.add(f"{path}.id", "must be a string")
    if spec.input or (typ == "table" and isinstance(ident, str)):
        if not isinstance(ident, str):
            if spec.input:
                v.add(f"{path}.id", "required: every input needs an id")
        else:
            _declare(v, path, ident, typ, repeater, raw)
    elif isinstance(ident, str) and not ID_RE.match(ident):
        v.add(f"{path}.id", f"ids must match {ID_RE.pattern}")
    _rules(v, path, typ, raw)
    if "value" in raw and spec.props.get("value", Prop(STRING)).kind == VALUE:
        _check_value(v, f"{path}.value", typ, raw, raw["value"])


def _declare(v: _V, path: str, ident: str, typ: str, repeater: str, raw: dict[str, Any]) -> None:
    if not ID_RE.match(ident):
        v.add(f"{path}.id", f"ids must match {ID_RE.pattern}")
        return
    if ident in RESERVED:
        v.add(f"{path}.id", f"{ident!r} is reserved")
        return
    if ident in v.fields:
        v.add(
            f"{path}.id",
            f"duplicate id {ident!r} (first used at {v.fields[ident].path})",
        )
        return
    v.fields[ident] = _Field(ident, typ, path, repeater, raw, _initial_items(raw) if typ == "repeater" else 1)


def _tabs(v: _V, path: str, val: Any, depth: int, repeater: str) -> None:
    if not isinstance(val, list) or not val:
        v.add(f"{path}.tabs", "must be a non-empty array of {label, children}")
        return
    if len(val) > MAX_TABS:
        v.add(f"{path}.tabs", f"at most {MAX_TABS} tabs")
    for i, t in enumerate(val):
        tp = f"{path}.tabs[{i}]"
        if not isinstance(t, dict):
            v.add(tp, "must be an object {label, children}")
            continue
        for k in t:
            if k not in ("label", "children"):
                v.add(f"{tp}.{k}", "unknown property (allowed: label, children)")
        if not isinstance(t.get("label"), str) or not t["label"].strip():
            v.add(f"{tp}.label", "required")
        kids = t.get("children")
        if not isinstance(kids, list) or not kids:
            v.add(f"{tp}.children", "must be a non-empty array of components")
            continue
        for j, kid in enumerate(kids):
            _component(v, f"{tp}.children[{j}]", kid, depth + 1, repeater)


def _check_string(v: _V, path: str, s: str) -> None:
    if len(s) > MAX_STRING_LEN:
        v.add(path, f"string longer than {MAX_STRING_LEN} characters")


def _check_prop(v: _V, path: str, pr: Prop, val: Any, repeater: str) -> None:
    k = pr.kind
    if k == BOOL:
        if not isinstance(val, bool):
            v.add(path, "must be true or false")
    elif k == NUMBER:
        if not _is_num(val):
            v.add(path, "must be a number")
    elif k == INT:
        if not _is_num(val) or val != int(val) or val < 0:
            v.add(path, "must be a non-negative whole number")
    elif k == ENUM:
        if val not in pr.enum:
            v.add(path, f"must be one of: {', '.join(pr.enum)}")
    elif k in (STRING, URL, DATE):
        if not isinstance(val, str):
            v.add(path, "must be a string")
            return
        _check_string(v, path, val)
        if k == URL and not re.match(r"^https://[^/@:\s]+(:\d{1,5})?(/|$|\?|#)", val):
            v.add(path, "must be an absolute https:// URL with a host")
        if k == DATE and not DATE_RE.match(val):
            v.add(path, "must be a YYYY-MM-DD date")
    elif k == NON_BLANK:
        if not isinstance(val, str) or not val.strip():
            v.add(path, "must be a non-blank string")
    elif k == FIELD_PATH:
        if not isinstance(val, str):
            v.add(path, "must be a field path string")
        else:
            v.paths.append((path, val))
    elif k == TEMPLATE:
        if not isinstance(val, str):
            v.add(path, "must be a string")
            return
        _check_string(v, path, val)
        holes = _template_holes(val)
        if holes is None:
            v.add(path, "unbalanced {{ }}")
            return
        v.exprs += [(path, h, repeater) for h in holes]
    elif k == EXPR:
        if not isinstance(val, str):
            v.add(path, "must be an expression string")
        else:
            v.exprs.append((path, val, repeater))
    elif k == OPTIONS:
        _options(v, path, val)
    elif k == STRING_LIST:
        if not isinstance(val, list) or not all(isinstance(e, str) for e in val):
            v.add(path, "must be an array of strings")
    elif k == NUMBER_LIST:
        if not isinstance(val, list) or not all(e is None or _is_num(e) for e in val):
            v.add(path, "must be an array of numbers")
    elif k == OBJECTS:
        if not isinstance(val, list) or not val:
            v.add(path, "must be a non-empty array of objects")
            return
        if len(val) > MAX_DISPLAY_ITEMS:
            v.add(path, f"at most {MAX_DISPLAY_ITEMS} entries")
            return
        if pr.entries:
            _count_rows(v, path, len(val))
        for i, e in enumerate(val):
            ep = f"{path}[{i}]"
            if not isinstance(e, dict):
                v.add(ep, "must be an object")
                continue
            for ek, ev in e.items():
                ip = (pr.items or {}).get(ek)
                if ip is None:
                    v.add(f"{ep}.{ek}", "unknown property")
                    continue
                _check_prop(v, f"{ep}.{ek}", ip, ev, repeater)
            for ek, ip in (pr.items or {}).items():
                if ip.required and ek not in e:
                    v.add(f"{ep}.{ek}", "required")
    elif k == ROWS:
        if not isinstance(val, list):
            v.add(path, "must be an array of row objects")
            return
        if len(val) > MAX_TABLE_ROWS:
            v.add(path, f"at most {MAX_TABLE_ROWS} rows")
        _count_rows(v, path, len(val))
        for i, row in enumerate(val):
            if not isinstance(row, dict):
                v.add(f"{path}[{i}]", "each row must be an object keyed by column key")
                return
            for ck, cell in row.items():
                if not (cell is None or isinstance(cell, str | bool) or _is_num(cell)):
                    v.add(
                        f"{path}[{i}].{ck}",
                        "cells must be strings, numbers, booleans or null",
                    )


def _options(v: _V, path: str, val: Any) -> None:
    if not isinstance(val, list) or not val:
        v.add(
            path,
            "must be a non-empty array of strings or {value, label, description} objects",
        )
        return
    if len(val) > MAX_OPTIONS:
        v.add(path, f"at most {MAX_OPTIONS} options")
        return
    seen: set[str] = set()
    for i, e in enumerate(val):
        ep = f"{path}[{i}]"
        if isinstance(e, str):
            value = e
        elif isinstance(e, dict):
            if not isinstance(e.get("value"), str):
                v.add(f"{ep}.value", "required string")
                continue
            value = e["value"]
            for ok, ov in e.items():
                if ok in ("label", "description"):
                    if not isinstance(ov, str):
                        v.add(f"{ep}.{ok}", "must be a string")
                elif ok != "value":
                    v.add(f"{ep}.{ok}", "unknown option property")
        else:
            v.add(ep, "must be a string or {value, label, description}")
            continue
        if not value.strip():
            v.add(ep, "option values must be non-empty")
        if value in seen:
            v.add(ep, f"duplicate option value {value!r}")
        seen.add(value)


def _count_rows(v: _V, path: str, n: int) -> None:
    before = v.rows
    v.rows += n
    if before <= MAX_CARD_ROWS < v.rows:
        v.add(
            path,
            f"the card renders more than {MAX_CARD_ROWS} table rows, list entries and repeater item fields",
        )


def _rules(v: _V, path: str, typ: str, obj: dict[str, Any]) -> None:
    for cond in ("visible_if", "disabled_if"):
        src = obj.get(cond)
        if isinstance(src, str):
            parsed = _expr_names(src)
            if parsed is not None and not parsed[0]:
                v.add(f"{path}.{cond}", "names no input, so it never changes")
    for k in ("max_items", "max_length"):
        if obj.get(k) == 0:
            v.add(f"{path}.{k}", f"{k} 0 leaves the input nothing to hold")
    default = obj.get("min") if typ == "slider" and "value" not in obj else obj.get("value")
    if obj.get("required") is True and obj.get("disabled") is True and not _answered(typ, default):
        v.add(
            f"{path}.required",
            "a disabled required field needs a value that answers it",
        )
    if typ == "text_input":
        lo, hi = obj.get("min_length"), obj.get("max_length")
        if _is_num(lo) and _is_num(hi) and lo > hi:
            v.add(path, "min_length must not exceed max_length")
    lo_date, hi_date = obj.get("min"), obj.get("max")
    dates = typ == "date" and isinstance(lo_date, str) and isinstance(hi_date, str) and lo_date and hi_date
    if dates and lo_date > hi_date:
        v.add(path, "min must not be after max")
    if typ == "progress" and _is_num(obj.get("max")) and obj["max"] <= 0:
        v.add(f"{path}.max", "must be greater than 0")
    if typ == "section" and obj.get("collapsible") is True and not str(obj.get("title") or "").strip():
        v.add(f"{path}.title", "required when collapsible")
    if typ in ("slider", "number"):
        lo, hi = obj.get("min"), obj.get("max")
        if _is_num(lo) and _is_num(hi) and lo >= hi:
            v.add(path, "min must be less than max")
        if _is_num(obj.get("step")) and obj["step"] <= 0:
            v.add(f"{path}.step", "must be greater than 0")
    if typ in ("multi_select", "include_exclude"):
        if _is_num(obj.get("max_items")) and obj["max_items"] > MAX_OPTIONS:
            v.add(f"{path}.max_items", f"at most {MAX_OPTIONS}")
        if "options" not in obj and obj.get("allow_custom") is not True:
            v.add(path, "needs options, or allow_custom: true for free entry")
    if typ == "list_input" and _is_num(obj.get("max_items")) and obj["max_items"] > MAX_LIST_ITEMS:
        v.add(f"{path}.max_items", f"at most {MAX_LIST_ITEMS}")
    if typ == "repeater":
        _repeater(v, path, obj)
    if typ == "table":
        _table(v, path, obj)
    if typ == "chart":
        labels = obj.get("labels") if isinstance(obj.get("labels"), list) else []
        series = obj.get("series") if isinstance(obj.get("series"), list) else []
        if len(labels) > MAX_CHART_POINTS:
            v.add(f"{path}.labels", f"at most {MAX_CHART_POINTS} points")
        if len(series) > MAX_CHART_SERIES:
            v.add(f"{path}.series", f"at most {MAX_CHART_SERIES} series")
        for i, s in enumerate(series):
            if isinstance(s, dict) and isinstance(s.get("values"), list) and len(s["values"]) != len(labels):
                v.add(
                    f"{path}.series[{i}].values",
                    "has a different number of values than labels",
                )


def _repeater(v: _V, path: str, obj: dict[str, Any]) -> None:
    lo, hi = obj.get("min_items"), obj.get("max_items")
    if _is_num(hi) and hi > MAX_REPEATER_ITEMS:
        v.add(f"{path}.max_items", f"at most {MAX_REPEATER_ITEMS}")
    if _is_num(lo) and lo > MAX_REPEATER_ITEMS:
        v.add(f"{path}.min_items", f"at most {MAX_REPEATER_ITEMS}")
    if _is_num(lo) and _is_num(hi) and lo > hi:
        v.add(path, "min_items must not exceed max_items")
    kids = obj.get("fields")
    if not isinstance(kids, list):
        return
    initial = _initial_items(obj)
    most = max(initial, int(min(hi, MAX_REPEATER_ITEMS))) if _is_num(hi) else max(initial, MAX_REPEATER_ITEMS)
    if obj.get("disabled") is True:
        most = initial
    rows = _nested_rows(kids)
    weight = max(1, _count_components(kids)) + _choice_options(kids)
    _count_rows(v, path, most * weight + max(most - 1, 0) * rows)

    def nested(kp: str, children: list[Any]) -> None:
        for i, k in enumerate(children):
            if not isinstance(k, dict):
                continue
            if k.get("type") == "repeater":
                v.add(f"{kp}[{i}]", "repeaters cannot nest")
                continue
            if isinstance(k.get("children"), list):
                nested(f"{kp}[{i}].children", k["children"])
            for j, t in enumerate(k.get("tabs") or []):
                if isinstance(t, dict) and isinstance(t.get("children"), list):
                    nested(f"{kp}[{i}].tabs[{j}].children", t["children"])

    nested(f"{path}.fields", kids)


def _table(v: _V, path: str, obj: dict[str, Any]) -> None:
    sel = obj.get("select") or "none"
    cols = obj.get("columns") if isinstance(obj.get("columns"), list) else []
    if len(cols) > MAX_TABLE_COLUMNS:
        v.add(f"{path}.columns", f"at most {MAX_TABLE_COLUMNS} columns")
    keys: set[str] = set()
    for i, c in enumerate(cols):
        if isinstance(c, dict) and isinstance(c.get("key"), str):
            if not c["key"].strip():
                v.add(f"{path}.columns[{i}].key", "column keys must be non-empty")
            elif c["key"] in keys:
                v.add(f"{path}.columns[{i}].key", f"duplicate column key {c['key']!r}")
            keys.add(c["key"])
    rows = obj.get("rows") if isinstance(obj.get("rows"), list) else []
    if keys:
        for i, r in enumerate(rows):
            if isinstance(r, dict):
                for k in sorted(r):
                    if k not in keys:
                        v.add(f"{path}.rows[{i}].{k}", f"no column has key {k!r}")
    if sel in ("single", "multi"):
        if not isinstance(obj.get("id"), str):
            v.add(f"{path}.id", "required when select is single or multi")
        rk = obj.get("row_key")
        if rk not in keys:
            v.add(
                f"{path}.row_key",
                "required when selectable: the column key whose value identifies a row",
            )
            return
        if obj.get("required") is True and not rows:
            v.add(f"{path}.rows", "a required selectable table has no rows to pick from")
        seen: set[str] = set()
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                continue
            key = r.get(rk)
            if not isinstance(key, str) or not key.strip():
                v.add(
                    f"{path}.rows[{i}].{rk}",
                    "every row of a selectable table needs a non-empty string key",
                )
            elif key in seen:
                v.add(f"{path}.rows[{i}].{rk}", f"duplicate row_key value {key!r}")
            else:
                seen.add(key)
        picked = obj.get("value")
        picked = [picked] if isinstance(picked, str) and picked else picked if isinstance(picked, list) else []
        for k in picked:
            if isinstance(k, str) and k not in seen:
                v.add(f"{path}.value", f"{k!r} is not the row_key of any row")
    else:
        for k in ("id", "required", "disabled"):
            if k in obj:
                v.add(
                    f"{path}.{k}",
                    "only a selectable table (select: single|multi) takes it",
                )


def _answered(typ: str, val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return typ != "toggle" or val
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, list):
        if typ == "list_input":
            return any(isinstance(e, str) and e.strip() for e in val)
        return bool(val)
    if isinstance(val, dict) and typ == "include_exclude":
        return bool((val.get("include") or []) + (val.get("exclude") or []))
    return True


def _check_value(v: _V, path: str, typ: str, obj: dict[str, Any], val: Any) -> None:
    opts = _option_values(obj.get("options"))

    def in_options(s: str) -> bool:
        return opts is None or s in opts

    if typ in ("text_input", "date"):
        if not isinstance(val, str):
            v.add(path, "must be a string")
        elif typ == "date" and val and (not DATE_RE.match(val) or val[:4] == "0000"):
            v.add(path, "must be a YYYY-MM-DD string (year 0001 or later)")
        elif (
            typ == "date"
            and val
            and (
                (isinstance(obj.get("min"), str) and obj["min"] and val < obj["min"])
                or (isinstance(obj.get("max"), str) and obj["max"] and val > obj["max"])
            )
        ):
            v.add(path, f"{val} is outside min..max")
        elif typ == "text_input" and val.strip():
            if _is_num(obj.get("max_length")) and len(val) > obj["max_length"]:
                v.add(path, "is longer than max_length")
            if _is_num(obj.get("min_length")) and len(val) < obj["min_length"]:
                v.add(path, "is shorter than min_length")
    elif typ in ("number", "slider"):
        if val is not None and not _is_num(val):
            v.add(path, "must be a number")
        elif _is_num(val):
            lo, hi = obj.get("min"), obj.get("max")
            if (_is_num(lo) and val < lo) or (_is_num(hi) and val > hi):
                v.add(path, f"{val} is outside min..max")
    elif typ == "toggle":
        if not isinstance(val, bool):
            v.add(path, "must be true or false")
    elif typ in ("select", "choice"):
        if not isinstance(val, str):
            v.add(path, "must be a string option value")
        elif val and not in_options(val):
            v.add(path, f"{val!r} is not one of the options")
    elif typ in ("multi_select", "list_input"):
        if not isinstance(val, list) or not all(isinstance(e, str) for e in val):
            v.add(path, "must be an array of strings")
            return
        if typ == "multi_select" and obj.get("allow_custom") is not True:
            for e in val:
                if not in_options(e):
                    v.add(path, f"{e!r} is not one of the options")
                    return
        if _is_num(obj.get("max_items")) and _list_count(typ, obj, val) > obj["max_items"]:
            v.add(path, "has more items than max_items")
    elif typ == "include_exclude":
        if not isinstance(val, dict):
            v.add(path, 'must be {"include": [...], "exclude": [...]}')
            return
        seen: dict[str, str] = {}
        for side, entries in val.items():
            if side not in ("include", "exclude"):
                v.add(f"{path}.{side}", "only include and exclude are allowed")
                continue
            if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
                v.add(f"{path}.{side}", "must be an array of strings")
                continue
            for e in entries:
                if not e.strip():
                    v.add(f"{path}.{side}", "has a blank entry")
                elif obj.get("allow_custom") is not True and not in_options(e):
                    v.add(f"{path}.{side}", f"{e!r} is not one of the options")
                if seen.get(e, side) != side:
                    v.add(path, f"{e!r} is in both include and exclude")
                seen[e] = side
    elif typ == "table":
        sel = obj.get("select")
        if sel == "single" and not isinstance(val, str):
            v.add(path, "a single-select table's value is one row_key string")
        elif sel == "multi" and (not isinstance(val, list) or not all(isinstance(e, str) for e in val)):
            v.add(path, "a multi-select table's value is an array of row_key strings")
        elif sel not in ("single", "multi"):
            v.add(path, "only a selectable table takes a value")
    elif typ == "repeater":
        if not isinstance(val, list):
            v.add(
                path,
                "must be an array of item objects keyed by the repeater's field ids",
            )
            return
        if len(val) > MAX_REPEATER_ITEMS:
            v.add(path, f"at most {MAX_REPEATER_ITEMS} items")
        if _is_num(obj.get("max_items")) and len(val) > obj["max_items"]:
            v.add(path, "has more items than max_items")
        fields: dict[str, dict[str, Any]] = {}
        _collect_inputs(obj.get("fields") or [], fields)
        for i, item in enumerate(val):
            if not isinstance(item, dict):
                v.add(f"{path}[{i}]", "must be an object")
                continue
            for k, fv in item.items():
                if k not in fields:
                    v.add(f"{path}[{i}].{k}", "not a field of this repeater")
                    continue
                _check_value(v, f"{path}[{i}].{k}", fields[k].get("type"), fields[k], fv)
    else:
        v.add(path, f"{typ} does not take a value")


def _list_count(typ: str, obj: dict[str, Any], entries: list[str]) -> int:
    """How many entries the browser keeps from a list default (list_input drops blanks and, by default, repeats)."""
    if typ != "list_input":
        return len(entries)
    dedupe = obj.get("dedupe", True) is not False
    seen: set[str] = set()
    n = 0
    for entry in entries:
        for line in re.split(r"[\r\n]+", entry):
            line = line.strip()
            if not line or (dedupe and line in seen):
                continue
            seen.add(line)
            n += 1
    return n


def _actions(v: _V, raw: Any, has_input: bool) -> None:
    if raw is None:
        if has_input:
            v.add("actions", "the card has inputs but no actions")
        return
    if not isinstance(raw, list):
        v.add("actions", "must be an array")
        return
    if len(raw) > MAX_ACTIONS:
        v.add("actions", f"at most {MAX_ACTIONS} actions")
    seen: set[str] = set()
    submit = False
    for i, a in enumerate(raw):
        ap = f"actions[{i}]"
        if not isinstance(a, dict):
            v.add(ap, "must be an object")
            continue
        for k, val in a.items():
            if k not in ACTION_PROPS:
                v.add(f"{ap}.{k}", "unknown action property")
                continue
            _check_prop(v, f"{ap}.{k}", ACTION_PROPS[k], val, "")
        for k, pr in ACTION_PROPS.items():
            if pr.required and k not in a:
                v.add(f"{ap}.{k}", "required")
        ident = a.get("id")
        if not isinstance(ident, str) or not ID_RE.match(ident):
            v.add(f"{ap}.id", f"ids must match {ID_RE.pattern}")
        elif ident in seen:
            v.add(f"{ap}.id", f"duplicate action id {ident!r}")
        seen.add(ident if isinstance(ident, str) else "")
        if isinstance(a.get("label"), str) and not a["label"].strip():
            v.add(f"{ap}.label", "must be non-blank")
        for cond in ("visible_if", "disabled_if"):
            if isinstance(a.get(cond), str):
                parsed = _expr_names(a[cond])
                if parsed is not None and not parsed[0]:
                    v.add(f"{ap}.{cond}", "names no input, so it never changes")
        if (a.get("kind") or "submit") == "message":
            if not isinstance(a.get("message"), str) or not a["message"].strip():
                v.add(f"{ap}.message", 'required for kind "message"')
        else:
            submit = True
            if "message" in a:
                v.add(f"{ap}.message", 'only kind "message" takes a message')
    if has_input and not submit:
        v.add(
            "actions",
            'the card has inputs but no submit action; add one with kind "submit"',
        )


def _field_errors(v: _V, raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, list):
        v.add("field_errors", "must be an array of {field, message}")
        return
    if len(raw) > MAX_FIELD_ERRORS:
        v.add("field_errors", f"at most {MAX_FIELD_ERRORS}")
    for i, e in enumerate(raw):
        ep = f"field_errors[{i}]"
        if not isinstance(e, dict):
            v.add(ep, "must be {field, message}")
            continue
        for k in e:
            if k not in ("field", "message"):
                v.add(f"{ep}.{k}", "unknown property (allowed: field, message)")
        if not isinstance(e.get("message"), str) or not e["message"].strip():
            v.add(f"{ep}.message", "required")
        if not isinstance(e.get("field"), str):
            v.add(f"{ep}.field", "required")
        else:
            v.paths.append((f"{ep}.field", e["field"]))


def _field_path_problem(v: _V, target: str) -> str:
    m = FIELD_PATH_RE.match(target)
    if not m:
        return 'must be "field_id" or "repeater_id[index].field_id"'
    root = v.fields.get(m.group(1))
    if root is None:
        return f"no input with id {m.group(1)!r}"
    if m.group(2) is None:
        if root.repeater:
            return f"{m.group(1)!r} is a field of repeater {root.repeater!r}"
        return "is disabled" if root.obj.get("disabled") is True else ""
    if root.typ != "repeater":
        return f"{m.group(1)!r} is not a repeater"
    inner = v.fields.get(m.group(3))
    if inner is None or inner.repeater != m.group(1):
        return f"{m.group(3)!r} is not a field of repeater {m.group(1)!r}"
    if int(m.group(2)) >= root.items:
        return f"repeater {m.group(1)!r} opens with {root.items} item(s); there is no item {m.group(2)}"
    if root.obj.get("disabled") is True or inner.obj.get("disabled") is True:
        return "is disabled"
    return ""


def _resolve(v: _V) -> None:
    for path, src, scope in v.exprs:
        if len(src) > 500:
            v.add(path, "expression longer than 500 characters")
            continue
        parsed = _expr_names(src)
        if parsed is None:
            v.add(path, f"expression {src!r} does not parse")
            continue
        refs, funcs = parsed
        for fn in funcs:
            if fn not in EXPR_FUNCS:
                v.add(path, f"expression {src!r} calls {fn!r}, which is not a function")
        for name, members in refs:
            if name == "index":
                if not scope:
                    v.add(
                        path,
                        f"expression {src!r}: index is only defined inside a repeater's fields",
                    )
                continue
            f = v.fields.get(name)
            if f is None:
                v.add(
                    path,
                    f"expression {src!r} reads {name!r}, which is not an input id on this card",
                )
                continue
            if f.repeater and f.repeater != scope:
                v.add(
                    path,
                    f"expression {src!r} reads {name!r}, a field of repeater {f.repeater!r}",
                )
                continue
            if members and f.typ == "repeater":
                inner = v.fields.get(members[0])
                if inner is None or inner.repeater != name:
                    v.add(
                        path,
                        f"expression {src!r} reads {name}.{members[0]}: not a field of {name!r}",
                    )
    for path, target in v.paths:
        problem = _field_path_problem(v, target)
        if problem:
            v.add(path, problem)


def card_rows(card: dict[str, Any]) -> int:
    """The card-row budget a valid card uses (for tests that size a repeater)."""
    v = _V()
    for i, c in enumerate(card.get("components") or []):
        _component(v, f"components[{i}]", c, 1, "")
    return v.rows

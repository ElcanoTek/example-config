"""Connector catalog copy: every mcp_servers entry is legible in the UI.

`display_name` and `description` are the only text a user reads before
deciding whether to switch a connector on — chat's Tools picker (the wrench
popover) and Settings -> Connections both render ``display_name or name`` as
the row title with ``description`` beneath it, and render NOTHING when the
description is empty. A connector that ships neither appears as a raw
snake_case identifier over a blank body, which reads as a broken row rather
than an unlabelled one.

fleet only WARNS about a gap (it falls back to a label derived from the server
name and never invents a description), so nothing upstream fails a bundle that
regresses here. That makes this the enforcement point. The house style is
fleet's docs/MCP-CATALOG.md, "Connector copy".

Everything here is offline and cheap. Deliberately not covered: whether the
copy is *true* — that a human still has to read.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "manifest.yaml"

# Words that are plumbing, not product: a user reading a list of connectors
# already knows these are MCP servers.
DISPLAY_NAME_NOISE = {"mcp", "server", "servers", "connector", "connectors"}

MAX_DISPLAY_NAME = 40
MAX_DESCRIPTION = 200

# Openers that describe the artifact instead of what the user can do with it.
# The picker row is two lines; spending the first three words on "This server
# provides" is the difference between a legible catalog and a wall of boilerplate.
BANNED_OPENERS = (
    "this ",
    "a ",
    "an ",
    "the ",
    "mcp ",
    "tools for",
    "tool for",
    "server for",
    "provides ",
    "allows ",
    "enables ",
    "used to",
    "wrapper ",
    "integration ",
)

# The gating clause is a promise about WHEN the connector appears, so it has to
# match the entry's real gate.
GATING_CLAUSE = "Appears once"

# An env var named in prose: at least two underscore-joined upper-case
# segments, so real variables (OPENX_API_KEY, FEEDS_AWS_*) match while
# domain acronyms (SSP, DSP, SQL, S3, IAM, SES) do not.
ENV_VAR_IN_PROSE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_[A-Z0-9*]+\b")


def _servers():
    manifest = yaml.safe_load(MANIFEST_PATH.read_text())
    return manifest.get("mcp_servers") or []


def _gate_vars(entry):
    """Every env var that can make this entry appear, or None when ungated."""
    if entry.get("always"):
        return None
    gate = list(entry.get("enabled_env") or [])
    for group in entry.get("enabled_groups") or []:
        gate.extend(group)
    return gate or None


SERVERS = _servers()
IDS = [s.get("name", f"<unnamed #{i}>") for i, s in enumerate(SERVERS)]


def test_manifest_declares_servers():
    """A copy suite that silently matches zero servers proves nothing."""
    assert SERVERS, f"no mcp_servers entries found in {MANIFEST_PATH}"


@pytest.mark.parametrize("entry", SERVERS, ids=IDS)
def test_display_name(entry):
    name = entry.get("name")
    display = entry.get("display_name")
    assert display, (
        f"{name}: no display_name — the Tools picker would show the raw "
        f"identifier {name!r} (fleet warns at boot and derives a plain label)"
    )
    assert display == display.strip(), f"{name}: display_name has edge whitespace"
    assert len(display) <= MAX_DISPLAY_NAME, f"{name}: display_name is {len(display)} chars (max {MAX_DISPLAY_NAME})"
    assert "_" not in display, f"{name}: display_name {display!r} still reads as an identifier"
    assert display[0].isupper(), f"{name}: display_name {display!r} should start upper-case"
    noise = DISPLAY_NAME_NOISE & {w.strip("()-").lower() for w in display.split()}
    assert not noise, f"{name}: display_name {display!r} carries plumbing words {sorted(noise)}"


@pytest.mark.parametrize("entry", SERVERS, ids=IDS)
def test_description_shape(entry):
    name = entry.get("name")
    desc = entry.get("description")
    assert desc, (
        f"{name}: no description — the Tools picker renders this connector "
        f"with an empty body, indistinguishable from a broken row"
    )
    assert desc == desc.strip(), f"{name}: description has edge whitespace"
    assert len(desc) <= MAX_DESCRIPTION, (
        f"{name}: description is {len(desc)} chars (max {MAX_DESCRIPTION}); "
        f"the picker row is two lines, not a paragraph"
    )
    assert desc.endswith("."), f"{name}: description should end with a period"
    # Plain text only — the picker renders the raw string, so markup shows up
    # literally. Underscores and `*` are NOT banned: they spell env var names
    # (OPENX_API_KEY) and the family wildcard (FEEDS_AWS_*) the gating clause
    # is required to use.
    assert not any(c in desc for c in "`#|"), f"{name}: description must be plain text, no markdown"
    assert "**" not in desc, f"{name}: description must be plain text, no markdown emphasis"
    assert "Try:" not in desc, f"{name}: example prompts belong in the bundle's docs, not in a two-line picker row"
    lowered = desc.lower()
    for opener in BANNED_OPENERS:
        assert not lowered.startswith(opener), (
            f"{name}: description opens with {opener!r} — lead with an "
            f"imperative capability verb instead ('Search and read...', 'Create and manage...')"
        )
    display = (entry.get("display_name") or "").lower()
    assert display and not lowered.startswith(display), (
        f"{name}: description restates the display name; the row already shows it"
    )


@pytest.mark.parametrize("entry", SERVERS, ids=IDS)
def test_gating_clause_matches_the_real_gate(entry):
    """A promise about when a connector appears has to match its gate.

    Both directions are wrong in a user-visible way: a gating clause on an
    ungated connector is a lie the picker contradicts on screen, and a gated
    connector with no clause leaves a user staring at a connector list that is
    missing an entry with nothing to explain why.
    """
    name = entry.get("name")
    desc = entry.get("description") or ""
    gate = _gate_vars(entry)
    claims_gating = GATING_CLAUSE in desc

    if gate is None:
        assert not claims_gating, (
            f"{name}: description claims a credential gate but the entry has "
            f"no enabled_env/enabled_groups (or is always: true) — it appears regardless"
        )
        return

    assert claims_gating, (
        f"{name}: entry is gated on {sorted(set(gate))} but the description "
        f"never says so; add a '{GATING_CLAUSE} ... is set.' clause"
    )
    # Any variable named in prose must be one this entry is actually gated on
    # (a trailing * collapses a family: FEEDS_AWS_* covers FEEDS_AWS_*_KEY).
    for named in ENV_VAR_IN_PROSE.findall(desc):
        if named.endswith("*"):
            prefix = named.rstrip("*")
            assert any(v.startswith(prefix) for v in gate), (
                f"{name}: description names {named!r}, which matches no gate var in {sorted(set(gate))}"
            )
        else:
            assert named in gate, (
                f"{name}: description names {named!r}, which is not one of this entry's gate vars {sorted(set(gate))}"
            )

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

import json
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

# Setup text does not belong in a description: fleet lists a connector whose
# credentials are not set as "Needs setup" in the Tools picker and on Settings
# -> Connections, with copy that depends on whether the reader is an admin.
SETUP_PHRASES = ("Appears once", "No credentials required")

# An env var named in prose: at least two underscore-joined upper-case
# segments, so real variables (OPENX_API_KEY, FEEDS_AWS_*) match while
# domain acronyms (SSP, DSP, SQL, S3, IAM, SES) do not.
# The end is a lookahead, not \b: a trailing family wildcard (FEEDS_AWS_*)
# ends in a non-word character, where \b would not match.
ENV_VAR_IN_PROSE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9*]+)+(?![A-Za-z0-9_*])")

# The Tools-picker groups, by ID: fleet's internal/clientconfig/
# connector_categories.go. An ID, never free text, so "SSP" and "SSPs" cannot
# split one group in two. fleet FAILS THE LOAD on an unknown one, so a typo
# here would stop the deployment booting; this catches it first.
CONNECTOR_CATEGORIES = {
    "house",
    "ssps",
    "deal-management",
    "data-storage",
    "development",
    "cloud-infrastructure",
    "databases",
    "data-analytics",
    "web-search",
    "productivity",
    "communication",
    "crm-sales",
    "customer-support",
    "commerce-payments",
    "finance",
    "design-media",
    "observability",
    "security",
    "ai-ml",
    "automation",
    "marketing-social",
    "knowledge-docs",
    "travel-local",
    "misc",
}


def _servers():
    manifest = yaml.safe_load(MANIFEST_PATH.read_text())
    return manifest.get("mcp_servers") or []


def _plugin_servers():
    """Connectors an Agent Plugin declares through fleet's extension."""
    out = []
    for plugin_json in sorted((MANIFEST_PATH.parent / "plugins").glob("*/plugin.json")):
        ext = (json.loads(plugin_json.read_text()).get("extensions") or {}).get("com.elcanotek.fleet") or {}
        for name, entry in (ext.get("mcp_servers") or {}).items():
            out.append({"name": name, **entry, "_source": str(plugin_json.relative_to(MANIFEST_PATH.parent))})
    return out


SERVERS = _servers()
PLUGIN_SERVERS = _plugin_servers()
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
    # literally. Underscores are NOT banned: they spell feed and table names
    # (openx_bids).
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


@pytest.mark.parametrize("entry", SERVERS + PLUGIN_SERVERS, ids=IDS + [e["name"] for e in PLUGIN_SERVERS])
def test_description_has_no_setup_text(entry):
    """The status says when a connector needs setup; the description says what it does.

    A gating clause ("Appears once OPENX_API_KEY is set") duplicated what the
    Tools picker now shows as "Needs setup", and put env var names in front of
    people who cannot set them. Plugin connectors show in the same picker.
    """
    name = entry.get("name")
    desc = entry.get("description") or ""
    for phrase in SETUP_PHRASES:
        assert phrase not in desc, (
            f"{name}: description carries setup text {phrase!r}; the Needs setup status covers it"
        )
    named = ENV_VAR_IN_PROSE.findall(desc)
    assert not named, f"{name}: description names env vars {named}; keep them in the bundle docs"


@pytest.mark.parametrize("entry", SERVERS, ids=IDS)
def test_category(entry):
    """Every connector sits under a Tools-picker heading fleet knows."""
    name = entry.get("name")
    cat = entry.get("category")
    assert cat, f"{name}: no category; the Tools picker files it under Misc Tools and fleet warns at boot"
    assert cat in CONNECTOR_CATEGORIES, (
        f"{name}: unknown category {cat!r}; fleet refuses to load the bundle. "
        f"Pick one of {sorted(CONNECTOR_CATEGORIES)}"
    )


@pytest.mark.parametrize("entry", PLUGIN_SERVERS, ids=[e["name"] for e in PLUGIN_SERVERS])
def test_plugin_connector_category(entry):
    """A plugin's connector sits under a known heading too (fleet reports an unknown one)."""
    cat = entry.get("category")
    assert cat, f"{entry['name']} ({entry['_source']}): no category in the fleet extension"
    assert cat in CONNECTOR_CATEGORIES, f"{entry['name']} ({entry['_source']}): unknown category {cat!r}"


@pytest.mark.parametrize(
    "text,named",
    [
        ("Appears once OPENX_API_KEY is set.", ["OPENX_API_KEY"]),
        ("Appears once the FEEDS_AWS_* credentials are set.", ["FEEDS_AWS_*"]),
        ("Query openx_bids over Athena for SSP and DSP data in S3.", []),
    ],
)
def test_env_var_pattern(text, named):
    """The env-name gate catches plain and wildcard names, not acronyms or table names."""
    assert ENV_VAR_IN_PROSE.findall(text) == named

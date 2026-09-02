"""Tests for the example Agent Plugin: its ``plugin_notes`` server and the
contract between ``plugin.json`` / ``mcp.json`` and the server's source.

The server lives under ``plugins/example-plugin/server/`` rather than ``mcp/``
(the Agent Plugins format requires a plugin to be self-contained), so it is
loaded here by path. It reads ``PLUGIN_ROOT`` / ``PLUGIN_DATA`` /
``PLUGIN_NOTES_FILE`` at import, exactly as it would when a client launches it,
which is why every test goes through the ``notes`` fixture: a fresh module per
test, pointed at a temp data dir.

The contract tests exist for the same reason the manifest ones do: a tool
renamed in code but still listed in the ``com.elcanotek.fleet`` allowlist is
dropped by fleet at boot with no error, and a probe naming a tool that is not
in the allowlist is dropped with only a report.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "example-plugin"
SERVER = PLUGIN / "server" / "plugin_notes.py"
FLEET_NAMESPACE = "com.elcanotek.fleet"


@pytest.fixture
def notes(tmp_path, monkeypatch):
    """Import a fresh copy of the server with the plugin variables pointed at tmp_path."""
    data = tmp_path / "data"
    monkeypatch.setenv("PLUGIN_ROOT", str(PLUGIN))
    monkeypatch.setenv("PLUGIN_DATA", str(data))
    monkeypatch.setenv("PLUGIN_NOTES_FILE", str(data / "notes.jsonl"))
    spec = importlib.util.spec_from_file_location("plugin_notes_under_test", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── the server ──────────────────────────────────────────────────────────────


def test_plugin_info_reports_the_client_supplied_variables(notes, tmp_path):
    info = notes.plugin_info()
    assert info["plugin_root"] == str(PLUGIN)
    assert info["plugin_data"] == str(tmp_path / "data")
    assert info["plugin_root_from_env"] is True
    assert info["plugin_data_from_env"] is True
    assert info["note_count"] == 0


def test_note_round_trip_persists_in_plugin_data(notes, tmp_path):
    rec = notes.note_add("  first note  ", tags=["Ops", "ops", " release "])
    assert rec["text"] == "first note"
    assert rec["tags"] == ["ops", "release"], "tags are lower-cased, trimmed and de-duplicated"
    assert (tmp_path / "data" / "notes.jsonl").is_file(), "the note lands under PLUGIN_DATA"

    notes.note_add("second", tags=["ops"])
    notes.note_add("third")
    listed = notes.note_list()
    assert [n["text"] for n in listed] == ["third", "second", "first note"], "newest first"
    assert [n["text"] for n in notes.note_list(tag="ops")] == ["second", "first note"]
    assert [n["text"] for n in notes.note_list(tag="OPS", limit=1)] == ["second"]
    assert notes.plugin_info()["note_count"] == 3


def test_note_add_rejects_empty_and_oversized_text(notes):
    assert "error" in notes.note_add("   ")
    assert "error" in notes.note_add("x" * (notes.MAX_TEXT_CHARS + 1))
    assert notes.plugin_info()["note_count"] == 0


def test_tags_are_capped(notes):
    rec = notes.note_add("many tags", tags=[f"t{i}" for i in range(notes.MAX_TAGS + 5)])
    assert len(rec["tags"]) == notes.MAX_TAGS


def test_corrupt_line_is_skipped_not_fatal(notes, tmp_path):
    notes.note_add("good")
    path = tmp_path / "data" / "notes.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write('"a string, not an object"\n')
    assert [n["text"] for n in notes.note_list()] == ["good"]


def test_note_clear_removes_everything_and_reports_the_count(notes, tmp_path):
    notes.note_add("a")
    notes.note_add("b")
    assert notes.note_clear() == {"cleared": 2}
    assert not (tmp_path / "data" / "notes.jsonl").exists()
    assert notes.note_list() == []
    assert notes.note_clear() == {"cleared": 0}


def test_runs_without_a_plugin_loader(tmp_path, monkeypatch):
    """Started by hand (no PLUGIN_* env), the server still resolves sane defaults."""
    for var in ("PLUGIN_ROOT", "PLUGIN_DATA", "PLUGIN_NOTES_FILE"):
        monkeypatch.delenv(var, raising=False)
    spec = importlib.util.spec_from_file_location("plugin_notes_bare", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    info = module.plugin_info()
    assert info["plugin_root"] == str(PLUGIN)
    assert info["plugin_root_from_env"] is False
    assert Path(info["plugin_data"]) == PLUGIN / ".plugin-data"


# ── plugin.json / mcp.json ↔ server ─────────────────────────────────────────


@pytest.fixture(scope="module")
def plugin_manifest() -> dict:
    return json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mcp_manifest() -> dict:
    return json.loads((PLUGIN / "mcp.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def server_source() -> str:
    return SERVER.read_text(encoding="utf-8")


def test_plugin_manifest_has_the_required_fields(plugin_manifest):
    assert plugin_manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    name = plugin_manifest["name"]
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", name) and "--" not in name and ".." not in name
    assert name == PLUGIN.name, "fleet keys PLUGIN_DATA by the manifest name; keep it equal to the folder"


def test_mcp_manifest_declares_one_stdio_server_whose_script_exists(mcp_manifest):
    assert mcp_manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    servers = mcp_manifest["mcpServers"]
    assert set(servers) == {"plugin_notes"}
    entry = servers["plugin_notes"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "python3"
    scripts = [a for a in entry["args"] if a.startswith("${PLUGIN_ROOT}/")]
    assert scripts, "the server is launched via ${PLUGIN_ROOT} so the plugin works at any install path"
    for arg in scripts:
        assert (PLUGIN / arg[len("${PLUGIN_ROOT}/") :]).is_file(), f"{arg} does not exist in the plugin"
    assert entry["env"]["PLUGIN_NOTES_FILE"].startswith("${PLUGIN_DATA}/"), "the data file must live in PLUGIN_DATA"
    for key in entry["env"]:
        assert key not in ("PLUGIN_ROOT", "PLUGIN_DATA"), "the two reserved names may not be set by env"


def test_fleet_extension_allowlist_and_probe_match_the_server(plugin_manifest, server_source):
    overrides = plugin_manifest["extensions"][FLEET_NAMESPACE]["mcp_servers"]
    assert set(overrides) == {"plugin_notes"}, "the extension may only name servers mcp.json declares"
    tools = overrides["plugin_notes"]["tools"]
    defined = set(re.findall(r"^def (\w+)\(", server_source, re.M))
    for tool in tools:
        assert tool in defined, f"allowlisted tool {tool!r} is not defined by the server (fleet would drop it silently)"
    probe = overrides["plugin_notes"]["probe"]
    assert probe["tool"] in tools, "a probe outside the allowlist is dropped by fleet with only a report"
    for key in overrides["plugin_notes"]:
        assert key not in ("env", "enabled_env", "account_vars", "identity_env"), (
            "nothing credential-shaped is expressible in fleet's extension; use manifest.yaml"
        )


def test_every_plugin_skill_is_well_formed():
    """fleet skips a skill whose frontmatter name is not the folder name."""
    skills = sorted((PLUGIN / "skills").iterdir())
    assert skills, "the example plugin ships at least one skill"
    for folder in skills:
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{folder.name}: frontmatter must be the first thing in SKILL.md"
        front = text.split("---\n", 2)[1]
        name = re.search(r"^name:\s*(\S+)\s*$", front, re.M)
        assert name and name.group(1) == folder.name, f"{folder.name}: frontmatter name must equal the folder name"
        assert re.search(r"^description:\s*\S", front, re.M), f"{folder.name}: description is required"

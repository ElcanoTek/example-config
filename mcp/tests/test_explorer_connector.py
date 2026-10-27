"""The `explorer` connector reaches this deployment's own Explorer and nothing else.

Offline checks over manifest.yaml:

- the URL comes only from EXPLORER_MCP_URL; its only default is the reserved,
  never-resolving explorer.invalid, so an unset variable can never send this
  deployment's searches to another organisation's Explorer, while the bundle
  still loads (fleet refuses an empty url);
- the connector stays dark until both the URL and the token are set;
- the tool allowlist is the reviewed Explorer catalog (ElcanoTek/explorer
  internal/mcp/tools.json), all read-only and parallel-safe;
- the search tools left the stdio `email` server, and nothing in the bundle
  still calls them there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "manifest.yaml"

# Pinned on purpose: when Explorer adds or retires a tool, this list changes
# with it, by a person.
CATALOG = {"search_emails", "search_emails_fuzzy", "find_latest_report"}


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def explorer(manifest):
    servers = [s for s in manifest["mcp_servers"] if s["name"] == "explorer"]
    assert len(servers) == 1, "exactly one explorer connector"
    return servers[0]


def test_url_comes_only_from_the_environment(explorer):
    assert explorer["type"] == "http"
    assert explorer["url"] == "${EXPLORER_MCP_URL:-https://explorer.invalid/mcp}"
    assert explorer["headers"] == {"Authorization": "Bearer ${EXPLORER_API_TOKEN}"}
    assert "account_vars" not in explorer, "fleet cannot run a named account against an http base"


def test_dark_until_url_and_token_are_both_set(explorer):
    assert set(explorer["enabled_env"]) == {"EXPLORER_MCP_URL", "EXPLORER_API_TOKEN"}
    assert not explorer.get("always"), "always: true would bypass the env gate"
    assert not explorer.get("enabled_groups"), "the env gate must be the only gate"


def test_tools_are_the_reviewed_catalog_and_parallel_safe(explorer, manifest):
    assert set(explorer["tools"]) == CATALOG
    assert len(explorer["tools"]) == len(CATALOG), "no duplicates"
    policy = manifest["agent_policy"]
    parallel = set(policy["parallel_safe_tools"])
    critical = set(policy.get("critical_tools") or [])
    for tool in CATALOG:
        assert f"mcp_explorer_{tool}" in parallel
        assert f"mcp_explorer_{tool}" not in critical


def test_nothing_still_calls_the_search_tools_on_the_email_server():
    pattern = re.compile(r"mcp_email_(search_emails|search_emails_fuzzy|find_latest_report)\b")
    offenders = []
    for path in ROOT.rglob("*"):
        if (
            path.is_dir()
            or ".git" in path.parts
            or ".venv" in path.parts
            or path.suffix not in {".yaml", ".yml", ".md", ".json"}
        ):
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders

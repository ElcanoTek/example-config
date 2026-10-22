"""The `lens` connector reaches this deployment's own Lens and nothing else.

Offline checks over manifest.yaml, pinning what would be expensive to get
wrong silently:

- the URL comes only from LENS_MCP_URL; its only default is the reserved,
  never-resolving lens.invalid, so an unset variable can never send this
  deployment's lists to another organisation's Lens, while the bundle still
  loads (fleet refuses an empty url);
- the connector stays dark until both the URL and the token are set;
- the tool allowlist is the reviewed Lens catalog (ElcanoTek/lens
  lens_mcp.TOOLS), every tool classified as read-only or gated;
- job control is approval-gated (never notify mode) with server-qualified
  names, and the reads are parallel-safe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "manifest.yaml"


def _matches(runtime_name, key):
    # fleet's policy matcher (internal/agentcore/audit.go, agent_policy.go):
    # a key applies to a tool when it equals the runtime name or is an
    # underscore-delimited suffix of it.
    return runtime_name == key or runtime_name.endswith("_" + key)


def effective_mode(policy, runtime_name):
    """The approval mode fleet resolves: the longest matching key wins."""
    best, mode = -1, "approve"
    for key, value in (policy.get("critical_tool_modes") or {}).items():
        if _matches(runtime_name, key) and len(key) > best:
            best, mode = len(key), value
    return mode


def is_critical(policy, runtime_name):
    return any(_matches(runtime_name, key) for key in policy["critical_tools"])


# Pinned on purpose: when Lens adds or retires a tool, this list and the
# classification are updated together, by a person.
READ_ONLY = {
    "get_job",
    "list_jobs",
    "get_job_results",
    "get_download_link",
    "list_input_files",
    "get_options",
}
GATED = {"create_job", "cancel_job"}
CATALOG = READ_ONLY | GATED


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lens(manifest):
    servers = [s for s in manifest["mcp_servers"] if s["name"] == "lens"]
    assert len(servers) == 1, "exactly one lens connector"
    return servers[0]


@pytest.fixture(scope="module")
def policy(manifest):
    return manifest["agent_policy"]


def test_url_comes_only_from_the_environment(lens):
    assert lens["type"] == "http"
    assert lens["url"] == "${LENS_MCP_URL:-https://lens.invalid/mcp}"
    assert lens["headers"] == {"Authorization": "Bearer ${LENS_API_TOKEN}"}
    assert "account_vars" not in lens, "fleet cannot run a named account against an http base"


def test_dark_until_url_and_token_are_both_set(lens):
    assert set(lens["enabled_env"]) == {"LENS_MCP_URL", "LENS_API_TOKEN"}
    assert not lens.get("always"), "always: true would bypass the env gate"
    assert not lens.get("enabled_groups"), "the env gate must be the only gate"


def test_tools_are_the_reviewed_catalog(lens):
    assert set(lens["tools"]) == CATALOG
    assert len(lens["tools"]) == len(CATALOG), "no duplicates"


def test_job_control_is_gated_and_reads_are_parallel_safe(policy):
    critical = set(policy["critical_tools"])
    parallel = set(policy["parallel_safe_tools"])
    for tool in GATED:
        assert f"mcp_lens_{tool}" in critical
        assert is_critical(policy, f"mcp_lens_{tool}")
        assert f"mcp_lens_{tool}" not in parallel
    for tool in READ_ONLY:
        assert f"mcp_lens_{tool}" in parallel
        assert f"mcp_lens_{tool}" not in critical
    # A bare `create_job` / `cancel_job` suffix would also gate any other
    # server's tool of that name.
    assert "create_job" not in critical and "cancel_job" not in critical


def test_job_control_needs_a_person_in_chat(policy):
    # Starting a job spends credit nothing can refund, so neither Lens write
    # may run in notify mode (which executes before anyone decides).
    modes = policy.get("critical_tool_modes") or {}
    no_session = set(policy.get("critical_tool_no_session_approval") or [])
    for tool in GATED:
        assert modes.get(f"mcp_lens_{tool}", "approve") == "approve"
        # Resolved the way fleet does, so a bare "create_job": notify (or any
        # other matching key) fails here too.
        assert effective_mode(policy, f"mcp_lens_{tool}") == "approve"
        # ...and every call gets its own decision: no "apply to all".
        assert f"mcp_lens_{tool}" in no_session
        # ...and the agent resumes with the result once the card settles.
        assert f"mcp_lens_{tool}" in set(policy.get("critical_tool_resume") or [])


def test_skill_names_only_real_tools():
    skill = (ROOT / "skills" / "lens-classification" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: lens-classification\n")
    mentioned = set(re.findall(r"mcp_lens_[a-z_]*[a-z]", skill))
    assert mentioned, "the skill should name the tools it teaches"
    assert mentioned <= {f"mcp_lens_{tool}" for tool in CATALOG}, mentioned - {f"mcp_lens_{t}" for t in CATALOG}

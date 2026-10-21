"""The `pages` connector reaches this deployment's own Pages and nothing else.

Offline checks over manifest.yaml. They pin the properties that would be
expensive to get wrong silently:

- the URL comes only from PAGES_MCP_URL; its only default is the reserved,
  never-resolving pages.invalid, so an unset variable can never send this
  deployment's data to another organisation's Pages, while the bundle still
  loads (fleet refuses an empty url, and a bare ${PAGES_MCP_URL} fails the load);
- the connector stays dark until both the URL and the token are set;
- every Pages write that changes a page, its access, a shared design or a
  workspace is audit-gated, and every tool is accounted for as read-only, gated
  or deliberately ungated, so a tool added to the allowlist cannot slip in
  unclassified;
- only the data commits run in notify mode, and they are one audited action.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "manifest.yaml"


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


def effective_undo_hint(policy, runtime_name):
    best, hint = -1, ""
    for key, value in (policy.get("critical_tool_undo_hints") or {}).items():
        if _matches(runtime_name, key) and len(key) > best:
            best, hint = len(key), value
    return hint


def is_critical(policy, runtime_name):
    return any(_matches(runtime_name, key) for key in policy["critical_tools"])


# The reviewed catalog: the Pages allowlist (its docs/INTEGRATION.md) minus
# create_upload_ticket. Pinned on purpose: when Pages adds or retires a tool,
# this list and the classification below are updated together, by a person.
READ_ONLY = {
    "list_pages",
    "get_page",
    "get_page_data",
    "get_page_refresh",
    "prepare_dashboard_update",
    "find_in_version",
    "preflight_page",
    "get_version",
    "list_versions",
    "list_workspaces",
    "list_themes",
    "get_guide",
    "list_templates",
    "get_template",
    "list_template_revisions",
    "validate_template",
    "template_urls",
    "list_template_pages",
    "get_page_config",
    "page_urls",
}
# Read-only compatibility alias, deliberately not offered as parallel-safe.
READ_ONLY_NOT_PARALLEL = {"configure_page_refresh"}
GATED_WRITES = {
    "deploy_page",
    "deploy_page_upload",
    "update_page",
    "patch_page",
    "update_page_config",
    "create_page_from_template",
    "rerender_page_from_template",
    "create_template_from_page",
    "register_template_upload",
    "delete_template",
    "publish_page",
    "rollback_page",
    "set_password",
    "set_title",
    "delete_page",
    "create_workspace",
    "rename_workspace",
    "set_page_workspace",
    "update_page_data",
    "update_page_data_upload",
}
# Writes that stay ungated: staging bytes (no page or version changes until a
# gated commit) and recording a refresh check (check status only; no page
# content or published version changes).
UNGATED_WRITES = {
    "start_page_upload",
    "append_page_upload",
    "cancel_page_upload",
    "record_refresh_check",
}
CATALOG = READ_ONLY | READ_ONLY_NOT_PARALLEL | GATED_WRITES | UNGATED_WRITES


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pages(manifest):
    servers = [s for s in manifest["mcp_servers"] if s["name"] == "pages"]
    assert len(servers) == 1, "exactly one pages connector"
    return servers[0]


def test_url_comes_only_from_the_environment(pages):
    assert pages["type"] == "http"
    # The only fallback is a reserved name that can never resolve (RFC 6761):
    # fleet needs a non-empty url to load the bundle, and an unset variable must
    # never reach another organisation's Pages.
    assert pages["url"] == "${PAGES_MCP_URL:-https://pages.invalid/mcp}"
    assert "data_sources" not in pages, "an unset ${PAGES_MCP_URL} there would fail the bundle load"
    assert pages["headers"] == {"Authorization": "Bearer ${PAGES_API_TOKEN}"}
    assert "account_vars" not in pages, "fleet cannot run a named account against an http base"


def test_dark_until_url_and_token_are_both_set(pages):
    assert set(pages["enabled_env"]) == {"PAGES_MCP_URL", "PAGES_API_TOKEN"}
    # Fleet checks `always` before enabled_env, and enabled_groups changes the
    # gate's meaning; either would let the connector register without both.
    assert not pages.get("always"), "always: true would bypass the env gate"
    assert not pages.get("enabled_groups"), "the env gate must be the only gate"


def test_no_other_organisations_host(pages):
    text = yaml.safe_dump(pages).lower()
    for host in ("elcanotek.com", "elcano-pages.com", "pages.example"):
        assert host not in text


def test_tool_list_is_the_reviewed_catalog(pages):
    tools = pages["tools"]
    assert len(tools) == len(set(tools)), "no duplicate tools"
    assert "create_upload_ticket" not in tools, "its shell PUT cannot work from fleet's sandbox"
    assert set(tools) == CATALOG, (
        f"catalog drift; review and classify: added {sorted(set(tools) - CATALOG)}, "
        f"missing {sorted(CATALOG - set(tools))}"
    )


def test_policy_matches_the_reviewed_classification(manifest, pages):
    policy = manifest["agent_policy"]
    tools = set(pages["tools"])
    gated = {t for t in tools if is_critical(policy, f"mcp_pages_{t}")}
    parallel = {t.removeprefix("mcp_pages_") for t in policy["parallel_safe_tools"] if t.startswith("mcp_pages_")}
    assert gated == GATED_WRITES, (
        f"gated drift: extra {sorted(gated - GATED_WRITES)}, missing {sorted(GATED_WRITES - gated)}"
    )
    assert parallel == READ_ONLY, (
        f"parallel-safe must be exactly the reviewed read-only set: {sorted(parallel ^ READ_ONLY)}"
    )
    assert not (gated & parallel)


def test_page_changing_writes_are_gated(manifest):
    gated = set(manifest["agent_policy"]["critical_tools"])
    for tool in (
        "deploy_page",
        "deploy_page_upload",
        "update_page",
        "patch_page",
        "update_page_config",
        "create_page_from_template",
        "rerender_page_from_template",
        "create_template_from_page",
        "delete_template",
        "publish_page",
        "rollback_page",
        "set_password",
        "set_title",
        "delete_page",
        "create_workspace",
        "rename_workspace",
        "set_page_workspace",
        "update_page_data",
        "update_page_data_upload",
    ):
        assert tool in gated, f"{tool} must be audit-gated"


def test_only_data_commits_notify_and_they_are_one_action(manifest, pages):
    # Resolve every tool the way fleet does (exact or underscore suffix,
    # longest key wins), so an override keyed on a longer or qualified name
    # (e.g. "mcp_pages_delete_page": notify) cannot slip past this test.
    policy = manifest["agent_policy"]
    notify = {t for t in pages["tools"] if effective_mode(policy, f"mcp_pages_{t}") == "notify"}
    assert notify == {"update_page_data", "update_page_data_upload"}
    assert policy["critical_tool_aliases"]["update_page_data"] == ["update_page_data_upload"]
    for tool in notify:
        hint = effective_undo_hint(policy, f"mcp_pages_{tool}")
        # A bare rollback_page(slug) restores the newest OTHER approved version,
        # which is wrong after two refreshes and moves the live page after an
        # unpublished canary; the hint must name the explicit version.
        assert "rollback_page" in hint and "version_id" in hint and "expected_version" in hint
        assert "publish=false" in hint

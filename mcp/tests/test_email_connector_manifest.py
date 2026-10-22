"""Contract tests for independent inbound and credential-gated outbound email."""

from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parents[2] / "manifest.yaml"


def _servers() -> dict[str, dict]:
    manifest = yaml.safe_load(MANIFEST.read_text())
    return {server["name"]: server for server in manifest["mcp_servers"]}


def test_s3_inbound_is_independent_of_outbound_ses() -> None:
    servers = _servers()
    inbound = servers["email"]
    assert inbound["optional"] is True
    assert inbound["enabled_by_default"] is True
    assert inbound["enabled_env"] == ["EMAIL_S3_BUCKET"]
    assert inbound["env"]["EMAIL_S3_BUCKET"] == "${EMAIL_S3_BUCKET}"
    assert "SES_OUTBOUND" not in str(inbound)
    # Named accounts would not isolate credentials (both connectors map AWS_*).
    assert "account_vars" not in inbound
    # enabled_env must be the only gate: fleet enables an `always` server
    # before it ever looks at enabled_env (clientconfig.go), and an
    # enabled_groups entry would add a second way on.
    assert not inbound.get("always")
    assert "enabled_groups" not in inbound
    assert inbound["probe"] == {"tool": "get_inbox_stats", "contains": '"status": "success"'}
    # Static keys stay optional so an instance role can stand in, and downloads
    # are confined to the workspace root.
    assert {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "EMAIL_ALLOWED_DIRS"} <= set(inbound["optional_env"])
    assert inbound["env"]["EMAIL_ALLOWED_DIRS"] == "${FLEET_WORKSPACE_ROOT}"


def test_ses_outbound_requires_sender_credentials_and_address() -> None:
    outbound = _servers()["ses_outbound"]
    assert outbound["optional"] is True
    assert outbound["enabled_env"] == [
        "SES_OUTBOUND_AWS_ACCESS_KEY_ID",
        "SES_OUTBOUND_AWS_SECRET_ACCESS_KEY",
        "SES_OUTBOUND_FROM_EMAIL",
    ]
    assert "account_vars" not in outbound
    assert not outbound.get("always")
    assert "enabled_groups" not in outbound
    assert outbound["env"]["AWS_ACCESS_KEY_ID"] == "${SES_OUTBOUND_AWS_ACCESS_KEY_ID}"
    assert outbound["env"]["AWS_SECRET_ACCESS_KEY"] == "${SES_OUTBOUND_AWS_SECRET_ACCESS_KEY}"
    # No default sender: an unset From keeps the connector dark (enabled_env).
    assert outbound["env"]["SES_FROM_EMAIL"] == "${SES_OUTBOUND_FROM_EMAIL}"
    assert outbound["env"]["SES_AWS_REGION"].endswith("us-east-2}")
    assert outbound["env"]["SES_ALLOWED_RECIPIENTS"] == "${SES_OUTBOUND_ALLOWED_RECIPIENTS}"
    # Fail closed: unlisted recipients are refused unless the host opts in.
    assert outbound["env"]["SES_ALLOW_UNLISTED_RECIPIENTS"].endswith("false}")
    # A scheduled run writes its report under the workspace root, not the
    # per-run ledger dir; without this root send_email cannot attach it.
    assert outbound["env"]["CUTLASS_ALLOWED_DIRS"].endswith(":${FLEET_WORKSPACE_ROOT}")


def test_send_email_is_approval_gated() -> None:
    """The one outbound write is in critical_tools, per this bundle's contract."""
    manifest = yaml.safe_load(MANIFEST.read_text())
    assert "send_email" in manifest["agent_policy"]["critical_tools"]
    # A mode override (e.g. `send_email: notify`) would drop the pre-send
    # approval while the membership check above still passed.
    modes = manifest["agent_policy"].get("critical_tool_modes") or {}
    for key, mode in modes.items():
        if key.endswith("send_email"):
            assert mode == "approve", f"{key}: {mode} removes the pre-send approval"
    assert "send_email" in _servers()["ses_outbound"]["tools"]


async def test_ses_outbound_allowlist_matches_the_server_tools() -> None:
    """fleet silently drops an allowlisted tool the server does not register,
    and never offers a registered tool the allowlist omits."""
    from ses_sender import mcp

    registered = {tool.name for tool in await mcp.list_tools()}
    assert set(_servers()["ses_outbound"]["tools"]) == registered

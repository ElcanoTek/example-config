"""Tests for the credential-gated example_api MCP server.

httpx is mocked with respx so no network call is ever made. We set
EXAMPLE_API_KEY via monkeypatch so the connector is "enabled" for the test, and
assert that the Bearer header is sent and payloads are POSTed correctly.
"""

import example_api as api
import httpx
import pytest
import respx

BASE_URL = "https://api.example.com/v1"


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    """Enable the connector with a fake key and the default base URL."""
    monkeypatch.setenv("EXAMPLE_API_KEY", "test-secret-key")
    monkeypatch.delenv("EXAMPLE_API_BASE_URL", raising=False)
    monkeypatch.delenv("EXAMPLE_API_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("EXAMPLE_API_OUTPUT_DIR", raising=False)


@respx.mock
def test_list_records_parses_and_sends_bearer():
    route = respx.get(f"{BASE_URL}/tickets").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "1"}, {"id": "2"}]})
    )

    result = api.api_list_records("tickets", limit=2)

    assert result["success"] is True
    assert result["data"] == {"items": [{"id": "1"}, {"id": "2"}]}

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-secret-key"
    assert request.url.params["limit"] == "2"


@respx.mock
def test_get_record_parses_and_sends_bearer():
    route = respx.get(f"{BASE_URL}/tickets/42").mock(
        return_value=httpx.Response(200, json={"id": "42", "status": "open"})
    )

    result = api.api_get_record("tickets", "42")

    assert result["success"] is True
    assert result["data"]["id"] == "42"
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-secret-key"


@respx.mock
def test_submit_record_posts_payload():
    route = respx.post(f"{BASE_URL}/tickets").mock(
        return_value=httpx.Response(201, json={"id": "99", "created": True})
    )

    payload = {"title": "Printer is on fire", "priority": "high"}
    result = api.api_submit_record("tickets", payload)

    assert result["success"] is True
    assert result["data"]["id"] == "99"

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer test-secret-key"
    import json

    assert json.loads(request.content) == payload


@respx.mock
def test_http_error_is_structured():
    respx.get(f"{BASE_URL}/tickets/missing").mock(
        return_value=httpx.Response(404, text="Not Found")
    )

    result = api.api_get_record("tickets", "missing")

    assert result["success"] is False
    assert result["status_code"] == 404
    assert "404" in result["error"]


def test_missing_key_is_gated_off(monkeypatch):
    """With no key set the connector returns a structured error, not a request."""
    monkeypatch.delenv("EXAMPLE_API_KEY", raising=False)
    result = api.api_list_records("tickets")
    assert result["success"] is False
    assert "EXAMPLE_API_KEY" in result["error"]


@respx.mock
def test_submit_record_writes_receipt_when_output_dir_set(monkeypatch, tmp_path):
    """With EXAMPLE_API_OUTPUT_DIR set (fleet's ${FLEET_WORKSPACE} mapping), a
    successful submit leaves a JSON receipt of what was posted and returned."""
    out_dir = tmp_path / "outputs"
    monkeypatch.setenv("EXAMPLE_API_OUTPUT_DIR", str(out_dir))
    respx.post(f"{BASE_URL}/tickets").mock(
        return_value=httpx.Response(201, json={"id": "7"})
    )

    result = api.api_submit_record("tickets", {"title": "New laptop"})

    assert result["success"] is True
    receipts = list(out_dir.glob("submit-*.json"))
    assert len(receipts) == 1
    import json

    receipt = json.loads(receipts[0].read_text())
    assert receipt["resource"] == "tickets"
    assert receipt["payload"] == {"title": "New laptop"}
    assert receipt["result"]["data"] == {"id": "7"}


@respx.mock
def test_submit_record_degrades_gracefully_without_output_dir(tmp_path):
    """Unset EXAMPLE_API_OUTPUT_DIR (fleet dropped the key: no workspace to
    offer) must not change the tool's behavior — success, and no receipt."""
    respx.post(f"{BASE_URL}/tickets").mock(
        return_value=httpx.Response(201, json={"id": "8"})
    )

    result = api.api_submit_record("tickets", {"title": "Docking station"})

    assert result["success"] is True
    assert list(tmp_path.rglob("submit-*.json")) == []


@respx.mock
def test_unexpanded_workspace_token_disables_receipts(monkeypatch):
    """A verbatim ${FLEET_WORKSPACE} value (a local agent that registered the
    manifest string unexpanded) is treated as unset, never as a literal path."""
    monkeypatch.setenv("EXAMPLE_API_OUTPUT_DIR", "${FLEET_WORKSPACE}/outputs")
    respx.post(f"{BASE_URL}/tickets").mock(
        return_value=httpx.Response(201, json={"id": "9"})
    )

    result = api.api_submit_record("tickets", {"title": "Monitor"})

    assert result["success"] is True
    assert api._output_dir() is None


def test_receipt_failure_never_breaks_the_tool(monkeypatch, tmp_path):
    """A receipt-write failure is logged, not raised: the API success stands."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("occupied")
    monkeypatch.setenv("EXAMPLE_API_OUTPUT_DIR", str(blocker))
    # Call the writer directly with a canned success result.
    api._write_receipt("tickets", {"a": 1}, {"success": True, "data": None})


@respx.mock
def test_custom_base_url(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_BASE_URL", "https://my.api.test/v2/")
    route = respx.get("https://my.api.test/v2/orders").mock(
        return_value=httpx.Response(200, json=[])
    )

    result = api.api_list_records("orders")

    assert result["success"] is True
    assert route.called

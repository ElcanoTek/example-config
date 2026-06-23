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
def test_custom_base_url(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_BASE_URL", "https://my.api.test/v2/")
    route = respx.get("https://my.api.test/v2/orders").mock(
        return_value=httpx.Response(200, json=[])
    )

    result = api.api_list_records("orders")

    assert result["success"] is True
    assert route.called

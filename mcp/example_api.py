#!/usr/bin/env python3
"""
Example REST Connector MCP Server

A CREDENTIAL-GATED, generic REST connector that demonstrates fleet's host-side
credential brokering. It stays DARK until EXAMPLE_API_KEY is present, so a fresh
checkout of this bundle runs clean with no secrets. When fleet runs a delegated
call against this server, it injects the brokered key into the environment for
just that call - the secret never lands in the model's context or the sandbox.

Point EXAMPLE_API_BASE_URL at any JSON REST API and the three tools below become
a thin, typed wrapper over it: list records, read one record, and submit a new
one. The default base URL is a placeholder host; nothing real is contacted until
you supply your own key and URL.

Configuration (read from the environment, never logged):
  EXAMPLE_API_KEY              required; sent as "Authorization: Bearer <key>".
  EXAMPLE_API_BASE_URL         optional; defaults to https://api.example.com/v1.
  EXAMPLE_API_TIMEOUT_SECONDS  optional; request timeout, defaults to 30.

Runs over stdio inside the fleet sandbox.
"""

import logging
import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# Log to stderr - stdout is reserved for the STDIO transport. We never log the
# API key or full request headers.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("example_api")

DEFAULT_BASE_URL = "https://api.example.com/v1"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "fleet-example-connector/1.0"


def _base_url() -> str:
    """Return the configured API base URL with any trailing slash trimmed."""
    return os.environ.get("EXAMPLE_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    """Return the configured request timeout in seconds, falling back safely."""
    raw = os.environ.get("EXAMPLE_API_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "EXAMPLE_API_TIMEOUT_SECONDS is not a number (%r); using default.", raw
        )
        return DEFAULT_TIMEOUT


def _headers() -> dict[str, str]:
    """Build request headers, including the Bearer auth from EXAMPLE_API_KEY.

    The key value is read here and never logged. Returns an empty Authorization
    only if the key is absent (the server is gated off in that case anyway).
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    api_key = os.environ.get("EXAMPLE_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured error result the model can read and recover from."""
    result: dict[str, Any] = {"success": False, "error": message}
    result.update(extra)
    return result


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Make one httpx request and return parsed JSON or a structured error.

    Centralizes URL building, headers, timeout, and the error handling that all
    three tools share. Never raises - every failure mode is returned as a
    {"success": False, "error": ...} object.
    """
    if not os.environ.get("EXAMPLE_API_KEY"):
        return _error(
            "EXAMPLE_API_KEY is not set. This connector is gated off until a key "
            "is brokered in by fleet."
        )

    url = f"{_base_url()}/{path.lstrip('/')}"
    try:
        with httpx.Client(timeout=_timeout()) as client:
            response = client.request(
                method, url, headers=_headers(), **kwargs
            )
            response.raise_for_status()
            if not response.content:
                return {"success": True, "data": None}
            return {"success": True, "data": response.json()}
    except httpx.HTTPStatusError as exc:
        return _error(
            f"HTTP {exc.response.status_code} from {method} {path}",
            status_code=exc.response.status_code,
            body=exc.response.text[:500],
        )
    except httpx.HTTPError as exc:
        return _error(f"Request to {method} {path} failed: {exc}")
    except ValueError as exc:
        # response.json() raised - body was not valid JSON.
        return _error(f"Response from {method} {path} was not valid JSON: {exc}")


@mcp.tool()
def api_list_records(resource: str, limit: int = 20) -> dict[str, Any]:
    """List records of a given resource type from the configured REST API.

    Performs GET {base_url}/{resource}?limit={limit}. Use this to browse a
    collection - for example resource="tickets" or resource="orders". The exact
    resource names depend on the API you point EXAMPLE_API_BASE_URL at.

    Args:
        resource: The collection path segment, e.g. "tickets".
        limit: Maximum number of records to request (default 20).

    Returns {"success": True, "data": <parsed JSON>} on success, or
    {"success": False, "error": ...} on any failure (including a missing key).
    """
    return _request("GET", resource, params={"limit": limit})


@mcp.tool()
def api_get_record(resource: str, record_id: str) -> dict[str, Any]:
    """Fetch a single record by id from the configured REST API.

    Performs GET {base_url}/{resource}/{record_id}. Use this after
    api_list_records to read the full detail of one item.

    Args:
        resource: The collection path segment, e.g. "tickets".
        record_id: The id of the record to fetch.

    Returns {"success": True, "data": <parsed JSON>} on success, or
    {"success": False, "error": ...} on any failure (including a missing key).
    """
    return _request("GET", f"{resource}/{record_id}")


@mcp.tool()
def api_submit_record(resource: str, payload: dict) -> dict[str, Any]:
    """Create a new record by POSTing a payload to the configured REST API.

    Performs POST {base_url}/{resource} with the payload as a JSON body. This is
    a WRITE: the fleet manifest marks tools whose name ends in "submit_record"
    as critical, so this call is held for an audit confirmation before it runs.

    Args:
        resource: The collection path segment to create under, e.g. "tickets".
        payload: A JSON-serializable object describing the new record.

    Returns {"success": True, "data": <parsed JSON>} on success, or
    {"success": False, "error": ...} on any failure (including a missing key).
    """
    return _request("POST", resource, json=payload)


if __name__ == "__main__":
    logger.info("Starting Example REST Connector MCP Server")
    if not os.environ.get("EXAMPLE_API_KEY"):
        logger.warning(
            "EXAMPLE_API_KEY is not set. The connector is gated off; tools will "
            "return a structured error until a key is provided."
        )
    else:
        logger.info("Connector configured against %s", _base_url())
    try:
        mcp.run(transport="stdio")
    except Exception as exc:  # noqa: BLE001 - top-level guard for clean exit
        logger.error("Failed to start server: %s", exc)
        sys.exit(1)

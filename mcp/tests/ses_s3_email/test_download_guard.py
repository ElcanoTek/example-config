"""download_link_attachment SSRF guard (ses_s3_email side).

Email-body URLs are attacker-controlled for anyone who can email the mailbox.
The tool wires utils/url_guard.py in as an httpx request event hook, so every
hop — initial URL, followed redirects, and the HTML-chase target — is vetted
against non-public address space before a request is sent. Hop-by-hop redirect
coverage is pinned in mcp/tests/utils/test_url_guard.py; these tests pin the
tool-level error contract: a blocked URL is a structured, non-retryable error,
consults no archive fallback, and writes nothing to disk.
"""

import os
import sys

import pytest

# Add the mcp directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ses_s3_email import download_link_attachment


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8080/admin",  # loopback
        "http://10.0.0.5/internal-report.csv",  # RFC1918
        "http://[::1]/x",  # v6 loopback
    ],
)
async def test_internal_hosts_are_refused(tmp_path, url):
    result = await download_link_attachment(url=url, output_dir=str(tmp_path))
    assert result["status"] == "error"
    assert "SECURITY" in result["error"]
    assert result["retryable"] is False
    assert list(tmp_path.iterdir()) == []  # nothing written, no archive fallback


async def test_non_http_scheme_is_refused(tmp_path):
    result = await download_link_attachment(url="file:///etc/passwd", output_dir=str(tmp_path))
    assert result["status"] == "error"
    assert "scheme" in result["error"].lower()

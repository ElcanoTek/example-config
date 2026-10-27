"""download_link_attachment is bounded: a byte cap and one deadline for the whole fetch.

Links in an email body are attacker-controllable. The body used to be read
whole into memory (no size limit), and httpx's timeout applies per read, so a
server could stream an unbounded file or drip bytes forever.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
import ses_s3_email

URL = "https://reports.example.com/report.csv"


@pytest.fixture
def dest(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(ses_s3_email, "ATTACHMENT_DOWNLOAD_DIR", str(out))
    monkeypatch.setattr(ses_s3_email.tempfile, "gettempdir", lambda: str(tmp_path))

    async def public(_request):  # no DNS in tests; the SSRF guard has its own suite
        return None

    monkeypatch.setattr(ses_s3_email.url_guard, "request_hook", public)

    async def no_archive(*_a, **_k):
        return None

    monkeypatch.setattr(ses_s3_email, "_fetch_archived_link", no_archive)
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", 1000)
    return out


@respx.mock
async def test_a_small_file_downloads(dest):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"}))
    result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    assert result["status"] == "success", result
    assert result["size_bytes"] == 8


@respx.mock
async def test_a_declared_oversized_file_is_refused(dest):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 5000))
    result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    assert result["status"] == "error" and result["retryable"] is False
    assert "cap" in result["error"]
    assert list(dest.iterdir()) == []


@respx.mock
async def test_an_undeclared_oversized_stream_is_refused(dest):
    async def body():
        for _ in range(10):
            yield b"x" * 500

    respx.get(URL).mock(return_value=httpx.Response(200, content=body()))
    result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    assert result["status"] == "error" and "cap" in result["error"]
    assert list(dest.iterdir()) == []


@respx.mock
async def test_the_whole_fetch_has_one_deadline(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_SECONDS", 1)

    async def drip():
        while True:
            await asyncio.sleep(0.2)
            yield b"x"

    respx.get(URL).mock(return_value=httpx.Response(200, content=drip()))
    result = await asyncio.wait_for(
        ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest), timeout_seconds=10_000), timeout=10
    )
    assert result["status"] == "error" and "timed out after 1 seconds" in result["error"]

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
ORIGINAL_ARCHIVE = ses_s3_email._fetch_archived_link


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


@respx.mock
async def test_the_html_chase_shares_the_one_deadline(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_SECONDS", 1)
    tracker = "https://click.example.com/ls/click?upn=abc"

    async def slow_html():
        await asyncio.sleep(0.8)
        yield b'<html><a href="https://reports.example.com/report.csv">Download</a></html>'

    async def drip():
        while True:
            await asyncio.sleep(0.2)
            yield b"x"

    respx.get(tracker).mock(
        return_value=httpx.Response(200, content=slow_html(), headers={"content-type": "text/html"})
    )
    respx.get(URL).mock(return_value=httpx.Response(200, content=drip()))
    started = asyncio.get_running_loop().time()
    result = await ses_s3_email.download_link_attachment(url=tracker, output_dir=str(dest), timeout_seconds=1)
    assert result["status"] == "error"
    assert asyncio.get_running_loop().time() - started < 1.8  # not 1 s for each fetch


async def test_the_archived_fallback_has_the_same_byte_cap(dest, monkeypatch):
    class Body:
        async def read(self, amt=None):
            return b"x" * (amt or 5000)

    class S3:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def list_objects_v2(self, **_kwargs):
            return {"Contents": [{"Key": "emails/attachments/by-url/h/report.csv"}]}

        async def get_object(self, **_kwargs):
            return {"Body": Body(), "Metadata": {}}

    class Session:
        def client(self, *_a, **_k):
            return S3()

    monkeypatch.setattr(ses_s3_email, "session", Session())
    monkeypatch.setattr(ses_s3_email, "_fetch_archived_link", ORIGINAL_ARCHIVE)
    result = await ses_s3_email._fetch_archived_link(URL, dest)
    assert result["status"] == "error" and "cap" in result["error"]
    assert list(dest.iterdir()) == []


@respx.mock
async def test_redirect_bodies_are_never_read_and_hops_are_bounded(dest):
    hop = "https://reports.example.com/r"

    async def endless():
        while True:
            yield b"x" * 4096  # a 3xx body is never read, so this would never end

    respx.get(hop).mock(return_value=httpx.Response(302, headers={"location": URL}, content=endless()))
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"a,b\n"))
    result = await asyncio.wait_for(ses_s3_email.download_link_attachment(url=hop, output_dir=str(dest)), timeout=10)
    assert result["status"] == "success", result
    assert result["redirect_chain"] == [hop, URL]

    loop = "https://reports.example.com/loop"
    respx.get(loop).mock(return_value=httpx.Response(302, headers={"location": loop}))
    result = await ses_s3_email.download_link_attachment(url=loop, output_dir=str(dest))
    assert result["status"] == "error"


class _ChunkedBody:
    """aiobotocore's StreamReader: read(n) may return fewer bytes than asked."""

    def __init__(self, data: bytes, step: int = 3) -> None:
        self.data, self.step = data, step

    async def read(self, _amt=None):
        chunk, self.data = self.data[: self.step], self.data[self.step :]
        return chunk


def _archive_session(body, *, length=None):
    class S3:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def list_objects_v2(self, **_kwargs):
            return {"Contents": [{"Key": "emails/attachments/by-url/h/report.csv"}]}

        async def get_object(self, **_kwargs):
            response = {"Body": body, "Metadata": {}}
            if length is not None:
                response["ContentLength"] = length
            return response

    class Session:
        def client(self, *_a, **_k):
            return S3()

    return Session()


async def test_the_archive_is_read_to_eof_not_one_short_read(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "session", _archive_session(_ChunkedBody(b"a,b\n1,2\n"), length=8))
    result = await ORIGINAL_ARCHIVE(URL, dest)
    assert result["status"] == "success", result
    assert result["size_bytes"] == 8
    # A body that ends before its ContentLength is a truncation, never a success.
    monkeypatch.setattr(ses_s3_email, "session", _archive_session(_ChunkedBody(b"a,b\n"), length=8))
    result = await ORIGINAL_ARCHIVE(URL, dest)
    assert result["status"] == "error" and "truncated" in result["error"]


@respx.mock
async def test_a_stalled_live_fetch_leaves_the_archive_time(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_SECONDS", 2)
    called: list[float] = []

    async def archive(*_a, **_k):
        called.append(asyncio.get_running_loop().time())
        return {"status": "success", "source": "s3_archive", "saved_to": "x"}

    monkeypatch.setattr(ses_s3_email, "_fetch_archived_link", archive)

    async def stall():
        await asyncio.sleep(60)
        yield b"x"

    respx.get(URL).mock(return_value=httpx.Response(200, content=stall()))
    result = await asyncio.wait_for(
        ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest), timeout_seconds=2), timeout=10
    )
    assert called, "the archive fallback never ran"
    assert result["source"] == "s3_archive"


async def test_a_batch_is_bounded_in_count_and_shares_one_byte_budget(dest, monkeypatch):
    links = [{"url": f"https://reports.example.com/{i}.csv"} for i in range(30)]

    async def extract(*_a, **_k):
        return {"status": "success", "links": links, "subject": "s", "total_links_found": len(links)}

    running = {"now": 0, "max": 0}

    async def one(url, output_dir=None, timeout_seconds=120):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        try:
            ses_s3_email._charge_batch_budget(400)
        except ses_s3_email.LinkDownloadTooLarge as exc:
            return {"status": "error", "error": str(exc)}
        finally:
            running["now"] -= 1
        return {"status": "success", "saved_to": "x", "size_bytes": 400, "content_type": "text/csv"}

    monkeypatch.setattr(ses_s3_email, "extract_download_links", extract)
    monkeypatch.setattr(ses_s3_email, "download_link_attachment", one)
    monkeypatch.setattr(ses_s3_email, "LINK_BATCH_MAX_COUNT", 20)
    monkeypatch.setattr(ses_s3_email, "LINK_BATCH_MAX_BYTES", 2000)
    result = await ses_s3_email.download_all_link_attachments("emails/k", output_dir=str(dest))
    assert running["max"] <= ses_s3_email.LINK_BATCH_CONCURRENCY
    assert result["downloaded_count"] == 5  # 5 x 400 bytes fit the 2000-byte budget
    assert result["error_count"] == 25  # 15 over budget + 10 over the link count
    assert sum("at most 20 links" in e["error"] for e in result["errors"]) == 10


@respx.mock
async def test_the_archive_gets_only_its_slice_even_when_live_fails_fast(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_SECONDS", 40)
    seen: list = []

    async def archive(url, download_dir, filename=None, *, read_timeout=None):
        seen.append(read_timeout)
        return None

    monkeypatch.setattr(ses_s3_email, "_fetch_archived_link", archive)
    respx.get(URL).mock(return_value=httpx.Response(500))
    await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest), timeout_seconds=40)
    assert seen and seen[0] <= 10.0 + 0.01  # min(30 s, 25% of 40 s), not the ~40 s left


async def test_an_archive_write_is_never_left_running_past_the_deadline(dest, monkeypatch):
    """Only the S3 read is under the deadline; the local write is awaited to completion."""
    monkeypatch.setattr(ses_s3_email, "session", _archive_session(_ChunkedBody(b"a,b\n1,2\n"), length=8))
    finished: list = []
    real_write = ses_s3_email.write_reserved

    async def slow_write(reserved, data):
        await asyncio.sleep(0.3)
        await real_write(reserved, data)
        finished.append(True)

    monkeypatch.setattr(ses_s3_email, "write_reserved", slow_write)
    result = await ORIGINAL_ARCHIVE(URL, dest, read_timeout=0.2)
    assert finished == [True]  # returned only after the write completed
    assert result["status"] == "success"

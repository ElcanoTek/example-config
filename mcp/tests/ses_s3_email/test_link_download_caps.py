"""download_link_attachment is bounded: a byte cap and one deadline for the whole fetch.

Links in an email body are attacker-controllable. The body used to be read
whole into memory (no size limit), and httpx's timeout applies per read, so a
server could stream an unbounded file or drip bytes forever.
"""

from __future__ import annotations

import asyncio
import os

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
    assert result["error_count"] == 15  # over budget; links over the count are summarized
    assert result["skipped_count"] == 10 and "at most 20 links" in result["skipped_reason"]
    assert result["skipped_sample"] == [f"https://reports.example.com/{i}.csv" for i in range(20, 25)]


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


@respx.mock
async def test_a_cap_sized_body_is_held_once_not_joined_from_chunks(dest, monkeypatch):
    """Peak memory stays near one copy of the body (spooled, read back once)."""
    import tracemalloc

    size = 8 * 1024 * 1024
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", size + 1)

    async def body():
        block = b"x" * (256 * 1024)
        for _ in range(size // len(block)):
            yield block

    respx.get(URL).mock(return_value=httpx.Response(200, content=body()))
    tracemalloc.start()
    try:
        result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["status"] == "success" and result["size_bytes"] == size
    assert peak < size * 1.6, peak  # a chunk list plus its join would be ~2x


async def test_a_batch_has_one_outer_deadline(dest, monkeypatch):
    links = [{"url": f"https://reports.example.com/{i}.csv"} for i in range(6)]

    async def extract(*_a, **_k):
        return {"status": "success", "links": links, "subject": "s", "total_links_found": len(links)}

    timeouts: list[int] = []

    async def slow(url, output_dir=None, timeout_seconds=120):
        timeouts.append(timeout_seconds)
        await asyncio.sleep(min(timeout_seconds, 1))
        return {"status": "error", "error": "timed out"}

    monkeypatch.setattr(ses_s3_email, "extract_download_links", extract)
    monkeypatch.setattr(ses_s3_email, "download_link_attachment", slow)
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_SECONDS", 2)
    started = asyncio.get_running_loop().time()
    result = await ses_s3_email.download_all_link_attachments("emails/k", output_dir=str(dest), timeout_seconds=120)
    assert asyncio.get_running_loop().time() - started < 3.5  # not 6 links x 120 s
    assert all(t <= 2 for t in timeouts)
    assert any("Not started" in e["error"] for e in result["errors"])


async def test_the_batch_deadline_starts_before_link_extraction(dest, monkeypatch):
    links = [{"url": f"https://reports.example.com/{i}.csv"} for i in range(3)]

    async def slow_extract(*_a, **_k):
        await asyncio.sleep(1.5)
        return {"status": "success", "links": links, "subject": "s", "total_links_found": len(links)}

    called: list[str] = []

    async def one(url, output_dir=None, timeout_seconds=120):
        called.append(url)
        return {"status": "error", "error": "x"}

    monkeypatch.setattr(ses_s3_email, "extract_download_links", slow_extract)
    monkeypatch.setattr(ses_s3_email, "download_link_attachment", one)
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_SECONDS", 2)
    result = await ses_s3_email.download_all_link_attachments("emails/k", output_dir=str(dest))
    assert called == []  # under 1 s left after extraction: nothing starts
    assert all("Not started" in e["error"] for e in result["errors"])


async def test_link_extraction_itself_is_bounded_by_the_batch_deadline(dest, monkeypatch):
    async def hung_extract(*_a, **_k):
        await asyncio.sleep(30)

    monkeypatch.setattr(ses_s3_email, "extract_download_links", hung_extract)
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_SECONDS", 1)
    started = asyncio.get_running_loop().time()
    result = await ses_s3_email.download_all_link_attachments("emails/k", output_dir=str(dest))
    assert asyncio.get_running_loop().time() - started < 2
    assert result["status"] == "error" and "deadline" in result["error"]


@respx.mock
async def test_no_write_starts_after_the_batch_deadline(dest):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"}))
    token = ses_s3_email._BATCH_DEADLINE.set(asyncio.get_running_loop().time() - 1)
    try:
        result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    finally:
        ses_s3_email._BATCH_DEADLINE.reset(token)
    assert result["status"] == "error" and "deadline" in result["error"], result
    assert list(dest.iterdir()) == []  # not even an empty reserved file


async def test_no_archive_write_starts_after_the_batch_deadline(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "session", _archive_session(_ChunkedBody(b"a,b\n1,2\n"), length=8))
    token = ses_s3_email._BATCH_DEADLINE.set(asyncio.get_running_loop().time() - 1)
    try:
        result = await ORIGINAL_ARCHIVE(URL, dest, read_timeout=5)
    finally:
        ses_s3_email._BATCH_DEADLINE.reset(token)
    assert result is None
    assert list(dest.iterdir()) == []


@respx.mock
async def test_a_write_already_running_at_the_batch_deadline_is_awaited(dest, monkeypatch):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"}))
    real_write = ses_s3_email.write_reserved

    async def slow_write(reserved, data):
        # The deadline passes while this write is already running.
        ses_s3_email._BATCH_DEADLINE.set(asyncio.get_running_loop().time() - 1)
        await asyncio.sleep(0.1)
        await real_write(reserved, data)

    monkeypatch.setattr(ses_s3_email, "write_reserved", slow_write)
    token = ses_s3_email._BATCH_DEADLINE.set(asyncio.get_running_loop().time() + 60)
    try:
        result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    finally:
        ses_s3_email._BATCH_DEADLINE.reset(token)
    assert result["status"] == "success", result
    with open(result["saved_to"], "rb") as fh:
        assert fh.read() == b"a,b\n1,2\n"


def _count_charged(monkeypatch) -> list[int]:
    charged: list[int] = []
    real = ses_s3_email._charge_batch_budget

    def counting(n):
        charged.append(n)
        real(n)

    monkeypatch.setattr(ses_s3_email, "_charge_batch_budget", counting)
    return charged


@respx.mock
async def test_live_and_chase_share_one_byte_cap(dest, monkeypatch):
    """The HTML page and the chased file together may not exceed EMAIL_LINK_MAX_BYTES (1000 here)."""
    charged = _count_charged(monkeypatch)
    tracker = "https://click.example.com/ls/click?upn=abc"
    page = b'<html><a href="https://reports.example.com/report.csv">Download</a></html>'
    page += b" " * (600 - len(page))

    async def body():
        for _ in range(6):
            yield b"x" * 100

    respx.get(tracker).mock(return_value=httpx.Response(200, content=page, headers={"content-type": "text/html"}))
    respx.get(URL).mock(return_value=httpx.Response(200, content=body()))
    result = await ses_s3_email.download_link_attachment(url=tracker, output_dir=str(dest))
    assert result["status"] == "error" and "cap" in result["error"], result
    assert sum(charged) <= 1000 + 100  # refused on the chunk that crossed the shared cap
    assert list(dest.iterdir()) == []


@respx.mock
async def test_live_and_archive_share_one_byte_cap(dest, monkeypatch):
    charged = _count_charged(monkeypatch)
    monkeypatch.setattr(ses_s3_email, "_fetch_archived_link", ORIGINAL_ARCHIVE)
    monkeypatch.setattr(ses_s3_email, "session", _archive_session(_ChunkedBody(b"y" * 600, step=100)))
    respx.get(URL).mock(return_value=httpx.Response(500, content=b"e" * 600))
    result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    assert result["status"] == "error", result
    assert sum(charged) <= 1000 + 100
    # The archived copy was never saved past the shared cap.
    assert not any(p.read_bytes().startswith(b"y") for p in dest.iterdir())


@respx.mock
async def test_a_declared_archive_larger_than_what_is_left_is_refused_up_front(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "_fetch_archived_link", ORIGINAL_ARCHIVE)
    reads: list[int] = []

    class Body(_ChunkedBody):
        async def read(self, _amt=None):
            reads.append(1)
            return await super().read(_amt)

    monkeypatch.setattr(ses_s3_email, "session", _archive_session(Body(b"y" * 600, step=100), length=600))
    respx.get(URL).mock(return_value=httpx.Response(500, content=b"e" * 600))
    result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    assert result["status"] == "error"
    assert reads == []  # 600 declared > 400 left: not read at all


@respx.mock
async def test_each_call_gets_a_fresh_budget(dest):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"z" * 700, headers={"content-type": "text/csv"}))
    for _ in range(2):
        result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
        assert result["status"] == "success", result
    assert ses_s3_email._CALL_BUDGET.get() is None


def test_a_chunk_crossing_the_call_cap_is_still_charged_to_the_batch():
    batch = [10_000]
    call = [100]
    b_token = ses_s3_email._BATCH_BUDGET.set(batch)
    c_token = ses_s3_email._CALL_BUDGET.set(call)
    try:
        with pytest.raises(ses_s3_email.LinkDownloadTooLarge):
            ses_s3_email._charge_batch_budget(150)
    finally:
        ses_s3_email._CALL_BUDGET.reset(c_token)
        ses_s3_email._BATCH_BUDGET.reset(b_token)
    assert call == [-50]
    assert batch == [10_000 - 150]  # the received bytes count against the batch too


@respx.mock
async def test_only_the_html_scan_prefix_is_searched_for_the_chase_link(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_HTML_SCAN_MAX_BYTES", 200)
    tracker = "https://click.example.com/ls/click?upn=abc"
    # The link sits past the scanned prefix: not chased, the page is saved.
    page = b"<html>" + b" " * 400 + b'<a href="https://reports.example.com/report.csv">Download</a></html>'
    respx.get(tracker).mock(return_value=httpx.Response(200, content=page, headers={"content-type": "text/html"}))
    chased = respx.get(URL).mock(return_value=httpx.Response(200, content=b"a,b\n"))
    result = await ses_s3_email.download_link_attachment(url=tracker, output_dir=str(dest))
    assert not chased.called
    assert "chased_to" not in result and result["size_bytes"] == len(page)


@respx.mock
async def test_a_small_html_interstitial_is_still_chased(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_HTML_SCAN_MAX_BYTES", 2000)
    tracker = "https://click.example.com/ls/click?upn=abc"
    page = b'<html><a href="https://reports.example.com/report.csv">Download</a></html>'
    respx.get(tracker).mock(return_value=httpx.Response(200, content=page, headers={"content-type": "text/html"}))
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"a,b\n", headers={"content-type": "text/csv"}))
    result = await ses_s3_email.download_link_attachment(url=tracker, output_dir=str(dest))
    assert result["status"] == "success" and result.get("chased_to") == URL, result


async def test_skipped_links_are_a_count_and_a_small_redacted_sample(dest, monkeypatch):
    links = [{"url": f"https://reports.example.com/{i}.csv?token=secret{i}"} for i in range(5000)]

    async def extract(*_a, **_k):
        return {"status": "success", "links": links, "subject": "s", "total_links_found": len(links)}

    async def one(url, output_dir=None, timeout_seconds=120):
        return {"status": "error", "error": "x"}

    monkeypatch.setattr(ses_s3_email, "extract_download_links", extract)
    monkeypatch.setattr(ses_s3_email, "download_link_attachment", one)
    monkeypatch.setattr(ses_s3_email, "LINK_BATCH_MAX_COUNT", 3)
    result = await ses_s3_email.download_all_link_attachments("emails/k", output_dir=str(dest))
    assert result["skipped_count"] == 4997
    assert len(result["skipped_sample"]) == 5
    assert all(u.split(" [url-sha256:")[0].endswith("?***") for u in result["skipped_sample"])
    assert len(result["errors"]) == 3  # only the attempted links
    assert "secret" not in str(result["skipped_sample"])


@respx.mock
async def test_a_15_mb_html_page_with_its_link_near_the_top_is_chased(dest, monkeypatch):
    """The first 10 MB are scanned: a large click-tracker page still chases."""
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", 200 * 1024 * 1024)
    assert ses_s3_email.LINK_HTML_SCAN_MAX_BYTES == 10 * 1024 * 1024
    tracker = "https://click.example.com/ls/click?upn=abc"
    head = b'<html><a href="https://reports.example.com/report.csv">Download</a>'
    page = head + b"x" * (15 * 1024 * 1024 - len(head))
    respx.get(tracker).mock(return_value=httpx.Response(200, content=page, headers={"content-type": "text/html"}))
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"a,b\n", headers={"content-type": "text/csv"}))
    result = await ses_s3_email.download_link_attachment(url=tracker, output_dir=str(dest))
    assert result["status"] == "success" and result.get("chased_to") == URL, result


@respx.mock
async def test_a_15_mb_html_report_without_a_link_downloads_as_is(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", 200 * 1024 * 1024)
    tracker = "https://click.example.com/ls/click?upn=abc"
    page = b"<html><p>report</p>" + b"x" * (15 * 1024 * 1024)
    respx.get(tracker).mock(return_value=httpx.Response(200, content=page, headers={"content-type": "text/html"}))
    result = await ses_s3_email.download_link_attachment(url=tracker, output_dir=str(dest))
    assert result["status"] == "success", result
    assert result["size_bytes"] == len(page) and "chased_to" not in result
    with open(result["saved_to"], "rb") as fh:
        assert fh.read() == page


@respx.mock
async def test_a_large_download_is_never_materialised_in_memory(dest, monkeypatch):
    import tracemalloc

    size = 16 * 1024 * 1024
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", size + 1)

    async def body():
        block = b"y" * (256 * 1024)
        for _ in range(size // len(block)):
            yield block

    respx.get(URL).mock(return_value=httpx.Response(200, content=body(), headers={"content-type": "text/csv"}))
    tracemalloc.start()
    try:
        result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["status"] == "success" and result["size_bytes"] == size
    assert peak < 4 * 1024 * 1024, peak  # chunks and copy buffers only, never the 16 MB body
    assert os.path.getsize(result["saved_to"]) == size


@respx.mock
async def test_three_concurrent_near_cap_downloads_work(dest, monkeypatch):
    size = 4 * 1024 * 1024
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", size + 10)
    urls = [f"https://reports.example.com/r{i}.csv" for i in range(3)]
    for u in urls:
        respx.get(u).mock(return_value=httpx.Response(200, content=b"z" * size, headers={"content-type": "text/csv"}))
    results = await asyncio.gather(*(ses_s3_email.download_link_attachment(url=u, output_dir=str(dest)) for u in urls))
    assert all(r["status"] == "success" and r["size_bytes"] == size for r in results), results


@pytest.mark.skipif(
    bool(
        __import__("os").environ.get("EMAIL_LINK_BATCH_MAX_BYTES")
        or __import__("os").environ.get("EMAIL_LINK_MAX_BYTES")
    ),
    reason="caps overridden in this environment",
)
def test_default_caps_fit_several_large_reports_per_batch():
    assert ses_s3_email.LINK_DOWNLOAD_MAX_BYTES == 200 * 1024 * 1024
    assert ses_s3_email.LINK_BATCH_MAX_BYTES == 1024 * 1024 * 1024
    token = ses_s3_email._BATCH_BUDGET.set([ses_s3_email.LINK_BATCH_MAX_BYTES])
    try:
        for _ in range(3):  # three 150 MB reports fit one batch
            ses_s3_email._charge_batch_budget(150 * 1024 * 1024)
    finally:
        ses_s3_email._BATCH_BUDGET.reset(token)


async def test_a_cancelled_write_waits_for_the_copy_then_removes_the_partial_file(dest, monkeypatch):
    """The spool is never closed under the running copy thread, and a cancelled
    call leaves no partial file behind."""
    import tempfile
    import threading

    started = threading.Event()
    release = threading.Event()
    real_write_fd = ses_s3_email._write_fd
    copy_saw_closed: list = []

    def slow_write_fd(fd, data):
        started.set()
        release.wait(5)
        copy_saw_closed.append(data.closed)
        real_write_fd(fd, data)

    monkeypatch.setattr(ses_s3_email, "_write_fd", slow_write_fd)
    spool = tempfile.TemporaryFile()  # noqa: SIM115 - closed by call() below, like the wrapper
    spool.write(b"x" * 1024)
    spool.seek(0)
    reserved = ses_s3_email.reserve_output_file(dest, "report.csv", "src")

    async def call():
        try:
            await ses_s3_email.write_reserved(reserved, spool)
        finally:
            spool.close()  # the call wrapper's cleanup

    task = asyncio.create_task(call())
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()  # still waiting for the copy thread
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert copy_saw_closed == [False]  # the spool was open for the whole copy
    assert not reserved.path.exists()  # no partial attachment


@respx.mock
async def test_an_unknown_length_body_crossing_the_file_cap_is_charged_to_the_batch(dest, monkeypatch):
    """No Content-Length: the chunk that crosses the per-file cap still counts."""
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", 1000)

    async def body():
        for _ in range(3):
            yield b"x" * 600  # crosses 1000 on the second chunk

    respx.get(URL).mock(return_value=httpx.Response(200, content=body()))
    batch = [10_000]
    token = ses_s3_email._BATCH_BUDGET.set(batch)
    try:
        result = await ses_s3_email.download_link_attachment(url=URL, output_dir=str(dest))
    finally:
        ses_s3_email._BATCH_BUDGET.reset(token)
    assert result["status"] == "error"
    assert batch == [10_000 - 1200]  # both received chunks charged, including the crossing one


async def test_an_archive_crossing_the_file_cap_is_charged_to_the_batch(dest, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "LINK_DOWNLOAD_MAX_BYTES", 1000)
    monkeypatch.setattr(ses_s3_email, "session", _archive_session(_ChunkedBody(b"y" * 1800, step=600)))
    batch = [10_000]
    token = ses_s3_email._BATCH_BUDGET.set(batch)
    try:
        result = await ORIGINAL_ARCHIVE(URL, dest)
    finally:
        ses_s3_email._BATCH_BUDGET.reset(token)
    assert result["status"] == "error"
    assert batch == [10_000 - 1200]

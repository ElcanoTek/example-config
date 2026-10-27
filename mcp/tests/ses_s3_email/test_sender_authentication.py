"""The inbox selects a vendor's mail by AUTHENTICATED sender, not From text.

``sender_contains`` used to match a substring of the From header, which anyone
can write, so a forged "From: reports@vendor.example" was picked up as that
vendor's report. Now the receiving server's own Authentication-Results (the
topmost one with its authserv-id) must show DMARC pass, or an SPF / DKIM pass
aligned with the From domain. EMAIL_SENDER_AUTH=off is the documented legacy
fallback. Also: URL query strings (presigned signatures) never reach a log
line, and the debug log is private.
"""

from __future__ import annotations

import logging
import os
import stat
from datetime import UTC, datetime

import pytest
import ses_s3_email as inbox

FROM = "Vendor Reports <reports@vendor.example>"


@pytest.mark.parametrize(
    "results",
    [
        [
            "amazonses.com; spf=pass smtp.mailfrom=bounce.vendor.example; dkim=pass header.i=@vendor.example; dmarc=pass header.from=vendor.example"
        ],
        ["amazonses.com; dmarc=pass header.from=vendor.example"],
        ["amazonses.com; spf=fail smtp.mailfrom=x.example; dkim=pass header.d=vendor.example"],
        ["amazonses.com; spf=pass smtp.mailfrom=mail.vendor.example"],
    ],
)
def test_authenticated_from_domain(results):
    assert inbox.authenticated_sender_domain(results, FROM) == "vendor.example"


@pytest.mark.parametrize(
    "results",
    [
        [],  # nothing verified
        ["amazonses.com; spf=pass smtp.mailfrom=attacker.example; dkim=pass header.d=attacker.example; dmarc=fail"],
        ["amazonses.com; spf=fail; dkim=none; dmarc=fail header.from=vendor.example"],
        # A forged header further down is ignored: only the receiver's topmost verdict counts.
        [
            "amazonses.com; dmarc=fail header.from=vendor.example",
            "amazonses.com; dmarc=pass header.from=vendor.example",
        ],
        # Another authserv-id is not our receiver.
        ["mx.attacker.example; dmarc=pass header.from=vendor.example"],
        # Not aligned: a lookalike domain.
        ["amazonses.com; dkim=pass header.d=notvendor.example"],
    ],
)
def test_unauthenticated_from_domain(results):
    assert inbox.authenticated_sender_domain(results, FROM) is None


# Real-shaped SES inbound headers (AWS SES receiving: amazonses.com authserv-id,
# SPF properties as further ";" items, comments in parentheses).
SES_SPF_ONLY = (
    "amazonses.com; spf=pass (spf: domain of mail.vendor.example designates 192.0.2.1 as permitted sender) "
    "client-ip=192.0.2.1; envelope-from=bounce@mail.vendor.example; helo=mail.vendor.example; "
    "dkim=none; dmarc=none header.from=vendor.example;"
)
SES_FULL = (
    "amazonses.com;\r\n spf=pass (spf: domain of vendor.example designates 192.0.2.1 as permitted sender) "
    "client-ip=192.0.2.1; envelope-from=reports@vendor.example; helo=mx.vendor.example;\r\n "
    "dkim=pass header.i=@vendor.example;\r\n dmarc=pass header.from=vendor.example;"
)


def test_real_shaped_ses_headers_authenticate():
    assert inbox.authenticated_sender_domain([SES_SPF_ONLY], FROM) == "vendor.example"
    assert inbox.authenticated_sender_domain([SES_FULL], FROM) == "vendor.example"
    # A comment that looks like a verdict is not one.
    fake = "amazonses.com; spf=fail (dmarc=pass header.from=vendor.example) envelope-from=x@attacker.example;"
    assert inbox.authenticated_sender_domain([fake], FROM) is None


def test_sibling_subdomains_align_by_organizational_domain():
    sibling = "amazonses.com; dkim=pass header.d=mail.vendor.example; dmarc=none"
    assert (
        inbox.authenticated_sender_domain([sibling], "x <reports@reports.vendor.example>") == "reports.vendor.example"
    )
    assert inbox.organizational_domain("a.b.vendor.co.uk") == "vendor.co.uk"
    assert not inbox._aligned("vendor.example", "notvendor.example")
    assert not inbox._aligned("attacker.co.uk", "vendor.co.uk")


def test_httpx_request_lines_never_carry_a_presigned_query(caplog):
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="httpx"):
        _logging.getLogger("httpx").info('HTTP Request: GET %s "HTTP/1.1 200 OK"', "https://b/x?X-Amz-Signature=s")
    assert "X-Amz-Signature=s" not in caplog.text
    assert "https://b/x?X-Amz-Signature=***" in caplog.text


def test_a_display_name_cannot_carry_the_vendor_domain():
    """Authenticated mail from attacker.example named "OpenX openx.com" is not OpenX's."""
    spoof = {"from": '"OpenX openx.com" <reports@attacker.example>', "authenticated_domain": "attacker.example"}
    assert inbox.sender_authenticated(spoof)
    assert not inbox.sender_filter_matches("openx.com", spoof)
    # The authenticated domain must be the From mailbox's own.
    mismatched = {"from": "reports@openx.com", "authenticated_domain": "attacker.example"}
    assert not inbox.sender_filter_matches("openx.com", mismatched)


def test_sender_filter_needs_authentication(monkeypatch):
    authed = {"from": FROM, "authenticated_domain": "vendor.example"}
    forged = {"from": FROM, "authenticated_domain": ""}
    assert inbox.sender_filter_matches("vendor.example", authed)
    assert not inbox.sender_filter_matches("vendor.example", forged)
    assert not inbox.sender_filter_matches("other.example", authed)
    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "off")
    assert inbox.sender_filter_matches("vendor.example", forged)


# -- search_emails end to end over a fake bucket ---------------------------------------


def _raw(auth: str | None) -> bytes:
    lines = []
    if auth:
        lines.append(f"Authentication-Results: {auth}")
    lines += [
        f"From: {FROM}",
        "To: inbox@example.com",
        "Subject: Daily report",
        "Date: Mon, 26 Oct 2026 10:00:00 +0000",
    ]
    return ("\r\n".join(lines) + "\r\n\r\nbody\r\n").encode()


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def read(self) -> bytes:
        return self.data


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_paginator(self, _name):
        objects = self.objects

        class _Paginator:
            def paginate(self, **_kwargs):
                async def pages():
                    yield {
                        "Contents": [
                            {"Key": key, "LastModified": datetime(2026, 10, 26, 10, tzinfo=UTC), "Size": len(data)}
                            for key, data in objects.items()
                        ]
                    }

                return pages()

        return _Paginator()

    async def get_object(self, Key, **_kwargs):  # noqa: N803 - boto3.s names
        return {"Body": _Body(self.objects[Key])}


class _FakeSession:
    def __init__(self, s3: _FakeS3) -> None:
        self.s3 = s3

    def client(self, *_args, **_kwargs):
        return self.s3


@pytest.mark.asyncio
async def test_search_emails_skips_a_forged_vendor_sender(monkeypatch):
    objects = {
        "emails/genuine": _raw(
            "amazonses.com; spf=pass smtp.mailfrom=vendor.example; dmarc=pass header.from=vendor.example"
        ),
        "emails/forged": _raw(
            "amazonses.com; spf=fail smtp.mailfrom=attacker.example; dmarc=fail header.from=vendor.example"
        ),
        "emails/no-auth": _raw(None),
    }
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3(objects)))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")

    result = await inbox.search_emails(sender_contains="vendor.example", date_from="2026-10-26", date_to="2026-10-26")

    assert result["status"] == "success", result
    assert [e["s3_key"] for e in result["emails"]] == ["emails/genuine"]
    codes = {w["code"] for w in result["search_warnings"]}
    assert "unauthenticated_sender_skipped" in codes

    # A BROAD search (no sender_contains) leaves out unauthenticated mail too.
    broad = await inbox.search_emails(date_from="2026-10-26", date_to="2026-10-26")
    assert [e["s3_key"] for e in broad["emails"]] == ["emails/genuine"]
    assert broad["emails"][0]["sender_authenticated"] is True
    assert "unauthenticated_sender_skipped" in {w["code"] for w in broad["search_warnings"]}

    fuzzy = await inbox.search_emails_fuzzy(keywords=["report"], date_from="2026-10-26", date_to="2026-10-26")
    assert [e["s3_key"] for e in fuzzy["emails"]] == ["emails/genuine"], fuzzy

    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "off")
    legacy = await inbox.search_emails(sender_contains="vendor.example", date_from="2026-10-26", date_to="2026-10-26")
    assert len(legacy["emails"]) == 3
    assert "unauthenticated_sender_match" in {w["code"] for w in legacy["search_warnings"]}


# -- logs -----------------------------------------------------------------------------


def test_url_query_strings_are_redacted_from_logs(caplog):
    url = "https://bucket.s3.amazonaws.com/report.csv?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIAEXAMPLE"
    with caplog.at_level(logging.INFO, logger=inbox.logger.name):
        inbox.logger.info("downloading %s", url)
    assert "deadbeef" not in caplog.text and "AKIAEXAMPLE" not in caplog.text
    assert "https://bucket.s3.amazonaws.com/report.csv?X-Amz-Signature=***&X-Amz-Credential=***" in caplog.text


def test_default_debug_log_is_private(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.delenv("EMAIL_DEBUG_LOG_FILE", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    path = inbox._debug_log_path()
    assert path is not None and path.startswith(str(tmp_path))
    inbox._open_private_log(path).close()
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_debug_log_refuses_a_planted_symlink_dir(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.delenv("EMAIL_DEBUG_LOG_FILE", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "elsewhere"
    target.mkdir()
    uid = os.getuid()
    (tmp_path / f"ses_s3_email-{uid}").symlink_to(target, target_is_directory=True)
    assert inbox._debug_log_path() is None


def test_the_debug_log_stream_is_the_verified_descriptor(tmp_path):
    """Writes go to the descriptor that was checked, never to a path reopened by name."""
    path = tmp_path / "debug.log"
    stream = inbox._open_private_log(str(path))
    path.rename(tmp_path / "kept.log")
    path.symlink_to(tmp_path / "elsewhere.log")
    stream.write("line\n")
    stream.close()
    assert (tmp_path / "kept.log").read_text() == "line\n"
    assert not (tmp_path / "elsewhere.log").exists()
    with pytest.raises(OSError):
        inbox._open_private_log(str(path))  # a symlink at open time is refused


def test_operator_trusted_legacy_prefix_hook(monkeypatch):
    """Off by default; when set, only keys under the prefix count as a pre-authenticated archive."""
    unauth = {"from": FROM, "authenticated_domain": ""}
    assert not inbox.message_trusted("emails/legacy/mailbux-1", unauth)
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/legacy/",))
    assert inbox.message_trusted("emails/legacy/mailbux-1", unauth)
    assert inbox.sender_filter_matches("vendor.example", unauth, "emails/legacy/mailbux-1")
    assert not inbox.message_trusted("emails/2026/10/26/abc", unauth)

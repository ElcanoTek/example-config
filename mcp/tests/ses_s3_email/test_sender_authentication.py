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
    assert "https://bucket.s3.amazonaws.com/report.csv?<redacted>" in caplog.text


def test_default_debug_log_is_private(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.delenv("EMAIL_DEBUG_LOG_FILE", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    path = inbox._debug_log_path()
    assert path is not None and path.startswith(str(tmp_path))
    inbox._open_private_log(path)
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

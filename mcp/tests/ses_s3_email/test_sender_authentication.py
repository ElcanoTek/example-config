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

_URL_HASH_RE = __import__("re").compile(r" \[url-id:[0-9a-f]{12}\]")


def _redacted(text: str) -> str:
    """redact_url_queries without the per-URL hash tags (tested separately)."""
    return _URL_HASH_RE.sub("", inbox.redact_url_queries(text))


@pytest.mark.parametrize(
    "results",
    [
        [
            "amazonses.com; spf=pass smtp.mailfrom=bounce.vendor.example; dkim=pass header.i=@vendor.example; dmarc=pass header.from=vendor.example"
        ],
        ["amazonses.com; dmarc=pass header.from=vendor.example"],
        ["amazonses.com; spf=fail smtp.mailfrom=x.example; dkim=pass header.d=vendor.example"],
        ["amazonses.com; spf=pass smtp.mailfrom=vendor.example"],
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
        # Not aligned without dmarc=pass: a subdomain of the From domain (no public-suffix list).
        ["amazonses.com; spf=pass smtp.mailfrom=mail.vendor.example"],
        ["amazonses.com; dkim=pass header.d=mail.vendor.example; dmarc=none header.from=vendor.example"],
    ],
)
def test_unauthenticated_from_domain(results):
    assert inbox.authenticated_sender_domain(results, FROM) is None


# Real-shaped SES inbound headers (AWS SES receiving: amazonses.com authserv-id,
# SPF properties as further ";" items, comments in parentheses).
SES_SPF_ONLY = (
    "amazonses.com; spf=pass (spf: domain of vendor.example designates 192.0.2.1 as permitted sender) "
    "client-ip=192.0.2.1; envelope-from=bounce@vendor.example; helo=mail.vendor.example; "
    "dkim=none; dmarc=none header.from=vendor.example;"
)
SES_SPF_ONLY_SUBDOMAIN = SES_SPF_ONLY.replace("bounce@vendor.example", "bounce@mail.vendor.example").replace(
    "domain of vendor.example", "domain of mail.vendor.example"
)
SES_FULL = (
    "amazonses.com;\r\n spf=pass (spf: domain of vendor.example designates 192.0.2.1 as permitted sender) "
    "client-ip=192.0.2.1; envelope-from=reports@vendor.example; helo=mx.vendor.example;\r\n "
    "dkim=pass header.i=@vendor.example;\r\n dmarc=pass header.from=vendor.example;"
)


def test_real_shaped_ses_headers_authenticate():
    assert inbox.authenticated_sender_domain([SES_SPF_ONLY], FROM) == "vendor.example"
    assert inbox.authenticated_sender_domain([SES_FULL], FROM) == "vendor.example"
    # SPF for a subdomain envelope-from with dmarc=none is not exact alignment.
    assert inbox.authenticated_sender_domain([SES_SPF_ONLY_SUBDOMAIN], FROM) is None
    # A comment that looks like a verdict is not one.
    fake = "amazonses.com; spf=fail (dmarc=pass header.from=vendor.example) envelope-from=x@attacker.example;"
    assert inbox.authenticated_sender_domain([fake], FROM) is None


def test_spf_dkim_alignment_is_strict_and_siblings_need_dmarc():
    sibling_dkim_only = "amazonses.com; dkim=pass header.d=mail.vendor.example; dmarc=none"
    reports = "x <reports@reports.vendor.example>"
    # No guessed organizational domain: siblings do not align on DKIM/SPF alone...
    assert inbox.authenticated_sender_domain([sibling_dkim_only], reports) is None
    # ...only through the receiver's own dmarc=pass (it has the real public-suffix list).
    assert (
        inbox.authenticated_sender_domain(
            ["amazonses.com; dkim=pass header.d=mail.vendor.example; dmarc=pass header.from=reports.vendor.example"],
            reports,
        )
        == "reports.vendor.example"
    )
    # The SPF/DKIM fallback is exact-domain only: neither a subdomain nor a parent
    # aligns on its own (either could be a public / shared-hosting suffix tenant).
    assert inbox._aligned("vendor.example", "vendor.example")
    assert not inbox._aligned("bounce.vendor.example", "vendor.example")
    assert not inbox._aligned("vendor.example", "reports.vendor.example")
    # Never across a public suffix, a lookalike, or a sibling.
    assert not inbox._aligned("evil.co.id", "vendor.co.id")
    assert not inbox._aligned("vendor.example", "notvendor.example")
    assert not inbox._aligned("mail.vendor.example", "reports.vendor.example")
    assert (
        inbox.authenticated_sender_domain(["amazonses.com; dkim=pass header.d=evil.co.id"], "r <r@vendor.co.id>")
        is None
    )


def test_sender_filter_is_a_domain_or_a_full_mailbox():
    def headers(addr):
        domain = addr.rpartition("@")[2]
        return {"from": f"Reports <{addr}>", "authenticated_domain": domain}

    match = inbox.sender_filter_matches
    assert match("ssp.example", headers("reports@ssp.example"))
    assert match("ssp.example", headers("do_not_reply.reporting@mail.ssp.example"))
    assert match("@ssp.example", headers("reports@ssp.example"))
    assert match("reports@ssp.example", headers("Reports@SSP.example"))
    assert match("exchange.tenants.example,exchange.example", headers("reporting@exchange.tenants.example"))
    assert not match("ssp.example", headers("reports@notssp.example"))
    assert not match("ssp.example", headers('"ssp.example"@attacker.example'))
    assert not match("reports@ssp.example", headers("other@ssp.example"))
    assert not match("ssp", headers("reports@ssp.example"))  # a bare name is not a domain


def test_httpx_request_lines_never_carry_a_query_value():
    """Root handlers redact records from any logger (httpx logs full URLs at INFO)."""
    import io
    import logging as _logging

    stream = io.StringIO()
    handler = _logging.StreamHandler(stream)
    root = _logging.getLogger()
    root.addHandler(handler)
    httpx_logger = _logging.getLogger("httpx")
    old_level = httpx_logger.level
    httpx_logger.setLevel(_logging.INFO)
    try:
        inbox.install_root_redaction([handler])
        httpx_logger.info('HTTP Request: GET %s "HTTP/1.1 200 OK"', "https://b/x.csv?upn=SECRET1&ticket=SECRET2&page=2")
    finally:
        root.removeHandler(handler)
        httpx_logger.setLevel(old_level)
    text = stream.getvalue()
    assert "SECRET1" not in text and "SECRET2" not in text
    assert "https://b/***.csv?***" in text and "upn" not in text
    # Idempotent over already-masked values.
    assert _redacted("https://b/***.csv?***") == "https://b/***.csv?***"


def test_a_quoted_mail_from_cannot_inject_a_verdict():
    payload = (
        'amazonses.com; spf=pass smtp.mailfrom="x;dmarc=pass;header.from=vendor.example"@attacker.example; '
        "dkim=none; dmarc=fail header.from=vendor.example"
    )
    assert inbox.authenticated_sender_domain([payload], FROM) is None
    comment = "amazonses.com; spf=fail (dmarc=pass; header.from=vendor.example) smtp.mailfrom=a@attacker.example"
    assert inbox.authenticated_sender_domain([comment], FROM) is None
    escaped = 'amazonses.com; spf=pass smtp.mailfrom="a\\";dmarc=pass;header.from=vendor.example"@attacker.example'
    assert inbox.authenticated_sender_domain([escaped], FROM) is None


def test_a_display_name_cannot_carry_the_vendor_domain():
    """Authenticated mail from attacker.example named "ExampleSSP ssp.example" is not ExampleSSP's."""
    spoof = {"from": '"ExampleSSP ssp.example" <reports@attacker.example>', "authenticated_domain": "attacker.example"}
    assert inbox.sender_authenticated(spoof)
    assert not inbox.sender_filter_matches("ssp.example", spoof)
    # The authenticated domain must be the From mailbox's own.
    mismatched = {"from": "reports@ssp.example", "authenticated_domain": "attacker.example"}
    assert not inbox.sender_filter_matches("ssp.example", mismatched)


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
    assert fuzzy["emails"][0]["sender_authenticated"] is True
    assert fuzzy["emails"][0]["trusted_legacy_archive"] is False

    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "off")
    legacy = await inbox.search_emails(sender_contains="vendor.example", date_from="2026-10-26", date_to="2026-10-26")
    assert len(legacy["emails"]) == 3
    assert "unauthenticated_sender_match" in {w["code"] for w in legacy["search_warnings"]}
    fuzzy_off = await inbox.search_emails_fuzzy(keywords=["report"], date_from="2026-10-26", date_to="2026-10-26")
    assert len(fuzzy_off["emails"]) == 3
    assert "unauthenticated_sender_match" in {w["code"] for w in fuzzy_off["search_warnings"]}


# -- logs -----------------------------------------------------------------------------


def test_url_query_strings_are_redacted_from_logs(caplog):
    url = "https://bucket.s3.amazonaws.com/report.csv?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIAEXAMPLE"
    with caplog.at_level(logging.INFO, logger=inbox.logger.name):
        inbox.logger.info("downloading %s", url)
    assert "deadbeef" not in caplog.text and "AKIAEXAMPLE" not in caplog.text
    assert "https://bucket.s3.amazonaws.com/***.csv?***" in caplog.text


def test_default_debug_log_is_private(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.delenv("EMAIL_DEBUG_LOG_FILE", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    path = inbox._debug_log_path()
    assert path is not None and path.startswith(str(tmp_path))
    inbox._open_private_log(path).close()
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_debug_log_pre_created_by_another_user_is_refused(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    log.write_text("planted\n")
    owner = os.stat(log).st_uid
    # The file is someone else's from this process's point of view.
    monkeypatch.setattr(inbox.os, "geteuid", lambda: owner + 1)
    monkeypatch.setattr(inbox.os, "getuid", lambda: owner)  # the directory check still passes
    with pytest.raises(OSError, match="another user"):
        inbox._open_private_log(str(log))


def test_debug_log_with_other_hard_links_is_refused(tmp_path):
    other = tmp_path / "other.txt"
    other.write_text("x")
    os.link(other, tmp_path / "debug.log")
    with pytest.raises(OSError, match="hard links"):
        inbox._open_private_log(str(tmp_path / "debug.log"))


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


def test_shared_hosting_suffixes_never_align_tenants():
    """exchange.tenants.example and evil.tenants.example are different senders."""
    ix = "Reports <reporting@exchange.tenants.example>"
    forged = "amazonses.com; spf=pass smtp.mailfrom=x@evil.tenants.example; dkim=pass header.d=tenants.example"
    assert inbox.authenticated_sender_domain([forged], ix) is None
    assert not inbox._aligned("evil.tenants.example", "exchange.tenants.example")
    genuine = "amazonses.com; dkim=pass header.d=exchange.tenants.example; dmarc=none"
    assert inbox.authenticated_sender_domain([genuine], ix) == "exchange.tenants.example"


def test_redaction_masks_keyless_components_and_fragments():
    assert _redacted("https://b/x.csv?eyJhbGciOi") == "https://b/***.csv?***"
    assert _redacted("https://b/x.csv?a=1&eyJ#token=z") == "https://b/***.csv?***#***"
    # A credential used as the parameter NAME (empty value) is not kept either.
    assert _redacted("https://b/x.csv?eyJhbGciOiJIUzI1NiJ9.e30.sig=") == "https://b/***.csv?***"
    # An apostrophe inside the query does not end the redaction.
    assert _redacted("https://b/x.csv?t=ab'cd") == "https://b/***.csv?***"
    assert _redacted("https://b/x.csv#frag") == "https://b/***.csv#***"
    assert _redacted("see https://b/x.csv now") == "see https://b/***.csv now"


@pytest.mark.asyncio
async def test_fuzzy_sender_keywords_never_match_the_display_name(monkeypatch):
    raw = (
        b"Authentication-Results: amazonses.com; dmarc=pass header.from=evil.example\r\n"
        b"From: ExampleSSP <attacker@evil.example>\r\nTo: inbox@example.com\r\nSubject: Daily\r\n"
        b"Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    )
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/spoof": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    result = await inbox.search_emails_fuzzy(
        keywords=["examplessp"], search_fields=["sender"], date_from="2026-10-26", date_to="2026-10-26"
    )
    assert result["emails"] == [], result


@pytest.mark.asyncio
async def test_find_latest_report_carries_the_day_searches_warnings(monkeypatch):
    counts = iter([1, 100])

    async def day_search(**kwargs):
        n = next(counts)
        return {
            "status": "success",
            "emails": [],
            "search_warnings": [
                {"code": "unauthenticated_sender_skipped", "count": n, "severity": "high", "message": f"{n} left out"}
            ],
        }

    monkeypatch.setattr(inbox, "search_emails", day_search)
    result = await inbox.find_latest_report(
        sender_contains="vendor.example", on_or_before="2026-10-26", lookback_days=2
    )
    codes = [w["code"] for w in result["search_warnings"]]
    assert codes == ["unauthenticated_sender_skipped"]
    assert result["search_warnings"][0]["days"] == ["2026-10-26", "2026-10-25"]
    assert result["search_warnings"][0]["count"] == 101
    assert result["search_warnings"][0]["message"].startswith("101 email(s) were left out across 2 day(s)")


def test_url_userinfo_is_masked_with_or_without_a_query():
    assert _redacted("GET https://user:pa55@example.com/report.csv done") == (
        "GET https://***@example.com/***.csv done"
    )
    assert _redacted("https://user:pa55@example.com/r.csv?sig=1#t=2") == ("https://***@example.com/***.csv?***#***")
    # Every RFC 3986 userinfo character, apostrophe and sub-delims included.
    for userinfo in ("user:pa'ss", "u(s)er:p*a+s,s;=!$&~", "us%40er:p.a_s-s"):
        assert _redacted(f"https://{userinfo}@example.com/x.csv") == "https://***@example.com/***.csv", userinfo
    assert _redacted("mail me at reports@example.com") == "mail me at reports@example.com"


def test_a_dmarc_failure_is_final():
    fails = "amazonses.com; dkim=pass header.d=mail.vendor.example; dmarc=fail header.from=vendor.example"
    assert inbox.authenticated_sender_domain([fails], FROM) is None
    for verdict in ("quarantine", "reject", "permerror", "temperror"):
        text = f"amazonses.com; dkim=pass header.d=vendor.example; dmarc={verdict} header.from=vendor.example"
        assert inbox.authenticated_sender_domain([text], FROM) is None, verdict
    ok = "amazonses.com; dkim=pass header.d=vendor.example; dmarc=none header.from=vendor.example"
    assert inbox.authenticated_sender_domain([ok], FROM) == "vendor.example"
    sub = "amazonses.com; dkim=pass header.d=mail.vendor.example; dmarc=none header.from=vendor.example"
    assert inbox.authenticated_sender_domain([sub], FROM) is None


def test_a_debug_log_parent_swapped_for_a_symlink_is_refused(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    resolved_at_startup = str(logs.resolve())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    logs.rename(tmp_path / "logs.orig")
    logs.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        inbox._open_private_log(str(logs / "debug.log"), parent_real=resolved_at_startup)
    assert not (elsewhere / "debug.log").exists()


@pytest.mark.asyncio
async def test_bounded_searches_list_trusted_legacy_prefixes_by_date_header(monkeypatch):
    legacy_in = (
        b"From: Vendor <reports@vendor.example>\r\nTo: inbox@example.com\r\nSubject: Old report\r\n"
        b"Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    )
    legacy_out = legacy_in.replace(b"26 Oct 2026", b"01 Jan 2024")

    class S3(_FakeS3):
        def get_paginator(self, _name):
            objects = self.objects

            class _Paginator:
                def paginate(self, **kwargs):
                    async def pages():
                        keys = [k for k in objects if k.startswith(kwargs["Prefix"])]
                        yield {
                            "Contents": [
                                # LastModified is the IMPORT time, outside the window.
                                {"Key": k, "LastModified": datetime(2026, 1, 1, tzinfo=UTC), "Size": 1}
                                for k in keys
                            ]
                        }

                    return pages()

            return _Paginator()

    objects = {"emails/legacy/mailbux-1": legacy_in, "emails/legacy/mailbux-2": legacy_out}
    monkeypatch.setattr(inbox, "session", _FakeSession(S3(objects)))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "emails/%Y/%m/%d/")
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/legacy/",))
    assert "emails/legacy/" in inbox.build_search_prefixes(
        datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 26, 23, 59, tzinfo=UTC)
    )
    result = await inbox.search_emails(date_from="2026-10-26", date_to="2026-10-26")
    assert [e["s3_key"] for e in result["emails"]] == ["emails/legacy/mailbux-1"], result


def test_fallback_requires_exact_domain_across_shared_suffixes():
    """pages.example-style shared suffixes: a tenant subdomain must not vouch for the parent."""
    sender = "Reports <reports@pages.example>"
    for verdict in ("dmarc=none", ""):
        sub_dkim = f"amazonses.com; dkim=pass header.d=attacker.pages.example; {verdict}".rstrip("; ")
        sub_spf = f"amazonses.com; spf=pass smtp.mailfrom=x@attacker.pages.example; {verdict}".rstrip("; ")
        assert inbox.authenticated_sender_domain([sub_dkim], sender) is None
        assert inbox.authenticated_sender_domain([sub_spf], sender) is None
        exact = f"amazonses.com; dkim=pass header.d=pages.example; {verdict}".rstrip("; ")
        assert inbox.authenticated_sender_domain([exact], sender) == "pages.example"
        exact_spf = f"amazonses.com; spf=pass smtp.mailfrom=x@pages.example; {verdict}".rstrip("; ")
        assert inbox.authenticated_sender_domain([exact_spf], sender) == "pages.example"
    # Relaxed alignment is still honoured when the receiver itself says dmarc=pass.
    relaxed = "amazonses.com; dkim=pass header.d=bounce.vendor.example; dmarc=pass header.from=vendor.example"
    assert inbox.authenticated_sender_domain([relaxed], "r <r@vendor.example>") == "vendor.example"


class _DatedS3(_FakeS3):
    """Lists only keys under the requested prefix, each with its own LastModified."""

    def __init__(self, objects: dict[str, tuple[bytes, datetime]]) -> None:
        super().__init__({k: v[0] for k, v in objects.items()})
        self.modified = {k: v[1] for k, v in objects.items()}
        self.listed: list[str] = []

    def get_paginator(self, _name):
        outer = self

        class _Paginator:
            def paginate(self, **kwargs):
                outer.listed.append(kwargs["Prefix"])

                async def pages():
                    keys = [k for k in outer.objects if k.startswith(kwargs["Prefix"])]
                    yield {
                        "Contents": [
                            {"Key": k, "LastModified": outer.modified[k], "Size": len(outer.objects[k])} for k in keys
                        ]
                    }

                return pages()

        return _Paginator()


def _dated_mail(date_header: str, subject: str, auth: str | None) -> bytes:
    lines = [f"Authentication-Results: {auth}"] if auth else []
    lines += [f"From: {FROM}", "To: inbox@example.com", f"Subject: {subject}", f"Date: {date_header}"]
    return ("\r\n".join(lines) + "\r\n\r\nbody\r\n").encode()


def _newer_legacy_objects():
    authed = "amazonses.com; dmarc=pass header.from=vendor.example"
    return {
        # SES-delivered, in the day partition, received 08:00.
        "emails/2026/10/26/ses-1": (
            _dated_mail("Mon, 26 Oct 2026 08:00:00 +0000", "Partition report", authed),
            datetime(2026, 10, 26, 8, tzinfo=UTC),
        ),
        # Imported legacy mail sent 20:00 the same day; LastModified is the import time.
        "emails/legacy/imp-1": (
            _dated_mail("Mon, 26 Oct 2026 20:00:00 +0000", "Legacy report", None),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    }


def _partition_setup(monkeypatch, s3):
    monkeypatch.setattr(inbox, "session", _FakeSession(s3))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "emails/%Y/%m/%d/")
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/legacy/",))


@pytest.mark.asyncio
async def test_a_full_partition_does_not_hide_a_newer_legacy_message(monkeypatch):
    s3 = _DatedS3(_newer_legacy_objects())
    _partition_setup(monkeypatch, s3)
    result = await inbox.search_emails(
        sender_contains="vendor.example", date_from="2026-10-26", date_to="2026-10-26", max_results=1
    )
    assert "emails/legacy/" in s3.listed
    assert [e["s3_key"] for e in result["emails"]] == ["emails/legacy/imp-1"], result

    fuzzy = await inbox.search_emails_fuzzy(
        keywords=["report"], search_fields=["subject"], date_from="2026-10-26", date_to="2026-10-26", max_results=1
    )
    assert [e["s3_key"] for e in fuzzy["emails"]] == ["emails/legacy/imp-1"], fuzzy


@pytest.mark.asyncio
async def test_find_latest_report_returns_the_newer_legacy_message(monkeypatch):
    _partition_setup(monkeypatch, _DatedS3(_newer_legacy_objects()))
    result = await inbox.find_latest_report(
        sender_contains="vendor.example", on_or_before="2026-10-26", lookback_days=1, require_payload=False
    )
    assert [e["s3_key"] for e in result["emails"]] == ["emails/legacy/imp-1"], result


@pytest.mark.asyncio
async def test_an_older_legacy_message_does_not_displace_the_partition_match(monkeypatch):
    objects = _newer_legacy_objects()
    objects["emails/legacy/imp-1"] = (
        _dated_mail("Mon, 26 Oct 2026 01:00:00 +0000", "Legacy report", None),
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    _partition_setup(monkeypatch, _DatedS3(objects))
    result = await inbox.search_emails(
        sender_contains="vendor.example", date_from="2026-10-26", date_to="2026-10-26", max_results=1
    )
    assert [e["s3_key"] for e in result["emails"]] == ["emails/2026/10/26/ses-1"], result


@pytest.mark.parametrize(
    "date_header",
    ["Mon, 26 Oct 99999999999 10:00:00 +0000", "Mon, 26 Oct 2026 99:99:99 +9999", "", "garbage", "26 Oct"],
)
def test_unreadable_legacy_dates_are_skipped_not_fatal(monkeypatch, date_header):
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/legacy/",))
    window = (datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 26, 23, 59, tzinfo=UTC))
    assert inbox._message_sent_at(date_header) is None
    assert inbox._legacy_outside_window("emails/legacy/x", date_header, *window) is True


def test_message_sent_at_survives_every_parser_error(monkeypatch):
    for exc in (OverflowError, ValueError, TypeError, IndexError):

        def boom(_value, exc=exc):
            raise exc("bad date")

        monkeypatch.setattr(inbox.email.utils, "parsedate_to_datetime", boom)
        assert inbox._message_sent_at("Mon, 26 Oct 2026 10:00:00 +0000") is None


@pytest.mark.asyncio
async def test_a_legacy_message_with_an_overflowing_date_does_not_fail_the_search(monkeypatch):
    objects = _newer_legacy_objects()
    objects["emails/legacy/imp-2"] = (
        _dated_mail("Mon, 26 Oct 99999999999 10:00:00 +0000", "Broken legacy", None),
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    _partition_setup(monkeypatch, _DatedS3(objects))
    result = await inbox.search_emails(sender_contains="vendor.example", date_from="2026-10-26", date_to="2026-10-26")
    assert result["status"] == "success", result
    assert "emails/legacy/imp-2" not in [e["s3_key"] for e in result["emails"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keyword", "sender", "expected"),
    [
        ("ssp.example", "reports@ssp.example.evil.example", False),
        ("ssp.example", "reports@notssp.example", False),
        ("ssp", "reports@ssp.example", False),
        ("ssp.example", "reports@ssp.example", True),
        ("ssp.example", "reports@mail.ssp.example", True),
        ("reports@ssp.example", "reports@ssp.example", True),
        ("reports@ssp.example", "other@ssp.example", False),
    ],
)
async def test_fuzzy_sender_keywords_match_on_label_boundaries(monkeypatch, keyword, sender, expected):
    domain = sender.rpartition("@")[2]
    raw = (
        f"Authentication-Results: amazonses.com; dmarc=pass header.from={domain}\r\n"
        f"From: Reports <{sender}>\r\nTo: inbox@example.com\r\nSubject: Daily\r\n"
        "Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    ).encode()
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    result = await inbox.search_emails_fuzzy(
        keywords=[keyword], search_fields=["sender"], date_from="2026-10-26", date_to="2026-10-26"
    )
    assert bool(result["emails"]) is expected, result


@pytest.mark.asyncio
async def test_fuzzy_subject_keywords_still_match_substrings(monkeypatch):
    raw = (
        b"Authentication-Results: amazonses.com; dmarc=pass header.from=ssp.example\r\n"
        b"From: Reports <reports@ssp.example>\r\nTo: inbox@example.com\r\nSubject: Weekly Streaming Summary\r\n"
        b"Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    )
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    result = await inbox.search_emails_fuzzy(
        keywords=["stream"], search_fields=["subject", "sender"], date_from="2026-10-26", date_to="2026-10-26"
    )
    assert [e["s3_key"] for e in result["emails"]] == ["emails/m"], result


def test_nested_legacy_prefixes_are_not_listed_twice(monkeypatch):
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "emails/%Y/%m/%d/")
    monkeypatch.setattr(
        inbox,
        "EMAIL_TRUSTED_LEGACY_PREFIXES",
        (
            "emails/2026/10/26/mailbux-",  # inside today's partition: already listed by it
            "emails/2024/01/01/mailbux-",  # inside the date tree, outside the window: irrelevant
            "emails/legacy/",  # outside the date tree: listed
        ),
    )
    prefixes = inbox.build_search_prefixes(
        datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 10, 26, 23, 59, tzinfo=UTC)
    )
    assert prefixes == ["emails/2026/10/26/", "emails/2026/10/25/", "emails/legacy/"]


@pytest.mark.asyncio
async def test_overlapping_prefixes_never_return_an_object_twice(monkeypatch):
    authed = "amazonses.com; dmarc=pass header.from=vendor.example"
    objects = {
        "emails/2026/10/26/mailbux-1": (
            _dated_mail("Mon, 26 Oct 2026 08:00:00 +0000", "Nested legacy report", authed),
            datetime(2026, 10, 26, 8, tzinfo=UTC),
        ),
        "emails/legacy/imp-1": (
            _dated_mail("Mon, 26 Oct 2026 07:00:00 +0000", "Legacy report", None),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    }
    s3 = _DatedS3(objects)
    _partition_setup(monkeypatch, s3)
    # An operator list that also overlaps itself (a parent and a child prefix).
    monkeypatch.setattr(
        inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/2026/10/26/mailbux-", "emails/legacy/", "emails/legacy/imp")
    )
    result = await inbox.search_emails(date_from="2026-10-26", date_to="2026-10-26", max_results=10)
    keys = [e["s3_key"] for e in result["emails"]]
    assert sorted(keys) == ["emails/2026/10/26/mailbux-1", "emails/legacy/imp-1"], result
    assert "emails/2026/10/26/mailbux-" not in s3.listed
    fuzzy = await inbox.search_emails_fuzzy(
        keywords=["report"], search_fields=["subject"], date_from="2026-10-26", date_to="2026-10-26"
    )
    assert sorted(e["s3_key"] for e in fuzzy["emails"]) == keys and len(fuzzy["emails"]) == 2


@pytest.mark.asyncio
async def test_the_newest_match_in_a_partition_wins_even_when_its_key_sorts_last(monkeypatch):
    authed = "amazonses.com; dmarc=pass header.from=vendor.example"
    objects = {
        # Key order (a < z) is not time order: the newest report sorts last.
        "emails/2026/10/26/a-old": (
            _dated_mail("Mon, 26 Oct 2026 06:00:00 +0000", "Old report", authed),
            datetime(2026, 10, 26, 6, tzinfo=UTC),
        ),
        "emails/2026/10/26/m-mid": (
            _dated_mail("Mon, 26 Oct 2026 12:00:00 +0000", "Mid report", authed),
            datetime(2026, 10, 26, 12, tzinfo=UTC),
        ),
        "emails/2026/10/26/z-new": (
            _dated_mail("Mon, 26 Oct 2026 22:00:00 +0000", "New report", authed),
            datetime(2026, 10, 26, 22, tzinfo=UTC),
        ),
        "emails/2026/10/25/x-yesterday": (
            _dated_mail("Sun, 25 Oct 2026 23:00:00 +0000", "Yesterday report", authed),
            datetime(2026, 10, 25, 23, tzinfo=UTC),
        ),
    }
    s3 = _DatedS3(objects)
    _partition_setup(monkeypatch, s3)
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ())
    result = await inbox.search_emails(
        sender_contains="vendor.example", date_from="2026-10-25", date_to="2026-10-26", max_results=1
    )
    assert [e["s3_key"] for e in result["emails"]] == ["emails/2026/10/26/z-new"], result
    assert "emails/2026/10/25/" not in s3.listed  # the older partition was skipped
    fuzzy = await inbox.search_emails_fuzzy(
        keywords=["report"], search_fields=["subject"], date_from="2026-10-25", date_to="2026-10-26", max_results=2
    )
    assert [e["s3_key"] for e in fuzzy["emails"]] == ["emails/2026/10/26/z-new", "emails/2026/10/26/m-mid"]

    latest = await inbox.find_latest_report(
        sender_contains="vendor.example", on_or_before="2026-10-26", lookback_days=1, require_payload=False
    )
    assert [e["s3_key"] for e in latest["emails"]] == ["emails/2026/10/26/z-new"], latest


def test_a_query_after_an_apostrophe_in_the_path_is_redacted():
    assert _redacted("https://example.com/o'clock.csv?token=secret") == "https://example.com/***.csv?***"
    assert _redacted("GET https://example.com/a'b/c.csv#t=1 done") == "GET https://example.com/\u2026/***.csv#*** done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "date_header",
    ["", "garbage", "Tue, 27 Oct 2026 03:00:00 +0500"],  # missing, malformed, shifted to the next day
)
async def test_in_tree_legacy_mail_is_windowed_by_its_partition_not_its_date_header(monkeypatch, date_header):
    objects = {
        "emails/2026/10/26/mailbux-1": (
            _dated_mail(date_header, "Legacy in tree", None).replace(b"Date: \r\n", b""),
            datetime(2026, 1, 1, tzinfo=UTC),  # import time
        ),
    }
    _partition_setup(monkeypatch, _DatedS3(objects))
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/2026/10/26/mailbux-",))
    result = await inbox.search_emails(date_from="2026-10-26", date_to="2026-10-26")
    assert [e["s3_key"] for e in result["emails"]] == ["emails/2026/10/26/mailbux-1"], result
    fuzzy = await inbox.search_emails_fuzzy(
        keywords=["legacy"], search_fields=["subject"], date_from="2026-10-26", date_to="2026-10-26"
    )
    assert [e["s3_key"] for e in fuzzy["emails"]] == ["emails/2026/10/26/mailbux-1"], fuzzy


def test_out_of_tree_legacy_mail_is_still_windowed_by_its_date_header(monkeypatch):
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "emails/%Y/%m/%d/")
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/legacy/", "emails/2026/10/26/mailbux-"))
    window = (datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 26, 23, 59, tzinfo=UTC))
    assert inbox._legacy_outside_window("emails/legacy/x", "garbage", *window) is True
    assert inbox._legacy_outside_window("emails/legacy/x", "Mon, 26 Oct 2026 10:00:00 +0000", *window) is False
    assert inbox._legacy_outside_window("emails/2026/10/26/mailbux-x", "garbage", *window) is False
    assert inbox._legacy_outside_window("emails/2026/10/26/mailbux-x", "Mon, 01 Jan 2024 10:00:00 +0000", *window) is (
        False
    )
    # Sorting inside the tree: the Date header only when it agrees with the partition day.
    day = datetime(2026, 10, 26, tzinfo=UTC)
    assert inbox._effective_received("emails/2026/10/26/mailbux-x", "garbage", datetime(2026, 1, 1, tzinfo=UTC)) == day
    assert inbox._effective_received(
        "emails/2026/10/26/mailbux-x", "Mon, 26 Oct 2026 10:00:00 +0000", datetime(2026, 1, 1, tzinfo=UTC)
    ) == datetime(2026, 10, 26, 10, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "expected"), [("off", True), ("required", False)])
async def test_fuzzy_display_name_keywords_match_only_in_off_mode(monkeypatch, mode, expected):
    raw = (
        b"Authentication-Results: amazonses.com; dmarc=pass header.from=vendor.example\r\n"
        b"From: Vendor Reports <reports@vendor.example>\r\nTo: inbox@example.com\r\nSubject: Daily\r\n"
        b"Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    )
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", mode)
    result = await inbox.search_emails_fuzzy(
        keywords=["vendor reports"], search_fields=["sender"], date_from="2026-10-26", date_to="2026-10-26"
    )
    assert bool(result["emails"]) is expected, result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sender", "subject", "keyword", "expected"),
    [
        # An attacker's authenticated mail naming the vendor domain in its subject.
        ("attacker@evil.example", "Report from vendor.example", "vendor.example", False),
        ("attacker@evil.example", "Report for reports@vendor.example", "reports@vendor.example", False),
        # The vendor's own mail still matches by sender.
        ("reports@vendor.example", "Daily", "vendor.example", True),
        # Plain words keep the subject/sender OR.
        ("attacker@evil.example", "Streaming summary", "streaming", True),
    ],
)
async def test_domain_shaped_fuzzy_keywords_bind_to_the_sender(monkeypatch, sender, subject, keyword, expected):
    domain = sender.rpartition("@")[2]
    raw = (
        f"Authentication-Results: amazonses.com; dmarc=pass header.from={domain}\r\n"
        f"From: Reports <{sender}>\r\nTo: inbox@example.com\r\nSubject: {subject}\r\n"
        "Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    ).encode()
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "required")
    result = await inbox.search_emails_fuzzy(keywords=[keyword], date_from="2026-10-26", date_to="2026-10-26")
    assert bool(result["emails"]) is expected, result


@pytest.mark.parametrize(
    ("keyword", "sender_shaped"),
    [
        ("vendor.example", True),
        ("mail.vendor.example", True),
        ("reports@vendor.example", True),
        ("@vendor.example", True),
        ("version 2.0", False),
        ("2.0", False),
        ("report @ 10am", False),
        ("v1.2.3", False),
        ("streaming", False),
        ("a@b", False),
        ("reports@example.xn--p1ai", True),
        ("example.xn--p1ai", True),
        ("bücher.example", True),
        ("reports@отчёты.рф", True),
        ("2.0.1", False),
    ],
)
def test_only_whole_mailboxes_or_domains_are_sender_shaped(keyword, sender_shaped):
    assert inbox.sender_shaped(keyword) is sender_shaped


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["version 2.0", "report @ 10am"])
async def test_non_sender_keywords_with_dots_or_at_still_match_the_subject(monkeypatch, keyword):
    raw = (
        "Authentication-Results: amazonses.com; dmarc=pass header.from=vendor.example\r\n"
        "From: Reports <reports@vendor.example>\r\nTo: inbox@example.com\r\n"
        f"Subject: Daily {keyword} digest\r\n"
        "Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    ).encode()
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "required")
    result = await inbox.search_emails_fuzzy(keywords=[keyword], date_from="2026-10-26", date_to="2026-10-26")
    assert [e["s3_key"] for e in result["emails"]] == ["emails/m"], result


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("https://josé:sécret@example.com/r.csv", "https://***@example.com/***.csv"),
        (
            "https://us'er:pa'ss@example.com/o'clock.csv?token=s3cr3t#frag",
            "https://***@example.com/***.csv?***#***",
        ),
        ("GET https://ü:p@exämple.com/a;b=c.csv?x=1 done", "GET https://***@exämple.com/***.csv?*** done"),
        ("https://a@b@example.com/x.csv", "https://***@example.com/***.csv"),  # the LAST '@' ends userinfo
        ("https://example.com/path@not-userinfo.csv?t=1", "https://example.com/***.csv?***"),
        ("https://files.example/download/SECRET/report.csv", "https://files.example/\u2026/***.csv"),
        (
            "https://***@example.com/\u2026/***.csv?***#***",
            "https://***@example.com/\u2026/***.csv?***#***",
        ),  # idempotent
        ("plain reports@example.com text", "plain reports@example.com text"),
    ],
)
def test_structural_url_redaction(line, expected):
    assert _redacted(line) == expected
    assert "s3cr3t" not in inbox.redact_url_queries(line) and "sécret" not in inbox.redact_url_queries(line)


def test_a_configured_debug_log_under_a_planted_parent_symlink_is_refused(tmp_path):
    elsewhere = tmp_path / "elsewhere-log"
    elsewhere.mkdir()
    (tmp_path / "logs-link").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        inbox._open_private_log(str(tmp_path / "logs-link" / "debug.log"))
    assert not (elsewhere / "debug.log").exists()
    # A plain directory still works.
    plain = tmp_path / "logs-plain"
    plain.mkdir()
    inbox._open_private_log(str(plain / "debug.log")).close()
    assert (plain / "debug.log").exists()


def test_the_debug_log_walk_uses_the_trusted_system_link_rule(monkeypatch, tmp_path):
    seen: list = []

    def fake_walk(path):
        seen.append(path)
        return os.open(str(tmp_path), os.O_RDONLY)

    monkeypatch.setattr(inbox.safe_open, "walk_trusted", fake_walk)
    inbox._open_private_log(str(tmp_path / "debug.log")).close()
    assert seen == [str(tmp_path)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keyword", "subject", "expected"),
    [
        ("media", "Daily", False),  # a plain word is never a sender suffix
        ("foo.media", "Daily", True),  # a domain matches the sender
        ("media", "Media summary", True),  # a plain word still matches the subject
    ],
)
async def test_plain_words_never_match_a_sender_domain_suffix(monkeypatch, keyword, subject, expected):
    raw = (
        "Authentication-Results: amazonses.com; dmarc=pass header.from=foo.media\r\n"
        f"From: Reports <reports@foo.media>\r\nTo: inbox@example.com\r\nSubject: {subject}\r\n"
        "Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    ).encode()
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "required")
    result = await inbox.search_emails_fuzzy(keywords=[keyword], date_from="2026-10-26", date_to="2026-10-26")
    assert bool(result["emails"]) is expected, result


@pytest.mark.asyncio
async def test_an_idn_keyword_in_the_subject_does_not_select_foreign_mail(monkeypatch):
    raw = (
        b"Authentication-Results: amazonses.com; dmarc=pass header.from=evil.example\r\n"
        b"From: Reports <attacker@evil.example>\r\nTo: inbox@example.com\r\n"
        b"Subject: from reports@example.xn--p1ai\r\n"
        b"Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    )
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "required")
    result = await inbox.search_emails_fuzzy(
        keywords=["reports@example.xn--p1ai"], date_from="2026-10-26", date_to="2026-10-26"
    )
    assert result["emails"] == []


def test_debug_dir_is_chmodded_through_its_verified_descriptor(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.delenv("EMAIL_DEBUG_LOG_FILE", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    calls: list = []
    monkeypatch.setattr(inbox.os, "chmod", lambda *a, **_k: calls.append(("chmod", a)))
    real_fchmod = os.fchmod
    monkeypatch.setattr(inbox.os, "fchmod", lambda fd, mode: (calls.append(("fchmod", mode)), real_fchmod(fd, mode)))
    base = tmp_path / f"ses_s3_email-{os.getuid()}"
    base.mkdir(mode=0o755)
    assert inbox._debug_log_path() == str(base / "debug.log")
    assert calls == [("fchmod", 0o700)]  # never a path-based chmod
    assert stat.S_IMODE(os.stat(base).st_mode) == 0o700


def test_debug_dir_swapped_for_a_symlink_is_never_chmodded(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.delenv("EMAIL_DEBUG_LOG_FILE", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o755)
    os.chmod(victim, 0o755)
    (tmp_path / f"ses_s3_email-{os.getuid()}").symlink_to(victim, target_is_directory=True)
    assert inbox._debug_log_path() is None
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o755


def test_in_tree_legacy_mail_keeps_sub_day_bounds(monkeypatch):
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "emails/%Y/%m/%d/")
    monkeypatch.setattr(inbox, "EMAIL_TRUSTED_LEGACY_PREFIXES", ("emails/2026/10/26/mailbux-",))
    window = (datetime(2026, 10, 26, 12, tzinfo=UTC), datetime(2026, 10, 26, 13, tzinfo=UTC))
    key = "emails/2026/10/26/mailbux-x"
    assert inbox._legacy_outside_window(key, "Mon, 26 Oct 2026 12:30:00 +0000", *window) is False
    assert inbox._legacy_outside_window(key, "Mon, 26 Oct 2026 08:00:00 +0000", *window) is True
    assert inbox._legacy_outside_window(key, "Mon, 26 Oct 2026 18:00:00 +0000", *window) is True
    # Missing, unreadable or off-day headers fall back to the partition day.
    for header in ("", "garbage", "Wed, 28 Oct 2026 10:00:00 +0000", "Mon, 01 Jan 2024 10:00:00 +0000"):
        assert inbox._legacy_outside_window(key, header, *window) is False, header


@pytest.mark.parametrize(
    ("needle", "from_addr", "expected"),
    [
        ("bücher.example", "reports@xn--bcher-kva.example", True),  # Unicode keyword, A-label header
        ("xn--bcher-kva.example", "reports@bücher.example", True),  # A-label keyword, Unicode header
        ("reports@bücher.example", "Reports@XN--BCHER-KVA.example", True),
        ("bücher.example", "reports@mail.xn--bcher-kva.example", True),
        ("bücher.example", "reports@xn--bcher-kva.example.evil.example", False),
        ("reports@bücher.example", "other@xn--bcher-kva.example", False),
    ],
)
def test_idn_sender_keywords_match_a_label_and_unicode_headers(needle, from_addr, expected):
    assert inbox.mailbox_matches(needle, from_addr) is expected
    headers = {"from": f"Reports <{from_addr}>", "authenticated_domain": inbox._address_domain(from_addr)}
    assert inbox.sender_filter_matches(needle, headers) is expected


def test_a_unicode_from_header_is_authenticated_against_its_a_label():
    auth = "amazonses.com; dmarc=pass header.from=xn--bcher-kva.example"
    assert inbox.authenticated_sender_domain([auth], "R <r@bücher.example>") == "xn--bcher-kva.example"
    headers = {"from": "R <r@bücher.example>", "authenticated_domain": "xn--bcher-kva.example"}
    assert inbox.sender_authenticated(headers)


@pytest.mark.asyncio
async def test_fuzzy_idn_keyword_matches_an_a_label_sender(monkeypatch):
    raw = (
        b"Authentication-Results: amazonses.com; dmarc=pass header.from=xn--bcher-kva.example\r\n"
        b"From: Reports <reports@xn--bcher-kva.example>\r\nTo: inbox@example.com\r\nSubject: Daily\r\n"
        b"Date: Mon, 26 Oct 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    )
    monkeypatch.setattr(inbox, "session", _FakeSession(_FakeS3({"emails/m": raw})))
    monkeypatch.setattr(inbox, "S3_BUCKET", "bucket")
    monkeypatch.setattr(inbox, "S3_PREFIX", "emails/")
    monkeypatch.setattr(inbox, "DATE_PREFIX_FORMAT", "")
    monkeypatch.setattr(inbox, "EMAIL_SENDER_AUTH", "required")
    result = await inbox.search_emails_fuzzy(keywords=["bücher.example"], date_from="2026-10-26", date_to="2026-10-26")
    assert [e["s3_key"] for e in result["emails"]] == ["emails/m"], result


def test_capability_paths_are_masked_and_tagged_with_a_short_hash():
    url = "https://files.example/download/SECRET-TOKEN/report.csv?sig=abc"
    out = inbox.redact_url_queries(f"GET {url} done")
    assert "SECRET-TOKEN" not in out and "abc" not in out
    digest = __import__("hmac").new(inbox._URL_TAG_KEY, url.encode(), "sha256").hexdigest()[:12]
    assert out == f"GET https://files.example/\u2026/***.csv?*** [url-id:{digest}] done"
    # Idempotent: a second pass (logger filter + root handler) adds nothing.
    assert inbox.redact_url_queries(out) == out
    # Two different secret paths stay distinguishable.
    other = inbox.redact_url_queries("https://files.example/download/OTHER/report.csv?sig=abc")
    assert other.split(" [")[0] == out.split(" ")[1]  # same visible form...
    assert other.split("url-id:")[1] != out.split("url-id:")[1]  # ...different hash
    # Nothing to mask: no tag.
    assert inbox.redact_url_queries("https://files.example/") == "https://files.example/"


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        # The stem never survives, filename-shaped or not; a short alphabetic
        # extension is all that is kept of the last segment.
        ("https://files.example/download/eyJhbGciOiJIUzI1NiJ9.e30.abcdef", "https://files.example/\u2026/***"),
        ("https://files.example/AbC9dEf8GhI7jKl6MnO5pQ4", "https://files.example/***"),
        ("https://files.example/download/deadbeef1234.csv", "https://files.example/\u2026/***.csv"),
        ("https://files.example/s/token=secret.csv", "https://files.example/\u2026/***.csv"),
        ("https://files.example/a/Weekly_Report_2026-10-26.xlsx", "https://files.example/\u2026/***.xlsx"),
        ("https://files.example/export.json", "https://files.example/***.json"),
        ("https://files.example/a/archive.tar.gz", "https://files.example/\u2026/***.gz"),
        ("https://files.example/a/x.123", "https://files.example/\u2026/***"),
        ("https://files.example/", "https://files.example/"),
        # Backslashes are path separators too.
        ("https://files.example\\download\\SECRET.csv", "https://files.example/\u2026/***.csv"),
        ("https://files.example\\SECRET", "https://files.example/***"),
    ],
)
def test_the_last_path_segment_keeps_only_its_extension(url, shown):
    out = inbox.redact_url_queries(url)
    assert _redacted(url) == shown, out
    last = url.replace("\\", "/").rsplit("/", 1)[-1]
    secret = last.rpartition(".")[0] or last
    if len(secret) >= 3:
        assert secret not in out and "[url-id:" in out  # any path segment: masked and tagged
    assert inbox.redact_url_queries(out) == out  # idempotent


def test_the_url_tag_is_a_per_process_keyed_hmac():
    import hashlib
    import hmac

    url = "https://files.example/verify/123456"
    out = inbox.redact_url_queries(url)
    tag = out.split("[url-id:")[1].rstrip("]")
    assert tag == hmac.new(inbox._URL_TAG_KEY, url.encode(), hashlib.sha256).hexdigest()[:12]
    assert tag != hashlib.sha256(url.encode()).hexdigest()[:12]  # never an unkeyed hash
    assert len(inbox._URL_TAG_KEY) == 32


def test_content_disposition_filenames_still_parse():
    """The log-redaction regexes never shadow the Content-Disposition parser."""
    import httpx

    response = httpx.Response(200, headers={"content-disposition": 'attachment; filename="report.csv"'})
    assert inbox.get_filename_from_response(response, "https://files.example/x") == "report.csv"


def test_ss_and_sharp_s_domains_never_align():
    """IDNA 2008 (non-transitional): faß.de is xn--fa-hia.de, never fass.de."""
    assert inbox.idna_domain("faß.de") == "xn--fa-hia.de"
    from_hdr = "R <r@faß.de>"
    for auth in (
        "amazonses.com; dkim=pass header.d=fass.de; dmarc=none",
        "amazonses.com; spf=pass smtp.mailfrom=x@fass.de",
    ):
        assert inbox.authenticated_sender_domain([auth], from_hdr) is None
    assert not inbox.mailbox_matches("fass.de", "r@faß.de")
    assert not inbox.mailbox_matches("faß.de", "r@fass.de")
    # A receiver's DMARC pass still counts.
    assert inbox.authenticated_sender_domain(["amazonses.com; dmarc=pass header.from=xn--fa-hia.de"], from_hdr) == (
        "xn--fa-hia.de"
    )


def test_without_the_idna_package_non_ascii_domains_fail_closed(monkeypatch):
    monkeypatch.setattr(inbox, "_idna_pkg", None)
    assert inbox.idna_domain("faß.de") == "faß.de"  # never IDNA 2003's fass.de
    assert inbox.authenticated_sender_domain(["amazonses.com; dkim=pass header.d=fass.de"], "R <r@faß.de>") is None
    assert not inbox.mailbox_matches("fass.de", "r@faß.de")
    assert inbox.idna_domain("Vendor.Example") == "vendor.example"

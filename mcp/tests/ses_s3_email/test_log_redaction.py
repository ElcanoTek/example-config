"""URL query strings (presigned signatures), userinfo and capability paths
never reach a log line, and the debug log is private.

(Sender authentication moved to Elcano Explorer with the archive searches.)
"""

from __future__ import annotations

import logging
import os
import stat

import pytest
import ses_s3_email as inbox

_URL_HASH_RE = __import__("re").compile(r" \[url-id:[0-9a-f]{12}\]")


def _redacted(text: str) -> str:
    """redact_url_queries without the per-URL hash tags (tested separately)."""
    return _URL_HASH_RE.sub("", inbox.redact_url_queries(text))


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


# -- search_emails end to end over a fake bucket ---------------------------------------


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


def test_redaction_masks_keyless_components_and_fragments():
    assert _redacted("https://b/x.csv?eyJhbGciOi") == "https://b/***.csv?***"
    assert _redacted("https://b/x.csv?a=1&eyJ#token=z") == "https://b/***.csv?***#***"
    # A credential used as the parameter NAME (empty value) is not kept either.
    assert _redacted("https://b/x.csv?eyJhbGciOiJIUzI1NiJ9.e30.sig=") == "https://b/***.csv?***"
    # An apostrophe inside the query does not end the redaction.
    assert _redacted("https://b/x.csv?t=ab'cd") == "https://b/***.csv?***"
    assert _redacted("https://b/x.csv#frag") == "https://b/***.csv#***"
    assert _redacted("see https://b/x.csv now") == "see https://b/***.csv now"


def test_url_userinfo_is_masked_with_or_without_a_query():
    assert _redacted("GET https://user:pa55@example.com/report.csv done") == (
        "GET https://***@example.com/***.csv done"
    )
    assert _redacted("https://user:pa55@example.com/r.csv?sig=1#t=2") == ("https://***@example.com/***.csv?***#***")
    # Every RFC 3986 userinfo character, apostrophe and sub-delims included.
    for userinfo in ("user:pa'ss", "u(s)er:p*a+s,s;=!$&~", "us%40er:p.a_s-s"):
        assert _redacted(f"https://{userinfo}@example.com/x.csv") == "https://***@example.com/***.csv", userinfo
    assert _redacted("mail me at reports@example.com") == "mail me at reports@example.com"


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


def test_a_query_after_an_apostrophe_in_the_path_is_redacted():
    assert _redacted("https://example.com/o'clock.csv?token=secret") == "https://example.com/***.csv?***"
    assert _redacted("GET https://example.com/a'b/c.csv#t=1 done") == "GET https://example.com/\u2026/***.csv#*** done"


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
        # ...and so are the scheme delimiter's slashes (WHATWG accepts any mix).
        ("https:\\\\files.example\\download\\SECRET.csv", "https://files.example/\u2026/***.csv"),
        ("https:/\\files.example\\SECRET", "https://files.example/***"),
        # One separator, "\\" or "/", is still a URL (WHATWG accepts it).
        ("https:\\files.example\\download\\SECRET.csv", "https://files.example/\u2026/***.csv"),
        ("https:/files.example/download/SECRET.csv", "https://files.example/\u2026/***.csv"),
        ("https:///files.example/SECRET", "https://files.example/***"),
        ("https:files.example/download/SECRET.csv", "https://files.example/\u2026/***.csv"),  # no separator
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


@pytest.mark.parametrize(
    ("line", "secrets_", "expected"),
    [
        ("https://user:SECRET\\@host/path", ["SECRET"], "https://***@host/***"),
        ("https:\\\\user:SECRET@host\\x\\SECRET2.csv", ["SECRET", "SECRET2"], "https://***@host/…/***.csv"),
        ("https://user:pw@host/a/b.csv", ["user:pw"], "https://***@host/…/***.csv"),
        ('https://alice:sec"ret@example.com/file', ["sec", "ret"], "https://***@example.com/***"),
        ("https://alice:sec<ret@example.com/file", ["sec", "ret"], "https://***@example.com/***"),
        ("https://alice:sec>ret@example.com/file", ["sec", "ret"], "https://***@example.com/***"),
    ],
)
def test_userinfo_is_masked_whatever_it_contains(line, secrets_, expected):
    out = inbox.redact_url_queries(line)
    assert _redacted(line) == expected, out
    for secret in secrets_:
        assert secret not in out, (secret, out)
    assert inbox.redact_url_queries(out) == out  # idempotent


def test_one_separator_userinfo_is_masked():
    for line in ("https:/user:SECRET@host/x.csv", "https:\\user:SECRET@host\\x.csv"):
        out = inbox.redact_url_queries(line)
        assert "SECRET" not in out and out.startswith("https://***@host/"), out
        assert inbox.redact_url_queries(out) == out

#!/usr/bin/env python3
"""AWS SES + S3 Email Polling MCP Server

This MCP server provides tools for the agent to poll an S3 bucket for emails
that have been deposited by AWS SES. It allows the agent to:
- Check for new emails since the last check
- Get email details (subject, from, to, date, attachments)
- Download attachments (especially CSV files) for data analysis

Searching the archive is not here: it is Elcano Explorer's MCP server
(search_emails, search_emails_fuzzy, find_latest_report; the bundle's
`explorer` server). Every tool below takes the s3_key those searches return.

Setup Requirements:
1. AWS SES configured to receive emails and save to S3
2. S3 bucket with emails stored in a prefix (e.g., emails/)
3. AWS credentials with S3 read access

Environment Variables:
- AWS_ACCESS_KEY_ID: AWS access key
- AWS_SECRET_ACCESS_KEY: AWS secret key
- AWS_REGION: AWS region (e.g., us-east-2)
- EMAIL_S3_BUCKET: S3 bucket name (e.g., your-email-inbox-bucket)
- EMAIL_S3_PREFIX: S3 prefix for emails (default: emails/)
- EMAIL_S3_ARCHIVE_PREFIX: S3 prefix of the link archive written by the
  ses-email-date-partitioner lambda (default: emails/attachments/). Used as
  a fallback when a live link download fails because the URL has expired.
- EMAIL_ATTACHMENT_DIR: Local directory for downloaded attachments
- EMAIL_LAST_CHECK_FILE: File to store last check timestamp
"""

import asyncio
import contextlib
import contextvars
import email
import email.utils
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import stat
import tempfile
from datetime import UTC, datetime
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import IO, Any, NamedTuple
from urllib.parse import parse_qs, unquote, urlparse

import aioboto3
import aiofiles
import httpx
from mcp.server.fastmcp import FastMCP
from utils import safe_open, url_guard
from utils.fsutil import ensure_dir

_LINE_CONT_RE = re.compile(r"=[\r\n]+")
_SPACE_RE = re.compile(r"\s+")
_FILENAME_RE = re.compile(r'filename[*]?=["\']?(?:UTF-8\'\')?([^"\';\n]+)', re.IGNORECASE)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# URLs in log lines keep only what identifies them, never what grants access:
# capability links carry their credential in the query (X-Amz-Signature,
# token=..., or the token AS a parameter name), in the fragment, in the
# userinfo, or as a PATH segment (https://files.example/download/<secret>/x.csv).
# So a logged URL keeps only its scheme and host: the path collapses to "…"
# and its last segment keeps only a short alphabetic extension ("***.csv",
# else "***"; even a filename-shaped segment can be a token), and the whole
# query, the whole fragment and any userinfo are masked
# (https://***@host/…/***.csv?***#***), followed by a per-process keyed
# HMAC-SHA256 tag of the full original URL ([url-id:…]) so operators can
# still tell links apart within one process's logs.
#
# Structural, not one regex per part: each URL candidate (scheme to the next
# whitespace, quote-mark " or angle bracket) is split by the URL grammar
# itself. The authority ends at the first "/", "?" or "#"; everything before
# its LAST "@" is userinfo (any characters: non-ASCII, apostrophes, ...); the
# query / fragment start at the first "?" / "#" after it. An apostrophe ends
# nothing: over-redacting a closing quote beats leaking a token. Idempotent:
# an already-redacted URL is left as it is (no second hash).
# The two delimiter slashes may be any mix of "/" and "\" (https:\\host\x,
# https:/\host): WHATWG parsers accept those too.
_URL_CANDIDATE_RE = re.compile(r"https?:[/\\]*[^\s\"<>]+", re.IGNORECASE)
# The scheme and its WHOLE separator run, of any length including none
# ("https:host", "https:/", "https:\\", "https:///"), is normalised to "scheme://" first.
_URL_SCHEME_SEP_RE = re.compile(r"(https?):[/\\]*", re.IGNORECASE)
_URL_HASH_TAG = " [url-id:"
# Keyed per process: the tag only correlates links within one process's logs,
# and an unkeyed hash of a short secret (a six-digit code) could be brute-forced.
_URL_TAG_KEY = secrets.token_bytes(32)
_MASKED_SEGMENT = "\u2026"  # "…"


_LOG_SEGMENT_EXT_RE = re.compile(r"\.([A-Za-z]{1,5})")


def _masked_last_segment(segment: str) -> str:
    """The last path segment as logged: never its stem (a filename-shaped
    segment can still be a token: /download/deadbeef1234.csv), only a short
    alphabetic extension: "report.csv" -> "***.csv", anything else -> "***"."""
    if segment in ("", "***") or (segment.startswith("***.") and _LOG_SEGMENT_EXT_RE.fullmatch(segment[3:])):
        return segment
    stem, dot, ext = segment.rpartition(".")
    if dot and stem and _LOG_SEGMENT_EXT_RE.fullmatch("." + ext):
        return "***." + ext
    return "***"


def _mask_path(path: str) -> str:
    """ "/a/b/report.csv" -> "/…/***.csv": every earlier segment collapses to
    "…" and the last keeps only its extension ("/token" -> "/***")."""
    if path in ("", "/"):
        return path
    segments = path.split("/")  # path starts with "/"
    last = _masked_last_segment(segments[-1])
    if len(segments) == 2:
        return "/" + last
    return "/" + _MASKED_SEGMENT + "/" + last


def _redact_one_url(url: str) -> str:
    # Lenient parsers (WHATWG, httpx) read "\\" as "/", so a backslash path
    # (https://host\\download\\SECRET.csv) or delimiter (https:\\host) is
    # masked like a slash one.
    head = _URL_SCHEME_SEP_RE.match(url)
    if head:
        url = f"{head.group(1)}://" + url[head.end() :]
    scheme, sep, rest = url.replace("\\", "/").partition("://")
    if not sep:
        return url
    cut = len(rest)
    for delimiter in "/?#":
        index = rest.find(delimiter)
        if index != -1:
            cut = min(cut, index)
    authority, tail = rest[:cut], rest[cut:]
    if "@" in authority:
        authority = "***@" + authority.rpartition("@")[2]
    query_at = min((i for i in (tail.find("?"), tail.find("#")) if i != -1), default=-1)
    path, rest_q = (tail[:query_at], tail[query_at:]) if query_at != -1 else (tail, "")
    masked = ""
    if rest_q:
        masked = "?***" if rest_q.startswith("?") else ""
        if "#" in rest_q:
            masked += "#***"
    return f"{scheme}://{authority}{_mask_path(path)}{masked}"


# Userinfo is masked BEFORE candidate matching, on the raw text: from the
# scheme and its delimiter through any non-whitespace characters up to the
# authority's end ("/", "?" or "#"; a "\\", quote or angle bracket inside the
# userinfo never ends it), everything before the LAST "@" becomes "***@".
_USERINFO_PREPASS_RE = re.compile(r"(https?):[/\\]*([^\s/?#]*)@", re.IGNORECASE)


def _mask_userinfo(text: str) -> str:
    return _USERINFO_PREPASS_RE.sub(lambda m: m.group(1) + "://***@", text)


def redact_url_queries(text: str) -> str:
    """Mask URL credentials in a log line: userinfo (https://user:pass@host ->
    https://***@host), every path segment but the last, the whole query and the
    whole fragment; a changed URL is tagged with a keyed HMAC of the original."""
    text = _mask_userinfo(text)

    def _sub(match: re.Match[str]) -> str:
        url = match.group(0)
        redacted = _redact_one_url(url)
        if redacted == url or text.startswith(_URL_HASH_TAG, match.end()):
            return redacted
        digest = hmac.new(_URL_TAG_KEY, url.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()[:12]
        return f"{redacted}{_URL_HASH_TAG}{digest}]"

    return _URL_CANDIDATE_RE.sub(_sub, text)


class _RedactUrlQueries(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never lose a log line over formatting
            return True
        redacted = redact_url_queries(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


logger.addFilter(_RedactUrlQueries())


class _RedactingFormatter(logging.Formatter):
    """Wraps a handler's formatter: the TEXT it writes loses query values, and the
    shared LogRecord is never modified (other handlers see it unchanged)."""

    def __init__(self, inner: logging.Formatter | None) -> None:
        super().__init__()
        self._inner = inner or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        return redact_url_queries(self._inner.format(record))


def install_root_redaction(handlers: list[logging.Handler] | None = None) -> None:
    """Redact what the root handlers write, so records from ANY logger this
    process emits (httpx / httpcore log "HTTP Request: GET <full url>" at INFO on
    their own loggers, which this module's logger filter never sees) lose their
    query values. Formatter-level, so another module's own logger-level masking
    (e.g. an SSP server's) and other handlers are untouched."""
    for handler in logging.getLogger().handlers if handlers is None else handlers:
        if not isinstance(handler.formatter, _RedactingFormatter):
            handler.setFormatter(_RedactingFormatter(handler.formatter))


install_root_redaction()


def _debug_log_path() -> str | None:
    """EMAIL_DEBUG_LOG_FILE, else a private per-user file (dir 0700, file 0600).

    The old default, /tmp/ses_s3_email_debug.log, was created with the umask's
    world-readable mode in the shared temp dir: every local user could read the
    subjects, senders and download links this server logs. None when no private
    location can be had (the stderr log still works)."""
    configured = os.environ.get("EMAIL_DEBUG_LOG_FILE", "").strip()
    if configured:
        return configured
    uid = os.getuid() if hasattr(os, "getuid") else None
    base = os.path.join(tempfile.gettempdir(), f"ses_s3_email-{uid if uid is not None else 'user'}")
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(base, 0o700)
        # Verify and chmod THROUGH a no-follow descriptor: a path-based chmod
        # after an lstat could follow a symlink swapped in between and chmod
        # an arbitrary directory. (The log itself is later opened by another
        # no-follow walk that re-checks the directory's owner.)
        fd = os.open(
            base,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            st = os.fstat(fd)
            euid = os.geteuid() if hasattr(os, "geteuid") else uid
            if not stat.S_ISDIR(st.st_mode) or (euid is not None and st.st_uid != euid):
                return None  # someone else's directory: do not log there
            os.fchmod(fd, 0o700)
        finally:
            os.close(fd)
    except OSError:
        return None  # a planted symlink (ELOOP / ENOTDIR) or unusable directory
    return os.path.join(base, "debug.log")


def _open_dir_nofollow(real_dir: str) -> int:
    """A descriptor for directory ``real_dir``, reached from "/" one component
    at a time with O_NOFOLLOW|O_DIRECTORY (safe_open.walk_trusted): a symlink
    is followed only when it is a trusted system symlink (root-owned, in a
    root-owned directory nobody else can write: macOS /var, /tmp); any other
    symlink, such as one planted in a shared or sandbox-writable directory, is
    refused (OSError), never followed."""
    return safe_open.walk_trusted(real_dir)


def _open_private_log(path: str, *, parent_real: str | None = None) -> IO[str]:
    """Open the log file owner-only, never through a symlink — neither the file
    nor any directory above it — and return a stream on THAT descriptor: the
    handler writes to it and never reopens the path by name.

    The configured parent is never realpath()ed: it is reached from "/" one
    component at a time without following any symlink (a planted parent
    symlink is refused, not resolved), unless the caller passes an already
    resolved ``parent_real``; the file is opened relative to that directory
    descriptor. A parent owned by another user is refused, and so is an
    existing log file owned by another user or hard-linked elsewhere."""
    parent = parent_real or os.path.dirname(os.path.abspath(path))
    dir_fd = _open_dir_nofollow(parent)
    try:
        dst = os.fstat(dir_fd)
        if hasattr(os, "getuid") and dst.st_uid not in (os.getuid(), 0):
            raise OSError(f"debug log directory {parent} belongs to another user")
        fd = os.open(
            os.path.basename(path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=dir_fd,
        )
    finally:
        os.close(dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"debug log {path} is not a regular file")
        # A file pre-created by another user (e.g. in a shared /tmp) stays
        # theirs even after fchmod, so they could read it: refuse it, and a
        # hard link to some other file (nlink > 1) as well.
        if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
            raise OSError(f"debug log {path} belongs to another user")
        if st.st_nlink != 1:
            raise OSError(f"debug log {path} has other hard links")
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


DEBUG_LOG_FILE = _debug_log_path()
if DEBUG_LOG_FILE:
    try:
        file_handler = logging.StreamHandler(_open_private_log(DEBUG_LOG_FILE))
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        file_handler.addFilter(_RedactUrlQueries())
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("debug log file disabled (%s): %s", DEBUG_LOG_FILE, exc)

# --- Configuration ---
S3_BUCKET = os.environ.get("EMAIL_S3_BUCKET", "")
S3_PREFIX = os.environ.get("EMAIL_S3_PREFIX", "emails/")
# Production always sets these env vars (Containerfile / .env); the /tmp
# fallback only fires in tests and ad-hoc dev runs, where ephemeral storage
# is fine and leaves no clutter in $HOME.
ATTACHMENT_DOWNLOAD_DIR = os.environ.get("EMAIL_ATTACHMENT_DIR", "/tmp/fleet-email/email_attachments")
LAST_CHECK_FILE = os.environ.get("EMAIL_LAST_CHECK_FILE", "/tmp/fleet-email/email_last_checked.txt")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
# Where the ses-email-date-partitioner lambda archives email body links
# (the SES/S3 email pipeline). Layout:
#   <prefix>by-url/<sha256(url)>/<filename>          archived payloads
#   <prefix>manifests/YYYY/MM/DD/<messageId>.json    per-email link outcomes
ARCHIVE_PREFIX = os.environ.get("EMAIL_S3_ARCHIVE_PREFIX", "emails/attachments/")

# ATTACHMENT_DOWNLOAD_DIR and LAST_CHECK_FILE's parent are created lazily, at
# first write (see utils.fsutil.ensure_dir): importing this module must not
# plant a directory in whatever working directory the process happens to have.

# Initialize FastMCP
mcp = FastMCP("ses_s3_email")

# Initialize aioboto3 session
session = aioboto3.Session()


def sanitize_download_filename(filename: str) -> str:
    """Sanitize an email-provided filename without losing meaningful path-like segments."""
    safe_name = (filename or "").strip()
    safe_name = safe_name.replace("\\", "_").replace("/", "_")
    safe_name = _SPACE_RE.sub(" ", safe_name).strip(" .")
    if not safe_name:
        safe_name = f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return safe_name


def clean_reported_filename(filename: str | None) -> str:
    """Normalize a MIME-decoded attachment filename for return to agents.

    Why: some vendors fold long filenames across multiple header lines, which
    leaves raw CR/LF bytes embedded in the decoded filename. Agents that copy
    the returned string verbatim back into download_attachment then fail the
    lookup because the part's filename on disk has different whitespace.
    Collapsing whitespace here makes the round-trip robust.
    """
    if not filename:
        return ""
    return _SPACE_RE.sub(" ", filename).strip(" .")


def sniff_file_metadata(path: Path | str, max_bytes: int = 4096) -> dict[str, Any]:
    """Inspect a downloaded file and return compact metadata for the agent.

    Handles plain CSV/TSV, UTF-8 BOM, gzip, and zip-of-CSVs. The goal is to
    answer "what's actually in this file and how do I pandas.read_csv it"
    without the agent having to hexdump or re-open the file. Best-effort: any
    failure collapses to a minimal record — the caller should treat missing
    fields as "unknown", not as "definitely absent".
    """
    import csv
    import gzip
    import io
    import zipfile

    p = Path(path)
    meta: dict[str, Any] = {"path": str(p), "size_bytes": None}
    try:
        meta["size_bytes"] = p.stat().st_size
    except OSError:
        return meta

    suffixes = [s.lower() for s in p.suffixes]
    suffix = suffixes[-1] if suffixes else ""
    kind = "other"
    inner_name: str | None = None

    try:
        if suffix == ".zip":
            kind = "zip"
            with zipfile.ZipFile(p) as zf:
                names = zf.namelist()
                meta["zip_members"] = names[:10]
                csv_members = [n for n in names if n.lower().endswith((".csv", ".tsv", ".txt"))]
                if csv_members:
                    inner_name = csv_members[0]
                    with zf.open(inner_name) as zfh:
                        head = zfh.read(max_bytes)
                    kind = "zip_csv"
                else:
                    return {**meta, "kind": "zip", "inner_member_count": len(names)}
        elif suffix == ".gz" or (len(suffixes) >= 2 and suffixes[-2] == ".csv" and suffix == ".gz"):
            kind = "gzip_csv"
            with gzip.open(p, "rb") as gfh:
                head = gfh.read(max_bytes)
        else:
            with open(p, "rb") as fh:
                head = fh.read(max_bytes)
            if suffix in {".csv", ".tsv", ".txt"}:
                kind = "csv" if suffix != ".tsv" else "tsv"
            elif head.lstrip().startswith(b"<"):
                kind = "html_or_xml"
            else:
                kind = "other"

        # BOM detection + encoding guess
        bom = None
        if head.startswith(b"\xef\xbb\xbf"):
            bom = "utf-8-bom"
            head = head[3:]
        elif head.startswith(b"\xff\xfe"):
            bom = "utf-16-le"
        elif head.startswith(b"\xfe\xff"):
            bom = "utf-16-be"
        meta["byte_order_mark"] = bom

        if kind not in {"csv", "tsv", "zip_csv", "gzip_csv"}:
            return {**meta, "kind": kind}

        try:
            text = head.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            text = head.decode("latin-1", errors="replace")

        # First non-empty line looks like a header row if it has commas/tabs/semicolons
        first_line = ""
        for line in text.splitlines():
            if line.strip():
                first_line = line
                break

        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            # Fallback: pick the most common candidate in the first line
            counts = {d: first_line.count(d) for d in [",", "\t", ";", "|"]}
            delimiter = max(counts, key=counts.get) if any(counts.values()) else ","

        # Column headers (best effort)
        header_fields: list[str] = []
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            header_fields = [c.strip().strip('"').strip() for c in next(reader, [])]
        except csv.Error:
            pass

        # Quick row-count estimate from the sample
        newline_count = head.count(b"\n")

        return {
            **meta,
            "kind": kind,
            "inner_member": inner_name,
            "delimiter": delimiter,
            "header_sample": header_fields[:20],
            "sample_newline_count": newline_count,
            "sample_bytes": len(head),
        }
    except Exception as exc:
        logger.warning(f"sniff_file_metadata failed for {p}: {exc}")
        return {**meta, "kind": kind, "sniff_error": str(exc)}


_BUNDLE_ROOT = os.path.realpath(Path(__file__).resolve().parents[1])


def _writable_download_roots() -> list[str]:
    """Host directories a download may be written under.

    The default attachment dir (fleet maps it into the run workspace) and its
    parent, EMAIL_ALLOWED_DIRS (fleet maps the deployment workspace root), the
    process temp dir, and cwd unless cwd is the bundle checkout. The caller's
    output_dir is model-supplied, so it is confined to these; nothing may ever
    land inside the bundle checkout, whose files fleet loads as configuration.
    """
    roots: list[str] = []
    default = os.path.realpath(ATTACHMENT_DOWNLOAD_DIR)
    roots += [default, os.path.dirname(default)]
    for extra in os.environ.get("EMAIL_ALLOWED_DIRS", "").split(":"):
        if extra.strip():
            roots.append(os.path.realpath(extra.strip()))
    roots.append(os.path.realpath(tempfile.gettempdir()))
    cwd = os.path.realpath(os.getcwd())
    if cwd != _BUNDLE_ROOT:
        roots.append(cwd)
    return [r for r in dict.fromkeys(roots) if not _is_within(r, _BUNDLE_ROOT)]


def _is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _check_confined(real: str, shown: str) -> None:
    roots = _writable_download_roots()
    if _is_within(real, _BUNDLE_ROOT) or not any(_is_within(real, r) for r in roots):
        raise ValueError(
            f"output_dir is outside the writable roots: {shown}. "
            f"Write under one of: {', '.join(roots) or '(none configured)'}, or omit output_dir "
            "to use the run's attachment directory."
        )


def resolve_download_dir(output_dir: str | None) -> str:
    """Return the real download directory, or raise ValueError when it resolves
    (symlinks followed) outside the writable roots or into the bundle. The
    default directory gets the same check as a model-supplied one."""
    candidate = output_dir or ATTACHMENT_DOWNLOAD_DIR
    real = os.path.realpath(os.path.abspath(os.path.expanduser(candidate)))
    _check_confined(real, candidate)
    return real


class ReservedFile(NamedTuple):
    path: Path
    fd: int
    ident: tuple[int, int]


def _open_confined_dir(download_dir: str | Path) -> int:
    """Open download_dir as a directory descriptor without trusting its path.

    Starts at the deepest existing writable root that contains it and walks
    down one component at a time with dir_fd: each missing component is
    created there and every component is opened with O_NOFOLLOW, so a
    directory swapped for a symlink anywhere below the root is refused
    instead of followed. Raises ValueError when the walk is not confined.
    """
    target = os.path.abspath(str(download_dir))
    if _is_within(target, _BUNDLE_ROOT):
        _check_confined(target, str(download_dir))
    roots = [r for r in _writable_download_roots() if _is_within(target, r) and os.path.isdir(r)]
    if not roots:
        _check_confined(target, str(download_dir))  # raises with the allowed roots
        raise ValueError(f"no writable root contains {download_dir}")
    root = max(roots, key=len)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        rel = os.path.relpath(target, root)
        for part in [] if rel == "." else rel.split(os.sep):
            if part in ("", ".", ".."):
                raise ValueError(f"output_dir is not a plain path below a writable root: {download_dir}")
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o755, dir_fd=fd)
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                raise ValueError(
                    f"output_dir changed or is not a real directory below a writable root: {download_dir}"
                ) from exc
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def reserve_output_file(download_dir: str | Path, filename: str, source_identifier: str) -> ReservedFile:
    """Create a new, empty output file and return it OPEN for writing.

    The directory is reached by a symlink-refusing walk from a writable root
    (see _open_confined_dir), and the file is created inside it with
    O_EXCL|O_NOFOLLOW. The caller writes through the returned descriptor, so
    neither a directory nor a file swapped for a symlink (e.g. by code in the
    sandbox, which shares the workspace) can redirect the bytes. Concurrent
    callers always get distinct files.
    """
    safe_name = sanitize_download_filename(filename)
    path = Path(safe_name)
    suffix = "".join(path.suffixes)
    stem = path.name[: -len(suffix)] if suffix else path.name
    token = hashlib.sha1(source_identifier.encode("utf-8")).hexdigest()[:8]

    dir_fd = _open_confined_dir(download_dir)
    try:
        counter = 0
        while True:
            name = f"{stem}__{token}{suffix}" if counter == 0 else f"{stem}__{token}_{counter}{suffix}"
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd)
            except FileExistsError:
                counter += 1
                continue
            try:
                st = os.fstat(fd)
            except BaseException:
                os.close(fd)
                raise
            return ReservedFile(Path(os.path.abspath(str(download_dir))) / name, fd, (st.st_dev, st.st_ino))
    finally:
        os.close(dir_fd)


def build_collision_safe_output_path(download_dir: str | Path, filename: str, source_identifier: str) -> Path:
    """Reserve a deterministic, collision-safe output path (created empty)."""
    reserved = reserve_output_file(download_dir, filename, source_identifier)
    os.close(reserved.fd)
    return reserved.path


def _write_fd(fd: int, data: Any) -> None:
    """Write ``data`` (bytes, or a binary file object copied from its current
    position in 1 MiB chunks) to ``fd``, which this takes ownership of."""
    with os.fdopen(fd, "wb") as fh:  # takes ownership: closes fd on exit
        if hasattr(data, "read"):
            shutil.copyfileobj(data, fh, 1024 * 1024)
        else:
            fh.write(data)


def _discard_reserved(reserved: ReservedFile) -> None:
    """Remove a reserved file whose write did not complete, through a confined
    directory descriptor and only while the name is still the inode reserved."""
    try:
        dir_fd = _open_confined_dir(reserved.path.parent)
    except (OSError, ValueError):
        return
    try:
        st = os.stat(reserved.path.name, dir_fd=dir_fd, follow_symlinks=False)
        if (st.st_dev, st.st_ino) == reserved.ident:
            os.unlink(reserved.path.name, dir_fd=dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


async def write_reserved(reserved: ReservedFile, data: Any) -> None:
    """Write through the reserved descriptor (closed afterwards), never by path.

    The descriptor (and ``data``, possibly a spool file) is handed to a worker
    thread; if that hand-off itself fails, it is closed here, so no error path
    leaks it. A cancelled caller (tool timeout, client cancel) still WAITS for
    the running copy: the thread cannot be stopped, and the caller's cleanup
    must not close the spool under it. The partial file is then removed and
    the cancellation re-raised.
    """
    try:
        task = asyncio.ensure_future(asyncio.to_thread(_write_fd, reserved.fd, data))
    except BaseException:
        os.close(reserved.fd)
        raise
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue  # cancelled again: keep waiting for the thread
            except Exception:  # noqa: BLE001 - the write failed anyway; cleanup below
                break
        if task.done() and not task.cancelled():
            task.exception()  # retrieved: the cancellation is what propagates
        _discard_reserved(reserved)
        raise
    except BaseException:
        _discard_reserved(reserved)
        raise


def attachment_name_matches(part_filename: str, requested_filename: str) -> bool:
    """Match attachment names even when the caller passes a saved path or sanitized variant."""
    if part_filename == requested_filename:
        return True

    requested_basename = Path(requested_filename).name
    if part_filename == requested_basename:
        return True

    sanitized_part = sanitize_download_filename(part_filename)
    sanitized_requested = sanitize_download_filename(requested_filename)
    sanitized_basename = sanitize_download_filename(requested_basename)
    return sanitized_part == sanitized_requested or sanitized_part.endswith(sanitized_basename)


def decode_email_header(header_value: str) -> str:
    """Decode email header that may contain encoded words."""
    if not header_value:
        return ""
    decoded_parts = decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


async def get_last_checked_timestamp() -> datetime:
    """Reads the timestamp of the last successful email check."""
    if not os.path.exists(LAST_CHECK_FILE):
        # Default to a very old date if never checked
        return datetime(2000, 1, 1, tzinfo=UTC)
    async with aiofiles.open(LAST_CHECK_FILE) as f:
        iso_ts = (await f.read()).strip()
        if not iso_ts:
            return datetime(2000, 1, 1, tzinfo=UTC)
        return datetime.fromisoformat(iso_ts)


async def update_last_checked_timestamp(ts: datetime | None = None):
    """Updates the timestamp to the current time or a specified time."""
    if ts is None:
        ts = datetime.now(UTC)
    ensure_dir(Path(LAST_CHECK_FILE).parent)
    async with aiofiles.open(LAST_CHECK_FILE, "w") as f:
        await f.write(ts.isoformat())
    logger.info(f"Updated last checked timestamp to {ts.isoformat()}")


# Common file extensions that indicate downloadable files
DOWNLOAD_EXTENSIONS = {
    # Documents
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".txt",
    # Data files
    ".csv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    # Archives
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    ".bz2",
    # Images
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".svg",
    ".webp",
    # Media
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".mkv",
    # Code/Dev
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".sql",
    # Other
    ".exe",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".apk",
}


# Image extensions that should NOT be treated as report payloads.
# Email banners, logos, and tracking pixels almost always have these
# extensions and are never the actual report file. They were previously
# matched by both DOWNLOAD_EXTENSIONS and the keyword heuristic in
# classify_url, which caused download_all_link_attachments to "succeed"
# by pulling the brand banner instead of the report URL.
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".svg",
    ".webp",
    ".ico",
}


# Click-tracking redirect URLs. These don't have file extensions and
# don't contain "report"/"download" in the encoded payload, but they
# ARE the path to the actual report (the email-marketing platform
# rewrites the original link into a tracker before delivery). We need
# to recognize these as download candidates so:
#   - has_payload=True searches return the email
#   - download_all_link_attachments tries the tracker (not the banner)
# Each entry is (host_suffix, path_substring). Host_suffix matches the
# end of the netloc (case-insensitive); path_substring matches anywhere
# in the path. Either an exact host_suffix match OR a path_substring
# match is enough to flag — many of these platforms split traffic
# across many subdomains.
CLICK_TRACKER_PATTERNS: tuple[tuple[str, str], ...] = (
    # SendGrid (Viant DSP, many SaaS senders)
    ("sendgrid.net", "/ls/click"),
    ("sendgrid.net", "/wf/click"),
    # Mailchimp
    ("list-manage.com", "/track/click"),
    ("mailchimp.com", "/track/click"),
    # HubSpot
    ("hubspotlinks.com", ""),
    ("hs-sites.com", "/e1t/c/"),
    # Iterable
    ("iterable.com", "/api/email/click"),
    ("links.iterable.com", ""),
    # Marketo / Eloqua / Pardot / Salesforce Marketing Cloud
    ("mkt.com", "/trk"),
    ("eloqua.com", "/e/er"),
    ("pardot.com", "/e/"),
    ("exct.net", ""),
    ("exacttarget.com", ""),
    # Postmark
    ("pstmrk.it", ""),
    # Generic patterns the rewriters share
    ("", "/ls/click"),
    ("", "/wf/click"),
    ("", "/track/click"),
)


def is_click_tracker_url(url: str) -> bool:
    """Return True if the URL looks like an email-marketing click-tracking
    redirector. These URLs don't expose the underlying file extension so
    extension/keyword classification misses them, but they are usually
    the only path to the actual payload (e.g. Viant DSP scheduled
    reports route through SendGrid's /ls/click)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    for host_suffix, path_substring in CLICK_TRACKER_PATTERNS:
        host_ok = (not host_suffix) or host == host_suffix or host.endswith("." + host_suffix)
        path_ok = (not path_substring) or path_substring in path
        # Require a non-empty match on at least one of the two so a
        # blank/blank entry never matches everything.
        if (host_suffix or path_substring) and host_ok and path_ok:
            return True
    return False


def clean_url(url: str) -> str:
    """Clean and normalize a URL extracted from email text.

    Handles common email formatting issues:
    - Removes embedded newlines and line-wrapping artifacts
    - Strips trailing punctuation
    - Fixes quoted-printable encoding artifacts
    - Normalizes whitespace
    """
    if not url:
        return ""

    # Remove soft line breaks (= followed by newline in quoted-printable)
    url = _LINE_CONT_RE.sub("", url)

    # Remove embedded newlines and carriage returns (common in quoted-printable)
    url = url.replace("\n", "").replace("\r", "")

    # Strip leading/trailing whitespace
    url = url.strip()

    # Clean up trailing punctuation that might be captured
    url = url.rstrip(".,;:!?")

    return url


def extract_urls_from_text(text: str) -> list[str]:
    """Extract URLs from plain text using regex."""
    # URL pattern that captures most common URL formats
    # Updated to handle line-wrapped URLs by including = and newlines in the capture
    # then cleaning them up afterward
    url_pattern = r'https?://[^\s<>"\')\]}>]*(?:=[\r\n]+[^\s<>"\')\]}>]*)*'
    urls = re.findall(url_pattern, text)

    # Clean up each URL
    cleaned = []
    for url in urls:
        url = clean_url(url)
        if url:
            cleaned.append(url)

    return cleaned


def extract_urls_from_html(html: str) -> list[dict[str, str]]:
    """Extract URLs from HTML anchor tags with their link text."""
    # Pattern to match <a> tags and extract href and text
    link_pattern = r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>'
    matches = re.findall(link_pattern, html, re.IGNORECASE)

    links = []
    for href, text in matches:
        if href.startswith(("http://", "https://")):
            # Clean and decode the URL
            cleaned_url = clean_url(href)
            links.append(
                {
                    "url": cleaned_url,
                    "text": text.strip() if text else "",
                }
            )
    return links


# Redirector/security wrappers that hide the real target URL. Proofpoint
# URL Defense rewrites every link in mail from protected organizations,
# Amazon Ads routes report links through na.r.ads.amazon.com/CL0/,
# and AWS SES click tracking uses awstrack.me/L0/. Decoding the inner URL
# lets classification see the real target (e.g. a presigned S3 report vs.
# an advertising.amazon.com sign-in page). Fetching still uses the OUTER
# URL so redirect chains and one-shot tokens behave normally.


def _decode_proofpoint_v2(url: str) -> str | None:
    parsed = urlparse(url)
    if "urldefense.proofpoint.com" not in parsed.netloc.lower() or "/v2/url" not in parsed.path:
        return None
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if not encoded:
        return None
    try:
        return unquote(encoded.replace("_", "/").replace("-", "%"))
    except Exception:
        return None


def _decode_proofpoint_v3(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if "urldefense.com" not in host and "urldefense.proofpoint.com" not in host:
        return None
    match = re.search(r"/v3/__(.+?)__;", url)
    if not match:
        return None
    # v3 replaces runs of special chars with '*' (originals live in the
    # base64 tail). Domain and path survive intact, which is all that
    # classification needs.
    return match.group(1)


def _decode_path_embedded(url: str) -> str | None:
    """Amazon Ads na.r.ads.amazon.com/CL0/<url> and awstrack.me/L0/<url>.

    The embedded URL is one percent-encoded path segment (its slashes are
    %2F), and trackers append trailing segments after it (/1/<msgid>/<sig>),
    so split on the first literal '/' before unquoting."""
    host = urlparse(url).netloc.lower()
    if host.endswith("r.ads.amazon.com"):
        marker = "/CL0/"
    elif host.endswith("awstrack.me"):
        marker = "/L0/"
    else:
        return None
    idx = url.find(marker)
    if idx < 0:
        return None
    inner = unquote(url[idx + len(marker) :].split("/", 1)[0])
    return inner if inner.startswith(("http://", "https://")) else None


def unwrap_url(url: str, max_depth: int = 3) -> str:
    """Peel known redirector/security wrappers to reach the inner URL.

    Returns the innermost decodable URL (possibly the input itself).
    Wrappers nest in practice (Proofpoint around awstrack around the real
    link), hence the loop.
    """
    current = url
    for _ in range(max_depth):
        inner = _decode_proofpoint_v2(current) or _decode_proofpoint_v3(current) or _decode_path_embedded(current)
        if not inner or not inner.startswith(("http://", "https://")):
            return current
        current = clean_url(inner)
    return current


def is_presigned_url(url: str) -> bool:
    """Time-limited signed storage URLs (S3/GCS/Azure) — always payloads."""
    parsed = urlparse(url)
    query = parsed.query.lower()
    if "x-amz-signature" in query or "x-amz-credential" in query or "x-goog-signature" in query:
        return True
    return "blob.core.windows.net" in parsed.netloc.lower() and "sig=" in query and "sv=" in query


# Inner-URL hosts that are never report payloads even when surrounding
# keywords look download-ish (sign-in pages, social, scheduling). Derived
# from a 14-day inbox inventory: advertising.amazon.com sign-in links alone
# were ~25% of all "download-likely" URLs before this filter.
JUNK_HOST_SUFFIXES = (
    "advertising.amazon.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "calendly.com",
    "calendar.google.com",
    "zoom.us",
    "youtube.com",
    "vimeo.com",
)


def _is_junk_host(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in JUNK_HOST_SUFFIXES)


# Never fetch list-management links: some providers process unsubscribes on
# a plain GET, so "downloading" one (e.g. reports.yahooinc.com/unsubscribe,
# which matches the "report" keyword) could silently cancel the scheduled
# report emails these tools exist to fetch.
UNSUBSCRIBE_RE = re.compile(r"unsubscribe|opt[-_]?out|email[-_]?preferences|manage[-_]?preferences", re.IGNORECASE)


def classify_url(url: str) -> dict[str, Any]:
    """Classify a URL and determine if it's likely a download link.

    Known redirector/security wrappers (Proofpoint, Amazon Ads CL0,
    awstrack) are decoded first so extension/keyword/presigned checks see
    the real target instead of the wrapper encoding."""
    cleaned_url = clean_url(url)
    inner_url = unwrap_url(cleaned_url)
    target = inner_url

    parsed = urlparse(target)
    path = unquote(parsed.path)

    # Extract filename from URL path
    filename = Path(path).name if path else ""
    extension = Path(path).suffix.lower() if path else ""

    # Image extensions are almost always email banners, logos, or
    # tracking pixels — never the actual report payload. Demote them
    # so download_all_link_attachments doesn't grab the brand banner
    # and call the email "downloaded" while ignoring the real link.
    is_image_asset = extension in IMAGE_EXTENSIONS

    # Check if extension indicates a downloadable file (excluding images,
    # handled separately as is_image_asset).
    is_download_extension = extension in DOWNLOAD_EXTENSIONS and not is_image_asset

    # Check for common download indicators in URL (includes API patterns)
    download_keywords = [
        "download",
        "attachment",
        "file",
        "export",
        "get",
        "report",
        "generate",
        "csv",
        "xlsx",
        "pdf",
        "api/download",
        "api/export",
        "api/report",
    ]
    has_download_keyword = any(kw in target.lower() for kw in download_keywords)

    # Check query parameters for download-related params
    query_lower = parsed.query.lower()
    download_params = [
        "format=csv",
        "format=xlsx",
        "format=pdf",
        "type=csv",
        # Magnite/Telaria scheduled reports use fmt= (?fmt=xls)
        "fmt=csv",
        "fmt=xls",
        "fmt=xlsx",
        "fmt=pdf",
        "export=",
        "download=",
        "action=download",
        "action=export",
    ]
    has_download_param = any(p in query_lower for p in download_params)

    # Click-tracking redirectors (SendGrid /ls/click, Mailchimp, etc.).
    # These don't have file extensions or readable keywords because the
    # original URL is encoded in a query parameter, but they ARE the
    # only path to the actual payload for many DSP / SaaS senders.
    is_click_tracker = is_click_tracker_url(cleaned_url) or is_click_tracker_url(target)

    is_presigned = is_presigned_url(target)
    is_junk_host = _is_junk_host(parsed.netloc)
    # A presigned URL or an explicit file extension is a real payload even
    # if the name mentions opt-outs (e.g. a CCPA suppression-list export
    # named ccpa_optout_report.csv) — only suppress bare list-management
    # pages, where a GET could actually unsubscribe.
    is_unsubscribe = (
        bool(UNSUBSCRIBE_RE.search(cleaned_url) or UNSUBSCRIBE_RE.search(target))
        and not is_download_extension
        and not is_presigned
    )

    is_download_likely = (
        (is_download_extension or has_download_keyword or has_download_param or is_click_tracker or is_presigned)
        and not is_image_asset
        and not is_junk_host
        and not is_unsubscribe
    )

    return {
        "url": cleaned_url,
        "original_url": url,  # Keep original for reference
        "inner_url": inner_url if inner_url != cleaned_url else None,
        "domain": parsed.netloc,
        "filename": filename if filename else None,
        "extension": extension if extension else None,
        "is_download_likely": is_download_likely,
        "is_image_asset": is_image_asset,
        "is_click_tracker": is_click_tracker,
        "is_presigned": is_presigned,
        "is_junk_host": is_junk_host,
        "is_unsubscribe": is_unsubscribe,
        "has_download_keyword": has_download_keyword,
        "has_download_param": has_download_param,
    }


def _extract_csvs_from_email(raw_email: bytes) -> tuple[str, str, list[dict[str, Any]]]:
    """Helper function to parse email content in a separate thread.

    Extracts CSV attachments from raw email bytes.
    """
    msg: Message = email.message_from_bytes(raw_email)
    email_subject = decode_email_header(msg.get("Subject", "unknown"))
    email_from = decode_email_header(msg.get("From", "unknown"))
    attachments = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue

        filename = part.get_filename()
        if filename:
            filename = decode_email_header(filename)
            content_type = part.get_content_type()

            # Check if it's a CSV
            is_csv = content_type == "text/csv" or filename.lower().endswith(".csv")

            if is_csv:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    attachments.append({"filename": filename, "payload": payload, "content_type": content_type})
    return email_subject, email_from, attachments


def get_filename_from_response(response: httpx.Response, url: str) -> str:
    """Extract filename from HTTP response headers or URL."""
    # Try Content-Disposition header first
    content_disposition = response.headers.get("content-disposition", "")
    if content_disposition:
        # Try to extract filename from Content-Disposition
        filename_match = _FILENAME_RE.search(content_disposition)
        if filename_match:
            return unquote(filename_match.group(1).strip())

    # Fall back to URL path
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = Path(path).name

    if filename and "." in filename:
        return filename

    # Generate a filename based on content type
    content_type = response.headers.get("content-type", "").lower()
    extension_map = {
        # Document types
        "application/pdf": ".pdf",
        "text/csv": ".csv",
        "application/csv": ".csv",
        "text/comma-separated-values": ".csv",
        "application/json": ".json",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "text/plain": ".txt",
        # Microsoft Office
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.ms-powerpoint": ".ppt",
        # Archives
        "application/zip": ".zip",
        "application/x-gzip": ".gz",
        "application/gzip": ".gz",
        "application/x-tar": ".tar",
        "application/x-rar-compressed": ".rar",
        # Images
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        # Octet-stream often used for generic binary downloads
        "application/octet-stream": "",  # Will need better handling
    }

    for mime, ext in extension_map.items():
        if mime in content_type:
            if ext:
                return f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            else:
                # For octet-stream, try to guess from URL or just use .bin
                parsed = urlparse(url)
                url_ext = Path(unquote(parsed.path)).suffix.lower()
                if url_ext and url_ext in DOWNLOAD_EXTENSIONS:
                    return f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}{url_ext}"
                return f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bin"

    return f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


# --- MCP Tools ---


def _extract_email_content(msg: Message, extract_body: bool = False) -> dict[str, Any]:
    """Helper to extract attachments and body/links from a message.

    Running this in an executor prevents blocking the event loop during
    CPU-intensive parsing and decoding.

    When extract_body is True, returns:
      - download_links: URLs the classifier flags as `is_download_likely`
        (kept for backward-compat with the search/download tools).
      - body_urls: ALL distinct URLs found in the body, each with
        classification flags (is_download_likely, is_click_tracker,
        is_image_asset, domain, link_text). This is the durable
        triage surface — agents can scan this list when our
        is_download_likely heuristic misses a new wrapper format.
    """
    attachments = []
    body_plain = ""
    body_html = ""
    download_links = []
    body_urls: list[dict[str, Any]] = []

    # Single pass to extract everything
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue

        content_disposition = part.get("Content-Disposition", "")
        filename = part.get_filename()

        # Check for attachment
        if "attachment" in content_disposition or filename:
            if filename:
                filename = clean_reported_filename(decode_email_header(filename))
                payload = part.get_payload(decode=True)
                attachments.append(
                    {
                        "filename": filename,
                        "content_type": part.get_content_type(),
                        "size_bytes": len(payload) if payload else 0,
                    }
                )

        # Check for body content if requested
        elif extract_body:
            content_type = part.get_content_type()
            if "attachment" not in content_disposition:
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body_plain = payload.decode("utf-8", errors="replace")
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body_html = payload.decode("utf-8", errors="replace")

    # Extract links if body was extracted
    if extract_body:
        seen_urls: set[str] = set()

        def _record(url: str, link_text: str | None, source: str) -> None:
            classification = classify_url(url)
            cleaned = classification["url"]
            if cleaned in seen_urls:
                return
            seen_urls.add(cleaned)
            entry = {
                "url": cleaned,
                "domain": classification["domain"],
                "filename": classification["filename"],
                "extension": classification["extension"],
                "link_text": link_text,
                "source": source,
                "is_download_likely": classification["is_download_likely"],
                "is_click_tracker": classification["is_click_tracker"],
                "is_image_asset": classification["is_image_asset"],
                "url_length": len(cleaned),
            }
            body_urls.append(entry)
            if classification["is_download_likely"]:
                download_links.append(
                    {
                        "url": cleaned,
                        "filename": classification["filename"],
                        "link_text": link_text,
                        "is_click_tracker": classification["is_click_tracker"],
                    }
                )

        if body_html:
            for link in extract_urls_from_html(body_html):
                _record(link["url"], link["text"] or None, "html")

        if body_plain:
            for url in extract_urls_from_text(body_plain):
                _record(url, None, "plain_text")

        # Catch URLs embedded in HTML but outside <a> tags (e.g. inside
        # <img src=> or raw text in the html body) — useful when the
        # html anchor parser misses something the plain-text extractor
        # also missed (alt-text-only, JS-rewritten, etc.).
        if body_html:
            for url in extract_urls_from_text(body_html):
                _record(url, None, "html_text")

    return {
        "attachments": attachments,
        "download_links": download_links,
        "body_urls": body_urls,
        "body_plain": body_plain,
        "body_html": body_html,
    }


@mcp.tool()
async def get_email(s3_key: str) -> dict[str, Any]:
    """Get full details of a specific email. Requires an s3_key from Explorer's search_emails or find_latest_report.

    Search with Explorer FIRST (mcp_explorer_search_emails / find_latest_report), then use the s3_key from its results here.
    Returns full email content including body preview and attachment list.

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Args:
        s3_key: The S3 object key from Explorer search results.

    Returns:
        Dictionary with: subject, from, to, date, body_preview,
        attachments[], download_links[] (URLs the classifier marks as
        likely report payloads — includes click-tracking redirectors
        such as SendGrid /ls/click), and body_urls[] (every URL found
        in the body with classification flags, so the agent can triage
        unknown wrapper formats).
    """
    try:
        async with session.client("s3", region_name=AWS_REGION) as s3_client:
            response = await s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            raw_email = await response["Body"].read()
        msg: Message = email.message_from_bytes(raw_email)

        content = _extract_email_content(msg, extract_body=True)
        attachments = content["attachments"]
        body_plain = content["body_plain"]
        body_html = content["body_html"]
        download_links = content["download_links"]
        body_urls = content["body_urls"]

        details = {
            "s3_key": s3_key,
            "subject": decode_email_header(msg.get("Subject", "")),
            "from": decode_email_header(msg.get("From", "")),
            "to": decode_email_header(msg.get("To", "")),
            "date": msg.get("Date", ""),
            "message_id": msg.get("Message-ID", ""),
            "body_preview": body_plain[:500] if body_plain else body_html[:500],
            "attachment_count": len(attachments),
            "attachments": attachments,
            "download_link_count": len(download_links),
            "download_links": download_links,
            "body_url_count": len(body_urls),
            "body_urls": body_urls,
        }

        return {"status": "success", "email": details}

    except Exception as e:
        logger.error(f"Failed to get email {s3_key}: {e}")
        return {"status": "error", "s3_key": s3_key, "error": str(e)}


@mcp.tool()
async def download_attachment(
    s3_key: str,
    filename: str,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Download an attachment from an email. Use after an Explorer search and get_email().

    Workflow: Explorer search_emails / find_latest_report → get_email() → download_attachment()

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Args:
        s3_key: The S3 object key from Explorer search results.
        filename: The attachment filename from get_email() results.
        output_dir: Optional save directory. Defaults to EMAIL_ATTACHMENT_DIR.

    Returns:
        Dictionary with: saved_to (local file path), size_bytes, content_type.
    """
    # Check the destination before reading anything from S3.
    try:
        download_dir = resolve_download_dir(output_dir)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}

    try:
        async with session.client("s3", region_name=AWS_REGION) as s3_client:
            response = await s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            raw_email = await response["Body"].read()
        msg: Message = email.message_from_bytes(raw_email)

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue

            part_filename = part.get_filename()
            if part_filename:
                part_filename = clean_reported_filename(decode_email_header(part_filename))

                if attachment_name_matches(part_filename, filename):
                    # Found the attachment
                    payload = part.get_payload(decode=True)

                    # get_payload(decode=True) returns None for parts that
                    # don't decode to bytes (e.g. message/rfc822 attachments
                    # or a malformed Content-Transfer-Encoding). Referencing
                    # output_path outside this guard used to raise
                    # UnboundLocalError, surfacing the useless "cannot access
                    # local variable 'output_path'" instead of this error.
                    if not isinstance(payload, bytes):
                        return {
                            "status": "error",
                            "error": (
                                f"Attachment '{part_filename}' was found but its payload could not be "
                                f"decoded to bytes (content type {part.get_content_type()}, "
                                f"transfer encoding {part.get('Content-Transfer-Encoding', 'unknown')})."
                            ),
                            "filename": part_filename,
                            "requested_filename": filename,
                            "content_type": part.get_content_type(),
                        }

                    reserved = reserve_output_file(
                        download_dir,
                        part_filename,
                        f"{s3_key}:{part_filename}",
                    )
                    output_path = reserved.path

                    # Write the file through the reserved descriptor
                    await write_reserved(reserved, payload)

                    logger.info(f"Downloaded attachment: {output_path}")

                    return {
                        "status": "success",
                        "filename": part_filename,
                        "requested_filename": filename,
                        "saved_to": str(output_path),
                        "size_bytes": len(payload),
                        "content_type": part.get_content_type(),
                        "file_metadata": sniff_file_metadata(output_path),
                    }

        return {
            "status": "error",
            "error": f"Attachment '{filename}' not found in email",
        }

    except Exception as e:
        logger.error(f"Failed to download attachment: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_inbox_stats() -> dict[str, Any]:
    """Get statistics about the email inbox.

    Returns:
        Dictionary with inbox statistics.
    """
    try:
        total_emails = 0
        total_size = 0
        oldest_email = None
        newest_email = None

        async with session.client("s3", region_name=AWS_REGION) as s3_client:
            paginator = s3_client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
                if "Contents" not in page:
                    continue
                for obj in page["Contents"]:
                    if obj["Key"] == S3_PREFIX:
                        continue
                    # The link archive lives under the email prefix
                    # (emails/attachments/) — its payloads/manifests are
                    # not emails.
                    if obj["Key"].startswith(ARCHIVE_PREFIX):
                        continue
                    total_emails += 1
                    total_size += obj["Size"]

                    if oldest_email is None or obj["LastModified"] < oldest_email:
                        oldest_email = obj["LastModified"]
                    if newest_email is None or obj["LastModified"] > newest_email:
                        newest_email = obj["LastModified"]

        last_checked = await get_last_checked_timestamp()

        return {
            "status": "success",
            "stats": {
                "total_emails": total_emails,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "oldest_email": oldest_email.isoformat() if oldest_email else None,
                "newest_email": newest_email.isoformat() if newest_email else None,
                "last_checked": last_checked.isoformat(),
                "s3_bucket": S3_BUCKET,
                "s3_prefix": S3_PREFIX,
            },
        }

    except Exception as e:
        logger.error(f"Failed to get inbox stats: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def extract_download_links(
    s3_key: str,
    download_likely_only: bool = False,
) -> dict[str, Any]:
    """Extract potential download links from an email body.

    This tool parses the email body (both plain text and HTML) to find URLs
    that may be download links. It classifies each URL based on file extension
    and download-related keywords.

    Args:
        s3_key: The S3 object key of the email file.
        download_likely_only: If True, only return links that appear to be downloads
                             (have file extensions or download keywords).

    Returns:
        Dictionary with list of found links and their classifications.
    """
    try:
        async with session.client("s3", region_name=AWS_REGION) as s3_client:
            response = await s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            raw_email = await response["Body"].read()
        msg: Message = email.message_from_bytes(raw_email)

        # Get email body
        body_plain = ""
        body_html = ""
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = part.get("Content-Disposition", "")

            if "attachment" not in content_disposition:
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body_plain = payload.decode("utf-8", errors="replace")
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body_html = payload.decode("utf-8", errors="replace")

        all_links = []
        seen_urls = set()

        # Extract from HTML (includes link text)
        if body_html:
            html_links = extract_urls_from_html(body_html)
            for link in html_links:
                if link["url"] not in seen_urls:
                    seen_urls.add(link["url"])
                    classification = classify_url(link["url"])
                    classification["link_text"] = link["text"]
                    classification["source"] = "html"
                    all_links.append(classification)

        # Extract from plain text
        if body_plain:
            text_urls = extract_urls_from_text(body_plain)
            for url in text_urls:
                if url not in seen_urls:
                    seen_urls.add(url)
                    classification = classify_url(url)
                    classification["link_text"] = None
                    classification["source"] = "plain_text"
                    all_links.append(classification)

        # Also extract any URLs from HTML that weren't in anchor tags
        if body_html:
            text_urls = extract_urls_from_text(body_html)
            for url in text_urls:
                if url not in seen_urls:
                    seen_urls.add(url)
                    classification = classify_url(url)
                    classification["link_text"] = None
                    classification["source"] = "html_text"
                    all_links.append(classification)

        # Filter if requested
        if download_likely_only:
            all_links = [link for link in all_links if link["is_download_likely"]]

        # Sort by download likelihood
        all_links.sort(key=lambda x: x["is_download_likely"], reverse=True)

        return {
            "status": "success",
            "s3_key": s3_key,
            "subject": decode_email_header(msg.get("Subject", "")),
            "total_links_found": len(all_links),
            "download_likely_count": sum(1 for link in all_links if link["is_download_likely"]),
            "links": all_links,
        }

    except Exception as e:
        logger.error(f"Failed to extract links from email {s3_key}: {e}")
        return {"status": "error", "s3_key": s3_key, "error": str(e)}


# Browser-style headers for fetching email body links. Many email-marketing
# click-tracking redirectors (SendGrid, Mailchimp, etc.) and DSP report-host
# CDNs return 400/403/Bad-Bot pages to default httpx UA strings, so we send
# a real Chrome UA + the headers a browser would send when following an
# email link. This is a server-side fetch on behalf of an authenticated
# user who already received the email — same effective trust as if they
# clicked the link in their mail client.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,application/octet-stream;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}


def _looks_like_html_response(response: httpx.Response) -> bool:
    """Detect whether an httpx response is an HTML landing page rather
    than a binary file. Click-trackers usually 302 to the file directly,
    but some sender platforms first land on an interstitial HTML page
    that contains the real download link as an anchor."""
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/html" in content_type or "application/xhtml" in content_type:
        return True
    # Some servers return the file with a generic content-type; sniff the
    # first bytes for an HTML signature as a fallback.
    head = response.content[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _pick_chase_target(html: str, original_url: str) -> str | None:
    """Given the HTML body of a click-tracker landing page, pick the most
    likely real download URL. Strategy: classify every URL in the HTML
    and prefer download-likely / non-image / non-tracker candidates over
    branding/footer links. Returns None if nothing plausible is found."""
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in extract_urls_from_html(html):
        url = link["url"]
        if url in seen or url == original_url:
            continue
        seen.add(url)
        c = classify_url(url)
        if not c["is_download_likely"] or c["is_image_asset"]:
            continue
        # Score: prefer non-tracker (real direct download) > tracker;
        # prefer URLs with a recognized extension; deprioritize same-host
        # CMS links.
        score = 0
        if not c["is_click_tracker"]:
            score += 10
        if c["extension"] in DOWNLOAD_EXTENSIONS and not c["is_image_asset"]:
            score += 5
        if c["has_download_keyword"]:
            score += 2
        candidates.append((score, c["url"]))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def archive_hash_for_url(url: str) -> str:
    """SHA-256 of the cleaned URL string (post clean_url, as both
    extract_download_links and the archiver lambda produce it) — the key
    contract shared with the ses-email-date-partitioner lambda's link
    archiver (the SES/S3 email pipeline). Cleaning here means an agent
    that hand-copied a URL with quoted-printable line breaks or trailing
    punctuation still hits the same archive key."""
    return hashlib.sha256(clean_url(url).encode("utf-8")).hexdigest()


def _archive_too_large(url: str, size: int) -> dict[str, Any]:
    logger.error(f"Refused oversized archived copy of {url}: {size} bytes")
    return {
        "status": "error",
        "source": "s3_archive",
        "url": url,
        "error": f"{LinkDownloadTooLarge(size)} The archived copy was not saved.",
        "retryable": False,
    }


async def _fetch_archived_link(
    url: str, download_dir: str | Path, filename: str | None = None, *, read_timeout: float | None = None
) -> dict[str, Any] | None:
    """Try to serve a link from the S3 archive written at email-arrival time.

    Returns a success result dict if the archive has the URL, else None.
    Never raises — archive lookup is a best-effort fallback.

    ``read_timeout`` bounds the S3 lookup and read only. The local write that
    follows is never put under a deadline: a cancelled to_thread write would
    keep running after this returned, so it is always awaited to completion."""
    try:
        if read_timeout is not None:
            read = await asyncio.wait_for(_read_archived(url), timeout=read_timeout)
        else:
            read = await _read_archived(url)
    except TimeoutError:
        logger.warning(f"Archive fallback read for {url} ran out of time")
        return None
    except Exception as e:
        logger.warning(f"Archive fallback lookup failed for {url}: {e}")
        return None
    if read is None or "status" in read:
        return read
    archive_key, archived_name, content, response, metadata = (
        read["key"],
        read["name"],
        read["content"],
        read["response"],
        read["metadata"],
    )
    size = read.get("size", 0)
    try:
        _check_batch_deadline()
        reserved = reserve_output_file(download_dir, filename or archived_name, f"archive:{url}")
        output_path = reserved.path
        await write_reserved(reserved, content)

        logger.info(f"Served expired link from S3 archive: {archive_key}")
        return {
            "status": "success",
            "source": "s3_archive",
            "url": url,
            "filename": output_path.name,
            "saved_to": str(output_path),
            "size_bytes": size,
            "content_type": response.get("ContentType", "unknown"),
            "archive_key": archive_key,
            "archived_at": metadata.get("archived-at"),
            "file_metadata": sniff_file_metadata(output_path),
            "note": (
                "Live download failed (link likely expired), so this file was "
                "served from the S3 link archive captured when the email "
                "arrived. Contents are identical to what the link returned "
                "at archive time."
            ),
        }
    except Exception as e:
        logger.warning(f"Archive fallback write failed for {url}: {e}")
        return None
    finally:
        with contextlib.suppress(Exception):
            content.close()


async def _read_archived(url: str) -> dict[str, Any] | None:
    """The archived copy's bytes (``{"key", "name", "content", "response",
    "metadata"}``), a refusal dict (``status``), or None when not archived."""
    url_hash = archive_hash_for_url(url)
    prefix = f"{ARCHIVE_PREFIX}by-url/{url_hash}/"
    async with session.client("s3", region_name=AWS_REGION) as s3_client:
        listing = await s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix, MaxKeys=1)
        contents = listing.get("Contents", [])
        if not contents:
            return None
        archive_key = contents[0]["Key"]
        archived_name = archive_key.rsplit("/", 1)[-1]
        # The archived copy gets the same byte cap as the live link.
        listed = contents[0].get("Size")
        if isinstance(listed, int) and listed > min(LINK_DOWNLOAD_MAX_BYTES, _call_budget_left()):
            return _archive_too_large(url, listed)
        response = await s3_client.get_object(Bucket=S3_BUCKET, Key=archive_key)
        declared = response.get("ContentLength")
        if isinstance(declared, int) and declared > min(LINK_DOWNLOAD_MAX_BYTES, _call_budget_left()):
            return _archive_too_large(url, declared)
        # StreamingBody.read(n) may return fewer bytes than asked before EOF:
        # read until EOF under a cumulative cap, then check it is complete.
        # Spooled to disk and never read back whole: the caller copies the
        # spool into the reserved file in chunks (write_reserved).
        spool = _new_call_spool()
        total = 0
        try:
            while True:
                chunk = await response["Body"].read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                try:
                    _charge_batch_budget(len(chunk))  # charged before the per-file cap
                except LinkDownloadTooLarge as exc:
                    spool.close()
                    if exc.batch:
                        # The batch's shared budget ran out (not this copy's size):
                        # a structured refusal, never a "not archived" None.
                        logger.error(f"Archived copy of {url} not read: the batch byte budget is spent")
                        return {
                            "status": "error",
                            "source": "s3_archive",
                            "url": url,
                            "code": "link_batch_budget_exceeded",
                            "error": f"{exc} The archived copy was not saved.",
                            "retryable": False,
                        }
                    return _archive_too_large(url, total)
                if total > LINK_DOWNLOAD_MAX_BYTES:
                    spool.close()
                    return _archive_too_large(url, total)
                spool.write(chunk)
        except BaseException:
            spool.close()
            raise
        spool.seek(0)
        content = spool
        if isinstance(declared, int) and total != declared:
            spool.close()
            return {
                "status": "error",
                "source": "s3_archive",
                "url": url,
                "error": f"The archived copy was truncated ({total} of {declared} bytes); not saved.",
                "retryable": True,
            }
        metadata = response.get("Metadata", {})
    return {
        "key": archive_key,
        "name": archived_name,
        "content": content,
        "size": total,
        "response": response,
        "metadata": metadata,
    }


# Caps for a link found in an email body (attacker-controllable). Reports this
# inbox receives are far below them; override per deployment with
# EMAIL_LINK_MAX_BYTES / EMAIL_LINK_MAX_SECONDS.
LINK_DOWNLOAD_MAX_BYTES = int(os.environ.get("EMAIL_LINK_MAX_BYTES", "") or 200 * 1024 * 1024)
LINK_DOWNLOAD_MAX_SECONDS = int(os.environ.get("EMAIL_LINK_MAX_SECONDS", "") or 240)
LINK_DOWNLOAD_MAX_REDIRECTS = 10
# How much of an HTML response is decoded and scanned for a chase target: the
# first 10 MB by default. It never limits a download: with no link found in
# that prefix, the response is saved as the file, under EMAIL_LINK_MAX_BYTES.
LINK_HTML_SCAN_MAX_BYTES = int(os.environ.get("EMAIL_LINK_HTML_SCAN_MAX_BYTES", "") or 10 * 1024 * 1024)


# A batch (download_all_link_attachments) shares ONE byte budget across its
# downloads; None outside a batch. A list so concurrent downloads of the batch
# charge the same counter.
LINK_BATCH_MAX_COUNT = int(os.environ.get("EMAIL_LINK_MAX_COUNT", "") or 20)
LINK_BATCH_CONCURRENCY = 3
LINK_BATCH_SKIPPED_SAMPLE = 5
# Default 1 GiB: several large reports (e.g. 3 x 150 MB) fit one batch; each
# file stays under LINK_DOWNLOAD_MAX_BYTES (200 MB).
LINK_BATCH_MAX_BYTES = int(os.environ.get("EMAIL_LINK_BATCH_MAX_BYTES", "") or 1024 * 1024 * 1024)
_BATCH_BUDGET: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("link_batch_budget", default=None)


# The batch's ONE deadline (event-loop time), None outside a batch. Checked
# before a download reserves its output file: a write that already started
# through its descriptor is always awaited, but none starts past the deadline.
_BATCH_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("link_batch_deadline", default=None)


class LinkBatchDeadlineExceeded(Exception):
    pass


def _check_batch_deadline() -> None:
    deadline = _BATCH_DEADLINE.get()
    if deadline is not None and asyncio.get_running_loop().time() >= deadline:
        raise LinkBatchDeadlineExceeded(
            f"Not saved: the batch's {LINK_DOWNLOAD_MAX_SECONDS}-second deadline passed before the write began."
        )


# Temp files holding this download_link_attachment call's response bodies;
# closed (deleted) when the call returns. None outside such a call.
_CALL_SPOOLS: contextvars.ContextVar[list[Any] | None] = contextvars.ContextVar("link_call_spools", default=None)
# How much of a body is kept in memory on the response: enough to sniff for
# HTML, and for an HTML page, the part the chase scan reads.
_SNIFF_BYTES = 512


def _new_call_spool() -> Any:
    spool = tempfile.TemporaryFile()  # noqa: SIM115 - closed by the call wrapper / archive reader
    spools = _CALL_SPOOLS.get()
    if spools is not None:
        spools.append(spool)
    return spool


def _spooled_body(response: httpx.Response, spool: Any, size: int) -> None:
    """Attach a spooled body to ``response``: ``response.content`` becomes only a
    bounded head (512 bytes; for an HTML page up to EMAIL_LINK_HTML_SCAN_MAX_BYTES),
    and the full body stays on disk for _body_of / write_reserved."""
    spool.seek(0)
    head = spool.read(_SNIFF_BYTES)
    content_type = (response.headers.get("content-type") or "").lower()
    sniffed = head.lstrip().lower()
    if (
        "text/html" in content_type
        or "application/xhtml" in content_type
        or sniffed.startswith((b"<!doctype html", b"<html"))
    ):
        head += spool.read(max(0, LINK_HTML_SCAN_MAX_BYTES - len(head)))
    response._content = head  # what Response.aread() stores (bounded head only)
    response.extensions = {**response.extensions, "spooled_body": (spool, size)}


def _body_of(response: httpx.Response) -> tuple[Any, int]:
    """(file-or-bytes positioned at the start, size) for the response's full body."""
    spooled = response.extensions.get("spooled_body")
    if spooled is None:
        return response.content, len(response.content)
    spool, size = spooled
    spool.seek(0)
    return spool, size


# One download_link_attachment call's remaining bytes across ALL its phases
# (live fetch, HTML chase, archive fallback); None outside such a call.
_CALL_BUDGET: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("link_call_budget", default=None)


def _call_budget_left() -> int:
    budget = _CALL_BUDGET.get()
    return LINK_DOWNLOAD_MAX_BYTES if budget is None else budget[0]


def _charge_batch_budget(n: int) -> None:
    """Charge ``n`` received bytes to this call's budget and to the batch's."""
    # Both counters are debited for the bytes received BEFORE either limit is
    # raised: a chunk that crosses the per-call cap still counts against the batch.
    call = _CALL_BUDGET.get()
    budget = _BATCH_BUDGET.get()
    if call is not None:
        call[0] -= n
    if budget is not None:
        budget[0] -= n
    if call is not None and call[0] < 0:
        raise LinkDownloadTooLarge(LINK_DOWNLOAD_MAX_BYTES - call[0])
    if budget is not None and budget[0] < 0:
        raise LinkDownloadTooLarge(LINK_BATCH_MAX_BYTES - budget[0], batch=True)


class LinkDownloadTooLarge(Exception):
    def __init__(self, size: int, *, batch: bool = False) -> None:
        if batch:
            message = f"The batch's downloads together exceed the {LINK_BATCH_MAX_BYTES}-byte batch budget."
        else:
            message = f"The download is larger than the {LINK_DOWNLOAD_MAX_BYTES}-byte cap ({size}+ bytes)."
        super().__init__(message)
        self.size = size
        self.batch = batch


@mcp.tool()
async def download_link_attachment(
    url: str,
    output_dir: str | None = None,
    filename: str | None = None,
    timeout_seconds: int = 120,
    chase_html: bool = True,
) -> dict[str, Any]:
    """Download a file from a URL found in an email body (not a direct attachment).

    Use this for download links IN the email body, not for actual attachments.
    For actual attachments, use download_attachment() instead.

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Browser-emulating fetch: we send a Chrome User-Agent and Accept headers
    so click-tracking redirectors (SendGrid /ls/click, Mailchimp, etc.)
    that block default Python UAs return the real 302 instead of a 400.
    Redirects are followed automatically; the chain is reported back as
    `redirect_chain` so the agent can see where the URL landed.

    HTML-chase fallback: if the URL is a click-tracker (or the redirect
    lands on a text/html page) and `chase_html` is True, we parse the
    landing page once for the most plausible download link and try it.
    This handles sender platforms that interpose an interstitial page
    between the click-tracker and the real file. We do NOT recurse — at
    most one chase per call.

    S3-archive fallback: every download-likely link is archived to S3 by
    a lambda minutes after the email arrives (the SES/S3 email pipeline). If
    the live fetch fails (expired presigned URL, dead one-shot token) or
    returns an HTML login page, the archived copy is served instead —
    look for `source: "s3_archive"` in the result. This makes historical
    report links downloadable long after the original URL died.

    Args:
        url: The URL to download from.
        output_dir: Optional directory to save the file. Defaults to EMAIL_ATTACHMENT_DIR.
        filename: Optional filename to use. If not provided, extracted from response.
        timeout_seconds: Download timeout in seconds for the whole fetch (default 120, at most 240).
            Files over 200 MB are refused (the deployment can change both caps).
        chase_html: If True (default), follow one HTML interstitial when
            the click-tracker doesn't land on a binary file directly.
            Set False to disable for debugging.

    Returns:
        Dictionary with the local file path and download details.
        On HTML-chase, includes `chased_from` (the URL that returned HTML)
        and `chased_to` (the URL we followed it to).
    """
    # ONE byte budget for every phase of this call (live fetch, HTML chase,
    # archive fallback), drawn on alongside the batch budget, so a single call
    # never transfers more than EMAIL_LINK_MAX_BYTES in total.
    token = _CALL_BUDGET.set([LINK_DOWNLOAD_MAX_BYTES])
    spools: list[Any] = []
    spools_token = _CALL_SPOOLS.set(spools)
    try:
        return await _download_link_attachment(url, output_dir, filename, timeout_seconds, chase_html)
    finally:
        _CALL_SPOOLS.reset(spools_token)
        _CALL_BUDGET.reset(token)
        for spool in spools:
            with contextlib.suppress(Exception):
                spool.close()


async def _download_link_attachment(
    url: str,
    output_dir: str | None,
    filename: str | None,
    timeout_seconds: int,
    chase_html: bool,
) -> dict[str, Any]:
    try:
        download_dir = resolve_download_dir(output_dir)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "url": url}

    # Validate URL up front
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {
            "status": "error",
            "error": f"Invalid URL scheme: {parsed.scheme}. Only http/https supported.",
        }

    classification = classify_url(url)
    effective_timeout = max(1, min(int(timeout_seconds or LINK_DOWNLOAD_MAX_SECONDS), LINK_DOWNLOAD_MAX_SECONDS))
    # ONE deadline for the whole call: the HTML-chase fetch gets only what is
    # left. The live fetch stops early enough to leave the archive fallback a
    # slice (25% of the budget, at most 30 s), so a stalled live link cannot
    # starve the fallback that exists for exactly that case.
    started = asyncio.get_running_loop().time()
    deadline = started + effective_timeout
    archive_slice = min(30.0, effective_timeout * 0.25)
    live_deadline = deadline - archive_slice

    async def _fetch(target_url: str) -> httpx.Response:
        # SSRF guard: the request event hook vets the host of EVERY hop
        # (initial URL, each followed redirect, and the HTML-chase target,
        # which also comes through here) against non-public address space
        # BEFORE the request is sent — email-body URLs are attacker
        # controlled, and follow_redirects would otherwise happily land on
        # 169.254.169.254 or an internal service. See utils/url_guard.py.
        #
        # Bounded: the body is streamed and refused past LINK_DOWNLOAD_MAX_BYTES
        # (a declared Content-Length over the cap is refused before reading),
        # and the whole fetch, redirects included, must finish within the
        # clamped timeout; httpx's own timeout is per read, so a server that
        # drips bytes could otherwise hold the call (and memory) open forever.
        remaining = live_deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise httpx.TimeoutException(f"no time left of the {effective_timeout}s deadline")

        async def _get() -> httpx.Response:
            # Redirects are followed HERE, not by httpx: with follow_redirects
            # httpx reads each 3xx body whole before moving on, outside the cap.
            # A redirect's body is never read, every hop passes the request hook
            # (the SSRF guard), and the hop count is bounded.
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(remaining),
                headers=_BROWSER_HEADERS,
                event_hooks={"request": [url_guard.request_hook]},
            ) as client:
                history: list[httpx.Response] = []
                current = target_url
                for _hop in range(LINK_DOWNLOAD_MAX_REDIRECTS + 1):
                    async with client.stream("GET", current) as streamed:
                        location = streamed.headers.get("location")
                        if streamed.is_redirect and location:
                            history.append(streamed)
                            current = str(streamed.url.join(location))
                            continue
                        # An error status (an expired link's 404 / 410 / 5xx) is raised
                        # BEFORE any size check or body read: its body is never
                        # charged, and the HTTPStatusError reaches the archive fallback.
                        streamed.raise_for_status()
                        declared = streamed.headers.get("content-length", "")
                        if declared.isdigit() and int(declared) > min(LINK_DOWNLOAD_MAX_BYTES, _call_budget_left()):
                            raise LinkDownloadTooLarge(int(declared))
                        # Spooled to disk as it arrives and NEVER read back whole:
                        # the saved file is copied from the spool in chunks
                        # (write_reserved), so concurrent near-cap downloads do
                        # not each hold their body in memory. Only a bounded head
                        # is kept on the response (_spooled_body).
                        spool = _new_call_spool()
                        total = 0
                        async for chunk in streamed.aiter_bytes():
                            total += len(chunk)
                            # Charge the received chunk to the call AND batch
                            # budgets first, then enforce the per-file cap: the
                            # chunk that crosses it still counts against the batch.
                            _charge_batch_budget(len(chunk))
                            if total > LINK_DOWNLOAD_MAX_BYTES:
                                raise LinkDownloadTooLarge(total)
                            spool.write(chunk)
                        _spooled_body(streamed, spool, total)
                        streamed.history = history
                        return streamed
                raise httpx.TooManyRedirects(f"more than {LINK_DOWNLOAD_MAX_REDIRECTS} redirects", request=None)

        try:
            return await asyncio.wait_for(_get(), timeout=remaining)
        except TimeoutError as exc:
            raise httpx.TimeoutException(f"no complete response within {effective_timeout}s") from exc

    async def _archived_within_deadline() -> dict[str, Any] | None:
        # The archive read gets its reserved slice (min(30 s, 25%)), and never
        # more than what is left of the one deadline, even when the live fetch
        # failed fast. The local write is not bounded (see _fetch_archived_link).
        remaining = min(archive_slice, deadline - asyncio.get_running_loop().time())
        if remaining <= 0:
            return None
        return await _fetch_archived_link(url, download_dir, filename, read_timeout=remaining)

    def _redirect_chain(response: httpx.Response) -> list[str]:
        chain = [str(h.url) for h in response.history]
        chain.append(str(response.url))
        return chain

    try:
        logger.info(f"Downloading from URL: {url}")
        response = await _fetch(url)

        # If we got HTML back from a click-tracker and chase is allowed,
        # try one more hop to the real download link.
        chased_from: str | None = None
        chased_to: str | None = None
        if (
            chase_html
            and response.status_code < 400
            and _looks_like_html_response(response)
            and (classification["is_click_tracker"] or len(response.history) > 0)
        ):
            # Only the first EMAIL_LINK_HTML_SCAN_MAX_BYTES of the page are decoded
            # and scanned for the real link: a cap-sized HTML body is never
            # decoded whole. No link found there: the full response is saved as
            # the download like any other (never refused for its size).
            prefix = response.content[:LINK_HTML_SCAN_MAX_BYTES]
            try:
                html = prefix.decode(response.encoding or "utf-8", errors="replace")
            except LookupError:
                html = prefix.decode("utf-8", errors="replace")
            del prefix
            if html:
                next_url = _pick_chase_target(html, url)
                if next_url:
                    logger.info(f"Click-tracker landed on HTML; chasing to {next_url}")
                    chased_from = str(response.url)
                    chased_to = next_url
                    # Release the first (HTML) body before the chase fetches the
                    # second: never two cap-sized bodies held at once.
                    html = ""
                    response = None
                    response = await _fetch(next_url)

        response.raise_for_status()

        # Determine filename
        final_filename = filename or get_filename_from_response(response, str(response.url))
        safe_filename = Path(final_filename).name
        body, body_size = _body_of(response)
        _check_batch_deadline()
        reserved = reserve_output_file(download_dir, safe_filename, url)
        output_path = reserved.path
        await write_reserved(reserved, body)

        logger.info(f"Downloaded link attachment: {output_path}")

        result: dict[str, Any] = {
            "status": "success",
            "url": url,
            "final_url": str(response.url),
            "filename": safe_filename,
            "saved_to": str(output_path),
            "size_bytes": body_size,
            "content_type": response.headers.get("content-type", "unknown"),
            "http_status": response.status_code,
            "redirect_chain": _redirect_chain(response),
            "is_click_tracker_source": classification["is_click_tracker"],
            "file_metadata": sniff_file_metadata(output_path),
        }
        if chased_from:
            result["chased_from"] = chased_from
            result["chased_to"] = chased_to
        # If the saved file is suspiciously HTML it may be a "log in to
        # download" interstitial we couldn't bypass. Prefer the archived
        # binary captured at email-arrival time when one exists.
        if _looks_like_html_response(response):
            # Release the live body before the archive read: never the HTML page
            # and the archived payload in memory at once (and both together
            # stay within the one per-call byte budget).
            body = response = None
            archived = await _archived_within_deadline()
            if archived is not None:
                # The live login-page HTML stays where it was saved: deleting
                # it by path could remove a file that replaced it meanwhile
                # (a check-then-unlink is not atomic). Point to it instead.
                archived["live_result_was"] = "html_page"
                archived["live_html_saved_to"] = str(output_path)
                return archived
            result["warning"] = (
                "Saved response appears to be HTML, not a binary file. "
                "The click-tracker may require user-side authentication "
                "or a session cookie. Inspect the file or ask the user "
                "to forward the actual report."
            )
        return result

    except url_guard.BlockedUrlError as e:
        # Policy refusal, not a fetch failure: no archive fallback, and never
        # retryable — the URL (or a redirect hop it took) points at non-public
        # address space.
        logger.error(f"Blocked SSRF attempt for URL {url}: {e}")
        return {
            "status": "error",
            "url": url,
            "error": str(e),
            "retryable": False,
        }
    except LinkBatchDeadlineExceeded as e:
        # The batch ran out of time: nothing was written, no archive fallback.
        return {"status": "error", "url": url, "error": str(e), "retryable": True}
    except LinkDownloadTooLarge as e:
        # Refused, not failed: no archive fallback and not retryable.
        logger.error(f"Refused oversized link download {url}: {e}")
        return {
            "status": "error",
            "url": url,
            "error": f"{e} The link was not saved.",
            **({"code": "link_batch_budget_exceeded"} if e.batch else {}),
            "retryable": False,
        }
    except httpx.TimeoutException:
        logger.error(f"Download timeout for URL: {url}")
        archived = await _archived_within_deadline()
        if archived is not None:
            archived["live_result_was"] = "timeout"
            return archived
        return {
            "status": "error",
            "url": url,
            "error": f"Download timed out after {effective_timeout} seconds",
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error downloading {url}: {e}")
        archived = await _archived_within_deadline()
        if archived is not None:
            archived["live_result_was"] = f"http_{e.response.status_code}"
            return archived
        # Click-tracker URLs that 400 are usually either truncated, or
        # one-shot tokens that have already been clicked. Either way
        # retrying via a different HTTP fetcher won't help — surface a
        # structured `retryable: false` so the agent knows to escalate.
        retryable = not (classification["is_click_tracker"] and e.response.status_code in (400, 410, 404))
        return {
            "status": "error",
            "url": url,
            "error": f"HTTP error {e.response.status_code}: {e.response.reason_phrase}",
            "http_status": e.response.status_code,
            "is_click_tracker_source": classification["is_click_tracker"],
            "retryable": retryable,
            "hint": (
                "Click-tracker URL rejected by the redirector. The link is "
                "likely truncated, expired, or browser-session-bound. No "
                "archived copy was found for this URL either (the archive "
                "covers emails received after 2026-05-28). Do not retry "
                "through other HTTP fetchers — ask the user to forward the "
                "actual file."
            )
            if not retryable
            else None,
        }
    except Exception as e:
        logger.error(f"Failed to download from URL {url}: {e}")
        archived = await _archived_within_deadline()
        if archived is not None:
            archived["live_result_was"] = f"exception: {e!s:.120}"
            return archived
        return {"status": "error", "url": url, "error": str(e)}


@mcp.tool()
async def download_all_link_attachments(
    s3_key: str,
    output_dir: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Download all likely download links from an email.

    This tool extracts all URLs from an email that appear to be download links
    (based on file extension or download keywords) and downloads them all.

    Args:
        s3_key: The S3 object key of the email file.
        output_dir: Optional directory to save files. Defaults to EMAIL_ATTACHMENT_DIR.
        timeout_seconds: Download timeout per file in seconds (default 120).

    Returns:
        Dictionary with list of downloaded files and any errors.
    """
    # Check the destination before reading anything from S3.
    try:
        download_dir = resolve_download_dir(output_dir)
    except ValueError as exc:
        return {"status": "error", "s3_key": s3_key, "error": str(exc)}
    # ONE deadline for the whole batch (EMAIL_LINK_MAX_SECONDS, under Fleet's
    # ~300 s tool cutoff), started BEFORE link extraction: extraction, every
    # fetch and every write start must fit in it.
    loop = asyncio.get_running_loop()
    batch_deadline = loop.time() + LINK_DOWNLOAD_MAX_SECONDS
    try:
        # First extract the download links
        try:
            links_result = await asyncio.wait_for(
                extract_download_links(s3_key, download_likely_only=True), timeout=LINK_DOWNLOAD_MAX_SECONDS
            )
        except TimeoutError:
            return {
                "status": "error",
                "s3_key": s3_key,
                "error": f"Link extraction did not finish within the batch's {LINK_DOWNLOAD_MAX_SECONDS}-second deadline.",
            }

        if links_result["status"] != "success":
            return links_result

        if not links_result["links"]:
            return {
                "status": "success",
                "s3_key": s3_key,
                "message": "No download links found in email",
                "downloaded": [],
                "errors": [],
            }

        downloaded = []
        errors = []

        # Bounded batch: at most LINK_BATCH_MAX_COUNT links, LINK_BATCH_CONCURRENCY
        # at a time, all charging one LINK_BATCH_MAX_BYTES budget, so one email
        # with many links cannot make the server buffer N x the per-file cap.
        all_links = links_result["links"]
        links = all_links[:LINK_BATCH_MAX_COUNT]
        # Links past the limit are summarized, never materialized one by one:
        # a count plus a small redacted sample.
        skipped_count = max(0, len(all_links) - LINK_BATCH_MAX_COUNT)
        skipped_sample = [
            redact_url_queries(str(link.get("url", "")))
            for link in all_links[LINK_BATCH_MAX_COUNT : LINK_BATCH_MAX_COUNT + LINK_BATCH_SKIPPED_SAMPLE]
        ]
        del all_links
        gate = asyncio.Semaphore(LINK_BATCH_CONCURRENCY)
        budget_token = _BATCH_BUDGET.set([LINK_BATCH_MAX_BYTES])
        deadline_token = _BATCH_DEADLINE.set(batch_deadline)
        # Each link gets at most what is left of the batch deadline; a link not
        # started before it expires is reported, not attempted.

        async def _one(link: dict[str, Any]) -> dict[str, Any]:
            async with gate:
                remaining = batch_deadline - asyncio.get_running_loop().time()
                if remaining < 1:
                    return {
                        "status": "error",
                        "error": f"Not started: the batch's {LINK_DOWNLOAD_MAX_SECONDS}-second deadline passed first.",
                    }
                return await download_link_attachment(
                    url=link["url"],
                    output_dir=download_dir,
                    timeout_seconds=max(1, min(int(timeout_seconds or LINK_DOWNLOAD_MAX_SECONDS), int(remaining))),
                )

        try:
            results = await asyncio.gather(*(_one(link) for link in links))
        finally:
            _BATCH_BUDGET.reset(budget_token)
            _BATCH_DEADLINE.reset(deadline_token)

        # Process results
        for link, result in zip(links, results, strict=True):
            if result["status"] == "success":
                downloaded.append(
                    {
                        "url": link["url"],
                        "link_text": link.get("link_text"),
                        "saved_to": result["saved_to"],
                        "size_bytes": result["size_bytes"],
                        "content_type": result["content_type"],
                        # "live" or "s3_archive" (served from the link
                        # archive because the live URL had expired)
                        "source": result.get("source", "live"),
                    }
                )
            else:
                errors.append(
                    {
                        "url": link["url"],
                        "link_text": link.get("link_text"),
                        "error": result["error"],
                        # e.g. link_batch_budget_exceeded: batch callers see why.
                        **{key: result[key] for key in ("code", "retryable") if key in result},
                    }
                )

        return {
            "status": "success",
            "s3_key": s3_key,
            "subject": links_result["subject"],
            "total_links_found": links_result["total_links_found"],
            "downloaded_count": len(downloaded),
            "error_count": len(errors),
            "downloaded": downloaded,
            "errors": errors,
            "download_directory": download_dir,
            **(
                {
                    "skipped_count": skipped_count,
                    "skipped_sample": skipped_sample,
                    "skipped_reason": f"Not downloaded: a batch fetches at most {LINK_BATCH_MAX_COUNT} links.",
                }
                if skipped_count
                else {}
            ),
        }

    except Exception as e:
        logger.error(f"Failed to download link attachments from {s3_key}: {e}")
        return {"status": "error", "s3_key": s3_key, "error": str(e)}


@mcp.tool()
async def list_archived_attachments(s3_key: str) -> dict[str, Any]:
    """List the link-archive manifest for an email: which body links were
    archived to S3 at arrival time, which were skipped, and which failed.

    A lambda archives every download-likely body link minutes after an
    email arrives (so files are retrievable after the original URLs
    expire). Use this to see what is available for a historical email
    before downloading: entries with status "archived" can be fetched via
    download_link_attachment(url=<entry url>) — it serves the archived
    copy automatically when the live URL is dead.

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Args:
        s3_key: The S3 object key of the email file
            (e.g. emails/2026/06/11/<messageId>).

    Returns:
        Dictionary with the manifest contents: per-link status
        ("archived", "already_archived", "skipped_not_download_likely",
        "failed_http_404", ...), archive keys, and file metadata. Emails
        received before the archiver was deployed (2026-05-28) have no
        manifest.
    """
    match = re.match(r"^.*?(\d{4}/\d{2}/\d{2})/([^/]+)$", s3_key)
    if not match:
        return {
            "status": "error",
            "s3_key": s3_key,
            "error": (
                "Could not derive a date partition from this key; expected a key like emails/YYYY/MM/DD/<messageId>."
            ),
        }
    manifest_key = f"{ARCHIVE_PREFIX}manifests/{match.group(1)}/{match.group(2)}.json"
    try:
        async with session.client("s3", region_name=AWS_REGION) as s3_client:
            response = await s3_client.get_object(Bucket=S3_BUCKET, Key=manifest_key)
            manifest = json.loads(await response["Body"].read())
        return {"status": "success", "manifest_key": manifest_key, **manifest}
    except Exception as e:
        # Only a genuinely missing key means "no manifest". Other S3
        # ClientErrors (AccessDenied, throttling, expired creds) must
        # surface as errors, not as a benign absence.
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404", "NotFound"):
            return {
                "status": "not_found",
                "s3_key": s3_key,
                "manifest_key": manifest_key,
                "message": (
                    "No archive manifest for this email. Either it arrived "
                    "before the link archiver was deployed (2026-05-28) or "
                    "archiving failed; try the live URLs via "
                    "download_link_attachment."
                ),
            }
        logger.error(f"Failed to read archive manifest {manifest_key}: {e}")
        return {"status": "error", "s3_key": s3_key, "error": str(e)}


if __name__ == "__main__":
    logger.info("Starting SES S3 Email MCP Server")
    logger.info(f"  Bucket: {S3_BUCKET}")
    logger.info(f"  Prefix: {S3_PREFIX}")
    logger.info(f"  Region: {AWS_REGION}")
    mcp.run()

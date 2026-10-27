#!/usr/bin/env python3
"""AWS SES + S3 Email Polling MCP Server

This MCP server provides tools for the agent to poll an S3 bucket for emails
that have been deposited by AWS SES. It allows the agent to:
- Check for new emails since the last check
- Get email details (subject, from, to, date, attachments)
- Download attachments (especially CSV files) for data analysis

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
from datetime import UTC, datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.parser import BytesHeaderParser
from email.utils import parseaddr
from pathlib import Path
from typing import IO, Any, NamedTuple
from urllib.parse import parse_qs, unquote, urlparse

import aioboto3
import aiofiles
import httpx
from mcp.server.fastmcp import FastMCP
from utils import safe_open, url_guard
from utils.fsutil import ensure_dir

try:  # IDNA 2008 / UTS 46 (a dependency of httpx); see idna_domain
    import idna as _idna_pkg
except ImportError:  # pragma: no cover - fail closed without it
    _idna_pkg = None

_LINE_CONT_RE = re.compile(r"=[\r\n]+")
_SPACE_RE = re.compile(r"\s+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
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

# Size of initial S3 byte-range request for header-only filtering
HEADER_FETCH_BYTES = int(os.environ.get("EMAIL_HEADER_FETCH_BYTES", "65536"))

# Emit progress logs every N scanned emails (0 disables periodic progress logs)
SEARCH_PROGRESS_EVERY = int(os.environ.get("EMAIL_SEARCH_PROGRESS_EVERY", "0"))

# Emit current object key logs every N scanned emails (0 disables)
SEARCH_KEY_LOG_EVERY = int(os.environ.get("EMAIL_SEARCH_KEY_LOG_EVERY", "0"))

# Max concurrent header range requests during search listing scans.
SEARCH_HEADER_FETCH_CONCURRENCY = int(os.environ.get("EMAIL_SEARCH_HEADER_FETCH_CONCURRENCY", "4"))

# Optional date-partitioned listing mode. Example format: emails/%Y/%m/%d/
DATE_PREFIX_FORMAT = os.environ.get("EMAIL_S3_DATE_PREFIX_FORMAT", "")
# Safety cap for number of daily prefixes to enumerate before falling back.
MAX_DATE_PREFIX_DAYS = int(os.environ.get("EMAIL_S3_MAX_DATE_PREFIX_DAYS", "62"))


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


def _is_meaningful_text_filter(value: str | None) -> bool:
    """Return True when a text filter has at least one alphanumeric character."""
    if value is None:
        return False
    return bool(_ALNUM_RE.search(value))


def _build_search_warnings(
    *,
    subject_contains: str | None,
    subject_keywords: list[str] | None,
    sender_contains: str | None,
    recipient_contains: str | None,
    date_from: str | None,
    date_to: str | None,
    has_payload: bool | None,
    max_results: int,
) -> list[dict[str, str]]:
    """Generate query-quality warnings for LLM agents."""
    warnings: list[dict[str, str]] = []

    if sender_contains is not None and not _is_meaningful_text_filter(sender_contains):
        warnings.append(
            {
                "code": "sender_filter_not_meaningful",
                "severity": "high",
                "message": "sender_contains has no letters or digits and will not narrow results reliably.",
                "suggestion": "Use a sender domain like 'thetradedesk.com' or a full address, or remove sender_contains.",
            }
        )

    ignored = ignored_sender_entries(sender_contains)
    if ignored:
        all_ignored = len(ignored) == len(_sender_needles(sender_contains or ""))
        warnings.append(
            {
                "code": "sender_filter_entries_ignored",
                "severity": "high",
                "message": (
                    f"sender_contains entries ignored (not a full mailbox or a multi-label domain): "
                    f"{', '.join(ignored)}."
                    + (" No entry is usable, so the sender filter matches nothing." if all_ignored else "")
                ),
                "suggestion": "Pass sender domains such as 'vendor.example' or full addresses such as "
                "'reports@vendor.example'; a bare name or TLD matches nothing.",
            }
        )

    if recipient_contains is not None and not _is_meaningful_text_filter(recipient_contains):
        warnings.append(
            {
                "code": "recipient_filter_not_meaningful",
                "severity": "high",
                "message": "recipient_contains has no letters or digits and will not narrow results reliably.",
                "suggestion": "Use a recipient email/domain fragment or remove recipient_contains.",
            }
        )

    if date_from is None and date_to is None:
        warnings.append(
            {
                "code": "unbounded_date_window",
                "severity": "medium",
                "message": "No explicit date_from/date_to was provided.",
                "suggestion": "Use both date_from and date_to to keep searches bounded.",
            }
        )
    elif date_from is None or date_to is None:
        warnings.append(
            {
                "code": "partially_bounded_date_window",
                "severity": "medium",
                "message": "Only one date bound was provided.",
                "suggestion": "Provide both date_from and date_to for deterministic retrieval.",
            }
        )

    has_text_filter = (
        bool(subject_contains) or bool(subject_keywords) or bool(sender_contains) or bool(recipient_contains)
    )
    has_structure_filter = has_payload is not None
    if not has_text_filter and not has_structure_filter:
        warnings.append(
            {
                "code": "broad_query_low_selectivity",
                "severity": "medium",
                "message": "No subject/sender/recipient/payload filters were provided.",
                "suggestion": "Add sender_contains and/or subject filters before expanding the date window.",
            }
        )

    if max_results > 250:
        warnings.append(
            {
                "code": "large_max_results",
                "severity": "low",
                "message": "max_results is very high and may increase response size and latency.",
                "suggestion": "Start with max_results <= 25, then widen only if needed.",
            }
        )

    return warnings


def build_search_prefixes(date_from_dt: datetime | None, date_to_dt: datetime | None) -> list[str]:
    """Build S3 prefixes to scan. Uses date partitions when configured and bounded."""
    if not DATE_PREFIX_FORMAT:
        return [S3_PREFIX]

    if date_from_dt is None or date_to_dt is None:
        return [S3_PREFIX]

    start_date = date_from_dt.date()
    end_date = date_to_dt.date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    total_days = (end_date - start_date).days + 1
    if total_days <= 0 or total_days > MAX_DATE_PREFIX_DAYS:
        logger.info(
            "Date prefix mode fallback: total_days=%s exceeds bounds (1..%s), using base prefix",
            total_days,
            MAX_DATE_PREFIX_DAYS,
        )
        return [S3_PREFIX]

    prefixes: list[str] = []
    current = end_date
    while current >= start_date:
        day_dt = datetime(current.year, current.month, current.day, tzinfo=UTC)
        prefix = day_dt.strftime(DATE_PREFIX_FORMAT)
        prefixes.append(prefix)
        current = current - timedelta(days=1)

    # Operator-trusted legacy imports (EMAIL_TRUSTED_LEGACY_PREFIXES) that live
    # OUTSIDE the date tree are listed too; their messages are windowed by their
    # Date header (see _legacy_outside_window). A legacy prefix INSIDE the date
    # tree (emails/2026/10/26/mailbux-) is never listed on its own: inside the
    # window its day partition already lists it, outside the window it is
    # irrelevant, and listing it again would cost a scan and duplicate results.
    partitions = list(prefixes)
    for legacy in EMAIL_TRUSTED_LEGACY_PREFIXES:
        if legacy in prefixes or any(legacy.startswith(p) for p in partitions) or _in_date_tree(legacy):
            continue
        prefixes.append(legacy)
    return prefixes


def _parse_partition(key: str) -> datetime | None:
    """The day a key's DATE_PREFIX_FORMAT partition names, or None.

    Never cut by a fixed width: a variable-width format ("%Y/%-m/%-d/",
    "%Y/%B/%d/") is matched by its PATH SEGMENTS (the format's "/" count is
    fixed), parsed, and accepted only when re-formatting the date reproduces
    exactly that prefix."""
    fmt = DATE_PREFIX_FORMAT
    if not fmt:
        return None
    strp = fmt.replace("%-", "%")  # strptime has no "%-d"; it accepts "5" for "%d"
    if "/" in fmt.rstrip("/") or fmt.endswith("/"):
        depth = fmt.count("/")
        pieces = key.split("/")
        if len(pieces) <= depth:  # both branches index pieces[depth - 1] / pieces[depth]
            return None
        if fmt.endswith("/"):
            candidates = ["/".join(pieces[:depth]) + "/"]
        else:
            # No trailing "/": the last date field is glued to the rest of the key
            # (emails/2026/10/26message-id): try that segment's prefixes, longest
            # first, and keep the first that round-trips through the format.
            base = "/".join(pieces[:depth]) + "/" if depth else ""
            last = pieces[depth]
            candidates = [base + last[:n] for n in range(len(last), 0, -1)]
    else:
        candidates = [key[:n] for n in range(len(key), 0, -1)]
    for candidate in candidates:
        try:
            day = datetime.strptime(candidate, strp).replace(tzinfo=UTC)
        except ValueError:
            continue
        if day.strftime(fmt) == candidate:
            return day
    return None


def _in_date_tree(prefix: str) -> bool:
    """True when ``prefix`` starts with a DATE_PREFIX_FORMAT partition (any day)."""
    return _parse_partition(prefix) is not None


def _message_sent_at(message_date: str) -> datetime | None:
    """The Date header as an aware datetime, or None when it cannot be read.

    parsedate_to_datetime raises more than ValueError on hostile input
    (OverflowError for an out-of-range year, IndexError / TypeError on
    truncated tuples): every one of those is just an unreadable date."""
    try:
        sent = email.utils.parsedate_to_datetime(message_date)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if sent is None:
        return None
    return sent.replace(tzinfo=UTC) if sent.tzinfo is None else sent


def _partition_day(s3_key: str) -> datetime | None:
    """The received day a key's date partition names (emails/2026/10/26/...), or None."""
    return _parse_partition(s3_key)


def _legacy_outside_window(
    s3_key: str, message_date: str, date_from_dt: datetime | None, date_to_dt: datetime | None
) -> bool:
    """For a key under a trusted legacy prefix (whose LastModified is the import
    time, not the mail's), True when it lies outside the window.

    A legacy object INSIDE the date tree is windowed by its Date header when
    that falls on its partition day (sub-day bounds hold), else by the
    partition day (the received date): a missing, malformed or
    timezone-shifted Date header must not make it undiscoverable. Only legacy objects outside the date tree are
    windowed by their Date header (and dropped when it cannot be read)."""
    if not trusted_legacy_key(s3_key) or (date_from_dt is None and date_to_dt is None):
        return False
    day = _partition_day(s3_key)
    if day is not None:
        # A valid Date header on the partition's own day gives the sub-day
        # position (a 12:00-13:00 window); a missing, unreadable or off-day
        # header falls back to the day overlapping the window.
        sent = _message_sent_at(message_date)
        if sent is not None and day <= sent < day + timedelta(days=1):
            return bool((date_from_dt and sent < date_from_dt) or (date_to_dt and sent > date_to_dt))
        day_end = day + timedelta(days=1) - timedelta(microseconds=1)
        return bool((date_from_dt and day_end < date_from_dt) or (date_to_dt and day > date_to_dt))
    sent = _message_sent_at(message_date)
    if sent is None:
        return True
    return bool((date_from_dt and sent < date_from_dt) or (date_to_dt and sent > date_to_dt))


def _effective_received(s3_key: str, message_date: str, last_modified: datetime) -> datetime:
    """When a message arrived, for ordering results: LastModified for SES-delivered
    mail; for a trusted legacy import (its LastModified is the import time) the
    Date header, bounded by its received-date partition when it is in the date
    tree. An unreadable legacy Date outside the tree sorts as the oldest."""
    if not trusted_legacy_key(s3_key):
        return last_modified
    sent = _message_sent_at(message_date)
    day = _partition_day(s3_key)
    if day is not None:
        # In the date tree the partition is the received day: the Date header
        # orders messages within it only when it agrees with that day.
        return sent if sent is not None and day <= sent < day + timedelta(days=1) else day
    return sent or datetime.min.replace(tzinfo=UTC)


def _is_legacy_prefix(prefix: str) -> bool:
    return prefix in EMAIL_TRUSTED_LEGACY_PREFIXES


def parse_date_bound(date_str: str | None, is_end: bool) -> datetime | None:
    """Parse ISO date/datetime bounds with sensible date-only behavior.

    - Date-only start bound (YYYY-MM-DD): 00:00:00 UTC
    - Date-only end bound (YYYY-MM-DD):   23:59:59.999999 UTC
    - Datetime bounds preserve provided time; naive datetimes assume UTC.
    """
    if not date_str:
        return None

    if "T" in date_str:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    dt = datetime.fromisoformat(date_str)
    if is_end:
        return datetime(dt.year, dt.month, dt.day, 23, 59, 59, 999999, tzinfo=UTC)
    return datetime(dt.year, dt.month, dt.day, 0, 0, 0, 0, tzinfo=UTC)


def resolve_search_bounds(
    date_from_dt: datetime | None, date_to_dt: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve effective search bounds for date-partitioned scans.

    When date partitions are configured and only date_from is provided,
    use current time as date_to to keep prefix listing bounded.
    """
    if not DATE_PREFIX_FORMAT:
        return date_from_dt, date_to_dt

    if date_from_dt is not None and date_to_dt is None:
        return date_from_dt, datetime.now(UTC)

    return date_from_dt, date_to_dt


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


async def fetch_email_headers(s3_client, s3_key: str) -> dict[str, str]:
    """Fetch and parse email headers only using an S3 range request."""
    return _parse_email_headers(await _fetch_header_chunk(s3_client, s3_key))


async def _fetch_header_chunk(s3_client, s3_key: str) -> bytes:
    response = await s3_client.get_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Range=f"bytes=0-{HEADER_FETCH_BYTES - 1}",
    )
    return await response["Body"].read()


def _parse_email_headers(raw_chunk: bytes) -> dict[str, str]:
    parser = BytesHeaderParser()
    msg = parser.parsebytes(raw_chunk)

    sender = decode_email_header(msg.get("From", ""))
    return {
        "subject": decode_email_header(msg.get("Subject", "")),
        "from": sender,
        "to": decode_email_header(msg.get("To", "")),
        "date": msg.get("Date", ""),
        "authenticated_domain": authenticated_sender_domain(msg.get_all("Authentication-Results") or [], sender) or "",
    }


# -- authenticated sender ------------------------------------------------------------
# A From header is text anyone can write: "From: reports@vendor.com" on a mail
# from anywhere used to be selected as that vendor's report. The receiving MTA
# (SES inbound) records what it verified in an Authentication-Results header it
# PREPENDS, so the topmost header carrying its authserv-id is the receiver's
# verdict; anything below it came with the message and is ignored.
EMAIL_AUTHSERV_ID = os.environ.get("EMAIL_AUTHSERV_ID", "amazonses.com").strip().lower() or "amazonses.com"
# "required" (default): sender_contains selects only mail whose From domain the
# receiver authenticated (DMARC pass, or an aligned SPF / DKIM pass) and the
# filter text matches. "off": the old header-substring match, for an inbox whose
# receiver adds no Authentication-Results (documented fallback; every search
# then carries an unauthenticated_sender_match warning).
EMAIL_SENDER_AUTH = os.environ.get("EMAIL_SENDER_AUTH", "required").strip().lower() or "required"

_AUTH_METHOD_RE = re.compile(r"^(dmarc|dkim|spf)\s*=\s*([a-z]+)\b(.*)$", re.IGNORECASE | re.DOTALL)
_AUTH_PROP_RE = re.compile(
    r"\b(header\.from|header\.d|header\.i|smtp\.mailfrom|smtp\.from|envelope-from)\s*=\s*"
    r'((?:"(?:[^"\\]|\\.)*"|[^\s;"])+)',
    re.IGNORECASE,
)


def _split_resinfo(text: str) -> list[str]:
    """Split an Authentication-Results value on ';' the RFC 8601 / 5322 way:
    never inside a "quoted-string" (backslash escapes honoured) or a (comment),
    which may nest; comments are dropped. A quoted MAIL FROM local part such as
    "x;dmarc=pass;header.from=vendor.example"@attacker.example therefore stays
    one value and cannot inject a verdict."""
    items: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote = False
    escaped = False
    for ch in text:
        if escaped:
            if not depth:
                current.append(ch)
            escaped = False
            continue
        if ch == "\\" and (in_quote or depth):
            escaped = True
            if not depth:
                current.append(ch)
            continue
        if in_quote:
            current.append(ch)
            if ch == '"':
                in_quote = False
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")" and depth:
            depth -= 1
            continue
        if depth:
            continue
        if ch == '"':
            in_quote = True
            current.append(ch)
        elif ch == ";":
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return items


def idna_domain(domain: str) -> str:
    """``domain`` in its canonical ASCII (IDNA A-label) form, lower-cased:
    "bücher.example" and "xn--bcher-kva.example" compare equal.

    Encoded with the ``idna`` package (IDNA 2008 / UTS 46, NON-transitional:
    "faß.de" is xn--fa-hia.de, never fass.de), a dependency of httpx. Python's
    built-in codec (IDNA 2003) is never used: it maps "ß" to "ss" and would let
    faß.de align with fass.de. When the package is missing, or the domain
    cannot be encoded, a non-ASCII domain is only lower-cased: it then equals
    no A-label, so it fails closed (no SPF/DKIM alignment and no keyword match;
    a receiver's dmarc=pass still counts)."""
    domain = str(domain or "").strip().strip(".")
    if domain.isascii():
        return domain.lower()
    if _idna_pkg is not None:
        try:
            return _idna_pkg.encode(domain, uts46=True, transitional=False).decode("ascii").lower()
        except (_idna_pkg.IDNAError, UnicodeError, ValueError):
            pass
    return domain.lower()


def _canonical_mailbox(mailbox: str) -> str:
    """local@domain with the domain IDNA-canonicalised (local part lower-cased)."""
    local, at, domain = str(mailbox or "").strip().lower().rpartition("@")
    return f"{local}@{idna_domain(domain)}" if at else idna_domain(domain)


def _address_domain(value: str) -> str:
    address = parseaddr(value)[1] if "<" in value or " " in value.strip() else value.strip()
    return idna_domain(address.rpartition("@")[2])


def _aligned(auth_domain: str, from_domain: str) -> bool:
    """SPF / DKIM fallback alignment: the authenticated domain must BE the From
    domain, exactly. Without a public-suffix list this module cannot tell an
    organizational domain from a shared suffix, so neither a subdomain
    (attacker.github.io for github.io, tenant.onmicrosoft.com) nor a parent
    (co.id) counts: either could be controlled by an unrelated party. Relaxed
    alignment (parent, subdomain, sibling) is accepted only through the
    receiver's own dmarc=pass, which SES computes with the real public-suffix
    list; this module never guesses an organizational domain."""
    if not auth_domain or not from_domain:
        return False
    return auth_domain == from_domain


def _auth_results(text: str) -> list[tuple[str, str, list[str]]]:
    """``[(method, result, [property domains])]`` from one Authentication-Results.

    SES writes a method's properties as further ``;``-separated items
    (``spf=pass (comment) client-ip=...; envelope-from=a@vendor.example;
    helo=...``), so an item that does not start a new method belongs to the
    one before it."""
    found: list[tuple[str, str, list[str]]] = []
    for item in _split_resinfo(text)[1:]:
        item = item.strip()
        match = _AUTH_METHOD_RE.match(item)
        if match:
            method, result, rest = match.groups()
            found.append(
                (method.lower(), result.lower(), [_address_domain(v) for _k, v in _AUTH_PROP_RE.findall(rest)])
            )
        elif found:
            found[-1][2].extend(_address_domain(v) for _k, v in _AUTH_PROP_RE.findall(item))
    return found


def authenticated_sender_domain(auth_results: list[str], from_header: str) -> str | None:
    """The From domain when the receiver authenticated it, else None.

    Reads only the topmost Authentication-Results whose authserv-id is
    EMAIL_AUTHSERV_ID. Accepted: dmarc=pass for the From domain (relaxed
    alignment, decided by the receiver), or, only when DMARC is absent or
    dmarc=none, dkim=pass / spf=pass for exactly the From domain."""
    from_domain = _address_domain(str(from_header or ""))
    if not from_domain:
        return None
    for header in auth_results:
        text = " ".join(str(header or "").split())
        authserv = _split_resinfo(text)[0].strip().split()
        if not authserv or authserv[0].lower() != EMAIL_AUTHSERV_ID:
            continue
        results = _auth_results(text)
        dmarc = [result for method, result, _domains in results if method == "dmarc"]
        for method, result, domains in results:
            if method == "dmarc" and result == "pass" and (not domains or from_domain in domains):
                return from_domain
        # The SPF / DKIM fallback applies only when the receiver gave NO DMARC
        # verdict or dmarc=none (no policy published). Any other DMARC result —
        # fail (e.g. strict adkim=s rejecting a subdomain signature),
        # quarantine / reject, temperror / permerror — is final: a weaker
        # fallback never overrides it.
        if any(result != "none" for result in dmarc):
            return None
        for method, result, domains in results:
            if method in ("dkim", "spf") and result == "pass" and any(_aligned(d, from_domain) for d in domains):
                return from_domain
        return None  # only the receiver's own (topmost) verdict counts
    return None


def sender_authenticated(headers: dict[str, str]) -> bool:
    """True when the receiver authenticated the From mailbox's own domain."""
    domain = str(headers.get("authenticated_domain") or "").lower()
    return bool(domain) and domain == _address_domain(str(headers.get("from", "")))


# Hook for bundles whose bucket also holds IMPORTED legacy mail (migrated
# objects that never passed through SES receiving and so carry no SES
# Authentication-Results). Default off. EMAIL_TRUSTED_LEGACY_PREFIXES is a
# comma-separated list of S3 key prefixes the OPERATOR vouches for (e.g. a
# one-time "emails/legacy-import/" migration): objects under one are treated
# as a pre-authenticated archive. Nothing arriving via SES lands there, so
# keep the prefix outside the receipt rule's write path.
EMAIL_TRUSTED_LEGACY_PREFIXES = tuple(
    p.strip() for p in os.environ.get("EMAIL_TRUSTED_LEGACY_PREFIXES", "").split(",") if p.strip()
)


def trusted_legacy_key(s3_key: str) -> bool:
    return bool(s3_key) and any(s3_key.startswith(prefix) for prefix in EMAIL_TRUSTED_LEGACY_PREFIXES)


def message_trusted(s3_key: str, headers: dict[str, str]) -> bool:
    """Authenticated by the receiver, or under an operator-trusted legacy prefix."""
    return sender_authenticated(headers) or trusted_legacy_key(s3_key)


def _sender_needles(sender_filter: str) -> list[str]:
    """Comma-separated items, lowercased; "@vendor.com" is the domain "vendor.com"."""
    needles = []
    for item in sender_filter.split(","):
        item = item.strip().lower()
        if item.startswith("@") and item.count("@") == 1:
            item = item[1:]
        if item:
            needles.append(item)
    return needles


# A fuzzy keyword shaped like a sender: the WHOLE keyword is a mailbox
# ("reports@vendor.example", "@vendor.example") or a domain name (two or more
# LDH labels, alphabetic TLD: "vendor.example"). "version 2.0", "2.0" or
# "report @ 10am" are not, and keep matching subject / body text.
_LDH_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD_RE = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{1,59}")


def sender_shaped(keyword: str) -> bool:
    """True when the WHOLE keyword is a mailbox ("reports@vendor.example",
    "@vendor.example") or a domain name: two or more LDH labels whose last is
    alphabetic or a punycode ("xn--") label. Unicode domains are IDNA-encoded
    first ("bücher.example", "отчёты.рф"). Version-like or numeric keywords
    ("2.0", "v1.2.3") and anything with spaces ("report @ 10am") are not."""
    k = str(keyword or "").strip()
    if not k or any(ch.isspace() for ch in k):
        return False
    domain = k.rpartition("@")[2] if "@" in k else k
    if not domain or "@" in domain:
        return False
    ascii_domain = idna_domain(domain)
    if not ascii_domain.isascii():
        return False
    labels = ascii_domain.split(".")
    if len(labels) < 2 or not all(_LDH_LABEL_RE.fullmatch(label) for label in labels):
        return False
    return bool(_TLD_RE.fullmatch(labels[-1]))


def mailbox_matches(needle: str, mailbox: str) -> bool:
    """One sender filter item against a parsed mailbox: an item with "@" must be
    the whole mailbox; anything else is a DOMAIN, matched as the mailbox domain
    or a subdomain of it (so "ssp.example" matches reports@mail.ssp.example, never
    notssp.example, ssp.example.evil.example or ssp.example@attacker.example)."""
    # Both sides IDNA-canonicalised: a Unicode keyword ("bücher.example")
    # matches an A-label header (xn--bcher-kva.example) and the reverse.
    mailbox = _canonical_mailbox(mailbox)
    if "@" in needle:
        return mailbox == _canonical_mailbox(needle)
    needle = idna_domain(needle)
    domain = mailbox.rpartition("@")[2]
    return bool(domain) and bool(needle) and (domain == needle or domain.endswith("." + needle))


def sender_filter_matches(
    sender_filter: str, headers: dict[str, str], s3_key: str = "", *, require_trust: bool = True
) -> bool:
    """Does ``sender_filter`` select this mail?

    ``sender_filter`` is one or more comma-separated items, each a sender DOMAIN
    ("ssp.example") or a full mailbox ("reports@ssp.example"); see
    :func:`mailbox_matches`. Under EMAIL_SENDER_AUTH=required the item is matched
    against the parsed From MAILBOX only (never the display name), and that
    mailbox's domain must be the one the receiver authenticated. Under "off" it is
    the old case-insensitive substring match on the whole header."""
    needles = _sender_needles(sender_filter)
    if EMAIL_SENDER_AUTH == "off":
        header = str(headers.get("from", "")).lower()
        return any(n in header for n in needles)
    # Only a full mailbox or a multi-label domain counts ("com", "media" or
    # "exchange" would match every authenticated mailbox under that TLD); any
    # other entry is ignored, and a filter of only such entries matches nothing.
    needles = [n for n in needles if sender_shaped(n)]
    mailbox = parseaddr(str(headers.get("from", "")))[1].lower()
    matched = bool(mailbox) and any(mailbox_matches(n, mailbox) for n in needles)
    return matched and (not require_trust or message_trusted(s3_key, headers))


def ignored_sender_entries(sender_filter: str | None) -> list[str]:
    """The sender_contains entries that are neither a full mailbox nor a
    multi-label domain, which the authenticated match ignores ([] in off mode)."""
    if not sender_filter or EMAIL_SENDER_AUTH == "off":
        return []
    return [n for n in _sender_needles(sender_filter) if not sender_shaped(n)]


def include_unauthenticated() -> bool:
    """Whether a search may return mail whose sender the receiver did not authenticate."""
    return EMAIL_SENDER_AUTH == "off"


async def fetch_email_headers_batch(
    s3_client,
    objects: list[dict[str, Any]],
    max_concurrency: int,
) -> list[tuple[dict[str, Any], dict[str, str] | None, Exception | None]]:
    """Fetch headers for a batch of S3 objects with bounded concurrency.

    S3 errors (AccessDenied, throttling, an outage) propagate so the search
    fails; only a message whose headers cannot be parsed is returned with
    its error, to be skipped."""
    if not objects:
        return []

    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def fetch_one(obj: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str] | None, Exception | None]:
        async with semaphore:
            raw_chunk = await _fetch_header_chunk(s3_client, obj["Key"])
            try:
                return obj, _parse_email_headers(raw_chunk), None
            except Exception as exc:  # noqa: BLE001 - skip this message only
                return obj, None, exc

    tasks = [fetch_one(obj) for obj in objects]
    return await asyncio.gather(*tasks)


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
    """Get full details of a specific email. Requires s3_key from search_emails().

    Use search_emails() FIRST to find emails, then use the s3_key from results here.
    Returns full email content including body preview and attachment list.

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Args:
        s3_key: The S3 object key from search_emails() results.

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
    """Download an attachment from an email. Use after search_emails() and get_email().

    Workflow: search_emails() → get_email() → download_attachment()

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Args:
        s3_key: The S3 object key from search_emails() results.
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
async def search_emails(
    subject_contains: str | None = None,
    subject_keywords: list[str] | None = None,
    sender_contains: str | None = None,
    recipient_contains: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_payload: bool | None = None,
    max_results: int = 25,
    include_body_preview: bool = False,
) -> dict[str, Any]:
    """RECOMMENDED: Search emails by subject, sender, date, or payload presence.

    START HERE when you need to find emails. This is the most efficient way to
    locate specific emails. Returns matching emails with metadata including s3_key
    (needed for get_email and download_attachment).

    PERFORMANCE GUIDANCE (important for large inboxes):
    - date_from and date_to are REQUIRED for every search.
    - Searches spanning more than 3 calendar days are rejected.
    - Prefer sender_contains + small max_results for source-specific queries.
    - Avoid broad multi-day windows during initial discovery.
    - Expand windows only when required evidence is missing.
    - Avoid body previews unless needed (`include_body_preview=False` is faster).

    DAY-BOUNDED RETRIEVAL PATTERNS (recommended):
    - Weekly WoW selection: run separate 1-day queries in sequence.
      Example order per source:
      1) current day (today),
      2) fallback current day-1,
      3) prior target day (selected current end date - 7),
      4) fallback prior target day-1.
    - "Most recent report" retrieval: start with a 1-day query for today,
      then step back one day at a time until a usable report is found.

    ANTI-PATTERN (discouraged, but still allowed when explicitly needed):
    - Do not start with wide ranges (for example 7-14 day windows) when selecting
      current/prior report candidates. Wide ranges increase scan volume and can
      produce overlapping-week selections.

    Date-partition behavior:
    - If EMAIL_S3_DATE_PREFIX_FORMAT is configured and only date_from is provided,
      date_to is auto-resolved to now to keep searches bounded.

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Args:
        subject_contains: Case-insensitive substring match in subject line
        subject_keywords: Case-insensitive list of keywords to find in subject (matches any by default)
        sender_contains: The sender's DOMAIN ("ssp.example", also matching its
            subdomains) or full mailbox ("reports@ssp.example"); several as a comma-separated
            list ("exchange.example,mail.exchange.example"). Matched against the
            authenticated From mailbox, never the display name; a bare name like
            "exchange" matches nothing.
        recipient_contains: Case-insensitive substring match in To recipients
        date_from: Only emails received on or after this date (ISO format: "2026-01-20")
        date_to: Only emails received on or before this date (ISO format: "2026-01-27")
        has_payload: Filter by presence of a fetchable payload — either a MIME
            attachment OR an in-body download link. True requires at least one;
            False requires neither; None (default) does not filter. Vendors split
            roughly evenly between attaching report files and shipping a download
            link in the body, and this flag abstracts over both delivery modes so
            you do not need per-vendor knowledge to find report emails.
        max_results: Maximum number of results to return (default 25)
        include_body_preview: Include first 500 chars of body (default False)

    Returns:
        Dictionary with matching emails and their metadata. Each match reports
        attachment_names, has_download_links, and (when present) the resolved
        download_links list, so you can see what was actually delivered before
        calling get_email or download_attachment.

    Examples:
        # Day-bounded query for today's candidates (recommended)
        search_emails(
            sender_contains="exchange.example,mail.exchange.example",
            subject_keywords=["Daily Performance", "Weekly Delivery"],
            date_from="2026-03-23",
            date_to="2026-03-23",
            has_payload=True,
            max_results=3,
        )

        # Prior-week target day query (run separately from current-day query)
        search_emails(
            sender_contains="exchange.example,mail.exchange.example",
            has_payload=True,
            date_from="2026-03-16",
            date_to="2026-03-16",
            max_results=3,
        )
    """
    try:
        started_at = asyncio.get_running_loop().time()

        # Remove empty keywords early (helps avoid accidental broad scans).
        if subject_keywords is not None:
            subject_keywords = [kw for kw in subject_keywords if kw and kw.strip()]
            if len(subject_keywords) == 0:
                subject_keywords = None

        search_warnings = _build_search_warnings(
            subject_contains=subject_contains,
            subject_keywords=subject_keywords,
            sender_contains=sender_contains,
            recipient_contains=recipient_contains,
            date_from=date_from,
            date_to=date_to,
            has_payload=has_payload,
            max_results=max_results,
        )

        # Hard-stop obviously invalid text filters that often cause expensive, irrelevant scans.
        high_invalid_filter_codes = {
            "sender_filter_not_meaningful",
            "recipient_filter_not_meaningful",
        }
        invalid_filter_warnings = [
            warning for warning in search_warnings if warning["code"] in high_invalid_filter_codes
        ]
        if invalid_filter_warnings:
            return {
                "status": "error",
                "error": "Query validation failed: non-meaningful sender/recipient filter.",
                "search_warnings": invalid_filter_warnings,
                "search_criteria": {
                    "subject_contains": subject_contains,
                    "subject_keywords": subject_keywords,
                    "sender_contains": sender_contains,
                    "recipient_contains": recipient_contains,
                    "date_from": date_from,
                    "date_to": date_to,
                    "has_payload": has_payload,
                },
            }

        # Parse and resolve date filters
        date_from_dt = parse_date_bound(date_from, is_end=False)
        date_to_dt = parse_date_bound(date_to, is_end=True)
        if date_from_dt is None or date_to_dt is None:
            return {
                "status": "error",
                "error": "Query validation failed: search_emails requires both date_from and date_to.",
                "search_warnings": [
                    {
                        "code": "bounded_date_window_required",
                        "severity": "high",
                        "message": "search_emails requires both date_from and date_to.",
                        "suggestion": "Use deterministic 1-day searches in sequence. At most 3 calendar days are allowed per search.",
                    }
                ],
                "search_criteria": {
                    "subject_contains": subject_contains,
                    "subject_keywords": subject_keywords,
                    "sender_contains": sender_contains,
                    "recipient_contains": recipient_contains,
                    "date_from": date_from,
                    "date_to": date_to,
                    "has_payload": has_payload,
                    "max_results": max_results,
                },
            }

        if date_to_dt < date_from_dt:
            date_from_dt, date_to_dt = date_to_dt, date_from_dt

        window_days = (date_to_dt.date() - date_from_dt.date()).days + 1
        if window_days > 3:
            # Accept the wide window but scan it day-by-day under the hood.
            # build_search_prefixes already emits one S3 prefix per day, so the
            # work is the same as N independent 1-day calls — and the agent
            # avoids the error/retry loop that used to cost round trips when
            # prompts supplied lookback_days > 3. Hard cap stays at
            # MAX_DATE_PREFIX_DAYS (62); anything wider falls back to the
            # unpartitioned prefix scan, which we still want to warn about.
            search_warnings.append(
                {
                    "code": "date_window_auto_chunked",
                    "severity": "medium" if window_days <= MAX_DATE_PREFIX_DAYS else "high",
                    "message": (
                        f"Window spans {window_days} days; scanning one S3 prefix per day."
                        if window_days <= MAX_DATE_PREFIX_DAYS
                        else f"Window spans {window_days} days; exceeds {MAX_DATE_PREFIX_DAYS}-day partitioned budget — falling back to a broad scan."
                    ),
                    "suggestion": "Prefer targeted 1-day searches for recurring report selection; wide windows are allowed but cost more scan budget.",
                }
            )

        date_from_dt, date_to_dt = resolve_search_bounds(date_from_dt, date_to_dt)
        auto_bounded_date_to_now = bool(DATE_PREFIX_FORMAT and date_from and not date_to and date_to_dt is not None)

        # Normalize search strings
        subject_lower = subject_contains.lower() if subject_contains else None
        sender_lower = sender_contains.lower() if sender_contains else None
        recipient_lower = recipient_contains.lower() if recipient_contains else None
        keywords_lower = [k.lower() for k in subject_keywords] if subject_keywords else None

        matching_emails = []
        unauthenticated_skipped = 0
        unauthenticated_unchecked = 0
        emails_scanned = 0
        header_fetches = 0
        full_fetches = 0
        parse_errors = 0

        logger.info(
            "search_emails start: recipient=%s sender=%s subject=%s keywords=%s date_from=%s date_to=%s max_results=%s",
            recipient_contains,
            sender_contains,
            subject_contains,
            subject_keywords,
            date_from,
            date_to,
            max_results,
        )

        search_prefixes = build_search_prefixes(date_from_dt, date_to_dt)
        using_partition_prefixes = not (len(search_prefixes) == 1 and search_prefixes[0] == S3_PREFIX)
        logger.info("search_emails prefixes: count=%s sample=%s", len(search_prefixes), search_prefixes[:3])

        async with session.client("s3", region_name=AWS_REGION) as s3_client:
            paginator = s3_client.get_paginator("list_objects_v2")

            # Date partitions are listed newest day first. A partition is always
            # scanned in FULL (its keys are not in time order), keeping the
            # newest max_results; once the limit is reached, only the older
            # partitions after it are skipped. Trusted legacy prefixes are not
            # time-ordered either (and come last): always scanned in full. The
            # limit is applied after one global sort. Without partitions (an
            # unbounded or very wide window) the scan still stops at the limit.
            stop_search = False
            seen_keys: set[str] = set()  # overlapping prefixes never yield one object twice
            for prefix in search_prefixes:
                legacy_scan = using_partition_prefixes and _is_legacy_prefix(prefix)
                if stop_search and not legacy_scan:
                    continue
                async for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
                    if "Contents" not in page:
                        continue

                    page_objects: list[dict[str, Any]] = []
                    for obj in page["Contents"]:
                        if obj["Key"] == prefix or obj["Key"] in seen_keys:
                            continue
                        seen_keys.add(obj["Key"])
                        # Skip link-archive payloads/manifests, which live
                        # under the email prefix but are not emails.
                        if obj["Key"].startswith(ARCHIVE_PREFIX):
                            continue

                        last_modified = obj["LastModified"]
                        if not using_partition_prefixes and not trusted_legacy_key(obj["Key"]):
                            if date_from_dt and last_modified < date_from_dt:
                                continue
                            if date_to_dt and last_modified > date_to_dt:
                                continue

                        page_objects.append(obj)

                    header_results = await fetch_email_headers_batch(
                        s3_client,
                        page_objects,
                        SEARCH_HEADER_FETCH_CONCURRENCY,
                    )

                    for obj, headers, header_error in header_results:
                        emails_scanned += 1

                        if header_error is not None or headers is None:
                            parse_errors += 1
                            logger.warning(f"Failed to parse email {obj['Key']}: {header_error}")
                            continue

                        header_fetches += 1
                        subject = headers["subject"]
                        sender = headers["from"]
                        recipients = headers["to"]
                        message_date = headers["date"]
                        last_modified = obj["LastModified"]
                        if _legacy_outside_window(obj["Key"], message_date, date_from_dt, date_to_dt):
                            continue

                        if SEARCH_PROGRESS_EVERY > 0 and emails_scanned % SEARCH_PROGRESS_EVERY == 0:
                            logger.info(
                                "search_emails progress: scanned=%s matched=%s header_fetches=%s full_fetches=%s",
                                emails_scanned,
                                len(matching_emails),
                                header_fetches,
                                full_fetches,
                            )

                        if SEARCH_KEY_LOG_EVERY > 0 and emails_scanned % SEARCH_KEY_LOG_EVERY == 0:
                            logger.info(
                                "search_emails key: scanned=%s key=%s size_bytes=%s received_at=%s",
                                emails_scanned,
                                obj["Key"],
                                obj.get("Size", 0),
                                last_modified.isoformat(),
                            )

                        if subject_lower and subject_lower not in subject.lower():
                            continue

                        if keywords_lower:
                            subject_check = subject.lower()
                            if not any(kw in subject_check for kw in keywords_lower):
                                continue

                        # Every search, filtered or broad, returns only mail whose
                        # sender the receiver authenticated (EMAIL_SENDER_AUTH=off: all).
                        # The other filters run FIRST, so unauthenticated_skipped
                        # counts only mail that would otherwise have matched.
                        trusted = include_unauthenticated() or message_trusted(obj["Key"], headers)
                        if sender_lower and not sender_filter_matches(
                            sender_lower, headers, obj["Key"], require_trust=False
                        ):
                            continue

                        if recipient_lower and recipient_lower not in recipients.lower():
                            continue
                        if not trusted:
                            # Never fetch or parse an untrusted message's body. It is
                            # counted only when the header filters alone decide the
                            # match: with has_payload set we cannot know, so no count.
                            if has_payload is None:
                                unauthenticated_skipped += 1
                            else:
                                unauthenticated_unchecked += 1
                            continue

                        need_full = has_payload is not None or include_body_preview

                        attachments: list[dict[str, Any]] = []
                        download_links: list[dict[str, Any]] = []
                        body_urls: list[dict[str, Any]] = []
                        body_plain = ""
                        body_html = ""

                        if need_full:
                            # S3 fetch errors (AccessDenied, throttling, an outage)
                            # propagate: the search fails instead of claiming success.
                            response = await s3_client.get_object(Bucket=S3_BUCKET, Key=obj["Key"])
                            raw_email = await response["Body"].read()
                            full_fetches += 1
                            # One malformed message never fails the whole search: only
                            # its MIME parse is caught, logged and skipped.
                            try:
                                msg: Message = await asyncio.get_running_loop().run_in_executor(
                                    None, email.message_from_bytes, raw_email
                                )
                                content = await asyncio.get_running_loop().run_in_executor(
                                    None, _extract_email_content, msg, True
                                )
                            except Exception as exc:  # noqa: BLE001 - skip this message only
                                parse_errors += 1
                                logger.warning(f"Skipped email {obj['Key']}: its body could not be parsed ({exc})")
                                continue

                            attachments = content["attachments"]
                            download_links = content["download_links"]
                            body_urls = content["body_urls"]
                            body_plain = content["body_plain"]
                            body_html = content["body_html"]

                        if has_payload is not None:
                            payload_count = len(attachments) + len(download_links)
                            if has_payload and payload_count == 0:
                                continue
                            if not has_payload and payload_count > 0:
                                continue

                        email_result = {
                            "s3_key": obj["Key"],
                            "subject": subject,
                            "from": sender,
                            "sender_authenticated": sender_authenticated(headers),
                            "trusted_legacy_archive": trusted_legacy_key(obj["Key"]),
                            "to": recipients,
                            "date": message_date,
                            "received_at": last_modified.isoformat(),
                            "size_bytes": obj["Size"],
                            "has_attachments": len(attachments) > 0,
                            "attachment_count": len(attachments),
                            "attachment_names": [att["filename"] for att in attachments],
                            "has_download_links": len(download_links) > 0,
                            "download_link_count": len(download_links),
                            "body_url_count": len(body_urls),
                        }

                        if download_links:
                            email_result["download_links"] = download_links

                        if include_body_preview:
                            preview = body_plain[:500] if body_plain else body_html[:500]
                            email_result["body_preview"] = preview
                            # Surface the structured URL list alongside
                            # the preview, so an agent can triage
                            # wrapper formats our classifier missed
                            # without re-fetching the email.
                            if body_urls:
                                email_result["body_urls"] = body_urls

                        email_result["_sort_at"] = _effective_received(obj["Key"], message_date, last_modified)
                        matching_emails.append(email_result)

                        if len(matching_emails) >= max_results:
                            stop_search = True
                            if not using_partition_prefixes:
                                break
                            if len(matching_emails) > max_results:
                                matching_emails.sort(key=lambda x: x["_sort_at"], reverse=True)
                                del matching_emails[max_results:]

                    if stop_search and not using_partition_prefixes:
                        break

        matching_emails.sort(key=lambda x: x["_sort_at"], reverse=True)
        del matching_emails[max_results:]
        for match in matching_emails:
            match.pop("_sort_at", None)

        elapsed_seconds = round(asyncio.get_running_loop().time() - started_at, 3)
        logger.info(
            "search_emails done: scanned=%s matched=%s header_fetches=%s full_fetches=%s parse_errors=%s elapsed_s=%s",
            emails_scanned,
            len(matching_emails),
            header_fetches,
            full_fetches,
            parse_errors,
            elapsed_seconds,
        )

        if unauthenticated_skipped:
            search_warnings.append(
                {
                    "code": "unauthenticated_sender_skipped",
                    "count": unauthenticated_skipped,
                    "severity": "high",
                    "message": (
                        f"{unauthenticated_skipped} email(s) were left out: the receiving server did not "
                        "authenticate their sender (no DMARC / aligned SPF or DKIM pass for the From domain), so "
                        "their From header may be forged."
                    ),
                    "suggestion": "Do not treat those as the vendor's report. If the vendor's mail really fails "
                    "authentication, the operator can set EMAIL_SENDER_AUTH=off (header matching) for this inbox.",
                }
            )
        if unauthenticated_unchecked:
            search_warnings.append(
                {
                    "code": "unauthenticated_sender_unchecked",
                    "count": unauthenticated_unchecked,
                    "severity": "high",
                    "message": (
                        f"{unauthenticated_unchecked} email(s) matched the header filters but were left out "
                        "unopened: the receiving server did not authenticate their sender, so whether they "
                        "carry a payload was not checked."
                    ),
                    "suggestion": "Do not treat those as the vendor's report. If the vendor's mail really fails "
                    "authentication, the operator can set EMAIL_SENDER_AUTH=off (header matching) for this inbox.",
                }
            )
        if EMAIL_SENDER_AUTH == "off":
            search_warnings.append(
                {
                    "code": "unauthenticated_sender_match",
                    "severity": "medium",
                    "message": "EMAIL_SENDER_AUTH=off: results include mail whose sender was not authenticated, "
                    "and sender_contains matched the From header text, which can be forged.",
                    "suggestion": "Prefer EMAIL_SENDER_AUTH=required on an inbox whose receiver adds "
                    "Authentication-Results.",
                }
            )

        if emails_scanned >= 2000 and len(matching_emails) == 0:
            search_warnings.append(
                {
                    "code": "high_scan_zero_matches",
                    "severity": "high",
                    "message": "Scanned a large volume with zero matches; query is likely too broad or misconfigured.",
                    "suggestion": "Tighten sender/subject filters and confirm date bounds before retrying.",
                }
            )

        return {
            "status": "success",
            "matches_found": len(matching_emails),
            "emails_scanned": emails_scanned,
            "search_stats": {
                "header_fetches": header_fetches,
                "full_fetches": full_fetches,
                "parse_errors": parse_errors,
                "elapsed_seconds": elapsed_seconds,
            },
            "search_criteria": {
                "subject_contains": subject_contains,
                "subject_keywords": subject_keywords,
                "sender_contains": sender_contains,
                "recipient_contains": recipient_contains,
                "date_from": date_from,
                "date_to": date_to,
                "effective_date_from": date_from_dt.isoformat() if date_from_dt else None,
                "effective_date_to": date_to_dt.isoformat() if date_to_dt else None,
                "auto_bounded_date_to_now": auto_bounded_date_to_now,
                "has_payload": has_payload,
            },
            "search_warnings": search_warnings,
            "emails": matching_emails,
        }

    except Exception as e:
        logger.error(f"Failed to search emails: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def search_emails_fuzzy(
    keywords: list[str],
    search_fields: list[str] | None = None,
    match_all: bool = False,
    date_from: str | None = None,
    date_to: str | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """Keyword search when you don't know exact subject. Use search_emails() if you know subject.

    Use this when you have keywords but don't know the exact email subject line.
    Searches across subject, sender, and optionally body text. Keyword matches are
    case-insensitive.

    PERFORMANCE GUIDANCE:
    - Use this as a fallback after exact `search_emails` attempts.
    - Keep date windows bounded with BOTH date_from and date_to.
    - Prefer 1-day window probes in sequence over broad multi-day scans.
    - Avoid `search_fields=["body"]` unless necessary (full reads are slower).
    - Start with small max_results and widen only if required evidence is missing.

    ANTI-PATTERN (discouraged):
    - Avoid starting fuzzy discovery with large date ranges when finding weekly
      current/prior candidates. Try day-bounded exact search first.

    Date-partition behavior:
    - If EMAIL_S3_DATE_PREFIX_FORMAT is configured and only date_from is provided,
      date_to is auto-resolved to now to keep searches bounded.

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    Args:
        keywords: List of keywords to search for
        search_fields: Which fields to search in (default: ["subject", "sender"])
                      Options: "subject", "sender", "body". "sender" is the From
                      MAILBOX only (e.g. reports@ssp.example), never the display
                      name, and is matched like sender_contains: a keyword must be
                      the whole mailbox or a domain ("ssp.example", which also
                      matches its subdomains); a bare company name never matches a
                      sender, so search it ("Example SSP") in the subject. While
                      "sender" is searched, a domain- or address-shaped keyword
                      matches the sender only, never subject or body text.
        match_all: If True, all keywords must match (AND). If False, any keyword (OR)
        date_from: Only emails received on or after this date (ISO format)
        date_to: Only emails received on or before this date (ISO format)
        max_results: Maximum results to return (default 50)

    Returns:
        Dictionary with matching emails sorted by relevance score.

    Examples:
        # Day-bounded fuzzy query (fallback only)
        search_emails_fuzzy(
            keywords=["Daily Performance", "CTV"],
            search_fields=["subject", "sender"],
            match_all=True,
            date_from="2026-03-23",
            date_to="2026-03-23",
            max_results=5,
        )

        # Prior target day fuzzy fallback: senders by domain
        search_emails_fuzzy(
            keywords=["ssp.example", "exchange.example"],
            search_fields=["sender"],
            match_all=False,
            date_from="2026-03-16",
            date_to="2026-03-16",
            max_results=5,
        )
    """
    try:
        started_at = asyncio.get_running_loop().time()

        # Validate keywords early.
        keywords = [kw for kw in keywords if kw and kw.strip()]
        if len(keywords) == 0:
            return {
                "status": "error",
                "error": "keywords must contain at least one non-empty value",
                "search_warnings": [
                    {
                        "code": "empty_keywords",
                        "severity": "high",
                        "message": "Fuzzy search invoked without usable keywords.",
                        "suggestion": "Provide 1-3 concrete keywords or use exact search_emails with sender/subject filters.",
                    }
                ],
            }

        if search_fields is None:
            search_fields = ["subject", "sender"]

        search_warnings = _build_search_warnings(
            subject_contains=None,
            subject_keywords=keywords,
            sender_contains=None,
            recipient_contains=None,
            date_from=date_from,
            date_to=date_to,
            has_payload=None,
            max_results=max_results,
        )

        # Validate search fields
        valid_fields = {"subject", "sender", "body"}
        if not all(field in valid_fields for field in search_fields):
            return {
                "status": "error",
                "error": f"Invalid search_fields. Must be subset of {valid_fields}",
            }

        # Parse and resolve date filters
        date_from_dt = parse_date_bound(date_from, is_end=False)
        date_to_dt = parse_date_bound(date_to, is_end=True)
        date_from_dt, date_to_dt = resolve_search_bounds(date_from_dt, date_to_dt)
        auto_bounded_date_to_now = bool(DATE_PREFIX_FORMAT and date_from and not date_to and date_to_dt is not None)

        # Normalize keywords (case-insensitive)
        keyword_pairs = [(keyword, keyword.lower()) for keyword in keywords]
        search_keywords = [normalized for _, normalized in keyword_pairs]

        matching_emails = []
        unauthenticated_skipped = 0
        emails_scanned = 0
        header_fetches = 0
        full_fetches = 0
        parse_errors = 0

        logger.info(
            "search_emails_fuzzy start: keywords=%s fields=%s match_all=%s date_from=%s date_to=%s max_results=%s",
            keywords,
            search_fields,
            match_all,
            date_from,
            date_to,
            max_results,
        )

        search_prefixes = build_search_prefixes(date_from_dt, date_to_dt)
        using_partition_prefixes = not (len(search_prefixes) == 1 and search_prefixes[0] == S3_PREFIX)
        logger.info("search_emails_fuzzy prefixes: count=%s sample=%s", len(search_prefixes), search_prefixes[:3])

        async with session.client("s3", region_name=AWS_REGION) as s3_client:
            paginator = s3_client.get_paginator("list_objects_v2")

            # Date partitions are listed newest day first. A partition is always
            # scanned in FULL (its keys are not in time order), keeping the
            # newest max_results; once the limit is reached, only the older
            # partitions after it are skipped. Trusted legacy prefixes are not
            # time-ordered either (and come last): always scanned in full. The
            # limit is applied after one global sort. Without partitions (an
            # unbounded or very wide window) the scan still stops at the limit.
            stop_search = False
            seen_keys: set[str] = set()  # overlapping prefixes never yield one object twice
            for prefix in search_prefixes:
                legacy_scan = using_partition_prefixes and _is_legacy_prefix(prefix)
                if stop_search and not legacy_scan:
                    continue
                async for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
                    if "Contents" not in page:
                        continue

                    for obj in page["Contents"]:
                        if obj["Key"] == prefix or obj["Key"] in seen_keys:
                            continue
                        seen_keys.add(obj["Key"])
                        # Skip link-archive payloads/manifests, which live
                        # under the email prefix but are not emails.
                        if obj["Key"].startswith(ARCHIVE_PREFIX):
                            continue

                        # Apply date filters when not using date-partition prefixes
                        last_modified = obj["LastModified"]
                        if not using_partition_prefixes and not trusted_legacy_key(obj["Key"]):
                            if date_from_dt and last_modified < date_from_dt:
                                continue
                            if date_to_dt and last_modified > date_to_dt:
                                continue

                        emails_scanned += 1

                        try:
                            # Fast path: subject/sender-only fuzzy search can be done with
                            # a header-range read. A body search reads the headers first
                            # too: an untrusted message's body is never fetched or parsed
                            # (it is matched on its headers only, then counted as skipped).
                            headers = None
                            if "body" not in search_fields or not include_unauthenticated():
                                headers = await fetch_email_headers(s3_client, obj["Key"])
                                header_fetches += 1
                            header_trusted = headers is not None and (
                                sender_authenticated(headers) or trusted_legacy_key(obj["Key"])
                            )
                            if headers is not None and ("body" not in search_fields or not header_trusted):
                                authenticated = sender_authenticated(headers)
                                subject = headers["subject"]
                                sender = headers["from"]
                                recipients = headers["to"]
                                message_date = headers["date"]
                                body = ""
                                attachments = []
                            else:
                                response = await s3_client.get_object(Bucket=S3_BUCKET, Key=obj["Key"])
                                raw_email = await response["Body"].read()
                                full_fetches += 1
                                msg: Message = email.message_from_bytes(raw_email)

                                # Extract fields to search
                                subject = decode_email_header(msg.get("Subject", ""))
                                sender = decode_email_header(msg.get("From", ""))
                                recipients = decode_email_header(msg.get("To", ""))
                                message_date = msg.get("Date", "")
                                authenticated = sender_authenticated(
                                    {
                                        "from": sender,
                                        "authenticated_domain": authenticated_sender_domain(
                                            msg.get_all("Authentication-Results") or [], sender
                                        )
                                        or "",
                                    }
                                )
                                body = ""

                                for part in msg.walk():
                                    content_type = part.get_content_type()
                                    content_disposition = part.get("Content-Disposition", "")
                                    if "attachment" not in content_disposition and content_type == "text/plain":
                                        payload = part.get_payload(decode=True)
                                        if isinstance(payload, bytes):
                                            body = payload.decode("utf-8", errors="replace")
                                            break

                                # Parse attachments for metadata
                                attachments = []
                                for part in msg.walk():
                                    if part.get_content_maintype() == "multipart":
                                        continue
                                    content_disposition = part.get("Content-Disposition", "")
                                    if "attachment" in content_disposition or part.get_filename():
                                        filename = part.get_filename()
                                        if filename:
                                            filename = decode_email_header(filename)
                                            attachments.append(filename)

                            if SEARCH_PROGRESS_EVERY > 0 and emails_scanned % SEARCH_PROGRESS_EVERY == 0:
                                logger.info(
                                    "search_emails_fuzzy progress: scanned=%s matched=%s header_fetches=%s full_fetches=%s",
                                    emails_scanned,
                                    len(matching_emails),
                                    header_fetches,
                                    full_fetches,
                                )

                            if SEARCH_KEY_LOG_EVERY > 0 and emails_scanned % SEARCH_KEY_LOG_EVERY == 0:
                                logger.info(
                                    "search_emails_fuzzy key: scanned=%s key=%s size_bytes=%s received_at=%s",
                                    emails_scanned,
                                    obj["Key"],
                                    obj.get("Size", 0),
                                    last_modified.isoformat(),
                                )

                            if _legacy_outside_window(obj["Key"], message_date, date_from_dt, date_to_dt):
                                continue
                            # Counted as unauthenticated_skipped only AFTER the keyword
                            # match below: unrelated unauthenticated mail is not counted.
                            trusted = include_unauthenticated() or authenticated or trusted_legacy_key(obj["Key"])

                            # Build searchable text (subject / body: substring match)
                            searchable_parts = []
                            if "subject" in search_fields:
                                searchable_parts.append(subject)
                            if "body" in search_fields:
                                searchable_parts.append(body)

                            searchable_text = " ".join(searchable_parts)
                            searchable_text = searchable_text.lower()
                            # The sender is matched like sender_contains: the parsed
                            # mailbox only (never the display name), a keyword being
                            # a whole mailbox or a domain on a label boundary, never a
                            # substring (vendor.example must not match
                            # reports@vendor.example.evil.example).
                            # EMAIL_SENDER_AUTH=off keeps the documented legacy match: a
                            # substring of the WHOLE From header, display name included.
                            sender_mailbox = parseaddr(sender)[1].lower() if "sender" in search_fields else ""
                            sender_header = str(sender).lower() if "sender" in search_fields else ""

                            def _sender_hit(
                                normalized: str, mailbox: str = sender_mailbox, header: str = sender_header
                            ) -> bool:
                                if include_unauthenticated():
                                    return bool(header) and normalized in header
                                if not mailbox:
                                    return False
                                return mailbox_matches(normalized.strip().lstrip("@"), mailbox)

                            # Check keyword matches. With sender authentication required
                            # and "sender" searched, a domain- or address-shaped keyword
                            # ("vendor.example", "reports@vendor.example") is a SENDER
                            # constraint only: a subject saying "vendor.example" on mail
                            # from attacker@evil.example must not select it.
                            # Conversely a plain word ("media") never matches a sender:
                            # mailbox_matches would read it as a domain suffix
                            # (reports@foo.media). Off mode keeps the whole-From substring.
                            sender_bound = "sender" in search_fields and not include_unauthenticated()
                            matches = []
                            for original, normalized in keyword_pairs:
                                if sender_bound:
                                    shaped = sender_shaped(normalized)
                                    hit = _sender_hit(normalized) if shaped else normalized in searchable_text
                                else:
                                    hit = _sender_hit(normalized) or normalized in searchable_text
                                if hit:
                                    matches.append(original)

                            if match_all:
                                # All keywords must match
                                if len(matches) != len(search_keywords):
                                    continue
                            else:
                                # At least one keyword must match
                                if len(matches) == 0:
                                    continue
                            if not trusted:
                                unauthenticated_skipped += 1
                                continue

                            # Calculate match score (percentage of keywords matched)
                            match_score = len(matches) / len(search_keywords)

                            matching_emails.append(
                                {
                                    "s3_key": obj["Key"],
                                    "subject": subject,
                                    "from": sender,
                                    "to": recipients,
                                    "date": message_date,
                                    "received_at": last_modified.isoformat(),
                                    "size_bytes": obj["Size"],
                                    "has_attachments": len(attachments) > 0,
                                    "attachment_count": len(attachments),
                                    "attachment_names": attachments,
                                    "match_score": match_score,
                                    "matched_keywords": matches,
                                    "sender_authenticated": authenticated,
                                    "trusted_legacy_archive": trusted_legacy_key(obj["Key"]),
                                    "_sort_at": _effective_received(obj["Key"], message_date, last_modified),
                                }
                            )

                            if len(matching_emails) >= max_results:
                                stop_search = True
                                if not using_partition_prefixes:
                                    break
                                if len(matching_emails) > max_results:
                                    matching_emails.sort(key=lambda x: (x["match_score"], x["_sort_at"]), reverse=True)
                                    del matching_emails[max_results:]

                        except Exception as e:
                            parse_errors += 1
                            logger.warning(f"Failed to parse email {obj['Key']}: {e}")
                            continue

                    if stop_search and not using_partition_prefixes:
                        break

        # Sort by match score (highest first), then by date (newest first);
        # the limit applies after the sort, across partitions and legacy prefixes.
        matching_emails.sort(key=lambda x: (x["match_score"], x["_sort_at"]), reverse=True)
        del matching_emails[max_results:]
        for match in matching_emails:
            match.pop("_sort_at", None)

        elapsed_seconds = round(asyncio.get_running_loop().time() - started_at, 3)
        logger.info(
            "search_emails_fuzzy done: scanned=%s matched=%s header_fetches=%s full_fetches=%s parse_errors=%s elapsed_s=%s",
            emails_scanned,
            len(matching_emails),
            header_fetches,
            full_fetches,
            parse_errors,
            elapsed_seconds,
        )

        if unauthenticated_skipped:
            search_warnings.append(
                {
                    "code": "unauthenticated_sender_skipped",
                    "count": unauthenticated_skipped,
                    "severity": "high",
                    "message": f"{unauthenticated_skipped} email(s) were left out: the receiving server did not "
                    "authenticate their sender, so their From header may be forged.",
                    "suggestion": "Only an operator can include them (EMAIL_SENDER_AUTH=off).",
                }
            )
        if EMAIL_SENDER_AUTH == "off":
            search_warnings.append(
                {
                    "code": "unauthenticated_sender_match",
                    "severity": "medium",
                    "message": "EMAIL_SENDER_AUTH=off: results include mail whose sender was not authenticated, "
                    "and the sender text searched can be forged.",
                    "suggestion": "Check each result's sender_authenticated before trusting it as a vendor's report.",
                }
            )

        if emails_scanned >= 2000 and len(matching_emails) == 0:
            search_warnings.append(
                {
                    "code": "high_scan_zero_matches",
                    "severity": "high",
                    "message": "Scanned a large volume with zero fuzzy matches.",
                    "suggestion": "Use sender-refined exact search_emails first, then retry fuzzy with tighter keywords.",
                }
            )

        return {
            "status": "success",
            "matches_found": len(matching_emails),
            "emails_scanned": emails_scanned,
            "search_stats": {
                "header_fetches": header_fetches,
                "full_fetches": full_fetches,
                "parse_errors": parse_errors,
                "elapsed_seconds": elapsed_seconds,
            },
            "search_criteria": {
                "keywords": keywords,
                "search_fields": search_fields,
                "match_all": match_all,
                "date_from": date_from,
                "date_to": date_to,
                "effective_date_from": date_from_dt.isoformat() if date_from_dt else None,
                "effective_date_to": date_to_dt.isoformat() if date_to_dt else None,
                "auto_bounded_date_to_now": auto_bounded_date_to_now,
            },
            "search_warnings": search_warnings,
            "emails": matching_emails,
        }

    except Exception as e:
        logger.error(f"Failed to fuzzy search emails: {e}")
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


def _merge_warning(warnings: list[dict[str, Any]], warning: dict[str, Any], day: str) -> None:
    """One warning per code across the day searches: the days it came from, and
    for counted warnings the summed count with the message rewritten to it."""
    code = warning.get("code")
    for existing in warnings:
        if existing.get("code") == code:
            existing.setdefault("days", []).append(day)
            if isinstance(warning.get("count"), int):
                existing["count"] = int(existing.get("count") or 0) + warning["count"]
                if code == "unauthenticated_sender_skipped":
                    existing["message"] = (
                        f"{existing['count']} email(s) were left out across {len(existing['days'])} day(s): the "
                        "receiving server did not authenticate their sender, so their From header may be forged."
                    )
                elif code == "unauthenticated_sender_unchecked":
                    existing["message"] = (
                        f"{existing['count']} email(s) across {len(existing['days'])} day(s) matched the header "
                        "filters but were left out unopened: the receiving server did not authenticate their sender."
                    )
            return
    warnings.append({**warning, "days": [day]})


@mcp.tool()
async def find_latest_report(
    sender_contains: str | None = None,
    subject_contains: str | None = None,
    subject_keywords: list[str] | None = None,
    recipient_contains: str | None = None,
    on_or_before: str | None = None,
    lookback_days: int = 14,
    require_payload: bool = True,
    max_candidates: int = 1,
) -> dict[str, Any]:
    """Walk back day-by-day from `on_or_before` until a qualifying email is found.

    Use this instead of looping search_emails() yourself when you need "the most
    recent report from vendor X on or before date Y". Stops after `lookback_days`
    or after collecting `max_candidates` matches, whichever comes first.

    Day-by-day stepping keeps each underlying S3 scan narrow, and the total
    number of scanned days is capped at `lookback_days` (hard max 60).

    Args:
        sender_contains: The sender's DOMAIN ("ssp.example", also matching its
            subdomains) or full mailbox ("reports@ssp.example"); several as a comma-separated
            list ("exchange.example,mail.exchange.example"). Matched against the
            authenticated From mailbox, never the display name; a bare name like
            "exchange" matches nothing.
        subject_contains: Case-insensitive substring match on subject.
        subject_keywords: Case-insensitive list of keywords to find in subject.
        recipient_contains: Case-insensitive substring match on recipient.
        on_or_before: ISO date (YYYY-MM-DD) to start walking back from. Defaults
            to today in UTC.
        lookback_days: Maximum number of days to walk back (default 14, hard cap 60).
        require_payload: Require a fetchable payload (attachment or download link).
            Default True matches the typical "find the report" use case.
        max_candidates: Stop after collecting this many matches (default 1).

    Returns:
        status, days_searched, first_match_date (or None), emails (list of
        matches with s3_key, subject, from, received_at, attachment_names,
        and download_links when present).
    """
    try:
        # Resolve on_or_before to a UTC date
        if on_or_before:
            try:
                anchor_dt = datetime.fromisoformat(on_or_before)
                if anchor_dt.tzinfo is None:
                    anchor_dt = anchor_dt.replace(tzinfo=UTC)
            except ValueError:
                return {
                    "status": "error",
                    "error": f"Invalid on_or_before date: {on_or_before!r}. Use ISO format YYYY-MM-DD.",
                }
            anchor_date = anchor_dt.date()
        else:
            anchor_date = datetime.now(UTC).date()

        lookback_days = max(1, min(int(lookback_days), 60))
        max_candidates = max(1, int(max_candidates))

        collected: list[dict[str, Any]] = []
        days_searched = 0
        first_match_date: str | None = None
        payload_flag = True if require_payload else None
        warnings: list[dict[str, Any]] = []

        for offset in range(lookback_days):
            day = anchor_date - timedelta(days=offset)
            day_iso = day.isoformat()
            days_searched += 1

            result = await search_emails(
                subject_contains=subject_contains,
                subject_keywords=subject_keywords,
                sender_contains=sender_contains,
                recipient_contains=recipient_contains,
                date_from=day_iso,
                date_to=day_iso,
                has_payload=payload_flag,
                max_results=max(max_candidates - len(collected), 1),
                include_body_preview=False,
            )

            for warning in result.get("search_warnings") or []:
                _merge_warning(warnings, warning, day_iso)
            if result.get("status") != "success":
                # A failed day (S3 error) must not be read as "no report that
                # day": an older match would then pass for the latest one.
                return {
                    "status": "error",
                    "error": f"Search for {day_iso} failed: {result.get('error', 'unknown error')}",
                    "failed_day": day_iso,
                    "days_searched": days_searched,
                    "anchor_date": anchor_date.isoformat(),
                    "emails": collected,
                    "search_warnings": warnings,
                }

            for match in result.get("emails", []):
                collected.append(match)
                if first_match_date is None:
                    first_match_date = day_iso
                if len(collected) >= max_candidates:
                    break

            if len(collected) >= max_candidates:
                break

        return {
            "status": "success",
            "days_searched": days_searched,
            "anchor_date": anchor_date.isoformat(),
            "first_match_date": first_match_date,
            "match_count": len(collected),
            "emails": collected,
            # The day searches' warnings (e.g. unauthenticated_sender_skipped), one
            # per code, with the days they came from and summed counts in words.
            "search_warnings": warnings,
            "search_criteria": {
                "sender_contains": sender_contains,
                "subject_contains": subject_contains,
                "subject_keywords": subject_keywords,
                "recipient_contains": recipient_contains,
                "on_or_before": anchor_date.isoformat(),
                "lookback_days": lookback_days,
                "require_payload": require_payload,
                "max_candidates": max_candidates,
            },
        }

    except Exception as e:
        logger.error(f"find_latest_report failed: {e}")
        return {"status": "error", "error": str(e)}


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

#!/usr/bin/env python3
"""Amazon SES outbound-email MCP server.

HTML / subject / body validation lives in `email_lint`. This file owns the
provider-neutral tool contract, path security, attachment loading, and
Amazon SES v2 transport.

Tenant identity comes only from the environment. Each bundle manifest maps
that deployment's variables in, so this file stays the same across clients.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
import mimetypes
import os
import re
import secrets
import stat
import sys
import tempfile
import time
import types
from collections.abc import Mapping, MutableMapping, Sequence
from email.utils import parseaddr
from pathlib import PurePath
from typing import Any

import aioboto3
import email_render
from botocore.exceptions import BotoCoreError, ClientError
from email_lint import (
    detect_html_content as _detect_html_content,
)
from email_lint import (
    ensure_mobile_friendly,
    extract_cid_references,
    format_findings,
    partition_findings,
    resolve_content_type,
    validate,
)
from email_lint import (
    extract_cid_references as _extract_cid_references,
)
from email_lint import (
    find_unresolved_template_tokens as _find_unresolved_template_tokens,
)
from email_lint import (
    resolve_content_type as _resolve_content_type,
)
from email_lint import (
    validate_email_body_legacy as _validate_email_body,
)
from email_lint import (
    validate_email_subject_legacy as _validate_email_subject,
)
from email_lint import (
    validate_html_legacy as _validate_html,
)
from mcp.server.fastmcp import FastMCP
from utils import run_ledger, safe_open

# Re-exported by name so the existing test files
# (`test_ses_template_validation.py`, `test_mcp_integration.py`,
# `test_ses_html_structure.py`) keep working. New code should
# import from `email_lint` directly.
__all__ = [
    "_detect_html_content",
    "_extract_cid_references",
    "_find_unresolved_template_tokens",
    "_resolve_content_type",
    "_validate_email_body",
    "_validate_email_subject",
    "_validate_html",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("ses_outbound")
_SESSION = aioboto3.Session()
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_MAX_TOTAL_ATTACHMENT_BYTES = 28 * 1024 * 1024
# A content_file is an email body; SES caps a whole message at 40MB.
_MAX_CONTENT_FILE_BYTES = 20 * 1024 * 1024

# The bundle checkout this server ships inside: mcp/ses_sender.py -> mcp/ -> root.
# Derived from __file__ rather than sniffed for marker files, so it is exact and
# does not shift with whatever cwd fleet happened to spawn the subprocess in.
_BUNDLE_ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


# The allowed roots of ONE tool call, resolved once and pinned (device, inode)
# when the call starts: the path checks and the reads/writes that follow all
# use these, so a root swapped for a symlink in between is refused rather than
# re-resolved to its new target (utils.safe_open.pin_roots).
_CALL_ROOTS: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("ses_call_roots", default=None)


def _pinned_call(fn):
    """Run a tool body with its allowed roots pinned for the whole call."""

    async def wrapper(*args, **kwargs):
        token = _CALL_ROOTS.set(safe_open.pin_roots(_configured_dirs()))
        try:
            return await fn(*args, **kwargs)
        finally:
            _CALL_ROOTS.reset(token)

    rebuilt = types.FunctionType(
        wrapper.__code__, fn.__globals__, fn.__name__, wrapper.__defaults__, wrapper.__closure__
    )
    functools.update_wrapper(rebuilt, fn)
    return rebuilt


def _allowed_dirs() -> list[str]:
    """This call's pinned roots when a tool call is running, else freshly configured ones."""
    pinned = _CALL_ROOTS.get()
    return list(pinned) if pinned is not None else _configured_dirs()


def _configured_dirs() -> list[str]:
    """Host-side roots this server may read attachments from.

    Shared by the security check and the diagnostics so a refused/missing path
    is always described against the same set: the run workdir, cwd unless it is
    the bundle checkout, the process tempdir, and CUTLASS_ALLOWED_DIRS.
    Recovery searches only the run workdir (or cwd when no run workdir was
    handed over); the other roots stay readable for an explicitly named path.
    """
    allowed_dirs: list[str] = []

    # The run workspace fleet hands a managed run. This is the deliberate
    # attachment source: the deal sheets and reports the run just produced.
    run_workdir = os.environ.get("CUTLASS_RUN_WORKDIR", "").strip()
    if run_workdir:
        allowed_dirs.append(os.path.realpath(run_workdir))

    # cwd, but never when it IS the bundle checkout. Older fleets spawned every
    # stdio server with cwd = the bundle root, which made the whole checkout —
    # version-controlled bundle content plus whatever output connectors have
    # accumulated there — readable and attachable to outbound mail. Nothing in
    # that tree is a deliberate attachment source. cwd stays allowed otherwise
    # because these servers are also run by a local coding agent
    # (docs/TESTING-LOCALLY.md), where cwd is the operator's own project dir.
    cwd = os.path.realpath(os.getcwd())
    if cwd != _BUNDLE_ROOT:
        allowed_dirs.append(cwd)

    allowed_dirs.append(os.path.realpath(tempfile.gettempdir()))

    extra = os.environ.get("CUTLASS_ALLOWED_DIRS", "")
    if extra:
        for d in extra.split(":"):
            d = d.strip()
            if d:
                allowed_dirs.append(os.path.realpath(d))

    return list(dict.fromkeys(allowed_dirs))


def _validate_path_security(file_path: str) -> str:
    """Validate that a file path is within allowed directories (see ``_allowed_dirs``).

    Returns the absolute real path if valid.
    Raises ValueError if the path is outside allowed directories or invalid.
    """
    if not file_path:
        raise ValueError("File path cannot be empty")

    allowed_dirs = _allowed_dirs()

    try:
        resolved_path = os.path.expanduser(file_path)
        if not os.path.isabs(resolved_path):
            resolved_path = os.path.abspath(resolved_path)

        # Need to check realpath to avoid symlink bypasses
        real_path = os.path.realpath(resolved_path)

        for allowed_dir in allowed_dirs:
            # ensure it ends with slash so /app doesn't match /apple
            allowed_prefix = allowed_dir if allowed_dir.endswith(os.sep) else allowed_dir + os.sep
            if real_path == allowed_dir or real_path.startswith(allowed_prefix):
                return resolved_path

        roots = ", ".join(allowed_dirs) or "(none configured)"
        raise ValueError(
            f"SECURITY: Path is outside allowed directories: {file_path}. "
            f"Readable roots for content_file/attachments: {roots}. "
            "Write the file under one of them (the run workspace root is allowed on Fleet) — "
            "the sandbox's /tmp is a different filesystem from this server's."
        )
    except Exception as e:
        if isinstance(e, (ValueError, PermissionError)):
            raise
        raise ValueError(f"SECURITY: Invalid path: {e}") from e


def _path_kind(path: str) -> str | None:
    """ "file", "other" (directory, device, dangling symlink...), or None when
    nothing exists at the path. Uses lstat so a dangling symlink counts as
    existing, and lets EACCES surface as PermissionError instead of being
    mistaken for "missing" — a file the server cannot reach must never be
    silently replaced by a same-named one it can."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        # ENOENT on the leaf is only "missing" when every parent component
        # actually exists; a dangling symlink (or a vanished directory) on the
        # way down is a structural error in what was named, never something a
        # same-named twin may stand in for.
        # Walk every ancestor, not just the immediate parent: with "sub" a
        # dangling symlink, "sub/deeper" neither lexists nor exists, so a
        # one-level check would wave "sub/deeper/report.csv" through.
        ancestor = os.path.dirname(path.rstrip(os.sep)) or os.sep
        while True:
            if os.path.lexists(ancestor) and not os.path.exists(ancestor):
                raise FileNotFoundError(
                    f"path component is a dangling symlink or missing directory: {path!r}"
                ) from None
            if os.path.exists(ancestor):
                break  # everything above here is real; the leaf is truly missing
            up = os.path.dirname(ancestor.rstrip(os.sep)) or os.sep
            if up == ancestor:
                break
            ancestor = up
        return None
    except NotADirectoryError:
        # A regular file where a directory was expected on the way down
        # ("archive/report.csv" with "archive" a file) is a structural error in
        # the request, not a missing file: never something to recover from.
        raise FileNotFoundError(f"path component is not a directory: {path!r}") from None
    if stat.S_ISLNK(st.st_mode):
        # Follow it with stat() and read the mode ourselves: a live symlink to
        # a regular file is a file; dangling or pointing at a non-file is
        # "other"; EACCES on the target (isfile() would swallow it) propagates.
        try:
            target = os.stat(path)
        except FileNotFoundError:
            return "other"
        except NotADirectoryError:
            return "other"
        return "file" if stat.S_ISREG(target.st_mode) else "other"
    return "file" if stat.S_ISREG(st.st_mode) else "other"


def _inside_allowed_roots(path: str) -> bool:
    """Containment check only — no filesystem probe beyond realpath's own
    symlink resolution — for deciding whether a location may be inspected."""
    real = os.path.realpath(path)
    for root in _allowed_dirs():
        prefix = root if root.endswith(os.sep) else root + os.sep
        if real == root or real.startswith(prefix):
            return True
    return False


def _attachment_entry_path(entry: Any) -> str:
    """Accept an attachment given as a bare path string or as an object with a
    ``path`` key (the shape fleet's chat path materializes and models emit
    interchangeably). The value is returned EXACTLY as received — no stripping —
    so a stray character reaches the resolver and its diagnostic, and a file
    literally named "report.csv " is never silently swapped for "report.csv".
    Blank values come back as "" for the caller to report."""
    raw = entry.get("path", "") if isinstance(entry, Mapping) else entry
    raw = "" if raw is None else str(raw)
    return raw if raw.strip() else ""


def _locate_attachment(file_path: str, label: str = "Attachment") -> str:
    """Resolve an attachment path for this HOST-SIDE server.

    The model authors paths from inside the sandbox; this server reads them on
    the host. The workspace is mounted at the same absolute path on both sides,
    so an absolute workspace path just works — but a bare file name resolves
    against this process's cwd, which may not be the directory the model wrote
    into, and a typo or stray character produces a path that exists nowhere.
    Both used to fail as a bare "Attachment file not found: <path>" with
    nothing the model could act on, so an unattended run's only move was to
    send the email again WITHOUT the file.

    Recovery is deliberately narrow: when the path does not exist, look for the
    same relative path or base name directly under the RUN WORKDIR (the one
    directory that is this run's own output) — only when the server also has a
    per-run task identity (CUTLASS_MOC_TASK_ID; fleet's shared chat client hands
    every conversation one mcp-shared workdir and no task id, and there recovery
    is off) — or, when fleet handed us no run workdir at all (local coding-agent
    use), under cwd when it is not the bundle checkout. The other readable roots
    (the temp dir, CUTLASS_ALLOWED_DIRS) stay
    readable for a path the caller names explicitly but are never searched: a
    stray /tmp/report.csv or another run's file must not be mailed because it
    happened to share a name. If
    exactly one file matches it is used (and logged); zero or several matches
    raise a FileNotFoundError whose message names the path as received, what it
    resolved to, the roots this server can read, and any candidates, so the
    model can pass the right absolute path on its next call.
    """
    expanded = os.path.expanduser(file_path)
    shared_workdir = (
        bool(os.environ.get("CUTLASS_RUN_WORKDIR", "").strip())
        and not os.environ.get("CUTLASS_MOC_TASK_ID", "").strip()
    )
    if shared_workdir and not os.path.isabs(expanded):
        # fleet's shared chat client: one mcp-shared workdir for every
        # conversation and no task identity. ANY relative name there — plain or
        # traversal — is as likely another conversation's file as this one's,
        # so relative references are not resolved at all, direct or by
        # recovery, and the caller is told to pass the absolute path it wrote.
        raise FileNotFoundError(
            f"{label} path {file_path!r} is relative, but this server runs on a shared workdir "
            "without a per-run task identity, where a relative name cannot be attributed to "
            "this conversation. Pass the absolute path printed by os.path.abspath()."
        )
    # Judged on the NORMALIZED form: "outputs/../report.csv" stays inside cwd and
    # is an ordinary relative reference; only a path that still begins with ".."
    # after normalization actually climbs out.
    escapes_cwd = not os.path.isabs(expanded) and PurePath(os.path.normpath(expanded)).parts[:1] == ("..",)
    if os.path.isabs(expanded) or escapes_cwd:
        # An absolute path — or a relative one that climbs out of cwd — is
        # judged by the allowlist BEFORE anything touches the filesystem, so a
        # caller cannot learn whether an arbitrary host path exists from which
        # error comes back: outside the roots it is refused whether or not it
        # exists, and never enters recovery.
        resolved = _validate_path_security(file_path)
        kind = _path_kind(resolved)  # PermissionError propagates to the caller
        if kind == "file":
            return resolved
        if kind == "other":
            # A directory, socket, device — or a dangling symlink — is not a
            # missing file.
            raise FileNotFoundError(f"{label} path exists but is not a regular file: {file_path!r}")
    else:
        # A plain relative path resolves against cwd. It is used directly only
        # when that location is inside an allowed root; when cwd is the bundle
        # checkout (a supported fleet fallback) the location is not eligible —
        # it is neither probed nor refused, and the run-workdir search below is
        # the one recovery that matters. An existing same-named bundle file
        # therefore cannot defeat recovery, and nothing forbidden is probed.
        resolved = os.path.abspath(expanded)
        if _inside_allowed_roots(os.getcwd()):
            # cwd is a readable root: the location is judged by the allowlist
            # BEFORE any probe — realpath follows symlinked parents too, so
            # "out/missing.csv" with out -> /etc is refused with the SECURITY
            # error whether or not the leaf exists (no existence oracle, and no
            # recovery into a same-named run-workdir file) — then by kind.
            # PermissionError from the probe propagates: an unreachable file is
            # reported, never replaced.
            validated = _validate_path_security(file_path)
            kind = _path_kind(validated)
            if kind == "file":
                return validated
            if kind == "other":
                # A directory, socket, device — or a dangling symlink — at the
                # named path is not a missing file: recovering a same-named
                # file elsewhere would mail something the caller did not name.
                raise FileNotFoundError(f"{label} path exists but is not a regular file: {file_path!r}")
        # Otherwise nothing at the location (or cwd is the bundle checkout,
        # which is neither probed nor refused): the run-workdir search below.

    # Recovery needs a PER-RUN workspace to search. On fleet's dedicated
    # per-run MCP client the server carries the task identity
    # (CUTLASS_MOC_TASK_ID) and CUTLASS_RUN_WORKDIR is that run's own directory;
    # on the shared, process-lifetime client (chat) there is no task id and the
    # workdir is one mcp-shared directory for every conversation, where a
    # same-named file is as likely another conversation's as this one's. Same
    # signal utils/run_ledger uses for its once-per-task guard: without a task
    # id, recovery is off and the caller gets the diagnostic instead.
    run_workdir = os.environ.get("CUTLASS_RUN_WORKDIR", "").strip()
    per_run = bool(os.environ.get("CUTLASS_MOC_TASK_ID", "").strip())
    if run_workdir and per_run:
        search_roots = [os.path.realpath(run_workdir)]
    elif run_workdir:
        search_roots = []  # shared workdir: never guess across runs
    else:
        cwd = os.path.realpath(os.getcwd())
        search_roots = [cwd] if cwd != _BUNDLE_ROOT else []
    roots = _allowed_dirs()
    probes = [os.path.basename(expanded)]
    if not os.path.isabs(expanded):
        probes.insert(0, expanded)
    # Candidates are kept as the MATCHED path (what the recipient sees as the
    # attachment name and what MIME detection reads); the real path is used
    # only for containment and de-duplication, so an allowed symlink such as
    # report.csv -> artifacts/4f2a still arrives as report.csv / text/csv.
    candidates: list[str] = []
    seen_real: set[str] = set()
    for root in search_roots:
        root_prefix = root if root.endswith(os.sep) else root + os.sep
        for probe in probes:
            joined = os.path.join(root, probe)
            exact_probe = probe == probes[0] and not os.path.isabs(expanded)
            # Containment and security verdict FIRST, filesystem probe second:
            # a probe carrying ".." could leave the root before realpath, and a
            # symlink planted in the shared run workspace could point recovery
            # at an arbitrary host path — either would turn the error text into
            # an existence oracle or name an unreadable file. For the caller's
            # own relative path that is a REFUSAL (what they named is invalid,
            # and a same-named twin must not stand in for it); a base-name probe
            # that fails is simply not a candidate.
            try:
                validated = _validate_path_security(joined)
            except ValueError:
                if exact_probe:
                    raise
                continue
            real = os.path.realpath(validated)
            if not (real == root or real.startswith(root_prefix)):
                if exact_probe:
                    raise ValueError(f"SECURITY: Path resolves outside the run workdir: {file_path!r}")
                continue
            # Same probe as the direct branch, on the MATCHED path (so a probe
            # that is itself a dangling symlink reads as "other", not as absent):
            # EACCES propagates (an unreadable location aborts recovery rather
            # than being skipped in favour of a same-named readable file) and
            # ENOTDIR is a structural error too.
            try:
                kind = _path_kind(validated)
            except FileNotFoundError:
                if exact_probe:
                    # The caller's own relative path runs through a regular file
                    # used as a directory: a structural error in what they
                    # named, refused rather than replaced by a base-name twin.
                    raise
                continue  # a base-name probe through a file-as-directory is just not a hit
            if kind == "other" and exact_probe:
                # The caller's own relative path lands on a non-file inside the
                # run workdir: that is what they named, so refuse it rather than
                # sliding on to a same-named regular file by base name.
                raise FileNotFoundError(f"{label} path exists but is not a regular file: {file_path!r}")
            if kind == "file" and real not in seen_real:
                seen_real.add(real)
                candidates.append(validated)

    if len(candidates) == 1:
        recovered = candidates[0]
        logger.warning(
            "%s %r not found at %s; recovered as %s (only same-named file in the run workdir)",
            label,
            file_path,
            resolved,
            recovered,
        )
        return recovered

    roots_text = ", ".join(roots) or "(none configured)"
    detail = (
        f"{label} file not found: {file_path!r} (resolved to {resolved}). "
        f"This server reads files host-side; readable roots: {roots_text}. "
    )
    if run_workdir and not per_run:
        searched = "(not searched: shared run workdir without a per-run task id)"
    else:
        searched = ", ".join(search_roots) or "(none: no run workdir and cwd is the bundle checkout)"
    if candidates:
        detail += (
            f"{len(candidates)} same-named candidates in {searched}, pass one explicitly: {', '.join(candidates)}. "
        )
    else:
        detail += f"No file with that name in the run workdir ({searched}). "
    detail += (
        "Write the file inside the run workspace and pass the absolute path printed by "
        "os.path.abspath(); the sandbox's /tmp is not visible here."
    )
    raise FileNotFoundError(detail)


class SESConfigurationError(RuntimeError):
    """Raised when required SES sender configuration is missing."""


def _resolve_from_email(explicit_from: str | None = None) -> str:
    configured = os.environ.get("SES_FROM_EMAIL", "").strip()
    if explicit_from and configured and explicit_from.strip().lower() != configured.lower():
        raise SESConfigurationError(
            f"from_email must match the configured SES_FROM_EMAIL ({configured}); arbitrary sender identities are disabled."
        )

    sender = (explicit_from or configured).strip()
    if not sender:
        message = "From email address not provided. Pass `from_email` or set SES_FROM_EMAIL in the environment."
        logger.error(message)
        raise SESConfigurationError(message)
    return sender


def _read_confined(path: str, max_bytes: int, label: str) -> tuple[bytes, os.stat_result]:
    """Read a validated path through ONE descriptor: ``(data, fstat)``.

    The path was already checked against ``_allowed_dirs()``, but the run
    workspace is shared with the sandbox, so the name cannot be trusted a
    second time: a file (or a directory above it) swapped for a symlink after
    the check would otherwise make this host-side server mail any host file.
    ``utils.safe_open`` re-resolves the path, walks down from its allowed root
    without following a symlink, refuses a non-regular file and one over
    ``max_bytes`` (``FileTooLargeError``), and reads from that descriptor.
    """
    return safe_open.read_bytes(path, _allowed_dirs(), max_bytes=max_bytes, label=label)


def _too_large_message(label: str, name: str, size: int) -> str:
    return f"{label} too large: {name} ({size / 1024 / 1024:.1f}MB). Maximum size is 25MB per attachment."


def _aws_region() -> str:
    return os.environ.get("SES_AWS_REGION", "us-east-2").strip() or "us-east-2"


async def _send_ses(request: Mapping[str, Any]) -> MutableMapping[str, Any]:
    """Send one SES v2 message and normalize provider errors for the MCP."""
    try:
        async with _SESSION.client("sesv2", region_name=_aws_region()) as client:
            response = await client.send_email(**dict(request))
    except ClientError as exc:
        error = exc.response.get("Error", {})
        code = str(error.get("Code", "SESClientError"))
        message = str(error.get("Message", exc))
        logger.error("SES SendEmail failed: %s: %s", code, message)
        return {"error": f"SES SendEmail failed: {code}: {message}", "code": code}
    except BotoCoreError as exc:
        logger.error("SES request failed: %s", exc)
        return {"error": f"SES request failed: {exc}"}
    except Exception as exc:  # pragma: no cover - network/SDK error
        logger.error("SES request error: %s", exc)
        return {"error": f"SES request error: {exc}"}

    return {
        "status_code": int(response.get("ResponseMetadata", {}).get("HTTPStatusCode", 200)),
        "message_id": response.get("MessageId", ""),
    }


def _normalize_recipients(addresses: Sequence[str] | None) -> list[str]:
    if not addresses:
        return []

    normalized: list[str] = []
    for address in addresses:
        if not address:
            continue
        for candidate in re.split(r"[,;\n]", address):
            cleaned = candidate.strip()
            if cleaned:
                normalized.append(cleaned)

    return normalized


def _allowed_recipient_domains() -> set[str] | None:
    """Parse the recipient-domain allowlist from the environment.

    Returns a lowercased set of allowed domains, or ``None`` when
    ``SES_ALLOWED_RECIPIENT_DOMAINS`` is unset/blank — see
    ``_reject_disallowed_recipients`` for what unset means per context.
    """
    raw = os.environ.get("SES_ALLOWED_RECIPIENT_DOMAINS", "").strip()
    if not raw:
        return None
    domains = {part.strip().lower().lstrip("@") for part in raw.split(",")}
    domains.discard("")
    return domains or None


def _allowed_recipients() -> set[str] | None:
    """Return an optional exact-address allowlist, normalized to lowercase."""
    raw = os.environ.get("SES_ALLOWED_RECIPIENTS", "").strip()
    if not raw:
        return None
    recipients = {parseaddr(part.strip())[1].lower() for part in re.split(r"[,;\n]", raw) if parseaddr(part.strip())[1]}
    return recipients or None


def _is_managed_run() -> bool:
    """True when a task runner (the deployed context) spawned us.

    CUTLASS_RUN_WORKDIR is injected into every stdio MCP subprocess by the Go
    config layer exactly when the task runner set it (the same signal the
    cross-run send-once ledger keys on). Local/dev runs (make run, pytest,
    run_workflow_live.sh) don't set it.
    """
    return bool(os.environ.get("CUTLASS_RUN_WORKDIR", "").strip())


def _describe_prior_send(record: Mapping[str, Any]) -> str:
    """Human-readable ' — subject "…" to … at HH:MM UTC' for a ledger row, or ''."""
    parts: list[str] = []
    subject = str(record.get("subject") or "").strip()
    if subject:
        parts.append(f'subject "{subject[:120]}"')
    to = str(record.get("to") or "").strip()
    if to:
        parts.append(f"to {to[:200]}")
    ts = record.get("ts")
    if isinstance(ts, (int, float)):
        parts.append(time.strftime("at %H:%M UTC on %Y-%m-%d", time.gmtime(ts)))
    return (" — " + ", ".join(parts)) if parts else ""


def _allow_unlisted_recipients() -> bool:
    """Explicit opt-out from recipient allowlisting for an approved deployment."""
    return os.environ.get("SES_ALLOW_UNLISTED_RECIPIENTS", "").strip().lower() in {"1", "true", "yes"}


def _reject_disallowed_recipients(addresses: Sequence[str] | None) -> str | None:
    """Return an error message if any recipient's domain is not allowlisted.

    ``addresses`` is the raw model-supplied to/cc/bcc input; it is normalized
    the same way as when the mail is built.

    Unset ``SES_ALLOWED_RECIPIENT_DOMAINS`` (fail-closed in prod): in a
    managed/deployed run (CUTLASS_RUN_WORKDIR set) sending is REFUSED — an unset allowlist used to mean "send to anyone", which let a
    prompt-injected recipient at any domain exfiltrate the deal sheet. Local/
    dev runs keep the pre-existing warn-only behavior. A deployment whose
    documented recipient policy permits any address can explicitly opt out
    with SES_ALLOW_UNLISTED_RECIPIENTS=1.
    """
    recipients = _normalize_recipients(addresses)
    exact_allowed = _allowed_recipients()
    if exact_allowed is not None:
        rejected = [recipient for recipient in recipients if parseaddr(recipient)[1].lower() not in exact_allowed]
        if rejected:
            return (
                f"Recipient address not allowlisted: {', '.join(rejected)}. "
                f"Allowed recipients: {sorted(exact_allowed)}. "
                "Set SES_ALLOWED_RECIPIENTS to change this."
            )
        return None

    allowed = _allowed_recipient_domains()
    if allowed is None:
        if not recipients:
            return None
        if _is_managed_run() and _allow_unlisted_recipients():
            logger.warning(
                "SES_ALLOWED_RECIPIENT_DOMAINS is unset and SES_ALLOW_UNLISTED_RECIPIENTS is on: this managed "
                "run may mail any address (%d recipient(s)); set a recipient-domain list to close this",
                len(recipients),
            )
            return None
        if _is_managed_run():
            return (
                "SES_ALLOWED_RECIPIENT_DOMAINS is not set and this is a managed "
                "(deployed) run: refusing to send (fail-closed, cutlass#741). "
                "REMEDIATION: add SES_ALLOWED_RECIPIENT_DOMAINS to THIS gig-runner's "
                ".env as the comma-separated recipient domains, then restart the runner "
                "(e.g. SES_ALLOWED_RECIPIENT_DOMAINS=example.com,reports.example.com). "
                "Matching is exact-domain. If the deployment's approved recipient "
                "policy is unrestricted, set SES_ALLOW_UNLISTED_RECIPIENTS=1 explicitly."
            )
        logger.warning("SES_ALLOWED_RECIPIENT_DOMAINS is unset; recipient domain allowlist NOT enforced")
        return None

    rejected: list[str] = []
    for recipient in recipients:
        _, parsed = parseaddr(recipient)
        _, _, domain = parsed.rpartition("@")
        domain = domain.strip().lower()
        if not domain or domain not in allowed:
            rejected.append(recipient)
    if rejected:
        return (
            f"Recipient domain not allowlisted: {', '.join(rejected)}. "
            f"Allowed domains: {sorted(allowed)}. "
            "Set SES_ALLOWED_RECIPIENT_DOMAINS to change this."
        )
    return None


def _missing_cid_message(problem: str, missing: list[str]) -> str:
    message = f"{problem} Missing inline cids: {', '.join(missing)}"
    if email_render.LOGO_CID in missing:
        message += (
            f". cid:{email_render.LOGO_CID} is the header logo: pass the inline_attachments list that "
            "render_email returned, unchanged."
        )
    return message


@mcp.tool()
@_pinned_call
async def send_email(
    to_email: str,
    subject: str,
    content: str = "",
    *,
    content_file: str | None = None,
    content_type: str | None = None,
    from_email: str | None = None,
    cc_emails: Sequence[str] | None = None,
    bcc_emails: Sequence[str] | None = None,
    attachments: Sequence[str | Mapping[str, Any]] | None = None,
    inline_attachments: Sequence[Mapping[str, Any]] | None = None,
) -> MutableMapping[str, Any]:
    """Send an email via Amazon SES. Every email is branded HTML.

    This is a CALLABLE TOOL - just call it directly, do NOT import it in Python.

    FASTEST PATH: build the body with `render_email` first, then call this tool
    with the `content_file` and `inline_attachments` it returns and the same
    subject. Do not paste or re-type the rendered HTML; the file is the email.

    BRANDING IS REQUIRED: HTML outside the branded frame (the
    canonical_template's container, header and footer) is refused (EB001),
    even a one-line note, so use render_email. The only unbranded email is a
    deliberately plain note (a short personal reply, not a report), sent as
    plain text with content_type='text/plain'.

    Only when a section cannot be expressed as render_email blocks, render the
    HTML by hand from the canonical_template embedded in
    protocols/email-style.yaml. That file is the single source of truth: it
    mirrors flag/design-system/email-safe.html verbatim, lists the canonical
    palette and placeholders, and defines the rendering_procedure and
    pre_send_checklist. Do not hand-roll email HTML.

    To theme a hand-built email, load the selected theme from
    protocols/email_styles/themes/<theme_id>.yaml (default: the protocol's
    `themes.default`) and apply its `color_overrides` as literal hex find-replace on
    the assembled HTML (canonical sections and any component snippets copied in)
    BEFORE substituting {{placeholders}}. Content type is auto-detected — HTML
    tags will be sent as text/html.

    RECIPIENT PRIVACY: The email body must NEVER mention internal implementation
    details such as Amazon SES, theme names, or any tooling used to build or send
    the email. Recipients should not see how the email was made.
    This also applies to internal quality flags about instruction conflict resolution
    or agent reasoning — these belong in logs, not in client-facing email content.

    HTML VALIDATION:
    Content is validated by the shared `email_lint` module. Each finding has a
    stable rule code (EH001, EC001, …) and severity. Errors block sending;
    warnings are surfaced in the response but do not prevent delivery.

    Common rules (see email_lint.RULES for the full catalog):
    - EH001 — element foster-parented out of a <table>/<tbody> (wrap in <tr><td>)
    - EH002 — loose text foster-parented out of a table
    - EH003 — unclosed HTML tags
    - EP001 — *-pad <td> nested inside another *-pad <td> (unclosed inner table)
    - EI001-004 — broken <img src> (empty, file://, data:, non-cid/http)
    - ET001/ET002 — unresolved `{{...}}` / `${...}` tokens in body / subject
    - EC001 — CSS rgba() (alpha stripped by most clients)
    - EL101-103 — canonical_template demo content survived in body

    HANDLING LARGE CONTENT (>50KB):
    For large HTML emails, save content to a file first using run_python, then use
    content_file parameter instead of content. This avoids JSON serialization limits.
    Write the file in your working directory (the run workspace) and pass its ABSOLUTE
    path. Never use /tmp: the sandbox's /tmp is a different filesystem from this
    server's, so the file is simply "not found" there. A rejected path names the
    readable roots in its error.

    Example:
        1. In run_python: with open('email.html', 'w') as f: f.write(html_content)
           then print(os.path.abspath('email.html'))
        2. Call send_email with content_file="<that absolute path>"

    ATTACHMENTS:
    Pass a list of files to attach: bare paths, or objects with a "path" key.
    Pass the absolute path the file was written to inside the run workspace. On
    the shared chat client (no per-run task identity) attachment paths MUST be
    absolute; relative names are refused there, existing or not. A bare name
    that names nothing is looked up in the run workdir only when this server
    runs on a dedicated per-run client (a task identity is present; on the
    shared chat client nothing is searched), or in cwd when no run workdir was
    handed over and cwd is not the bundle checkout, and used only when exactly
    one file matches; the temp dir and CUTLASS_ALLOWED_DIRS accept explicit
    paths but are not searched. Files are read and base64-encoded; MIME types
    are auto-detected.

    Example:
        send_email(..., attachments=["<abs path to>/report.pdf", {"path": "<abs path to>/data.csv"}])
        (absolute paths under your working directory, same rule as content_file)

    Args:
        to_email: Recipient email address. Supports comma/semicolon/newline separated values.
        subject: Email subject line
        content: HTML content (must be styled, not plain text). Can be empty if content_file is provided.
        content_file: Path to file containing email content. Use this for large content (>50KB)
                      to avoid JSON serialization issues. Takes precedence over content if both provided.
        content_type: Usually omit - auto-detected from content
        cc_emails: Optional list of CC recipients
        bcc_emails: Optional list of BCC recipients
        attachments: Optional list of files to attach: bare paths, or objects with a
            "path" key. Pass the absolute path the file was written to inside the run
            workspace. On the shared chat client (no per-run task identity) attachment
            paths MUST be absolute; relative names are refused there, existing or not.
            A bare name that names nothing is looked up in the run workdir
            only when this server runs on a dedicated per-run client (a task identity is
            present; on the shared chat client nothing is searched), or in cwd when no
            run workdir was handed over and cwd is not the bundle checkout, and used only
            when exactly one file matches; the temp dir and CUTLASS_ALLOWED_DIRS accept
            explicit paths but are not searched. Files are read and base64-encoded; MIME
            types are auto-detected.
        inline_attachments: Optional list of inline CID attachments. Each item must include
                            'path' and 'cid', and may include 'mime_type'.

    Returns:
        Dictionary with: status="queued", message_id, content_type, html_validated,
        attachments_count and/or inline_attachments_count (if provided)
    """

    # Cross-run send-once guard (issue #673): the Go orchestrator dedups
    # identical send_email calls within one run, but its fingerprint set dies
    # with the process — a task re-run after a crash-after-send would re-send
    # the deal-sheet email. The fingerprint mirrors the orchestrator's
    # sendEmailFingerprint normalization so the two guards agree. No-op unless
    # CUTLASS_RUN_WORKDIR is set.
    # The per-task ledger directory is the task identity. Key the persistent
    # ONE-email invariant to that identity, not to body text: a resumed agent
    # may regenerate equivalent copy, but must not send a second email (#749.2).
    dedup_fingerprint = run_ledger.task_email_key()
    prior_send = run_ledger.email_sent_record(dedup_fingerprint) if dedup_fingerprint else None
    if prior_send is not None:
        # Report success (status_code 202, no "error" key) so the suppressed
        # send still discharges the send-email commitment — ONE email for this
        # task run WAS delivered to the provider. The invariant is one email
        # per task run regardless of subject or body (#749.2): a resumed or
        # nudged agent may regenerate the message, but must not send twice.
        # The note says exactly that — an earlier wording claimed the email
        # was "identical", which misled an agent whose first send was a stub
        # into believing its full report had gone out.
        sent_desc = _describe_prior_send(prior_send)
        logger.info("send_email suppressed: this task run already sent its one email%s", sent_desc)
        return {
            "status_code": 202,
            "status": "queued",
            "duplicate_suppressed": True,
            "note": (
                "send suppressed: this task run already sent its one outbound email"
                f"{sent_desc} (cross-run send-once ledger hit). The guard allows exactly one email per "
                "task run regardless of subject or body, so THIS call sent nothing. Do not retry. If the "
                "email that went out was incomplete, say so plainly in your run summary rather than "
                "claiming the full deliverable was delivered."
            ),
        }

    try:
        sender = _resolve_from_email(from_email)
    except SESConfigurationError as exc:
        return {"error": str(exc)}

    # Recipient domain allowlist (no-op unless SES_ALLOWED_RECIPIENT_DOMAINS is set).
    recipient_error = _reject_disallowed_recipients([to_email, *(cc_emails or []), *(bcc_emails or [])])
    if recipient_error:
        return {"error": recipient_error}

    # Handle content_file parameter - read content from file if provided
    actual_content = content
    content_source = "direct"
    if content_file:
        try:
            # Expand user home directory and resolve path securely
            file_path = _validate_path_security(content_file)
            try:
                raw_content, content_stat = _read_confined(file_path, _MAX_CONTENT_FILE_BYTES, "Content file")
            except FileNotFoundError:
                return {"error": f"Content file not found: {content_file}"}
            actual_content = raw_content.decode("utf-8")
            content_source = f"file:{content_file}"
            logger.info("Read email content from file: %s (%d bytes)", content_file, len(actual_content))
        except ValueError as e:
            return {"error": str(e)}
        except PermissionError:
            return {"error": f"Permission denied reading content file: {content_file}"}
        except UnicodeDecodeError as e:
            return {"error": f"Failed to decode content file as UTF-8: {content_file} - {e}"}
        except Exception as e:
            return {"error": f"Failed to read content file: {content_file} - {e}"}

    # Ensure we have content from either source
    if not actual_content or not actual_content.strip():
        return {"error": "Email content is empty. Provide content directly or via content_file parameter."}

    # Auto-detect and resolve content type
    resolved_type, was_corrected = resolve_content_type(actual_content, content_type)

    if resolved_type == "text/html" and content_file:
        upgraded = ensure_mobile_friendly(actual_content)
        if upgraded != actual_content:
            actual_content = upgraded
            # Written back only into the very file that was read (same inode,
            # reached by the same symlink-refusing walk): a name swapped for a
            # symlink in between is left alone, never written through.
            with contextlib.suppress(OSError, ValueError):
                safe_open.rewrite_same_file(
                    file_path, _allowed_dirs(), content_stat, actual_content.encode("utf-8"), label="Content file"
                )

    # Run the shared validator (subject + body + structure + leakage).
    findings = validate(actual_content, subject=subject, content_type=resolved_type, require_frame=True)
    errors, warnings = partition_findings(findings)

    for finding in errors:
        logger.error("validation error: %s", finding.format())
    for finding in warnings:
        logger.warning("validation warning: %s", finding.format())

    if errors:
        msg = f"Validation Failed. Fix these errors before sending:\n\n  - {format_findings(errors)}"
        if warnings:
            msg += f"\n\nWarnings (non-blocking, but worth fixing):\n  - {format_findings(warnings)}"
        return {"error": msg}

    inline_attachment_info: list[dict[str, Any]] = []
    inline_cid_map: dict[str, Mapping[str, Any]] = {}
    if inline_attachments:
        for item in inline_attachments:
            if not isinstance(item, Mapping):
                return {"error": "inline_attachments entries must be objects with keys: path, cid"}
            path_val = _attachment_entry_path(item)
            cid_val = str(item.get("cid", "")).strip()
            if not path_val or not cid_val:
                return {"error": "Each inline_attachments entry must include non-empty 'path' and 'cid'"}
            if cid_val in inline_cid_map:
                return {"error": f"Duplicate inline attachment cid: {cid_val}"}
            inline_cid_map[cid_val] = item

    if resolved_type == "text/html":
        cid_refs = extract_cid_references(actual_content)
        if cid_refs:
            if not inline_cid_map:
                return {
                    "error": _missing_cid_message(
                        "HTML contains cid: references but no inline_attachments were provided.", sorted(cid_refs)
                    )
                }
            missing_refs = sorted(cid for cid in cid_refs if cid not in inline_cid_map)
            if missing_refs:
                return {
                    "error": _missing_cid_message(
                        "HTML contains cid: references that are not mapped in inline_attachments.", missing_refs
                    )
                }

    to_recipients = _normalize_recipients([to_email])
    if not to_recipients:
        return {"error": "No valid to_email recipients provided."}

    destination: dict[str, list[str]] = {"ToAddresses": to_recipients}
    cc_recipients = _normalize_recipients(cc_emails)
    bcc_recipients = _normalize_recipients(bcc_emails)
    if cc_recipients:
        destination["CcAddresses"] = cc_recipients
    if bcc_recipients:
        destination["BccAddresses"] = bcc_recipients

    ses_attachments: list[dict[str, Any]] = []
    total_attachment_bytes = 0

    # Handle regular attachments
    attachment_info: list[dict[str, Any]] = []
    if attachments:
        for entry in attachments:
            file_path = _attachment_entry_path(entry)
            if not file_path:
                return {"error": "attachments entries must be file paths (or objects with a non-empty 'path')"}
            try:
                # Resolve securely, recovering a misreferenced file when it is
                # unambiguous and otherwise failing with an actionable message.
                resolved_path = _locate_attachment(file_path)

                # Get file info
                file_name = os.path.basename(resolved_path)
                # Opened ONCE here, without following a swapped symlink; the
                # size and the bytes both come from that descriptor.
                try:
                    raw_data, _file_stat = _read_confined(resolved_path, _MAX_ATTACHMENT_BYTES, "Attachment")
                except safe_open.FileTooLargeError as exc:
                    return {"error": _too_large_message("Attachment", file_name, exc.size)}
                file_size = len(raw_data)  # what is actually sent, not the stat before reading

                # Detect MIME type
                mime_type, _ = mimetypes.guess_type(resolved_path)
                if mime_type is None:
                    mime_type = "application/octet-stream"

                total_attachment_bytes += file_size
                if total_attachment_bytes > _MAX_TOTAL_ATTACHMENT_BYTES:
                    return {"error": "Combined attachments are too large. Maximum raw attachment size is 28MB."}

                ses_attachments.append(
                    {
                        "RawContent": raw_data,
                        "FileName": file_name,
                        "ContentType": mime_type,
                        "ContentDisposition": "ATTACHMENT",
                        "ContentTransferEncoding": "BASE64",
                    }
                )

                attachment_info.append(
                    {
                        "name": file_name,
                        "size": file_size,
                        "mime_type": mime_type,
                    }
                )
                logger.info("Added attachment: %s (%d bytes, %s)", file_name, file_size, mime_type)

            except (ValueError, FileNotFoundError) as e:
                return {"error": str(e)}
            except PermissionError:
                return {"error": f"Permission denied reading attachment: {file_path}"}
            except Exception as e:
                return {"error": f"Failed to read attachment {file_path}: {e}"}

    # Handle inline CID attachments
    if inline_attachments:
        for item in inline_attachments:
            try:
                file_path = _attachment_entry_path(item)
                cid = str(item.get("cid", "")).strip()
                if not file_path:
                    return {"error": "inline_attachments entries must include a non-empty 'path'"}

                # Resolve securely (same recovery + diagnostics as attachments;
                # the path is passed exactly as received).
                resolved_path = _locate_attachment(file_path, label="Inline attachment")

                file_name = os.path.basename(resolved_path)
                try:
                    raw_data, _file_stat = _read_confined(resolved_path, _MAX_ATTACHMENT_BYTES, "Inline attachment")
                except safe_open.FileTooLargeError as exc:
                    return {"error": _too_large_message("Inline attachment", file_name, exc.size)}
                file_size = len(raw_data)  # what is actually sent, not the stat before reading

                mime_type = str(item.get("mime_type") or "").strip()
                if not mime_type:
                    guessed, _ = mimetypes.guess_type(resolved_path)
                    mime_type = guessed or "application/octet-stream"

                total_attachment_bytes += file_size
                if total_attachment_bytes > _MAX_TOTAL_ATTACHMENT_BYTES:
                    return {"error": "Combined attachments are too large. Maximum raw attachment size is 28MB."}

                ses_attachments.append(
                    {
                        "RawContent": raw_data,
                        "FileName": file_name,
                        "ContentType": mime_type,
                        "ContentDisposition": "INLINE",
                        "ContentId": cid,
                        "ContentTransferEncoding": "BASE64",
                    }
                )

                inline_attachment_info.append(
                    {
                        "name": file_name,
                        "size": file_size,
                        "mime_type": mime_type,
                        "cid": cid,
                    }
                )
                logger.info("Added inline attachment: %s (%d bytes, %s, cid=%s)", file_name, file_size, mime_type, cid)

            except (ValueError, FileNotFoundError) as e:
                return {"error": str(e)}
            except PermissionError:
                return {"error": f"Permission denied reading inline attachment: {item.get('path')}"}
            except Exception as e:
                return {"error": f"Failed to read inline attachment {item.get('path')}: {e}"}

    body_kind = "Html" if resolved_type == "text/html" else "Text"
    simple_content: dict[str, Any] = {
        "Subject": {"Data": subject, "Charset": "UTF-8"},
        "Body": {body_kind: {"Data": actual_content, "Charset": "UTF-8"}},
    }
    if ses_attachments:
        simple_content["Attachments"] = ses_attachments

    request: dict[str, Any] = {
        "FromEmailAddress": sender,
        "Destination": destination,
        "Content": {"Simple": simple_content},
    }
    reply_to_addr = os.environ.get("SES_REPLY_TO", "").strip()
    if reply_to_addr:
        request["ReplyToAddresses"] = [reply_to_addr]
    configuration_set = os.environ.get("SES_CONFIGURATION_SET", "").strip()
    if configuration_set:
        request["ConfigurationSetName"] = configuration_set

    result = await _send_ses(request)
    if "error" in result:
        return result

    # Provider accepted (202) — record for the cross-run send-once guard.
    # Recording only after acceptance mirrors the orchestrator's semantics: a
    # failed send never discharges the commitment and stays retryable. The
    # subject/recipients ride along so a later suppression can say WHICH
    # email already went out.
    run_ledger.record_email_sent(
        dedup_fingerprint,
        subject=subject,
        to=str(to_email),
    )

    result["status"] = "queued"
    result["content_type"] = resolved_type
    result["content_source"] = content_source
    result["content_length"] = len(actual_content)
    result["html_validated"] = resolved_type == "text/html"
    if warnings:
        result["validation_warnings"] = [
            {"rule": f.rule, "severity": f.severity, "message": f.message, "hint": f.hint} for f in warnings
        ]
    if attachment_info:
        result["attachments_count"] = len(attachment_info)
        result["attachments"] = attachment_info
    if inline_attachment_info:
        result["inline_attachments_count"] = len(inline_attachment_info)
        result["inline_attachments"] = inline_attachment_info
    if was_corrected:
        result["content_type_auto_corrected"] = True
        result["original_content_type"] = content_type
    return result


@mcp.tool()
@_pinned_call
async def validate_email_content(
    content: str = "",
    content_file: str = "",
    subject: str = "",
    content_type: str = "",
) -> MutableMapping[str, Any]:
    """Validate HTML email content before sending. Use this to check for errors
    and fix them before calling send_email.

    Returns a structured result with:
    - valid: true if the content would pass send_email validation
    - findings: list of {rule, severity, message, hint, line} — full detail
    - errors: list of finding-format strings that would block sending
    - warnings: list of finding-format strings (non-blocking)
    - stats: content size, etc.

    Provide either content (HTML string) or content_file (path to HTML file).
    Pass the intended subject line to catch unresolved template tokens there
    as well; omit it to validate the body only. The content type is detected
    as send_email detects it (pass content_type to override): HTML must sit
    in the branded frame (EB001), and plain text skips the HTML checks.
    """
    actual_content = ""
    source = "content"

    if content_file:
        try:
            resolved_path = _validate_path_security(content_file.strip())
            try:
                raw_content, _ = _read_confined(resolved_path, _MAX_CONTENT_FILE_BYTES, "Content file")
            except FileNotFoundError:
                return {"valid": False, "errors": [f"File not found: {content_file}"], "warnings": []}
            actual_content = raw_content.decode("utf-8", errors="replace")
            source = "content_file"
        except ValueError as e:
            return {"valid": False, "errors": [str(e)], "warnings": []}
        except Exception as e:
            return {"valid": False, "errors": [f"Failed to read content file: {e}"], "warnings": []}
    elif content:
        actual_content = content
    else:
        return {"valid": False, "errors": ["Provide either content or content_file"], "warnings": []}

    resolved_type, _ = resolve_content_type(actual_content, content_type or None)
    findings = validate(actual_content, subject=subject or None, content_type=resolved_type, require_frame=True)
    errors, warnings = partition_findings(findings)

    return {
        "valid": len(errors) == 0,
        "findings": [
            {"rule": f.rule, "severity": f.severity, "message": f.message, "hint": f.hint, "line": f.line}
            for f in findings
        ],
        "errors": [f.format() for f in errors],
        "warnings": [f.format() for f in warnings],
        "source": source,
        "content_type": resolved_type,
        "stats": {
            "content_length": len(actual_content),
            "content_length_kb": round(len(actual_content) / 1024, 1),
        },
    }


@mcp.tool()
@_pinned_call
async def render_email(
    subject: str,
    blocks: list[dict[str, Any]],
    output_dir: str = "",
    *,
    output_file: str = "",
    theme: str = "",
    client_name: str = "",
    date: str = "",
    date_range: str = "",
    badge_label: str = "",
    status: str = "",
    preheader: str = "",
    headline: str = "",
) -> MutableMapping[str, Any]:
    """Render a branded HTML email from structured content and save it as a file.

    This is the fast way to send any styled email, from a two-line note to a
    full report: you supply only the content, and the branded header (the
    client's logo and name), footer, mobile layout, Outlook fallbacks and theme
    colors are added for you. It sends nothing. Pass the returned
    `content_file`, the same `subject` and the returned `inline_attachments`
    (the logo, plus any chart files you add) to send_email; never paste or
    re-type the HTML.

    Args:
        subject: Email subject (35-60 chars reads best): the inbox line and page title.
        blocks: Ordered body sections. Each is an object with a "type":
            - {"type": "text", "heading"?: str, "paragraphs": [str, ...], "bullets"?: [str, ...]}
              Prose. Use this for short notes and the sign-off too.
            - {"type": "headline_metrics", "heading"?: str, "items": [{"label", "value",
              "delta"?: "+4.2% WoW", "trend"?: "positive"|"negative"|"neutral"}], "caption"?: str}
              (1-6 KPIs; 3-5 reads best)
            - {"type": "metrics", "heading"?: str, "items": [{"label", "value"}, ...]}
              Compact key-figures card.
            - {"type": "callout", "kind": "risk"|"opportunity", "title"?: str, "text"?: str, "bullets"?: [str, ...]}
            - {"type": "table", "heading"?: str, "columns": ["Channel", "Spend", ...] or
              [{"label", "align"?: "left"|"right"|"center", "width"?: percent}],
              "rows": [[cell, ...], ...], "caption"?: str}  Up to 10 columns (6-10 columns
              automatically use compact desktop sizing and stack into full-width cards on
              mobile); widths default sensibly and must sum to 100 when all given. A cell is
              a string, {"text": str, "trend": "positive"|"negative"} for a colored delta,
              or {"share": 63.6} for a share-of-total bar. Column labels must be dimensional
              (Campaign, Channel, Date), never "Metric" or "Item". Put Spend before Margin.
            - {"type": "chart", "heading"?: str, "cid": str, "alt": str, "caption"?: str}
              A 1088px-wide PNG you pass to send_email as
              inline_attachments=[{"path": ..., "cid": <same cid>}].
            - {"type": "cta", "text": str (<= 25 chars), "url": "https://..."}  Only when
              the workflow defines a call to action; never invent a URL.
            - {"type": "closing", "heading"?: str, "text": str or [str, ...]}
        output_dir: The ABSOLUTE path of your working directory (shown under
            "Working directory" in your instructions). The email is written there.
        output_file: Optional exact output file path (e.g. when a workflow contract
            requires a specific body_html_path). If given without output_dir, its
            parent directory is used as output_dir.
        theme: Theme id from protocols/email_styles/themes/; defaults to the bundle default.
        client_name: The header headline beside the logo; defaults to this deployment's
            client name.
        date: Header date (ISO, e.g. 2026-10-08); defaults to today (UTC).
        date_range: Period the numbers cover, shown after the headline.
        badge_label, status: Status strip under the header, e.g. "On track" /
            "Pacing 98% of plan". Give both or neither; omitted, there is no strip.
        preheader: Inbox preview text; defaults to the start of the first paragraph.
        headline: Short title under the client name, 2-6 words, e.g. "Weekly delivery
            review". Leave out the client name and dates (both are already in the
            header). Defaults to the subject.

    Text formatting: values are escaped, so write plain text. **bold** and line
    breaks are the only formatting. Real data only: never the template's
    example names or numbers.

    Returns:
        content_file (absolute path to pass to send_email), inline_attachments
        (the header logo's file under cid:brand-logo; pass it to send_email and
        append your chart files), valid, errors, warnings, content_length, and
        inline_cids (every cid the email references).
    """
    file_arg = output_file.strip()
    directory_arg = output_dir.strip() or (os.path.dirname(file_arg) if file_arg else "")
    if not directory_arg:
        return {"error": "output_dir is required: pass the absolute path of your working directory."}
    try:
        directory = _validate_path_security(directory_arg)
        target_file = _validate_path_security(file_arg) if file_arg else ""
    except ValueError as exc:
        return {"error": str(exc)}
    if file_arg and not os.path.isdir(directory):
        # Created by a symlink-refusing walk from its allowed root, so a
        # directory swapped for a symlink cannot place new ones elsewhere.
        try:
            safe_open.makedirs(directory, _allowed_dirs(), label="output_dir")
        except ValueError as exc:  # a confinement refusal (UnsafePathError) is the caller's answer
            return {"error": str(exc)}
        except OSError:
            pass  # reported just below as "not an existing directory"
    if not os.path.isdir(directory):
        return {"error": f"output_dir is not an existing directory: {directory_arg}"}

    try:
        html_content = email_render.render_email(
            subject=subject,
            blocks=blocks,
            theme=theme,
            client_name=client_name,
            date=date,
            date_range=date_range,
            badge_label=badge_label,
            status=status,
            preheader=preheader,
            headline=headline,
        )
    except email_render.RenderError as exc:
        return {"error": f"Cannot render the email: {exc}"}

    stem = os.path.join(directory, f"email-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(3)}")
    path = target_file if target_file else f"{stem}.html"
    inline_attachments: list[dict[str, str]] = []
    # Only files this call made, with the inode each was created as: cleanup never
    # removes another file, nor follows a directory swapped for a symlink.
    created: list[tuple[str, tuple[int, int]]] = []
    # Every write goes through utils.safe_open: the directory is reached by a
    # walk from its allowed root that never follows a symlink, and the file is
    # opened with O_NOFOLLOW, so neither the file nor a directory above it
    # swapped for a symlink (the workspace is shared with the sandbox) can
    # redirect the write to a host file outside the allowed roots.
    roots = _allowed_dirs()
    try:
        if target_file:
            safe_open.write_new_or_truncate(
                target_file,
                roots,
                html_content.encode("utf-8"),
                create_dirs=True,
                label="output_file",
                on_created=lambda ident: created.append((target_file, ident)),
            )
        else:
            # Create-only and owner-only: never overwrites, and an output_dir in a
            # shared location (the temp dir is a readable root) does not expose
            # the email to other local users. Fleet's approval stager and this
            # server run as the same user.
            safe_open.write_new_or_truncate(
                path,
                roots,
                html_content.encode("utf-8"),
                exclusive=True,
                label="email",
                on_created=lambda ident: created.append((path, ident)),
            )
        logo = email_render.brand_logo()
        if logo is not None and f"cid:{email_render.LOGO_CID}" in html_content:
            # A copy beside the email, inside the workspace: send_email reads
            # attachments only from there, and so does fleet's approval card
            # when it previews the logo.
            logo_path = f"{stem}-{email_render.LOGO_CID}.png"
            logo_bytes = logo[0].read_bytes()
            safe_open.write_new_or_truncate(
                logo_path,
                roots,
                logo_bytes,
                exclusive=True,
                label="logo",
                on_created=lambda ident: created.append((logo_path, ident)),
            )
            inline_attachments.append({"path": os.path.abspath(logo_path), "cid": email_render.LOGO_CID})
    except (OSError, ValueError) as exc:
        # Half a rendered email is not handed back, so do not leave it behind.
        for leftover, ident in created:
            safe_open.remove_created(leftover, roots, ident)
        return {"error": f"Could not write the rendered email into {directory_arg}: {exc}"}

    findings = validate(html_content, subject=subject, content_type="text/html", require_frame=True)
    errors, warnings = partition_findings(findings)
    for finding in errors:
        logger.error("render_email produced a lint error: %s", finding.format())
    logger.info("Rendered email to %s (%d bytes, %d blocks)", path, len(html_content), len(blocks))

    result: dict[str, Any] = {
        "content_file": os.path.abspath(path),
        "inline_attachments": inline_attachments,
        "subject": subject,
        "valid": not errors,
        "errors": [f.format() for f in errors],
        "warnings": [f.format() for f in warnings],
        "content_length": len(html_content),
    }
    cids = sorted(extract_cid_references(html_content))
    if cids:
        result["inline_cids"] = cids
    return result


@mcp.tool()
async def validate_email_address(
    email_address: str,
    *,
    source: str = "",
) -> MutableMapping[str, Any]:
    """Perform local syntax validation without sending or querying a provider."""

    display_name, parsed = parseaddr(email_address.strip())
    valid = bool(
        parsed
        and len(parsed) <= 254
        and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", parsed)
    )
    # Empty source means this deployment. Manifests set SES_TENANT_LABEL so
    # the tool schema stays free of a hardcoded client name.
    label = source.strip() or os.environ.get("SES_TENANT_LABEL", "").strip() or "SES"
    return {
        "valid": valid,
        "email": parsed or email_address.strip(),
        "display_name": display_name,
        "source": label,
        "validation": "local_syntax",
    }


if __name__ == "__main__":
    mcp.run()

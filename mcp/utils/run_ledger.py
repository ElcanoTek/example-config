"""Cross-run create-idempotency and email send-once ledgers (issue #673).

Per-run guards (the Go orchestrator's in-memory fingerprints, the in-memory
``PreparedDealStore`` consume-once flags) die with the process: a run that
crashes after creating deals 1-3 of 10 re-creates 1-3 on re-run, and a crash
after the deal-sheet send re-sends the email. These ledgers persist that state
in ``CUTLASS_RUN_WORKDIR`` — a per-task directory the task runner mints and
re-mounts on retry — so a re-run of the SAME task resumes instead of
duplicating.

Design constraints:

* **Multi-process safe.** Each SSP MCP is a separate OS process, so state is
  append-only JSONL on disk (one ``os.write`` of one ``\\n``-terminated line
  per record via ``O_APPEND``), re-read from disk on every lookup. Torn or
  corrupt lines from a crashed writer are skipped on read.
* **Strictly fail-safe.** Every function is a no-op (``None``/``False``) when
  ``CUTLASS_RUN_WORKDIR`` is unset or not an existing directory, and ledger
  I/O errors are swallowed — the ledger must never block a create or a send.
* **Compact records only.** A create record stores the ssp, deal name, a
  success flag, a timestamp, and a whitelisted id/url summary — never
  credentials, never full payloads.

The email fingerprint mirrors the Go orchestrator's per-run dedup key
(``sendEmailFingerprint`` in ``internal/agent/agent.go``) so the two guards
agree on what "the same email" means.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

CREATES_FILENAME = "creates.jsonl"
EMAILS_FILENAME = "emails.jsonl"

# Whitelisted scalar keys copied from an executor result into the ledger
# record. Everything else (payloads, verification bodies, warnings) is
# deliberately dropped.
_RESULT_SUMMARY_KEYS = (
    "deal_id",
    "internal_deal_id",
    "external_deal_id",
    "line_item_id",
    "profile_id",
    "targeting_id",
    "deal_url",
    "requires_manual_remediation",
    "quality_flags",
)
_DEAL_SUMMARY_KEYS = ("id", "deal_id", "dealID", "internalDealID", "externalDealID", "name", "code", "displayName")

_EMAIL_SPLIT_RE = re.compile(r"[,;\n]")


def _workdir() -> str | None:
    """The per-task persistent dir, or None when the ledgers must no-op."""
    workdir = os.environ.get("CUTLASS_RUN_WORKDIR", "").strip()
    if not workdir or not os.path.isdir(workdir):
        return None
    return workdir


def _ledger_path(filename: str) -> str | None:
    workdir = _workdir()
    if workdir is None:
        return None
    return os.path.join(workdir, filename)


def _read_records(path: str) -> list[dict[str, Any]]:
    """All parseable records in the file; corrupt/partial lines are skipped."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue  # torn write from a crashed process — tolerate and skip
        if isinstance(record, dict):
            records.append(record)
    return records


def _append_record(path: str, record: dict[str, Any]) -> None:
    """Append one record as one line via a single O_APPEND write (fail-safe)."""
    line = (json.dumps(record, sort_keys=True, default=str) + "\n").encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        return  # never block the action on ledger I/O


def _summarize_create_result(result: dict[str, Any]) -> dict[str, Any]:
    """Compact, credential-free summary of an executor's success result."""
    summary: dict[str, Any] = {}
    for key in _RESULT_SUMMARY_KEYS:
        value = result.get(key)
        if isinstance(value, str | int | float | bool):
            summary[key] = value
        elif key == "quality_flags" and isinstance(value, list):
            # Quality flags are part of the create outcome contract, not a
            # verbose response body.  Keep only JSON-shaped entries so the
            # resumed response preserves remediation signals safely.
            summary[key] = json.loads(json.dumps(value, default=str))
    deal = result.get("deal")
    if isinstance(deal, dict):
        deal_summary = {
            key: deal[key] for key in _DEAL_SUMMARY_KEYS if isinstance(deal.get(key), str | int | float | bool)
        }
        if deal_summary:
            summary["deal"] = deal_summary
    return summary


# ---------------------------------------------------------------------------
# Create ledger
# ---------------------------------------------------------------------------


def lookup_create(ssp: str, deal_name: str) -> dict[str, Any] | None:
    """The recorded result for a deal already created in a previous run.

    Returns the latest successful create record for ``(ssp, deal_name)``, or
    None when there is no ledger (env unset / dir missing) or no hit. Reads
    the file fresh on every call — another process (or a previous run of this
    task) may have appended since this process started.
    """
    if not ssp or not deal_name:
        return None
    path = _ledger_path(CREATES_FILENAME)
    if path is None or not os.path.exists(path):
        return None
    match: dict[str, Any] | None = None
    for record in _read_records(path):
        if record.get("ssp") == ssp and record.get("deal_name") == deal_name and record.get("success") is True:
            match = record
    return match


def record_create(ssp: str, deal_name: str, result: dict[str, Any]) -> None:
    """Record a CONFIRMED-successful create. Callers must only invoke this on
    their confirmed-success path — never for partial or failed outcomes
    (partial outcomes where the deal EXISTS go through ``record_partial_create``)."""
    if not ssp or not deal_name:
        return
    path = _ledger_path(CREATES_FILENAME)
    if path is None:
        return
    record: dict[str, Any] = {
        "ssp": ssp,
        "deal_name": deal_name,
        "success": True,
        "ts": time.time(),
    }
    record.update(_summarize_create_result(result if isinstance(result, dict) else {}))
    _append_record(path, record)


def lookup_partial_create(ssp: str, deal_name: str) -> dict[str, Any] | None:
    """The latest PARTIAL create record for ``(ssp, deal_name)`` — a create
    whose deal EXISTS server-side but whose follow-up step failed (e.g. the IX
    hyphenated-seat attach PATCH). None when there is no ledger or no hit.

    A hit means "the deal exists — do NOT re-create it"; callers should refuse
    the create and point at the recorded ids so the retry REPAIRS the existing
    deal instead of minting a duplicate. Check ``lookup_create`` first — a
    confirmed-success record takes precedence (resume, not refuse).
    """
    if not ssp or not deal_name:
        return None
    path = _ledger_path(CREATES_FILENAME)
    if path is None or not os.path.exists(path):
        return None
    match: dict[str, Any] | None = None
    for record in _read_records(path):
        if record.get("ssp") == ssp and record.get("deal_name") == deal_name and record.get("partial") is True:
            match = record
    return match


def record_partial_create(ssp: str, deal_name: str, result: dict[str, Any], reason: str = "") -> None:
    """Record a PARTIAL create: the create POST succeeded (the deal EXISTS
    server-side) but a required follow-up step failed, so the executor returned
    success=False. Without this record a cross-run retry re-runs the create and
    mints a DUPLICATE live deal (issue #708). The record is shaped distinctly
    from a confirmed success (``success: False, partial: True``) so
    ``lookup_create`` never resumes it as a success; ``lookup_partial_create``
    surfaces it to block the blind re-create."""
    if not ssp or not deal_name:
        return
    path = _ledger_path(CREATES_FILENAME)
    if path is None:
        return
    record: dict[str, Any] = {
        "ssp": ssp,
        "deal_name": deal_name,
        "success": False,
        "partial": True,
        "ts": time.time(),
    }
    if reason:
        record["reason"] = reason
    record.update(_summarize_create_result(result if isinstance(result, dict) else {}))
    _append_record(path, record)


def record_create_submitted(ssp: str, deal_name: str, prepared_deal_id: str = "") -> None:
    """Record — BEFORE the POST goes out — that a non-idempotent create is
    being submitted for ``(ssp, deal_name)``. This is the fail-closed half of
    the ambiguous-outcome guard (issue #708): if the process dies or the
    transport fails mid-POST, no success/failure record will ever follow, and
    ``lookup_unresolved_submit`` keeps refusing a blind re-create across runs.
    Callers must resolve the marker on a DEFINITE outcome: ``record_create``
    (confirmed created) or ``resolve_create_submitted`` (confirmed NOT
    created, e.g. the server responded 4xx)."""
    if not ssp or not deal_name:
        return
    path = _ledger_path(CREATES_FILENAME)
    if path is None:
        return
    record: dict[str, Any] = {
        "ssp": ssp,
        "deal_name": deal_name,
        "success": False,
        "submitted": True,
        "ts": time.time(),
    }
    if prepared_deal_id:
        record["prepared_deal_id"] = prepared_deal_id
    _append_record(path, record)


def resolve_create_submitted(ssp: str, deal_name: str, reason: str = "") -> None:
    """Resolve a prior ``record_create_submitted`` marker with a DEFINITE
    did-NOT-create outcome (the server responded with a 4xx — the deal was not
    created), releasing the per-name guard so a corrected retry may create."""
    if not ssp or not deal_name:
        return
    path = _ledger_path(CREATES_FILENAME)
    if path is None:
        return
    record: dict[str, Any] = {
        "ssp": ssp,
        "deal_name": deal_name,
        "success": False,
        "submit_resolved": True,
        "ts": time.time(),
    }
    if reason:
        record["reason"] = reason
    _append_record(path, record)


def lookup_unresolved_submit(ssp: str, deal_name: str) -> dict[str, Any] | None:
    """The latest UNRESOLVED pre-POST submit marker for ``(ssp, deal_name)``,
    or None. A submit marker is resolved by any LATER record for the same
    key: a confirmed create (``success: True``), a partial create
    (``partial: True`` — the deal exists), or an explicit
    ``resolve_create_submitted`` record (confirmed not created).

    A hit means a create POST for this deal name went out and its outcome was
    never confirmed — the deal MAY exist server-side. Callers must refuse to
    re-create under this name (non-retryable) and direct the trader to verify
    manually; for SSPs without a list-deals endpoint (Magnite) there is no
    programmatic reconciliation."""
    if not ssp or not deal_name:
        return None
    path = _ledger_path(CREATES_FILENAME)
    if path is None or not os.path.exists(path):
        return None
    match: dict[str, Any] | None = None
    for record in _read_records(path):
        if record.get("ssp") != ssp or record.get("deal_name") != deal_name:
            continue
        if record.get("submitted") is True:
            match = record
        elif record.get("submit_resolved") is True or record.get("success") is True or record.get("partial") is True:
            match = None
    return match


def resumed_create_response(record: dict[str, Any]) -> dict[str, Any]:
    """Tool response for a ledger hit: the recorded result plus an explicit
    resume marker. ``success`` stays True so the agent's batch accounting and
    the audit-gate commitment discharge proceed exactly as for a fresh create.
    """
    response = dict(record)
    response["success"] = True
    response["resumed"] = True
    response["note"] = (
        f"Deal '{record.get('deal_name')}' was already created on {record.get('ssp')} by a previous run "
        "of this task (cross-run create ledger hit). Returning the recorded result — NO new deal was "
        "created and no SSP API call was made. Count this deal as created in batch accounting; do not "
        "retry the create."
    )
    return response


# ---------------------------------------------------------------------------
# Email send-once ledger
# ---------------------------------------------------------------------------


def _parse_email_list(raw: str) -> list[str]:
    """Mirror of the Go orchestrator's parseEmailList: split on , ; and
    newlines, lowercase+trim, drop empties, dedupe, sort."""
    if not raw or not raw.strip():
        return []
    unique: set[str] = set()
    result: list[str] = []
    for part in _EMAIL_SPLIT_RE.split(raw):
        normalized = part.strip().lower()
        if not normalized or normalized in unique:
            continue
        unique.add(normalized)
        result.append(normalized)
    result.sort()
    return result


def _parse_recipient_arg(value: Any) -> list[str]:
    """Mirror of the Go orchestrator's parseRecipientArg (string, list, None)."""
    if value is None:
        return []
    if isinstance(value, str):
        return _parse_email_list(value)
    if isinstance(value, list | tuple):
        collected: list[str] = []
        for item in value:
            collected.extend(_parse_recipient_arg(item))
        collected.sort()
        return collected
    return []


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def email_fingerprint(
    to_email: Any,
    subject: str,
    content: str = "",
    content_file: str | None = None,
    cc_emails: Any = None,
    bcc_emails: Any = None,
) -> str | None:
    """Semantic fingerprint of an email — sha256 over normalized recipients +
    subject + body reference. Byte-for-byte mirror of the Go orchestrator's
    sendEmailFingerprint so the per-run (Go) and cross-run (this) guards agree.
    Returns None when the args cannot form a meaningful fingerprint (missing
    recipients/subject/body) — callers must then skip the guard entirely.
    """
    to_recipients = _parse_recipient_arg(to_email)
    if not to_recipients:
        return None

    subject = (subject or "").strip()
    if not subject:
        return None

    content = (content or "").strip()
    content_file = (content_file or "").strip()
    if not content and not content_file:
        return None

    cc_recipients = _parse_recipient_arg(cc_emails)
    bcc_recipients = _parse_recipient_arg(bcc_emails)

    body_reference = "content_hash:" + _sha256_hex(content)
    if not content:
        body_reference = "content_file:" + content_file

    fingerprint_source = "|".join(
        [
            "to=" + ",".join(to_recipients),
            "cc=" + ",".join(cc_recipients),
            "bcc=" + ",".join(bcc_recipients),
            "subject=" + subject.lower(),
            body_reference,
        ]
    )
    return _sha256_hex(fingerprint_source)


def email_sent_record(fingerprint: str | None) -> dict[str, Any] | None:
    """The FIRST ledger row recording a provider-accepted send under this
    fingerprint (the email this task run actually delivered), or None when the
    ledger is inert or nothing was sent. Rows carry whatever metadata the
    sender recorded (subject, to, ts) so a suppression can name the email."""
    if not fingerprint:
        return None
    path = _ledger_path(EMAILS_FILENAME)
    if path is None or not os.path.exists(path):
        return None
    for record in _read_records(path):
        if record.get("fingerprint") == fingerprint:
            return record
    return None


def email_already_sent(fingerprint: str | None) -> bool:
    """Whether an email with this fingerprint was already accepted by the
    provider in a previous run of this task. False when the ledger is inert."""
    return email_sent_record(fingerprint) is not None


def task_email_key() -> str | None:
    """Stable ONE-email key for this task, independent of regenerated body text.

    Requires an EXPLICIT task identity (``CUTLASS_MOC_TASK_ID``). This module's
    contract is that ``CUTLASS_RUN_WORKDIR`` is a per-task directory, but a host
    may legitimately point it at one directory shared by every run on the box —
    fleet does exactly that for MCP servers on its shared, process-lifetime
    client (``${FLEET_WORKSPACE}`` resolves to a single ``mcp-shared`` dir, and
    it cannot inject a per-run task id into an already-spawned subprocess).
    Deriving the key from that path then collapses every task on the deployment
    onto ONE key: the first send writes the record, and every later send — any
    task, any conversation, forever, since the ledger has no TTL — is reported
    as an already-sent duplicate (HTTP 202 ``duplicate_suppressed``) and never
    delivered.

    A shared path is not a task identity. Without the task id there is nothing
    to key a once-per-task invariant to, so the guard stays inert — the
    fail-safe direction, since a send that might duplicate beats a send that
    silently never happens. Hosts that want cross-run send-once must set
    ``CUTLASS_MOC_TASK_ID`` (fleet does, on its dedicated per-run client).
    """
    if _workdir() is None:
        return None
    task_id = os.environ.get("CUTLASS_MOC_TASK_ID", "").strip()
    if not task_id:
        return None
    return "task:" + _sha256_hex(f"moc:{task_id}")


def record_email_sent(fingerprint: str | None, **meta: Any) -> None:
    """Record a provider-ACCEPTED send. Callers must only invoke this after the
    provider confirmed acceptance — a failed send must stay retryable.
    ``meta`` (subject, to, …) is stored verbatim for later suppression notes;
    it never participates in matching."""
    if not fingerprint:
        return
    path = _ledger_path(EMAILS_FILENAME)
    if path is None:
        return
    record: dict[str, Any] = {"fingerprint": fingerprint, "ts": time.time()}
    for key, value in meta.items():
        if value not in (None, ""):
            record[key] = value
    _append_record(path, record)

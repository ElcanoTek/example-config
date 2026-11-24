"""The client Outbox: a copy of every email ses_sender sends, kept in S3.

send_email builds the message here once (``build_message``), sends those exact
bytes through SES as Raw content, and then hands them back to ``archive`` so
the Outbox holds what the recipients were sent. Each email becomes two
objects in the tenant's email bucket, under ``SES_OUTBOX_PREFIX``
(default ``outbox/``), partitioned by the UTC send day:

    outbox/YYYY/MM/DD/<ses-message-id>.eml    the raw MIME message
    outbox/YYYY/MM/DD/<ses-message-id>.json   who/what sent it (``record``)

The .eml is written first, so a .json always has its message. Explorer reads
both (read-only) and shows them to everyone in the client organization.

Archiving runs only after SES has accepted the email, and it never fails the
send: an S3 error is reported in the tool result (``outbox.status=failed``)
and logged, but the email has gone out and must not be retried for it.
``SES_OUTBOX_BUCKET`` unset keeps the Outbox off (``status=disabled``).

A private send is not copied. The person decides that: the model passes
``private=True`` only when they asked, and fleet's ``fleet/private_emails``
request ``_meta`` (``fleet_private_choice``) overrides the argument with the
person's own choice: the Private box on the approval card, or a scheduled
task set to send its emails privately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import Mapping, MutableMapping, Sequence
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, getaddresses
from typing import Any

from botocore.config import Config
from botocore.exceptions import ClientError
from mcp.server.lowlevel.server import request_ctx

logger = logging.getLogger(__name__)

RECORD_SCHEMA = 1
_DEFAULT_PREFIX = "outbox/"
# SES message ids are [0-9a-f-]; anything else (or nothing) gets a random key
# so a surprising id can never steer the object outside its day folder.
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,200}")
_S3_CONFIG = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3, "mode": "standard"})
# The whole copy (both objects, retries included) must finish well inside
# fleet's per-tool-call budget: the email has already gone out by now.
_ARCHIVE_DEADLINE_SECONDS = 20.0
# SES refuses a message over 40MB after encoding.
MAX_MESSAGE_BYTES = 40 * 1024 * 1024
# The tools/call `_meta` entry fleet sets when the person chose whether an
# email is private: true for a card approved with Private ticked or any call in
# a run of a task set to send privately, false for a card approved unticked.
PRIVATE_META_KEY = "fleet/private_emails"
# 7bit makes every non-ASCII text part quoted-printable or base64: an 8bit part
# can be mangled when a configuration set rewrites the body (open/click
# tracking), and some relays still refuse it.
_POLICY = policy.SMTP.clone(cte_type="7bit")


def fleet_private_choice() -> bool | None:
    """The person's private choice fleet sent with the current tools/call, or None.

    Read from the request being served, so a tool that sends through
    ses_sender from inside another server (a deal sheet's email) follows the
    choice made for its own call. None outside a request, when fleet sent no
    choice, or when the value is not a boolean.
    """
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    extra = getattr(getattr(ctx, "meta", None), "model_extra", None) or {}
    value = extra.get(PRIVATE_META_KEY)
    return value if isinstance(value, bool) else None


def build_message(
    *,
    sender: str,
    to: Sequence[str],
    cc: Sequence[str],
    reply_to: str,
    subject: str,
    body: str,
    content_type: str,
    attachments: Sequence[Mapping[str, Any]],
    sent_at: datetime,
) -> bytes:
    """Build the RFC 5322 message send_email sends and the Outbox keeps.

    ``attachments`` are the entries send_email already loaded: ``RawContent``,
    ``FileName``, ``ContentType``, ``ContentDisposition`` (``ATTACHMENT`` or
    ``INLINE``) and, for inline parts, ``ContentId``. Inline parts join the
    HTML in multipart/related so ``cid:`` references resolve; regular ones are
    attached after it. Bcc is never a header: SES takes it from the
    Destination, and a header would show it to every recipient.

    Raises ValueError for a value that cannot be a header (a subject with a
    line break, for one); send_email turns that into a validation error.
    """
    msg = EmailMessage(policy=_POLICY)
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Date"] = formatdate(sent_at.timestamp(), usegmt=True)

    if content_type == "text/html":
        msg.set_content(body, subtype="html", charset="utf-8")
    else:
        msg.set_content(body, charset="utf-8")

    for part in attachments:
        if str(part.get("ContentDisposition", "")).upper() != "INLINE":
            continue
        maintype, subtype = _split_mime(str(part.get("ContentType", "")))
        msg.add_related(
            part["RawContent"],
            maintype=maintype,
            subtype=subtype,
            cid=f"<{part['ContentId']}>",
            disposition="inline",
            filename=part.get("FileName") or None,
        )
    for part in attachments:
        if str(part.get("ContentDisposition", "")).upper() == "INLINE":
            continue
        maintype, subtype = _split_mime(str(part.get("ContentType", "")))
        msg.add_attachment(
            part["RawContent"],
            maintype=maintype,
            subtype=subtype,
            filename=part.get("FileName") or "attachment",
        )
    return msg.as_bytes()


def _split_mime(content_type: str) -> tuple[str, str]:
    maintype, _, subtype = content_type.partition("/")
    if not maintype or not subtype:
        return "application", "octet-stream"
    return maintype, subtype


def bucket() -> str:
    return os.environ.get("SES_OUTBOX_BUCKET", "").strip()


def prefix() -> str:
    value = os.environ.get("SES_OUTBOX_PREFIX", "").strip() or _DEFAULT_PREFIX
    return value if value.endswith("/") else value + "/"


def object_keys(message_id: str, sent_at: datetime) -> tuple[str, str]:
    """The (.eml, .json) keys for one sent email."""
    stem = message_id if _SAFE_ID.fullmatch(message_id or "") else f"unknown-{secrets.token_hex(8)}"
    day = sent_at.astimezone(UTC).strftime("%Y/%m/%d")
    base = f"{prefix()}{day}/{stem}"
    return f"{base}.eml", f"{base}.json"


def _addresses(values: Sequence[str]) -> list[str]:
    return [addr for _, addr in getaddresses(list(values)) if addr]


def record(
    *,
    message_id: str,
    sent_at: datetime,
    sender: str,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
    reply_to: str,
    subject: str,
    content_type: str,
    attachments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The .json written next to the .eml (``archive`` adds its key and size).

    ``sent_by`` and ``task_id`` come from fleet (the signed-in person of the
    chat turn, and the scheduled task), never from a model argument, so a
    reader can trust who and what sent the email. Bcc is recorded here, not in
    the message: the Outbox is the client's own record of who was mailed.
    """
    return {
        "schema": RECORD_SCHEMA,
        "message_id": message_id,
        "sent_at": sent_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "from": sender,
        "to": _addresses(to),
        "cc": _addresses(cc),
        "bcc": _addresses(bcc),
        "reply_to": reply_to,
        "subject": subject,
        "content_type": content_type,
        "attachments": [
            {
                "name": part.get("FileName", ""),
                "mime_type": part.get("ContentType", ""),
                "size": len(part.get("RawContent", b"")),
                "inline": str(part.get("ContentDisposition", "")).upper() == "INLINE",
            }
            for part in attachments
        ],
        "sent_by": os.environ.get("SES_TURN_USER_EMAIL", "").strip().lower(),
        "task_id": os.environ.get("CUTLASS_MOC_TASK_ID", "").strip(),
        "tenant": os.environ.get("SES_TENANT_LABEL", "").strip(),
    }


async def archive(
    session: Any,
    region: str,
    *,
    raw: bytes,
    message_id: str,
    sent_at: datetime,
    **fields: Any,
) -> MutableMapping[str, Any]:
    """Write one sent email to the Outbox; report, never raise.

    ``fields`` are the rest of ``record()``'s arguments. Any error, or running
    past the deadline, is ``status=failed``: the email was already accepted,
    so nothing here may turn the send into a failure.
    """
    target = bucket()
    if not target:
        return {"status": "disabled"}
    try:
        eml_key, json_key = object_keys(message_id, sent_at)
        meta = record(message_id=message_id, sent_at=sent_at, **fields)
        doc = json.dumps(dict(meta, eml_key=eml_key, eml_size=len(raw)), ensure_ascii=False, indent=2)
        async with asyncio.timeout(_ARCHIVE_DEADLINE_SECONDS):
            async with session.client("s3", region_name=region, config=_S3_CONFIG) as s3:
                await s3.put_object(Bucket=target, Key=eml_key, Body=raw, ContentType="message/rfc822")
                await s3.put_object(
                    Bucket=target, Key=json_key, Body=doc.encode("utf-8"), ContentType="application/json"
                )
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "S3ClientError"))
        logger.error("Outbox copy failed for %s: %s", message_id, code)
        return {"status": "failed", "error": f"S3 {code}"}
    except TimeoutError:
        logger.error("Outbox copy for %s ran past %ss", message_id, _ARCHIVE_DEADLINE_SECONDS)
        return {"status": "failed", "error": "timeout"}
    except Exception as exc:
        # Deliberately broad: credential refresh, the SDK and the event loop
        # can all raise their own types after SES has accepted the email.
        logger.error("Outbox copy failed for %s: %s", message_id, type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}
    return {"status": "saved", "key": eml_key}

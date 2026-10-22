"""Cross-run send-once guard for ses_sender.send_email (issue #673).

A crash after the deal-sheet email was accepted by Amazon SES re-sends it on
task re-run: the Go orchestrator's duplicate-send fingerprints die with the
process. With CUTLASS_RUN_WORKDIR set (where records live) AND
CUTLASS_MOC_TASK_ID set (whose records they are — the runner supplies it),
send_email records the accepted send and suppresses a re-send of the same
task — reporting SUCCESS (status_code 202, no "error" key) so the suppressed
duplicate still discharges the send-email commitment, exactly like a real
accepted send would.

The task id is REQUIRED for suppression: a host may point CUTLASS_RUN_WORKDIR
at one directory shared by every run on the box, and keying the guard to that
path suppressed every send on the deployment after the first. See
tests/utils/test_run_ledger.py::test_shared_workdir_never_suppresses_a_later_task.
"""

import pytest
from email_fixtures import framed
from ses_sender import send_email

_VALID_HTML = framed("<p>Deal sheet attached.</p>")
_OTHER_HTML = framed("<p>Completely different body.</p>")


@pytest.fixture(autouse=True)
def _recipient_allowlist(monkeypatch: pytest.MonkeyPatch):
    """These tests set CUTLASS_RUN_WORKDIR (the managed-run marker), which now
    also engages the recipient-allowlist fail-closed default.
    Satisfy the allowlist so this suite keeps testing ONLY the send-once
    ledger; the allowlist behavior itself is covered by
    test_ses_recipient_allowlist.py."""
    monkeypatch.setenv("SES_ALLOWED_RECIPIENT_DOMAINS", "example.net,x.com")


@pytest.fixture
def task_identity(monkeypatch: pytest.MonkeyPatch):
    """The task id a real task-runner run supplies (see deal_sheet_server.py).

    The ledger keys the once-per-task invariant to THIS, not to the workdir
    path — a workdir may be shared by every run on the box.
    """
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-send-once")


@pytest.fixture
def counted_send(monkeypatch: pytest.MonkeyPatch):
    """Stub the Amazon SES network call, counting provider submissions."""
    calls = {"count": 0}

    async def fake_send_ses(*_args, **_kwargs):
        calls["count"] += 1
        return {"status_code": 200, "message_id": f"msg-{calls['count']}"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send_ses)
    return calls


async def _send(subject: str = "Deal Sheet", content: str = _VALID_HTML, **kwargs):
    return await send_email(
        to_email="trader@example.net",
        subject=subject,
        content=content,
        from_email="reports@example.com",
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("task_identity")
async def test_identical_resend_suppressed_but_reports_success(counted_send, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))

    first = await _send()
    assert first.get("status") == "queued"
    assert first.get("status_code") == 200
    assert "duplicate_suppressed" not in first
    assert counted_send["count"] == 1

    second = await _send()
    # Suppressed — but shaped as a SUCCESS (202, no "error") so the send-email
    # commitment is discharged and the agent does not retry.
    assert second["duplicate_suppressed"] is True
    assert second["status_code"] == 202
    assert second["status"] == "queued"
    assert "error" not in second
    assert "send suppressed" in second["note"]
    assert "one outbound email" in second["note"]
    assert 'subject "Deal Sheet"' in second["note"]
    assert "trader@example.net" in second["note"]
    assert "identical" not in second["note"]
    assert "sent nothing" in second["note"]
    assert counted_send["count"] == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("task_identity")
async def test_regenerated_email_is_still_suppressed_for_same_task(counted_send, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))

    await _send()
    different_subject = await _send(subject="Another Subject")
    assert different_subject["duplicate_suppressed"] is True
    assert counted_send["count"] == 1

    different_body = await _send(content=_OTHER_HTML)
    assert different_body["duplicate_suppressed"] is True
    assert counted_send["count"] == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("task_identity")
async def test_recipient_normalization_matches_go_fingerprint(counted_send, tmp_path, monkeypatch):
    """Reordered/re-cased recipients are the SAME email (mirrors the Go
    orchestrator's sendEmailFingerprint normalization)."""
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))

    await send_email(
        to_email="A@x.com, b@x.com",
        subject="Deal Sheet",
        content=_VALID_HTML,
        from_email="reports@example.com",
    )
    assert counted_send["count"] == 1

    second = await send_email(
        to_email="b@x.com; a@X.com",
        subject="deal sheet",
        content=_VALID_HTML,
        from_email="reports@example.com",
    )
    assert second["duplicate_suppressed"] is True
    assert counted_send["count"] == 1


@pytest.mark.asyncio
async def test_without_workdir_no_suppression(counted_send, monkeypatch):
    monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)

    await _send()
    second = await _send()
    assert "duplicate_suppressed" not in second
    assert counted_send["count"] == 2


@pytest.mark.asyncio
async def test_shared_workdir_without_task_id_keeps_sending(counted_send, tmp_path, monkeypatch):
    """The reported regression, at the server boundary.

    A host that points CUTLASS_RUN_WORKDIR at ONE directory shared by every run
    on the box (fleet's shared MCP client) supplies no task id. Every send used
    to hash to the same key, so the first send anywhere on the deployment made
    every later send — different task, different report, different day — come
    back 202 `duplicate_suppressed` with no email delivered. Regenerating the
    body could not clear it either: the key is deliberately body-independent.
    """
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))
    monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)

    await _send()
    second = await _send(subject="Weekly Report — Jul 27", content=_OTHER_HTML)

    assert "duplicate_suppressed" not in second
    assert second.get("status") == "queued"
    assert counted_send["count"] == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("task_identity")
async def test_failed_send_is_not_recorded(tmp_path, monkeypatch):
    """A provider rejection must NOT discharge the send: the retry after a
    failure goes back to the provider (mirrors the orchestrator's
    failed-send-doesn't-discharge semantics)."""
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))
    calls = {"count": 0}

    async def failing_then_ok(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"error": "Amazon SES API error (500): boom", "status_code": 500}
        return {"status_code": 200, "message_id": "msg-2"}

    monkeypatch.setattr("ses_sender._send_ses", failing_then_ok)

    first = await _send()
    assert "error" in first

    second = await _send()
    assert "duplicate_suppressed" not in second
    assert second.get("status") == "queued"
    assert calls["count"] == 2

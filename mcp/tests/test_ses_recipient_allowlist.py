"""Recipient-domain allowlist fail-closed default.

An UNSET ``SES_ALLOWED_RECIPIENT_DOMAINS`` used to mean "send to anyone"
— warn-only — letting a prompt-injected or brief-supplied recipient at any
domain receive the deal-sheet email (XLSX + per-deal ids/QA links). The
default is now fail-CLOSED in a managed/deployed run (CUTLASS_RUN_WORKDIR set,
the task-runner signal the send-once ledger already keys on):

  - managed run + unset allowlist            -> send REFUSED
  - managed run + allowlisted recipient      -> sent
  - managed run + non-allowlisted recipient  -> refused (pre-existing gate)
  - local/dev (no CUTLASS_RUN_WORKDIR)       -> unchanged (warn-only)
  - managed run + SES_ALLOW_UNLISTED_RECIPIENTS=1 -> sent (explicit,
    documented unrestricted-recipient policy)
"""

import pytest
from ses_sender import send_email

_VALID_HTML = "<html><body><p>Deal sheet attached.</p></body></html>"


@pytest.fixture
def counted_send(monkeypatch: pytest.MonkeyPatch):
    """Stub the Amazon SES network call, counting provider submissions."""
    calls = {"count": 0}

    async def fake_send_ses(*_args, **_kwargs):
        calls["count"] += 1
        return {"status_code": 200, "message_id": f"msg-{calls['count']}"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send_ses)
    return calls


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Baseline: no allowlist, no bypass, no managed-run marker."""
    monkeypatch.delenv("SES_ALLOWED_RECIPIENTS", raising=False)
    monkeypatch.delenv("SES_ALLOWED_RECIPIENT_DOMAINS", raising=False)
    monkeypatch.delenv("SES_ALLOW_UNLISTED_RECIPIENTS", raising=False)
    monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)


async def _send(to_email: str = "trader@example.net", subject: str = "Deal Sheet"):
    return await send_email(
        to_email=to_email,
        subject=subject,
        content=_VALID_HTML,
        from_email="reports@example.com",
    )


@pytest.mark.asyncio
async def test_managed_run_unset_allowlist_refuses_send(counted_send, clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))

    result = await _send()
    assert "error" in result
    assert "SES_ALLOWED_RECIPIENT_DOMAINS" in result["error"]
    assert "fail-closed" in result["error"]
    assert counted_send["count"] == 0, "nothing may reach Amazon SES when the deployed allowlist is unset"


@pytest.mark.asyncio
async def test_managed_run_allowlisted_recipient_sends(counted_send, clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))
    monkeypatch.setenv("SES_ALLOWED_RECIPIENT_DOMAINS", "example.net")

    result = await _send()
    assert "error" not in result
    assert result["status"] == "queued"
    assert counted_send["count"] == 1


@pytest.mark.asyncio
async def test_managed_run_non_allowlisted_recipient_refused(counted_send, clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))
    monkeypatch.setenv("SES_ALLOWED_RECIPIENT_DOMAINS", "example.net")

    result = await _send(to_email="attacker@evil.example")
    assert "error" in result
    assert "attacker@evil.example" in result["error"]
    assert counted_send["count"] == 0


@pytest.mark.asyncio
async def test_exact_address_allowlist_overrides_domain_allowlist(counted_send, clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))
    monkeypatch.setenv("SES_ALLOWED_RECIPIENTS", "analyst@example.net")
    monkeypatch.setenv("SES_ALLOWED_RECIPIENT_DOMAINS", "example.net")

    rejected = await _send(to_email="someone-else@example.net")
    assert "error" in rejected
    assert "Recipient address not allowlisted" in rejected["error"]
    assert counted_send["count"] == 0

    accepted = await _send(to_email="Analyst <analyst@example.net>")
    assert accepted["status"] == "queued"
    assert counted_send["count"] == 1


@pytest.mark.asyncio
async def test_local_dev_unset_allowlist_unchanged(counted_send, clean_env):
    # No CUTLASS_RUN_WORKDIR: local/dev/pytest context keeps the pre-existing
    # warn-only behavior so `make run` and the smoke scripts still send.
    result = await _send(subject="Dev Sheet")
    assert "error" not in result
    assert result["status"] == "queued"
    assert counted_send["count"] == 1


@pytest.mark.asyncio
async def test_managed_run_explicit_unrestricted_policy_sends(counted_send, clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))
    monkeypatch.setenv("SES_ALLOW_UNLISTED_RECIPIENTS", "1")

    result = await _send(subject="Canary Sheet")
    assert "error" not in result
    assert result["status"] == "queued"
    assert counted_send["count"] == 1

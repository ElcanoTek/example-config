"""Amazon SES v2 request-shape and sender-safety tests."""

from __future__ import annotations

from typing import Any

import pytest
from ses_sender import send_email, validate_email_address

_HTML = "<html><body><p>Report attached.</p></body></html>"


@pytest.fixture
def ses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SES_FROM_EMAIL", "victoria@northwind.example")
    monkeypatch.setenv("SES_REPLY_TO", "victoria@northwind.example")
    monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)


@pytest.fixture
def capture_request(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_send(request):
        captured.update(request)
        return {"status_code": 200, "message_id": "ses-message-id"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send)
    return captured


@pytest.mark.asyncio
async def test_builds_simple_ses_v2_request(ses_env, capture_request, monkeypatch):
    monkeypatch.setenv("SES_CONFIGURATION_SET", "northwind-email-outbound")

    result = await send_email(
        to_email="alice@example.com; bob@example.com",
        cc_emails=["carol@example.com"],
        bcc_emails=["audit@example.com"],
        subject="Northwind report",
        content=_HTML,
    )

    assert result["status"] == "queued"
    assert result["message_id"] == "ses-message-id"
    assert capture_request["FromEmailAddress"] == "victoria@northwind.example"
    assert capture_request["ReplyToAddresses"] == ["victoria@northwind.example"]
    assert capture_request["ConfigurationSetName"] == "northwind-email-outbound"
    assert capture_request["Destination"] == {
        "ToAddresses": ["alice@example.com", "bob@example.com"],
        "CcAddresses": ["carol@example.com"],
        "BccAddresses": ["audit@example.com"],
    }
    simple = capture_request["Content"]["Simple"]
    assert simple["Subject"] == {"Data": "Northwind report", "Charset": "UTF-8"}
    assert simple["Body"]["Html"]["Data"] == _HTML


@pytest.mark.asyncio
async def test_regular_and_inline_attachments_use_ses_attachment_shape(ses_env, capture_request, tmp_path):
    report = tmp_path / "report.csv"
    logo = tmp_path / "logo.png"
    report.write_bytes(b"date,revenue\n2026-08-26,10\n")
    logo.write_bytes(b"not-a-real-png")

    result = await send_email(
        to_email="alice@example.com",
        subject="Attachments",
        content='<html><body><img src="cid:brand-logo"><p>Attached.</p></body></html>',
        attachments=[str(report)],
        inline_attachments=[{"path": str(logo), "cid": "brand-logo"}],
    )

    assert result["attachments_count"] == 1
    assert result["inline_attachments_count"] == 1
    attachments = capture_request["Content"]["Simple"]["Attachments"]
    assert attachments[0]["RawContent"].startswith(b"date,revenue")
    assert attachments[0]["ContentDisposition"] == "ATTACHMENT"
    assert attachments[1]["RawContent"] == b"not-a-real-png"
    assert attachments[1]["ContentDisposition"] == "INLINE"
    assert attachments[1]["ContentId"] == "brand-logo"


@pytest.mark.asyncio
async def test_explicit_sender_cannot_override_configured_identity(ses_env, capture_request):
    result = await send_email(
        to_email="alice@example.com",
        subject="Spoof attempt",
        content=_HTML,
        from_email="someone-else@northwind.example",
    )

    assert "error" in result
    assert "must match the configured SES_FROM_EMAIL" in result["error"]
    assert capture_request == {}


@pytest.mark.asyncio
async def test_email_address_validation_is_local():
    valid = await validate_email_address("Northwind User <user@northwind.example>")
    invalid = await validate_email_address("not-an-email")

    assert valid["valid"] is True
    assert valid["email"] == "user@northwind.example"
    assert valid["validation"] == "local_syntax"
    assert invalid["valid"] is False

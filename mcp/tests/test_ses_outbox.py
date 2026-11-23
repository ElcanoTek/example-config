"""The client Outbox: every sent email is copied to S3 unless sent privately."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest
from botocore.exceptions import ClientError
from email_fixtures import framed, sent_body, sent_message
from ses_sender import send_email

_HTML = framed("<p>Weekly numbers attached.</p>")
_DAY_KEY = re.compile(r"^outbox/\d{4}/\d{2}/\d{2}/")


class _FakeS3:
    def __init__(self, fail: bool = False) -> None:
        self.puts: list[dict[str, Any]] = []
        self.fail = fail

    async def put_object(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "PutObject")
        self.puts.append(kwargs)
        return {}

    async def __aenter__(self) -> _FakeS3:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeSession:
    def __init__(self, s3: _FakeS3) -> None:
        self.s3 = s3
        self.services: list[str] = []

    def client(self, service: str, **_kwargs: Any) -> _FakeS3:
        self.services.append(service)
        return self.s3


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SES_FROM_EMAIL", "victoria@northwind.example")
    monkeypatch.setenv("SES_OUTBOX_BUCKET", "northwind-victoria-email")
    monkeypatch.setenv("SES_TURN_USER_EMAIL", "dana@northwind.example")
    monkeypatch.setenv("SES_TENANT_LABEL", "Northwind")
    monkeypatch.delenv("SES_OUTBOX_PREFIX", raising=False)
    monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)
    monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    async def fake_send(request):
        requests.append(request)
        return {"status_code": 200, "message_id": "0100018f-abc-000000"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send)
    return requests


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr("ses_sender._SESSION", _FakeSession(fake))
    return fake


@pytest.mark.asyncio
async def test_sent_email_is_saved_with_its_record(env, sent, s3, monkeypatch):
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-42")

    result = await send_email(
        to_email="alice@client.example",
        cc_emails=["carol@client.example"],
        bcc_emails=["audit@northwind.example"],
        subject="Weekly report",
        content=_HTML,
    )

    assert result["status"] == "queued"
    assert result["outbox"]["status"] == "saved"
    eml, record = s3.puts
    assert _DAY_KEY.match(eml["Key"]) and eml["Key"].endswith("/0100018f-abc-000000.eml")
    assert record["Key"] == eml["Key"].removesuffix(".eml") + ".json"
    assert result["outbox"]["key"] == eml["Key"]
    assert eml["Bucket"] == record["Bucket"] == "northwind-victoria-email"
    # The Outbox keeps the exact bytes SES was given.
    assert eml["Body"] == sent[0]["Content"]["Raw"]["Data"]
    assert eml["ContentType"] == "message/rfc822"

    doc = json.loads(record["Body"])
    assert doc["schema"] == 1
    assert doc["message_id"] == "0100018f-abc-000000"
    assert doc["to"] == ["alice@client.example"]
    assert doc["cc"] == ["carol@client.example"]
    assert doc["bcc"] == ["audit@northwind.example"]
    assert doc["subject"] == "Weekly report"
    assert doc["sent_by"] == "dana@northwind.example"
    assert doc["task_id"] == "task-42"
    assert doc["tenant"] == "Northwind"
    assert doc["eml_key"] == eml["Key"]
    assert doc["eml_size"] == len(eml["Body"])
    assert doc["sent_at"].endswith("Z")


@pytest.mark.asyncio
async def test_record_lists_attachments(env, sent, s3, tmp_path):
    report = tmp_path / "report.csv"
    report.write_bytes(b"date,revenue\n")

    await send_email(to_email="alice@client.example", subject="Report", content=_HTML, attachments=[str(report)])

    doc = json.loads(s3.puts[1]["Body"])
    assert doc["attachments"] == [{"name": "report.csv", "mime_type": "text/csv", "size": 13, "inline": False}]


@pytest.mark.asyncio
async def test_non_ascii_subject_survives(env, sent, s3):
    await send_email(to_email="alice@client.example", subject="Café revenue: €12k", content=_HTML)

    assert sent_message(sent[0])["Subject"] == "Café revenue: €12k"
    assert json.loads(s3.puts[1]["Body"])["subject"] == "Café revenue: €12k"


@pytest.mark.asyncio
async def test_prefix_without_slash_is_a_folder(env, sent, s3, monkeypatch):
    monkeypatch.setenv("SES_OUTBOX_PREFIX", "sent")

    await send_email(to_email="alice@client.example", subject="Report", content=_HTML)

    assert re.match(r"^sent/\d{4}/\d{2}/\d{2}/", s3.puts[0]["Key"])


@pytest.mark.asyncio
async def test_unexpected_message_id_stays_inside_the_day_folder(env, s3, monkeypatch):
    async def fake_send(_request):
        return {"status_code": 200, "message_id": "../../emails/evil"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send)

    await send_email(to_email="alice@client.example", subject="Report", content=_HTML)

    key = s3.puts[0]["Key"]
    assert _DAY_KEY.match(key)
    assert re.fullmatch(r"outbox/\d{4}/\d{2}/\d{2}/unknown-[0-9a-f]{16}\.eml", key)


@pytest.mark.asyncio
async def test_no_bucket_keeps_the_outbox_off(env, sent, s3, monkeypatch):
    monkeypatch.delenv("SES_OUTBOX_BUCKET")

    result = await send_email(to_email="alice@client.example", subject="Report", content=_HTML)

    assert result["status"] == "queued"
    assert result["outbox"] == {"status": "disabled"}
    assert s3.puts == []


@pytest.mark.asyncio
async def test_s3_failure_reports_but_does_not_fail_the_send(env, sent, monkeypatch):
    monkeypatch.setattr("ses_sender._SESSION", _FakeSession(_FakeS3(fail=True)))

    result = await send_email(to_email="alice@client.example", subject="Report", content=_HTML)

    assert "error" not in result
    assert result["status"] == "queued"
    assert result["message_id"] == "0100018f-abc-000000"
    assert result["outbox"] == {"status": "failed", "error": "S3 AccessDenied"}
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_failed_send_is_not_saved(env, s3, monkeypatch):
    async def fake_send(_request):
        return {"error": "SES SendEmail failed: Throttling: slow down", "code": "Throttling"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send)

    result = await send_email(to_email="alice@client.example", subject="Report", content=_HTML)

    assert "error" in result
    assert s3.puts == []


@pytest.mark.asyncio
async def test_private_note_to_self_is_sent_but_not_saved(env, sent, s3):
    result = await send_email(to_email="Dana <DANA@northwind.example>", subject="Reminder", content=_HTML, private=True)

    assert result["status"] == "queued"
    assert result["outbox"] == {"status": "private"}
    assert len(sent) == 1
    assert s3.puts == []


@pytest.mark.parametrize(
    ("to", "cc", "bcc"),
    [
        ("alice@client.example", None, None),
        ("dana@northwind.example", ["alice@client.example"], None),
        ("dana@northwind.example", None, ["alice@client.example"]),
    ],
)
@pytest.mark.asyncio
async def test_private_refused_when_anyone_else_receives_it(env, sent, s3, to, cc, bcc):
    result = await send_email(
        to_email=to, cc_emails=cc, bcc_emails=bcc, subject="Reminder", content=_HTML, private=True
    )

    assert "only for a note to yourself" in result["error"]
    assert sent == [] and s3.puts == []


@pytest.mark.asyncio
async def test_private_refused_without_a_signed_in_person(env, sent, s3, monkeypatch):
    monkeypatch.delenv("SES_TURN_USER_EMAIL")

    result = await send_email(to_email="dana@northwind.example", subject="Reminder", content=_HTML, private=True)

    assert "needs a signed-in person" in result["error"]
    assert sent == [] and s3.puts == []


@pytest.mark.asyncio
async def test_unexpected_archive_error_does_not_fail_the_send(env, sent, monkeypatch):
    class _Exploding(_FakeS3):
        async def put_object(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("credential refresh failed")

    monkeypatch.setattr("ses_sender._SESSION", _FakeSession(_Exploding()))

    result = await send_email(to_email="alice@client.example", subject="Report", content=_HTML)

    assert result["status"] == "queued"
    assert result["outbox"] == {"status": "failed", "error": "RuntimeError"}


@pytest.mark.asyncio
async def test_slow_archive_gives_up_without_failing_the_send(env, sent, monkeypatch):
    class _Hanging(_FakeS3):
        async def put_object(self, **_kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(10)
            return {}

    monkeypatch.setattr("ses_sender._SESSION", _FakeSession(_Hanging()))
    monkeypatch.setattr("outbox._ARCHIVE_DEADLINE_SECONDS", 0.05)

    result = await send_email(to_email="alice@client.example", subject="Report", content=_HTML)

    assert result["status"] == "queued"
    assert result["outbox"] == {"status": "failed", "error": "timeout"}


@pytest.mark.asyncio
async def test_non_ascii_body_is_never_8bit(env, sent, s3):
    await send_email(to_email="alice@client.example", subject="Report", content=framed("<p>Revenue €12k</p>"))

    raw = sent[0]["Content"]["Raw"]["Data"]
    assert raw.isascii()
    assert "€12k" in sent_body(sent[0])[1]


@pytest.mark.asyncio
async def test_subject_with_a_line_break_is_a_validation_error(env, sent, s3):
    result = await send_email(to_email="alice@client.example", subject="Report\nBcc: evil@x.example", content=_HTML)

    assert result["error"].startswith("Validation Failed")
    assert sent == []


@pytest.mark.asyncio
async def test_message_over_the_ses_limit_is_refused_before_sending(env, sent, s3, monkeypatch):
    monkeypatch.setattr("outbox.MAX_MESSAGE_BYTES", 1024)

    result = await send_email(to_email="alice@client.example", subject="Report", content=_HTML + " " * 2048)

    assert "SES accepts at most 40MB" in result["error"]
    assert sent == []

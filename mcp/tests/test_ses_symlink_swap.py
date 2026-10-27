"""send_email / render_email / validate_email_content read and write each
validated path exactly ONCE, through a descriptor that cannot be redirected.

The run workspace is shared with the sandbox, and this server runs on the
host. A path used to be checked with realpath and then re-opened by name, so
code in the sandbox could swap the file (or a directory above it) for a
symlink between the check and the open and have the host mail, or overwrite,
any file the server's user can reach. Each test here performs that swap right
after the path check passes and asserts the outside file is neither sent nor
touched.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
import ses_sender
from email_fixtures import framed

SECRET = b"host-secret: do not mail me\n"


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(SECRET)
    unused_tmp = tmp_path / "unused-tempdir"
    unused_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(unused_tmp))
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(ws))
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-symlink-swap")
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    monkeypatch.setenv("SES_FROM_EMAIL", "victoria@example.com")
    monkeypatch.setenv("SES_ALLOWED_RECIPIENT_DOMAINS", "example.com")
    monkeypatch.chdir(ws)
    return {"ws": ws, "outside": outside, "secret": outside / "secret.txt"}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    async def fake_send(request):
        captured.append(request)
        return {"status_code": 200, "message_id": "m-1"}

    monkeypatch.setattr(ses_sender, "_send_ses", fake_send)
    # The send-once ledger is irrelevant here.
    monkeypatch.setattr(ses_sender.run_ledger, "task_email_key", lambda: None)
    return captured


def _swap_after(monkeypatch: pytest.MonkeyPatch, name: str, swap, *, when: Path | None = None) -> None:
    """Run ``swap`` right after ses_sender.<name> has approved a path (only the
    path ``when``, if given), i.e. between the check and the open."""
    original = getattr(ses_sender, name)

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if when is None or str(args[0]) == str(when):
            swap()
        return result

    monkeypatch.setattr(ses_sender, name, wrapped)


def _leaf_swap(path: Path, target: Path):
    def swap() -> None:
        if path.exists() or path.is_symlink():
            path.unlink()
        path.symlink_to(target)

    return swap


def _dir_swap(directory: Path, target_dir: Path):
    def swap() -> None:
        moved = directory.with_name(directory.name + ".orig")
        directory.rename(moved)
        directory.symlink_to(target_dir, target_is_directory=True)

    return swap


def _attachment_bytes(request: dict[str, Any]) -> list[bytes]:
    return [a["RawContent"] for a in request["Content"]["Simple"].get("Attachments", [])]


@pytest.mark.asyncio
async def test_attachment_leaf_swapped_for_symlink_is_refused(workspace, sent, monkeypatch):
    report = workspace["ws"] / "report.csv"
    report.write_bytes(b"date,revenue\n")
    _swap_after(monkeypatch, "_locate_attachment", _leaf_swap(report, workspace["secret"]))

    result = await ses_sender.send_email(
        to_email="alice@example.com", subject="Report", content=framed("<p>Hi</p>"), attachments=[str(report)]
    )

    assert "error" in result and "SECURITY" in result["error"]
    assert sent == []


@pytest.mark.asyncio
async def test_attachment_parent_dir_swapped_for_symlink_is_refused(workspace, sent, monkeypatch):
    out = workspace["ws"] / "out"
    out.mkdir()
    (out / "secret.txt").write_bytes(b"harmless\n")
    _swap_after(monkeypatch, "_locate_attachment", _dir_swap(out, workspace["outside"]))

    result = await ses_sender.send_email(
        to_email="alice@example.com",
        subject="Report",
        content=framed("<p>Hi</p>"),
        attachments=[str(out / "secret.txt")],
    )

    assert "error" in result and "SECURITY" in result["error"]
    assert all(SECRET not in b for r in sent for b in _attachment_bytes(r))
    assert sent == []


@pytest.mark.asyncio
async def test_inline_attachment_swap_is_refused(workspace, sent, monkeypatch):
    logo = workspace["ws"] / "logo.png"
    logo.write_bytes(b"png")
    _swap_after(monkeypatch, "_locate_attachment", _leaf_swap(logo, workspace["secret"]))

    result = await ses_sender.send_email(
        to_email="alice@example.com",
        subject="Report",
        content=framed('<img src="cid:logo"><p>Hi</p>'),
        inline_attachments=[{"path": str(logo), "cid": "logo"}],
    )

    assert "error" in result and "SECURITY" in result["error"]
    assert sent == []


@pytest.mark.asyncio
async def test_content_file_swap_is_refused(workspace, sent, monkeypatch):
    body = workspace["ws"] / "email.html"
    body.write_text(framed("<p>Hi</p>"))
    _swap_after(monkeypatch, "_validate_path_security", _leaf_swap(body, workspace["secret"]))

    result = await ses_sender.send_email(to_email="alice@example.com", subject="Report", content_file=str(body))

    assert "error" in result and "SECURITY" in result["error"]
    assert sent == []


@pytest.mark.asyncio
async def test_validate_email_content_swap_is_refused(workspace, monkeypatch):
    body = workspace["ws"] / "email.html"
    body.write_text(framed("<p>Hi</p>"))
    _swap_after(monkeypatch, "_validate_path_security", _leaf_swap(body, workspace["secret"]))

    result = await ses_sender.validate_email_content(content_file=str(body))

    assert result["valid"] is False
    assert "SECURITY" in result["errors"][0]


@pytest.mark.asyncio
async def test_render_email_output_file_swap_never_writes_outside(workspace, monkeypatch):
    target = workspace["ws"] / "email.html"
    target.write_text("old")
    _swap_after(monkeypatch, "_validate_path_security", _leaf_swap(target, workspace["secret"]), when=target)

    result = await ses_sender.render_email(
        subject="Weekly delivery review",
        blocks=[{"type": "text", "paragraphs": ["Hello"]}],
        output_file=str(target),
        client_name="Example",
    )

    assert "error" in result and "SECURITY" in result["error"]
    assert workspace["secret"].read_bytes() == SECRET


@pytest.mark.asyncio
async def test_render_email_output_dir_swap_never_writes_outside(workspace, monkeypatch):
    out = workspace["ws"] / "out"
    out.mkdir()
    _swap_after(monkeypatch, "_validate_path_security", _dir_swap(out, workspace["outside"]))

    result = await ses_sender.render_email(
        subject="Weekly delivery review",
        blocks=[{"type": "text", "paragraphs": ["Hello"]}],
        output_dir=str(out),
        client_name="Example",
    )

    assert "error" in result and "SECURITY" in result["error"]
    assert sorted(os.listdir(workspace["outside"])) == ["secret.txt"]


@pytest.mark.asyncio
async def test_render_email_without_a_swap_still_writes(workspace):
    result = await ses_sender.render_email(
        subject="Weekly delivery review",
        blocks=[{"type": "text", "paragraphs": ["Hello"]}],
        output_file=str(workspace["ws"] / "new" / "email.html"),
        client_name="Example",
    )

    assert "error" not in result, result
    assert Path(result["content_file"]).read_text().startswith("<!")


@pytest.mark.asyncio
async def test_mobile_rewrite_skips_a_file_replaced_after_reading(workspace, sent, monkeypatch):
    """The ensure_mobile_friendly write-back only rewrites the inode it read."""
    body = workspace["ws"] / "email.html"
    body.write_text(framed("<p>Hi</p>"))
    real_read = ses_sender._read_confined

    def read_then_swap(path, max_bytes, label):
        data = real_read(path, max_bytes, label)
        _leaf_swap(body, workspace["secret"])()
        return data

    monkeypatch.setattr(ses_sender, "_read_confined", read_then_swap)
    monkeypatch.setattr(ses_sender, "ensure_mobile_friendly", lambda html: html + "<!-- upgraded -->")

    await ses_sender.send_email(to_email="alice@example.com", subject="Report", content_file=str(body))

    assert workspace["secret"].read_bytes() == SECRET


@pytest.mark.asyncio
async def test_in_workspace_symlink_still_attaches(workspace, sent):
    """A symlink that already pointed inside the workspace when checked keeps working."""
    artifacts = workspace["ws"] / "artifacts"
    artifacts.mkdir()
    (artifacts / "4f2a").write_bytes(b"date,revenue\n")
    link = workspace["ws"] / "report.csv"
    link.symlink_to(artifacts / "4f2a")

    result = await ses_sender.send_email(
        to_email="alice@example.com", subject="Report", content=framed("<p>Hi</p>"), attachments=[str(link)]
    )

    assert result.get("attachments_count") == 1, result
    assert result["attachments"][0]["name"] == "report.csv"
    assert _attachment_bytes(sent[0]) == [b"date,revenue\n"]


@pytest.mark.asyncio
async def test_fifo_attachment_is_refused_without_blocking(workspace, sent):
    fifo = workspace["ws"] / "pipe.csv"
    os.mkfifo(fifo)

    result = await ses_sender.send_email(
        to_email="alice@example.com", subject="Report", content=framed("<p>Hi</p>"), attachments=[str(fifo)]
    )

    assert "error" in result
    assert sent == []


@pytest.mark.asyncio
async def test_combined_size_counts_the_bytes_read_not_the_earlier_stat(workspace, sent, monkeypatch):
    """A file that grows after fstat (within its own cap) is counted by what is sent."""
    a = workspace["ws"] / "a.csv"
    b = workspace["ws"] / "b.csv"
    a.write_bytes(b"x")
    b.write_bytes(b"x")

    class TinyStat:
        st_size = 1

    big = b"x" * (20 * 1024 * 1024)
    monkeypatch.setattr(ses_sender, "_read_confined", lambda *_a, **_k: (big, TinyStat()))

    result = await ses_sender.send_email(
        to_email="alice@example.com", subject="Report", content=framed("<p>Hi</p>"), attachments=[str(a), str(b)]
    )

    assert "Combined attachments are too large" in result.get("error", "")
    assert sent == []


@pytest.mark.asyncio
async def test_a_failed_render_write_leaves_no_partial_file(workspace, monkeypatch):
    """A write that fails after the file was created (ENOSPC) is cleaned up."""
    import errno

    real_write = os.write

    def no_space(fd, data):
        real_write(fd, bytes(data)[:10])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ses_sender.safe_open.os, "write", no_space)
    out = workspace["ws"] / "out"
    out.mkdir()
    result = await ses_sender.render_email(
        subject="Weekly delivery review",
        blocks=[{"type": "text", "paragraphs": ["Hello"]}],
        output_dir=str(out),
        client_name="Example",
    )
    assert "error" in result
    assert list(out.iterdir()) == []


@pytest.mark.asyncio
async def test_render_output_dir_refusal_is_a_structured_error(workspace, monkeypatch):
    target = workspace["ws"] / "new" / "email.html"

    def refuse(*_a, **_k):
        raise ses_sender.safe_open.UnsafePathError("SECURITY: output_dir path changed")

    monkeypatch.setattr(ses_sender.safe_open, "makedirs", refuse)
    result = await ses_sender.render_email(
        subject="Weekly delivery review",
        blocks=[{"type": "text", "paragraphs": ["Hello"]}],
        output_file=str(target),
        client_name="Example",
    )
    assert "SECURITY" in result["error"]


@pytest.mark.asyncio
async def test_a_root_swapped_between_validation_and_read_is_refused(workspace, sent, monkeypatch, tmp_path):
    """The allowed roots are pinned once per call: a root replaced by a symlink after
    the path check does not get its new target resolved and accepted."""
    ws = workspace["ws"]
    (ws / "report.csv").write_bytes(b"date,revenue\n")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "report.csv").write_bytes(SECRET)

    def swap_root():
        ws.rename(tmp_path / "ws.orig")
        ws.symlink_to(decoy, target_is_directory=True)

    # CUTLASS_RUN_WORKDIR points at a path that will be the symlink: realpath'd
    # fresh per call it would accept the decoy; pinned, the swap is refused.
    _swap_after(monkeypatch, "_locate_attachment", swap_root)
    result = await ses_sender.send_email(
        to_email="alice@example.com",
        subject="Report",
        content=framed("<p>Hi</p>"),
        attachments=[str(ws / "report.csv")],
    )

    assert "error" in result and "SECURITY" in result["error"], result
    assert sent == []


def test_pinned_roots_are_not_re_resolved(tmp_path):
    from utils import safe_open

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_bytes(b"inside")
    pinned = safe_open.pin_roots([str(root)])
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_bytes(SECRET)
    root.rename(tmp_path / "root.orig")
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(root / "a.txt"), pinned)

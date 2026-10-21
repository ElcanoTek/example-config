"""Model-supplied download destinations stay inside the run's writable roots.

download_attachment / download_link_attachment take an optional output_dir
from the model. Mail content is untrusted, so a prompt-injected run could ask
for a destination such as the bundle checkout, whose files fleet loads as
configuration. resolve_download_dir confines it; these tests pin that, and
that concurrent downloads of the same file never share a path.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
import ses_s3_email
from ses_s3_email import build_collision_safe_output_path, resolve_download_dir


@pytest.fixture
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    attachments = workspace / "email_attachments"
    attachments.mkdir(parents=True)
    shared = tmp_path / "workspace-root"
    shared.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(ses_s3_email, "ATTACHMENT_DOWNLOAD_DIR", str(attachments))
    monkeypatch.setenv("EMAIL_ALLOWED_DIRS", str(shared))
    # Neither the temp dir nor cwd may cover `elsewhere` in these tests.
    monkeypatch.setattr(ses_s3_email.tempfile, "gettempdir", lambda: str(workspace))
    monkeypatch.chdir(workspace)
    return {"workspace": workspace, "attachments": attachments, "shared": shared, "elsewhere": elsewhere}


def test_default_is_the_attachment_dir(roots):
    assert resolve_download_dir(None) == str(roots["attachments"])
    assert resolve_download_dir("") == str(roots["attachments"])


def test_workspace_and_workspace_root_are_allowed(roots):
    sub = roots["workspace"] / "reports"
    assert resolve_download_dir(str(sub)) == os.path.realpath(sub)
    assert resolve_download_dir(str(roots["shared"] / "run-1")) == os.path.realpath(roots["shared"] / "run-1")


def test_outside_the_roots_is_refused(roots):
    with pytest.raises(ValueError, match="outside the writable roots"):
        resolve_download_dir(str(roots["elsewhere"]))
    with pytest.raises(ValueError):
        resolve_download_dir("/etc")


def test_bundle_checkout_is_refused_even_when_a_root_covers_it(roots, monkeypatch):
    bundle = Path(ses_s3_email._BUNDLE_ROOT)
    monkeypatch.setenv("EMAIL_ALLOWED_DIRS", str(bundle.parent))
    with pytest.raises(ValueError):
        resolve_download_dir(str(bundle / "personas"))
    with pytest.raises(ValueError):
        resolve_download_dir(str(bundle))


def test_symlink_escaping_a_root_is_refused(roots):
    link = roots["workspace"] / "escape"
    link.symlink_to(roots["elsewhere"], target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_download_dir(str(link))


def test_relative_traversal_is_refused(roots):
    with pytest.raises(ValueError):
        resolve_download_dir(str(roots["workspace"] / ".." / "elsewhere"))


async def test_link_download_refuses_a_bad_destination_cleanly(roots):
    # A refused destination comes back as a clean error before any network
    # call or file write, not as an exception.
    result = await ses_s3_email.download_link_attachment(
        url="https://example.com/report.csv", output_dir=str(roots["elsewhere"])
    )
    assert result["status"] == "error"
    assert "outside the writable roots" in result["error"]
    assert not any(roots["elsewhere"].iterdir())


def test_concurrent_reservations_never_share_a_path(tmp_path):
    paths: list[Path] = []
    lock = threading.Lock()

    def reserve():
        p = build_collision_safe_output_path(tmp_path, "report.csv", "same-source")
        with lock:
            paths.append(p)

    threads = [threading.Thread(target=reserve) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(paths)) == 16
    assert all(p.exists() for p in paths), "each path is reserved on disk before it is returned"


def test_default_dir_inside_the_bundle_is_refused(roots, monkeypatch):
    monkeypatch.setattr(ses_s3_email, "ATTACHMENT_DOWNLOAD_DIR", str(Path(ses_s3_email._BUNDLE_ROOT) / "personas"))
    with pytest.raises(ValueError):
        resolve_download_dir(None)


def test_directory_swapped_for_a_symlink_after_the_check_is_refused(roots):
    target = roots["workspace"] / "reports"
    target.mkdir()
    checked = resolve_download_dir(str(target))
    # Between the check and the write, the directory becomes a symlink out.
    target.rmdir()
    target.symlink_to(roots["elsewhere"], target_is_directory=True)
    with pytest.raises(ValueError):
        ses_s3_email.reserve_output_file(checked, "report.csv", "src")
    assert not any(roots["elsewhere"].iterdir())


async def test_a_reserved_file_swapped_for_a_symlink_cannot_redirect_the_write(roots):
    victim = roots["elsewhere"] / "victim.txt"
    victim.write_text("original")
    reserved = ses_s3_email.reserve_output_file(str(roots["attachments"]), "report.csv", "src")
    reserved.path.unlink()
    reserved.path.symlink_to(victim)
    await ses_s3_email.write_reserved(reserved, b"attacker-chosen bytes")
    assert victim.read_text() == "original"


def test_missing_directories_are_created_by_the_confined_walk(roots):
    deep = roots["workspace"] / "reports" / "2026" / "10"
    reserved = ses_s3_email.reserve_output_file(str(deep), "report.csv", "src")
    os.close(reserved.fd)
    assert reserved.path.parent == deep and reserved.path.exists()


def test_walk_refuses_a_symlinked_component_and_creates_nothing_outside(roots):
    (roots["workspace"] / "reports").symlink_to(roots["elsewhere"], target_is_directory=True)
    with pytest.raises(ValueError):
        ses_s3_email.reserve_output_file(str(roots["workspace"] / "reports" / "new" / "sub"), "r.csv", "src")
    assert not any(roots["elsewhere"].iterdir()), "nothing may be created through the symlink"


def test_reservation_never_needs_procfs(roots, monkeypatch):
    real_readlink = os.readlink

    def no_proc(path, *args, **kwargs):
        if str(path).startswith("/proc/"):
            raise OSError("no procfs")
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", no_proc)
    reserved = ses_s3_email.reserve_output_file(str(roots["attachments"]), "r.csv", "src")
    os.close(reserved.fd)
    assert reserved.path.exists()


async def test_write_reserved_closes_the_fd_when_the_hand_off_fails(roots, monkeypatch):
    reserved = ses_s3_email.reserve_output_file(str(roots["attachments"]), "r.csv", "src")

    def broken_to_thread(*_args, **_kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(ses_s3_email.asyncio, "to_thread", broken_to_thread)
    with pytest.raises(RuntimeError):
        await ses_s3_email.write_reserved(reserved, b"x")
    with pytest.raises(OSError):
        os.fstat(reserved.fd)  # closed, not leaked


async def test_bulk_download_refuses_before_reading_s3(roots, monkeypatch):
    async def no_s3(*args, **kwargs):
        raise AssertionError("S3 must not be read for a refused destination")

    monkeypatch.setattr(ses_s3_email, "extract_download_links", no_s3)
    result = await ses_s3_email.download_all_link_attachments(
        s3_key="emails/2026/10/20/x", output_dir=str(roots["elsewhere"])
    )
    assert result["status"] == "error"
    assert "outside the writable roots" in result["error"]


async def test_attachment_download_refuses_before_reading_s3(roots, monkeypatch):
    class NoS3:
        def client(self, *_args, **_kwargs):
            raise AssertionError("S3 must not be read for a refused destination")

    monkeypatch.setattr(ses_s3_email, "session", NoS3())
    result = await ses_s3_email.download_attachment(
        s3_key="emails/2026/10/20/x", filename="report.csv", output_dir=str(roots["elsewhere"])
    )
    assert result["status"] == "error"
    assert "outside the writable roots" in result["error"]

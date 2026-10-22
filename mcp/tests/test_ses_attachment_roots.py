"""Attachment read roots for the outbound SES connector.

``_validate_path_security`` decides what `send_email` is allowed to read off
disk and mail out. It used to start that allowlist with the process's cwd —
and fleet used to spawn every stdio MCP server with cwd = the bundle root,
which is still what happens on an older fleet and on a run with no workspace
to offer. The whole client-config checkout was therefore an attachable source:
the manifest, the servers, and whatever connector output had accumulated in
the operator's git tree.

What the roots are now:

  - the bundle checkout                      -> REFUSED (never a root)
  - CUTLASS_RUN_WORKDIR (${FLEET_WORKSPACE}) -> allowed, where task output lives
  - a cwd that is not the bundle checkout    -> allowed (a local coding agent
    registers this server with cwd = the user's own project; see
    docs/TESTING-LOCALLY.md and INSTALL.md)
  - the system temp dir, CUTLASS_ALLOWED_DIRS -> unchanged
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import ses_sender
from ses_sender import _validate_path_security

BUNDLE_ROOT = Path(ses_sender.__file__).resolve().parent.parent


@pytest.fixture
def isolated_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Strip every root but cwd, so each test's acceptance has one cause.

    The temp dir is pointed at an empty directory of its own: pytest's
    ``tmp_path`` lives under the real ``gettempdir()``, so without this a
    "cwd is allowed" assertion would pass even if cwd had been dropped.
    """
    monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    unused_tmp = tmp_path / "unused-tempdir"
    unused_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(unused_tmp))
    return unused_tmp


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("report\n")
    return path


def test_bundle_root_is_refused_when_it_is_the_cwd(isolated_roots, monkeypatch):
    monkeypatch.chdir(BUNDLE_ROOT)

    with pytest.raises(ValueError, match="outside allowed directories"):
        _validate_path_security(str(BUNDLE_ROOT / "manifest.yaml"))


def test_file_nested_in_the_bundle_checkout_is_refused(isolated_roots, monkeypatch):
    monkeypatch.chdir(BUNDLE_ROOT)

    with pytest.raises(ValueError, match="outside allowed directories"):
        _validate_path_security(str(BUNDLE_ROOT / "mcp" / "ses_sender.py"))


def test_relative_path_resolved_against_the_bundle_root_is_refused(isolated_roots, monkeypatch):
    """The shape the agent actually produces: a bare `report.csv` while the
    server was spawned in the checkout."""
    monkeypatch.chdir(BUNDLE_ROOT)

    with pytest.raises(ValueError, match="outside allowed directories"):
        _validate_path_security("manifest.yaml")


def test_run_workspace_is_an_allowed_root(isolated_roots, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    attachment = _touch(workspace / "deal_sheet.xlsx")
    # cwd stays the bundle root, so acceptance can only come from the workspace.
    monkeypatch.chdir(BUNDLE_ROOT)
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(workspace))

    assert _validate_path_security(str(attachment)) == str(attachment)


def test_blank_run_workspace_adds_no_root(isolated_roots, monkeypatch, tmp_path):
    """fleet drops the key when there is no workspace; a whitespace value must
    not turn into an allowed root (or a crash)."""
    attachment = _touch(tmp_path / "elsewhere" / "deal_sheet.xlsx")
    monkeypatch.chdir(BUNDLE_ROOT)
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", "   ")

    with pytest.raises(ValueError, match="outside allowed directories"):
        _validate_path_security(str(attachment))


def test_non_bundle_cwd_is_still_an_allowed_root(isolated_roots, monkeypatch, tmp_path):
    """The local coding-agent case: cwd is the user's project directory."""
    project = tmp_path / "user-project"
    attachment = _touch(project / "q3_report.csv")
    monkeypatch.chdir(project)

    assert _validate_path_security(str(attachment)) == str(attachment)


def test_tempdir_is_still_an_allowed_root(monkeypatch, tmp_path):
    monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    fake_tmp = tmp_path / "systmp"
    attachment = _touch(fake_tmp / "generated.pdf")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))
    monkeypatch.chdir(BUNDLE_ROOT)

    assert _validate_path_security(str(attachment)) == str(attachment)


def test_cutlass_allowed_dirs_is_still_honored(isolated_roots, monkeypatch, tmp_path):
    extra = tmp_path / "operator-share"
    attachment = _touch(extra / "brief.docx")
    monkeypatch.chdir(BUNDLE_ROOT)
    monkeypatch.setenv("CUTLASS_ALLOWED_DIRS", f"{extra}:")

    assert _validate_path_security(str(attachment)) == str(attachment)


def test_path_outside_every_root_is_still_refused(isolated_roots, monkeypatch, tmp_path):
    project = tmp_path / "user-project"
    project.mkdir()
    outside = _touch(tmp_path / "somewhere-else" / "secret.txt")
    monkeypatch.chdir(project)

    with pytest.raises(ValueError, match="outside allowed directories"):
        _validate_path_security(str(outside))

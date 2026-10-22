"""Attachment path resolution for the host-side SES outbound server.

A production run wrote its CSV inside the sandbox, then the send failed with a
bare "Attachment file not found" and the model's only recovery was to send the
report again WITHOUT the file. These pin the two fixes: a misreferenced file is
recovered when exactly one same-named file exists under the deliberate roots,
and an unrecoverable path fails with a message that names what was received,
what it resolved to, and where this server can read from.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from email_fixtures import framed
from ses_sender import _locate_attachment, send_email

_VALID_HTML = framed("<p>Hello there.</p>")


@pytest.fixture(autouse=True)
def _recipient_allowlist(monkeypatch: pytest.MonkeyPatch):
    """run_roots sets CUTLASS_RUN_WORKDIR (the managed-run marker), which also
    engages the recipient-allowlist fail-closed default. Satisfy
    the allowlist so this suite tests ONLY attachment resolution; the allowlist
    behavior itself is covered by test_ses_recipient_allowlist.py."""
    monkeypatch.setenv("SES_ALLOWED_RECIPIENT_DOMAINS", "example.com")


@pytest.fixture
def ses_send_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror test_ses_sender.ses_env, without its CUTLASS_RUN_WORKDIR delenv
    which would fight run_roots."""
    monkeypatch.setenv("SES_FROM_EMAIL", "victoria@northwind.example")


@pytest.fixture
def capture_request(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_send(request):
        captured.update(request)
        return {"status_code": 200, "message_id": "ses-message-id"}

    monkeypatch.setattr("ses_sender._send_ses", fake_send)
    return captured


@pytest.fixture
def run_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A run workdir and a DIFFERENT cwd, both under the allowed tempdir."""
    workdir = tmp_path / "mcp-run"
    workdir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(workdir))
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-under-test")  # dedicated per-run client
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    monkeypatch.chdir(elsewhere)
    return workdir, elsewhere


def test_bare_name_recovers_from_run_workdir(run_roots):
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("a,b\n1,2\n")
    assert os.path.realpath(_locate_attachment("report.csv")) == os.path.realpath(workdir / "report.csv")


def test_wrong_directory_recovers_by_base_name(run_roots, tmp_path: Path):
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("a,b\n1,2\n")
    wrong = tmp_path / "somewhere-else" / "report.csv"  # never created
    assert os.path.realpath(_locate_attachment(str(wrong))) == os.path.realpath(workdir / "report.csv")


def test_ambiguous_candidates_are_not_guessed(run_roots):
    """Both probes (the relative path as given, and its base name) can match
    different files in the run workdir; then nothing is guessed and both are
    named so the model can pick."""
    workdir, _ = run_roots
    (workdir / "sub").mkdir()
    (workdir / "sub" / "report.csv").write_text("one")
    (workdir / "report.csv").write_text("two")
    with pytest.raises(FileNotFoundError) as exc:
        _locate_attachment("sub/report.csv")  # cwd has no such file; both probes hit in the workdir
    msg = str(exc.value)
    assert "2 same-named candidates" in msg or "candidates" in msg
    assert str(workdir / "report.csv") in msg


def test_unrecoverable_path_names_received_path_and_roots(run_roots):
    workdir, elsewhere = run_roots
    with pytest.raises(FileNotFoundError) as exc:
        _locate_attachment("missing.csv ")  # a stray trailing space, as a model might emit
    msg = str(exc.value)
    assert "'missing.csv '" in msg  # repr of what was received
    assert "resolved to" in msg
    assert str(workdir) in msg and str(elsewhere) in msg
    assert "os.path.abspath" in msg


def test_existing_absolute_path_is_used_verbatim(run_roots):
    workdir, elsewhere = run_roots
    (workdir / "report.csv").write_text("one")
    (elsewhere / "report.csv").write_text("two")  # would be ambiguous if we ever guessed
    assert os.path.realpath(_locate_attachment(str(elsewhere / "report.csv"))) == os.path.realpath(
        elsewhere / "report.csv"
    )


def test_outside_roots_still_refused(run_roots):
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment("/etc/passwd")


@pytest.mark.asyncio
async def test_send_email_attaches_recovered_file_and_accepts_object_entries(run_roots, ses_send_env, capture_request):
    workdir, _ = run_roots
    (workdir / "deal_daily_export.csv").write_text("date,spend\n2026-09-14,1.0\n")
    result = await send_email(
        to_email="test@example.com",
        subject="Weekly report",
        content=_VALID_HTML,
        attachments=[{"path": "deal_daily_export.csv"}],
    )
    assert result.get("status") == "queued", result
    assert result.get("attachments_count") == 1
    assert result["attachments"][0]["name"] == "deal_daily_export.csv"
    assert capture_request["Content"]["Simple"]["Attachments"][0]["FileName"] == "deal_daily_export.csv"


@pytest.mark.asyncio
async def test_send_email_reports_actionable_error_for_missing_attachment(run_roots, ses_send_env, capture_request):
    workdir, _ = run_roots
    result = await send_email(
        to_email="test@example.com",
        subject="Weekly report",
        content=_VALID_HTML,
        attachments=[str(workdir / "never_written.csv")],
    )
    assert "error" in result
    assert "never_written.csv" in result["error"]
    assert "readable roots" in result["error"]
    assert str(workdir) in result["error"]
    assert capture_request == {}


def test_bare_name_recovers_when_cwd_is_the_bundle_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The fleet fallback that spawns the server in the bundle root must still
    find a file the run wrote into CUTLASS_RUN_WORKDIR — the security check on
    the missing path must not pre-empt recovery."""
    from ses_sender import _BUNDLE_ROOT

    workdir = tmp_path / "mcp-run"
    workdir.mkdir()
    (workdir / "report.csv").write_text("a\n")
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(workdir))
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-under-test")  # dedicated per-run client
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    monkeypatch.chdir(_BUNDLE_ROOT)
    assert os.path.realpath(_locate_attachment("report.csv")) == os.path.realpath(workdir / "report.csv")


def test_other_readable_roots_are_not_searched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """CUTLASS_ALLOWED_DIRS and the temp dir stay readable for an explicit path
    but are never recovery roots: a same-named file there must not be mailed
    in place of the one the run meant, and the diagnostic still names every
    readable root so the model can pass the right absolute path."""
    workdir = tmp_path / "mcp-run"
    workdir.mkdir()
    mounted = tmp_path / "mounted-output"
    mounted.mkdir()
    (mounted / "report.csv").write_text("someone else's")
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(workdir))
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-under-test")  # dedicated per-run client
    monkeypatch.setenv("CUTLASS_ALLOWED_DIRS", str(mounted))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError) as exc:
        _locate_attachment("report.csv")
    msg = str(exc.value)
    assert str(mounted) in msg  # listed as readable
    assert "No file with that name in the run workdir" in msg
    # explicit path into an allowed dir still works verbatim
    assert os.path.realpath(_locate_attachment(str(mounted / "report.csv"))) == os.path.realpath(mounted / "report.csv")


def test_absolute_path_outside_roots_is_refused_whether_or_not_it_exists(run_roots):
    """No existence oracle: an absolute path outside the roots gets the same
    SECURITY verdict for a real file and for a missing one."""
    from ses_sender import _BUNDLE_ROOT

    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment(os.path.join(_BUNDLE_ROOT, "manifest.yaml"))  # exists
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment(os.path.join(_BUNDLE_ROOT, "definitely-missing.csv"))  # does not


def test_raw_path_is_preserved_and_never_swapped(run_roots):
    """'report.csv ' (trailing space) and 'report.csv' are different files;
    asking for the first must attach the first, not silently the second."""
    from ses_sender import _attachment_entry_path

    workdir, _ = run_roots
    (workdir / "report.csv ").write_text("spaced")
    (workdir / "report.csv").write_text("plain")
    assert _attachment_entry_path("report.csv ") == "report.csv "
    assert _attachment_entry_path({"path": "report.csv "}) == "report.csv "
    assert _attachment_entry_path("   ") == ""
    assert Path(_locate_attachment("report.csv ")).read_text() == "spaced"


def test_tool_schema_accepts_object_attachments():
    """FastMCP derives the tool schema from the annotation: object entries must
    be admitted there, not only by the Python function."""
    import inspect

    from ses_sender import send_email as tool

    hint = str(inspect.signature(tool).parameters["attachments"].annotation)
    assert "Mapping" in hint and "str" in hint


def test_symlink_escaping_the_roots_is_not_a_candidate(run_roots):
    """A symlink planted under the run workdir that points outside the readable
    roots: the caller's own probe lands on it, so it is refused with the
    SECURITY error — never attached, never replaced — and the target path is
    not echoed."""
    from ses_sender import _BUNDLE_ROOT

    workdir, _ = run_roots
    target = Path(_BUNDLE_ROOT) / "manifest.yaml"
    assert target.is_file()
    (workdir / "manifest.yaml").symlink_to(target)
    with pytest.raises(ValueError, match="SECURITY") as exc:
        _locate_attachment("manifest.yaml")
    assert str(target) not in str(exc.value)


def test_dotdot_probe_cannot_escape_the_search_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A missing relative path carrying '..' that would leave the run workdir
    when joined to it is refused (SECURITY) rather than turned into a
    candidate — even when the escaped file sits under another readable root."""
    workdir = tmp_path / "mcp-run"
    workdir.mkdir()
    sibling = tmp_path / "mcp-run-sibling"
    sibling.mkdir()
    (sibling / "report.csv").write_text("outside the workdir")
    deep = tmp_path / "elsewhere" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(workdir))
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-under-test")  # dedicated per-run client
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    monkeypatch.chdir(deep)  # cwd-relative resolution misses; workdir + '..' would hit
    with pytest.raises(ValueError, match="SECURITY") as exc:
        _locate_attachment("../mcp-run-sibling/report.csv")
    assert str(sibling) not in str(exc.value)


def test_existing_directory_is_refused_not_recovered(run_roots):
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the real one")
    (workdir / "out").mkdir()
    (workdir / "out" / "report.csv").mkdir()  # a DIRECTORY at the named path
    with pytest.raises(FileNotFoundError, match="not a regular file"):
        _locate_attachment(str(workdir / "out" / "report.csv"))


def test_recovered_symlink_keeps_its_own_name(run_roots):
    """An allowed symlink recovered by name is returned as the matched path, so
    the attachment keeps 'report.csv' / text/csv rather than its target's name."""
    workdir, _ = run_roots
    (workdir / "artifacts").mkdir()
    (workdir / "artifacts" / "4f2a").write_text("a,b\n")
    (workdir / "report.csv").symlink_to(workdir / "artifacts" / "4f2a")
    got = _locate_attachment("somewhere/report.csv")
    assert os.path.basename(got) == "report.csv"


@pytest.mark.asyncio
async def test_inline_attachment_path_is_passed_raw(run_roots, ses_send_env, capture_request):
    """Inline entries get the same raw-path handling as regular attachments."""
    workdir, _ = run_roots
    (workdir / "chart.png ").write_bytes(b"spaced")
    (workdir / "chart.png").write_bytes(b"plain")
    html = framed('<div><img src="cid:chart"></div>')
    result = await send_email(
        to_email="test@example.com",
        subject="Weekly report",
        content=html,
        inline_attachments=[{"path": str(workdir / "chart.png "), "cid": "chart"}],
    )
    assert result.get("status") == "queued", result
    assert result["inline_attachments"][0]["size"] == len(b"spaced")
    inline = capture_request["Content"]["Simple"]["Attachments"][0]
    assert inline["FileName"] == "chart.png "
    assert inline["RawContent"] == b"spaced"


def test_relative_traversal_is_refused_without_probing(run_roots):
    """A relative path climbing out of cwd is judged like an absolute one: the
    SECURITY refusal, whether or not the target exists, and no recovery."""
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment("../" * 12 + "etc/passwd")  # exists on most hosts
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment("../" * 12 + "etc/definitely-not-here.csv")  # does not


def test_bare_name_colliding_with_bundle_file_still_recovers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """cwd == bundle checkout and a bundle file shares the requested name: the
    forbidden location is neither probed nor refused, and the run workdir copy
    is recovered."""
    from ses_sender import _BUNDLE_ROOT

    workdir = tmp_path / "mcp-run"
    workdir.mkdir()
    (workdir / "manifest.yaml").write_text("the run's own file")
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(workdir))
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-under-test")  # dedicated per-run client
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    monkeypatch.chdir(_BUNDLE_ROOT)
    assert (Path(_BUNDLE_ROOT) / "manifest.yaml").is_file()
    assert os.path.realpath(_locate_attachment("manifest.yaml")) == os.path.realpath(workdir / "manifest.yaml")


def test_safe_parent_segments_do_not_count_as_escaping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """'outputs/../report.csv' normalizes inside cwd: an ordinary relative
    reference, recovered from the run workdir even when cwd is the bundle root."""
    from ses_sender import _BUNDLE_ROOT

    workdir = tmp_path / "mcp-run"
    workdir.mkdir()
    (workdir / "report.csv").write_text("a\n")
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(workdir))
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-under-test")  # dedicated per-run client
    monkeypatch.delenv("CUTLASS_ALLOWED_DIRS", raising=False)
    monkeypatch.chdir(_BUNDLE_ROOT)
    assert os.path.realpath(_locate_attachment("outputs/../report.csv")) == os.path.realpath(workdir / "report.csv")


def test_dangling_symlink_is_refused_not_recovered(run_roots):
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the other file")
    (workdir / "out").mkdir()
    (workdir / "out" / "report.csv").symlink_to(workdir / "out" / "missing-target.csv")
    with pytest.raises(FileNotFoundError, match="not a regular file"):
        _locate_attachment(str(workdir / "out" / "report.csv"))


def test_shared_workdir_without_task_id_does_not_recover(run_roots, monkeypatch: pytest.MonkeyPatch):
    """fleet's shared chat client: one mcp-shared workdir for every conversation
    and no task id. A same-named file there may be someone else's, so nothing
    is searched and the diagnostic says so."""
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("another conversation's file")
    monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)
    with pytest.raises(FileNotFoundError) as exc:
        _locate_attachment("report.csv")
    assert "shared workdir" in str(exc.value)
    assert str(workdir / "report.csv") not in str(exc.value)
    # an absolute path that names nothing still gets the diagnostic, with the
    # shared workdir explicitly marked as not searched
    with pytest.raises(FileNotFoundError) as exc2:
        _locate_attachment(str(workdir / "never.csv"))
    assert "not searched: shared run workdir" in str(exc2.value)


def test_shared_workdir_rejects_relative_names_even_when_present(run_roots, monkeypatch: pytest.MonkeyPatch):
    """Shared chat client (run workdir, no task id) with cwd == that workdir: a
    bare 'report.csv' that exists there may be another conversation's, so it is
    refused with the absolute-path instruction rather than attached."""
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("someone else's")
    monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)
    monkeypatch.chdir(workdir)
    with pytest.raises(FileNotFoundError, match="shared workdir"):
        _locate_attachment("report.csv")
    # an explicit absolute path into the workdir is still honoured
    assert os.path.realpath(_locate_attachment(str(workdir / "report.csv"))) == os.path.realpath(workdir / "report.csv")


def test_relative_symlink_escaping_roots_is_refused_not_recovered(run_roots):
    """cwd is a readable root; 'out/report.csv' there is a symlink to a file
    outside every root while the run workdir holds a real report.csv. The
    named path must get the SECURITY refusal, not the workdir file."""
    from ses_sender import _BUNDLE_ROOT

    workdir, elsewhere = run_roots
    (workdir / "report.csv").write_text("the run's real file")
    (elsewhere / "out").mkdir()
    (elsewhere / "out" / "report.csv").symlink_to(Path(_BUNDLE_ROOT) / "manifest.yaml")
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment("out/report.csv")


def test_unreadable_directory_surfaces_permission_error_not_recovery(run_roots):
    """An allowed path below a directory the server cannot traverse must fail
    as a permission problem, never be treated as missing and replaced by a
    same-named file from the run workdir."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the other file")
    locked = workdir / "locked"
    locked.mkdir()
    (locked / "report.csv").write_text("unreachable")
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            _locate_attachment(str(locked / "report.csv"))
    finally:
        locked.chmod(0o700)


def test_symlinked_parent_escaping_roots_is_refused_even_for_missing_leaf(run_roots):
    """cwd is allowed and holds out -> <outside>. Both out/<existing> and
    out/<missing> get the SECURITY refusal: no probe, no oracle, no recovery."""
    from ses_sender import _BUNDLE_ROOT

    workdir, elsewhere = run_roots
    (workdir / "missing.csv").write_text("would be wrongly recovered")
    (elsewhere / "out").symlink_to(_BUNDLE_ROOT)
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment("out/manifest.yaml")  # exists behind the symlink
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment("out/missing.csv")  # does not


def test_recovery_probe_permission_error_aborts_recovery(run_roots):
    """An unreadable directory inside the run workdir along a recovery probe
    must abort recovery with the permission error, not be skipped in favour of
    a same-named readable file."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("readable twin")
    locked = workdir / "locked"
    locked.mkdir()
    (locked / "report.csv").write_text("unreachable")
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            _locate_attachment("locked/report.csv")  # cwd is elsewhere: probes workdir/locked/report.csv
    finally:
        locked.chmod(0o700)


def test_file_as_parent_directory_is_refused_not_recovered(run_roots):
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the other file")
    (workdir / "archive").write_text("I am a file, not a directory")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        _locate_attachment(str(workdir / "archive" / "report.csv"))


def test_relative_probe_hitting_a_non_file_in_the_workdir_is_refused(run_roots):
    """cwd differs from the run workdir; 'sub/report.csv' is a DIRECTORY in the
    workdir while workdir/report.csv is a file: refuse, do not attach the twin."""
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the twin")
    (workdir / "sub").mkdir()
    (workdir / "sub" / "report.csv").mkdir()
    with pytest.raises(FileNotFoundError, match="not a regular file"):
        _locate_attachment("sub/report.csv")


def test_relative_probe_that_is_a_dangling_symlink_is_refused(run_roots):
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the twin")
    (workdir / "sub").mkdir()
    (workdir / "sub" / "report.csv").symlink_to(workdir / "sub" / "gone.csv")
    with pytest.raises(FileNotFoundError, match="not a regular file"):
        _locate_attachment("sub/report.csv")


def test_symlink_to_unreadable_target_surfaces_permission_error(run_roots):
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    workdir, _ = run_roots
    locked = workdir / "locked"
    locked.mkdir()
    (locked / "report.csv").write_text("unreachable")
    (workdir / "link.csv").symlink_to(locked / "report.csv")
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            _locate_attachment(str(workdir / "link.csv"))
    finally:
        locked.chmod(0o700)


def test_exact_relative_probe_through_a_file_as_directory_is_refused(run_roots):
    """cwd elsewhere; the workdir has a regular FILE named 'archive' and a
    report.csv twin: 'archive/report.csv' is refused, not replaced."""
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the twin")
    (workdir / "archive").write_text("I am a file")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        _locate_attachment("archive/report.csv")


def test_exact_relative_probe_that_escapes_is_refused_not_replaced(run_roots):
    """cwd elsewhere; workdir/sub/report.csv is a symlink leaving the roots and
    workdir/report.csv is a real file: 'sub/report.csv' is refused (SECURITY),
    never swapped for the twin."""
    from ses_sender import _BUNDLE_ROOT

    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the twin")
    (workdir / "sub").mkdir()
    (workdir / "sub" / "report.csv").symlink_to(Path(_BUNDLE_ROOT) / "manifest.yaml")
    with pytest.raises(ValueError, match="SECURITY"):
        _locate_attachment("sub/report.csv")


def test_shared_client_refuses_traversal_relative_paths_too(run_roots, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Shared chat client: '../x.csv' must be refused like any relative name,
    even when it would resolve inside a readable root."""
    workdir, elsewhere = run_roots
    (tmp_path / "x.csv").write_text("readable but not attributable")
    monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)
    with pytest.raises(FileNotFoundError, match="shared workdir"):
        _locate_attachment("../x.csv")


def test_dangling_symlink_parent_is_refused_not_recovered(run_roots):
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the twin")
    (workdir / "sub").symlink_to(workdir / "vanished-dir")
    with pytest.raises(FileNotFoundError, match="dangling symlink or missing directory"):
        _locate_attachment("sub/report.csv")


def test_dangling_symlink_grandparent_is_refused_not_recovered(run_roots):
    """workdir/sub is a dangling symlink and the request names a path BELOW it
    ('sub/deeper/report.csv'): the one-level parent check would miss it, and
    base-name recovery would attach an unrelated workdir/report.csv."""
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the twin")
    (workdir / "sub").symlink_to(workdir / "vanished-dir")
    with pytest.raises(FileNotFoundError, match="dangling symlink or missing directory"):
        _locate_attachment("sub/deeper/report.csv")


def test_truly_missing_nested_directory_still_recovers(run_roots):
    """A nested path with no dangling component anywhere on the way down is
    ordinary 'missing': base-name recovery still finds the workdir file."""
    workdir, _ = run_roots
    (workdir / "report.csv").write_text("the real one")
    assert os.path.realpath(_locate_attachment("nope/deeper/report.csv")) == os.path.realpath(workdir / "report.csv")

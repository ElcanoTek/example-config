"""Team email templates: saved from a chat into the directory fleet reads.

Covers mcp/email_template_store.py and the three ses_outbound tools in front
of it. The fixture template stands in for "an email a person asked to keep": it
is a complete, valid spec with a sample that fits it. A copy of it placed in
the bundle's own directory stands in for a template the bundle ships.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import email_render
import email_template_store
import pytest
import yaml
from email_template_fixture import spec as fixture_spec
from ses_sender import (
    check_email_template,
    delete_email_template,
    get_email_template,
    list_email_templates,
    render_email,
    save_email_template,
)

import email_templates

BUILT_IN = "built-in-update"


@pytest.fixture(autouse=True)
def _team_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    team = tmp_path / "team"
    bundle = tmp_path / "email_templates"
    bundle.mkdir()
    (bundle / f"{BUILT_IN}.yaml").write_text(yaml.safe_dump(fixture_spec(), sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(email_templates, "TEMPLATES_DIR", bundle)
    monkeypatch.setenv("EMAIL_TEMPLATES_TEAM_DIR", str(team))
    monkeypatch.setenv("EMAIL_TEMPLATE_AUTHOR", "Jordan@Acme.example")
    monkeypatch.setenv("SES_TENANT_LABEL", "Acme")
    monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)
    return team


def _spec(**changes: Any) -> dict[str, Any]:
    return fixture_spec(**{"name": "Monthly QBR recap", **changes})


async def test_a_saved_template_is_listed_enforced_and_previewed(_team_dir: Path, tmp_path: Path) -> None:
    checked = await check_email_template("monthly-qbr-recap", _spec())
    assert checked["valid"] is True and checked["template"] == "monthly-qbr-recap"
    assert not _team_dir.exists(), "checking stores nothing"

    saved = await save_email_template("monthly-qbr-recap", _spec())
    assert saved.get("error") is None, saved
    assert saved["saved"] == "monthly-qbr-recap" and saved["replaced_an_earlier_version"] is False

    stored = yaml.safe_load((_team_dir / "monthly-qbr-recap.yaml").read_text(encoding="utf-8"))
    assert stored["created_by"] == "jordan@acme.example" and stored["created_at"].endswith("Z")
    assert "updated_at" not in stored
    # No form was given, so it has the standard one: the pickers and the approval-card ending.
    assert [f["type"] for f in stored["fields"]] == ["text", "recipients", "palette", "textarea"]
    assert "monthly-qbr-recap" in stored["promptTemplate"]
    assert email_template_store.SEND_LINE in stored["promptTemplate"]
    preview = (_team_dir / "previews" / "monthly-qbr-recap.html").read_text(encoding="utf-8")
    assert preview.startswith("<!DOCTYPE html>") and "cid:" not in preview
    styles = json.loads((_team_dir / "previews" / "styles.json").read_text(encoding="utf-8"))
    assert sorted(styles["options"]) == email_render.available_themes()
    assert set(styles["custom"]) == {"Header", "Accent"}

    listed = {t["template"]: t for t in (await list_email_templates())["templates"]}
    assert listed["monthly-qbr-recap"]["source"] == "team" and listed[BUILT_IN]["source"] == "bundle"
    assert (await get_email_template("monthly-qbr-recap"))["name"] == "Monthly QBR recap"

    # render_email holds an email to it exactly as it does to a built-in template.
    blocks = copy.deepcopy(stored["sample"]["blocks"])
    ok = await render_email(
        stored["sample"]["subject"], blocks, str(tmp_path), template="monthly-qbr-recap", client_name="Acme"
    )
    assert ok.get("error") is None and ok["template"] == "monthly-qbr-recap"
    blocks.insert(2, {"type": "table", "heading": "Deals", "columns": ["Deal"], "rows": [["x"]]})
    refused = await render_email(
        stored["sample"]["subject"], blocks, str(tmp_path), template="monthly-qbr-recap", client_name="Acme"
    )
    assert "does not fit template" in refused["error"]


async def test_saving_again_replaces_and_keeps_who_first_added_it(
    _team_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await save_email_template("monthly-qbr-recap", _spec())
    monkeypatch.setenv("EMAIL_TEMPLATE_AUTHOR", "sam@acme.example")
    again = await save_email_template("monthly-qbr-recap", _spec(name="QBR recap v2", created_by="mallory@example.com"))
    assert again["replaced_an_earlier_version"] is True
    stored = yaml.safe_load((_team_dir / "monthly-qbr-recap.yaml").read_text(encoding="utf-8"))
    assert stored["name"] == "QBR recap v2"
    assert stored["created_by"] == "jordan@acme.example", "the author is fleet's record, never the spec's"
    assert stored["updated_at"].endswith("Z")
    assert (await check_email_template("monthly-qbr-recap", _spec()))["replaces_an_existing_team_template"] is True


@pytest.mark.parametrize(
    ("template_id", "changes", "message"),
    [
        (BUILT_IN, {}, "built-in templates and cannot be replaced"),
        ("Monthly QBR", {}, "not a usable template id"),
        ("../escape", {}, "not a usable template id"),
        ("qbr", {"audience": "everyone"}, "'audience' must be one of"),
        ("qbr", {"sections": []}, "'sections' must be a non-empty list"),
        ("qbr", {"promptTemplate": "Write it."}, "Give both 'fields' and 'promptTemplate'"),
        ("qbr", {"theme": "neon"}, "'theme' 'neon' is not one of"),
        ("qbr", {"max_words": 20}, "sample does not fit the template"),
    ],
)
async def test_a_template_that_is_not_sound_is_refused_and_nothing_is_written(
    _team_dir: Path, template_id: str, changes: dict[str, Any], message: str
) -> None:
    checked = await check_email_template(template_id, _spec(**changes))
    assert checked["valid"] is False and message in checked["problems"]
    saved = await save_email_template(template_id, _spec(**changes))
    assert message in saved["error"]
    assert not _team_dir.exists() or list(_team_dir.glob("*.yaml")) == []


async def test_a_sample_that_breaks_its_own_sections_is_refused(_team_dir: Path) -> None:
    spec = _spec()
    spec["sample"]["blocks"].insert(1, {"type": "table", "columns": ["Deal"], "rows": [["x"]]})
    saved = await save_email_template("qbr", spec)
    assert "sample does not fit the template" in saved["error"] and "has no 'section'" in saved["error"]


async def test_removing_a_team_template(_team_dir: Path) -> None:
    await save_email_template("monthly-qbr-recap", _spec())
    assert "built-in template" in (await delete_email_template(BUILT_IN))["error"]
    assert "no team template" in (await delete_email_template("nope"))["error"]
    assert await delete_email_template("monthly-qbr-recap") == {"removed": "monthly-qbr-recap"}
    assert not (_team_dir / "monthly-qbr-recap.yaml").exists()
    assert not (_team_dir / "previews" / "monthly-qbr-recap.html").exists()
    assert "monthly-qbr-recap" not in email_templates.template_ids()
    assert "unknown email template" in (await get_email_template("monthly-qbr-recap"))["error"]


async def test_not_from_a_scheduled_run_and_not_without_a_place(
    _team_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "0b6e3c1e-task")
    assert "scheduled run cannot" in (await save_email_template("qbr", _spec()))["error"]
    assert "scheduled run cannot" in (await delete_email_template("qbr"))["error"]
    monkeypatch.delenv("CUTLASS_MOC_TASK_ID")
    for unusable in ("", "relative/dir", "${FLEET_WORKSPACE_ROOT}/mcp-runs/email-templates"):
        monkeypatch.setenv("EMAIL_TEMPLATES_TEAM_DIR", unusable)
        assert "no place for team email templates" in (await save_email_template("qbr", _spec()))["error"]
        assert email_templates.team_template_ids() == []
    assert not _team_dir.exists()


async def test_a_team_file_never_shadows_or_escapes(_team_dir: Path, tmp_path: Path) -> None:
    _team_dir.mkdir()
    # A file planted under a built-in id is ignored: the built-in one is what loads.
    (_team_dir / f"{BUILT_IN}.yaml").write_text("name: hijack\n", encoding="utf-8")
    assert email_templates.team_template_ids() == []
    assert (await get_email_template(BUILT_IN))["name"] != "hijack"
    # A link is not a template, and a save never writes through one.
    outside = tmp_path / "outside.yaml"
    outside.write_text("name: outside\n", encoding="utf-8")
    (_team_dir / "linked.yaml").symlink_to(outside)
    assert "linked" not in email_templates.template_ids()
    saved = await save_email_template("linked", _spec())
    assert "is a link" in saved["error"]
    assert outside.read_text(encoding="utf-8") == "name: outside\n"


async def test_the_library_is_capped(_team_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_template_store, "MAX_TEAM_TEMPLATES", 1)
    assert (await save_email_template("first", _spec())).get("error") is None
    assert "already 1 team templates" in (await save_email_template("second", _spec()))["error"]
    # Replacing the one that exists is still allowed.
    assert (await save_email_template("first", _spec(name="First, revised"))).get("error") is None


async def test_no_other_tool_can_write_into_the_library(_team_dir: Path, tmp_path: Path) -> None:
    """render_email takes an output path inside the allowed roots; the library is not one of them."""
    await save_email_template("monthly-qbr-recap", _spec())
    stored = _team_dir / "monthly-qbr-recap.yaml"
    before = stored.read_text(encoding="utf-8")
    blocks = [{"type": "text", "paragraphs": ["Hi team, this is a test."]}]
    for kwargs in (
        {"output_file": str(stored)},
        {"output_file": str(_team_dir / "previews" / "styles.json")},
        {"output_dir": str(_team_dir)},
        {"output_dir": str(_team_dir / "previews")},
    ):
        refused = await render_email("Subject", blocks, client_name="Acme", **kwargs)
        assert "holds the team email templates" in refused["error"], kwargs
    assert stored.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in _team_dir.iterdir()) == ["monthly-qbr-recap.yaml", "previews"]
    # At rest nothing in it is writable, so a tool that opens or replaces a file by path is refused too.
    assert stored.stat().st_mode & 0o222 == 0
    assert _team_dir.stat().st_mode & 0o222 == 0 and (_team_dir / "previews").stat().st_mode & 0o222 == 0
    # ... and the approved tools still work on the locked folder.
    assert (await save_email_template("second", _spec(name="Second"))).get("error") is None
    assert await delete_email_template("second") == {"removed": "second"}
    assert _team_dir.stat().st_mode & 0o222 == 0


async def test_a_style_field_the_renderer_cannot_produce_is_refused_before_anything_is_written(_team_dir: Path) -> None:
    fields, prompt = email_template_store.default_form("qbr", "QBR")
    for change, message in (
        ({"custom": ["Bogus"]}, "custom color 'Bogus'"),
        ({"options": ["neon"]}, "style option 'neon'"),
    ):
        style = dict(next(f for f in fields if f["key"] == "style"), **change)
        spec = _spec(fields=[style if f["key"] == "style" else f for f in fields], promptTemplate=prompt)
        assert message in (await check_email_template("qbr", spec))["problems"]
        assert message in (await save_email_template("qbr", spec))["error"]
    assert not _team_dir.exists()


async def test_the_sample_is_held_to_every_header_field_render_email_checks(_team_dir: Path) -> None:
    spec = _spec()
    spec["sample"]["client_name"] = "Acme (ACM48213)"  # the template bans campaign codes
    saved = await save_email_template("qbr", spec)
    assert "sample does not fit the template" in saved["error"] and "ACM48213" in saved["error"]


async def test_one_save_at_a_time_across_server_processes(_team_dir: Path) -> None:
    """Each chat runs its own server; a save waits while another holds the library."""
    import fcntl
    import os
    import threading

    await save_email_template("first", _spec())
    lock = os.open(_team_dir.parent / f".{_team_dir.name}.lock", os.O_RDWR)
    fcntl.flock(lock, fcntl.LOCK_EX)
    done: list[dict[str, Any]] = []
    worker = threading.Thread(target=lambda: done.append(email_template_store.save("second", _spec())))
    worker.start()
    worker.join(timeout=0.5)
    assert worker.is_alive() and not (_team_dir / "second.yaml").exists(), "the save went ahead without the lock"
    os.close(lock)
    worker.join(timeout=10)
    assert done and done[0]["saved"] == "second"
    assert sorted(p.name for p in _team_dir.glob("*.yaml")) == ["first.yaml", "second.yaml"]


async def test_a_save_that_fails_part_way_leaves_the_library_as_it_was(
    _team_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Template, example and styles table are one change: a failed write puts the others back."""
    await save_email_template("first", _spec(name="First"))
    before = {
        p.relative_to(_team_dir).as_posix(): p.read_text(encoding="utf-8") for p in _team_dir.rglob("*") if p.is_file()
    }
    real_replace = os.replace
    failed: list[Any] = []

    def failing(src: Any, dst: Any) -> None:
        # The styles table is the last of the three renames; the renames that undo the others go through.
        if Path(dst).name == "styles.json" and not failed:
            failed.append(dst)
            raise OSError("disk trouble")
        real_replace(src, dst)

    monkeypatch.setattr(email_template_store.os, "replace", failing)
    for template_id, spec in (("second", _spec(name="Second")), ("first", _spec(name="First, revised"))):
        failed.clear()
        saved = await save_email_template(template_id, spec)
        assert failed and "could not be written" in saved["error"]
        after = {
            p.relative_to(_team_dir).as_posix(): p.read_text(encoding="utf-8")
            for p in _team_dir.rglob("*")
            if p.is_file()
        }
        assert after == before
    assert _team_dir.stat().st_mode & 0o222 == 0


async def test_a_broken_neighbour_does_not_block_a_save(_team_dir: Path) -> None:
    await save_email_template("first", _spec(name="First"))
    _team_dir.chmod(0o755)
    (_team_dir / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    _team_dir.chmod(0o555)
    assert (await save_email_template("second", _spec(name="Second"))).get("error") is None
    assert await delete_email_template("second") == {"removed": "second"}


async def test_a_template_removed_by_another_chat_is_an_ordinary_error(
    _team_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listed a moment ago, gone by the time it is read: a message, not a crash."""
    await save_email_template("first", _spec(name="First"))
    monkeypatch.setattr(email_templates, "_template_path", lambda _id: _team_dir / "gone.yaml")
    assert "no longer there" in (await get_email_template("first"))["error"]
    listed = await list_email_templates()
    assert any("no longer there" in problem for problem in listed["problems"])


async def test_the_default_client_name_is_checked_too(
    _team_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With client_name left out the header shows the deployment's name, so that is what is checked."""
    spec = _spec(never_say=["Acme"])
    spec["sample"]["client_name"] = ""
    spec["sample"]["subject"] = "Spring Sale | Week of May 4"
    spec["sample"]["blocks"][-1]["text"] = ["Thank you,", "The team"]
    spec["sample"]["blocks"][-2]["text"] = "Please confirm the added budget by Thursday."
    saved = await save_email_template("qbr", spec)
    assert "sample does not fit the template" in saved["error"] and "Acme" in saved["error"]
    monkeypatch.setenv("SES_TENANT_LABEL", "DemandCo")
    assert (await save_email_template("qbr", spec)).get("error") is None
    monkeypatch.setenv("SES_TENANT_LABEL", "Acme")
    blocks = copy.deepcopy(spec["sample"]["blocks"])
    refused = await render_email(spec["sample"]["subject"], blocks, str(tmp_path), template="qbr")
    assert "does not fit template" in refused["error"] and any("Acme" in v for v in refused["violations"])


async def test_the_rendered_email_is_what_never_say_is_about(_team_dir: Path, tmp_path: Path) -> None:
    """Text the renderer adds or formats is checked: numbers turned into text, today's date."""
    spec = _spec(never_say=["re:63\\.6%", "re:\\b20\\d\\d-\\d\\d-\\d\\d\\b"])
    spec["sections"].insert(
        -1, {"id": "channels", "title": "Channels", "block": "table", "required": False, "instruction": "By channel."}
    )
    assert (await save_email_template("qbr", spec)).get("error") is None
    blocks = copy.deepcopy(spec["sample"]["blocks"])
    args = {"template": "qbr", "client_name": "Acme", "date": "May 11, 2026"}
    assert (await render_email("Subject", copy.deepcopy(blocks), str(tmp_path), **args)).get("error") is None
    table = {
        "section": "channels",
        "type": "table",
        "columns": ["Channel", "Share"],
        "rows": [["Video", {"share": 63.6}]],
    }
    refused = await render_email("Subject", [*blocks[:-1], table, blocks[-1]], str(tmp_path), **args)
    assert any("63.6%" in v for v in refused["violations"]), refused
    # No date given: the header shows today's, in the form the template bans.
    out = tmp_path / "out"
    out.mkdir()
    refused = await render_email("Subject", copy.deepcopy(blocks), str(out), template="qbr", client_name="Acme")
    assert "does not fit template" in refused["error"] and list(out.iterdir()) == []


async def test_a_sample_block_of_the_wrong_shape_is_a_message_not_a_crash(_team_dir: Path) -> None:
    spec = _spec()
    spec["sections"][1]["limits"] = {}
    spec["sample"]["blocks"][1]["items"] = 5
    checked = await check_email_template("qbr", spec)
    assert checked["valid"] is False and "sample" in checked["problems"]
    assert "error" in await save_email_template("qbr", spec)
    assert not _team_dir.exists() or list(_team_dir.glob("*.yaml")) == []


async def test_a_folder_that_cannot_be_locked_again_is_reported(
    _team_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await save_email_template("first", _spec(name="First"))
    real_chmod = os.chmod

    def stubborn(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        if mode == 0o555:
            raise PermissionError("not permitted")
        real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(email_template_store.os, "chmod", stubborn)
    saved = await save_email_template("second", _spec(name="Second"))
    assert "could not be made read-only again" in saved["error"]
    assert (_team_dir / "second.yaml").is_file()


def test_a_heading_the_renderer_always_shows_cannot_be_blanked() -> None:
    raw = _spec()
    raw["sections"][1]["heading"] = ""
    assert "always shows a heading" in " ".join(email_templates.validate_spec(raw))


async def test_the_folder_is_locked_again_when_opening_it_fails(
    _team_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure half-way through unlocking, or while creating the previews folder, leaves nothing writable."""
    await save_email_template("first", _spec(name="First"))
    real_chmod, real_mkdir = os.chmod, Path.mkdir

    def second_open_fails(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        if mode == 0o755 and Path(path).name == "previews":
            raise PermissionError("not permitted")
        real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(email_template_store.os, "chmod", second_open_fails)
    assert "error" in await save_email_template("second", _spec(name="Second"))
    assert _team_dir.stat().st_mode & 0o222 == 0
    monkeypatch.setattr(email_template_store.os, "chmod", real_chmod)

    # The previews folder is gone and cannot be made again.
    real_chmod(_team_dir, 0o755)
    real_chmod(_team_dir / "previews", 0o755)
    for leftover in (_team_dir / "previews").iterdir():
        leftover.unlink()
    (_team_dir / "previews").rmdir()
    real_chmod(_team_dir, 0o555)

    def no_room(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == "previews":
            raise OSError("no space left on device")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", no_room)
    assert "error" in await save_email_template("second", _spec(name="Second"))
    assert _team_dir.stat().st_mode & 0o222 == 0

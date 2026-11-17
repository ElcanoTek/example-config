"""Team email templates: added from a chat, kept on this deployment.

A bundle may ship templates of its own, Git-tracked under email_templates/
(this one ships none). A team template is one a person asked the assistant to
save, so the next email of that kind comes out the same
way. It is the same YAML, held to the same checks, and written to the
directory `EMAIL_TEMPLATES_TEAM_DIR` names:

    <dir>/<id>.yaml              the template
    <dir>/previews/<id>.html     its example, rendered from its own sample
    <dir>/previews/styles.json   the recolor table fleet's library uses

fleet lists that directory in its Email templates library beside the bundle's,
and mounts it read-only in every sandbox, so the only way in is
`save_email_template`, which fleet stages on an approval card.

Nothing is written unless the whole template is sound: the spec validates, its
sample obeys its own sections and limits, and the sample renders and lints
clean. A template that could not produce its own example would only fail later,
in front of whoever uses it.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import fcntl
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import email_render
import email_template_preview
import yaml
from email_lint import partition_findings, validate

import email_templates

MAX_TEAM_TEMPLATES = 100
_STAMPS = ("created_by", "created_at", "updated_at")

# What every template's form asks besides its own fields, and how every prompt
# ends (email_templates/README.md, "The form"). A team template saved without
# a form gets this one, so it has the same Send to and Style pickers and goes
# to the same approval card as the bundle's.
STYLE_HINT = "The example beside this form shows the look. Custom colors carry white text, so pick dark shades."
STYLE_LINE = (
    "Style: {style} (a theme name goes in render_email's theme; custom colors go in its colors as header and accent)"
)
SEND_LINE = (
    "When it is built, call mcp_ses_outbound_send_email straight away, to the Send to addresses "
    "(to me when there are none). That puts the finished email on an approval card and nothing goes out "
    "until I choose Send. Do not use preview_email and do not ask me first."
)


class StoreError(ValueError):
    """Why a team template was not saved or removed, worded for the person asking."""


def author() -> str:
    """Who is saving: fleet's record of the signed-in person ("" outside a person's chat turn).

    Read here, not in ses_sender: the deal-sheet server imports ses_sender to
    send its own email and has no use for this.
    """
    return os.environ.get("EMAIL_TEMPLATE_AUTHOR", "").strip().lower()


def _style_options() -> list[str]:
    """This bundle's email themes (protocols/email_styles/themes/), the default one first."""
    default = email_render.default_theme()
    return [default, *(theme for theme in email_render.available_themes() if theme != default)]


def _style_field() -> dict[str, Any]:
    """The Style picker, with each theme's real colors."""
    swatches = {}
    for option in _style_options():
        overrides = email_render._theme_overrides(option)
        swatches[option] = [overrides.get(c, c) for c in ("#1A0B1E", "#6262A0")]
    return {
        "key": "style",
        "label": "Style",
        "type": "palette",
        "options": list(swatches),
        "swatches": swatches,
        "custom": ["Header", "Accent"],
        "default": email_render.default_theme(),
        "hint": STYLE_HINT,
    }


def default_form(template_id: str, name: str) -> tuple[list[dict[str, Any]], str]:
    """The form and prompt a template gets when it declares none."""
    fields = [
        {
            "key": "about",
            "label": "Who or what it is about",
            "type": "text",
            "required": True,
            "placeholder": "Client and campaign, or the subject of the email",
        },
        {
            "key": "recipient",
            "label": "Send to",
            "type": "recipients",
            "hint": "Leave empty for a review copy to yourself.",
        },
        _style_field(),
        {"key": "notes", "label": "Anything to stress or leave out", "type": "textarea", "advanced": True},
    ]
    prompt = "\n".join(
        [
            f'Write the email "{name}" for {{about}} with the email template {template_id}.',
            f'Read the template first (mcp_ses_outbound_get_email_template, template "{template_id}"), gather the '
            "real facts it needs, then build it with mcp_ses_outbound_render_email, passing template "
            f'"{template_id}".',
            "Send to: {recipient}",
            STYLE_LINE,
            "Notes: {notes}",
            SEND_LINE,
        ]
    )
    return fields, prompt


def _require_team_dir() -> Path:
    directory = email_templates.team_dir()
    if directory is None:
        raise StoreError("This deployment has no place for team email templates, so none can be saved or removed.")
    if os.environ.get("CUTLASS_MOC_TASK_ID", "").strip():
        raise StoreError("Email templates are changed from a chat, by a person. A scheduled run cannot.")
    return directory


def _check_id(template_id: str) -> str:
    wanted = str(template_id or "").strip().removesuffix(".yaml")
    if not email_templates._ID.fullmatch(wanted) or len(wanted) > 60:
        raise StoreError(
            f"{template_id!r} is not a usable template id: lowercase letters and digits joined by '-' "
            "(for example monthly-qbr-recap), at most 60 characters."
        )
    if wanted in email_templates.bundle_template_ids():
        raise StoreError(
            f"{wanted!r} is one of the built-in templates and cannot be replaced. Save yours under another id."
        )
    return wanted


def prepare(template_id: str, spec: Any) -> tuple[str, dict[str, Any], str]:
    """Check a template end to end. Returns (id, the spec as it would be saved, its preview).

    Raises StoreError listing every problem found at the level reached, so a
    writer fixes the spec in one pass.
    """
    wanted = _check_id(template_id)
    if not isinstance(spec, Mapping):
        raise StoreError("The template must be an object with name, description, sections, sample and so on.")
    spec = {k: copy.deepcopy(v) for k, v in spec.items() if k not in (*_STAMPS, "id")}
    if ("fields" in spec) != ("promptTemplate" in spec):
        raise StoreError("Give both 'fields' and 'promptTemplate', or neither to get the standard form.")
    if "fields" not in spec:
        spec["fields"], spec["promptTemplate"] = default_form(wanted, str(spec.get("name") or wanted).strip())
    issues = email_templates.validate_spec(spec)
    if spec.get("theme") and str(spec["theme"]) not in email_render.available_themes():
        issues.append(f"'theme' {spec['theme']!r} is not one of {', '.join(email_render.available_themes())}")
    if issues:
        raise StoreError("The template is not valid: " + "; ".join(issues))
    candidate = dict(spec, id=wanted)
    try:
        # Before anything is written: a Style field naming a look the renderer
        # cannot produce would otherwise fail only when the table is rebuilt.
        email_template_preview.add_styles(candidate, {}, {})
    except ValueError as exc:
        raise StoreError(f"The template's form is not valid: {exc}") from exc
    try:
        page = email_template_preview.render_sample(candidate)
    except (ValueError, email_render.RenderError) as exc:
        raise StoreError(f"The template's sample does not fit the template: {exc}") from exc
    errors, _ = partition_findings(
        validate(page, subject=str(spec["sample"].get("subject") or ""), content_type="text/html")
    )
    preview = email_template_preview.standalone(page)
    if errors:
        raise StoreError("The template's sample does not render cleanly: " + "; ".join(f.format() for f in errors))
    text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=1000)
    if len(text.encode("utf-8")) > email_templates.MAX_TEMPLATE_BYTES:
        raise StoreError(f"The template is larger than {email_templates.MAX_TEMPLATE_BYTES // 1024} KB.")
    return wanted, spec, preview


# At rest the folders are not writable and the files are read-only, even to
# this server's own user. The folder sits inside the roots other tools may
# write to by pathname (render_email's output_file, for one), and those tools
# open or replace a file without changing its mode, so they are refused by the
# filesystem. Only save and delete, behind their approval cards, unlock it.
_DIR_LOCKED, _DIR_OPEN, _FILE_MODE = 0o555, 0o755, 0o444


@contextlib.contextmanager
def _one_writer(directory: Path):  # noqa: ANN202 - a context manager
    """Hold the library for one save or delete, across every chat's server process.

    Each chat runs its own copy of this server. Without this, one copy could
    lock the folder again while another is half-way through its files, or two
    could rebuild the styles table from different lists. The lock file sits
    beside the folder, since the folder itself is read-only at rest.
    """
    directory.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory.parent / f".{directory.name}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def _unlocked(directory: Path):  # noqa: ANN202 - a context manager
    previews = _team_previews(directory)
    for folder in (directory, previews):
        os.chmod(folder, _DIR_OPEN)
    try:
        yield previews
    finally:
        for folder in (previews, directory):
            with contextlib.suppress(OSError):
                os.chmod(folder, _DIR_LOCKED)


def _write(path: Path, text: str) -> None:
    """Replace `path` atomically, never through a symlink."""
    if path.is_symlink():
        raise StoreError(f"{path.name} is a link, not a file; it was left alone.")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _team_previews(directory: Path) -> Path:
    for folder in (directory, directory / "previews"):
        if folder.is_symlink():
            raise StoreError("The team templates folder is a link; nothing was changed.")
        if not folder.is_dir():
            if folder.parent.is_dir() and not os.access(folder.parent, os.W_OK):
                os.chmod(folder.parent, _DIR_OPEN)  # the locked team folder, on its first preview
            folder.mkdir(mode=_DIR_OPEN, parents=True)
    return directory / "previews"


def _write_styles(previews: Path) -> None:
    styles = email_template_preview.preview_styles(email_templates.template_ids())
    _write(previews / "styles.json", json.dumps(styles, indent=2) + "\n")


def _store(
    directory: Path, wanted: str, clean: dict[str, Any], preview: str, author: str
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    """Write one checked template; the caller holds the library (_one_writer)."""
    path = directory / f"{wanted}.yaml"
    existing: Mapping[str, Any] = {}
    if path.is_file() and not path.is_symlink():
        with contextlib.suppress(OSError, yaml.YAMLError, UnicodeDecodeError):
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, Mapping) else {}
    elif len(email_templates.team_template_ids()) >= MAX_TEAM_TEMPLATES:
        raise StoreError(f"There are already {MAX_TEAM_TEMPLATES} team templates. Remove one before adding another.")
    now = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    who = str(author or "").strip().lower()
    clean["created_by"] = str(existing.get("created_by") or who)
    clean["created_at"] = str(existing.get("created_at") or now)
    if existing:
        clean["updated_at"] = now
    with _unlocked(directory) as previews:
        # The preview first: a template whose example is missing is worse than
        # an example whose template is one write behind.
        _write(previews / f"{wanted}.html", preview)
        _write(path, yaml.safe_dump(clean, sort_keys=False, allow_unicode=True, width=1000))
        _write_styles(previews)
    return clean, existing


def save(template_id: str, spec: Any, author: str = "") -> dict[str, Any]:
    """Validate and store a team template. Returns what the caller reports back."""
    directory = _require_team_dir()
    wanted, clean, preview = prepare(template_id, spec)
    with _one_writer(directory):
        clean, existing = _store(directory, wanted, clean, preview, author)
    return {
        "saved": wanted,
        "name": str(clean.get("name") or ""),
        "replaced_an_earlier_version": bool(existing),
        "sections": [s["id"] for s in clean["sections"]],
        "where": "It is now in the Email templates library (More options in the chat composer), marked Team.",
        "use": f"render_email(template='{wanted}')",
    }


def delete(template_id: str) -> dict[str, Any]:
    directory = _require_team_dir()
    wanted = str(template_id or "").strip().removesuffix(".yaml")
    if wanted in email_templates.bundle_template_ids():
        raise StoreError(f"{wanted!r} is a built-in template; it is changed in the bundle, not from a chat.")
    if wanted not in email_templates.team_template_ids():
        known = ", ".join(email_templates.team_template_ids()) or "there are none"
        raise StoreError(f"There is no team template {template_id!r}. Team templates: {known}.")
    with _one_writer(directory), _unlocked(directory) as previews:
        if not (directory / f"{wanted}.yaml").is_file():
            raise StoreError(f"There is no team template {template_id!r}; it was removed a moment ago.")
        (directory / f"{wanted}.yaml").unlink()
        with contextlib.suppress(FileNotFoundError):
            (previews / f"{wanted}.html").unlink()
        _write_styles(previews)
    return {"removed": wanted}


def holds(path: str | os.PathLike[str]) -> bool:
    """True when `path` is the team templates folder or anything in it (links resolved).

    For tools that write where a caller points them: none of them may write here.
    """
    directory = email_templates.team_dir()
    if directory is None:
        return False
    try:
        target, root = Path(path).resolve(), directory.resolve()
    except OSError:
        return False
    return target == root or root in target.parents

"""Download directories are created at first use, not at import.

The inbox server used to `mkdir` its attachment directory and its
last-checked state directory while the module was still being imported. Importing a server is not using it: a test that only collects
the module, `fleet mcp test` probing a connector, or an operator opening a
REPL all planted a directory — in whatever working directory the process
happened to have, which for a stdio MCP server on an older fleet was the
bundle checkout itself.

The destination is unchanged; only the timing moved. So these tests pin both
halves: an import touches nothing, and a real download still gets its
directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx
import ses_s3_email
from utils import url_guard

MCP_DIR = Path(ses_s3_email.__file__).resolve().parent
PUBLIC_IP = "93.184.216.34"  # a genuinely global address; TEST-NET is "reserved"


def _import_in_subprocess(module: str, cwd: Path, env_overrides: dict[str, str]) -> None:
    """Import ``module`` in a fresh interpreter rooted at ``cwd``.

    A subprocess is the only honest way to test import-time behavior: the
    module is already imported in this process, so re-importing it here would
    be a no-op.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(MCP_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"importing {module} failed:\n{result.stderr}"


def test_importing_ses_s3_email_creates_no_directories(tmp_path):
    # Relative settings, so anything created lands in cwd — the shape of the
    # original bug, where cwd was the operator's bundle checkout.
    _import_in_subprocess(
        "ses_s3_email",
        cwd=tmp_path,
        env_overrides={
            "EMAIL_ATTACHMENT_DIR": "email_attachments",
            "EMAIL_LAST_CHECK_FILE": "state/last_checked.txt",
            "EMAIL_DEBUG_LOG_FILE": str(tmp_path / "debug.log"),
        },
    )

    assert not (tmp_path / "email_attachments").exists()
    assert not (tmp_path / "state").exists()
    assert [p.name for p in tmp_path.iterdir() if p.is_dir()] == []


@pytest.fixture
def public_dns_for_url_guard(monkeypatch):
    """Resolve every hostname to a public address inside the shared SSRF guard."""

    async def fake_resolve(host: str) -> list[str]:
        return [PUBLIC_IP]

    monkeypatch.setattr(url_guard, "_resolve_host", fake_resolve)


@respx.mock
async def test_ses_s3_email_download_creates_the_output_dir(public_dns_for_url_guard, tmp_path):
    respx.get("https://files.example.com/report.csv").mock(
        return_value=httpx.Response(200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"})
    )
    download_dir = tmp_path / "not" / "yet" / "there"

    result = await ses_s3_email.download_link_attachment(
        url="https://files.example.com/report.csv",
        output_dir=str(download_dir),
    )

    assert result["status"] == "success"
    assert download_dir.is_dir()
    assert Path(result["saved_to"]).read_bytes() == b"a,b\n1,2\n"


@respx.mock
async def test_ses_s3_email_download_creates_the_default_dir(public_dns_for_url_guard, tmp_path, monkeypatch):
    """With no output_dir the default (EMAIL_ATTACHMENT_DIR) is created too."""
    respx.get("https://files.example.com/report.csv").mock(
        return_value=httpx.Response(200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"})
    )
    default_dir = tmp_path / "default_attachments"
    monkeypatch.setattr(ses_s3_email, "ATTACHMENT_DOWNLOAD_DIR", str(default_dir))

    result = await ses_s3_email.download_link_attachment(url="https://files.example.com/report.csv")

    assert result["status"] == "success"
    assert default_dir.is_dir()
    assert Path(result["saved_to"]).parent == default_dir


async def test_ses_s3_email_timestamp_write_creates_its_dir(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "last_checked.txt"
    monkeypatch.setattr(ses_s3_email, "LAST_CHECK_FILE", str(state_file))

    await ses_s3_email.update_last_checked_timestamp()

    assert state_file.exists()

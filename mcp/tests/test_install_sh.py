"""install.sh piped into bash (`curl ... | bash`) never treats the caller's cwd as the bundle.

Piped, there is no script file: BASH_SOURCE[0] is empty and the old fallback
to $0 ("bash") resolved to ".", so a cwd holding any manifest.yaml + mcp/
had its servers registered in the user's agents. Only a real script file
locates the bundle; otherwise the chosen install dir is used.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"

pytestmark = pytest.mark.skipif(
    not INSTALL_SH.is_file() or shutil.which("bash") is None, reason="needs install.sh and bash"
)


def _fake_bundle(path: Path, server: str) -> Path:
    (path / "mcp").mkdir(parents=True)
    (path / "manifest.yaml").write_text(
        f"mcp_servers:\n  - name: {server}\n    type: stdio\n    command: python3\n    args: [mcp/{server}.py]\n"
    )
    return path


def _env(tmp_path: Path, bundle_dir: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "BUNDLE_DIR": str(bundle_dir)}


def test_piped_install_ignores_a_bundle_in_cwd(tmp_path):
    hostile = _fake_bundle(tmp_path / "hostile", "planted_server")
    chosen = _fake_bundle(tmp_path / "chosen", "chosen_server")

    proc = subprocess.run(
        ["bash", "-s", "--", "--list"],
        input=INSTALL_SH.read_bytes(),
        cwd=hostile,
        env=_env(tmp_path, chosen),
        capture_output=True,
        timeout=60,
    )
    out = proc.stdout.decode() + proc.stderr.decode()

    assert f"bundle: {chosen}" in out
    assert "planted_server" not in out
    assert "chosen_server" in out


def test_script_file_run_uses_its_own_directory(tmp_path):
    bundle = _fake_bundle(tmp_path / "bundle", "own_server")
    shutil.copy(INSTALL_SH, bundle / "install.sh")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    proc = subprocess.run(
        ["bash", str(bundle / "install.sh"), "--list"],
        cwd=elsewhere,
        env=_env(tmp_path, tmp_path / "unused"),
        capture_output=True,
        timeout=60,
    )
    out = proc.stdout.decode() + proc.stderr.decode()

    assert f"bundle: {bundle}" in out
    assert "own_server" in out

"""Filesystem helpers shared by this bundle's MCP servers.

Exists for one rule: **importing a server must not touch the filesystem.**
Both inbox servers used to `mkdir` their attachment directory at module
import, so a plain `import ses_s3_email` — a test collecting the module,
`fleet mcp test` probing the connector, an operator opening a REPL — created
whatever `EMAIL_ATTACHMENT_DIR` pointed at, in
whatever working directory the process happened to have. With a relative
setting and a server spawned in the bundle checkout (older fleets did exactly
that), the side effect landed in the operator's git tree.

Creating the directory at first write instead keeps the same destination and
the same behavior for anything that actually downloads a file.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["ensure_dir"]


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    """Create ``path`` (with parents) if needed and return it as a ``Path``."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory

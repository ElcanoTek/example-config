#!/usr/bin/env python3
"""
Plugin Notes MCP Server

The stdio MCP server declared by the example Agent Plugin's mcp.json
(plugins/example-plugin/mcp.json). It exists to show the two things the
Agent Plugins format (https://agent-plugins.org, spec v1.0.0) adds on top of a
plain MCP server:

- PLUGIN_ROOT: the absolute plugin directory. mcp.json launches this file as
  "${PLUGIN_ROOT}/server/plugin_notes.py", so the plugin can be installed at any
  path in any compatible client and still find itself.
- PLUGIN_DATA: a writable directory the client creates before launch and keeps
  across plugin updates. The notes live there, so a scratchpad written in one
  conversation is readable in the next, and survives replacing the plugin
  directory wholesale.

A conformant client sets both variables in the subprocess environment; the
placeholders in mcp.json are expanded in args and env values only. Under fleet
the server is launched host-side by the credential broker, exactly like the
bundle's own mcp/*.py servers, and its tools show up as mcp_plugin_notes_<tool>.

It is dependency-free apart from the MCP runtime (mcp.server.fastmcp.FastMCP,
the same import the bundle's other servers use) and needs no credentials, so it
is always-on.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Log to stderr - stdout is reserved for the STDIO transport.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("plugin_notes")

# PLUGIN_ROOT / PLUGIN_DATA are supplied by the client (fleet sets them last, so
# a plugin cannot shadow them). Fall back to this file's parents so the server
# still starts when run by hand outside any plugin loader.
PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)
PLUGIN_DATA = Path(os.environ.get("PLUGIN_DATA") or PLUGIN_ROOT / ".plugin-data")

# mcp.json maps PLUGIN_NOTES_FILE to "${PLUGIN_DATA}/notes.jsonl" to demonstrate
# placeholder expansion in an env VALUE; the default below is the same location.
NOTES_FILE = Path(os.environ.get("PLUGIN_NOTES_FILE") or PLUGIN_DATA / "notes.jsonl")

MAX_TEXT_CHARS = 4000
MAX_TAGS = 10


def _read_notes() -> list[dict[str, Any]]:
    """Load every note (one JSON object per line). A missing file is an empty list;
    a corrupt line is skipped with a warning rather than failing every tool."""
    if not NOTES_FILE.exists():
        return []
    notes: list[dict[str, Any]] = []
    for lineno, line in enumerate(NOTES_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping corrupt note on line %d of %s", lineno, NOTES_FILE)
            continue
        if isinstance(rec, dict):
            notes.append(rec)
    return notes


def _append_note(rec: dict[str, Any]) -> None:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NOTES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        t = str(t).strip().lower()
        if t and t not in out:
            out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


@mcp.tool()
def plugin_info() -> dict[str, Any]:
    """Report where PLUGIN_ROOT and PLUGIN_DATA resolved and how many notes are stored.

    Call this to show the Agent Plugins variables at work: the root is the plugin
    directory the client loaded, the data dir is the persistent writable location
    it created for this plugin.
    """
    return {
        "plugin_root": str(PLUGIN_ROOT),
        "plugin_data": str(PLUGIN_DATA),
        "plugin_root_from_env": "PLUGIN_ROOT" in os.environ,
        "plugin_data_from_env": "PLUGIN_DATA" in os.environ,
        "notes_file": str(NOTES_FILE),
        "note_count": len(_read_notes()),
    }


@mcp.tool()
def note_add(text: str, tags: list[str] | None = None) -> dict[str, Any]:
    """Append one scratch note (with optional tags) to the plugin's persistent data dir.

    Notes are shared by every conversation on this deployment and survive
    restarts and plugin updates. Do not store anything confidential.
    """
    text = (text or "").strip()
    if not text:
        return {"error": "text is required"}
    if len(text) > MAX_TEXT_CHARS:
        return {"error": f"text is {len(text)} chars; the limit is {MAX_TEXT_CHARS}"}
    rec = {
        "id": f"n{int(time.time() * 1000)}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "text": text,
        "tags": _clean_tags(tags),
    }
    _append_note(rec)
    logger.info("stored note %s (%d chars)", rec["id"], len(text))
    return rec


@mcp.tool()
def note_list(tag: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List stored notes, newest first, optionally filtered to one tag."""
    limit = max(1, min(int(limit or 20), 200))
    notes = _read_notes()
    if tag:
        want = tag.strip().lower()
        notes = [n for n in notes if want in (n.get("tags") or [])]
    return list(reversed(notes))[:limit]


@mcp.tool()
def note_clear() -> dict[str, Any]:
    """Delete every stored note. Destructive: say what you are removing before calling it."""
    count = len(_read_notes())
    if NOTES_FILE.exists():
        NOTES_FILE.unlink()
    logger.info("cleared %d notes", count)
    return {"cleared": count}


if __name__ == "__main__":
    logger.info("plugin_notes starting: root=%s data=%s notes=%s", PLUGIN_ROOT, PLUGIN_DATA, NOTES_FILE)
    mcp.run(transport="stdio")

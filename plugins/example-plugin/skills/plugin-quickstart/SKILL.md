---
name: plugin-quickstart
description: Explains how this bundle packages a skill and an MCP server as a portable Agent Plugin (agent-plugins.org), and how to use the plugin_notes server that ships with it. Use when someone asks how Agent Plugins work in fleet, wants to author or port a plugin, or wants to save or list scratch notes across turns.
---

# Plugin quickstart

You are reading a skill that arrived inside an **Agent Plugin** — the open,
vendor-neutral package format from [agent-plugins.org](https://agent-plugins.org)
(specification v1.0.0). The same directory loads, unchanged, in fleet and in
Cursor, VS Code, GitHub Copilot, ChatGPT/Codex, Kiro and the other compatible
clients, so a plugin is written once and shared across tools.

## What this plugin is

```
plugins/example-plugin/
├── plugin.json                 # REQUIRED: "$schema" + "name" (+ metadata)
├── skills/
│   └── plugin-quickstart/
│       └── SKILL.md            # this file — an ordinary Agent Skill
├── mcp.json                    # one stdio MCP server, declared portably
└── server/
    └── plugin_notes.py         # the server the mcp.json entry launches
```

- `plugin.json` identifies the plugin. Only `$schema` and `name` are required;
  everything else is display metadata.
- `skills/` holds Agent Skills in exactly the format the bundle's top-level
  `skills/` uses (see `skills/example-skill/SKILL.md` for the frontmatter rules).
  fleet merges them into the same roster; this bundle's own `skills/` win a name
  collision, and a plugin's skills win over fleet's built-in pack.
- `mcp.json` declares MCP servers. This one runs `server/plugin_notes.py` over
  stdio with two variables every conformant client supplies: `PLUGIN_ROOT` (the
  plugin directory) and `PLUGIN_DATA` (a writable directory that persists across
  plugin updates). The `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` placeholders are
  expanded in `args`, `env` values and `cwd` only — never in `command`.

## Using the plugin_notes server

The server keeps a small scratchpad in `PLUGIN_DATA`, so notes survive a
restart and a plugin update. Its tools (addressed in fleet as
`mcp_plugin_notes_<tool>`):

- `plugin_info()` — where `PLUGIN_ROOT` and `PLUGIN_DATA` resolved, and how many
  notes are stored. Call it first if you want to show someone the plugin
  variables at work.
- `note_add(text, tags=[])` — append one note. Returns the stored record.
- `note_list(tag=None, limit=20)` — newest first, optionally filtered by tag.
- `note_clear()` — delete every note. This is the only destructive tool; say
  what you are about to remove before calling it.

Notes are plain JSON lines in `${PLUGIN_DATA}/notes.jsonl`. They are shared by
every conversation on this deployment, so do not store anything confidential.

## Porting or authoring a plugin

1. Create a directory with a `plugin.json` whose `name` is 1–64 characters of
   `a-z 0-9 . -`, alphanumeric at both ends, with no `--` or `..`.
2. Put skills under `skills/<name>/SKILL.md`; put MCP servers in `mcp.json`.
   Commands are one executable token — a bare name looked up on `PATH`, or a
   `./`-relative path inside the plugin. Never embed credentials in `env` or
   `headers`; the format is public package data.
3. Drop the directory into this bundle's `plugins/` and run
   `fleet validate-config`. Plugin problems (an unknown manifest field, a
   skipped server entry) show up as advisories on the `manifest` line; a
   defect in one component never blocks the rest of the bundle.
4. In another client, point its plugin loader at the same directory — no
   reformatting needed.

The fleet-side mapping and the exact failure boundaries are documented in the
fleet repo's `docs/AGENT-PLUGINS.md`.

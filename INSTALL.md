# Installing this bundle's MCP servers into your coding agent

This bundle ships a small set of [MCP](https://modelcontextprotocol.io) servers
(under `mcp/`) and a one-line installer (`install.sh`) that registers them into
whatever local coding agents you already have. Use it to try the servers in your
own agent **before** the `fleet` app is provisioned — they are the same Python
servers `fleet` runs.

Everything is driven from [`manifest.yaml`](manifest.yaml): the server list,
their commands, the env-var names each one needs, and any HTTP transports. The
installer reads that file, so it stays correct as the bundle changes and works
unchanged in any sibling `*-config` bundle you fork from this one.

> **Secrets are never written into any config.** The installer registers each
> server's credential variables as *references* in your agent's own
> environment-expansion syntax. Your agent resolves the live value from its
> launch environment at run time. You export the values; the installer only ever
> handles the variable *names*.

> **Going further than just the MCP servers?** Once they're registered, see
> **[docs/TESTING-LOCALLY.md](docs/TESTING-LOCALLY.md)** for the hands-on guide to
> *testing* the full bundle in a local agent — verifying a server live and calling
> its tools, applying the Aria persona / a system prompt, and running a protocol
> end-to-end (Claude Code first, then a note for the other agents).

---

## TL;DR — one line

```bash
curl -fsSL https://raw.githubusercontent.com/ElcanoTek/example-config/main/install.sh | bash
```

That clones (or updates) the bundle to `~/example-config`, builds a `.venv`,
installs `mcp/requirements.txt`, and registers every server into each agent it
detects. Pick a different directory with `bash -s -- <dir>` or `BUNDLE_DIR=...`:

```bash
curl -fsSL https://raw.githubusercontent.com/ElcanoTek/example-config/main/install.sh | bash -s -- ~/code/example-config
```

Already have the repo checked out? Just run it in place:

```bash
cd <bundle>
./install.sh            # register into all detected agents
./install.sh --list     # list the servers + which agents are detected (no changes)
./install.sh --dry-run  # show exactly what WOULD be registered (no changes)
./install.sh --agent claude          # target one agent
./install.sh --agent claude --scope project   # Claude Code scope: local|project|user
```

The installer is **idempotent** (re-running skips servers an agent already has),
**non-destructive** (it merges into existing config files, never clobbering other
servers), and **skips agents you don't have installed**.

---

## What this bundle ships

Two example servers, chosen to show the two shapes you'll build for real: an
always-on server with no credentials, and a credential-gated server that stays
dark until you supply a key.

| Server | Transport | Script | Enabled when you set | Env vars it reads |
|---|---|---|---|---|
| `knowledge_base` | stdio | `mcp/knowledge_base.py` | always on | _(none)_ |
| `example_api` | stdio | `mcp/example_api.py` | `EXAMPLE_API_KEY` | `EXAMPLE_API_KEY`, `EXAMPLE_API_BASE_URL`, `EXAMPLE_API_TIMEOUT_SECONDS` |

- **`knowledge_base`** — a tiny "team knowledge base" over a bundled JSON handbook
  (`mcp/data/handbook.json`). No credentials, so it runs the moment it's
  registered. Tools: `kb_search`, `kb_get_article`, `kb_list_categories`. This is
  the canonical "point an agent at your own docs" pattern — swap the handbook for
  your content and the tools work unchanged.
- **`example_api`** — a generic REST connector that demonstrates host-side
  credential brokering. It registers but stays **dark** until `EXAMPLE_API_KEY`
  is set, so a fresh checkout runs clean with no secrets. Tools: `api_list_records`,
  `api_get_record`, `api_submit_record`. `EXAMPLE_API_BASE_URL` defaults to a
  placeholder host (`https://api.example.com/v1`); point it at any JSON API.

The bundle also ships one **Agent Plugin** — `plugins/example-plugin/`, in the
portable [agent-plugins.org](https://agent-plugins.org) format — whose `mcp.json`
declares a third stdio server, `plugin_notes` (`server/plugin_notes.py`, always
on, no credentials). `install.sh` registers only the manifest's servers; clients
that implement Agent Plugins (Cursor, VS Code, Copilot, Codex, Kiro, fleet) load
that directory directly, so point their plugin loader at it instead.

This table is a snapshot — `manifest.yaml` is the source of truth. Run
`./install.sh --list` for the live list (it prints each server's exact env-var
names). "Enabled when" is `fleet`'s gate; for a *local agent* the servers still
register, they just won't authenticate until you export the credentials below.

---

## Setting the credential env vars

`knowledge_base` needs nothing — it works the moment it's registered. `example_api`
reads its credentials from its environment. Export the variables it needs **in the
shell you launch your agent from** — the agent inherits them, and the `${VAR}` /
`{env:VAR}` references the installer wrote resolve to your live values. Nothing
secret is ever stored in a config file or this repo.

```bash
# example_api stays dark until you set its key. Set only what you want to use:
export EXAMPLE_API_KEY=...                          # required to enable the server
export EXAMPLE_API_BASE_URL=https://api.example.com/v1   # optional; point at your API
export EXAMPLE_API_TIMEOUT_SECONDS=30               # optional
# then launch your agent from that same shell.
```

Put them in `~/.bashrc` / `~/.zshrc` (or a sourced `.env`) so every agent session
inherits them. To see the full, authoritative env-var list per server:

```bash
./install.sh --list                       # quick view, per server
# or read manifest.yaml directly — every "${NAME}" in a server's `env:` block is
# a variable you supply; literal values (base URLs, IDs) are pre-filled for you.
```

A gated server whose variables you have **not** set will register but fail to
connect / authenticate — that's expected. `knowledge_base` is the safe one to
learn the mechanics on; set `EXAMPLE_API_KEY` and restart the agent to bring
`example_api` online.

---

## How it registers per agent (and the manual snippet for each)

The installer auto-detects these. If you'd rather wire one up by hand, or the CLI
path fails, use the snippet below. Replace `<bundle>` with the absolute path to
your checkout (e.g. `/home/you/example-config`) — most config files don't expand
`~`. Each snippet shows both servers; drop the `example_api` block if you only
want the always-on knowledge base, or just run the installer.

### Claude Code

CLI (what the installer runs; `${VAR}` is stored verbatim and expanded at launch):

```bash
# always-on, no credentials — the ideal first target:
claude mcp add knowledge_base -s user \
  -- python3 <bundle>/mcp/knowledge_base.py

# credential-gated: pass env-var NAMES as ${VAR} references (Claude stores them
# verbatim and expands them from your launch environment):
claude mcp add example_api -s user \
  -e EXAMPLE_API_KEY='${EXAMPLE_API_KEY}' \
  -e EXAMPLE_API_BASE_URL='${EXAMPLE_API_BASE_URL}' \
  -e EXAMPLE_API_TIMEOUT_SECONDS='${EXAMPLE_API_TIMEOUT_SECONDS}' \
  -- python3 <bundle>/mcp/example_api.py
```

Scopes: `-s local` (this project, private), `-s project` (writes a shared
`.mcp.json`), `-s user` (all your projects — the installer's default).
Equivalent project `.mcp.json` (`${VAR}` / `${VAR:-default}` expansion is
supported in `command`, `args`, `env`, `url`, `headers`):

```json
{
  "mcpServers": {
    "knowledge_base": {
      "type": "stdio",
      "command": "python3",
      "args": ["<bundle>/mcp/knowledge_base.py"]
    },
    "example_api": {
      "type": "stdio",
      "command": "python3",
      "args": ["<bundle>/mcp/example_api.py"],
      "env": {
        "EXAMPLE_API_KEY": "${EXAMPLE_API_KEY}",
        "EXAMPLE_API_BASE_URL": "${EXAMPLE_API_BASE_URL}",
        "EXAMPLE_API_TIMEOUT_SECONDS": "${EXAMPLE_API_TIMEOUT_SECONDS}"
      }
    }
  }
}
```

Doc: <https://code.claude.com/docs/en/mcp>

### opencode

Project `opencode.json` (or `~/.config/opencode/opencode.json`). `command` is an
**array**, the env block is `environment`, and `{env:VAR}` does the substitution:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "knowledge_base": {
      "type": "local",
      "command": ["python3", "<bundle>/mcp/knowledge_base.py"],
      "enabled": true,
      "environment": {}
    },
    "example_api": {
      "type": "local",
      "command": ["python3", "<bundle>/mcp/example_api.py"],
      "enabled": true,
      "environment": {
        "EXAMPLE_API_KEY": "{env:EXAMPLE_API_KEY}",
        "EXAMPLE_API_BASE_URL": "{env:EXAMPLE_API_BASE_URL}",
        "EXAMPLE_API_TIMEOUT_SECONDS": "{env:EXAMPLE_API_TIMEOUT_SECONDS}"
      }
    }
  }
}
```

Doc: <https://opencode.ai/docs/mcp-servers/>

### Codex CLI

`~/.codex/config.toml`. Credentials are forwarded by **name** via `env_vars`
(values stay in your environment); literal non-secrets go in an `[…​.env]` table:

```toml
[mcp_servers.knowledge_base]
command = "python3"
args = ["<bundle>/mcp/knowledge_base.py"]
startup_timeout_sec = 30
tool_timeout_sec = 1800

[mcp_servers.example_api]
command = "python3"
args = ["<bundle>/mcp/example_api.py"]
startup_timeout_sec = 30
tool_timeout_sec = 1800
env_vars = ["EXAMPLE_API_KEY", "EXAMPLE_API_BASE_URL", "EXAMPLE_API_TIMEOUT_SECONDS"]
```

Codex's default tool timeout is short (60s) — the installer sets a generous
`tool_timeout_sec`. CLI equivalent: `codex mcp add knowledge_base -- python3 <bundle>/mcp/knowledge_base.py`.
Doc: <https://developers.openai.com/codex/mcp>

### Goose

`~/.config/goose/config.yaml`, under `extensions:`. Literal env values go in
`envs:`; credential variable **names** go in `env_keys:` (Goose pulls those from
its keyring / environment):

```yaml
extensions:
  knowledge_base:
    name: knowledge_base
    type: stdio
    cmd: python3
    args: ["<bundle>/mcp/knowledge_base.py"]
    enabled: true
    envs: {}
    env_keys: []
    timeout: 300
  example_api:
    name: example_api
    type: stdio
    cmd: python3
    args: ["<bundle>/mcp/example_api.py"]
    enabled: true
    envs: {}
    env_keys: [EXAMPLE_API_KEY, EXAMPLE_API_BASE_URL, EXAMPLE_API_TIMEOUT_SECONDS]
    timeout: 300
```

You can also add it interactively with `goose configure` → *Add Extension* →
*Command-line Extension*.
Doc: <https://block.github.io/goose/docs/guides/config-file/>

### Crush

Project `crush.json` (or `~/.config/crush/crush.json`). Top-level key is `mcp`
(not `mcpServers`); it expands `$VAR` / `${VAR}` in `command`, `args`, `env`,
`url`, and `headers`. `timeout` is in **milliseconds**:

```json
{
  "$schema": "https://charm.land/crush.json",
  "mcp": {
    "knowledge_base": {
      "type": "stdio",
      "command": "python3",
      "args": ["<bundle>/mcp/knowledge_base.py"],
      "env": {},
      "timeout": 1800000
    },
    "example_api": {
      "type": "stdio",
      "command": "python3",
      "args": ["<bundle>/mcp/example_api.py"],
      "env": {
        "EXAMPLE_API_KEY": "${EXAMPLE_API_KEY}",
        "EXAMPLE_API_BASE_URL": "${EXAMPLE_API_BASE_URL}",
        "EXAMPLE_API_TIMEOUT_SECONDS": "${EXAMPLE_API_TIMEOUT_SECONDS}"
      },
      "timeout": 1800000
    }
  }
}
```

Doc: <https://github.com/charmbracelet/crush> (README + `internal/config`).

### Grok Build (xAI)

CLI, like Claude/Codex:

```bash
grok mcp add knowledge_base -t stdio -c python3 -a <bundle>/mcp/knowledge_base.py
grok mcp add example_api    -t stdio -c python3 -a <bundle>/mcp/example_api.py
# verify: grok mcp list
```

The installer adds the stdio servers this way. (Both servers in this bundle are
stdio; the installer prints a note and asks you to add manually only for `http`
servers, which this bundle has none of.)

---

## Verifying a server works

**1. Python deps import (the installer does this as a smoke test):**

```bash
cd <bundle>
.venv/bin/python -m pip install -r mcp/requirements.txt   # one-time
( cd mcp && ../.venv/bin/python -c "import knowledge_base" )   # no output == OK
```

**2. Speak MCP to the server directly** — initialize + list its tools over stdio.
A real `tools/list` response (server name + tool names) means it starts and
exposes tools:

```bash
cd <bundle>
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | .venv/bin/python mcp/knowledge_base.py | head -c 2000
```

`knowledge_base` needs no credentials, so it always starts. (For `example_api`,
export `EXAMPLE_API_KEY` first or it may refuse to authenticate; swap the script
path for `mcp/example_api.py`.) See
[docs/TESTING-LOCALLY.md](docs/TESTING-LOCALLY.md) for a cleaner probe that sends
the `initialized` notification and prints just the tool names.

**3. Confirm your agent sees it:**

- **Claude Code:** `claude mcp list` (shows ✔ Connected per server) and
  `claude mcp get <name>`. Inside a session, `/mcp` lists connected servers/tools.
- **opencode / Crush:** launch the agent and ask it to list available tools, or
  check the MCP/tools panel.
- **Codex:** `codex mcp list`.
- **Goose:** `goose configure` → *Toggle Extensions* shows the extension enabled.

If a server shows as failed-to-connect, it almost always means its credential
env vars aren't exported in the shell that launched the agent — set them
(see above) and restart the agent. `knowledge_base` connecting but `example_api`
failing usually just means `EXAMPLE_API_KEY` isn't set.

---

## Troubleshooting

- **"already configured — skipped":** the server is already registered for that
  agent. To re-add, remove it first (e.g. `claude mcp remove <name> -s <scope>`).
- **`example_api` won't connect:** it's gated on `EXAMPLE_API_KEY`. Export the
  key in the shell that launches the agent, then restart. `knowledge_base` needs
  no credentials and should connect on its own.
- **`python3` / `git` required:** the installer needs `python3` to build the venv
  and run the servers, and `git` only when it has to clone the bundle.
- **Re-running is safe** — the installer never overwrites another server's config.

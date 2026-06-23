# Testing this bundle locally in a coding agent

This guide is for a **developer who wants to exercise the whole bundle by hand** —
its MCP servers, its personas / system prompts, and its protocols / workflows — in
a local coding agent, **before (or independent of) a full `fleet` deployment.**

`fleet` is the production runtime: it reads this bundle via
`FLEET_CLIENT_CONFIG_DIR`, parses [`manifest.yaml`](../manifest.yaml), and wires
the MCP catalog + persona + system prompt + protocols into the chat/agent loop for
you. You don't need any of that to *try the pieces*. A local coding agent can:

- run the bundle's [MCP servers](../mcp/) and call their tools,
- adopt a [persona](../personas/) / [system prompt](../system_prompts/) so the
  agent behaves like `fleet`'s "Aria" would, and
- execute a [protocol](../protocols/) as a one-shot task.

**Claude Code is the first-class, primary environment for this** — it's the one
this guide spells out in full. The other agents (Goose, opencode, Codex, Crush)
work the same way; a short [other-agents](#other-agents) note at the end points at
the right mechanism for each.

> **This guide builds *on top of* [`INSTALL.md`](../INSTALL.md).** INSTALL.md is the
> reference for the one-line MCP installer (`install.sh`), the per-agent MCP config
> snippets, and the credential-env-var contract. This guide does not duplicate
> those snippets — it links to them and adds the *testing* layer: how to verify a
> server live and call a tool, how to feed a persona/system-prompt into the agent,
> and how to run a protocol end-to-end. Read INSTALL.md's
> ["What this bundle ships"](../INSTALL.md#what-this-bundle-ships) table once for
> the server catalog; everything here assumes it.

Throughout, replace `<bundle>` with the absolute path to your checkout (e.g.
`/home/you/example-config`); most agent config files don't expand `~`.

---

## Contents

1. [One-time setup: the Python venv](#one-time-setup-the-python-venv)
2. [Part 1 — MCP servers](#part-1--mcp-servers)
   - [Register them (lean on the installer)](#register-them-lean-on-the-installer)
   - [The Claude Code path, explicitly](#the-claude-code-path-explicitly)
   - [Verify the knowledge base is live (stdio probe)](#verify-the-knowledge-base-is-live-stdio-probe)
   - [Call `kb_search` from inside the agent](#call-kb_search-from-inside-the-agent)
3. [Part 2 — Personas & system prompts](#part-2--personas--system-prompts)
   - [What they are in this bundle](#what-they-are-in-this-bundle)
   - [Applying Aria + a system prompt in Claude Code](#applying-aria--a-system-prompt-in-claude-code)
4. [Part 3 — Protocols & workflows](#part-3--protocols--workflows)
   - [What a protocol is here](#what-a-protocol-is-here)
   - [Worked end-to-end: ask-the-handbook (Claude Code)](#worked-end-to-end-ask-the-handbook-claude-code)
5. [Optional — enabling `example_api` with a key](#optional--enabling-example_api-with-a-key)
6. [Other agents](#other-agents)
7. [This pattern is copyable to other client bundles](#this-pattern-is-copyable-to-other-client-bundles)

---

## One-time setup: the Python venv

Every stdio MCP server in this bundle is plain Python launched as `python3
mcp/<server>.py`. They need their dependencies importable. The installer builds
this venv for you, but if you're wiring an agent up by hand, do it once:

```bash
cd <bundle>
python3 -m venv .venv
.venv/bin/pip install -r mcp/requirements.txt
```

A couple of things worth knowing for testing:

- **The same venv runs the test suite.** Before trusting a server, you can prove
  the Python side is healthy exactly the way CI does:

  ```bash
  cd <bundle>
  .venv/bin/pip install -r mcp/requirements.txt pytest ruff
  .venv/bin/python -m pytest mcp/ -q     # offline; no credentials needed
  .venv/bin/python -m ruff check mcp/
  ```

- **Which `python3` do your agents launch?** The MCP config snippets register the
  command as `python3` (see INSTALL.md). For a server's imports to resolve, the
  `python3` your agent finds on `PATH` must be the one with the bundle's deps. Two
  reliable options:
  - **Activate / point at the venv** so `python3` *is* `.venv/bin/python` — e.g.
    launch the agent from a shell where you've run `source .venv/bin/activate`, or
  - **Register the venv interpreter explicitly** by using
    `<bundle>/.venv/bin/python` instead of bare `python3` in the agent's MCP
    config (works in every agent's config file; see the per-agent snippets in
    INSTALL.md). This is the most robust choice for local testing because it
    doesn't depend on shell state.

---

## Part 1 — MCP servers

This bundle ships two servers (see
[INSTALL.md → "What this bundle ships"](../INSTALL.md#what-this-bundle-ships)):
`knowledge_base` (always on, no credentials) and `example_api` (gated on
`EXAMPLE_API_KEY`). **Start with `knowledge_base`** — it needs no secrets, so it's
the right server to learn the mechanics on. `example_api` is covered in its own
[optional section](#optional--enabling-example_api-with-a-key) at the end.

### Register them (lean on the installer)

The fastest path for **all** agents is the bundle's installer — it reads
`manifest.yaml` and registers every server into each agent it detects, writing the
credentials as env-var *references* (never values). See
[INSTALL.md → "one line"](../INSTALL.md#tldr--one-line) and the flags:

```bash
cd <bundle>
./install.sh --list      # show the servers + which agents are detected (no changes)
./install.sh --dry-run   # print exactly what WOULD be registered (no changes)
./install.sh             # register into every detected agent
./install.sh --agent claude --scope project   # one agent; Claude Code scope
```

The installer is idempotent (re-running skips servers an agent already has) and
non-destructive (it merges, never clobbers other servers). For the per-agent
**manual** config (opencode `opencode.json`, Codex `config.toml`, Goose
`config.yaml`, Crush `crush.json`, Grok), see
[INSTALL.md → "How it registers per agent"](../INSTALL.md#how-it-registers-per-agent-and-the-manual-snippet-for-each).

### The Claude Code path, explicitly

Claude Code is the primary testing environment, so here is the whole path spelled
out. There are two equivalent ways to register a server — the CLI (`claude mcp
add`, what the installer runs) and a checked-in `.mcp.json`. Both are current as
of Claude Code v2.1.x and support `${VAR}` / `${VAR:-default}` expansion in
`command`, `args`, `env`, `url`, and `headers`.

**A) CLI — `claude mcp add`** (the always-on `knowledge_base` server needs no
creds, so it's the ideal first target):

```bash
cd <bundle>

# stdio server, registered for all your projects (-s user). Use the venv
# interpreter so its deps resolve regardless of shell state:
claude mcp add knowledge_base -s user \
  -- <bundle>/.venv/bin/python <bundle>/mcp/knowledge_base.py
```

Scopes: `-s local` (this project, private), `-s project` (writes a shared
`.mcp.json` checked into the repo), `-s user` (all your projects). For *testing*,
`-s user` is convenient; `-s project` is right when you want the config to travel
with a repo.

**B) Project `.mcp.json`** — drop this at a project root and Claude Code picks it
up (it prompts once to approve project-scoped servers). Equivalent to the CLI:

```json
{
  "mcpServers": {
    "knowledge_base": {
      "type": "stdio",
      "command": "<bundle>/.venv/bin/python",
      "args": ["<bundle>/mcp/knowledge_base.py"]
    }
  }
}
```

> The `mcp/<server>.py` paths and the env-var names per server are all in
> `manifest.yaml` and in INSTALL.md's
> [server table](../INSTALL.md#what-this-bundle-ships). `manifest.yaml` is the
> source of truth — `./install.sh --list` prints the live list.

### Verify the knowledge base is live (stdio probe)

There are three levels of verification, cheapest first.

**1. The Python imports (offline smoke test).** No creds needed:

```bash
cd <bundle>/mcp && ../.venv/bin/python -c "import knowledge_base"   # silent == OK
```

**2. Speak MCP to the server directly.** Initialize, send the `initialized`
notification, then `tools/list`. A real tool list back proves the server starts
and exposes tools — this is independent of any agent:

```bash
cd <bundle>
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | .venv/bin/python mcp/knowledge_base.py 2>/dev/null | sed -n '2p' \
  | python3 -c "import sys,json; print([t['name'] for t in json.load(sys.stdin)['result']['tools']])"
```

For `knowledge_base` this prints its three tools:

```
['kb_list_categories', 'kb_search', 'kb_get_article']
```

You can call a tool over the same transport without an agent at all — send a
`tools/call` for `kb_search`. This is the fastest way to prove the handbook
actually loads and ranks results:

```bash
cd <bundle>
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"kb_search","arguments":{"query":"how much notice to request time off","limit":3}}}' \
  | .venv/bin/python mcp/knowledge_base.py 2>/dev/null | sed -n '2p' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['result']; print(r.get('structuredContent', r))"
```

The top hit is the `requesting-time-off` article — the exact article the
[ask-the-handbook protocol](#worked-end-to-end-ask-the-handbook-claude-code) below
cites.

**3. Confirm the agent sees it.** In **Claude Code**:

```bash
claude mcp list          # shows ✔ Connected / ✗ per server
claude mcp get knowledge_base
```

Then start a session and run the in-session command `/mcp`, which lists every
connected server with its tool count.

### Call `kb_search` from inside the agent

To actually **call a tool**, just ask in natural language — Claude routes to the
MCP tool. A few prompts that exercise each tool, no credentials required:

```
List the categories in the team knowledge base using the knowledge_base MCP server.
```

That triggers `kb_list_categories` and returns the handbook's categories
(People, Engineering, Security, Support, Data). Then:

```
Search the team knowledge base for "request time off" and show me the top results
with their article ids.
```

That triggers `kb_search` — a real round-trip through the MCP server. Follow up to
exercise `kb_get_article`:

```
Fetch the full "requesting-time-off" article and quote the notice period exactly.
```

---

## Part 2 — Personas & system prompts

### What they are in this bundle

This bundle separates the agent's *base behavior* from its *character*:

- **`system_prompts/`** — the base operating rules `fleet` puts at the top of the
  system prompt. [`chat.md`](../system_prompts/chat.md) is the interactive base;
  [`default.md`](../system_prompts/default.md) is the scheduled/one-shot base. They
  define rules like leading with the answer, grounding claims in tool results,
  tool-use discipline, and how to load on-demand MCP servers.
- **`personas/`** — a YAML *character* layered on top.
  [`assistant.yaml`](../personas/assistant.yaml) is this bundle's default persona,
  **"Aria"** — a general-purpose workspace assistant. It carries the voice, the
  operating approach, the grounding principles, the formatting rules, and a QA
  checklist. (The bundle also ships `analyst.yaml` — "Atlas" — and
  `onboarding-guide.yaml` — "Sage"; the same steps apply to any of them.)

In `fleet`, `PERSONA_DEFAULT=assistant` selects Aria and the system prompt is
applied automatically. **To test locally you reproduce that layering yourself** by
feeding the prompt text into the agent. The persona YAML is structured data, not a
ready-made prompt string — the practical move is to **hand the agent the file(s)
and tell it to adopt them**, which every agent below can do.

### Applying Aria + a system prompt in Claude Code

Claude Code gives you several mechanisms; pick by how persistent you want it.

**Option A — `--append-system-prompt` / `--append-system-prompt-file` (per
invocation, exact).** This injects the real prompt *text* into the system prompt,
which most faithfully mirrors `fleet`. Append the interactive base prompt from a
file and the persona as text:

```bash
claude \
  --append-system-prompt-file <bundle>/system_prompts/chat.md \
  --append-system-prompt "Adopt this persona for all responses (its voice,
operating approach, principles, and formatting):
$(cat <bundle>/personas/assistant.yaml)"
```

`--system-prompt` / `--system-prompt-file` *replace* the entire default prompt
instead of appending — usually too aggressive for testing inside Claude Code
(you'd lose its tool-use scaffolding), but available for a clean-room run. These
flags apply only to the current invocation.

**Option B — `CLAUDE.md` (project memory; persists, shareable).** This is the
closest analogue to how `fleet` always applies the persona. Create a `CLAUDE.md`
at your test project root that points Claude at the bundle's prompt files:

```markdown
# Test harness for the Northwind example bundle

When acting in this project, adopt the bundle's base rules and persona:

1. Read and follow `<bundle>/system_prompts/chat.md` as your base operating rules.
2. Read and follow `<bundle>/personas/assistant.yaml` — adopt Aria's voice,
   operating approach, grounding principles, and formatting rules.
3. The bundle's MCP servers (knowledge_base, …) are registered; prefer them over
   reimplementing their behavior.
```

(`claude` auto-discovers `CLAUDE.md` from the working dir up. Copy the two files
into the project if you'd rather not use absolute paths.)

**Option C — Output styles (persistent, switchable persona).** Claude Code's
[output styles](https://code.claude.com/docs/en/output-styles) are the built-in
way to save a persona you can toggle between sessions. Create
`.claude/output-styles/aria.md` from the persona content (Markdown with
frontmatter `name`/`description` and the behavior in the body — paste the voice,
principles, and formatting rules out of `assistant.yaml`), then select it with
`/output-style aria`.

---

## Part 3 — Protocols & workflows

### What a protocol is here

A **protocol** in [`protocols/`](../protocols/) is a self-contained playbook for
one task type. This bundle ships a few Markdown protocols —
[`ask-the-handbook.md`](../protocols/ask-the-handbook.md),
[`research-report.md`](../protocols/research-report.md),
[`weekly-status.md`](../protocols/weekly-status.md), and the annotated
[`example.md`](../protocols/example.md) template. They are prose step-by-step
procedures: they name the exact MCP tools to call, the inference rules, the output
sections, and the quality gates.

A protocol is "run" by the agent **reading it once and executing it step by step**
against a real task — exactly what `fleet` does, and exactly what you reproduce
locally. The recipe for testing any protocol is the same three ingredients:

1. **The persona / system prompt** in effect (Part 2) — so the agent has the voice
   and the formatting canon the protocol assumes.
2. **The MCP server(s) the protocol names**, registered (Part 1) — read the
   protocol's tool references to see which it needs.
3. **A task prompt** that points the agent at the protocol file and supplies the
   inputs. Most protocols include a copy-pasteable **Usage** line.

### Worked end-to-end: ask-the-handbook (Claude Code)

A concrete, credential-free pick: **persona `assistant` (Aria) + protocol
[`ask-the-handbook.md`](../protocols/ask-the-handbook.md) + the always-on
`knowledge_base` MCP server.** The protocol searches the knowledge base, reads the
source article, and answers with a citation — refusing to invent policy when the
handbook doesn't cover the question. It explicitly calls
`mcp_knowledge_base_kb_search`, `mcp_knowledge_base_kb_get_article`, and
`mcp_knowledge_base_kb_list_categories`.

**Step 1 — register the one MCP server it needs** (no credentials):

```bash
cd <bundle>
claude mcp add knowledge_base -s user \
  -- <bundle>/.venv/bin/python <bundle>/mcp/knowledge_base.py
```

**Step 2 — launch Claude Code with the persona + base prompt applied:**

```bash
claude \
  --append-system-prompt-file <bundle>/system_prompts/chat.md \
  --append-system-prompt "Adopt this persona:
$(cat <bundle>/personas/assistant.yaml)"
```

**Step 3 — verify the wiring** before running the protocol. In the session:

```
/mcp
```

Confirm `knowledge_base` is connected with its three tools. (Quick sanity check
you can ask for: *"List the knowledge base categories to confirm the server
responds."*)

**Step 4 — run the protocol**, using its own Usage line:

```
Read <bundle>/protocols/ask-the-handbook.md and follow it step by step.
Ask the handbook: how much notice do I need to give to request time off?
```

**What to watch for / how to grade the run.** A faithful run will, per the
protocol: call `kb_search` with the question as terms, take the top result's id
(`requesting-time-off`), call `kb_get_article` to read the **full** article (not
the snippet), then answer directly and **cite the source by title and id** — e.g.
*Source: "Requesting Time Off" (`requesting-time-off`)* — quoting the exact notice
period verbatim rather than paraphrasing it. Because Aria is in effect, the answer
leads with the specific number, stays tight, and never fabricates a figure.

**The "not in the handbook" path is worth testing too** — it's the protocol's
strict contract. Ask something the handbook doesn't cover:

```
Ask the handbook: what is the company's policy on expensing a home gym membership?
```

A correct run searches genuinely, then says plainly *"I couldn't find this in the
team knowledge base,"* optionally names the closest article (the expenses one),
and **does not invent a policy**. An honest "not found" is the right answer here —
that the agent refuses to guess is the thing you're verifying.

The same three-ingredient recipe works for every protocol — swap the protocol
file, keep Aria as the persona, and register whichever MCP server(s) that protocol
names.

---

## Optional — enabling `example_api` with a key

`example_api` is the credential-gated server. It registers but stays **dark**
until `EXAMPLE_API_KEY` is set, so everything above runs without it. To exercise
it, you need a key and (usually) a base URL pointing at a real JSON API.

**Step 1 — register it** (the installer does this; manual CLI shown for clarity):

```bash
cd <bundle>
claude mcp add example_api -s user \
  -e EXAMPLE_API_KEY='${EXAMPLE_API_KEY}' \
  -e EXAMPLE_API_BASE_URL='${EXAMPLE_API_BASE_URL}' \
  -e EXAMPLE_API_TIMEOUT_SECONDS='${EXAMPLE_API_TIMEOUT_SECONDS}' \
  -- <bundle>/.venv/bin/python <bundle>/mcp/example_api.py
```

**Step 2 — export the credentials, then launch the agent from that same shell.**
The `${VAR}` references resolve from your environment at run time; nothing secret
is written to config:

```bash
export EXAMPLE_API_KEY=...                          # required to bring it online
export EXAMPLE_API_BASE_URL=https://api.example.com/v1   # point at your JSON API
export EXAMPLE_API_TIMEOUT_SECONDS=30               # optional
claude
```

**Step 3 — confirm and call.** In the session, `/mcp` should now show
`example_api` connected alongside `knowledge_base`, with three tools:
`api_list_records`, `api_get_record`, and `api_submit_record`. Ask the agent to
list records from a resource your API exposes:

```
Use the example_api MCP server to list the first 5 records from the "items" resource.
```

> **`api_submit_record` is a write.** The manifest marks it as a *critical* tool,
> which under `fleet` means it pauses for an audit confirmation before running. A
> local agent has no such gate, so treat it carefully — point `EXAMPLE_API_BASE_URL`
> at a sandbox/test API before asking the agent to submit anything.

If `example_api` shows as failed-to-connect, the usual cause is that
`EXAMPLE_API_KEY` isn't exported in the shell that launched the agent — set it and
restart. See [INSTALL.md → Troubleshooting](../INSTALL.md#troubleshooting).

---

## Other agents

For Goose, opencode, Codex, and Crush, **lean on INSTALL.md for the MCP wiring**
and use the persona/prompt equivalent below. Protocol testing is identical to the
Claude Code flow: register the MCP server(s) the protocol names, put Aria in
effect, then give the agent the protocol's Usage line as the task. As with every
agent, point `cmd`/`args` (or `command`) at `<bundle>/.venv/bin/python
<bundle>/mcp/<server>.py` so deps resolve.

| Agent | MCP registration | Persona / system-prompt equivalent |
|---|---|---|
| **Goose** | [INSTALL.md → Goose](../INSTALL.md#goose) (`~/.config/goose/config.yaml` under `extensions:`; literals in `envs:`, credential names in `env_keys:`). Verify with `goose configure → Toggle Extensions`. | A [`.goosehints`](https://block.github.io/goose/docs/guides/using-goosehints/) file (global `~/.config/goose/.goosehints` or per-project) is added to the system prompt every request — reference `system_prompts/chat.md` + `personas/assistant.yaml` there. For a runnable, parameterized persona+task unit, use a [Goose recipe](https://block.github.io/goose/docs/category/recipes/). |
| **opencode** | [INSTALL.md → opencode](../INSTALL.md#opencode) (`opencode.json`, `command` is an array, env block is `environment`, `{env:VAR}` substitution) | An [`AGENTS.md`](https://opencode.ai/docs/rules/) file (project root or global `~/.config/opencode/AGENTS.md`) is added to context — reference the bundle's prompt + persona there. opencode also supports a per-agent custom system-prompt file via its [agent `prompt` config](https://opencode.ai/docs/agents/). |
| **Codex CLI** | [INSTALL.md → Codex CLI](../INSTALL.md#codex-cli) (`~/.codex/config.toml`; `env_vars` forwards credential names; generous `tool_timeout_sec`) | An [`AGENTS.md`](https://developers.openai.com/codex/guides/agents-md) file — repo-root for project scope or `~/.codex/AGENTS.md` for global — supplies custom instructions; reference the bundle's prompt + persona there. |
| **Crush** | [INSTALL.md → Crush](../INSTALL.md#crush) (`crush.json`; top-level key is `mcp`, `$VAR`/`${VAR}` expansion, `timeout` in **ms**) | A [context file](https://github.com/charmbracelet/crush) — `CRUSH.md` (or `AGENTS.md`) in the project root, plus global `~/.config/crush/CRUSH.md` — is read into context; reference the bundle's prompt + persona there. |

> The universal verification steps (probe the server over stdio; confirm the agent
> sees it) from [Part 1](#verify-the-knowledge-base-is-live-stdio-probe) apply to
> every agent. The most common failure is simply that a gated server's credential
> vars weren't exported in the shell that launched the agent.

---

## This pattern is copyable to other client bundles

Nothing in this guide is specific to this example except the names (Aria, the two
example servers, the handbook protocols). The **bundle contract is generic** —
`fleet` loads any `*-config` bundle the same way, and `install.sh` reads
`manifest.yaml` so it works unchanged in a sibling bundle. To adapt this guide for
your own bundle:

1. **MCP servers** — unchanged. `./install.sh --list` and the per-agent snippets in
   that bundle's INSTALL.md already reflect its own `manifest.yaml`. The
   register → export-creds → probe-stdio → call-a-tool loop is identical; only the
   server names and env-var names differ.
2. **Persona / system prompt** — point the same mechanisms
   (`--append-system-prompt-file`, `CLAUDE.md`, output styles, `.goosehints`,
   `AGENTS.md`, `CRUSH.md`) at *that* bundle's `system_prompts/` and
   `personas/*.yaml`.
3. **Protocols** — same three-ingredient recipe (persona + the MCP servers the
   protocol names + the protocol's own Usage line). Read the new `protocols/` to
   pick a worked example.

So a new bundle's testing guide is mostly this file with the names swapped — copy
it, retarget the file paths, and replace the
[worked example](#worked-end-to-end-ask-the-handbook-claude-code) with one of the
new bundle's real persona+protocol+MCP combinations.

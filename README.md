# example-config

A complete, **fork-this-to-start** client-config bundle for
[**fleet**](https://github.com/ElcanoTek/fleet) — the self-hosted, general-purpose
AI-agent platform you run yourself.

fleet ships **no** client content. At boot it loads a *bundle* from the
`FLEET_CLIENT_CONFIG_DIR` environment variable: a manifest plus prompts,
personas, playbooks, and MCP servers that turn the generic engine into *your*
team's agent workspace. This repo is a clean, generic example of that bundle —
branded for a fictional company, **Northwind**, with a default persona named
**Aria** — written so any team running agents (support, ops, research,
engineering, analytics, …) can fork it and swap in their own content.

> Nothing here is industry-specific and nothing here is secret. "Northwind" is a
> placeholder for your company; the example MCP servers, personas, and protocols
> exist to show you the *shape*. Replace them and the bundle is yours.

---

## Why fleet

fleet runs AI agents — both interactive real-time **chat** and recurring
**scheduled** tasks — on infrastructure you control. One Go process boots a
unified agent runtime, an execution sandbox, a scheduler, and a worker pool. The
design principles that matter when you author a bundle:

- **Any model.** fleet runs its own native agent loop, and models route
  OpenRouter-style, so you choose the right model per task instead of hard-wiring
  one vendor.
- **Sandboxed by default.** Every tool call — bash, Python, file I/O, MCP —
  executes inside a rootless-Podman container over a per-conversation workspace.
- **Cost-controlled.** Each turn runs against configurable per-task cost and
  token **ceilings**. A model that won't stop calling tools is bounded by the
  ceiling, the per-turn timeout, and an iteration cap — not by your invoice.
- **Connected to your data via brokered MCP credentials.** fleet speaks **MCP**
  and ships a per-deployment catalog. Credentials are brokered **host-side**: the
  broker injects the secret only when it runs a delegated MCP call, so it never
  enters the sandbox or the model's context. The manifest names the *variables*,
  never the *values*.
- **Reusable personas and protocols.** Standardize your team's agent recipes —
  the prompts, the connected tools, the guardrails — once, in a bundle, and point
  any deployment at it.
- **Standards-based, MIT-licensed, observable.** MCP is shipped and tested. The
  runtime emits structured events for every turn — tool calls, results, usage,
  cost — so you can see exactly what an agent did and what it cost.

See the [fleet README](https://github.com/ElcanoTek/fleet) for the full
architecture and deploy story.

## Why a config bundle

If your team keeps reaching for the same agent recipes — the same system prompt,
the same connected tools, the same "always cite the source" rule — a bundle is
where you write them down **once**. A bundle gives you:

- **One source of truth** for branding, model defaults, the MCP catalog, the
  empty-state cards, and the agent tool policy (`manifest.yaml`).
- **Versioned, reviewable agent behavior** — personas and protocols are plain
  files in git; a prompt change is a pull request.
- **A controlled execution environment** — the sandbox image is a bundle
  artifact (`sandbox/Containerfile`); its base tracks `fedora-minimal:latest` for
  current patches, and you can pin a digest if you want reproducible builds.
- **Portability** — point a dev box and a production box at the same checkout and
  they behave identically.

## How fleet loads this bundle

fleet reads the bundle directory from `FLEET_CLIENT_CONFIG_DIR` (default: fleet's
own generic `config/default`). Point it at a checkout of this repo:

```sh
export FLEET_CLIENT_CONFIG_DIR=/path/to/example-config
export PERSONA_DEFAULT=assistant        # selects "Aria" (personas/assistant.yaml)
```

At boot fleet parses `manifest.yaml`, resolves each MCP server's **enable gate**
and `${VAR}` env interpolation against the process environment, and reads the
`system_prompts/`, `personas/`, `protocols/`, and `skills/` directories.
`PERSONA_DEFAULT`
matches a persona's `name:` field and picks the default; users can still switch
persona per-conversation in the UI. The loader and the full manifest schema live
in
[`internal/clientconfig/clientconfig.go`](https://github.com/ElcanoTek/fleet/blob/main/internal/clientconfig/clientconfig.go).

## The bundle contract

A bundle is this annotated tree. Everything below ships in **this** repo:

```
example-config/
  manifest.yaml          # branding, model tiers, MCP catalog, cards, agent policy,
                         #   task templates, per-persona tool permissions — plus
                         #   copy-paste-ready commented examples of http_tools,
                         #   webhook_triggers, providers, and pricing
  system_prompts/
    default.md           # base prompt for SCHEDULED agents
    chat.md              # base prompt for INTERACTIVE chat
  personas/              # *.yaml — one per persona; PERSONA_DEFAULT picks the default
    assistant.yaml       #   Aria  — general-purpose workspace assistant (the default)
    analyst.yaml         #   Atlas — data & research analyst
    onboarding-guide.yaml#   Sage  — internal-knowledge / onboarding guide
  protocols/             # *.md | *.yaml — reusable playbooks the agent runs on demand
    example.md           #   annotated template: what a protocol is and how to write one
    ask-the-handbook.md  #   grounded Q&A over the knowledge base, with citations
    research-report.md   #   web + attached-source research into a cited brief
    weekly-status.md     #   a SCHEDULED playbook: gather inputs → compute → write an artifact
  skills/                # <name>/SKILL.md folders — Agent Skills: instructions + bundled code/refs
    example-skill/       #   annotated template: skill format + progressive disclosure (+ demo script)
    csv-profiler/        #   profile a CSV with the stdlib only (types, nulls, basic stats)
  mcp/                   # the bundle's Python MCP servers (+ tests, requirements.txt)
    knowledge_base.py    #   always-on; searches mcp/data/handbook.json
    example_api.py       #   credential-gated generic REST connector
    data/handbook.json   #   the demo company handbook (swap for your content)
  sandbox/
    Containerfile        # the execution-sandbox image for agent tool calls
  evals/                 # golden regression sets replayed by `fleet eval run`
    example.yaml         #   template set: the case format + every scorer kind
  install.sh             # register these MCP servers into your own coding agent (see INSTALL.md)
```

The manifest's full schema — `Branding`, `Models`, `Sandbox`, the MCP
`ServerDef` with its enable gate and `${VAR}` semantics, the empty-state
`cards`, the `agent_policy`, `task_templates`, the per-persona
`tool_permissions`, and the `http_tools` / `webhook_triggers` / `providers` /
`pricing` sections — is documented field-by-field in
[`clientconfig.go`](https://github.com/ElcanoTek/fleet/blob/main/internal/clientconfig/clientconfig.go).
Read `manifest.yaml` itself too — it is heavily commented.

## A tour of what this bundle ships

### Branding (white-label)

Five lines in `manifest.yaml` under `branding:` are the **whole** white-label
surface — app name, login title/tagline, and social-share strings. This bundle
sets them to **Northwind**. Change those lines and the app is yours.

### MCP servers — connect agents to your data

This bundle ships two servers that demonstrate the two shapes you will use for
everything else.

| Server | Gate | Credentials | What it shows |
| --- | --- | --- | --- |
| `knowledge_base` | `always: true` | none | Point an agent at **your own docs.** Searches a bundled JSON handbook. |
| `example_api` | `enabled_env: [EXAMPLE_API_KEY]` | brokered host-side | The **gated connector** pattern — stays dark until the key is set. |

- **`knowledge_base`** is the canonical "answer from our docs" pattern. It serves
  a small company handbook (`mcp/data/handbook.json`, with People / Engineering /
  Security / Support / Data articles) and exposes tools to search, list
  categories, and read articles. It needs no credentials, so a fresh checkout
  runs clean. Swap the JSON for your content — or rewrite the server to read your
  wiki, your vector store, your ticketing system.
- **`example_api`** is a generic REST connector that registers but stays **dark**
  until `EXAMPLE_API_KEY` is present in the environment. This is the **host-side
  credential broker** in miniature: the manifest names the variable
  (`account_vars: [EXAMPLE_API_KEY]`), fleet holds the secret host-side, and it
  injects the value only when it runs a delegated MCP call — never into the
  sandbox or the model. Point `EXAMPLE_API_BASE_URL` at any JSON API to make it
  real. Its entry also carries a **live `${FLEET_WORKSPACE}` mapping**
  (`EXAMPLE_API_OUTPUT_DIR: "${FLEET_WORKSPACE}/outputs"`): fleet substitutes a
  writable workspace directory at subprocess launch — and drops the key on
  spawns with no workspace — so the server writes a JSON receipt of each
  submitted record when it can, and degrades gracefully when it can't.

The manifest also carries a commented, copy-paste-ready third entry showing an
**HTTP (remote) MCP server** with `optional: true` — the opt-in-per-conversation
pattern, with `enabled_by_default`, `beta`, and a `tools:` allowlist.

Its write tool, `api_submit_record`, is listed under the manifest's
`critical_tools` — so fleet makes the agent stop and get an **audit confirmation**
before that consequential action runs (with a per-tool approval window from
`critical_tool_timeouts`). The read tools (`api_get_record`,
`api_list_records`, and the `kb_*` tools) are listed under `parallel_safe_tools`,
so fleet may dispatch them concurrently within a single turn.

### Personas — the same engine, different voice and remit

Personas live in `personas/*.yaml`. `PERSONA_DEFAULT` (matched against a
persona's `name:`) selects the default; users pick any persona per conversation
in the UI.

| File | Name | Remit |
| --- | --- | --- |
| `assistant.yaml` | **Aria** | Everyday workspace assistant — the shipped default. |
| `analyst.yaml` | **Atlas** | Data & research analyst — computes on real data, shows the work. |
| `onboarding-guide.yaml` | **Sage** | Internal-knowledge guide — answers "how do we do X here?", always cited from the handbook. |

The manifest's `personas:` block adds **per-persona tool permissions** — a
least-privilege gate that can only *narrow* what a persona sees, never widen
it. This bundle uses the deny-form on Sage (`deny: ["mcp:example_api/*"]`): the
onboarding guide never needs the REST connector, so it never sees it. Aria and
Atlas have no entry and keep every permitted tool.

### Quick-start cards (empty state)

The chat home screen shows the cards declared under `empty_state.cards`. This
bundle ships four that span team types: **Summarize a document**, **Analyze a
dataset** (runs Python in the sandbox on an attached CSV), **Ask the handbook**
(grounded in `knowledge_base`), and **Draft something**.

### Task templates — pre-filled scheduled tasks

`task_templates:` seeds the Operations Center's "new task from a template"
picker. This bundle ships three that exercise its own content: **Weekly Status
Report** (runs `protocols/weekly-status.md` on a Friday cron, as Atlas, with an
SLA hint), **Handbook Freshness Check** (audits the knowledge base, with a
fallback model), and **Research Brief** (a `{topic}` placeholder the UI prompts
for, with network enabled). Templates pre-fill only form-editable fields — the
security-sensitive knobs (credentials, MCP selection, triggers) are deliberately
not templatable, so a template can never widen a task's authority.

### Protocols — reusable playbooks

Protocols under `protocols/` encode "the way we do this here" once so every
agent — chat or scheduled — runs it the same way. Start with `example.md`
(an annotated template), then see `ask-the-handbook.md` (grounded, cited Q&A),
`research-report.md` (web research into a cited brief), and `weekly-status.md`
(a **scheduled** playbook that gathers inputs, computes honest metrics in Python,
and writes a status artifact to the workspace).

### Skills — packaged capabilities (instructions + code)

Skills under `skills/` follow the open
[**Agent Skills**](https://github.com/anthropics/skills) standard that fleet
implements. A skill is the packaged sibling of a protocol: where a protocol is a
single Markdown playbook, a skill is a *folder* —
`skills/<name>/SKILL.md` plus optional reference `.md` files and a `scripts/`
directory — so it can bundle deterministic code and lookup material alongside its
instructions. Each `SKILL.md` opens with YAML frontmatter (`name`, which must
equal the folder name, and a specific `description`); the loader
(`clientconfig.ReadSkills`) reads them at boot.

Skills use **progressive disclosure** to keep context lean: (1) the
`description` is always in the system-prompt roster — it is the trigger; (2) the
`SKILL.md` body and any sibling reference files load only when the skill is used;
(3) scripts are *run*, not read into context, so deterministic logic executes
instead of being re-derived. The optional `allowed-tools` frontmatter field is
advisory metadata only — fleet does **not** enforce it as an authorization gate;
govern consequential tools through `agent_policy` instead.

This bundle ships two:

| Skill | What it shows |
| --- | --- |
| `example-skill` | The annotated **template** — frontmatter rules, the three disclosure levels, a `REFERENCE.md`, and a tiny stdlib demo script. Copy it to start your own. |
| `csv-profiler` | A genuinely useful skill with a real **standard-library-only** script that profiles a CSV (row/col counts, per-column type inference, null counts, basic stats) — so it runs even without pandas. |

### Sandbox

`sandbox/Containerfile` defines the rootless container that `bash` and
`run_python` execute inside — a Fedora + Python data-analysis stack
(pandas/numpy/scipy/matplotlib/scikit-learn, plus document and image tooling). It
runs `--read-only`, `--cap-drop=ALL`, `--network=none`, as a non-root user, and
its base tracks **`fedora-minimal:latest`** so rebuilds pick up current patches
(pin a digest if you want reproducible builds). The "Analyze a dataset" card
relies on this stack. The image is a per-bundle artifact: add the packages *your*
agents need.

### Evals — regression-gate your bundle

`evals/` holds **golden regression sets**: known-good prompts replayed through
fleet's governed run loop at a pinned model and scored against expectations
(`contains` / `regex` / `equals` / an `llm_judge` rubric). This is how you gate
a model swap, a persona edit, or any manifest change on *"did my known-good
tasks get worse?"* before it reaches production:

```sh
fleet eval run example --bundle-path "$PWD"   # exit 0 = pass, 1 = fail — CI-ready
```

The shipped [`evals/example.yaml`](evals/example.yaml) is a template set showing
the case format and every scorer kind. Grow real sets from real runs with
`fleet eval capture --task <uuid>` (or `--conversation <id>`), which appends the
captured case to `evals/<set>.yaml`. Each `fleet eval run` replays against live
models (real spend, no cache); wire it into your bundle repo's CI to make the
gate automatic. See fleet's
[docs/EVALS.md](https://github.com/ElcanoTek/fleet/blob/main/docs/EVALS.md).

## Make it yours

1. **Rebrand.** Edit the five `branding:` lines in `manifest.yaml`. Rename the
   persona files and their `name:` fields, and set `PERSONA_DEFAULT` to your
   default.
2. **Point the knowledge base at your docs.** Replace `mcp/data/handbook.json`
   with your own content — or edit `mcp/knowledge_base.py` to read your wiki,
   database, or vector store.
3. **Add your MCP servers.** Drop a Python server under `mcp/`, add an entry to
   `mcp_servers[]` with the right enable gate (`always`, or `enabled_env` for a
   credential-gated connector), and name its credential *variables* (never
   values) — fleet brokers the secret host-side. See
   [`mcp/README.md`](mcp/README.md) for the server-authoring guide and tests.
   For scheduled inputs, map your connector's input-directory variable to
   `${FLEET_WORKSPACE}/inputs`; map an attribution variable to
   `${FLEET_TASK_ID}` when the remote system records the originating task.
4. **Govern new tools.** List read-only tools in `agent_policy.parallel_safe_tools`
   and writes / consequential actions in `critical_tools` so they require an
   audit gate before running.
5. **Write personas, protocols, and skills.** Copy an existing `personas/*.yaml`
   and `protocols/*.md` and adapt the voice, remit, and steps to your team. For a
   capability that ships code or reference files, copy the `skills/example-skill/`
   folder (the annotated template) and replace its body and `scripts/`.
6. **Tune the sandbox.** Add packages your agents need to `sandbox/Containerfile`.
   The base tracks `fedora-minimal:latest`; pin a digest there if you want
   reproducible builds.
7. **Capture goldens and gate regressions.** Once a scheduled task or a
   conversation does something well, `fleet eval capture` it into an
   `evals/<set>.yaml` and run `fleet eval run <set>` in CI — so the next model
   swap or prompt edit has to prove it didn't make your known-good work worse.

## Where to go next

- **[INSTALL.md](INSTALL.md)** — register this bundle's MCP servers into your own
  coding agent (Claude Code, Goose, opencode, Codex, …) with the one-line
  `install.sh`, before fleet is even provisioned.
- **[docs/TESTING-LOCALLY.md](docs/TESTING-LOCALLY.md)** — hands-on guide to
  exercising the whole bundle — servers, personas, prompts, and protocols — in a
  local coding agent.
- **[mcp/README.md](mcp/README.md)** — author and test the Python MCP servers
  (venv setup, `pytest`, `ruff`).
- **[fleet README](https://github.com/ElcanoTek/fleet)** /
  [`clientconfig.go`](https://github.com/ElcanoTek/fleet/blob/main/internal/clientconfig/clientconfig.go)
  — what fleet is, the deploy lifecycle, and the authoritative bundle schema.

## License

Released under the [MIT License](LICENSE).

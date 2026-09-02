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
export PERSONA_DEFAULT=assistant        # personas/assistant.yaml — the persona named "Aria"
```

At boot fleet parses `manifest.yaml`, resolves each MCP server's **enable gate**
and `${VAR}` env interpolation against the process environment, and reads the
`system_prompts/`, `personas/`, `protocols/`, `prompts/`, and `skills/`
directories.
`PERSONA_DEFAULT` selects the default persona by **file basename**: fleet reads
`personas/<value>.yaml` (so `assistant`, not `Aria` — the `name:` field inside
the file is display and prompt content, never the selector). Users can still
switch persona per-conversation in the UI. The loader and the full manifest
schema live in
[`internal/clientconfig/clientconfig.go`](https://github.com/ElcanoTek/fleet/blob/main/internal/clientconfig/clientconfig.go).

## The bundle contract

A bundle is this annotated tree. Everything below ships in **this** repo:

```
example-config/
  manifest.yaml          # branding, model tiers, MCP catalog, cards, agent policy,
                         #   task templates, per-persona tool permissions — plus
                         #   copy-paste-ready commented examples of http_tools,
                         #   webhook_triggers, providers, pricing, hooks, and a
                         #   remote_mcp_catalog directory entry
  system_prompts/
    default.md           # base prompt for SCHEDULED agents
    chat.md              # base prompt for INTERACTIVE chat
  personas/              # *.yaml — one per persona; PERSONA_DEFAULT picks one by file basename
    assistant.yaml       #   Aria  — general-purpose workspace assistant (the default)
    analyst.yaml         #   Atlas — data & research analyst
    onboarding-guide.yaml#   Sage  — internal-knowledge / onboarding guide
  protocols/             # *.md | *.yaml — reusable playbooks the agent runs on demand
    example.md           #   annotated template: what a protocol is and how to write one
    ask-the-handbook.md  #   grounded Q&A over the knowledge base, with citations
    research-report.md   #   web + attached-source research into a cited brief
    weekly-status.md     #   a SCHEDULED playbook: gather inputs → compute → write an artifact
  prompts/               # Git-backed library shown in Chat + Operations Center
    weekly-project-brief.yaml # exact prompt content with browse-friendly metadata
  skills/                # <name>/SKILL.md folders — Agent Skills: instructions + bundled code/refs
    example-skill/       #   annotated template: skill format + progressive disclosure (+ demo script)
    csv-profiler/        #   profile a CSV with the stdlib only (types, nulls, basic stats)
  plugins/               # Agent Plugins (agent-plugins.org): portable skills + MCP packages
    example-plugin/      #   plugin.json + one skill + mcp.json (a stdio server using PLUGIN_ROOT/PLUGIN_DATA)
  mcp/                   # the bundle's Python MCP servers (+ tests, requirements.txt)
    knowledge_base.py    #   always-on; searches mcp/data/handbook.json
    example_api.py       #   credential-gated generic REST connector
    data/handbook.json   #   the demo company handbook (swap for your content)
  sandbox/
    Containerfile        # the execution-sandbox image for agent tool calls
  evals/                 # golden regression sets replayed by `fleet eval run`
    example.yaml         #   template set: the case format + every scorer kind
  assets/                # brand assets referenced from manifest.yaml
    northwind-mark.svg   #   the placeholder mark the (commented) branding.logo points at
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

`branding:` in `manifest.yaml` is the whole white-label surface, and it covers
four things:

| | Field | Notes |
| --- | --- | --- |
| Strings | `app_name`, `login_title`, `login_tagline`, `share_title`, `share_description` | Rendered in-app, in the browser tab, and in social-share cards. |
| Mark | `logo` | Bundle-relative image path, served straight from this bundle — no web rebuild, nothing copied into fleet. Omit it and the rail shows fleet's own mark. Ships **commented** here: the field needs a fleet at/past #886 (2026-07-29) — see the dated note in `manifest.yaml`. |
| Unfurl card | `share_image` | The `og:image`/`twitter:image` scrapers show when a deployment link is pasted into Slack/Teams/Discord. PNG/WebP/JPEG, served from the bundle. Also ships **commented**: needs a fleet at/past #900. |
| Palette | `colors.light` / `colors.dark` | 19 tokens, applied as a render-blocking stylesheet so even the login page paints in your colors. |

This bundle brands the strings and the neutral zinc palette as **Northwind**
and ships a placeholder mark at `assets/northwind-mark.svg` behind the
commented `logo:` line (uncomment it once your fleet understands the field).
Change them and the app is yours; a sparse block is fine, since every field
falls back to fleet's generic value.

Two things worth knowing before you tune the palette. Fleet's defaults for the
structure, scrim, and rail tokens are hand-tinted from **fleet's** primary hue
rather than derived from yours, so overriding `primary` alone leaves
fleet-tinted emphasis borders and rail rows next to your brand — set the whole
block, as `manifest.yaml` does. And the semantic status colors
(success / danger / warning) are deliberately **not** themable: they encode
meaning, so a failed tool call reads as failure in every deployment.

Full reference: fleet's
[docs/BRANDING.md](https://github.com/ElcanoTek/fleet/blob/main/docs/BRANDING.md).
The browser **tab title** and PWA name follow `branding.app_name` too, but only
on a fleet at or past #899 (`f793c6e`, 2026-07-30): from there the web layer
resolves the name server-side per request (via the token-gated `/brand/meta`),
so no web rebuild is needed, and the build-time `NEXT_PUBLIC_APP_NAME` env
survives only as the fallback shown when the backend is unreachable. On an
older fleet the tab title is *only* `NEXT_PUBLIC_APP_NAME`, baked at web build
time and defaulting to "Fleet" — keep setting it there until the deployment
catches up.

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

`knowledge_base` also declares a **`probe:`** — the bundle-vetted, read-only
canary call that `fleet mcp test --deep` executes to prove the server works
end-to-end (here: that the handbook actually loaded), one rung past the
tools/list handshake. The probe runner only ever calls what a manifest
declares, so the author vets the call for side effects once, in the manifest.

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

Personas live in `personas/*.yaml`. `PERSONA_DEFAULT` (the **file basename** —
`assistant` for `personas/assistant.yaml`, not the display name `Aria`) selects
the default; users pick any persona per conversation in the UI.

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

### Prompts — the hybrid prompt library

Files under `prompts/` appear in Fleet's prompt picker in both Chat and the
Operations Center. They are read-only in the UI and remain versioned and
reviewable in this repository. Fleet preserves the exact YAML, Markdown, or text
body when inserting it into a draft. The same picker also holds private or
workspace-shared prompts created by non-Git users and can export the visible
library as a JSON backup. Start with `weekly-project-brief.yaml` and the authoring
notes in `prompts/README.md`.

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

### Agent Plugins — the portable package for skills + MCP servers

`plugins/` holds **Agent Plugins**: the open, vendor-neutral
[Agent Plugins standard](https://agent-plugins.org) (v1.0.0) that packages Agent
Skills and MCP servers together in one directory with a `plugin.json` manifest.
The same directory loads in fleet **and** in Cursor, VS Code, GitHub Copilot,
ChatGPT/Codex, Kiro and the other compatible clients, so a plugin is written
once and shared across tools — the reason to reach for it over a bare skill or a
manifest `mcp_servers` entry is exactly that portability.

```
plugins/
  example-plugin/
    plugin.json                       # REQUIRED: "$schema" + "name" (+ metadata)
    skills/plugin-quickstart/SKILL.md # an ordinary Agent Skill
    mcp.json                          # one stdio server: python3 ${PLUGIN_ROOT}/server/plugin_notes.py
    server/plugin_notes.py            # a scratch-notes server that persists in ${PLUGIN_DATA}
```

fleet (from the release that implements [ADR-0054](https://github.com/ElcanoTek/fleet/blob/main/docs/adr/0054-agent-plugins.md))
merges a plugin's skills into the same roster as `skills/` — this bundle's own
skill wins a name collision, a plugin's wins over fleet's built-in pack — and
appends its `mcp.json` servers to the MCP catalog as always-on entries launched
in the plugin root with `PLUGIN_ROOT` / `PLUGIN_DATA` set, subject to every gate a
manifest server already has (host-side brokering, `agent_policy`, hot reload).
Older fleet releases ignore the directory. An unknown `plugin.json` field is
reported and ignored, a bad server entry skips only itself, and a plugin defect
never blocks the bundle; `fleet validate-config` lists the problems as advisories.
A plugin is bundle content with the bundle's trust class — review it like `mcp/`
and `skills/`. Details: fleet's
[`docs/AGENT-PLUGINS.md`](https://github.com/ElcanoTek/fleet/blob/main/docs/AGENT-PLUGINS.md).

The example plugin ships:

| Part | What it shows |
| --- | --- |
| `plugin-quickstart` skill | How the plugin is laid out, how to use its server, and how to author or port a plugin. |
| `plugin_notes` server | A stdio MCP server started as `python3 ${PLUGIN_ROOT}/server/plugin_notes.py` that keeps a scratch-notes file under `${PLUGIN_DATA}` — the persistent, update-surviving data dir every conformant client provides. Tools: `plugin_info`, `note_add`, `note_list`, `note_clear`. |

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

1. **Rebrand.** Edit `branding:` in `manifest.yaml` — the strings, the `colors`
   block, and (on a fleet new enough for the fields — see the dated notes in
   the manifest) uncomment `logo` / `share_image`, replacing
   `assets/northwind-mark.svg` with your own files. The app name, tab title,
   and share cards all follow the bundle at request time — no web rebuild.
   Rename the persona files and their `name:` fields, and set `PERSONA_DEFAULT`
   to your default persona's **file basename**. Run `fleet validate-config` to
   catch a bad `logo` path before you restart into it.
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
5. **Write personas, prompts, protocols, and skills.** Copy an existing
   `personas/*.yaml`, `prompts/*.yaml`, or `protocols/*.md` and adapt it to your
   team. Git-backed prompt files appear directly in the shared Prompt Library;
   UI-authored prompts cover teammates who do not use Git. For a
   capability that ships code or reference files, copy the `skills/example-skill/`
   folder (the annotated template) and replace its body and `scripts/`.
6. **Tune the sandbox.** Add packages your agents need to `sandbox/Containerfile`.
   The base tracks `fedora-minimal:latest`; pin a digest there if you want
   reproducible builds.
7. **Capture goldens and gate regressions.** Once a scheduled task or a
   conversation does something well, `fleet eval capture` it into an
   `evals/<set>.yaml` and run `fleet eval run <set>` in CI — so the next model
   swap or prompt edit has to prove it didn't make your known-good work worse.

### Deployment-level knobs (host env, not bundle)

Two web **build-time** env vars deliberately stay outside the bundle, because
they are properties of the host, not of the client whose branding it wears
(one bundle can be deployed at more than one origin):

- **`NEXT_PUBLIC_PUBLIC_ORIGIN`** — the deployment's public origin, e.g.
  `https://chat.yourco.com`. fleet's web layer resolves relative unfurl URLs
  (the OG/share image, icons) against it via `metadataBase`. Left unset it
  falls back to the placeholder `https://chat.example.com`, so links pasted
  into Slack/Teams unfurl with image URLs pointing at a host that isn't yours
  — **silently**, since nothing errors. fleet's `bootstrap.sh` / `update.sh`
  set it at web build time; set it yourself on any hand-rolled deploy.
- **`NEXT_PUBLIC_APP_NAME`** — on a fleet at or past #899 (`f793c6e`), only the
  fallback name shown when the backend is unreachable; `branding.app_name` wins
  whenever fleet can be reached. On an older fleet it is the *only* source of
  the browser tab title and PWA name, so set it to match `branding.app_name`
  or the tab keeps saying "Fleet".

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

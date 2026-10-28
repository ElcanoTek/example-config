# example-config

A complete, **fork-this-to-start** client-config bundle for
[**fleet**](https://github.com/ElcanoTek/fleet) — the self-hosted, general-purpose
AI-agent platform you run yourself.

fleet ships **no** client content. At boot it loads a *bundle* from the
`FLEET_CLIENT_CONFIG_DIR` environment variable: a manifest plus prompts,
personas, playbooks, and MCP servers that turn the generic engine into *your*
team's agent workspace. This repo is a clean, generic example of that bundle —
branded for a fictional company, **Northwind**, with a default persona named
**Victoria** — written so any team running agents (support, ops, research,
engineering, analytics, …) can fork it and swap in their own content.

It is also the starting point for every Elcano client bundle: it ships the
standard client stack's connectors (the deployment's own **Pages** and **Lens**,
**inbound email** through Elcano Explorer and **outbound email** through
Amazon SES), each dark until its
credentials are in fleet's host environment, plus two helpers that turn a
client's brand colors and logo into the palette, email theme and link card. A
client fork changes branding, persona and docs; it never copies another
client's bundle. See [Make it yours](#make-it-yours).

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
export FLEET_PERSONA_DEFAULT=victoria         # personas/victoria.yaml (new chats)
export FLEET_PERSONA=personas/victoria.yaml   # scheduled runs
```

Set both: fleet's stock default for each is `assistant`, which this bundle does
not ship.

At boot fleet parses `manifest.yaml`, resolves each MCP server's **enable gate**
and `${VAR}` env interpolation against the process environment, and reads the
`system_prompts/`, `personas/`, `protocols/`, `prompts/`, and `skills/`
directories.
`FLEET_PERSONA_DEFAULT` (fleet still accepts the legacy bare `PERSONA_DEFAULT`)
selects the default persona by **file basename**: fleet reads
`personas/<value>.yaml` (so `victoria`, not `Victoria` — the `name:` field inside
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
  personas/              # *.yaml — one per persona; FLEET_PERSONA_DEFAULT picks one by file basename
    victoria.yaml        #   Victoria — the starter persona a client fork fills in (the default)
  protocols/             # *.md | *.yaml — reusable playbooks the agent runs on demand
    example.md           #   annotated template: what a protocol is and how to write one
    ask-the-handbook.md  #   grounded Q&A over the knowledge base, with citations
    research-report.md   #   web + attached-source research into a cited brief
    weekly-status.md     #   a SCHEDULED playbook: gather inputs → compute → write an artifact
    email-style.yaml     #   the report-email template, rules and chart colors (render_email)
    email_styles/        #   email themes: hex find-replace maps (default.yaml = unbranded)
  prompts/               # Git-backed library shown in Chat + Operations Center
    weekly-project-brief.yaml # exact prompt content with browse-friendly metadata
  skills/                # <name>/SKILL.md folders — Agent Skills: instructions + bundled code/refs
    example-skill/       #   annotated template: skill format + progressive disclosure (+ demo script)
    csv-profiler/        #   profile a CSV with the stdlib only (types, nulls, basic stats)
    lens-classification/ #   run and read Lens jobs through the `lens` connector
    pages-dashboards/    #   build, publish and update dashboards through the `pages` connector
  plugins/               # Agent Plugins (agent-plugins.org): portable skills + MCP packages
    example-plugin/      #   plugin.json + one skill + mcp.json (a stdio server using PLUGIN_ROOT/PLUGIN_DATA)
  mcp/                   # the bundle's Python MCP servers (+ tests, requirements.txt)
    knowledge_base.py    #   searches mcp/data/handbook.json (off by default: demo content)
    example_api.py       #   credential-gated generic REST connector
    ses_sender.py        #   render, lint and send audited outbound email (`ses_outbound`)
    email_render.py      #   render_email's HTML builder; email_lint.py its checks
    utils/               #   shared run ledger, URL guard and filesystem helpers
    data/handbook.json   #   the demo company handbook (swap for your content)
  sandbox/
    Containerfile        # the execution-sandbox image for agent tool calls
  evals/                 # golden regression sets replayed by `fleet eval run`
    example.yaml         #   template set: the case format + every scorer kind
  assets/                # brand assets referenced from manifest.yaml
    northwind-mark.svg   #   the placeholder mark branding.logo points at
    email-logo.png       #   the mark for the report-email header (PNG, 2x, made for its dark band)
    northwind-share.png  #   the generated 1280x640 unfurl card (branding.share_image)
    make-palette.py      #   brand hexes -> colors block + email theme + chart colors, contrast-checked
    make-share-card.py   #   regenerates the unfurl card from the branding block
  docs/                  # deployment-record templates a client fork fills in
    DEPLOYMENT.md        #   hosts, decisions and checklist for the client's stack
    EMAIL-DEPLOYMENT.md  #   the client's SES/S3 email tenant record
    onboarding/solution-engineer-handoff.md  # the starter persona's sources and open topics
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
| Mark | `logo` | Bundle-relative image path, served straight from this bundle — no web rebuild, nothing copied into fleet. Omit it and the rail shows fleet's own mark. Needs a fleet at/past #886 (2026-07-29). |
| Unfurl card | `share_image` | The `og:image`/`twitter:image` scrapers show when a deployment link is pasted into Slack/Teams/Discord. PNG/WebP/JPEG, served from the bundle. Needs a fleet at/past #900. |
| Palette | `colors.light` / `colors.dark` | 19 tokens, applied as a render-blocking stylesheet so even the login page paints in your colors. |

This bundle brands the strings and the neutral zinc palette as **Northwind**,
with a placeholder mark at `assets/northwind-mark.svg` and a generated card at
`assets/northwind-share.png`. Change them and the app is yours.

**Two helpers do the brand work.** `assets/make-palette.py` takes a client's
brand hexes (`--primary`, optionally `--secondary`, `--accent`, `--ink`,
`--paper`, `--dark-background`) and writes all 19 tokens for both modes, the
matching report-email theme under `protocols/email_styles/themes/` and the
email chart colors, moving a color along its own hue only where it misses 4.5:1
for text, labels and `accent` (fleet paints it as link text) or 3:1 for
`primary` and `secondary` on the page and on `surface_1` (fleet uses them only
as graphics: fills, borders, active bars and a chart series); `--check`
re-verifies a hand-edited block and runs in the tests.
`assets/make-share-card.py --host fleet.<domain>` then regenerates the unfurl
card from the strings, the palette and the mark.

Two things worth knowing before you tune the palette. Fleet's defaults for the
structure, scrim, and rail tokens are hand-tinted from **fleet's** primary hue
rather than derived from yours, so overriding `primary` alone leaves
fleet-tinted emphasis borders and rail rows next to your brand — set the whole
block, as `manifest.yaml` does. And the semantic status colors
(success / danger / warning) are deliberately **not** themable: they encode
meaning, so a failed tool call reads as failure in every deployment.

Full reference: fleet's
[docs/BRANDING.md](https://github.com/ElcanoTek/fleet/blob/main/docs/BRANDING.md).

**The same block also brands the Auth service.** A client deployment that
uses central sign-in (the `auth` repo in password mode) points Auth's
`AUTH_CLIENT_CONFIG_DIR` at a checkout of this bundle, and Auth reads the
`branding:` block above — `app_name`, `login_title`, `login_tagline`, `logo`
and `colors` — for its sign-in, signed-in and admin pages. Nothing extra is
needed in the bundle: one manifest, one palette, both services. Auth ignores
the rest of the file and refuses to start on a bundle it cannot read, so run
`auth-server -check-config` after a branding change. See auth's
[docs/DEPLOY.md](https://github.com/ElcanoTek/auth/blob/main/docs/DEPLOY.md)
("Branding from the client bundle").

The browser **tab title** and PWA name follow `branding.app_name` too, but only
on a fleet at or past #899 (`f793c6e`, 2026-07-30): from there the web layer
resolves the name server-side per request (via the token-gated `/brand/meta`),
so no web rebuild is needed, and the build-time `NEXT_PUBLIC_APP_NAME` env
survives only as the fallback shown when the backend is unreachable. On an
older fleet the tab title is *only* `NEXT_PUBLIC_APP_NAME`, baked at web build
time and defaulting to "Fleet" — keep setting it there until the deployment
catches up.

### MCP servers — connect agents to your data

This bundle ships two demo servers that show the two shapes you will use for
everything else, and the four connectors of the standard Elcano client stack
(Pages, Lens, Explorer for inbound mail, `ses_outbound` for outbound).

| Server | Gate | Credentials | What it does |
| --- | --- | --- | --- |
| `knowledge_base` | `always: true`, optional and off by default | none | Point an agent at **your own docs.** Searches a bundled JSON handbook (demo content, so no chat gets it unless a user switches it on). |
| `example_api` | `enabled_env: [EXAMPLE_API_KEY]` | brokered host-side | The **gated connector** pattern — listed as **Needs setup** and never launched until the key is set. |
| `pages` | `enabled_env: [PAGES_MCP_URL, PAGES_API_TOKEN]` | token in the header, host-side | Build, publish and refresh dashboards on the deployment's own Pages. Page changes wait for approval; data refreshes (`update_page_data`, `update_page_data_upload`) run at once and post a card (`critical_tool_modes: notify`). |
| `lens` | `enabled_env: [LENS_MCP_URL, LENS_API_TOKEN]` | token in the header, host-side | Classify sites, apps and CTV apps into quality tiers on the deployment's own Lens. Starting or cancelling a job needs approval. |
| `explorer` | `enabled_env: [EXPLORER_MCP_URL, EXPLORER_API_TOKEN]` | token in the header, host-side | The only inbound-mail connector, on the deployment's own Explorer: `search_emails`, `search_emails_fuzzy` and `find_latest_report` find report emails; `get_email`, `extract_download_links` and `list_archived_attachments` read them by `s3_key`; `get_download_link` returns a URL for an attachment or body link that the native `download_url` tool fetches. Report links expire about 48 hours after delivery, so the archive caches them in S3 on arrival and Explorer serves the cached copy. Only mail SES authenticated (DMARC / aligned SPF or DKIM) is returned; `sender_contains` takes sender domains or addresses and matches the authenticated mailbox, not From text. |
| `ses_outbound` | `enabled_env: [SES_OUTBOUND_AWS_ACCESS_KEY_ID, …SECRET_ACCESS_KEY, SES_OUTBOUND_FROM_EMAIL]` | send-only IAM user, host-side | Render and send audited email as the client's sender. Every `send_email` needs approval; unlisted recipients are refused unless `SES_OUTBOUND_ALLOW_UNLISTED_RECIPIENTS=true`. |

The Lens, Explorer and Pages entries default their `url` to a reserved `.invalid` host:
fleet needs a non-empty URL to load the bundle, and an unset one must never
reach another organisation's box. The outbound email host variables are unprefixed
(`SES_OUTBOUND_*`) because one fleet box serves one client; the
production-support email provisioning writes exactly these names.

- **`knowledge_base`** is the canonical "answer from our docs" pattern. It serves
  a small company handbook (`mcp/data/handbook.json`, with People / Engineering /
  Security / Support / Data articles) and exposes tools to search, list
  categories, and read articles. It needs no credentials, so a fresh checkout
  runs clean. Swap the JSON for your content — or rewrite the server to read your
  wiki, your vector store, your ticketing system.
- **`example_api`** is a generic REST connector that does not launch until
  `EXAMPLE_API_KEY` is present in the environment; until then the Tools picker
  lists it as **Needs setup** (search finds it, with no switch). This is the **host-side
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

Every entry also carries a `category:`: the Tools-picker group it is listed
under, an ID from fleet's list (`ssps`, `deal-management`, `data-storage`,
`knowledge-docs`, `misc`, …, plus `house` for your own team's tools, which the
picker lists first under `branding.house_tools_name`). An unknown ID fails
fleet's load, so `mcp/tests/test_connector_copy.py` checks it.

Its write tool, `api_submit_record`, is listed under the manifest's
`critical_tools` — so fleet makes the agent stop and get an **audit confirmation**
before that consequential action runs (with a per-tool approval window from
`critical_tool_timeouts`). The read tools (`api_get_record`,
`api_list_records`, and the `kb_*` tools) are listed under `parallel_safe_tools`,
so fleet may dispatch them concurrently within a single turn.

### Personas — the same engine, different voice and remit

Personas live in `personas/*.yaml`. `FLEET_PERSONA_DEFAULT` (the **file basename** —
`victoria` for `personas/victoria.yaml`, not the display name) selects the
default for new chats and `FLEET_PERSONA` (the path) the one scheduled runs use;
users pick any persona per conversation in the UI.

This bundle ships one, [`victoria.yaml`](personas/victoria.yaml): **Victoria**,
in the starter shape from production-support's fleet-onboarding (mission,
`client_context`, working style, context still to develop). Its context here is
the fictional Northwind; a client fork replaces `client_context` with facts the
client publishes, and records the sources in
`docs/onboarding/solution-engineer-handoff.md`. Internal rules, fees and
approval steps come later, from the client.

The manifest's `personas:` block can add **per-persona tool permissions** — a
least-privilege gate that can only *narrow* what a persona sees, never widen it
(for example `deny: ["mcp:example_api/*"]`). It is empty: Victoria keeps every
permitted tool.

### Quick-start cards (empty state)

The chat home screen shows the cards declared under `empty_state.cards`. This
bundle ships three that span team types: **Summarize a document**, **Analyze a
dataset** (runs Python in the sandbox on an attached CSV), and **Draft
something**. The "Ask the handbook" card is gone because the handbook is demo
content and off by default; its protocol stays for local testing.

### Task templates — pre-filled scheduled tasks

`task_templates:` seeds the Operations Center's "new task from a template"
picker. This bundle ships two that exercise its own content: **Weekly Status
Report** (runs `protocols/weekly-status.md` on a Friday cron, as Victoria, with
an SLA hint) and **Research Brief** (a `{topic}` placeholder the UI prompts for,
with network enabled). Templates pre-fill only form-editable fields — the
security-sensitive knobs (credentials, MCP selection, triggers) are deliberately
not templatable, so a template can never widen a task's authority.

### Protocols — reusable playbooks

Protocols under `protocols/` encode "the way we do this here" once so every
agent — chat or scheduled — runs it the same way. Start with `example.md`
(an annotated template), then see `ask-the-handbook.md` (grounded, cited Q&A),
`research-report.md` (web research into a cited brief), and `weekly-status.md`
(a **scheduled** playbook that gathers inputs, computes honest metrics in Python,
and writes a status artifact to the workspace; `system_prompts/default.md` keeps
scheduled reports succinct by leading with a 1–2 sentence conclusion, avoiding
routine processing narration, and surfacing skipped items or partial inputs as
concise one-line notes).

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

This bundle ships four:

| Skill | What it shows |
| --- | --- |
| `example-skill` | The annotated **template** — frontmatter rules, the three disclosure levels, a `REFERENCE.md`, and a tiny stdlib demo script. Copy it to start your own. |
| `csv-profiler` | A genuinely useful skill with a real **standard-library-only** script that profiles a CSV (row/col counts, per-column type inference, null counts, basic stats) — so it runs even without pandas. |
| `lens-classification` | How to run and read Lens jobs (site, app and CTV quality tiers) through the `lens` connector. |
| `pages-dashboards` | How to build, publish and update dashboards through the `pages` connector. |

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
| `com.elcanotek.fleet` extension | The spec's reverse-domain namespace for client-specific data, here carrying fleet's per-server `tools` allowlist and `fleet mcp test --deep` `probe` for `plugin_notes`. Other clients ignore it; nothing credential-shaped can go there. |

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

A client fork (a **private** `ElcanoTek/<client>-config` created from this
repo's `main`) needs only these edits; everything else ships ready:

1. **Brand strings.** In `manifest.yaml` `branding:`, set `app_name`,
   `login_title`, `login_tagline`, `share_title`, `share_description` from the
   client's own wording, and `SES_TENANT_LABEL` in the `ses_outbound` entry to
   the same name as `app_name` (a test checks).
2. **Palette and email theme.** Run
   `python3 assets/make-palette.py --slug <client> --primary <hex> [--secondary
   <hex> --accent <hex> --ink <hex> --paper <hex> --dark-background <hex>]
   --write` with the hexes from the client's site or brand guide. It rewrites
   `branding.colors`, writes `protocols/email_styles/themes/<client>.yaml`,
   makes it the default theme and recolors the email charts. Note the source of
   the hexes in a comment above `colors:`.
3. **Mark and card.** Replace `assets/northwind-mark.svg` with the client's mark
   (a square PNG of at least 256x256, or an SVG), point `logo` at it, set
   `share_image` to `assets/<client>-share.png`, and run
   `python3 assets/make-share-card.py --host fleet.<client domain>` (needs
   Pillow, in `mcp/requirements.txt`, and `rsvg-convert` for an SVG mark).
   Replace `assets/email-logo.png` with the mark for the report-email header:
   a PNG (Gmail and Outlook do not show SVG) at twice its shown size, so 88px
   tall for the 44px header, legible on the email theme's dark header band
   (use the light version of the mark if the main one is dark). For an SVG,
   `rsvg-convert -h 88 <mark>.svg -o assets/email-logo.png`.
   Delete the Northwind files.
4. **Persona.** Replace `client_context` (and the Northwind wording) in
   `personas/victoria.yaml` with the client's sourced facts; fill in
   `docs/onboarding/solution-engineer-handoff.md` with the pages read. Follow
   production-support's fleet-onboarding starter task.
5. **Names and docs.** `fleet-sandbox-example` → `fleet-sandbox-<client>` in
   `manifest.yaml` and the sandbox workflows; `REPO` and the default directory
   in `install.sh`; the title and intro of this README and `AGENTS.md`; fill in
   `docs/DEPLOYMENT.md` as the deployment goes, and `docs/EMAIL-DEPLOYMENT.md`
   once the email tenant exists.
6. **Check.** `pytest -m 'not expensive'`, `ruff check mcp/ plugins/ assets/`,
   `python3 assets/make-palette.py --check`, and `fleet validate-config` with
   `FLEET_PERSONA_DEFAULT=victoria FLEET_PERSONA=personas/victoria.yaml`,
   built at the fleet revision the box will run.

Do not copy files from another client's bundle: improvements to the shared
connectors land here first, as reviewed PRs, and are hand-ported to existing
client bundles in their own PRs.

**Going further** (any fork, any time):

- **Point the knowledge base at your docs.** Replace `mcp/data/handbook.json`
  with your own content (and switch the connector back on by default), or edit
  `mcp/knowledge_base.py` to read your wiki, database, or vector store.
- **Add your MCP servers.** Drop a Python server under `mcp/`, add an entry to
  `mcp_servers[]` with the right enable gate (`always`, or `enabled_env` for a
  credential-gated connector), and name its credential *variables* (never
  values) — fleet brokers the secret host-side. See
  [`mcp/README.md`](mcp/README.md) for the server-authoring guide and tests.
  For scheduled inputs, map your connector's input-directory variable to
  `${FLEET_WORKSPACE}/inputs`; map an attribution variable to
  `${FLEET_TASK_ID}` when the remote system records the originating task.
  Do not put real domains, account IDs, bucket names, or credentials into this
  template.
- **Govern new tools.** List read-only tools in `agent_policy.parallel_safe_tools`
  and writes / consequential actions in `critical_tools` so they require an
  audit gate before running.
- **Write prompts, protocols, and skills.** Copy an existing `prompts/*.yaml` or
  `protocols/*.md` and adapt it to your team. Git-backed prompt files appear
  directly in the shared Prompt Library; UI-authored prompts cover teammates who
  do not use Git. For a capability that ships code or reference files, copy the
  `skills/example-skill/` folder (the annotated template) and replace its body
  and `scripts/`.
- **Tune the sandbox.** Add packages your agents need to `sandbox/Containerfile`.
  The base tracks `fedora-minimal:latest`; pin a digest there if you want
  reproducible builds.
- **Capture goldens and gate regressions.** Once a scheduled task or a
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

### Provisioning baseline (what the reference client deployments run)

The *mechanics* of deploying — sizing rationale, bootstrap flags, the
`--force-caddy` stock-Caddyfile stop, backup timers — live in fleet's
[docs/DEPLOYMENT.md](https://github.com/ElcanoTek/fleet/blob/main/docs/DEPLOYMENT.md);
this section only records the **defaults our client deployments actually run**
(as of 2026-09), so the next deploy starts from a known-good point and deviates
deliberately. Every line is a baseline, not a rule — adjust per client, and
record what a client changed (and why) in *that client's* bundle repo. Client
specifics — names, domains, IPs — stay out of this public template.

- **Box**: Vultr **dedicated** Optimized Cloud Compute, General Purpose —
  8 vCPU / 32 GB / 160 GB NVMe (`voc-g-8c-32gb-160s`, ~$240/mo), **Fedora**
  (bootstrap assumes `dnf`), public IPv4 **+ IPv6**, hostname/label = the
  box's FQDN (`fleet.<domain>`). **Automatic backups: enabled** (+20%) — whole-instance
  restore on top of the on-box daily DB dump, which is same-host and covers
  logical loss only. No VPC, no provider firewall group (on-box firewalld is
  the firewall), no DDoS add-on. **Startup script: `elcano-key-only-ssh`**,
  which turns SSH password login off at first boot (bootstrap repeats it as a
  safety net). Select every team SSH key at creation, including the ops box's
  — the deploy is driven from it.
- **DNS**: Cloudflare `A` + `AAAA` for `fleet.<domain>`, **DNS-only (grey
  cloud, never proxied)** — Caddy does its own Let's Encrypt issuance, which a
  proxy breaks. Verify against the zone's own nameservers before
  bootstrapping; your resolver may cache the pre-record NXDOMAIN.
- **Per-client credentials, never shared across clients** — separate spend,
  separate blast radius, separate revocation:
  - **Read-only SSH deploy key** for the bundle repo: the box generates its
    own key (`/root/.ssh/id_ed25519`) and it is registered on that client's
    bundle repo only, read-only, titled with the box's domain so a key audit
    knows what breaks on removal
    (`gh repo deploy-key add - -R ElcanoTek/<client>-config -t "fleet.<domain> (read-only)"`;
    production-support's `scripts/deploy-box.sh` prints this command). The box
    clones `git@github.com:ElcanoTek/<client>-config.git`. Never put a GitHub
    personal access token in `.git/config`, `/root/.git-credentials` or
    `/root/.config/gh/hosts.yml`.
  - **OpenRouter key**: one fresh key per client with a **$2,000/month spend
    limit** set at creation — the cap turns a runaway loop or leaked key into
    a bounded incident; size it to the client's expected usage. Set on the box
    with `fleet config set-openrouter-key` (fleet exits by design until it
    exists, and `fleet-web` dependency-fails alongside — expected pre-key
    state, not a broken deploy).
- **First-boot env**: `FLEET_PERSONA_DEFAULT=victoria`,
  `FLEET_PERSONA=personas/victoria.yaml` and
  `FLEET_SANDBOX_MEMORY=2g` (pandas-sized jobs are OOM-killed at the 512 MiB
  default) in `fleet.env`; `NEXT_PUBLIC_APP_NAME=<Client>` in `fleet-web.env`
  (see the knob above).
- **Verify before declaring it live**: `fleet doctor` at 0 advisories
  (including the sandbox smoke test), `fleet mcp test --all`, a valid cert on
  the domain, and `/api/brand/logo` + `/api/brand/share-image` **byte-identical**
  to the bundle's asset files — that last check is what proves the box serves
  *your* bundle rather than fleet's defaults. Trap: `fleet validate-config`
  only vouches for the binary it was built from; validate with the rev the box
  will boot.
- **Harden**: remove Fedora's default-open `cockpit` from firewalld — end
  state `ssh http https` only. If the bundle's share card carries a
  placeholder hostname, regenerate it with
  `assets/make-share-card.py --host fleet.<domain>`.

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

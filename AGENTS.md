# AGENTS.md

Operating guide for AI coding agents working in **example-config**. It follows the
[agents.md](https://agents.md) convention; Claude Code and every other
`AGENTS.md`-aware tool read this file directly. Do not add a `CLAUDE.md` or `CLAUDE.local.md` here (or above the checkout): Claude Code then loads those instead and skips this file.

Humans, start with [`README.md`](README.md).

## What this repo is

The public **fork-this-to-start** client bundle for
[`fleet`](https://github.com/ElcanoTek/fleet). fleet is a client-agnostic engine
that ships no customer content and loads a bundle at boot
(`FLEET_CLIENT_CONFIG_DIR`). This repo is that bundle, branded for a fictional
company — **Northwind** — with a default persona named **Victoria**: branding,
model defaults, the MCP catalog (`manifest.yaml`), system prompts, the persona,
protocols, skills, and the Python MCP servers under `mcp/`.

It is also the starting point for every Elcano client bundle. Besides the two
demo servers it ships the standard client stack's connectors (Pages, Lens,
inbound `email` and `ses_outbound`), each dark until its host env is set, and
two brand helpers (`assets/make-palette.py`, `assets/make-share-card.py`). A
client fork changes branding, persona and docs and nothing else to start; see
the README's "Make it yours".

Nothing here is secret, and no real client appears anywhere. Northwind is a
placeholder.

**Bundles are data; fleet is engine.** If something a forking team needs can't be
expressed here, the fix is usually to extend fleet's bundle schema — not to
special-case a customer in fleet, and not to special-case Northwind either.

## This bundle owns `mcp/`

**Edit `mcp/` here. It is the source of truth. There is no upstream.**

This template was never a mirror of another repo's servers. The two demo
servers (`knowledge_base.py`, `example_api.py`) and the example Agent Plugin's
server (`plugins/example-plugin/server/plugin_notes.py`) were written here. The
email servers (`ses_s3_email.py`, `ses_sender.py` and their helpers) were
hand-ported from a client bundle once, in 2026-10, as a reviewed change; this is
their home now, and client bundles receive fixes from here by hand. Keep it
that way.

- **MUST** make MCP server changes here, as normal reviewed PRs with tests.
- **MUST NOT** introduce an automated sync between this bundle and any other
  repo — not another server repo, not a sibling client bundle, not a "generate
  the examples from production" mirror. Those mirrors revert reviewed fixes: a
  sync once silently undid an email fix and every `send_email` on the box
  answered `202 duplicate_suppressed` for a day.
- **MUST NOT** stamp `Synced-From:` commits or tell a reader to "fix it
  upstream and re-sync."
- Client bundles that forked this tree are
  **peers**, not downstreams. A fix that matters to them is hand-ported there,
  in its own PR, by someone who has looked at that bundle's tests. Do not
  assume their `mcp/` still matches this one.

## Build · test · lint

```sh
python3 -m venv .venv
.venv/bin/pip install -r mcp/requirements.txt pytest

.venv/bin/python -m pytest mcp/ -m 'not expensive' -q   # test suite (incl. the plugin's server)
.venv/bin/python -m ruff check mcp/ plugins/ assets/    # lint
.venv/bin/python assets/make-palette.py --check          # palette contrast
```

`pytest.ini` sets `testpaths = mcp mcp/tests`; `mcp/tests/test_plugin_notes.py`
loads the plugin's server by path, since a plugin must stay self-contained under
`plugins/`. The `expensive` marker gates
tests that spend real API money — run those by hand with `-m expensive`, never
in a batch.

The MCP SDK pin is `mcp>=1.28.1,<2`. The `<2` ceiling is load-bearing: 2.0
drops `mcp.server.fastmcp`, which both servers import, so an uncapped pin
resolves to a release on which neither server starts.

When you change a server's env vars or tool names, **update `manifest.yaml` in
the same PR**. A tool renamed in code but still allowlisted in the manifest is
dropped silently by fleet at boot, and an env key the code reads but the
manifest never provides means the subprocess never receives it.

## Invariants

- **`manifest.yaml` is the complete contract** for every server: env keys, tool
  allowlist, `critical_tools`. A server reads plain env vars and stays
  customer-agnostic; per-customer identity arrives only through manifest env.
- **Credentials are never values in this repo.** Manifests name *variables*.
  fleet brokers the secrets host-side; they never enter a server's source, a
  test fixture, or a log line. The example connector stays off (listed as "Needs setup" in the Tools picker) until
  `EXAMPLE_API_KEY` is set in the host environment.
- **Keep Northwind generic.** Do not drop a real customer's name, seat, account
  id, or mailbox into fixtures, docs, handbook articles, or example prompts.
  Forks that become real client bundles scrub that themselves; the template
  must not be the place it is introduced.
- **Every write tool is audit-gated** through fleet's approval flow via
  `agent_policy.critical_tools`. Adding a write tool means adding it there in
  the same PR.
- **Every connector carries catalog copy.** `display_name` and `description`
  on each `mcp_servers` entry are the only text a user reads in chat's Tools
  picker and on Settings -> Connections before switching a connector on, and
  fleet renders nothing where a description is missing — a blank row reads as
  a broken connector. Write them in fleet's house style (its
  `docs/MCP-CATALOG.md`, "Connector copy"): a vendor-cased display name with
  no plumbing words, then one imperative capability sentence and no setup
  text: fleet lists a connector whose `enabled_env`/`enabled_groups` are unset
  as "Needs setup", so no "Appears once ... is set" clause and no env var
  names. Each entry also names its Tools-picker group with `category:`, an ID
  from fleet's list (`house` for Elcano's own tools, shown first as "Elcano
  Tools"; `ssps`, `deal-management`, `data-storage`, `misc`, …); fleet fails
  the load on an unknown one. fleet only WARNS on a copy gap, so
  `mcp/tests/test_connector_copy.py` is the gate.
- **Honest docs.** If you change behavior, change the README / `mcp/README.md`
  / `docs/` in the same PR.
- **The persona needs both variables.** `FLEET_PERSONA_DEFAULT` is a file
  basename (`victoria`), not the `name:` field; `FLEET_PERSONA` is the path
  (`personas/victoria.yaml`). fleet's stock default for both is `assistant`,
  which this bundle does not ship; `bundle-preflight.yml` passes the same
  values through `bundle_env`.
- **Standard connectors stay dark and generic.** Pages and Lens keep their
  reserved `.invalid` default URLs; `email` and `ses_outbound` read unprefixed
  host variables (`EMAIL_*`, `SES_OUTBOUND_*`, the names production-support's
  email provisioning writes), carry no `account_vars` (the named-account overlay
  would not isolate their shared `AWS_*` keys), and default to refusing
  unlisted recipients. Fixtures use `example.com` / `.example` addresses only:
  no real sender, customer, vendor account or tracking host.
- **Colors stay contrast-checked.** Change `branding.colors` with
  `assets/make-palette.py`, or rerun its `--check` (the tests do) after a hand
  edit; the email theme and `chart_colors` must follow the same palette.

## Where to look

- **Bundle contract, install, and the "make it yours" checklist:**
  [`README.md`](README.md)
- **Running the whole bundle in a local coding agent:**
  [`docs/TESTING-LOCALLY.md`](docs/TESTING-LOCALLY.md)
- **Registering these servers in your own agent:** [`INSTALL.md`](INSTALL.md)
- **Authoring and testing the Python MCP servers:** [`mcp/README.md`](mcp/README.md)
- **Setting up a client's SES/S3 email tenant:** the
  [new-client email-report runbook](https://github.com/ElcanoTek/production-support/blob/main/ses-s3/docs/NEW-CLIENT-EMAIL-SETUP.md);
  the connectors are already here, and real deployment details go only in the
  forked client bundle's `docs/EMAIL-DEPLOYMENT.md`.

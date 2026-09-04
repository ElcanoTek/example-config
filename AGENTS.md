# AGENTS.md

Operating guide for AI coding agents working in **example-config**. It follows the
[agents.md](https://agents.md) convention; `CLAUDE.md` is a symlink to this file so
Claude Code and any `AGENTS.md`-aware tool read the same instructions.

Humans, start with [`README.md`](README.md).

## What this repo is

The public **fork-this-to-start** client bundle for
[`fleet`](https://github.com/ElcanoTek/fleet). fleet is a client-agnostic engine
that ships no customer content and loads a bundle at boot
(`FLEET_CLIENT_CONFIG_DIR`). This repo is that bundle, branded for a fictional
company — **Northwind** — with a default persona named **Aria**: branding, model
defaults, the MCP catalog (`manifest.yaml`), system prompts, personas,
protocols, skills, and the two example Python MCP servers under `mcp/`.

Nothing here is industry-specific and nothing here is secret. Northwind is a
placeholder. The servers, personas, and protocols exist to show the *shape*.

**Bundles are data; fleet is engine.** If something a forking team needs can't be
expressed here, the fix is usually to extend fleet's bundle schema — not to
special-case a customer in fleet, and not to special-case Northwind either.

## This bundle owns `mcp/`

**Edit `mcp/` here. It is the source of truth. There is no upstream.**

This template was never a cutlass SSP mirror. The two servers
(`knowledge_base.py`, `example_api.py`) were written here, and so was the
example Agent Plugin's server (`plugins/example-plugin/server/plugin_notes.py`).
Keep it that way.

- **MUST** make MCP server changes here, as normal reviewed PRs with tests.
- **MUST NOT** introduce an automated sync between this bundle and any other
  repo — not cutlass, not a sibling client bundle, not a "generate the examples
  from production" mirror. Those mirrors revert reviewed fixes. See
  elcano-config #48 / #75: a sync silently undid an email fix and every
  `send_email` on the box answered `202 duplicate_suppressed` for a day.
- **MUST NOT** stamp `Synced-From:` commits or tell a reader to "fix it
  upstream and re-sync."
- Client bundles that forked this tree (`omnicom-config`, and others) are
  **peers**, not downstreams. A fix that matters to them is hand-ported there,
  in its own PR, by someone who has looked at that bundle's tests. Do not
  assume their `mcp/` still matches this one.

## Build · test · lint

```sh
python3 -m venv .venv
.venv/bin/pip install -r mcp/requirements.txt pytest

.venv/bin/python -m pytest mcp/ -m 'not expensive' -q   # test suite (incl. the plugin's server)
.venv/bin/python -m ruff check mcp/ plugins/            # lint
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
  test fixture, or a log line. The example connector stays dark until
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
  no plumbing words, then one imperative capability sentence and — only for a
  gated connector — a clause naming the real `enabled_env`/`enabled_groups`
  vars. fleet only WARNS on a gap, so `mcp/tests/test_connector_copy.py` is
  the gate.
- **Honest docs.** If you change behavior, change the README / `mcp/README.md`
  / `docs/` in the same PR.
- **`PERSONA_DEFAULT` is a file basename**, not the `name:` field inside the
  YAML. The shipped default is `assistant` (display name Aria).

## Where to look

- **Bundle contract, install, and the "make it yours" checklist:**
  [`README.md`](README.md)
- **Running the whole bundle in a local coding agent:**
  [`docs/TESTING-LOCALLY.md`](docs/TESTING-LOCALLY.md)
- **Registering these servers in your own agent:** [`INSTALL.md`](INSTALL.md)
- **Authoring and testing the Python MCP servers:** [`mcp/README.md`](mcp/README.md)
- **Adding a bundle-managed SES/S3 email-report pipeline:** use the external
  canonical [new-client email-report runbook](https://github.com/ElcanoTek/ses-s3-setup/blob/main/docs/NEW-CLIENT-EMAIL-SETUP.md),
  then keep real deployment details only in the forked client bundle.

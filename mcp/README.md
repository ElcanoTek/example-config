# MCP servers

This directory holds the bundle's MCP (Model Context Protocol) servers, wired
into the agent catalog in `manifest.yaml`. Two are examples that show the two
shapes every fleet MCP server takes; two are the standard client stack's email
connectors, dark until their host env is set:

| Server | File | Shape | Credentials |
| --- | --- | --- | --- |
| Demo Handbook | `knowledge_base.py` | always registered, off by default (demo content) | none |
| Example REST API | `example_api.py` | credential-gated | `EXAMPLE_API_KEY` |
| Email Reports (`email`) | `ses_s3_email.py` | credential-gated, read-only | `EMAIL_S3_BUCKET` (+ `EMAIL_AWS_*` keys or an instance role) |
| Amazon SES Email (`ses_outbound`) | `ses_sender.py` (+ `email_render.py`, `email_lint.py`) | credential-gated, every send approval-gated | `SES_OUTBOUND_AWS_*`, `SES_OUTBOUND_FROM_EMAIL` |

All are stdio servers built on `mcp.server.fastmcp.FastMCP`. fleet launches
each as a host-side subprocess with only the environment its manifest entry
maps (never the whole host env, and never inside the agent's command sandbox); `utils/` holds their shared run ledger, URL guard and
filesystem helpers. The run workspace is shared with the sandbox, so the
outbound sender (`ses_sender.py`) reads attachments and content files and writes
rendered email only through `utils/safe_open.py`: its configured roots are
pinned (`O_NOFOLLOW|O_DIRECTORY`, by device and inode) when the server starts,
before any tool call, and never re-resolved; each path is then one
symlink-refusing walk from its root and that descriptor (never the name a
second time), and files it created are removed only through the helper. The
inbound server (`ses_s3_email.py`) does not use `safe_open`: it confines a
model-supplied `output_dir` to its own allowed roots (`resolve_download_dir`),
walks to it without following a symlink and creates each download fresh
(`O_CREAT|O_EXCL|O_NOFOLLOW`), but its roots are resolved per call, not pinned. Lens and Pages are remote (http) servers on the client's
own boxes, so they have manifest entries but no code here.

To stand up a client's SES/S3 email tenant (infrastructure, least-privilege
runtime access, the fleet host variables, historical migration, validation),
use the canonical
[new-client email-report runbook](https://github.com/ElcanoTek/production-support/blob/main/ses-s3/docs/NEW-CLIENT-EMAIL-SETUP.md).
Record real identifiers in the forked client bundle's `docs/EMAIL-DEPLOYMENT.md`,
not in this generic template.

## `knowledge_base.py` — always-on, no credentials

A tiny "team knowledge base" over a bundled JSON handbook
(`data/handbook.json`). Because it needs no secrets, fleet starts it the moment
it boots. This is the canonical "point an agent at your own docs" pattern.

Tools (the function names match the manifest's `agent_policy`):

- `kb_list_categories()` -> list of `{category, article_count}`.
- `kb_search(query, limit=5)` -> ranked `[{id, title, category, snippet,
  score}]` using a simple, dependency-free keyword/substring scorer over the
  title, body, tags, and category (title and tag hits are weighted higher).
- `kb_get_article(article_id)` -> the full `{id, title, category, tags, body}`,
  or a structured `{"error": ...}` if the id is unknown.

If `data/handbook.json` is missing or invalid the server still starts; every
tool returns a clear error so the model can recover.

## `example_api.py` — credential-gated REST connector

A generic REST connector over `httpx`. It does not launch until
`EXAMPLE_API_KEY` is set; meanwhile the Tools picker lists it as **Needs setup**
(search finds it, with no switch), so a fresh checkout runs clean with no
secrets. fleet brokers the real key host-side and injects it only for the
duration of a delegated call — it never enters the model's context or the
sandbox.

Configuration (read from the environment, never logged):

- `EXAMPLE_API_KEY` — required; sent as `Authorization: Bearer <key>`.
- `EXAMPLE_API_BASE_URL` — optional; defaults to `https://api.example.com/v1`.
- `EXAMPLE_API_TIMEOUT_SECONDS` — optional; request timeout, defaults to 30.
- `EXAMPLE_API_OUTPUT_DIR` — optional; a writable directory where each
  successful `api_submit_record` drops a JSON receipt. The manifest maps it to
  the reserved `${FLEET_WORKSPACE}` runtime token — fleet substitutes a
  workspace directory at subprocess launch and **drops the key** on spawns
  with no workspace to offer, so the server treats it as optional (unset,
  empty, or an unexpanded `${...}` value all disable receipts).

Tools:

- `api_list_records(resource, limit=20)` -> `GET {base_url}/{resource}`.
- `api_get_record(resource, record_id)` -> `GET {base_url}/{resource}/{id}`.
- `api_submit_record(resource, payload)` -> `POST {base_url}/{resource}`. This
  is a **write**: the manifest marks any tool whose name ends in
  `submit_record` as `critical`, so fleet holds it for an audit confirmation
  before it runs. On success it also writes a local JSON receipt when
  `EXAMPLE_API_OUTPUT_DIR` is configured (see above).

Each tool returns `{"success": True, "data": <parsed JSON>}` or a structured
`{"success": False, "error": ...}` — it never raises.

## Running the tests

The tests call the tool functions directly (no live MCP transport). `httpx` is
mocked with `respx`, so no network call is ever made.

```bash
# from this mcp/ directory
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

pytest
```

You should see all tests pass. `tests/conftest.py` puts this directory on
`sys.path` so `import knowledge_base` / `import example_api` work from anywhere.

## Pointing `example_api` at a real API

The default base URL is a placeholder and nothing real is contacted until you
supply your own values. To use a real JSON REST API:

1. Set `EXAMPLE_API_KEY` in the environment fleet runs under (the manifest's
   `enabled_env` gate turns the server on once it is present). Set
   `EXAMPLE_API_BASE_URL` to your API root, e.g. `https://api.acme.com/v1`.
2. Call the tools with `resource` set to your collection names (for example
   `tickets`, `orders`, `contacts`).

If your API uses a different auth scheme or response envelope, edit `_headers()`
and `_request()` in `example_api.py` — those two helpers are the only places
the HTTP details live.

## Swapping the handbook for your own docs

The knowledge base reads `data/handbook.json`. Replace it with your content
using the same per-article shape:

```json
{
  "articles": [
    {
      "id": "kebab-case-id",
      "title": "Article title",
      "category": "Engineering",
      "tags": ["searchable", "keywords"],
      "body": "One or more short paragraphs of text."
    }
  ]
}
```

A bare top-level list of articles is also accepted. To load a file from a
different location without editing code, set `HANDBOOK_PATH` to its absolute
path. For anything beyond a flat JSON file (a wiki export, a vector store),
replace the `_load_articles` loader and the `kb_search` scorer — the tool
signatures can stay the same so the manifest and personas do not change.

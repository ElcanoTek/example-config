---
name: lens-classification
description: How to run Lens from chat or a scheduled task — classify a list of websites, iOS/Android apps or CTV apps into ad-quality tiers (Premium / Standard / Long Tail / Failed) with IAB categories, then read and download the results. Use whenever someone asks to classify, score, vet, tier or categorise domains, sites, apps, bundles or a supply/allow list; asks "what's the quality of these sites"; wants custom yes/no or multiple-choice labels on a list; or asks about a Lens job, its progress or its output. Covers choosing how to send the list (inline vs a workspace file), polling without duplicating jobs, reading the summary, pulling the CSV into the workspace, custom categories, and what the errors mean.
---

# Lens classification

Lens is this deployment's classification service. The `lens` connector's tools
are `mcp_lens_*`. A job runs in the background on the Lens host: it takes about
a minute for a handful of domains and can take hours for tens of thousands, and
it spends OpenRouter credit. The same jobs show in the Lens dashboard, where
yours are tagged `via API: fleet-…`.

## 1. Start the job — once

Call `mcp_lens_create_job` with exactly one input:

| The list is… | Pass |
|---|---|
| A few to ~10,000 identifiers in the conversation | `items: ["example.com", "1234567890", "com.example.app", …]` |
| A CSV/XLSX in the workspace (up to 2 MiB) | `content_base64` as a **workspace file reference** (`{"workspace_file": "list.csv", "sha256": "<whole-file sha256>", "offset": 0, "length": <size>}`) plus `filename: "list.csv"`. Never paste the bytes. Get the hash and size with `sha256sum list.csv; stat -c %s list.csv` |
| Already uploaded to Lens, or bigger than 2 MiB | `input_file: "<name>"` (see `mcp_lens_list_input_files`). For a large file, ask the user to upload it in the Lens dashboard first |

- Lens finds the identifier column on its own (`Domain`, `url`, `pageURL`,
  `app_id`, …) and recognises CTV lists by their `Bundle ID` and app-name
  columns. CTV lists must go as a file, not `items`.
- Give the job a `name` the user will recognise in the dashboard.
- Keep the defaults. Only set `llm_model`, `research_model`, `deep_retry: false`
  (faster on big lists, fewer rescued sites) or `research_fallback: false` when
  the user asks. `mcp_lens_get_options` lists the choices.
- For a very large list, tell the user roughly how many rows there are before
  starting, because it costs money.
- **Never start a second job for the same list** while one is queued or
  running. If unsure, check `mcp_lens_list_jobs` first.

In chat, `create_job` waits for the user to approve it on a card, because the
spend can't be undone. Say what you are about to run (list size, options)
before calling it. Scheduled runs pass the usual audit gate instead.
`mcp_lens_cancel_job(job_id)` also needs approval; it stops further work but
refunds nothing already spent.

## 2. Wait for it

`mcp_lens_get_job(job_id)` returns `status`, `progress` (processed / total),
`eta_seconds`, `cost_usd`, `queue_position` and a `next_step`. Nothing blocks,
so:

- **Chat:** report that it started, with its size and the ETA once known, and
  offer to check back. If the user wants to wait, poll every minute or two with
  `sleep 60` in bash between checks. Don't poll in a tight loop.
- **Scheduled task:** poll with `sleep` between checks (start at 60 s, back off
  to 5 min), up to a sensible bound for the list size. If it is still running
  at the bound, report the job id and its progress rather than starting it
  again.
- `failed`: read `error` and `log_tail`, tell the user plainly, and only re-run
  if they want to.

## 3. Read and deliver the results

- `mcp_lens_get_job_results(job_id, limit=0)` returns summary counts over the
  whole output: by `Quality`, by `Type`, and the top IAB tier 1 categories.
  Lead with that.
- Page rows with `offset` / `limit` (max 200), and filter with
  `quality: "Premium"` or `columns: ["Domain", "Quality", "IAB Tier 1", "Justification"]`.
  This also works mid-run, on the rows written so far.
- For the whole file, call `mcp_lens_get_download_link(job_id, file="output")`,
  then `download_url(url)`. The CSV lands in the workspace for pandas, email
  attachments or Pages, and never passes through the model. Links expire in 15
  minutes, so fetch straight away. `file` can also be `log`, `input` or
  `categories`.
- Output columns include Quality, Justification, IAB Tier 1–3, Description,
  Language, Political_Leaning, Audience_Size, Bot_Protection, Scrape_Mode
  (`research` means classified from web research because the site blocked
  crawling) and Cost_USD.
- `Failed` rows are sites that couldn't be reached or researched. Say how many
  there are rather than hiding them.

## Custom categories

For extra per-row questions, pass
`custom_categories: {"categories": [{"name": "Brand safe", "type": "boolean", "question": "…"}, {"name": "Purpose", "type": "choice", "question": "…", "options": ["News", "Shopping", "Other", "Unknown"]}]}`.
The limit is 12 categories. Write each question so it can be answered from the
site's own content, and include an "Unknown" option for choice questions. Each
category adds output columns.

## Errors

Tool errors come back as `{"ok": false, "code": …, "error": …}`. The `error`
text says what to do; relay it rather than guessing.

| code | Meaning |
|---|---|
| `unauthorized` | The token is wrong or revoked. An admin must mint a new one on the Lens host (`lens token create`) and update `LENS_API_TOKEN` |
| `no_identifiers` | The file has no recognisable domain, app or CTV column |
| `too_many_items` | Send the list as a file instead of `items` |
| `invalid_model` | Not offered by this Lens; check `get_options` |
| `job_not_found` | Wrong id, or the job was deleted in the dashboard |
| `output_missing` / `file_missing` | The job finished without that file. Report it; don't retry blindly |

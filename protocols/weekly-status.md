# Weekly Status Protocol

A **scheduled-agent** playbook: on a recurring cadence, gather a week's worth of
raw inputs (a CSV, a log, a notes file), compute a few honest metrics in Python,
and write a concise weekly status artifact to the conversation workspace. Built
to run unattended on the scheduler, but equally runnable on demand in chat.

## Usage

```
Run the weekly status protocol over <input file(s)> for the week ending <date>
```

**Example:**
```
Run the weekly status protocol over workspace/tickets.csv for the week ending 2026-06-21
```

When run by the scheduler, the same instruction is the task text and the input
files are whatever has been placed in the workspace for this conversation.

## Inputs

| Parameter   | Source                  | Description                                                       |
|-------------|-------------------------|-------------------------------------------------------------------|
| input_files | User text / workspace   | The CSV, log, or notes file(s) to summarize.                      |
| week_ending | User text / default     | Last day of the reporting week. Default: most recent Sunday.      |
| lookback    | Default: 7 days         | Window the report covers, ending on `week_ending`.                |
| prior_report| Workspace (optional)    | Last run's artifact, used for week-over-week deltas if present.   |

## Step 1 — Locate and load inputs

- Identify the input file(s) from the task text, or list the workspace and pick
  the obvious source (e.g. the most recent `*.csv` / `*.log` / notes file).
- Load each with `run_python` in the sandbox (pandas for tabular data; plain
  parsing for logs/notes). Don't assume a schema — inspect columns, dtypes, and
  row count first.
- If the expected input is missing or empty, **stop and report that** in the
  artifact rather than emitting an empty or fabricated status.

## Step 2 — Clean and scope to the week

- Parse timestamps and filter rows to the `lookback` window ending on
  `week_ending`. Note rows dropped as out-of-window.
- Drop obvious non-data rows (totals, blank lines, headers repeated mid-file) and
  record how many you dropped.
- Note any data-quality issues you hit (missing values, malformed dates,
  duplicates) — these go in a Data Notes section, not silently swallowed.

## Step 3 — Compute metrics in Python

Compute a small, honest set of metrics — actually run the numbers; never
eyeball or estimate them. Pick what fits the data; for example:

- **Volume**: counts this week (rows, events, tickets, commits, …).
- **Throughput / rate**: completed vs. opened, pass/fail, on-time vs. late.
- **Distribution**: top categories, owners, or sources by count or value.
- **Trend**: week-over-week delta vs. `prior_report` if available, else absolute.
- **Outliers / flags**: notable spikes, dips, or stuck items worth a human's eye.

Use sums and counts for rollups; when deriving a rate, divide summed numerators
by summed denominators (ratio-of-sums) rather than averaging per-row rates.
Round for display only — keep full precision in the computation.

## Step 4 — Write the artifact to the workspace

Write a single Markdown file to the workspace, e.g.
`workspace/weekly-status-<week_ending>.md`, with these sections:

1. **TL;DR** — 2–4 sentences: the headline, the one number that matters, and any
   flag a reader must not miss.
2. **Metrics** — a compact table of the Step 3 figures, with week-over-week
   deltas where available.
3. **Highlights & flags** — a short bullet list of what moved and what needs
   attention. Use "observed" / "likely contributor" language; don't assert causes
   the data can't prove.
4. **Data notes** — input file(s) used, window covered, rows included vs.
   dropped, and any quality caveats.

Keep it concise — a status someone reads in under a minute. If a `SendMessage`
or notification channel is configured for scheduled runs, post the TL;DR with a
pointer to the artifact; otherwise the workspace file is the deliverable.

## Validation

Before finishing, verify:

- Every figure in the artifact came from a `run_python` computation over the
  actual input — nothing estimated, nothing carried over from a prior week by hand.
- Table totals reconcile to the source row counts (account for documented drops).
- Deltas are computed against a real prior report; if none exists, that's stated,
  not faked.
- Causal language is hedged appropriately; flags are observations, not verdicts.
- If inputs were missing, empty, or partial, the artifact says so up front and is
  marked accordingly rather than presenting a confident-but-hollow status.

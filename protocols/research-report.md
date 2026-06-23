# Research Report Protocol

Research a question across the open web and any attached sources, then produce a
structured, **cited** brief. The goal is a decision-ready answer a reader can
trust — every non-obvious claim traceable to a source — not a wall of links.

## Usage

```
Run the research report protocol on: <question>
```

**Example:**
```
Run the research report protocol on: what are the tradeoffs between rootless and
rootful containers for running untrusted code? Use the two PDFs I attached plus
current web sources.
```

## Inputs

| Parameter        | Source            | Description                                                        |
|------------------|-------------------|--------------------------------------------------------------------|
| question         | User task text    | The research question. Restate it as a focused, answerable scope.  |
| attached_sources | Attachments       | Files (PDF, docs, CSV, notes) the user wants treated as evidence.  |
| depth            | Default: standard | `quick` (≈3–5 sources), `standard` (≈6–12), or `deep` (12+).       |
| audience         | Default: general  | Who the brief is for — tunes vocabulary and how much to assume.    |

## Step 1 — Frame the question

- Restate the question in one sentence and define its scope: what's in, what's
  out, and the time window that matters (e.g. "as of 2026", "last 2 years").
- Break it into 3–6 sub-questions whose answers, together, answer the whole.
  These become your search agenda and, later, the section outline.
- Note any assumptions you're making to keep moving, so the user can correct them.

## Step 2 — Gather evidence

- **Attached sources first.** Read every attachment before searching the web —
  it's the user's own context and usually the highest-value evidence. Use
  `run_python` in the sandbox to extract text/tables from PDFs and spreadsheets.
- **Then the web.** For each sub-question, run `WebSearch`, then `WebFetch` the
  most promising results to read the actual content — never cite a result from
  its search snippet alone.
- **Prefer primary and authoritative sources**: official docs, standards,
  filings, peer-reviewed work, and reputable reporting over aggregators and SEO
  content. Note each source's date; flag anything stale for a time-sensitive
  question.
- If the `example_api` connector is enabled and relevant, pull structured records
  from it as an additional evidence stream.
- Keep a running source list as you go: for each, capture a short id, the title,
  the URL or filename, the publisher, the date, and the one or two facts you're
  taking from it.

## Step 3 — Cross-check and resolve conflicts

- Corroborate any load-bearing claim with a second independent source. A single
  source for a pivotal fact is a flag, not a conclusion.
- When sources disagree, say so explicitly and weigh them (recency, authority,
  methodology) rather than silently picking one.
- Separate **fact** (sourced) from **inference** (your reasoning over the facts)
  from **opinion** (a source's view). Don't let one masquerade as another.
- Drop sources that don't survive scrutiny instead of padding the brief with them.

## Step 4 — Write the brief

Structure (omit a section only if it would be empty):

1. **Answer up front** — 2–4 sentences that directly answer the question. A
   reader who stops here should still get the gist.
2. **Key findings** — a tight bullet list, each bullet a claim with an inline
   citation marker (`[S1]`, `[S2]`, …).
3. **Detail by sub-question** — one short section per sub-question from Step 1,
   with reasoning and inline citations. Use tables for anything comparative.
4. **Caveats & open questions** — what's uncertain, contested, time-sensitive,
   or simply not answerable from the evidence found.
5. **Sources** — a numbered list keyed to the `[S#]` markers: title, publisher,
   date, and URL or filename.

Keep it as short as it can be while still complete; match depth to the `depth`
input and vocabulary to the `audience`.

## Validation

Before delivering, verify:

- Every `[S#]` marker resolves to a real entry in the Sources list, and every
  source listed is actually used in the body.
- No fabricated sources, quotes, figures, or URLs. If you couldn't verify a
  source's content with `WebFetch`, don't cite it.
- Every load-bearing claim is either corroborated or explicitly flagged as
  single-sourced or uncertain.
- The "Answer up front" section genuinely answers the original question.
- Where the evidence doesn't support a confident answer, the brief says so plainly
  rather than overstating.

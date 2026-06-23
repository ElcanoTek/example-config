---
name: csv-profiler
description: Profile a CSV or other delimited file with the Python standard library only — row/column counts, per-column type inference (integer/float/boolean/string), null counts and fill rates, and basic stats (min/max/mean/median/stdev for numerics; distinct count and most-common value for the rest). Use for a fast, dependency-free first look at a dataset before deeper analysis, or whenever pandas is unavailable.
---

# CSV Profiler

A quick, deterministic first look at a delimited dataset — **before** you reach
for pandas or write bespoke analysis. It answers the questions you ask of every
new CSV: how many rows and columns, what type is each column, how many values
are missing, and what is the shape of the numbers. It runs anywhere Python 3
does because it uses **only the standard library** (`csv`, `statistics`), so it
works even when the sandbox has no data-science stack installed.

## When to use

- You just received a CSV (or TSV) and want to understand its structure before
  analyzing it.
- You want honest null counts and per-column types without assuming pandas is
  present.
- You need a reproducible profile to paste into a report or to sanity-check a
  data export (e.g. catch a column that is unexpectedly all-null or ragged rows).

For a deeper, statistical analysis once you know the shape, hand off to the
**Analyze a dataset** flow (pandas in the sandbox) — this skill is the fast first
pass, not a replacement for it.

## How to run it

Run the bundled script in the sandbox. It takes a path (or `-` for stdin):

```bash
python3 skills/csv-profiler/scripts/profile_csv.py data.csv
```

A human-readable table is the default. Useful flags:

| Flag            | Effect                                                                 |
|-----------------|------------------------------------------------------------------------|
| `--json`        | Emit the full profile as JSON (machine-readable; good for further work).|
| `--delimiter ;` | Use a different field delimiter. Pass `\t` for tab-separated files.    |
| `--no-header`   | Treat the first row as data; columns are named `col_1 … col_n`.        |

Examples:

```bash
# Tab-separated, as JSON
python3 skills/csv-profiler/scripts/profile_csv.py export.tsv --delimiter '\t' --json

# Headerless file piped in from another command
cut -d, -f1-5 big.csv | python3 skills/csv-profiler/scripts/profile_csv.py - --no-header
```

## What it reports

- **Top line:** total row count and column count, plus a count of any **ragged
  rows** (rows whose field count differs from the header — a common sign of an
  unquoted delimiter or a malformed export).
- **Per column:**
  - **type** — `integer`, `float`, `boolean`, `string`, or `empty`, inferred
    from the non-empty cells.
  - **nulls** and **fill_rate** — how many cells are empty and the fraction that
    are populated.
  - **stats** — for numeric columns, `min` / `max` / `mean` / `median` (and
    `stdev` when there is more than one value); for text/boolean columns, the
    distinct-value count and the most common value with its frequency.

## How to read the output

- Lead with the shape, then call out anything notable: a column with a low
  `fill_rate`, an unexpected `type` (a numeric column inferred as `string` usually
  means stray non-numeric cells or thousands separators), or non-zero
  `ragged_rows`.
- The profile is **descriptive, not a verdict** — flag what looks off and confirm
  with the user or with a deeper pass rather than asserting data is "clean."
- Never invent statistics the script did not produce. If a column is `empty` or
  the file failed to parse, say so plainly.

## Notes

- Type inference is per column over its non-empty cells: a column is `integer`
  only if **every** non-empty cell parses as an int, `float` if every cell parses
  as a float, `boolean` for recognized true/false tokens, otherwise `string`.
- The script reads the whole file into memory — fine for the typical CSVs a team
  works with interactively; for multi-gigabyte files, sample first.
- Standard-library only and deterministic, so the same input always yields the
  same profile.

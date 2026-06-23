#!/usr/bin/env python3
"""Profile a CSV file using only the Python standard library.

Reads a delimited text file, infers a type per column, and reports row/column
counts plus per-column null counts and basic statistics. It depends only on the
standard library (csv, statistics, argparse, json), so it runs in the sandbox
even when pandas is unavailable — a deterministic first look at a dataset before
reaching for heavier tooling.

Type inference per column (over the non-empty cells):
  - "integer"  if every non-empty cell parses as an int
  - "float"    if every non-empty cell parses as a float (and not all integer)
  - "boolean"  if every non-empty cell is a recognized true/false token
  - "empty"    if the column has no non-empty cells
  - "string"   otherwise

Numeric columns report min / max / mean / median / stdev. Non-numeric columns
report the distinct-value count and the most common value. Every column reports
its null (empty) count and fill rate.

Usage:
    python3 profile_csv.py DATA.csv [--delimiter ,] [--no-header] [--json]
    python3 profile_csv.py -            # read from stdin
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from typing import Any

_TRUE_TOKENS = {"true", "yes", "y", "t", "1"}
_FALSE_TOKENS = {"false", "no", "n", "f", "0"}
_BOOL_TOKENS = _TRUE_TOKENS | _FALSE_TOKENS


def _is_blank(value: str) -> bool:
    return value.strip() == ""


def _try_int(value: str) -> bool:
    try:
        int(value.strip())
    except ValueError:
        return False
    return True


def _try_float(value: str) -> bool:
    try:
        float(value.strip())
    except ValueError:
        return False
    return True


def infer_type(cells: list[str]) -> str:
    """Infer a column type from its non-empty cells."""
    values = [c for c in cells if not _is_blank(c)]
    if not values:
        return "empty"
    if all(_try_int(v) for v in values):
        return "integer"
    if all(_try_float(v) for v in values):
        return "float"
    if all(v.strip().lower() in _BOOL_TOKENS for v in values):
        return "boolean"
    return "string"


def _numeric_stats(cells: list[str], as_int: bool) -> dict[str, Any]:
    nums: list[float] = [
        int(c.strip()) if as_int else float(c.strip()) for c in cells if not _is_blank(c)
    ]
    if not nums:
        return {}
    stats: dict[str, Any] = {
        "min": min(nums),
        "max": max(nums),
        "mean": round(statistics.fmean(nums), 6),
        "median": statistics.median(nums),
    }
    if len(nums) > 1:
        stats["stdev"] = round(statistics.stdev(nums), 6)
    return stats


def _categorical_stats(cells: list[str]) -> dict[str, Any]:
    values = [c.strip() for c in cells if not _is_blank(c)]
    if not values:
        return {}
    counts = Counter(values)
    top_value, top_count = counts.most_common(1)[0]
    return {
        "distinct": len(counts),
        "most_common": top_value,
        "most_common_count": top_count,
    }


def profile_column(name: str, cells: list[str]) -> dict[str, Any]:
    total = len(cells)
    nulls = sum(1 for c in cells if _is_blank(c))
    col_type = infer_type(cells)
    fill_rate = round((total - nulls) / total, 4) if total else 0.0
    column: dict[str, Any] = {
        "name": name,
        "type": col_type,
        "count": total,
        "nulls": nulls,
        "fill_rate": fill_rate,
    }
    if col_type in ("integer", "float"):
        column["stats"] = _numeric_stats(cells, as_int=col_type == "integer")
    elif col_type in ("string", "boolean"):
        column["stats"] = _categorical_stats(cells)
    else:
        column["stats"] = {}
    return column


def profile_rows(header: list[str], rows: list[list[str]]) -> dict[str, Any]:
    ncols = len(header)
    columns: list[dict[str, Any]] = []
    for idx, name in enumerate(header):
        cells = [row[idx] if idx < len(row) else "" for row in rows]
        columns.append(profile_column(name, cells))
    ragged = sum(1 for row in rows if len(row) != ncols)
    return {
        "rows": len(rows),
        "columns": ncols,
        "ragged_rows": ragged,
        "column_profiles": columns,
    }


def read_csv(stream: Any, delimiter: str, has_header: bool) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(stream, delimiter=delimiter)
    records = [row for row in reader if row]
    if not records:
        return [], []
    if has_header:
        return records[0], records[1:]
    width = len(records[0])
    header = [f"col_{i + 1}" for i in range(width)]
    return header, records


def format_text(report: dict[str, Any]) -> str:
    lines = [
        f"Rows: {report['rows']}    Columns: {report['columns']}",
    ]
    if report["ragged_rows"]:
        lines.append(f"Ragged rows (wrong column count): {report['ragged_rows']}")
    lines.append("")
    header = f"{'column':<24} {'type':<9} {'nulls':>7} {'fill':>7}  stats"
    lines.append(header)
    lines.append("-" * len(header))
    for col in report["column_profiles"]:
        stats = col["stats"]
        if col["type"] in ("integer", "float") and stats:
            summary = (
                f"min={stats['min']} max={stats['max']} "
                f"mean={stats['mean']} median={stats['median']}"
            )
            if "stdev" in stats:
                summary += f" stdev={stats['stdev']}"
        elif stats:
            summary = (
                f"distinct={stats['distinct']} "
                f"top={stats['most_common']!r}({stats['most_common_count']})"
            )
        else:
            summary = ""
        lines.append(
            f"{col['name']:<24} {col['type']:<9} {col['nulls']:>7} "
            f"{col['fill_rate']:>7} {summary}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile a CSV using only the standard library.")
    parser.add_argument("path", help="Path to the CSV file, or - for stdin.")
    parser.add_argument("--delimiter", default=",", help="Field delimiter (default: ',').")
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Treat the first row as data; synthesize column names col_1..col_n.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    delimiter = "\t" if args.delimiter == "\\t" else args.delimiter
    has_header = not args.no_header

    if args.path == "-":
        header, rows = read_csv(sys.stdin, delimiter, has_header)
    else:
        with open(args.path, newline="", encoding="utf-8") as handle:
            header, rows = read_csv(handle, delimiter, has_header)

    if not header:
        print("No data found in input.", file=sys.stderr)
        return 1

    report = profile_rows(header, rows)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

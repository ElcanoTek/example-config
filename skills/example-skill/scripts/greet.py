#!/usr/bin/env python3
"""Tiny standard-library demo for the example-skill template.

This script exists only to show Level-3 progressive disclosure: deterministic
logic the agent *runs* rather than re-deriving in prose. It takes an optional
name and prints a stable greeting, then exits 0. Replace it in your own skill
with a real transform, validator, or checklist generator.

Usage:
    python3 skills/example-skill/scripts/greet.py [NAME]
"""

from __future__ import annotations

import sys


def greet(name: str) -> str:
    """Return a deterministic greeting for the given name."""
    cleaned = name.strip() or "there"
    return f"Hello, {cleaned}! This is the example-skill Level-3 script."


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else "there"
    print(greet(name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

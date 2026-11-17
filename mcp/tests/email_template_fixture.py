"""A complete email template for the template tests.

This bundle ships no email templates of its own: the team saves theirs from a
chat. The tests still need "an email a person asked to keep", so this is one:
a valid spec whose sample fits it. It is demo data (Acme Outfitters) and
is never listed in the library.
"""

from __future__ import annotations

import copy
from typing import Any

TEMPLATE_ID = "weekly-status-update"

_SPEC: dict[str, Any] = {
    "name": "Weekly status update",
    "description": "The weekly note to a client: results, what is working, what changed, what we are watching.",
    "audience": "client",
    "use_when": "A campaign is live and the client gets a short written update each week.",
    "max_words": 260,
    "subject": {
        "instruction": "Client, campaign and the week, e.g. 'Acme | Spring Sale | Week of May 4'.",
        "max_chars": 70,
    },
    "header": {"headline": "The campaign name.", "status": "One short line on pacing."},
    "rules": [
        "Lead with the result, then the reason.",
        "Plain language; no internal codes or tool names.",
    ],
    "never_say": ["No optimizations", "excited", "re:\\b[A-Z]{3}\\d{5}\\b", "re:\\b[A-Z]{3}-\\d{3}\\b"],
    "sections": [
        {
            "id": "summary",
            "title": "Summary",
            "heading": "Summary",
            "block": "text",
            "instruction": "The week in at most three bullets, most important first.",
            "limits": {"max_paragraphs": 1, "max_bullets": 3, "max_words_per_bullet": 28},
        },
        {
            "id": "glance",
            "title": "At a glance",
            "heading": "At A Glance",
            "block": "headline_metrics",
            "instruction": "Three or four numbers the client tracks.",
            "limits": {"min_items": 3, "max_items": 4},
        },
        {
            "id": "working",
            "title": "What is working",
            "heading": "What Is Working",
            "block": "text",
            "instruction": "Bullets only: what is performing and why.",
            "limits": {"max_paragraphs": 0, "max_bullets": 3, "max_words_per_bullet": 28},
        },
        {
            "id": "adjusted",
            "title": "What we adjusted",
            "heading": "What We Adjusted",
            "block": "text",
            "required": False,
            "instruction": "Changes made this week. Leave the section out when there were none.",
            "limits": {"max_paragraphs": 0, "max_bullets": 3, "max_words_per_bullet": 28},
        },
        {
            "id": "watching",
            "title": "What we are watching",
            "heading": "What We Are Watching",
            "block": "text",
            "instruction": "One or two things under review and when the client hears back.",
            "limits": {"max_paragraphs": 0, "max_bullets": 2, "max_words_per_bullet": 28},
        },
        {
            "id": "ask",
            "title": "Ask",
            "heading": "Action Needed: Confirm The Budget",
            "block": "callout",
            "kind": "opportunity",
            "required": False,
            "instruction": "The one thing the client must do, if anything.",
            "limits": {"max_words": 40},
        },
        {
            "id": "closing",
            "title": "Closing",
            "block": "closing",
            "instruction": "One line and the sign-off.",
            "limits": {"max_words": 30},
        },
    ],
    "sample": {
        "subject": "Acme Outfitters | Spring Sale | Week of May 4",
        "client_name": "Acme Outfitters",
        "headline": "Spring Sale",
        "date": "May 11, 2026",
        "date_range": "May 4 to May 10, 2026",
        "badge_label": "On track",
        "status": "Pacing to plan",
        "blocks": [
            {
                "section": "summary",
                "type": "text",
                "bullets": [
                    "Spend reached **$41,200**, 3% ahead of plan for the week.",
                    "Cost per visit fell to $1.84 from $2.10.",
                    "Video completion held at 78%.",
                ],
            },
            {
                "section": "glance",
                "type": "headline_metrics",
                "items": [
                    {"label": "Spend", "value": "$41,200", "delta": "+3.0%", "trend": "positive"},
                    {"label": "Cost per visit", "value": "$1.84", "delta": "-12.4%", "trend": "positive"},
                    {"label": "Video completion", "value": "78%"},
                ],
            },
            {
                "section": "working",
                "type": "text",
                "bullets": [
                    "Connected TV drove the lowest cost per visit.",
                    "Weekend delivery recovered after the frequency change.",
                    "The outdoor audience outperformed the general one.",
                ],
            },
            {
                "section": "adjusted",
                "type": "text",
                "bullets": [
                    "Moved 10% of display budget to connected TV.",
                    "Paused two placements with low viewability.",
                ],
            },
            {
                "section": "watching",
                "type": "text",
                "bullets": [
                    "Mobile cost per visit, which rose late in the week.",
                    "Creative fatigue on the oldest video; we report back Friday.",
                ],
            },
            {
                "section": "ask",
                "type": "callout",
                "text": "Please confirm the added $5,000 for the final week by Thursday.",
            },
            {"section": "closing", "type": "closing", "text": ["Thank you,", "The Acme team"]},
        ],
    },
}


def spec(**changes: Any) -> dict[str, Any]:
    """A fresh copy of the template, with `changes` applied on top."""
    fresh = copy.deepcopy(_SPEC)
    fresh.update(changes)
    return fresh

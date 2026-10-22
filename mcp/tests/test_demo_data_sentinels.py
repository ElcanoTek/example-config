"""The email-lint demo-data sentinels must track the canonical template verbatim.

`email_lint.py` blocks an outbound email that still contains the canonical
template's example data — the "you copy-pasted the template instead of
regenerating it" check. It does that by matching literal strings.

That makes it tightly coupled to `protocols/email-style.yaml`: rename a demo
campaign in the template and every sentinel naming it silently stops matching, so
the leak check keeps passing while no longer checking anything. That is exactly
what happened when the demo campaigns were renamed (they had carried real company
names), and it is invisible without this test — the detector has no way to
report that its own needles are stale.
"""

from __future__ import annotations

from pathlib import Path

import email_lint
import pytest
import yaml

BUNDLE_ROOT = Path(__file__).resolve().parents[2]

EMAIL_STYLE_PATH = BUNDLE_ROOT / "protocols" / "email-style.yaml"


@pytest.fixture(scope="module")
def canonical_template() -> str:
    with EMAIL_STYLE_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["canonical_template"]


@pytest.mark.parametrize("marker", email_lint._TIER2_DEMO_PROSE_MARKERS)
def test_tier2_prose_marker_is_really_in_the_template(marker: str, canonical_template: str) -> None:
    assert marker in canonical_template, (
        f"tier-2 demo-prose sentinel is stale — {marker!r} no longer appears in canonical_template, "
        "so leaked demo prose would pass the lint. Update the sentinel to match the template."
    )


@pytest.mark.parametrize("marker", email_lint._TIER3_DEMO_MARKERS)
def test_tier3_marker_is_really_in_the_template(marker: str, canonical_template: str) -> None:
    assert marker in canonical_template, (
        f"tier-3 demo sentinel is stale — {marker!r} no longer appears in canonical_template, "
        "so it can never fire. Update the sentinel to match the template."
    )


def test_tier3_threshold_is_satisfiable() -> None:
    # The tier-3 rule blocks only when at least this many demo markers co-occur.
    # A threshold above the number of markers can never trigger.
    assert len(email_lint._TIER3_DEMO_MARKERS) >= email_lint._TIER3_BLOCK_THRESHOLD


def test_no_sentinel_names_a_real_brand(canonical_template: str) -> None:
    # The template's demo data is illustrative filler. It must not carry a real
    # company name: a half-stripped template would email it. These are the brands
    # an earlier version of the demo data used, so they are the ones that could
    # come back with a careless re-port.
    others = ("amazon", "pepsi")
    haystacks = {
        "canonical_template": canonical_template.lower(),
        "tier2 sentinels": " ".join(email_lint._TIER2_DEMO_PROSE_MARKERS).lower(),
        "tier3 sentinels": " ".join(email_lint._TIER3_DEMO_MARKERS).lower(),
    }
    for where, hay in haystacks.items():
        found = [name for name in others if name in hay]
        assert not found, f"{where} names a real brand: {found}"

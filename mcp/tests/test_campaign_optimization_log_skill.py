"""Pins the campaign-optimization-log skill, protocol, template, prompts, and show_ui blueprints."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

TESTS_DIR = Path(__file__).resolve().parent
GENUI_LITE_PATH = TESTS_DIR / "genui_lite.py"
assert GENUI_LITE_PATH.is_file(), f"Required GenUI validator missing: {GENUI_LITE_PATH}"
_spec = importlib.util.spec_from_file_location("genui_lite", GENUI_LITE_PATH)
assert _spec is not None and _spec.loader is not None
_genui_lite = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("genui_lite", _genui_lite)
_spec.loader.exec_module(_genui_lite)
validate = _genui_lite.validate

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "campaign-optimization-log"
PROTOCOL = ROOT / "protocols" / "optimization-log-protocol.md"
TEMPLATE = ROOT / "protocols" / "CampaignConfig_TEMPLATE.yaml"
QUICKSTART = ROOT / "protocols" / "optimization-trader-quickstart.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_skill_description_routes_all_trader_commands() -> None:
    frontmatter = yaml.safe_load(_text(SKILL_DIR / "SKILL.md").split("---")[1])
    assert frontmatter["name"] == "campaign-optimization-log"
    desc = frontmatter["description"]
    assert len(desc) <= 1024
    for phrase in [
        "onboard",
        "campaign config",
        "review <Client> opts",
        "review my opts",
        "workbench",
        "execute <Opt ID>",
        "craft a client email",
        "incremental budget",
        "sync all campaign configs",
        "update the optimization protocol",
    ]:
        assert phrase in desc, phrase


def test_reference_show_ui_blueprints_are_valid_genui_cards() -> None:
    ref = _text(SKILL_DIR / "REFERENCE.md")
    blocks = re.findall(r"```json\n(\{.*?\})\n```", ref, re.DOTALL)
    assert len(blocks) == 3, f"expected 3 show_ui blueprints in REFERENCE.md, found {len(blocks)}"
    for idx, raw in enumerate(blocks, start=1):
        spec = json.loads(raw)
        assert isinstance(spec.get("title"), str) and spec["title"]
        assert isinstance(spec.get("components"), list) and spec["components"]
        assert isinstance(spec.get("actions"), list) and spec["actions"]
        errors = validate(spec)
        assert errors == [], f"Blueprint {idx} ({spec.get('title')}) failed genui validation: {errors}"


def test_protocol_and_skill_cover_core_guardrails() -> None:
    skill = _text(SKILL_DIR / "SKILL.md")
    protocol = _text(PROTOCOL)
    quickstart = _text(QUICKSTART)
    template = _text(TEMPLATE)

    for required in [
        "Day-1 DSP Report Preflight QA",
        "storage_target",
        "review <Client> opts",
        "review my opts",
        "[SCALE WARNING]",
        "Magnite DV+",
        "_Optimization_Block_List",
        "Post-Execution Verification Receipt",
        "Read-Before-Write Merge Guardrail",
        "4-Test Spend Elasticity",
    ]:
        assert required in skill, f"SKILL.md missing {required!r}"
        assert required in protocol, f"optimization-log-protocol.md missing {required!r}"

    assert 'schema_version: "3.0"' in template
    assert "storage:" in template
    assert "ssp_seats:" in template
    assert "deals:" in template
    assert "review <Client> opts" in quickstart
    assert "review my opts" in quickstart
    assert "Clone & edit" in quickstart
    assert "Flow 4" in skill

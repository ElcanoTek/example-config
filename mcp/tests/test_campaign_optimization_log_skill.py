"""Pins the campaign-optimization-log skill, protocol, template, prompts, and show_ui blueprints."""

from __future__ import annotations

import importlib.util
import json
import math
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


def _step_grid_errors(node: object, path: str = "root") -> list[str]:
    """Mirror fleet/internal/genui/spec.go numberValue step-grid validation."""
    errors: list[str] = []
    if isinstance(node, dict):
        ntype = node.get("type")
        val = node.get("value")
        if ntype in {"number", "slider"} and isinstance(val, (int, float)) and not isinstance(val, bool):
            n = float(val)
            raw_min = node.get("min")
            has_min = isinstance(raw_min, (int, float)) and not isinstance(raw_min, bool)
            min_v = float(raw_min) if has_min else 0.0
            raw_step = node.get("step")
            has_step = isinstance(raw_step, (int, float)) and not isinstance(raw_step, bool)
            if not has_step and ntype == "slider":
                step, has_step = 1.0, True
            else:
                step = float(raw_step) if has_step else 0.0
            if has_step and step > 0:
                base = min_v if has_min else 0.0
                q = (n - base) / step
                if math.fabs(q - round(q)) > 1e-9:
                    errors.append(f"{path}: {val} is not on the step grid (min + k * step)")
        for key in ("components", "children", "tabs", "items"):
            children = node.get(key)
            if isinstance(children, list):
                for idx, child in enumerate(children):
                    errors.extend(_step_grid_errors(child, f"{path}.{key}[{idx}]"))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            errors.extend(_step_grid_errors(item, f"{path}[{idx}]"))
    return errors


def test_step_grid_helper_rejects_off_grid_default() -> None:
    off_grid = {
        "title": "Sample",
        "components": [
            {
                "type": "number",
                "id": "min_spend_usd",
                "label": "Min Spend Sample Floor ($ USD)",
                "value": 25,
                "min": 1,
                "step": 5,
            }
        ],
        "actions": [{"id": "ok", "label": "OK"}],
    }
    assert _step_grid_errors(off_grid) != []
    on_grid = {
        **off_grid,
        "components": [{**off_grid["components"][0], "step": 1}],
    }
    assert _step_grid_errors(on_grid) == []


def test_reference_show_ui_blueprints_are_valid_genui_cards() -> None:
    ref = _text(SKILL_DIR / "REFERENCE.md")
    blocks = re.findall(r"```json\n(\{.*?\})\n```", ref, re.DOTALL)
    assert len(blocks) == 3, f"expected 3 show_ui blueprints in REFERENCE.md, found {len(blocks)}"
    for idx, raw in enumerate(blocks, start=1):
        spec = json.loads(raw)
        assert isinstance(spec.get("title"), str) and spec["title"]
        assert isinstance(spec.get("components"), list) and spec["components"]
        assert isinstance(spec.get("actions"), list) and spec["actions"]
        errors = validate(spec) + _step_grid_errors(spec)
        assert errors == [], f"Blueprint {idx} ({spec.get('title')}) failed genui validation: {errors}"


def test_protocol_and_skill_cover_core_guardrails_and_stay_partner_generic() -> None:
    skill = _text(SKILL_DIR / "SKILL.md")
    ref = _text(SKILL_DIR / "REFERENCE.md")
    protocol = _text(PROTOCOL)
    quickstart = _text(QUICKSTART)
    template = _text(TEMPLATE)

    for required in [
        "Day-1 DSP Report Preflight QA",
        "storage_target",
        "connected_storage",
        "connected_feed",
        "review <Client> opts",
        "review my opts",
        "[SCALE WARNING]",
        "Shared Targeting-List Isolation Rule",
        "_Optimization_Block_List",
        "Post-Execution Verification Receipt",
        "Read-Before-Write Merge Guardrail",
        "4-Test Spend Elasticity",
    ]:
        assert required in skill, f"SKILL.md missing {required!r}"
        assert required in protocol, f"optimization-log-protocol.md missing {required!r}"

    for doc_name, content in [
        ("SKILL.md", skill),
        ("REFERENCE.md", ref),
        ("optimization-log-protocol.md", protocol),
        ("optimization-trader-quickstart.md", quickstart),
        ("CampaignConfig_TEMPLATE.yaml", template),
    ]:
        assert not re.search(r"fast\.?io", content, re.I), f"{doc_name} must not reference fast.io"

    assert 'schema_version: "3.0"' in template
    assert "storage:" in template
    assert "connected_storage" in template
    assert "connected_feed" in template
    assert "ssp_seats:" in template
    assert "deals:" in template
    assert "review <Client> opts" in quickstart
    assert "review my opts" in quickstart
    assert "Clone & edit" in quickstart
    assert "Flow 4" in skill

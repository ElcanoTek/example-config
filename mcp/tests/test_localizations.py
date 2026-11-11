"""Offline coverage and integrity checks for this bundle's UI translations.

English source strings remain the canonical keys. Raw protocol files, tool IDs,
credentials and user-authored content are not translation inputs.
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def owned_messages():
    base = ROOT
    out = {}

    def add(value, location):
        if isinstance(value, str) and value.strip() and re.search("[A-Za-z]", value):
            out.setdefault(value, []).append(location)

    d = yaml.safe_load((base / "manifest.yaml").read_text())
    for k in ["login_title", "login_tagline", "share_title", "share_description"]:
        add(d.get("branding", {}).get(k), "branding." + k)
    branding = d.get("branding", {})
    house_tools = (branding.get("house_tools_name") or "").strip()
    if not house_tools:
        house_tools = (branding.get("app_name") or "Fleet").strip() + " Tools"
    add(house_tools, "branding.house_tools_name (including app-name fallback)")
    for k in ["label", "mobile_label", "title", "message", "subject"]:
        add(d.get("branding", {}).get("support", {}).get(k), "branding.support." + k)
    for section in ["mcp_servers", "remote_mcp_catalog"]:
        for n, c in enumerate(d.get(section, []) or []):
            for k in ["display_name", "description", "setup_hint"]:
                add(c.get(k), f"{section}.{n}.{k}")
            for v in c.get("url_variants", []) or []:
                add(v.get("label"), section + ".variant.label")
    for key in ["cards", "protocol_pills"]:
        for n, c in enumerate(d.get("empty_state", {}).get(key, []) or []):
            if not isinstance(c, dict):
                continue
            for k in ["title", "section", "desc", "cta", "promptTemplate", "starterPrompt"]:
                add(c.get(k), f"empty_state.{key}.{n}.{k}")
            for f in c.get("fields", []) or []:
                for k in ["label", "placeholder", "hint"]:
                    add(f.get(k), f"field.{f.get('key')}.{k}")
                if f.get("type") not in ["select", "multiselect"]:
                    add(f.get("default"), "field.default")
                for v in f.get("options", []) or []:
                    add(v, "field.option")
    for n, c in enumerate(d.get("task_templates", []) or []):
        for k in ["name", "description"]:
            add(c.get(k), f"task_templates.{n}.{k}")
        for k in ["prompt", "context"]:
            add(c.get("task", {}).get(k), f"task_templates.{n}.task.{k}")
    for p in (base / "prompts").glob("*"):
        if p.name.lower().startswith("readme") or p.suffix not in [".yaml", ".yml", ".md", ".txt"]:
            continue
        name = p.stem.replace("_", " ").replace("-", " ").strip()
        description = ""
        if p.suffix in [".yaml", ".yml"]:
            try:
                c = yaml.safe_load(p.read_text())
            except yaml.YAMLError:
                c = {}
            if not isinstance(c, dict):
                c = {}
            name = str(c.get("name") or name).strip()
            description = str(c.get("description") or c.get("goal") or "").strip()
            for f in c.get("fields", []) or []:
                if not isinstance(f, dict):
                    continue
                for k in ["label", "placeholder", "hint"]:
                    add(f.get(k), str(p.relative_to(base)) + ".field." + k)
                for v in f.get("options", []) or []:
                    add(v, str(p.relative_to(base)) + ".field.option")
            add(c.get("promptTemplate"), str(p.relative_to(base)) + ".promptTemplate")
        else:
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("# "):
                    name = line[2:].strip()
                    continue
                if line and not line.startswith("#"):
                    description = line
                    break
        description = " ".join(description.split())
        if len(description) > 240:
            description = description[:239] + "…"
        add(name, str(p.relative_to(base)) + ".name")
        add(description, str(p.relative_to(base)) + ".description")
    # Fleet's ListPersonas API exposes filenames only; the picker capitalizes
    # the basename. YAML name/role/tagline are raw model context, not UI fields.
    # User-authored persona names/descriptions are separate and never translated.
    for p in (base / "personas").glob("*.yaml"):
        add(p.stem[:1].upper() + p.stem[1:], "persona.basename")
    for p in list((base / "skills").glob("*/SKILL.md")) + list((base / "plugins").glob("*/skills/*/SKILL.md")):
        parts = p.read_text().split("---", 2)
        if len(parts) == 3:
            c = yaml.safe_load(parts[1])
            if isinstance(c, dict):
                add(c.get("description"), str(p.relative_to(base)) + ".description")
    for p in (base / "plugins").glob("*/plugin.json"):
        plugin = json.loads(p.read_text())
        for server in plugin.get("extensions", {}).get("com.elcanotek.fleet", {}).get("mcp_servers", {}).values():
            for key in ["display_name", "description", "setup_hint"]:
                add(server.get(key), str(p.relative_to(base)) + "." + key)
    return out


def test_owned_copy_has_complete_spanish_and_portuguese_catalogs():
    """A changed UI label must be translated in the same bundle PR."""
    expected = set(owned_messages())
    assert expected, "no bundle presentation copy found"
    catalogs = {}
    for language in ("es", "pt"):
        pairs = json.loads((ROOT / "locales" / f"{language}.json").read_text(), object_pairs_hook=list)
        assert isinstance(pairs, list)
        catalog = dict(pairs)
        assert len(catalog) == len(pairs), f"duplicate keys in {language}"
        assert set(catalog) == expected, (
            language,
            "missing",
            sorted(expected - set(catalog)),
            "stale",
            sorted(set(catalog) - expected),
        )
        catalogs[language] = catalog
        for source, translated in catalog.items():
            assert isinstance(translated, str) and translated.strip(), (language, source)
            assert placeholders(source) == placeholders(translated), (language, source)
            # Inline code, links and email destinations are operational values.
            # The translated prose must never rewrite them.
            for token in protected_tokens(source):
                assert token in translated, (language, source, token)
    assert catalogs["es"] != catalogs["pt"]


def placeholders(text):
    return sorted(re.findall(r"\{[A-Za-z_0-9]+\}", text))


def protected_tokens(text):
    tokens = re.findall(r"`[^`]+`|https?://[^\s)]+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]+\b", text)
    # Prepared prompts also refer to operational targets without Markdown.
    tokens += re.findall(r"\bmcp_[A-Za-z0-9_]+\b|\b(?:protocols|skills|prompts|personas)/[A-Za-z0-9_./-]+", text)
    # Only actual protocol basenames are reserved: ordinary hyphenated prose
    # remains translatable. Keep the token's boundaries as well as its text.
    for path in (ROOT / "protocols").glob("*"):
        if (
            path.is_file()
            and path.suffix in (".md", ".yaml", ".yml", ".txt")
            and path.stem.lower() != "readme"
            and re.search(r"(?<![\w-])" + re.escape(path.stem) + r"(?![\w-])", text)
            and (
                "-" in path.stem
                or "_" in path.stem
                or re.search(r"[\"'`]" + re.escape(path.stem) + r"[\"'`]", text)
            )
        ):
            tokens.append(path.stem)
    return tokens


def test_prepared_prompts_preserve_bare_operational_targets():
    source = "Read protocols/research-report.md with mcp_pages_get_guide."
    for token in ("protocols/research-report.md", "mcp_pages_get_guide"):
        assert token in protected_tokens(source)
        corrupted = source.replace(token, "translated-target")
        assert any(value not in corrupted for value in protected_tokens(source))
    for path in (ROOT / "protocols").glob("*.md"):
        if path.stem.lower() == "readme":
            continue
        source = f'Run the "{path.stem}" protocol.'
        assert path.stem in protected_tokens(source)
        assert path.stem not in protected_tokens(f"Run the {path.stem}-other protocol.")


def test_kubernetes_control_plane_ships_the_catalogs():
    containerfile = ROOT / "deploy/kubernetes/Containerfile.control-plane"
    if containerfile.exists():
        assert re.search(r"^COPY\s+locales/\s+/opt/fleet/client/locales/\s*$", containerfile.read_text(), re.M)

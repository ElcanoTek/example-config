#!/usr/bin/env bash
# shellcheck disable=SC2016  # single-quoted '${VAR}' literals + inlined Python are intentional
# MCP installer for this client-config bundle.
#
# Registers EVERY MCP server declared in this bundle's manifest.yaml into
# whichever local coding agents you have installed. It is generic: it reads the
# server list, commands, env-var names, and HTTP transports straight from
# manifest.yaml + mcp/, so this same script drops unchanged into any sibling
# "<client>-config" bundle.
#
# One-line install (run from anywhere; clones/uses the bundle, builds .venv):
#   curl -fsSL https://raw.githubusercontent.com/ElcanoTek/example-config/main/install.sh | bash
#
# Pick the install dir (default ~/example-config) — positional arg or $BUNDLE_DIR:
#   curl -fsSL .../install.sh | bash -s -- ~/code/example-config
#   curl -fsSL .../install.sh | BUNDLE_DIR=/opt/example-config bash
#
# If you already have the repo checked out, just run ./install.sh in it.
#
# Safe by design:
#   * Secrets are NEVER written into any config. Credential env vars from the
#     manifest are registered as *references* in each agent's own
#     environment-expansion syntax (Claude `${VAR}`, opencode `{env:VAR}`,
#     Crush `${VAR}`, Codex `env_vars=[...]`, Goose `env_keys:[...]`), so each
#     agent resolves the live value from its launch environment at run time.
#   * Idempotent: re-running skips servers an agent already has.
#   * It only touches agents you actually have installed; absent ones are skipped.
#
# Flags:
#   --list              List the MCP servers in this bundle + detected agents, then exit.
#   --dry-run           Show exactly what WOULD be registered, change nothing.
#   --agent <name>      Target one agent only (claude|opencode|codex|goose|crush|grok).
#   --scope <scope>     Claude Code scope: local|project|user  (default: user).
#   -h, --help          This help.
#
# ── Per-agent registration mechanism + the doc each format was taken from ─────
#   Claude Code  `claude mcp add <name> [-s scope] [-e K=V] -- <cmd> <args>`;
#                HTTP via `-t http -H "H: v" <name> <url>`. Idempotency check:
#                `claude mcp get <name>` (exit 0 = present). `${VAR}` in -e values
#                is stored verbatim and expanded by Claude Code at launch.
#                doc: https://code.claude.com/docs/en/mcp  (verified CLI v2.1.185)
#   opencode     project `opencode.json` "mcp" block. Local: {type:"local",
#                command:[...], enabled:true, environment:{...}}. `{env:VAR}`
#                substitution. doc: https://opencode.ai/docs/mcp-servers/
#   Codex CLI    `~/.codex/config.toml` [mcp_servers.<name>] command/args/[env]
#                table + `env_vars=[...]` to forward names without baking values.
#                doc: https://developers.openai.com/codex/mcp
#   Goose        `~/.config/goose/config.yaml` extensions.<name>: type:stdio,
#                cmd/args, envs:{literals}, env_keys:[secret names], timeout.
#                doc: https://block.github.io/goose/docs/guides/config-file/
#   Crush        project `crush.json` "mcp" block: {type:"stdio", command, args,
#                env, timeout(ms)}; supports ${VAR}/$VAR expansion.
#                doc: https://github.com/charmbracelet/crush  (README + config.go)
#   Grok Build   `grok mcp add <name> -t stdio -c <cmd> -a <arg> -a <arg> ...`
#                (CLI, like Claude/Codex). doc: lifeline README pattern; grok mcp --help
#
set -euo pipefail

REPO="https://github.com/ElcanoTek/example-config.git"

# ── pretty printing ──────────────────────────────────────────────────────────
b() { printf '\n\033[1m%s\033[0m\n' "$*"; }   # bold heading
i() { printf '  %s\n' "$*"; }                 # indented line
warn() { printf '  \033[33m%s\033[0m\n' "$*"; }
err() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; }

# ── arg parsing ────────────────────────────────────────────────────────────--
DO_LIST=0; DO_DRYRUN=0; ONLY_AGENT=""; CLAUDE_SCOPE="${CLAUDE_SCOPE:-user}"; POS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --list) DO_LIST=1; shift ;;
    --dry-run|--dry) DO_DRYRUN=1; shift ;;
    --agent) ONLY_AGENT="${2:-}"; shift 2 ;;
    --agent=*) ONLY_AGENT="${1#*=}"; shift ;;
    --scope) CLAUDE_SCOPE="${2:-}"; shift 2 ;;
    --scope=*) CLAUDE_SCOPE="${1#*=}"; shift ;;
    -h|--help)
      cat <<'USAGE'
MCP installer for this client-config bundle.

Registers every MCP server declared in this bundle's manifest.yaml into whichever
local coding agents you have installed (Claude Code, opencode, Codex, Goose,
Crush, Grok). Generic: it reads the server list/commands/env-var names/HTTP
transports from manifest.yaml + mcp/, so it works unchanged in any *-config bundle.

Usage:
  ./install.sh [flags] [bundle-dir]
  curl -fsSL .../install.sh | bash [-s -- bundle-dir]

Flags:
  --list            List the bundle's MCP servers + detected agents, then exit.
  --dry-run         Show exactly what WOULD be registered; change nothing.
  --agent <name>    Target one agent: claude|opencode|codex|goose|crush|grok.
  --scope <scope>   Claude Code scope: local|project|user  (default: user).
  -h, --help        This help.

Safe: secrets are never written to config (credential vars are registered as
references resolved from your environment at launch); idempotent; merges into
existing config without clobbering other servers; skips agents you don't have.
See INSTALL.md for per-agent details and verification steps.
USAGE
      exit 0 ;;
    -*) err "unknown flag: $1"; exit 2 ;;
    *) POS="$1"; shift ;;
  esac
done

# ── locate / fetch the bundle ─────────────────────────────────────────────────
# If this script is run from inside a checkout (manifest.yaml beside it), use
# that. Otherwise clone/update into the chosen dir (one-line curl|bash path).
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/manifest.yaml" ] && [ -d "$SELF_DIR/mcp" ]; then
  DIR="$SELF_DIR"
else
  DIR="${POS:-${BUNDLE_DIR:-$HOME/example-config}}"
  if [ -d "$DIR/.git" ]; then
    i "Updating $DIR"
    git -C "$DIR" pull --quiet --ff-only </dev/null 2>/dev/null || i "(kept current checkout)"
  elif [ -f "$DIR/manifest.yaml" ]; then
    i "Using $DIR"
  else
    command -v git >/dev/null 2>&1 || { err "git is required to clone the bundle."; exit 1; }
    i "Cloning $REPO -> $DIR"
    git clone --quiet --depth 1 "$REPO" "$DIR" </dev/null
  fi
fi
MANIFEST="$DIR/manifest.yaml"
[ -f "$MANIFEST" ] || { err "$MANIFEST not found."; exit 1; }
[ -d "$DIR/mcp" ]  || { err "$DIR/mcp not found."; exit 1; }

b "MCP installer — bundle: $DIR"
command -v python3 >/dev/null 2>&1 || { err "python3 is required."; exit 1; }

# ── venv + deps (skipped for --list to keep it instant) ───────────────────────
VENV="$DIR/.venv"
PY="$VENV/bin/python"
ensure_venv() {
  if [ ! -x "$PY" ]; then
    i "Creating venv at $VENV"
    python3 -m venv "$VENV" </dev/null
  fi
  # pyyaml powers the manifest parser below; the rest run the servers.
  if ! "$PY" -c 'import yaml' >/dev/null 2>&1; then
    i "Installing dependencies (mcp/requirements.txt)…"
    "$PY" -m pip install --quiet --upgrade pip </dev/null >/dev/null 2>&1 || true
    "$PY" -m pip install --quiet -r "$DIR/mcp/requirements.txt" </dev/null
  fi
}
# For --list we still need a YAML reader. Prefer the venv; fall back to system
# python3 having pyyaml; finally build the venv on demand.
manifest_py() {
  if [ -x "$PY" ] && "$PY" -c 'import yaml' >/dev/null 2>&1; then echo "$PY"; return; fi
  if python3 -c 'import yaml' >/dev/null 2>&1; then echo "python3"; return; fi
  ensure_venv; echo "$PY"
}

# ── THE manifest parser (single source of truth) ──────────────────────────────
# Emits one row per server; fields separated by the ASCII Unit Separator (US,
# 0x1f) — a non-whitespace, never-in-data delimiter that bash `read` does NOT
# coalesce, so empty fields (e.g. an http server's empty `command`) keep their
# column. List/map fields are packed as compact JSON so bash can hand them to the
# per-agent emitters without re-parsing YAML. Adding a server == editing only
# manifest.yaml. Columns:
#   name  type  command  argsJSON  envJSON  optenvJSON  url  headersJSON
US=$'\037'   # field separator used across the script
read_manifest() {  # $1 = python interpreter, $2 = manifest path, $3 = bundle dir
  "$1" - "$2" "$3" <<'PYEOF'
import json, sys, yaml
US = "\x1f"
manifest, bundle = sys.argv[1], sys.argv[2]
with open(manifest) as fh:
    doc = yaml.safe_load(fh) or {}
for s in doc.get("mcp_servers", []) or []:
    name = s.get("name", "")
    typ = s.get("type", "stdio")
    cmd = s.get("command", "") or ""
    args = s.get("args", []) or []
    env = s.get("env", {}) or {}
    optenv = s.get("optional_env", []) or []
    url = s.get("url", "") or ""
    headers = s.get("headers", {}) or {}
    row = [
        name, typ, cmd,
        json.dumps(args, separators=(",", ":")),
        json.dumps(env, separators=(",", ":")),
        json.dumps(optenv, separators=(",", ":")),
        url,
        json.dumps(headers, separators=(",", ":")),
    ]
    print(US.join(row))
PYEOF
}

# ── small JSON helpers (jq if present, else the bundle python) ────────────────
# We only ever READ tiny JSON snippets that came out of our own parser, so a
# python one-liner is plenty and avoids a hard jq dependency.
PYJSON="$(manifest_py)"
json_keys()   { "$PYJSON" -c 'import json,sys;print("\n".join(json.load(sys.stdin).keys()))'; }     # stdin=obj
json_get()    { "$PYJSON" -c 'import json,sys;print(json.load(sys.stdin).get(sys.argv[1],""))' "$1"; } # stdin=obj key
json_list()   { "$PYJSON" -c 'import json,sys;print("\n".join(json.load(sys.stdin)))'; }            # stdin=array
json_inarr()  { "$PYJSON" -c 'import json,sys;sys.exit(0 if sys.argv[1] in json.load(sys.stdin) else 1)' "$1"; }

# Render a python repr of "the env value as the agent should store it":
#   literal stays literal; "${VAR}" is rendered in the agent's expansion syntax.
# style: claude=${VAR}  opencode={env:VAR}  crush=${VAR}  (codex/goose handled separately)
render_val() {  # $1 value  $2 style
  case "$1" in
    '${'*'}')
      local var="${1#\$\{}"; var="${var%\}}"
      case "$2" in
        opencode) printf '{env:%s}' "$var" ;;
        *)        printf '${%s}' "$var" ;;
      esac ;;
    *) printf '%s' "$1" ;;
  esac
}
# Is this value a ${VAR} reference (a credential/runtime var) vs a literal?
is_ref() { case "$1" in '${'*'}') return 0 ;; *) return 1 ;; esac; }

# ── load the server table once ────────────────────────────────────────────────
MANIFEST_TSV="$(read_manifest "$PYJSON" "$MANIFEST" "$DIR")"
SERVER_COUNT="$(printf '%s\n' "$MANIFEST_TSV" | grep -c . || true)"

# ── --list ────────────────────────────────────────────────────────────────────
if [ "$DO_LIST" -eq 1 ]; then
  b "MCP servers declared in this bundle ($SERVER_COUNT)"
  while IFS="$US" read -r name typ cmd argsj envj optj url headersj; do
    [ -n "$name" ] || continue
    if [ "$typ" = "http" ]; then
      i "$name  [http]  $url"
    else
      target="$(printf '%s' "$argsj" | "$PYJSON" -c 'import json,sys;a=json.load(sys.stdin);print(a[0] if a else "")')"
      i "$name  [stdio]  $cmd $target"
    fi
    # show the credential (ref) env-var names so a client knows what to export
    refs="$(printf '%s' "$envj" | "$PYJSON" -c 'import json,sys;d=json.load(sys.stdin);print(" ".join(k for k,v in d.items() if isinstance(v,str) and v.startswith("${") and v.endswith("}")))')"
    [ -n "$refs" ] && printf '       env: %s\n' "$refs"
  done <<< "$MANIFEST_TSV"
  b "Detected agents"
  for a in claude opencode codex goose crush grok; do
    if command -v "$a" >/dev/null 2>&1; then i "$a — installed"; else i "$a — not installed"; fi
  done
  b "Next"
  i "Register into installed agents:   ./install.sh"
  i "Preview without changes:          ./install.sh --dry-run"
  i "One agent only:                   ./install.sh --agent claude"
  exit 0
fi

# ── ensure venv for the real run / dry-run that reports the python path ───────
ensure_venv
PY="$VENV/bin/python"   # now guaranteed
PYJSON="$PY"

want_agent() { [ -z "$ONLY_AGENT" ] || [ "$ONLY_AGENT" = "$1" ]; }

if [ "$DO_DRYRUN" -eq 1 ]; then
  b "DRY RUN — nothing will be changed"
  i "Python for servers: $PY"
fi

ADDED=0; SKIPPED=0

# Per-server iteration with a callback name. Keeps the per-agent code small.
for_each_server() {  # $1 = function to call per server
  while IFS="$US" read -r name typ cmd argsj envj optj url headersj; do
    [ -n "$name" ] || continue
    "$1" "$name" "$typ" "$cmd" "$argsj" "$envj" "$optj" "$url" "$headersj"
  done <<< "$MANIFEST_TSV"
}

# Absolute server script path from command+args (args[0] is mcp/<file>.py).
abs_target() {  # $1 argsj
  local rel; rel="$(printf '%s' "$1" | "$PYJSON" -c 'import json,sys;a=json.load(sys.stdin);print(a[0] if a else "")')"
  case "$rel" in /*) printf '%s' "$rel" ;; *) printf '%s/%s' "$DIR" "$rel" ;; esac
}

# ═══════════════════════════════════════════════════════════════════════════════
#  CLAUDE CODE  — claude mcp add / get  (CLI, idempotent via `get`)
# ═══════════════════════════════════════════════════════════════════════════════
reg_claude_one() {
  local name="$1" typ="$2" cmd="$3" argsj="$4" envj="$5" optj="$6" url="$7" headersj="$8"
  if claude mcp get "$name" >/dev/null 2>&1; then
    i "  $name: already configured — skipped"; SKIPPED=$((SKIPPED+1)); return
  fi
  local -a add
  if [ "$typ" = "http" ]; then
    # The URL is positional and must come right after the name; -H flags go LAST
    # (verified against claude v2.1.185 — flags before the URL break the parser).
    add=(claude mcp add "$name" "$url" -t http -s "$CLAUDE_SCOPE")
    # headers: -H "Name: value" with ${VAR} kept verbatim (Claude expands at launch)
    while IFS= read -r hk; do
      [ -n "$hk" ] || continue
      local hv; hv="$(printf '%s' "$headersj" | json_get "$hk")"
      add+=(-H "$hk: $(render_val "$hv" claude)")
    done < <(printf '%s' "$headersj" | json_keys)
  else
    add=(claude mcp add "$name" -s "$CLAUDE_SCOPE")
    # env: -e KEY=VALUE  (refs rendered as ${VAR} → stored verbatim, never baked)
    while IFS= read -r k; do
      [ -n "$k" ] || continue
      local v; v="$(printf '%s' "$envj" | json_get "$k")"
      add+=(-e "$k=$(render_val "$v" claude)")
    done < <(printf '%s' "$envj" | json_keys)
    local tgt; tgt="$(abs_target "$argsj")"
    add+=(-- "$cmd" "$tgt")
  fi
  if [ "$DO_DRYRUN" -eq 1 ]; then
    i "  would: ${add[*]}"; return
  fi
  if "${add[@]}" </dev/null >/dev/null 2>&1; then
    i "  $name: added"; ADDED=$((ADDED+1))
  else
    warn "  $name: 'claude mcp add' failed — see manual steps in INSTALL.md"
  fi
}
register_claude() {
  command -v claude >/dev/null 2>&1 || { i "Claude Code: not installed — skipped"; return; }
  case "$CLAUDE_SCOPE" in local|project|user) ;; *) err "invalid --scope: $CLAUDE_SCOPE"; exit 2 ;; esac
  b "Claude Code  (scope: $CLAUDE_SCOPE)"
  for_each_server reg_claude_one
}

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIG-FILE AGENTS  — built as JSON/TOML/YAML by a single python emitter that
#  merges into any existing file (idempotent, never clobbers other servers).
# ═══════════════════════════════════════════════════════════════════════════════
# One python program handles opencode/crush/codex/goose. It receives the server
# table on stdin (the same TSV) plus the target agent + file, merges, and writes.
# The merge program is large; it is fed to python for each agent. (Kept inline so
# install.sh stays a single file.)
config_merge() {  # $1 agent  $2 target_file  ; TSV passed via $MANIFEST_TSV env
  local agent="$1" target="$2" dry="0"
  [ "$DO_DRYRUN" -eq 1 ] && dry="1"
  # The heredoc IS the program (on stdin); the server table is handed over as an
  # env var so it doesn't fight the heredoc for python's stdin.
  MANIFEST_TSV="$MANIFEST_TSV" "$PY" - "$agent" "$target" "$DIR" "$dry" <<'PYEOF'
import json, os, re, sys

agent, target, bundle, dry = sys.argv[1], sys.argv[2], sys.argv[3], (sys.argv[4] == "1")

rows = []
for line in os.environ.get("MANIFEST_TSV", "").splitlines():
    if not line.strip():
        continue
    name, typ, cmd, argsj, envj, optj, url, headersj = line.split("\x1f")
    rows.append(dict(
        name=name, typ=typ, cmd=cmd,
        args=json.loads(argsj), env=json.loads(envj),
        optenv=json.loads(optj), url=url, headers=json.loads(headersj),
    ))

def abs_target(r):
    rel = r["args"][0] if r["args"] else ""
    return rel if os.path.isabs(rel) else os.path.join(bundle, rel)

REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

def is_ref(v):
    # A whole-value reference, e.g. "${EXAMPLE_API_KEY}" (manifest env values).
    return isinstance(v, str) and bool(re.fullmatch(REF_RE, v))

def ref_name(v):
    return v[2:-1]

def subst(value, style):
    # Rewrite every embedded ${VAR} into the target agent's expansion syntax.
    # Handles composite values like a header "Bearer ${TOKEN}". opencode uses
    # {env:VAR}; crush/claude keep ${VAR} (native shell-style expansion).
    if not isinstance(value, str):
        return value
    if style == "opencode":
        return REF_RE.sub(lambda m: "{env:%s}" % m.group(1), value)
    return value  # ${VAR} already native for crush/claude

def ref_names_in(value):
    # Every distinct ${VAR} name embedded in a (possibly composite) string.
    return list(dict.fromkeys(REF_RE.findall(value))) if isinstance(value, str) else []

added, skipped = [], []

# ---- opencode.json : { "mcp": { name: {type:"local"|"remote", ...} } } -------
def do_opencode(cfg):
    block = cfg.setdefault("mcp", {})
    cfg.setdefault("$schema", "https://opencode.ai/config.json")
    for r in rows:
        if r["name"] in block:
            skipped.append(r["name"]); continue
        if r["typ"] == "http":
            # {env:VAR} substitution works in remote url/headers
            headers = {k: subst(v, "opencode") for k, v in r["headers"].items()}
            block[r["name"]] = {"type": "remote", "url": r["url"],
                                "enabled": True, "headers": headers}
        else:
            environment = {k: (("{env:%s}" % ref_name(v)) if is_ref(v) else v)
                           for k, v in r["env"].items()}
            block[r["name"]] = {"type": "local",
                                "command": [r["cmd"], abs_target(r)],
                                "enabled": True, "environment": environment}
        added.append(r["name"])
    return cfg

# ---- crush.json : { "mcp": { name: {type:"stdio", command, args, env} } } ----
def do_crush(cfg):
    block = cfg.setdefault("mcp", {})
    cfg.setdefault("$schema", "https://charm.land/crush.json")
    for r in rows:
        if r["name"] in block:
            skipped.append(r["name"]); continue
        if r["typ"] == "http":
            headers = {k: (("${%s}" % ref_name(v)) if is_ref(v) else v)
                       for k, v in r["headers"].items()}
            block[r["name"]] = {"type": "http", "url": r["url"],
                                "headers": headers, "timeout": 1800}
        else:
            env = {k: (("${%s}" % ref_name(v)) if is_ref(v) else v)
                   for k, v in r["env"].items()}
            block[r["name"]] = {"type": "stdio", "command": r["cmd"],
                                "args": [abs_target(r)], "env": env,
                                "timeout": 1800000}  # ms
        added.append(r["name"])
    return cfg

# ---- goose config.yaml : extensions.<name> ----------------------------------
def do_goose(cfg):
    block = cfg.setdefault("extensions", {})
    for r in rows:
        if r["name"] in block:
            skipped.append(r["name"]); continue
        if r["typ"] == "http":
            hdr_keys = []
            for v in r["headers"].values():
                hdr_keys.extend(ref_names_in(v))
            block[r["name"]] = {
                "name": r["name"], "type": "streamable_http",
                "uri": r["url"], "enabled": True, "timeout": 300,
                "env_keys": list(dict.fromkeys(hdr_keys)),
            }
        else:
            envs = {k: v for k, v in r["env"].items() if not is_ref(v)}      # literals
            env_keys = [k for k, v in r["env"].items() if is_ref(v)]          # secret names
            block[r["name"]] = {
                "name": r["name"], "type": "stdio",
                "cmd": r["cmd"], "args": [abs_target(r)],
                "enabled": True, "envs": envs, "env_keys": env_keys,
                "timeout": 300,
            }
        added.append(r["name"])
    return cfg

# ---- codex config.toml : [mcp_servers.<name>] (hand-rendered TOML) -----------
def render_codex(existing_text):
    # Parse just enough to know which server tables already exist; never rewrite
    # the user's other content — append only the servers we don't see.
    present = set(re.findall(r"^\s*\[mcp_servers\.([A-Za-z0-9_-]+)\]", existing_text, re.M))
    out = []
    for r in rows:
        if r["name"] in present:
            skipped.append(r["name"]); continue
        added.append(r["name"])
        if r["typ"] == "http":
            # Codex stdio is primary; HTTP servers use url + bearer_token_env_var
            # (Codex resolves the token from that env var name at launch).
            out.append('[mcp_servers.%s]' % r["name"])
            out.append('url = %s' % json.dumps(r["url"]))
            tok = []
            for v in r["headers"].values():
                tok.extend(ref_names_in(v))
            if tok:
                out.append('bearer_token_env_var = %s' % json.dumps(tok[0]))
            out.append("")
            continue
        out.append('[mcp_servers.%s]' % r["name"])
        out.append('command = %s' % json.dumps(r["cmd"]))
        out.append('args = [%s]' % ", ".join(json.dumps(a) for a in [abs_target(r)]))
        out.append('startup_timeout_sec = 30')
        out.append('tool_timeout_sec = 1800')
        # forward credential vars by NAME (never write the value)
        refs = [ref_name(v) for v in r["env"].values() if is_ref(v)]
        if refs:
            out.append('env_vars = [%s]' % ", ".join(json.dumps(x) for x in refs))
        # literal (non-secret) values go in an [env] sub-table
        lits = {k: v for k, v in r["env"].items() if not is_ref(v)}
        if lits:
            out.append('[mcp_servers.%s.env]' % r["name"])
            for k, v in lits.items():
                out.append('%s = %s' % (k, json.dumps(v)))
        out.append("")
    block = "\n".join(out)
    if not block.strip():
        return existing_text  # nothing new
    sep = "" if existing_text.endswith("\n") or not existing_text else "\n"
    return existing_text + sep + block + "\n"

# ---- dispatch ----------------------------------------------------------------
def load_json(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (ValueError, OSError):
            return {}
    return {}

def load_yaml(path):
    import yaml
    if os.path.exists(path):
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError):
            return {}
    return {}

if agent == "codex":
    existing = ""
    if os.path.exists(target):
        with open(target) as f:
            existing = f.read()
    new_text = render_codex(existing)
    if not dry:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write(new_text)
elif agent == "goose":
    import yaml
    cfg = do_goose(load_yaml(target))
    if not dry:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)
else:
    cfg = load_json(target)
    cfg = do_opencode(cfg) if agent == "opencode" else do_crush(cfg)
    if not dry:
        d = os.path.dirname(target)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(target, "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")

prefix = "would add" if dry else "added"
for n in added:
    print("ADD\t%s" % n)
for n in skipped:
    print("SKIP\t%s" % n)
PYEOF
}

# Run a config-file agent: detect, pick its file, merge, summarize.
run_config_agent() {  # $1 agent  $2 detect_cmd  $3 target_file  $4 human_label
  local agent="$1" detect="$2" target="$3" label="$4"
  if [ -n "$detect" ] && ! command -v "$detect" >/dev/null 2>&1; then
    i "$label: not installed — skipped"; return
  fi
  b "$label"
  [ "$DO_DRYRUN" -eq 1 ] && i "  target: $target"
  local out; out="$(config_merge "$agent" "$target")"
  while IFS=$'\t' read -r verb nm; do
    case "$verb" in
      ADD)  if [ "$DO_DRYRUN" -eq 1 ]; then i "  would add: $nm"; else i "  $nm: added"; fi; ADDED=$((ADDED+1)) ;;
      SKIP) i "  $nm: already configured — skipped"; SKIPPED=$((SKIPPED+1)) ;;
    esac
  done <<< "$out"
}

# ═══════════════════════════════════════════════════════════════════════════════
#  GROK BUILD  — grok mcp add (CLI; one -a per arg)
# ═══════════════════════════════════════════════════════════════════════════════
reg_grok_one() {
  local name="$1" typ="$2" cmd="$3" argsj="$4" envj="$5" optj="$6" url="$7" headersj="$8"
  if grok mcp get "$name" >/dev/null 2>&1; then
    i "  $name: already configured — skipped"; SKIPPED=$((SKIPPED+1)); return
  fi
  if [ "$typ" = "http" ]; then
    i "  $name: http server — add manually (see INSTALL.md)"; return
  fi
  local tgt; tgt="$(abs_target "$argsj")"
  local -a add=(grok mcp add "$name" -t stdio -c "$cmd" -a "$tgt")
  if [ "$DO_DRYRUN" -eq 1 ]; then i "  would: ${add[*]}"; return; fi
  if "${add[@]}" </dev/null >/dev/null 2>&1; then i "  $name: added"; ADDED=$((ADDED+1));
  else warn "  $name: 'grok mcp add' failed — configure manually"; fi
}
register_grok() {
  command -v grok >/dev/null 2>&1 || { i "Grok Build: not installed — skipped"; return; }
  b "Grok Build"
  for_each_server reg_grok_one
}

# ── credential hint ───────────────────────────────────────────────────────────
b "Credentials"
i "Servers register WITHOUT secrets — each agent resolves credential env vars from"
i "its own launch environment. Export the vars this bundle's servers need before"
i "launching the agent (run './install.sh --list' to see each server's env names),"
i "e.g.  export EXAMPLE_API_KEY=...   export EXAMPLE_API_BASE_URL=...  (see INSTALL.md)."

# ── run ────────────────────────────────────────────────────────────────────────
want_agent claude   && register_claude
want_agent opencode && run_config_agent opencode opencode "$PWD/opencode.json" "opencode  ($PWD/opencode.json)"
want_agent codex    && run_config_agent codex    codex    "$HOME/.codex/config.toml" "Codex CLI  (~/.codex/config.toml)"
want_agent goose    && run_config_agent goose    goose    "$HOME/.config/goose/config.yaml" "Goose  (~/.config/goose/config.yaml)"
want_agent crush    && run_config_agent crush    crush    "$PWD/crush.json" "Crush  ($PWD/crush.json)"
want_agent grok     && register_grok

# ── smoke test: import the first stdio server module from the venv ────────────
if [ "$DO_DRYRUN" -eq 0 ]; then
  b "Smoke test"
  # Pull the first stdio server's script path (args[0]) straight from the table.
  FIRST_TARGET="$(MANIFEST_TSV="$MANIFEST_TSV" "$PY" - <<'PYEOF'
import json, os
for line in os.environ.get("MANIFEST_TSV", "").splitlines():
    if not line.strip():
        continue
    f = line.split("\x1f")
    if f[1] == "stdio":
        args = json.loads(f[3])
        print(args[0] if args else "")
        break
PYEOF
)"
  if [ -n "$FIRST_TARGET" ] && [ -f "$DIR/$FIRST_TARGET" ]; then
    modname="$(basename "$FIRST_TARGET" .py)"
    if ( cd "$DIR/mcp" && "$PY" -c "import importlib; importlib.import_module('$modname')" ) >/dev/null 2>&1; then
      i "Imported $modname from the venv — Python deps OK"
    else
      warn "Could not import $modname — run: (cd $DIR/mcp && $PY -c 'import $modname') to see the error"
    fi
  fi
fi

# ── summary ────────────────────────────────────────────────────────────────────
b "Done"
if [ "$DO_DRYRUN" -eq 1 ]; then
  i "Dry run complete — $SERVER_COUNT servers in manifest. Re-run without --dry-run to apply."
else
  i "Newly registered: $ADDED   Already present (skipped): $SKIPPED"
  i "Verify in an agent by listing its tools, or see INSTALL.md to test a server directly."
fi

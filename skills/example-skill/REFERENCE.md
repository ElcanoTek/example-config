# Example Skill — reference

This is the **Level 2 reference file** for the [`example-skill`](SKILL.md)
template. `SKILL.md` keeps the procedure tight and points here for the long-form
detail. This is exactly the pattern your own skills should follow: a focused
`SKILL.md`, with depth pushed into a sibling file the agent reads only when it
needs it.

## The Agent Skills standard

Skills follow the open **Agent Skills** standard:
<https://github.com/anthropics/skills>. fleet implements that format, so a skill
authored for this bundle is portable to any tool that speaks it, and skills
written for that ecosystem drop into this bundle's `skills/` directory unchanged.

## Frontmatter, field by field

| Field         | Required | Constraint                                                                                  |
|---------------|----------|---------------------------------------------------------------------------------------------|
| `name`        | yes      | Must equal the folder name exactly. Lowercase letters, digits, hyphens only; ≤ 64 chars; must not contain "claude" or "anthropic". |
| `description` | yes      | Non-empty; ≤ 1024 chars. The only text shown in the system-prompt roster — state what it does AND when to use it. |
| `allowed-tools` | no     | Allowed and ignored by the loader. **Advisory metadata, not an enforced authorization gate** — do not rely on it as a security boundary. |
| `license`     | no       | Allowed and ignored.                                                                        |
| `metadata`    | no       | Allowed and ignored; a free-form map for your own tooling.                                  |

The frontmatter block (the opening `---`) must be the **first thing in the
file** — no blank lines, comments, or BOM before it, or the loader will not
recognize the file as a skill.

## Why each level exists

- **Level 1 (description).** The roster lists every skill's description and
  nothing else. The model decides whether a skill is relevant from this line
  alone, so it carries the whole trigger. "Profiles a CSV with the standard
  library" earns its place; "CSV helper" does not.
- **Level 2 (`SKILL.md` + reference files).** Loaded only once the skill is in
  play. Keeping `SKILL.md` short means the agent spends its context on the steps
  it is about to run, not on material it may never need. Reference files like
  this one hold the catalog of detail.
- **Level 3 (scripts).** Deterministic logic belongs in code the agent *runs*,
  not in prose it re-derives. A script gives the same answer every time, costs no
  model tokens to execute, and is independently testable. Keep scripts to the
  Python standard library so they run in the sandbox with no install step.

## A skill versus a protocol

Both encode "the way we do this here." Reach for a **protocol**
([`protocols/`](../../protocols/example.md)) when a single Markdown playbook says
it all. Reach for a **skill** when the capability also wants to ship code or
reference files beside the instructions — a validator, a transform, a lookup
table — so the agent runs a known-good implementation instead of improvising one.

## Common mistakes that get a skill dropped or ignored

- `name` does not match the folder name → dropped by the loader.
- `name` contains uppercase, spaces, underscores, or a forbidden word → dropped.
- Empty or missing `description` → dropped (and nothing for the model to match).
- Frontmatter not at the very top of the file → not recognized as a skill.
- A vague `description` → the skill loads but the model never knows to use it.
- A script named `test_*.py` / `*_test.py` → the test runner tries to collect it.

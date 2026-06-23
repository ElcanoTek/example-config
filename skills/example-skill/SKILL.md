---
name: example-skill
description: Annotated template that teaches how to write an Agent Skill for this bundle — the format, the frontmatter rules, and the three levels of progressive disclosure. Use when authoring a new skill, or when you want a known-good skeleton (with a bundled reference file and a runnable script) to copy and adapt.
---

# Example Skill

A **skill** is a reusable capability this bundle ships under `skills/`. It is the
packaged sibling of a [protocol](../../protocols/example.md): where a protocol is
a single Markdown playbook, a skill is a *folder* that can also bundle reference
documents and runnable scripts alongside its instructions.

This file is both a working example and the **template** for writing your own.
Copy the folder, rename it, and replace the body. Keep skills generic and
genuinely reusable — a good skill reads like a capability a careful teammate
would hand off, not a script bolted to one dataset.

## Anatomy of a skill

A skill is a folder `skills/<skill-name>/` containing a `SKILL.md`, plus any
sibling reference files and a `scripts/` directory:

```
skills/
  example-skill/
    SKILL.md         # this file — frontmatter + instructions
    REFERENCE.md     # long-form detail, loaded on demand (Level 2)
    scripts/
      greet.py       # a deterministic stdlib script, run on demand (Level 3)
```

`SKILL.md` begins with **YAML frontmatter** fenced by `---` lines, and the
frontmatter must be the very first thing in the file:

```
---
name: example-skill
description: <what it does AND when to use it — one or two sentences>
---
# Title
... Markdown instructions ...
```

### Frontmatter rules (the loader drops a skill that breaks them)

fleet's loader (`clientconfig.ReadSkills`) enforces these. A skill that violates
any of them is silently dropped from the prompt:

- **`name` must equal the folder name exactly.** This skill lives in
  `skills/example-skill/`, so `name: example-skill`.
- **`name` must be lowercase letters, digits, and hyphens only**, at most 64
  characters, and must not contain the words "claude" or "anthropic".
- **`description` must be non-empty and at most 1024 characters.** It is the
  *only* text shown in the system-prompt roster, so make it specific: say both
  **what** the skill does and **when** to reach for it. That sentence is what the
  agent matches a request against.

Extra frontmatter fields (`allowed-tools`, `license`, `metadata`) are allowed and
ignored. Note that `allowed-tools` is **not** an enforced authorization gate in
fleet — it is advisory metadata only, not a security boundary. Govern
consequential tools through the manifest's `agent_policy` instead.

## Progressive disclosure — the core idea

A skill keeps the model's context lean by revealing detail in three levels, only
as needed:

1. **Level 1 — the description** (always loaded). One or two sentences in the
   frontmatter `description`. This is all the agent sees until the skill is
   relevant; it is the trigger.
2. **Level 2 — `SKILL.md` body + reference files** (loaded when the skill is
   used). Keep this file focused on the procedure. Push long lookup tables,
   edge-case catalogs, and deep explanation into sibling `.md` files and tell the
   agent to read them on demand. This skill keeps its detailed format notes in
   [`REFERENCE.md`](REFERENCE.md) — read it when you need the full field-by-field
   rules or worked examples.
3. **Level 3 — scripts** (run, not read into context). Push deterministic
   operations into a script the body tells the agent to *run*, so the logic
   executes instead of the model re-deriving it token by token. This skill ships
   [`scripts/greet.py`](scripts/greet.py), a tiny standard-library demo:

   ```bash
   python3 skills/example-skill/scripts/greet.py "Aria"
   ```

   It prints a deterministic greeting and exits — a stand-in for the real work a
   skill's script does (a validator, a transform, a checklist generator). Scripts
   in this bundle use the Python **standard library only**, so they run in the
   sandbox without extra dependencies.

## When to use

- When you are **authoring a new skill** and want a correct skeleton to copy.
- When you need a quick refresher on the **frontmatter rules** or what
  progressive disclosure means in practice — read [`REFERENCE.md`](REFERENCE.md)
  for the full detail.
- For a real, useful skill (not just a template), see the sibling
  [`csv-profiler`](../csv-profiler/SKILL.md) skill.

## Writing your own — the checklist

1. **Copy this folder** to `skills/<your-name>/` and rename it. Set
   `name:` to match the new folder exactly.
2. **Write a specific `description`** — what it does *and* when to use it. This is
   the one line the agent sees in the roster; vague descriptions never fire.
3. **Keep `SKILL.md` to the procedure.** Move long reference material into a
   sibling `.md` file and tell the agent to read it on demand.
4. **Push deterministic work into a script** under `scripts/`. Standard library
   only; do not name it like a test (no `test_*.py` / `*_test.py`) or the test
   runner will try to collect it.
5. **Validate the frontmatter** against the rules above before you finish — name
   equals folder, lowercase-hyphen, non-empty specific description.

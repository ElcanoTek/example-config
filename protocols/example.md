# Example Protocol

A **protocol** is a reusable playbook this bundle ships under `protocols/`. The
agent reads one on demand — when the user names it, or when it is clearly
relevant to the request — and follows its steps. Protocols are how you encode
"the way we do this here" once and have every agent run it the same way, whether
the run is an interactive chat or a scheduled task.

This file is both a working example you can run and the **template** for writing
your own. Copy it, rename it, and replace the body. Keep protocols generic and
genuinely reusable: a good protocol reads like a checklist a careful teammate
would follow, not a script bolted to one dataset.

## Anatomy of a protocol

A protocol is plain Markdown (a `.yaml` form is also supported for highly
structured playbooks). The shape every protocol in this bundle follows:

- **Title** (`# Name Protocol`) — what it produces, in a few words.
- **Usage** — one or two lines showing how a user invokes it, with a concrete
  example. This is what the agent matches against.
- **Inputs** — a small table of parameters: name, where the value comes from
  (user text, an attachment, a default), and what it means.
- **Steps** — numbered, in order. Each step is one coherent unit of work and
  names the tool it uses (`run_python`, `WebSearch`, `mcp_knowledge_base_kb_search`, …).
- **Validation** — the checks that must pass before the result is delivered, and
  what to do when one fails (flag it; never paper over a gap).

## When to use

When the user asks to "run the example protocol", or when you are writing a new
protocol and want a known-good skeleton to start from.

## Inputs

| Parameter | Source         | Description                                              |
|-----------|----------------|----------------------------------------------------------|
| goal      | User task text | What the user wants this run to accomplish, in one line. |
| inputs    | User / context | Any files, links, or facts the task needs to proceed.    |

## Steps

1. **Restate the goal** in one sentence so the user can correct a
   misunderstanding before any work happens.
2. **Gather inputs.** Read attached files, follow given links, and pull from the
   knowledge base or APIs as needed. Ask only for what you genuinely cannot
   infer — don't interrogate the user for things already in context.
3. **Do the work with your tools.** Run Python in the sandbox to compute on data,
   search the web for facts, draft the artifact. Do the task; don't just
   describe how it could be done.
4. **Validate.** Re-read the goal and confirm the output actually answers it.
   Check that every claim is grounded in a tool result, source, or stated
   assumption — nothing fabricated.
5. **Deliver.** Lead with the answer. Put detail and caveats below it, and end
   with a clear next step or a specific question if one is needed.

## Validation

- The output addresses the stated goal, not an adjacent one.
- Every factual claim is grounded (tool result, cited source, or stated assumption).
- Assumptions made under ambiguity are written down, not hidden.
- Any gap, failed tool call, or missing input is flagged plainly.

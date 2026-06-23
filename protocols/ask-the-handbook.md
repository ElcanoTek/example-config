# Ask the Handbook Protocol

Answer a question about a policy, process, or "how we do things here" using the
team knowledge base — and cite the article the answer came from. The contract is
strict: **ground the answer in the handbook, or say you don't know.** Never
fill a gap with a plausible-sounding guess about company policy.

## Usage

```
Ask the handbook: <question>
```

**Example:**
```
Ask the handbook: how much notice do I need to give to request time off?
```

## Inputs

| Parameter | Source         | Description                                                          |
|-----------|----------------|----------------------------------------------------------------------|
| question  | User task text | The policy/process question to answer from the knowledge base.       |

This protocol uses the always-on `knowledge_base` MCP server (no credentials
required). Its tools: `mcp_knowledge_base_kb_search`,
`mcp_knowledge_base_kb_get_article`, and `mcp_knowledge_base_kb_list_categories`.

## Step 1 — Search the knowledge base

- Call `mcp_knowledge_base_kb_search` with the user's question phrased as search
  terms. It returns ranked `{id, title, category, snippet, score}` results.
- If results are thin or off-target, try a second search with reworded terms or
  synonyms. If you're unsure what the handbook even covers, call
  `mcp_knowledge_base_kb_list_categories` first to orient.
- If a tool returns an object with an `error` field, surface that to the user
  (the handbook couldn't be loaded) rather than guessing — and stop.

## Step 2 — Read the source article

- Take the most relevant result's `id` and call
  `mcp_knowledge_base_kb_get_article` to fetch the full article. **Don't answer
  from a search snippet** — snippets are truncated and can mislead.
- If the question spans topics, fetch the two or three articles that cover it.
- If the best fetch turns out not to actually address the question, go back to
  Step 1 with different terms before concluding it's unknown.

## Step 3 — Answer with a citation

- Answer directly from the article's text, in your own words, leading with the
  specific answer to the specific question.
- **Cite the article** by title (and id) so the user can verify, e.g.
  *Source: "Requesting Time Off" (`requesting-time-off`).*
- Quote the handbook verbatim for anything precise — numbers, deadlines, dollar
  amounts, named approvers — rather than paraphrasing it loosely.
- If the article only partially answers the question, answer the part it covers
  and say plainly which part it doesn't.

## Step 4 — Handle "not in the handbook"

If no article answers the question after a genuine search:

- Say so directly: *"I couldn't find this in the team knowledge base."*
- Optionally name the closest categories or articles you did find, so the user
  knows where the gap is.
- You may add clearly-labeled general guidance, but never present it as company
  policy. Suggest who or where to check next (the relevant team, an owner).
- **Do not invent a policy, number, or process.** An honest "not found" is the
  correct answer here.

## Validation

Before delivering, verify:

- The answer is grounded in an article actually fetched via
  `mcp_knowledge_base_kb_get_article`, not a snippet or memory.
- A specific source (title + id) is cited for every policy claim.
- Precise figures/deadlines are quoted, not paraphrased into something looser.
- If nothing matched, the response says "not found" plainly and offers a next
  step — with zero fabricated policy.

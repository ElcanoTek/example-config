# email_templates/ — the fixed shape of the emails you send again and again

An email template is the sections of a recurring email, in order, with what
belongs in each and how long it may be. This bundle ships none: the team makes
its own from a chat (see **Team templates**), and a template that proves itself
can be added here as a `.yaml` file (see **Shipping a template in the bundle**).

A template is used in three places, all reading the same file:

- **The renderer enforces it.** `render_email(template="<id>")` checks the
  blocks against the template before rendering. Blocks that are out of order,
  too long, missing a required section or using a banned phrase are refused,
  with every problem listed at once (`mcp/email_templates.py`).
- **The agent reads it.** `list_email_templates` and `get_email_template` on
  the `ses_outbound` server return the catalog and one template's rules. The
  sample is never returned: example numbers handed to a writer end up in real
  emails.
- **People see it.** fleet's Email templates library (Chat, the composer's
  `…` menu) lists the templates, shows each section's rules beside its example,
  and inserts the template's prompt into the chat. The library entry appears
  once there is at least one template.

Without `template`, `render_email` behaves exactly as before.

## Team templates

A person adds a template from a chat: "save this email as a template called
Monthly QBR recap". The assistant writes the template from the email it just
built, checks it (`check_email_template`), and calls `save_email_template`,
which fleet stages on an approval card. Once approved it is:

- stored on the deployment, in the directory `EMAIL_TEMPLATES_TEAM_DIR` names
  (`mcp/email_template_store.py`), with its example rendered from its own
  sample and the name of whoever saved it;
- listed in fleet's Email templates library for everyone, marked Team;
- enforced by `render_email(template=<id>)`.

Nothing is saved unless the sample fits the template and renders cleanly.
Saving an existing team id replaces it, and a team template cannot take the id
of a template in this directory. `delete_email_template` removes one, also
behind a card. Neither works from a scheduled run.

A template saved without a form gets the standard one: who or what the email is
about, **Send to**, **Style**, and notes.

## File format

```yaml
name: "Weekly optimization update"       # shown in the library
description: "One line for the catalog."
audience: client                         # client | internal
use_when: "When this template is the right one."
based_on: "Where the shape came from."
theme: default                           # optional; a theme id from protocols/email_styles/themes/
max_words: 260                           # optional; prose in the whole email (cards, tables, captions not counted)
subject: {instruction: "...", max_chars: 70}
header: {headline: "...", status: "..."} # optional; how to fill render_email's headline and status strip
rules: ["Writing rules, shown to the writer and in the library."]
never_say: ["a phrase", "re:\\bID-\\d{3,}\\b"]    # enforced; `re:` marks a regular expression
sections:
  - id: summary                          # what a block names in "section"
    title: Summary                       # shown in the library
    heading: Summary                     # optional; set on the block for the writer ("" removes it, except on metrics and callouts)
    block: text                          # a render_email block type
    kind: opportunity                    # callout sections only: risk | opportunity
    required: true                       # default true; false = leave out when there is nothing true to say
    instruction: "What goes here."
    limits: {max_paragraphs: 2, max_bullets: 3, max_words_per_bullet: 28, max_words: 100}
sample: {subject: ..., client_name: ..., blocks: [...]}   # render_email arguments for the preview
fields: [...]                            # optional form, same schema as prompts/ form prompts
promptTemplate: "..."                    # what the library inserts into the chat
```

Limits by block type: `text` and `callout` take `max_words`, `max_paragraphs`,
`max_bullets`, `min_bullets`, `max_words_per_bullet`; `closing` takes
`max_words` and `max_paragraphs`; `headline_metrics` and `metrics` take
`min_items` and `max_items`; `table` takes `max_rows` and `max_columns`. A
`max_` limit of `0` means "none" (for example `max_paragraphs: 0` for a
bullets-only section).

## The form

The standard form asks three things besides what the email is about, and its
`promptTemplate` ends the same way every time:

- **Send to** (`type: recipients`): the library offers the addresses the
  person's tasks already mail and the ones they added before. Do not list
  people's addresses in `options`; the suggestions are per person.
- **Style** (`type: palette`): `options` are this bundle's email themes
  (`protocols/email_styles/themes/`, the default one first and preselected),
  `swatches` are each theme's real header and accent colors, and `custom:
  [Header, Accent]` lets the person pick other colors instead. The prompt tells
  the assistant to pass a theme name as `theme` and custom colors as
  `colors={"header": ..., "accent": ...}`; `render_email` refuses a color too
  light for the white text on it.
- The last line sends the finished email straight to its approval card: the
  assistant calls `send_email` as soon as the email is built, fleet stages the
  call on a card that shows the email and its recipients, and nothing goes out
  until the person chooses Send.

The library shows the chosen look on the example as the person picks it. The
example is rendered in the plain palette (the theme with no overrides,
`default.yaml`), and `previews/styles.json` says which of its colors become
which for each look; a test checks the recolored example is exactly what
`render_email` produces. Keep a theme with no overrides in the bundle.

## Shipping a template in the bundle

1. Add `<id>.yaml` here in the format above. The easiest source is a team
   template: copy its file from the deployment's
   `mcp-runs/email-templates/` directory, drop `created_by`, `created_at` and
   `updated_at`, and replace real names and numbers in the sample with demo
   data.
2. Render the examples: `python mcp/email_template_preview.py` (writes
   `previews/<id>.html` and `previews/styles.json`; commit them).
3. Run `pytest mcp/tests/test_email_templates.py`. It validates every file here
   and fails on a stale example.

A template is trusted bundle content, like a protocol. It grants no tools or
permissions, and sending still goes through `send_email`'s approval.

# Prompt library

Files in this directory appear as read-only, Git-backed entries in Fleet's
Prompt Library in both Chat and Operations Center. Fleet accepts `.yaml`,
`.yml`, `.md`, and `.txt` files and inserts the exact file body into the draft.

For YAML, use top-level `name` plus optional `description` or `goal` fields to
make the catalog easy to browse. For Markdown, use a level-one heading followed
by a short introductory paragraph. Keep credentials and customer secrets out of
prompt files; this directory is intended to be committed and reviewed.

People who do not work in Git can create private or workspace-shared prompts in
the same picker and download a JSON backup.

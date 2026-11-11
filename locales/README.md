# Interface translations

`es.json` and `pt.json` translate this bundle's presentation copy into Spanish
and Portuguese. English remains in the manifest, prompt metadata and skill
metadata and acts as the catalog key. Fleet's language selector appears on the
sign-in screen and under **Settings → General → Language**; it remembers the
choice in this browser.

These catalogs cover branding, support text, connector and plugin labels,
quick-start cards, form labels and option labels, task templates, prompt-library
metadata and prepared prompts, and skill descriptions. Keep tool names, paths,
URLs, account identifiers, form option values, and every `{placeholder}` unchanged.
Raw protocol/prompt source files remain verbatim for editing and export. A locale
changes the interface and prepared draft copy; it does not rewrite saved chats,
user-authored records or the agent's operating instructions.

Use a Fleet release that supports optional `locales/es.json` and `locales/pt.json`
bundle catalogs. Older Fleet releases ignore these files and continue displaying
English; there are no new manifest fields or environment variables. Kubernetes
control-plane images must copy `locales/` to `/opt/fleet/client/locales/`.

When owned UI copy changes, update both catalogs in this repository. The offline
`mcp/tests/test_localizations.py` check rejects missing/stale messages, blank
translations, duplicate keys, changed placeholders and changed code/link targets.
Each client bundle owns its translations independently.

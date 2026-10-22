---
name: pages-dashboards
description: Create, publish and update dashboards and pages on this deployment's Pages. Use for any request to put a dashboard, report or content page on Pages, change one that is live, reuse a design for another page, add charts or an animated recap, or set up a recurring refresh. Read Pages' own guide with mcp_pages_get_guide before building, then use this deployment's sources and approval rules below.
---

# Pages dashboards

Pages hosts versioned dashboards and content pages at stable links. This
deployment has its own Pages instance; nothing here reaches any other
organisation's.

## Start with Pages' own guide

Pages ships its instructions, matched to the version this deployment runs.
Before creating, reusing or changing a page:

1. `mcp_pages_get_guide` with no arguments, for the index.
2. `mcp_pages_get_guide` with `topic="workflow"` and the section for the step
   you are on (for example `choosing-the-route-for-a-new-page`,
   `sources-prove-the-data-before-you-build`, `changing-a-live-page`,
   `recurring-refreshes`, `before-you-say-it-is-done`).

Read only the section you need. The guide decides how Pages works; this skill
only adds what is specific to this deployment.

## Where the data comes from here

Bind each input to a connector this deployment actually has, and name the
server and tool in any refresh you prepare:

- **Email reports:** the `email` connector reads this deployment's inbound
  report archive (search, open, download attachments).
- **Files the user supplies** in the conversation, and files already in the
  workspace.
- **Lens results:** the `lens` connector's job results and downloads, when a
  page reports on site, app or CTV classification.

That is the whole list in the template. When a client fork adds its own
reporting connectors, add it here with the server and tools a
refresh should read.

If the data a page needs is not reachable through one of these, say so and stop;
never substitute another source or reuse an old file.

## Approvals here

- Every write that changes a page, its access, a shared design (including
  registering a template revision) or a workspace waits for a person to
  approve it.
- Data refreshes (`update_page_data` and its upload twin) run without waiting
  and are recorded; each one is an immutable version. To undo a published
  refresh, call `rollback_page` with `version_id` set to the `expected_version`
  that refresh passed (the version live before it) and `expected_version` set
  to the version it published. A bare `rollback_page(slug)` picks the newest
  other approved version, which is the wrong one after two refreshes. A canary
  (`publish=false`) changed nothing live, so there is nothing to roll back.
- Say what a write will do before you make it, and confirm the page is live
  afterwards, as the guide's publishing section describes.

## Access

A page with no password and no partner portal opens only for people signed in to
this Pages deployment as admins. To share a page with someone outside it, set a
password (`set_password`) and send the link and the password, or ask an admin to
add it to a partner portal.

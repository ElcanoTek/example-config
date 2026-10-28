# <Client> email deployment

> **Template.** A client fork fills this in once its SES/S3 email tenant is
> planned, replacing every `<placeholder>`. It records identifiers and variable
> names only, never credential values.

**Status: planned.** Nothing below is live until its validation evidence is
recorded here.

For provisioning logic, IAM policy source, DNS tooling and rollback, use the
canonical
[new-client email-report runbook](https://github.com/ElcanoTek/production-support/blob/main/ses-s3/docs/NEW-CLIENT-EMAIL-SETUP.md)
in production-support. This file supplies the client's concrete values and
evidence; it does not fork the procedure.

## Status and ownership

| Item | Status |
|---|---|
| Inbound `@<domain>` mail | Planned; MX not yet changed |
| Fleet `explorer` connector (inbound) | In the bundle, dark until `EXPLORER_MCP_URL` and `EXPLORER_API_TOKEN` are set |
| Fleet `ses_outbound` connector | In the bundle, dark until the sender keys and `SES_OUTBOUND_FROM_EMAIL` are set |
| Historical import | <none, or the mailbox and date range to migrate> |
| Outbound recipient policy | <unrestricted with per-send approval, or the allowlist> |
| Explorer | <host>; its `EMAIL_S3_BUCKET` is `<tenant>-victoria-email` |

The AWS/Lambda deployment is owned by production-support's `ses-s3/` (tenant
`<tenant>`, `config/tenants/<tenant>.json`). This bundle owns `manifest.yaml`,
`mcp/ses_sender.py`, `mcp/email_render.py`,
`mcp/email_lint.py`, `protocols/email-style.yaml`, the client's email theme and
their tests. There is no automated cross-repository sync.

## Deployment inventory

| Property | Value |
|---|---|
| AWS account / region | `<account>` / `us-east-2` |
| Receiving domain | `<domain>` (root-domain catch-all, or a `reports.` subdomain) |
| S3 bucket | `<tenant>-victoria-email` |
| CloudFormation stack | `victoria-email-<tenant>` |
| SES rule set / rule | `victoria-rules` / `<tenant>-save-to-s3` (ends in `StopAction`) |
| Lambda boundary | `VictoriaEmailTenantLambdaBoundary-<tenant>` |
| Fleet runtime IAM user | `<tenant>-email-runtime` (read-only on `emails/`) |
| Fleet outbound IAM user | `<tenant>-email-sender` (send-only, From-locked) |
| Outbound From / Reply-To | `victoria@<domain>` |
| Custom MAIL FROM | `bounce.<domain>` (reject on MX failure) |
| Public MX (target) | `10 inbound-smtp.us-east-2.amazonaws.com` |
| Fleet host | `fleet.<domain>` |

## Mail flow and receipt-rule order

```text
INBOUND: anything@<domain>
  -> MX 10 inbound-smtp.us-east-2.amazonaws.com
  -> victoria-rules / <tenant>-save-to-s3 (recipient: <domain>)
       -> s3://<tenant>-victoria-email/emails/raw/<message-id>
       -> StopAction
  -> <tenant>-ses-email-date-partitioner -> emails/YYYY/MM/DD/<message-id>
  -> Explorer (<tenant>-email-runtime, read-only) -> Fleet `explorer` connector

OUTBOUND: Fleet `ses_outbound` connector
  -> IAM user <tenant>-email-sender
  -> From/Reply-To victoria@<domain>, MAIL FROM bounce.<domain>
  -> recipients per the policy above, after Fleet's send approval
```

## Fleet connector contract

Host variables (names only; values live in `/etc/fleet/fleet.env`, `0600
root:root`, written by production-support's email provisioning):

- Inbound: the `explorer` connector (Elcano Explorer's MCP server; host
  variables `EXPLORER_MCP_URL` and `EXPLORER_API_TOKEN`, the token minted on
  the Explorer host with `explorer token create`, scopes `email:search` and
  `email:read`). It is the only inbound-mail connector: it searches the
  archive, reads emails by `s3_key`, and `get_download_link` hands out a URL
  for an attachment or a body link that the agent fetches with the native
  `download_url` tool. Report links expire about 48 hours after delivery, so
  the archive caches them in S3 on arrival and Explorer serves the cached
  copy. The bucket and the read-only runtime keys are set on the Explorer
  host, not in Fleet. Explorer returns only mail SES inbound authenticated
  (DMARC, or an SPF / DKIM pass for exactly the From domain), matches
  `sender_contains` against the authenticated mailbox, never the display
  name, and reports skipped mail in `search_warnings`
  (`unauthenticated_sender_skipped` / `_unchecked`). Its sender settings
  (`EMAIL_SENDER_AUTH`, `EMAIL_TRUSTED_LEGACY_PREFIXES`, `EMAIL_AUTHSERV_ID`)
  are set on the Explorer host; see ElcanoTek/explorer `docs/API.md`.
- Outbound: `SES_OUTBOUND_AWS_ACCESS_KEY_ID`,
  `SES_OUTBOUND_AWS_SECRET_ACCESS_KEY`, `SES_OUTBOUND_AWS_REGION`,
  `SES_OUTBOUND_FROM_EMAIL`, `SES_OUTBOUND_REPLY_TO`,
  `SES_OUTBOUND_ALLOW_UNLISTED_RECIPIENTS`.
  `ses_outbound` pins its configured roots when the server starts (a root not
  created yet is reached later from its nearest existing ancestor, pinned
  then, by a no-follow walk). A root it did not know at startup is opened with
  no-follow on every path component; trusted system symlinks directly under
  `/` (root-owned, in a root-owned directory nobody else can write: macOS
  `/tmp`, `/var`) are followed. The same rule applies at startup, so a root
  that is, or sits under, any OTHER symlink is refused whenever it is pinned:
  configure it by its resolved real path.

Inbound must work with no outbound variables set.

## Validation evidence

To be filled in as each runbook phase passes: stack status, boundary on both
Lambda roles, DKIM and custom MAIL FROM status, rule order, `fleet
validate-config`, `fleet mcp test --deep explorer`, `ses_outbound` handshake, the
controlled send, MX change, and the external test message.

## Rollback

Before MX cutover, nothing routes mail here; fix and repeat. After cutover,
restore the previous MX (or remove it if there was none) and keep the stack and
bucket while diagnosing. Outbound-only rollback: deactivate the sender key or
remove its variables and restart Fleet.

## Change log

| Date | Change |
|---|---|
| <date> | Planned |

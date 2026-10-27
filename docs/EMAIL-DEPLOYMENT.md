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
| Fleet `email` connector | In the bundle, dark until `EMAIL_S3_BUCKET` is set |
| Fleet `ses_outbound` connector | In the bundle, dark until the sender keys and `SES_OUTBOUND_FROM_EMAIL` are set |
| Historical import | <none, or the mailbox and date range to migrate> |
| Outbound recipient policy | <unrestricted with per-send approval, or the allowlist> |
| Explorer | <host>; its `EMAIL_S3_BUCKET` is `<tenant>-victoria-email` |

The AWS/Lambda deployment is owned by production-support's `ses-s3/` (tenant
`<tenant>`, `config/tenants/<tenant>.json`). This bundle owns `manifest.yaml`,
`mcp/ses_s3_email.py`, `mcp/ses_sender.py`, `mcp/email_render.py`,
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
  -> Fleet `email` connector (<tenant>-email-runtime, read-only)

OUTBOUND: Fleet `ses_outbound` connector
  -> IAM user <tenant>-email-sender
  -> From/Reply-To victoria@<domain>, MAIL FROM bounce.<domain>
  -> recipients per the policy above, after Fleet's send approval
```

## Fleet connector contract

Host variables (names only; values live in `/etc/fleet/fleet.env`, `0600
root:root`, written by production-support's email provisioning):

- Inbound: `EMAIL_S3_BUCKET`, `EMAIL_AWS_ACCESS_KEY_ID`,
  `EMAIL_AWS_SECRET_ACCESS_KEY` (optional `EMAIL_AWS_REGION`).
  Searches return only mail SES inbound authenticated, read from its own
  `Authentication-Results`. That means a DMARC pass, or (only when DMARC is
  absent or `none`; a DMARC fail is final) an SPF / DKIM pass for exactly the From
  domain; a subdomain, parent or sibling domain counts only through SES's own
  `dmarc=pass`, so a vendor without DMARC that signs from a subdomain is
  skipped unless `EMAIL_SENDER_AUTH=off`. `sender_contains` takes the sender's DOMAIN (matching it
  and its subdomains on label boundaries) or a full address, several
  comma-separated, and matches the authenticated mailbox, never the display
  name (fuzzy sender keywords too; a domain-shaped fuzzy keyword matches the
  sender only): a bare vendor name matches nothing. Each date partition is
  scanned in full and the newest matches kept. Each result carries
  `sender_authenticated`; skipped mail is reported as an
  `unauthenticated_sender_skipped` warning.
  Optional knobs: `EMAIL_SENDER_AUTH=off` falls back to the forgeable
  From-substring match, fuzzy sender keywords included (every search then warns);
  `EMAIL_TRUSTED_LEGACY_PREFIXES` (comma-separated S3 key prefixes, off by
  default) admits operator-imported legacy mail that has no
  `Authentication-Results` (mail in the dated tree is windowed by its partition
  day); `EMAIL_AUTHSERV_ID` (default `amazonses.com`) names
  the receiver. Email-link download limits:
  `EMAIL_LINK_MAX_BYTES` (200 MB per file) and `EMAIL_LINK_MAX_SECONDS` (240 s)
  cap each download, every redirect hop and the archive fallback (which gets at
  most 30 s or a quarter of the deadline) included;
  `EMAIL_LINK_HTML_SCAN_MAX_BYTES`: the first 10 MB of an HTML click-tracker
  page is scanned for its real link; never a download limit (with no link
  found there, the response is saved whole as the file). Downloads stream to a
  temp file and are copied out in chunks; `download_all_link_attachments`
  takes at most `EMAIL_LINK_MAX_COUNT` (20) links, 3 at a time, within one
  240 s deadline and one `EMAIL_LINK_BATCH_MAX_BYTES` budget (1 GB, each file
  still under 200 MB). The debug
  log defaults to a private per-user file (`<tmp>/ses_s3_email-<uid>/debug.log`,
  0600; refused if another user owns it or it has extra hard links), and URL
  userinfo, the whole query string and the whole `#fragment` are redacted from
  log lines. Live fetch, HTML chase and archive fallback share one byte budget
  per link.
- Outbound: `SES_OUTBOUND_AWS_ACCESS_KEY_ID`,
  `SES_OUTBOUND_AWS_SECRET_ACCESS_KEY`, `SES_OUTBOUND_AWS_REGION`,
  `SES_OUTBOUND_FROM_EMAIL`, `SES_OUTBOUND_REPLY_TO`,
  `SES_OUTBOUND_ALLOW_UNLISTED_RECIPIENTS`.
  `ses_outbound` pins its configured roots when the server starts (a root not
  created yet is reached later from its nearest existing ancestor, pinned
  then, by a no-follow walk). A root it did not know at startup is opened with
  no-follow on every path component; trusted system symlinks directly under
  `/` (root-owned, in a root-owned directory nobody else can write: macOS
  `/tmp`, `/var`) are followed, so only a root under any OTHER symlinked
  ancestor is refused: configure it before start, or by its resolved path.

Inbound must work with no outbound variables set.

## Validation evidence

To be filled in as each runbook phase passes: stack status, boundary on both
Lambda roles, DKIM and custom MAIL FROM status, rule order, `fleet
validate-config`, `fleet mcp test --deep email`, `ses_outbound` handshake, the
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

# <Client> deployment

> **Template.** A client fork fills this in from the first day of its
> deployment (it is how a later session resumes), replacing every
> `<placeholder>`. Names, identifiers and where secrets are kept only.

Status: planned | in progress | live
Owner: <engineer>  Started: <YYYY-MM-DD>
Runbook: `client-deploy` skill in ElcanoTek/production-support

Names, identifiers and where secrets are kept only. **No secret values.**

## Decisions

| Item | Value |
|---|---|
| Client / short name / slug | |
| Website / LinkedIn | |
| Brand assets sent (mark SVG, hex colours) | |
| Client domain | |
| Pages content domain | |
| Vultr region | |
| In scope | auth, lens, explorer, pages, fleet, email (strike what isn't) |
| First admin (auth) | |
| Let's Encrypt contact | |
| Email: root domain or `reports.` subdomain | |
| Email: sender / reply-to / recipient policy | |
| Email: history to migrate? | |
| Solution engineer | |
| Secrets stored in | (password manager vault / item names) |

## Inventory

| Host | Vultr instance ID | Plan | IPv4 | IPv6 | Host key (ed25519 SHA256) |
|---|---|---|---|---|---|
| auth.<domain> | | | | | |
| lens.<domain> | | | | | |
| explorer.<domain> | | | | | |
| pages.<domain> (+ content host) | | | | | |
| fleet.<domain> | | | | | |

## Checklist

Tick each with its evidence (command and result, date).

### Names and repos
- [ ] Client domain bought, auto-renew on, DNSSEC on
- [ ] Pages content domain bought, auto-renew on, DNSSEC on
- [ ] Zone-scoped Cloudflare token created (stored in: )
- [ ] `ElcanoTek/<client>-config` created from example-config (no sibling-bundle copies); fork pass PR open (branding + the `branding.support` CTO contact button + palette, mark + share card, persona, handoff)
- [ ] Read-only SSH deploy key(s) registered on `ElcanoTek/<client>-config` (`fleet.<domain>`, and `auth.<domain>` if using bundle branding)

### Boxes and DNS
- [ ] 5 boxes created: key-only startup script, all team keys, FQDN hostnames, Vultr automatic backups on with the Daily schedule
- [ ] Host keys verified out of band and in `known_hosts`
- [ ] A/AAAA records, DNS-only, visible at the zone's nameservers
- [ ] `sshd -T` shows no password login on every box

### Services
- [ ] auth: password mode, `auth doctor` clean, `/healthz` ok
- [ ] auth: apps registered (fleet, lens, explorer, pages) with back-channel logout,
  via `scripts/register-auth-apps.sh` (no secret shown or stored)
- [ ] auth: first admin created with team `Elcano`, admin MFA policy on, granted fleet, lens, explorer, pages
- [ ] auth: emails its own invites (key from `provision_tenant_email.sh`, switched on by
  `ses-s3/scripts/activate_auth_sender.sh --apply --restart`), a self-invite from the console arrived
  from `signin@<domain>`
- [ ] lens: central auth, `lens doctor` clean, fleet token minted
- [ ] explorer: central auth, email bucket wired, `explorer doctor` clean
- [ ] pages: both hosts, central auth, host split verified, fleet's token installed
  box to box, the unseen `initial` token revoked
- [ ] fleet: bundle cloned, OpenRouter key with spend limit, OIDC wired
- [ ] fleet: `fleet doctor --check` 0 advisories, `fleet mcp test --all` passes
- [ ] fleet: brand assets byte-identical to the bundle
- [ ] Daily backups: `scripts/install-backup-timer.sh` applied for auth, pages and lens, and each first
  backup passes `--check`; fleet's own `fleet-backup` timer enabled

### Email (see `docs/EMAIL-DEPLOYMENT.md`)
- [ ] Tenant stack deployed (bucket versioning on, 90-day Lambda log retention), DKIM and MAIL FROM verified
- [ ] Sender (send-only) and Explorer (read-only) users created; no Fleet runtime user (retired)
- [ ] `fleet mcp test explorer` passes (Fleet reads mail through explorer); outbound handshake passes or N/A
- [ ] MX cut over with approval; external test message filed (`check_filing.sh`) and found through explorer

### Handover
- [ ] Fresh backup on auth and fleet, then `scripts/verify-client.sh` passes; its table is pasted below as evidence
- [ ] All hosts answer `http_check` with 200/3xx
- [ ] End-to-end sign-in as the first admin to every app
- [ ] `estate.json` + README updated in production-support (PR: )
- [ ] `ses-s3/README.md` rollout table updated (PR: )
- [ ] This file and `docs/EMAIL-DEPLOYMENT.md` filled in from live checks (PR: )
- [ ] Solution engineer handed the client (protocols, users)

## Verification

`scripts/verify-client.sh <domain> <content domain>` from production-support
(read-only), <YYYY-MM-DD>. Paste its table here.

## Open questions

-

## Change log

| Date | Change | By |
|---|---|---|

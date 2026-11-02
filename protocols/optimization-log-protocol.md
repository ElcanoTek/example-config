# Optimization Log Protocol (v3.0, 2026-10-22)

One protocol for the whole campaign optimization lifecycle: **Intelligent UI (`show_ui`) Campaign Config Onboarding & Day-1 Preflight QA**, the persistent Optimization Log (`OptLog` + `OptTargets`), the scheduled weekday review runner, the **Chat-First Optimization Workbench (`show_ui`)**, additive SSP execution or target-list export, **Client-Facing Optimization Story Emails (`show_ui`) & 4-Test Spend Elasticity / Incremental Budget Evaluation**, and end-of-flight closeout.

Campaign-specific settings (storage location, trader owner, sources, KPI & channel overrides, levers, SSP seats & deal IDs, platform limits, optimization days, and state) live in each campaign's config (`CampaignConfig_<Client>_<Code>.yaml`), never hardcoded in prompts.

Each review answers four questions, in this order: **What did we recommend? Is it live? Did it work? What's next?** Anything that doesn't serve one of those is out of scope.

---

## 1. Files & Workspace Storage Organization (`Optimizations/`)

Every campaign uses **3 co-located files** (backed by this protocol, `protocols/CampaignConfig_TEMPLATE.yaml`, and `protocols/optimization-trader-quickstart.md`):

| File | Purpose |
|---|---|
| `CampaignConfig_<Client>_<Code>.yaml` | Settings: `storage` location, trader owner & email, `campaign.curator`, `campaign.deal_plan_client_id`, `ssp_seats`, `deals` (`<dsp_id>::<deal_name>` → SSP deal ID & internal API ID), DSP report sources, KPI & channel benchmarks, trader-confirmed eligibility thresholds (`cycle`), levers, platform limits, optimization days, `schema_version`, and `state` (`last_optimization_run`, `last_light_review`, `carried_over`, `carried_over_from`, `carried_over_run_type`, `wrap_completed`) |
| `OptLog_<Client>_<Code>.csv` | The log: one row per change per deal (10 columns) |
| `OptTargets_<Client>_<Code>.csv` | Exact values per Opt ID (Block List domains/apps, cities, OS, hours) used for go-live checks, inline Workbench editing, and additive SSP execution (5 columns). Required whenever a row targets more than one value |
| `protocols/CampaignConfig_TEMPLATE.yaml` | Starter YAML template for onboarding new campaigns |
| `protocols/optimization-trader-quickstart.md` | 1-page Trader Quick-Start cheat sheet |

### Storage locations & discovery (`storage` block)
- **Persistent Conversation Workspace (`storage.type: workspace`, default):**
  - Stored under `Optimizations/<Client> (<Code>)/` (or a custom workspace subfolder in `storage.folder_path`) inside the persistent conversation working directory and indexed in `Optimizations/CampaignRegistry.yaml`.
  - When a recurring weekday task is created from the campaign's optimization thread via **"Make recurring task…"** (or when the trader continues in that optimization thread or attaches the exported YAML/CSV files in a new thread), the agent discovers all active campaigns by scanning `Optimizations/**/CampaignConfig_*.yaml` and `Optimizations/CampaignRegistry.yaml`.
  - Whenever onboarding saves a campaign, it also upserts `{campaign_id, storage_backend, folder_path, config_filename}` into `Optimizations/CampaignRegistry.yaml` and provides clickable markdown download links (`[CampaignConfig_<Client>_<Code>.yaml](...)`) in chat.
- **Co-location rule:** Always read and write `OptLog_<Client>_<Code>.csv` and `OptTargets_<Client>_<Code>.csv` in the **same directory** as that campaign's `CampaignConfig_<Client>_<Code>.yaml`.
- **In-place atomic updates:** Edit files in place under an exclusive per-campaign file lock (`Optimizations/<Client> (<Code>)/.lock` via `fcntl.flock`), writing to `<file>.tmp` and committing via `os.replace`. Never delete and re-create without atomic replacement.
- **Self-serve protocol & skill updates:** A workspace override copy of this protocol at `Optimizations/Protocols/optimization-log-protocol.md` is checked alongside the bundled `protocols/optimization-log-protocol.md`. Traders can update the protocol anytime in chat (`"update the optimization protocol: <change>"`) — the agent edits `Optimizations/Protocols/optimization-log-protocol.md` in place (bumping the version header and changelog). Traders can also click **Clone & edit** on `campaign-optimization-log` in **Settings → Skills** (or on any Optimization workflow in the **Prompt Library**) and check **Share with this workspace** to publish skill or prompt overrides without an engineering deploy.

### `OptTargets` columns (5) & composite key

| Column | Content |
|---|---|
| `Opt ID` | Matches the log row, including the deal suffix (e.g. `ACM-011.1`, `DCO-001`) |
| `Lever` | Same lever as the log row's Change prefix (`Domain Block List`, `Domain Allow List`, `Daypart`, `Geo Targeting`, `Device/OS/Browser Targeting`) |
| `Value` | One **clean SSP-executable** entry per row: bare domain (`dailymotion.com`), bare app bundle/store ID (`in.playsimple.wordsearch`), city, OS, or hour range (`00:00-05:59`). Never put display labels like `Word Search Explorer (ID: in.playsimple.wordsearch)` in `Value` — put human display names in `OptLog.Notes` so `OptTargets` passes SSP API validation directly |
| `Added` | ISO date (`YYYY-MM-DD`) |
| `Source` | `agent` or the trader's name |

Because a multi-target optimization has many rows sharing the same `Opt ID`, the unique row key of `OptTargets.csv` is the composite tuple **`(Opt ID, Lever, Value)`**. Write the targets **when the row is proposed** (and whenever a `Backlog` row is promoted to `Proposed`), in the same atomic write batch as the log. A Change that names a count ("Add the 21 sites") without targets rows is incomplete. If targets are missing at go-live time, check the values named in `Baseline`/`Notes`, mark the note `partial check (n of N values)`, and list the row under "Waiting on you" until the full list is supplied.

---

## 2. Guided Campaign Config Onboarding & Day-1 Preflight QA (`show_ui`)

Traders never need to hand-edit YAML files. When a trader asks to set up a campaign config — e.g., **"onboard `<Client>` `<CampaignCode>` to the optimization log"**, **"set up optimization tracking for `<Client>`"**, **"build a campaign config for `<Client>`"**, or selects **Onboard campaign to Optimization Log** from the Prompt Library:

### Step 2.1 — Auto-Discovery & Day-1 DSP Report Preflight QA
Before showing the onboarding card, auto-discover everything available so the form is pre-filled and validated:
1. **Locate & Download DSP Reports:**
   - Search the conversation workspace (`Optimizations/` or attached files) and the report inbox (`mcp_explorer_find_latest_report` / `mcp_explorer_search_emails`) to find the latest DSP report drop(s). For mailbox sources, `mcp_explorer_find_latest_report` returns message metadata (`s3_key`, attachment filenames) only — always download the report payloads into your working directory:
     - For file attachments (`delivery: attachment`): call `mcp_explorer_get_download_link(s3_key=..., filename=...)` + `download_url(url=<its download_url>)`.
     - For body download links (`delivery: download_link`): call `mcp_explorer_extract_download_links(s3_key=...)` first to extract the URLs from the email body, then pass `s3_key` and the report `url` to `mcp_explorer_get_download_link(s3_key=..., url=...)` + `download_url(url=<its download_url>)`.
     - Verify the downloaded files exist on disk before inspecting them.
   - Download one sample report per report type in `run_python` and inspect its columns to auto-fill:
     - `deal_field` (e.g., `Inventory Source`, `Deal name`, `Deal ID`)
     - Active campaign deal names
     - Date coverage window and total spend/impressions
     - Available conversion, revenue, click, viewability, and video-completion columns.
2. **Run Day-1 DSP Report Preflight QA:**
   - Verify whether all 5 standard dimensional report types are flowing (`overall`, `domain`/`app`, `geo`, `time_of_day`, `system`/`device`).
   - Verify that every active deal has non-null KPI columns (e.g., if the primary KPI is `ROAS` or `CPA`, check whether conversion/revenue columns are present and non-zero across the dimensional reports, or if conversion columns only exist in `overall`).
   - Check for filename/subject token quirks (e.g., a truncated campaign code token on one report type) and record them in `dsps[].notes` so future runs never filter them out.
   - Any missing report type or missing conversion column becomes a clear `warn` item in the Onboarding Card's **Day-1 Preflight Checklist (`status_list`)** so the trader can fix the DSP scheduled report on Day 1 instead of discovering it a week later.
3. **Auto-Resolve Deal Planner Profile, SSP Seats & Deal IDs:**
   - Populate `campaign.curator`, `campaign.deal_plan_client_id`, and `ssp_seats`:
     - When `manifest.yaml` registers `deal_plan` and SSP connectors, set `campaign.deal_plan_client_id` to the bundle's Deal Planner profile ID in `deal_profiles/<id>.yaml` (for example `"reklaim"` — never an advertiser or campaign name) and query live SSP read tools (`*_find_deals` / `*_get_deal`) to populate `deals` with each deal's public buyer-facing `deal_id` (e.g. `IX...`, `OX-...`, `MGNI-...`), `internal_deal_id`, `channel` (`Display`, `OLV`, `CTV`, `Native`), direct console `deal_url`, and any preserved account-level baseline blocklist IDs (`baseline_block_list_ids`).
     - When `manifest.yaml` does not register `deal_plan` or SSP connectors, set `campaign.deal_plan_client_id: null` and populate `deals` from the discovered DSP report deal names and any user-provided deal metadata.
   - Key entries in `deals` as `"<dsp_id>::<Exact Deal Name in DSP Report>"` (or `"<Exact Deal Name in DSP Report>"` when the campaign has a single DSP) so multi-DSP campaigns never collide when two DSP reports share a deal name.

### Step 2.2 — Present the Interactive `show_ui` Campaign Config Builder Card
Present one structured `show_ui` card (`title: "Campaign Optimization Setup — <Client> (<CampaignCode>)"`) pre-populated with everything discovered in Step 2.1 (following Blueprint 1 in `skills/campaign-optimization-log/REFERENCE.md`):
1. **Day-1 Preflight Status Banner (`status_list`):**
   - Shows pass/warn status for: (a) DSP Report Source & Date Window, (b) Dimensional Coverage (`overall`, `domain`, `geo`, `time_of_day`, `system`), (c) KPI Formula & Column Check, and (d) Active SSP Deals Linked (`N` deals mapped to SSP Deal IDs).
2. **Tab 1 — `1. Campaign, Team & Save Location`:**
   - `client_name` (`text_input`), `campaign_code` (`text_input`), `opt_id_prefix` (`text_input`, 2–4 uppercase letters, e.g. `ACM`, `DCO`), `start_date` & `end_date` (`date`), `curator` (`text_input`).
   - `trader_name` (`text_input`), `digest_to` (`text_input`, trader's internal email), `cc_emails` (`text_input`, optional).
   - `optimization_days` (`multi_select` of `MON`..`FRI`, pre-filled with the trader's preferred day) and `light_days` (`multi_select` of `MON`..`FRI`).
   - **Where to Save (`storage_target` `select` + `storage_custom_path` `text_input`):**
     - `workspace_default`: `Persistent Conversation Workspace — Optimizations/<Client> (<Code>)/ (Recommended)`
     - `custom`: `Custom Workspace Subfolder (specify below)`
3. **Tab 2 — `2. KPI, Thresholds & Levers`:**
   - `primary_kpi` (`select`: `ROAS`, `CTR`, `CPA`, `VCR`, `Viewability`, `CPC`, `CPM`), `kpi_direction` (`choice`: `higher_is_better` vs `lower_is_better`), and `kpi_benchmark` (`number`, e.g. `33.0` for 33x ROAS or `0.08` for 0.08% CTR). When multiple DSPs are discovered during preflight (`len(dsps) > 1`), render **repeatable per-DSP KPI field groups** (`<dsp_id>_primary_kpi`, `<dsp_id>_kpi_direction`, `<dsp_id>_kpi_target`, `<dsp_id>_benchmark_ctv`, `<dsp_id>_benchmark_olv`, `<dsp_id>_benchmark_display`) for each DSP so every `dsps[]` entry receives its own KPI block.
   - `use_baseline_fallback` (`toggle`, default `false`): *"No client benchmark yet — compute initial baseline from the first report window."*
   - `channel_overrides` (`text_input`, optional): Per-channel benchmark or KPI overrides (e.g., `Display: CTR >= 0.08%, OLV: VCR >= 75%`).
   - **Trader-Confirmed Eligibility Thresholds (`number` + `slider`):**
     - `min_spend_share_pct` (`number`, e.g. `10` for `10%`, saved as `cycle.materiality.min_addressable_spend_share = min_spend_share_pct / 100.0`)
     - `min_projected_kpi_lift_pct` (`number`, e.g. `5` for `5%`, saved as `cycle.materiality.min_projected_kpi_change = min_projected_kpi_lift_pct / 100.0`)
     - `min_impression_threshold` (`number`, e.g. `1000`, saved as `cycle.min_sample.impressions`)
     - `max_volume_cut_pct` (`slider`, `15..75`, e.g. `40` for `40%`, saved as `cycle.max_deal_volume_cut_share = max_volume_cut_pct / 100.0`)
   - `ssp_direct_levers` (`multi_select`) and `client_request_levers` (`multi_select`).
4. **Tab 3 — `3. Active Deals & Report Sources (<N>)`:**
   - Multi-select `table` (`id="active_deals"`, `select="multi"`, `row_key="deal_id"`, all active campaign deals pre-selected) showing `DSP :: Deal Name in Report`, `SSP (Seat)`, `Channel`, `Deal ID`, and `7d Spend`.
   - `report_source_type` (`choice`: `mailbox` vs `uploaded_file`), `report_mailbox` (`text_input`), and `deal_field` (`text_input`). When multiple DSPs are discovered during preflight (`len(dsps) > 1`), render **repeatable per-DSP report source controls** (`<dsp_id>_report_source_type`, `<dsp_id>_mailbox_address`, `<dsp_id>_deal_field`) for every DSP found.
5. **Card Actions:**
   - **Primary Submit (`id="save_config"`, `style="primary"`):** `Save Config & Initialize Log`
   - **Secondary Submit (`id="save_and_run"`, `style="secondary"`):** `Save Config & Run First Optimization Review Now`

*(Fast-path rule: if the trader explicitly says `"just create and save the config now without showing the form"` and provided all required fields and thresholds, skip the card and initialize the 3 files directly.)*

### Step 2.3 — Initialize & Save All 3 Files
When the trader submits the Onboarding Card:
1. Normalize percentage controls (`min_spend_share_pct / 100.0`, `min_projected_kpi_lift_pct / 100.0`, `max_volume_cut_pct / 100.0`) into decimal shares and validate `CampaignConfig_<Client>_<Code>.yaml` via `yaml.safe_load` (`schema_version: "3.0"`), populating a complete `dsps[]` entry for every configured DSP and `campaign.deal_plan_client_id`.
2. Initialize `OptLog_<Client>_<Code>.csv` with header row:
   `Opt ID,DSP,Deal,Change,Status,Proposed,Live,Baseline,Result,Notes`
3. Initialize `OptTargets_<Client>_<Code>.csv` with header row:
   `Opt ID,Lever,Value,Added,Source`
4. Save all three files to `Optimizations/<Client> (<Code>)/` (or `storage_custom_path`), upsert the campaign pointer in `Optimizations/CampaignRegistry.yaml`, and provide clickable download links in chat.
5. Remind the trader how to schedule their weekday review runner by clicking **"Make recurring task…"** (one weekday task per `team.digest_to` recipient at `15:00 UTC`). If the trader clicked **`Save Config & Run First Optimization Review Now`**, immediately run the initial Optimization Day scan (Section 5) and open the **Optimization Workbench card** (Section 6) in the same turn!

---

## 3. Log Columns (10) & Lifecycle

| Column | Content |
|---|---|
| `Opt ID` | `<PREFIX>-NNN.d`: NNN = one action, `.d` = one deal (`ACM-002.1`, `DCO-001`). Single-row items have no suffix. Never reused |
| `DSP` | DSP from the config (`DV360`, `The Trade Desk`, `Amazon DSP`, etc.) |
| `Deal` | Exact deal name from the report's `deal_field`, or `n/a (line-item level)` |
| `Change` | `<Lever>: <what changes>`, imperative. Add `— client request` when the lever is a client request in the config |
| `Status` | Strictly one of the canonical values below (`Backlog`, `Proposed`, `Live`, `Kept`, `Reverted`, `Dropped`) |
| `Proposed` | ISO date (`YYYY-MM-DD`) |
| `Live` | ISO date the change went live (detected or trader-declared) |
| `Baseline` | At proposal: deal KPI and the targeted segment's numbers, with numerator/denominator, spend and window |
| `Result` | Early read (≥ 3d), then final before → after on the KPI with windows (≥ 7d), plus one plain-English value sentence with a number |
| `Notes` | Append only: `YYYY-MM-DD: <what happened, who>`. Execution path (`[Chat API]`, `[Console/DSP]`, `[Client Request]`), go-live check, scale warnings, inconclusive/drop reasons, revert recommendations, human display names for app bundles, and open-row goal go here |

### Lifecycle

`Backlog` → `Proposed` → `Live` → `Kept` / `Reverted`

Any status → `Dropped` (keep `Status = Dropped` strictly — never write `Dropped (inconclusive)` in `Status`; record the reason such as `"inconclusive"` or `"expired"` in `Notes`). Dropped rows stay in the log so they aren't re-proposed unless the data materially changes (cite the old Opt ID).

- **Backlog**: qualifying ideas not in the current package, including chat ideas. Not re-scored. Listed in the Workbench card (Tab 3). Dropped as `"expired"` in `Notes` after 21 days without promotion.
- **Proposed**: in the current package, waiting on implementation.
- **Live**: implemented; being measured.
- **Kept / Reverted**: decided after at least 7 delivery days. For SSP-side changes whose 7-day read regresses, an unattended run keeps `Status = Live` and adds `[REVERT RECOMMENDED]` to `Notes` until the inverse SSP update is actually executed and verified, at which point `Status` becomes `Reverted`.

### Before the first row: lock the KPI

Do not log a `Proposed` row until the config's primary KPI (name, formula, direction) is final and `status: active`. A benchmark is captured at onboarding (or a baseline is computed only when no benchmark is provided). If the KPI changes after rows exist, do not mass-drop them: re-baseline open rows in `Notes` on the new KPI and keep going. If a KPI is `blocked` with an `unblock_check`, run that check on every run and email the trader once when it passes.

---

## 4. Config Keys, Automatic Schema Migration, Trader Routing & PTO Coverage

Canonical names below. Legacy names are still accepted so older configs keep running, and self-healing migration upgrades them automatically.

| Canonical key | Legacy alias (auto-migrated) | Default |
|---|---|---|
| `schema_version` | — | `"3.0"` (backward-compatible with `"2.8"`; triggers auto-migration when older) |
| `storage.type` | — | `"workspace"` |
| `storage.folder_path` | — | `"Optimizations"` |
| `campaign.curator` | — | `"default"` (or seat identifier from `ssp_seats`) |
| `campaign.deal_plan_client_id` | — | Bundle's Deal Planner profile key (e.g. `"reklaim"` when `deal_plan` is registered in `manifest.yaml`, else `null`) |
| `team.trader` | — | required (trader's full name) |
| `team.digest_to` | — | required (that trader's internal email address) |
| `team.cc` | — | `[]` (optional list of internal CC email addresses) |
| `team.backup_trader` | — | `null` (optional email added to `to_email` during PTO handoff while `today <= team.backup_until`) |
| `team.backup_until` | — | `null` (optional ISO date `YYYY-MM-DD`; once `today > team.backup_until`, `backup_trader` and `backup_until` are automatically cleared) |
| `team.optimization_days` (list, MON-FRI) | `team.review_days`, `team.review_day` | none: campaign is skipped |
| `team.light_days` (list, optional) | — | `[]` (no light checks) |
| `dsps[].kpi.primary.channel_benchmarks` | `kpi.primary.channel_benchmarks`, `dsps[].kpi.channel_benchmarks` | `{}` (optional per-channel benchmark overrides under each DSP entry, e.g. `{Display: 0.08, OLV: 0.25}`) |
| `dsps[].kpi.channel_kpis` | `kpi.channel_kpis`, `dsps[].kpi.channel_kpi_overrides` | `{}` (optional per-channel KPI formula/name override under each DSP entry, e.g. `VCR` for `OLV`/`CTV`) |
| `cycle.max_new_opt_groups_per_run` | `cycle.max_opt_groups_per_campaign_week` | `5` |
| `cycle.max_deal_volume_cut_share` | — | Trader-confirmed fraction (`max_volume_cut_pct / 100.0`, e.g. `0.40`) |
| `cycle.materiality`, `cycle.min_sample`, `cycle.evaluation_days` | — | Trader-confirmed values saved during onboarding |
| `ssp_seats` | — | required map of SSP → `{seat, connector_label, mcp_prefix, account_id}` |
| `deals` (or `dsps[].deals`) | — | required map of `<dsp_id>::<deal_name>` → `{deal_id, internal_deal_id, ssp, seat, dsp, channel, deal_url, baseline_block_list_ids, optimization_block_list_id}` |
| `files.opt_log`, `files.opt_targets` | — | derived from `OptLog_<Client>_<Code>.csv` and `OptTargets_<Client>_<Code>.csv` |
| `state.last_optimization_run` | `state.last_full_review` | `null` (updated to ISO date after each Optimization Day run) |
| `state.last_light_review` | — | `null` (updated to ISO date after each light check) |
| `state.carried_over` | — | `false` (set `true` when deferred by the 5-campaign cap) |
| `state.carried_over_from` | — | `null` (ISO date when the run was first deferred) |
| `state.carried_over_run_type` | — | `null` (`"full"` \| `"light"` \| `"closeout"` — records the exact deferred run type) |
| `state.wrap_completed` | — | `false` (set `true` after the end-of-flight closeout run) |

`optimization_days` and `light_days` must not overlap. A day in both runs a full Optimization Day run.

### Automatic Schema Migration & Dry-Run Exemption Rule
1. **`schema_version` Stamp:** Every config carries `schema_version: "3.0"` at the top level.
2. **Self-Healing on Normal Runs (In-Memory Only on `DRY_RUN`):** Whenever a normal (non-`DRY_RUN`) scheduled run or chat session loads any `CampaignConfig_*.yaml` whose `schema_version` is missing or older than `"3.0"` (or that contains legacy keys like `review_days`, `review_day`, `last_full_review`, `max_opt_groups_per_campaign_week`, or expired `team.backup_until`), the agent migrates the keys in memory and writes the upgraded config back in place under the campaign's `.lock` file. **When `DRY_RUN` is active, perform all schema migration and expired-backup cleanup strictly in memory without writing any changes or `.bak` modifications to disk.**
3. **One-Command Fleet Sync (`"sync all campaign configs"`):** Whenever a trader says `"sync all campaign configs"`, the agent scans all `CampaignConfig_*.yaml` files in `Optimizations/`, upgrades every config to the current schema version in place, and prints a confirmation table.

### Trader distribution, one-email-per-task-run rule & PTO coverage
Every campaign routes strictly to the trader specified in its own `CampaignConfig` under `team.digest_to`.
Because `mcp_ses_outbound_send_email` enforces at most **one outbound email per scheduled task run** (suppressing any second `send_email` call in the same run), each scheduled recurring task must be scoped to a **single `team.digest_to` recipient** (one recurring weekday task per trader). If a single task invocation discovers due campaigns belonging to multiple `team.digest_to` addresses, it processes only the single target trader's `team.digest_to` group in that run (leaving other traders' campaigns untouched for their own task run) so that only one consolidated email is sent per task invocation.
When a trader is out on PTO, saying **"cover `<Trader>`'s campaigns for `<Backup Trader>` until `<Date>`"** sets `team.backup_trader` and `team.backup_until: "<YYYY-MM-DD>"` on their active configs so the backup trader is included in `to_email` while `runtime_today <= team.backup_until`. Whenever a non-`DRY_RUN` run or chat session observes `runtime_today > team.backup_until`, it clears `team.backup_trader: null` and `team.backup_until: null`.

---

## 5. Review Runs, Carryover Queue & Short Notification Email

Any trader can trigger a review on demand in chat (`"run optimization review for <Client>"` or `"run weekday optimization queue"`), and once scheduled via **Make recurring task…** (one weekday task per `team.digest_to` trader at **15:00 UTC**), the runner checks that trader's active campaigns automatically.

### Queue selection & deterministic carryover (max 5 campaigns per scheduled run)
1. Discover all `CampaignConfig_*.yaml` files in `Optimizations/` (and `Optimizations/CampaignRegistry.yaml`) for the task's target `team.digest_to` trader where `campaign.active: true`.
2. **End-of-Flight Closeout Check (`runtime_today > campaign.end_date`):**
   - If `campaign.active: true`, `runtime_today > campaign.end_date`, and `state.wrap_completed != true`, the campaign is due for **one final End-of-Flight Closeout Run** (`run_type = "closeout"`).
3. A live campaign (`start_date <= runtime_today <= end_date`) is **due for an Optimization Day Run (Full Review, `run_type = "full"`)** today if:
   - `state.carried_over == true` **and** `state.carried_over_run_type in ("full", null)`, **OR**
   - Today's weekday (`MON`–`FRI`) is in `team.optimization_days` (or legacy `team.review_days` / `team.review_day`) **and** `state.last_optimization_run != runtime_today`, **OR**
   - Its scheduled `optimization_days` day occurred earlier this calendar week (since Monday) and `state.last_optimization_run` is older than that scheduled day.
4. A live campaign is **due for a Light Check (`run_type = "light"`)** today if it is not due for an Optimization Day Run or Closeout, and either:
   - `state.carried_over == true` **and** `state.carried_over_run_type == "light"`, **OR**
   - Today's weekday is in `team.light_days` and `state.last_light_review != runtime_today`.
5. **Priority order (up to 5 campaigns per scheduled run — all carried-over work promoted ahead of new work):**
   1. **All carried-over runs (`state.carried_over == true`)**, preserving their stored `state.carried_over_run_type` (`"full"` first, then `"closeout"`, then `"light"`, ordered by oldest `state.carried_over_from` / `state.last_optimization_run`), so deferred closeouts and light checks can never starve behind newly due full reviews.
   2. Today's newly due Optimization Day runs (`run_type == "full"`), oldest `state.last_optimization_run` first.
   3. Newly due end-of-flight closeout runs (`run_type == "closeout"`).
   4. Today's newly due light checks (`run_type == "light"`).
6. Any due campaign beyond the 5-campaign cap (when not in `DRY_RUN`) has `state.carried_over: true`, `state.carried_over_from: <runtime_today>` (if not already set), and `state.carried_over_run_type: "<full|light|closeout>"` written to its `CampaignConfig` so the next weekday executes the exact deferred run type.
7. `DRY_RUN` in the task prompt (or `"dry run"` in chat) performs all migrations and calculations strictly in memory, writes nothing to disk, and sends no email.

### Full review steps (1–6)
1. **Load.** Config, log, targets. Reports per each DSP's `source` in `dsps[]` over the inclusive lookback window `since = runtime_today - (window_days - 1) days` through `until = runtime_today` (where `window_days = max(low_spend_lookback_days, evaluation_days)`, so a 28-day window spans 28 inclusive calendar dates `runtime_today - 27 days` .. `runtime_today`):
   - `mailbox` → call `mcp_explorer_find_latest_report` or `mcp_explorer_search_emails(date_from=since, date_to=until, ...)` to locate the report message(s), and then **download the report payload files**:
     - For attached files (`delivery: attachment`): call `mcp_explorer_get_download_link(s3_key=..., filename=...)` + `download_url(url=<its download_url>)`.
     - For body download links (`delivery: download_link`): call `mcp_explorer_extract_download_links(s3_key=...)` first to obtain the extracted URLs, then call `mcp_explorer_get_download_link(s3_key=..., url=...)` + `download_url(url=<its download_url>)`.
   - `uploaded_file` → read the CSV/XLSX files already placed in the conversation working directory.
   Always verify the files exist on disk before continuing. Strip footer rows, dedupe overlapping dates (newest drop wins), record the data window. Confirm every live deal in the config appears in the report; if one is missing, say so and don't treat it as zero delivery.
2. **Go-live.** For each `Proposed` row, check its deal in delivery data using the full value list from `OptTargets`:

   | Lever | Live when (2+ consecutive days) |
   |---|---|
   | Block List | The listed entries' combined impression share on that deal falls ≥ 90% below their prior-7-day baseline share **AND** the remaining untargeted delivery on that deal remains active (`> 0` impressions, not a deal-wide pause or flight drop) |
   | Allow List / inclusion targeting (e.g. OS include) | ≥ 90–95% of the deal's impressions fall inside the allowed set (while deal delivery is active) |
   | Daypart / geo / device exclusion | The excluded segment's share of the deal's impressions falls ≥ 90% (while remaining deal delivery is active) |
   | Client request (bid, budget, pause) | Not auto-set; flag `"possible go-live"` and ask the trader |

   When `cycle.auto_detect_live` is `true` (or omitted), set Status `Live`, `Live` = first qualifying day, `Notes` `"auto-detected; confirm"`. When `cycle.auto_detect_live == false`, keep `Status = Proposed`, leave `Live` blank, and write `"possible go-live (<first qualifying day>); awaiting trader confirmation (auto_detect_live=false)"` in `Notes`. If `OptTargets` has no rows for the Opt ID, follow the partial-check rule under Section 1. A trader's date always wins. If a trader says live but data shows no change after 3 days, flag it.
3. **Results.** At ≥ 3 delivery days, write an early read in `Result`. At ≥ 7 delivery days, compare pre vs post over equal inclusive windows (`evaluation_days` before `Live` vs `evaluation_days` starting at `Live`, using ratio of sums and the deal's channel KPI/benchmark if configured), write `before → after` and the value sentence:
   - If the KPI improved or held benchmark: set `Status = Kept`.
   - If the outcome cannot be judged (e.g. insufficient post-change delivery): set `Status = Dropped` (never `Dropped (inconclusive)`) and append `"<runtime_today>: Outcome inconclusive after 7d window"` to `Notes`.
   - If the KPI materially regressed:
     - For an SSP-side change whose targets are still live on the SSP: keep `Status = Live` (do **not** mark `Status = Reverted` before the SSP targeting is actually removed, or subsequent block-list unions and live deals will retain the unremoved targets), prepend `"[REVERT RECOMMENDED: 7d KPI regressed <before -> after>]"` to `Notes`, and surface it at the top of the notification email and Workbench so the trader can execute `revert <Opt ID>` (which transitions `Status = Reverted` once the SSP removal is verified).
     - If the trader or DSP already undid the change in delivery data, set `Status = Reverted`.
4. **What's next (with SSP Capability Classification, Scale Protection & Supply Expansion).**
   Find candidates across every report, per deal, using only config levers and respecting `ssp_constraints`. Check each candidate against `skills/ssp-deal-capabilities/SKILL.md` and `manifest.yaml`, and tag its execution path in `Notes`:
   - **`[Chat API]`**: Used only when `manifest.yaml` registers `deal_plan` and the deal's SSP connector, and `skills/ssp-deal-capabilities/` marks the knob **API**.
   - **`[Console/DSP]`**: Applied by the trader in the SSP console (`UI` / `API (tool)`, or when `manifest.yaml` does not register SSP write connectors) or buyer DSP.
   - **`[Client Request]`**: DSP-side bid or budget shift requiring client approval.
   - **Scale / Pacing Protection (`cycle.max_deal_volume_cut_share`, default `0.40` when absent):** Compare the proposed exclusion's share of the deal's impressions or spend against `cycle.max_deal_volume_cut_share`. Whenever the cut exceeds `cycle.max_deal_volume_cut_share`, prepend **`[SCALE WARNING: removes XX% of deal volume]`** in `Notes`.
   - **Balance Exclusions with Supply-Expansion / Positive Levers:** On every Optimization Day run, check for positive / expansion opportunities:
     1. **Under-pacing high-performers:** If a deal beats the KPI benchmark by ≥ 20% but is under-delivering or had more than `cycle.max_deal_volume_cut_share` of its volume cut by prior Block Lists, recommend loosening restrictive targeting, lowering the floor, or expanding supply.
     2. **Curated Allow-List / High-Priority Deal Clone:** If 5–25 top domains/apps drive ≥ 2x the campaign KPI benchmark with strong scale, recommend a positive curation play.
     3. **Within-DSP / Cross-Deal Budget Reallocation (`[Client Request]`):** Shift spend toward deals or line items beating the benchmark by ≥ 30%.
   - Cap at `max_new_opt_groups_per_run` (default 5). Zero is valid. Qualifying extras go to `Backlog`.
5. **Write (with Atomic Lock & Read-Before-Write Merge Guardrail):**
   - Skip all disk writes when `DRY_RUN` is active.
   - Immediately before saving `OptLog_<Client>_<Code>.csv` or `OptTargets_<Client>_<Code>.csv` (both in scheduled runs and in chat Workbench turns), acquire the exclusive per-campaign file lock `Optimizations/<Client> (<Code>)/.lock` via `fcntl.flock`, re-read the latest file from disk, and merge:
     - **Merge `OptLog` by `Opt ID`:** Preserve any status overrides, `Live` dates, or new rows added concurrently by another process. Allocate (or re-verify) new `Opt ID` numbers **after** re-reading the latest `OptLog`; if a newly proposed `Opt ID` collides with a concurrent writer's row, re-number the new group to `max(existing_opt_id_number) + 1` and update its corresponding `OptTargets` rows before saving.
     - **Merge `OptTargets` by composite key `(Opt ID, Lever, Value)`:** When updating targets for a specific `(Opt ID, Lever)` (such as inline Workbench domain edits), replace that `(Opt ID, Lever)` group atomically with the submitted values while preserving all rows for other `(Opt ID, Lever)` pairs by `(Opt ID, Lever, Value)` so sibling target rows are never collapsed or overwritten.
     - Write to `<file>.tmp`, flush, and commit via `os.replace` before releasing `.lock` (deferring the `CampaignConfig` `state.last_optimization_run` / `state.carried_over` update until Step 6 confirms notification email acceptance).
6. **Short Notification Email ("Opts Ready in Chat") & State Completion:**
   - Send one consolidated email for the trader (`team.digest_to`) via `mcp_ses_outbound_send_email`:
     - **Subject:** `Opts ready in Chat — <Client> (<CampaignCode>) — <N> recommendations` (or `Opts ready in Chat — <date> — <N> campaigns` when grouped for that trader).
     - **Block 1 (`headline_metrics`):** Up to 4 KPI & outcome snapshot cards (primary KPI vs. goal, spend, and any 7-day `Kept` / `Reverted` / `[REVERT RECOMMENDED]` headline item).
     - **Block 2 (`callout`, `kind="opportunity"`):** **"1-Line Handoff to Fleet Chat"** box at the top:
       > `Open Fleet Chat and type: review <Client> opts`
       > `Your interactive Optimization Workbench card will open so you can inspect the data, ask questions about any domain/deal, edit the target lists, or apply selected updates.`
     - **Block 3 (`table`):** One compact summary table per campaign (`Opt ID & Deal`, `Proposed Change & Execution Path`, `7d Spend & Projected Lift`).
   - **Check the actual `mcp_ses_outbound_send_email` success response (`status == "queued"`):** `mcp_ses_outbound_send_email` returns `status: "queued"`, `status_code` (2xx), and `message_id` on success (and `error` on failure; it does not return an `ok` boolean). **Only after `mcp_ses_outbound_send_email` returns `status == "queued"` (with a non-empty `message_id` and no `error`)**, update `CampaignConfig` (`state.last_optimization_run: <runtime_today>`, `state.carried_over: false`, `state.carried_over_from: null`, `state.carried_over_run_type: null`) and save it in place. If the email call fails, keep the prior `last_optimization_run` and `carried_over` state so the next run retries.

### Light review (optional mid-week check — steps 1–3 and 5L/6L only)
- **Purpose:** A silent background check between full weekly reviews. It does **not** propose new optimizations (`What's next` is skipped) and sends **no email** on a quiet day.
- **Step 5L / 6L (Conditional Alert Delivery Before State Commit):**
  - Merge any updated `OptLog` and `OptTargets` rows using the Read-Before-Write Merge Guardrail above.
  - **If the light check is quiet** (no change went live, no 7-day result finished, and no delivery guardrail tripped): send no email, and immediately write `state.last_light_review: <runtime_today>`, `state.carried_over: false`, `state.carried_over_from: null`, `state.carried_over_run_type: null` in `CampaignConfig` (leaving `state.last_optimization_run` unchanged).
  - **If the light check requires an alert email** (a change went live mid-week, a 7-day result finished, or a delivery guardrail tripped): send the notification email via `mcp_ses_outbound_send_email` **first**, and **only after `mcp_ses_outbound_send_email` succeeds (`status == "queued"`, non-empty `message_id`, no `error`)**, write `state.last_light_review: <runtime_today>` and clear `state.carried_over` (`false`, `carried_over_from: null`, `carried_over_run_type: null`) in `CampaignConfig`. If the email send fails, do **not** advance `state.last_light_review` and retain carryover state so the alert is retried.

### End-of-Flight Closeout Run (`runtime_today > campaign.end_date`)
When an active campaign passes its `end_date` and `state.wrap_completed != true`:
1. Resolve every remaining open row in `OptLog` so no row is left permanently `Live`, `Proposed`, or `Backlog`:
   - `Live` rows with **≥ 3 post-change delivery days** get a final pre-vs-post readout (`Status = Kept` or `Status = Dropped` with `"inconclusive"` in `Notes`).
   - `Live` rows with **< 3 post-change delivery days** are set to `Status = Dropped` (canonical status) with `Result = "Flight ended before 3-day evaluation window"` and `"<runtime_today>: Inconclusive — campaign flight ended with <3 post-change delivery days"` appended to `Notes`.
   - Any open `Proposed` or `Backlog` rows that never went live are set to `Status = Dropped` with `"<runtime_today>: Campaign flight ended before implementation"` in `Notes`.
   Save the updated `OptLog` in place.
2. Email the trader a **Campaign Optimization Wrap Summary** (`Optimization wrap — <Client> (<CampaignCode>)`) via `mcp_ses_outbound_send_email` containing all `Kept` optimizations with their before → after KPI lift and value sentences.
3. **Only after `mcp_ses_outbound_send_email` succeeds (`status == "queued"`, non-empty `message_id`, no `error`)**, set `campaign.active: false`, `state.wrap_completed: true`, `state.carried_over: false`, `state.carried_over_from: null`, `state.carried_over_run_type: null` in `CampaignConfig_<Client>_<Code>.yaml` and save in place (if email delivery fails, leave `campaign.active: true` and `state.wrap_completed: false` so the next run retries the wrap email).

---

## 6. The Chat-First Optimization Workbench (`show_ui`) & Execution Flow

Whenever a trader opens chat and says:
- **"review `<Client>` opts"** (or **"open `<Client>` workbench"**, **"show open opts for `<Client>`"**, **"review opts for `<Client>`"**) → Load `CampaignConfig`, `OptLog`, and `OptTargets` for that campaign and render its interactive **Optimization Workbench card** via `show_ui`.
- **"review my opts"** (or **"open my workbench"**, **"review `<Trader>`'s opts"**) → Multi-Campaign Workbench mode: Find **all active `CampaignConfig_*.yaml` files owned by that trader** (`team.trader` / `team.digest_to`), load their open `Proposed` and `Backlog` rows, and render **one unified Multi-Campaign Optimization Workbench card**.
  - **Campaign-Qualified Keys in Multi-Campaign Mode:** Because two campaigns can independently use overlapping `Opt ID` prefixes or numbers, **every** selectable-table `row_key` value (`<campaign_code>:<opt_id>`), every per-row target selector ID (`<campaign_code>_<opt_id>_domains`), and every per-row status selector ID (`<campaign_code>_<opt_id>_status_action`) in multi-campaign mode MUST be prefixed with the campaign code (e.g., `CMP00166:DCO-001`, `CMP00166_DCO_001_domains`, and `CMP00166_DCO_002_status_action`), while displaying `Campaign` and `Opt ID` (`DCO-001`) in the visible table columns. Decode the `<campaign_code>` prefix on submission.

### Workbench Card Specification (`show_ui`)
1. **Top KPI & Result Strip (`columns` of `stat` cards):**
   - Primary KPI vs. benchmark/prior week for each DSP, plus any newly completed 7-day `Kept` / `Reverted` outcome.
2. **Three Structured `tabs`:**
   - **Tab 1 — `1. Proposed SSP Optimizations (<N>)`:**
     - A multi-select `table` (`id="selected_opts"`, `select="multi"`, `row_key="opt_id"`) listing every open SSP-side `Proposed` row.
     - **Pre-selection rule:** Clean SSP rows are **pre-selected by default** (`value=[...]`). Rows carrying `[SCALE WARNING]` (cutting more than `cycle.max_deal_volume_cut_share`, default `0.40` when absent) or `[STALE]` are **unchecked by default** so the trader consciously opts in before cutting beyond their configured volume threshold.
     - **Inline Target Editor (`multi_select`):** For any multi-target optimization (e.g., a domain Block List), include an editable `multi_select` (`id="<opt_id>_domains"`, or `<campaign_code>_<opt_id>_domains` in multi-campaign mode) pre-populated from `OptTargets_<Client>_<Code>.csv` so the trader can uncheck individual domains or add a custom domain right inside the card.
   - **Tab 2 — `2. Console & Client Requests (<N>)`:**
     - A display `table` of open `Proposed` rows tagged `[Console/DSP]` or `[Client Request]`, paired with a per-row status selector (`id="<opt_id>_status_action"`, or `<campaign_code>_<opt_id>_status_action` in multi-campaign mode) so the trader can mark `<Opt ID> is Live` (`Status = Live`, `Live = <today>`) or `<Opt ID> Sent to Client` (`Status = Proposed`, logging `"<today>: Sent to client (<trader>)"` in `Notes`) in one click.
   - **Tab 3 — `3. Backlog & Signals (<N>)`:**
     - A multi-select `table` (`id="promote_backlog"`, `select="multi"`, `row_key="opt_id"`) of the top `Backlog` candidates so the trader can check any row they want to promote early.
3. **Inline Validation / Question Box (`text_input`, `id="trader_question"`, `multiline=true`):**
   - Allows the trader to type any validation question before committing to an update.
4. **Two Footer Actions:**
   - **Primary Submit (`id="execute_selected"`, `style="primary"`):** `Apply Selected Updates`
   - **Secondary Submit (`id="ask_questions"`, `style="secondary"`, `validate=false`):** `Ask / Validate First`

### How Workbench Submissions Are Handled
- **Step 0 — Apply Status Confirmations, Backlog Promotions & Inline Target Edits on EVERY Submission (`ask_questions` AND `execute_selected`):**
  - Before running either action's specific logic, process any submitted changes using the canonical 10-column `OptLog` lifecycle fields (`Backlog`, `Proposed`, `Live`, `Kept`, `Reverted`, `Dropped`):
    1. **`*_status_action` (with strict `:live` > `:sent` precedence per `Opt ID`):**
       - If both `live` and `sent` are submitted for the same `Opt ID`, `live` always takes precedence (`Status = Live`, `Live = <today>`).
       - For `live`: set `Status = Live`, `Live = <today>`, and append `"<today>: Confirmed live (<trader>)"` to `Notes` (skipping if the row is already `Live`).
       - For `sent` (when `live` is not selected): keep `Status = Proposed` and append `"<today>: Sent to client (<trader>)"` to `Notes` (idempotent if already logged today).
    2. **`promote_backlog`:** Promote each checked `Backlog` row to `Status = Proposed`, `Proposed = <today>` in `OptLog`, **and materialize its candidate target values as rows in `OptTargets_<Client>_<Code>.csv`** (matching the `"propose <Opt ID>"` text-command behavior) so the newly `Proposed` row has complete `OptTargets` rows ready for execution and go-live checks.
    3. **Inline target edits (`<opt_id>_domains` / `<campaign_code>_<opt_id>_domains`):** Replace the target group for that `(Opt ID, Lever)` in `OptTargets_<Client>_<Code>.csv` (and update the row's target count in `OptLog`) if the trader unchecked or added target values.
    4. Persist those `OptLog` / `OptTargets` updates in place using the **Read-Before-Write Merge Guardrail** (with per-campaign `.lock` file and composite `(Opt ID, Lever, Value)` merge key).
- **When the trader clicks `Ask / Validate First` (`action="ask_questions"`):**
  1. After applying Step 0 above, load the campaign's DSP reports in `run_python`, answer `trader_question` concisely with exact numbers, and **re-render the Workbench card** (`replaces=<old_card_id>`) preserving unapplied `selected_opts` and `*_domains` selections while **resetting all already-applied `*_status_action` controls back to `"none"`** (and removing promoted rows from `promote_backlog`) so a subsequent button click never re-applies `"sent"` or overwrites a declared `Live` date.
- **When the trader clicks `Apply Selected Updates` (`action="execute_selected"`):**
  1. **Apply Step 0 above** first so no status, backlog promotion, or inline target edit is ever dropped.
  2. **Branch on Bundle Connector Contract (`manifest.yaml`):**
     - **Case A — When `manifest.yaml` does NOT register `deal_plan` and SSP seat connectors** (standard analytics/reporting bundles):
       - Do **not** ask the trader to enable nonexistent Deal Planner or SSP connectors in the Tools picker.
       - Compute the additive target lists (unioning `Kept` + `Live` + selected `Proposed` block targets from `OptTargets`, or keeping only removal targets for allow lists), persist the updated `OptTargets` and `OptLog` rows under the Read-Before-Write Merge Guardrail, and output a copy-paste-ready **Target List & Operator Execution Summary** (along with the **Post-Execution Verification Receipt** table template and `[Open in <SSP> ↗](<deal_url>)` links) so the trader can apply the changes in the SSP console and mark them `Live`.
     - **Case B — When `manifest.yaml` DOES register `deal_plan` and SSP seat connectors** (for example, `reklaim-config`):
       - **Verify Required Connectors:** Check `ssp_seats` and `deals` in `CampaignConfig` to identify each target deal's SSP and seat (`mcp_prefix`), plus `deal_plan` (`mcp_deal_plan_*`). Call `enable_mcp_connectors` if listed under Available Optional MCP Connectors.
       - **Pre-Flight Live Deal Read — Additive Block-List Union vs. Allow-List Removal:**
         - **Block List (`add block` / `include block`):** Take the **additive union** of all block values from `OptTargets` rows with `Status` in `Kept` + `Live` + the newly selected `Proposed` row (excluding `Reverted` or `Dropped` rows) **AND** union with any pre-existing baseline block list currently live on the SSP deal.
         - **Allow List (`remove allow`):** Pass **ONLY** the selected removal target values (plus any prior `Kept`/`Live` removal targets in `OptTargets` for that deal) to `remove allow` — **NEVER** union removal targets with the live allow list.
       - **Magnite DV+ / Shared-List Isolation Rule (Create-Then-Swap):** On Magnite DV+, **never** edit a shared list used by other campaigns and **never** attach another campaign's block list. Keep any account-level baseline blocklist intact (`baseline_block_list_ids`) and attach/update a **campaign-dedicated list** named `<Client>_<CampaignCode>_Optimization_Block_List`:
         - When `optimization_block_list_id` is `null`, first stage `magnite_create_targeting_list` (`name="<Client>_<CampaignCode>_Optimization_Block_List"`, initial block domains) and wait for trader approval; once created, persist the returned list ID to `deals["<dsp_id>::<Deal Name>"].optimization_block_list_id` in `CampaignConfig`, and only then stage the deal update plan that attaches `optimization_block_list_id` alongside preserved `baseline_block_list_ids` and detaches any foreign campaign list.
       - **Route Through Deal Planner Using `campaign.deal_plan_client_id`:**
         - Use `campaign.deal_plan_client_id` from `CampaignConfig.yaml` (the bundle's Deal Planner profile key in `deal_profiles/<id>.yaml`, e.g. `"reklaim"` — never an advertiser or campaign name) to load `get_deal_profile(client_id)`.
         - Present the selected SSP changes on `mcp_deal_plan_recommendation_card` when required by `system_prompts/chat.md`, then call `mcp_deal_plan_compile_update_plan(recommendation_set=..., selected=...)` → `mcp_deal_plan_finalize_plan(plan_id, rows=<selected row numbers 1..N>)` → stage `mcp_deal_plan_execute_plan(plan_id)`.
  3. **Post-Approval Verification & Receipt:**
     - When an `execute_plan` approval completes (resumed automatically when `execute_plan` is in `manifest.yaml`'s `critical_tool_resume`, or on the follow-up turn):
       1. **Check `plan_status` & SSP Readback:** Confirm `confirmed_after_update` (or call `*_get_deal` / `*_find_deals` if any item is `unclear`).
       2. **Audit Every Target Value Against `OptTargets`:** Verify that:
          - Every domain/app/value from `OptTargets` for the executed `Opt ID` is present on the deal's Block List (or removed from its Allow List),
          - The prior `Kept` / `Live` entries count did not drop,
          - On Magnite DV+, the campaign's `optimization_block_list_id` and any recorded account-level `baseline_block_list_ids` are attached while **no foreign campaign's targeting list** is attached, and
          - The deal's status remains `Active`.
       3. **Render the Post-Execution Verification Receipt in Chat:** Output a compact table with one row per deal:
          - **`Opt ID` & Deal Name**
          - **SSP Deal ID + Direct Console Link** (`[Open in <SSP> ↗](<deal_url>)`)
          - **`Before → After` Summary**
          - **Verification Status:** `VERIFIED` or `FLAGGED`
       4. **Close the Loop in `OptLog`:** For `VERIFIED` rows, set `Status = Live`, `Live = <today>`, append `"<today>: Executed & verified live on SSP (<before -> after>) (<trader>)"` to `Notes`, and save `OptLog` and `OptTargets` in place under the Read-Before-Write Merge Guardrail.

---

## 7. Client-Facing Optimization Story Emails (`show_ui`) & Spend Elasticity Pitch

While scheduled review notifications are strictly internal (routed to `team.digest_to`), traders frequently ask in chat for a **client-facing showcase email** that tells a compelling story around the optimizations executed or verified — or evaluates whether the campaign can absorb **incremental budget**.

### 7.1 Non-Negotiable Client-Facing Rules (Zero Jargon, Single-KPI Focus)
Every client-facing optimization email MUST follow these rules automatically:
1. **Strip All Internal IDs & Ad-Ops Plumbing (Body AND Attachments):**
   - **NEVER include** internal campaign codes (`CMP00166`) in the subject line, header subtitle, executive summary, or attachments.
   - **NEVER include** internal Opt IDs (`DCO-001`, `ACM-010`), raw SSP Deal IDs (`IX176...`, `MGNI-MD-...`), targeting list IDs (`70417`), or API mechanics (`NONE_OF`, `ANY_OF`) anywhere in the email body or in any attached file.
   - Refer to deals by their **human-readable program/trigger name + SSP/channel** (e.g., **Winter Weather OLV (Index Exchange)**).
2. **Laser-Focus on a Single Coherent Primary KPI per Section:**
   - Anchor every headline card, chart, and table on the selected DSP's `kpi.primary.name` (`ROAS`, `CTR`, `CPA`, `VCR`) and its benchmark. When a campaign has multiple `dsps[]` entries or channel-level KPI overrides (`channel_kpis`), the Story Builder card (`primary_kpi_scope`) requires selecting either one specific DSP/channel KPI OR rendering separate per-DSP / per-KPI chart and table sections — never average or combine incomparable KPIs on a single chart axis.
3. **Punchy, Scannable Structure Over Long Prose:**
   - Keep the Executive Summary to **2 short, high-impact sentences**: (1) current 7-day KPI performance vs. benchmark, and (2) what optimizations were deployed today and their projected (or realized) KPI lift while preserving scale.
4. **Single-Panel, Brand-Themed Chart (`1088px` wide):**
   - Always generate a **single-panel horizontal bar chart** (`figsize=(10.88, 4.2)`, `dpi=100`) with direct data labels at the end of each bar and the legend placed **above** the plot area (`bbox_to_anchor=(0.5, 1.16)`, `ncol=3`, `frameon=False`).
   - **Read all chart colors from `protocols/email-style.yaml` (`chart_colors`):** use `comparison_series` for Actual KPI bars, `primary_series` for Post-Curation KPI bars, `text` for the dashed benchmark line and labels, `background` for figure/axes fill, and `grid` for gridlines so the chart PNG always matches the deployment's brand palette.

### 7.2 Interactive Menu of Story Archetypes & Modular Sections (`show_ui`)
When a trader asks to craft a client-facing optimization email (`"craft a client email for <Client>"`, `"client story email for <Client> opts"`), present the **`show_ui` Story Builder card** (Blueprint 3 in `skills/campaign-optimization-log/REFERENCE.md`):

#### A. Pick the Story Archetype (`choice` — 1-Click Preset)
| Archetype | When to Use | Default Focus |
|---|---|---|
| **1. `just_executed` — "Just Executed" Proactive Curation Showcase** *(Default)* | Right after executing `Proposed` optimizations | Highlights current 7d momentum + exact changes made today + projected KPI lift + 28d proof that removed supply was non-performing |
| **2. `proven_lift` — "Proven Lift" Before-vs-After Readout** | After `Live` optimizations hit 7+ days (`Kept`) | Highlights verified pre-vs-post KPI improvement on curated deals and total media dollars saved/reallocated |
| **3. `expansion_pitch` — Strategic Allocation & Incremental Budget Pitch** | When top-performing deals beat benchmark and can absorb more spend | Pairs recent supply hygiene with the **4-Test Spend Elasticity Analysis** (Section 7.3) and a concrete incremental budget recommendation |

#### B. Pick the Modular Email Sections (`multi_select` Checkboxes)
- [x] **Block 1 — Punchy 2-Sentence Executive Summary**
- [x] **Block 2 — Headline KPI Snapshot Cards (`headline_metrics`)**
- [x] **Block 3 — Single-Panel Deal KPI Lift Chart (`chart`)**
- [x] **Block 4 — Deal Performance & Lift Table (`table`)**
- [x] **Block 5 — "Optimizations Implemented" Detail Table (`table`)** (with `domain_granularity` selector: `full_list` | `top_examples` | `category_summary`)
- [x] **Block 6 — Phase 2 Curation Roadmap Callout (`callout`)**
- [ ] **Block 7 — Spend Elasticity & Incremental Budget Pitch (`callout` + `table`, auto-checked for Archetype 3)**
- [ ] **Attach Client-Safe KPI & Channel Summary Table (`attach_kpi_summary_xlsx` toggle, `.xlsx`):** Generates a clean, client-safe `.xlsx` workbook summarizing campaign, channel, and deal-program KPI performance **without any raw SSP deal IDs, internal Opt IDs, or targeting-list IDs** so external recipients never receive internal ad-ops identifiers.

#### C. Rendering & Staging the Email (`mcp_ses_outbound_render_email` → `mcp_ses_outbound_send_email`)
Once the trader submits the Story Builder card:
1. If `attach_kpi_summary_xlsx == true`, generate the sanitized client-safe KPI summary `.xlsx` in the working directory (`kpi_summary_xlsx_path`), verifying that it contains zero raw SSP deal IDs, targeting-list IDs, or internal campaign/Opt codes.
2. Call `mcp_ses_outbound_render_email` following `protocols/email-style.yaml`.
3. Take the returned `content_file` and `inline_attachments` list (which includes the rendered brand header logo), append the chart PNG mapping `{"path": chart_png_path, "cid": "story_chart"}` to `inline_attachments`, and call `mcp_ses_outbound_send_email` passing `content_file`, `inline_attachments`, and (when `attach_kpi_summary_xlsx == true`) `attachments=[kpi_summary_xlsx_path]`.

### 7.3 The 4-Test Spend Elasticity & Incremental Budget Evaluator
Whenever a trader asks *"Can this campaign absorb more budget without hurting `<KPI>`?"* or selects **Archetype 3 (`expansion_pitch`)**, run these 4 empirical tests in `run_python` across the full 28-day report window (`runtime_today - 27 days` through `runtime_today`) before recommending a budget increase:
1. **Test 1 — Daily Spend vs. Daily KPI Elasticity (Quartiles & Correlation):**
   - Group daily performance across the 28-day window into **Low-Spend Days**, **Mid-Spend Days**, and **Peak-Spend Days** (and compute the Pearson/Spearman correlation between daily spend and daily KPI).
2. **Test 2 — Post-Curation Supply Headroom:**
   - Calculate the exact impression and spend share retained after applying the active Block Lists.
3. **Test 3 — High-Efficiency Channel / Deal Mix Headroom:**
   - Identify deals or channels outperforming the benchmark that currently receive a small share of impressions.
4. **Test 4 — Recommended Safe Incremental Scaling Band & Direction-Aware KPI Guardrail:**
   - Translate Tests 1–3 into a concrete **Safe Daily & Weekly Budget Scaling Band** paired with a direction-aware **KPI Guardrail Bound** derived from `kpi.primary.direction`:
     - When `kpi.primary.direction == "higher_is_better"` (`ROAS`, `CTR`, `VCR`, `Viewability`): set a **KPI Guardrail Floor** (`max(benchmark, trailing_7d_actual * 0.90)`).
     - When `kpi.primary.direction == "lower_is_better"` (`CPA`, `CPC`, `CPM`, `CPCV`): set a **KPI Guardrail Ceiling** (`min(benchmark, trailing_7d_actual * 1.10)`).

---

## 8. Quick Text Commands Reference (Supported Anytime in Chat)

| Trader says | Agent does |
|---|---|
| `"onboard <Client> <CampaignCode>"` (or `"set up a campaign config for <Client>"`) | Runs Auto-Discovery & Day-1 Preflight QA, then opens the interactive **`show_ui` Campaign Config Builder Card** (Section 2) |
| `"review <Client> opts"` (or `"open <Client> workbench"`) | Renders the interactive **Optimization Workbench card** (`show_ui`, Section 6) for that campaign |
| `"review my opts"` (or `"open my workbench"`) | Renders a **unified Multi-Campaign Optimization Workbench card** across all active campaigns owned by that trader |
| `"execute ACM-018.1, ACM-019.1"` (or `"execute all clean"`) | Unions targets with prior `Kept`/`Live` rows and applies/exports or stages the SSP update plan |
| `"craft a client email for <Client>"` (or `"client story for <Client> opts"`) | Opens the interactive **`show_ui` Client-Facing Optimization Story Builder** (Section 7) |
| `"can <Client> take incremental budget?"` (or `"spend elasticity for <Client>"`) | Runs the **4-Test Spend Elasticity & Incremental Budget Evaluator** (Section 7.3) |
| `"ACM-006 is live"` (optionally `"since 10/9"`) | Status `Live`, Live date, note who said it |
| `"revert ACM-008"` / `"keep ACM-005"` | Set the decision early, reason in `Notes` (and if reverting an SSP change, remove its targets while keeping earlier `Kept` lists intact before setting `Status = Reverted`) |
| `"drop ACM-008, <reason>"` | Status `Dropped`, reason in `Notes` |
| `"log this"` / a concrete, data-backed idea in exploration | Add as `Backlog`, `Notes` `"Source: Chat <date> (<trader>)"`; confirm in one line |
| `"propose ACM-010"` | `Backlog` → `Proposed` (and write `OptTargets` rows if multi-value) |
| `"sync all campaign configs"` | Upgrades all `CampaignConfig_*.yaml` files in `Optimizations/` to the latest `schema_version` in place |
| `"update the optimization protocol: <change>"` | Updates `Optimizations/Protocols/optimization-log-protocol.md` in place and bumps the version/changelog |
| `"cover <Trader>'s campaigns for <Backup> until <Date>"` | Sets `team.backup_trader` and `team.backup_until: "<YYYY-MM-DD>"` on `<Trader>`'s active configs (auto-cleared once `today > team.backup_until`) |

---

## 9. Workflow Health (First Full Review of Each Month) & Guardrails

### Monthly Workflow Health (First Full Review of Each Month)
On the first full review of each month, include a compact 4-bullet Workflow Health note (omit if all metrics are `N/A` on a newly onboarded trader's first week):
1. Share of `Live` changes that reached a decision (target ≥ 80%)
2. Median days from `Proposed` to `Live`
3. Rows dropped (`Status == "Dropped"`) for process reasons (setup or KPI changes) rather than data
4. Trader corrections to runs in chat

### Non-Negotiable Guardrails
- Levers, paths and platform limits come only from the config, `manifest.yaml`, and `skills/ssp-deal-capabilities/SKILL.md`.
- Every number cites its report and window. Never report ROAS/CPA for a DSP with `no_conversion_tracking: true`.
- Use `"Block List"` / `"Allow List"`, never the older terms.
- Scheduled review notifications are strictly internal: automated runs send strictly to the config's `team.digest_to` (plus `team.backup_trader` while `not team.backup_until or runtime_today <= team.backup_until`, and `team.cc` if set) and never email a client directly. Client-facing optimization showcase emails are crafted on-demand in chat (Section 7) and sent only to the recipients the trader approves on the send card.

---

## Changelog

- **v3.0 (2026-10-22):** promoted to the workspace bundle (`protocols/optimization-log-protocol.md` + `skills/campaign-optimization-log/SKILL.md` + Prompt Library starters); added the **Interactive `show_ui` Campaign Config Onboarding Card** with **Day-1 DSP Report Preflight QA** and trader-confirmed eligibility thresholds; unified the **Chat-First Optimization Workbench (`show_ui`)** (`"review <Client> opts"` / `"review my opts"`), **Magnite DV+ Shared-List Isolation Rule**, **Supply-Expansion Levers**, **Read-Before-Write Merge Guardrail**, and **Post-Execution Verification Receipt** with **Section 7: Client-Facing Optimization Story Emails (`show_ui`)** and the **4-Test Spend Elasticity & Incremental Budget Evaluator**.
- **v2.8 (2026-10-22):** standardized `ssp_seats` and `deals` mappings; enforced bare SSP-executable values in `OptTargets.Value`; added Magnite DV+ Shared-List Isolation Rule, `"review my opts"` Multi-Campaign Workbench mode, Supply-Expansion levers, Read-Before-Write Merge Guardrail, and Section 7 Client-Facing Optimization Story Emails.
- **v2.7 (2026-10-21):** renamed `review_days` → `optimization_days` and `state.last_full_review` → `state.last_optimization_run`; added `schema_version` and Automatic Schema Migration; added mandatory Pre-Flight Live Deal Read and Post-Approval Verification Receipt.
- **v2.6 (2026-10-21):** introduced the Chat-First Optimization Workbench (`show_ui`) and compact "Opts Ready in Chat" notification email.
- **v2.5 (2026-10-16):** captured KPI `benchmark` upfront at onboarding; added `channel_benchmarks` and `channel_kpis`; added Pacing/Scale Protection (`max_deal_volume_cut_share: 0.40`), Additive SSP Execution, `team.cc` / `team.backup_trader`, and End-of-Flight Closeout Run.

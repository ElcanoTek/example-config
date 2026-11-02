# Optimization Log Protocol (v3.0, 2026-10-22)

One protocol for the whole campaign optimization lifecycle: **Intelligent UI (`show_ui`) Campaign Config Onboarding & Day-1 Preflight QA**, the persistent Optimization Log (`OptLog` + `OptTargets`), the scheduled weekday review runner, the **Chat-First Optimization Workbench (`show_ui`)**, additive SSP execution via the Deal Planner (`deal-booking`), **Client-Facing Optimization Story Emails (`show_ui`) & 4-Test Spend Elasticity / Incremental Budget Evaluation**, and end-of-flight closeout.

Campaign-specific settings (storage location, trader owner, sources, KPI & channel overrides, levers, SSP seats & deal IDs, platform limits, optimization days, and state) live in each campaign's config (`CampaignConfig_<Client>_<Code>.yaml`), never hardcoded in prompts.

Each review answers four questions, in this order: **What did we recommend? Is it live? Did it work? What's next?** Anything that doesn't serve one of those is out of scope.

---

## 1. Files & Flexible Storage Organization (`shared/`, `fast.io`, or Custom Path)

Every campaign uses **3 co-located files** (backed by this protocol, `protocols/CampaignConfig_TEMPLATE.yaml`, and `protocols/optimization-trader-quickstart.md`):

| File | Purpose |
|---|---|
| `CampaignConfig_<Client>_<Code>.yaml` | Settings: `storage` location, trader owner & email, `campaign.curator`, `ssp_seats`, `deals` (deal name → SSP deal ID & internal API ID), DSP report sources, KPI & channel benchmarks, levers, platform limits, optimization days, `schema_version`, and `state` (`last_optimization_run`, `last_light_review`, `carried_over`, `wrap_completed`) |
| `OptLog_<Client>_<Code>.csv` | The log: one row per change per deal (10 columns) |
| `OptTargets_<Client>_<Code>.csv` | Exact values per Opt ID (Block List domains/apps, cities, OS, hours) used for go-live checks, inline Workbench editing, and additive SSP execution (5 columns). Required whenever a row targets more than one value |
| `protocols/CampaignConfig_TEMPLATE.yaml` | Starter YAML template for onboarding new campaigns |
| `protocols/optimization-trader-quickstart.md` | 1-page Trader Quick-Start cheat sheet |

### Storage locations & discovery (`storage` block)
Traders can save a campaign's 3 files wherever fits their workflow (chosen in the `show_ui` Onboarding Card or stated in chat):
- **Fleet Shared Workspace Files (`storage.type: shared`, default):**
  - Stored under `shared/Optimizations/` (or `shared/Optimizations/<Client>_<Code>/`) inside the shared file library and indexed in `shared/Optimizations/CampaignRegistry.yaml`.
- **Fast.io workspace (`storage.type: fastio`, when Fast.io is configured):**
  - Preferred folder: `/Optimizations/` (or `/Optimizations/<Client>_<Code>/`), workspace root (`/`), or any custom folder/workspace path the trader specifies in `storage.folder_path`.
  - **Discovery & Registry rule:** At the start of any run or chat session, discover configs in `shared/Optimizations/` and (when Fast.io is configured) by paginating `mcp_fastio_helpers_find(query="CampaignConfig_", node_type="file", limit=100, offset=0)` (advancing `offset` by 100 while `has_more` is true) and reading `CampaignRegistry.yaml` for any configs stored in custom folders or workspaces. Whenever onboarding saves a campaign, it also upserts `{campaign_id, storage_backend, workspace_id, folder_path, config_filename}` into `CampaignRegistry.yaml` so cross-campaign queues (`review my opts`, weekday runner, `sync all campaign configs`) always discover it.
- **Local Conversation Workspace (`storage.type: workspace` — manual single-conversation mode only):**
  - Written to the conversation working directory with clickable markdown download links (`[CampaignConfig_<Client>_<Code>.yaml](CampaignConfig_<Client>_<Code>.yaml)`). Because conversation workspaces are isolated to a single chat, `workspace_local` is strictly for **manual single-conversation use** and is not included in scheduled runs or cross-session `review my opts` unless saved to `shared/` or Fast.io.
- **Co-location rule:** Always read and write `OptLog_<Client>_<Code>.csv` and `OptTargets_<Client>_<Code>.csv` in the **same folder (`parent_node_id` / directory)** as that campaign's `CampaignConfig_<Client>_<Code>.yaml`.
- **In-place updates:** Edit files in place by reading the latest version, applying the change, and writing/uploading under the same name into the same directory or `parent_node_id`. Never delete and re-create.
- **Self-serve protocol & skill updates:** A workspace override copy of this protocol at `shared/Protocols/optimization-log-protocol.md` (or `/Protocols/optimization-log-protocol.md` in Fast.io when configured) is checked alongside the bundled `protocols/optimization-log-protocol.md`. Traders can update the protocol anytime in chat (`"update the optimization protocol: <change>"`) — the agent edits `shared/Protocols/optimization-log-protocol.md` (or `/Protocols/optimization-log-protocol.md` in Fast.io) in place (bumping the version header and changelog) so both the weekday scheduled runner and `/campaign-optimization-log` in chat pick up the change immediately. Traders can also click **Clone & edit** on `campaign-optimization-log` in **Settings → Skills** (or on any Optimization workflow in the **Prompt Library**) and check **Share with this workspace** to publish skill or prompt overrides without an engineering deploy.

### `OptTargets` columns (5)

| Column | Content |
|---|---|
| `Opt ID` | Matches the log row, including the deal suffix (e.g. `NWO-011.1`, `ACM-001`) |
| `Lever` | Same lever as the log row's Change prefix (`Domain Block List`, `Domain Allow List`, `Daypart`, `Geo Targeting`, `Device/OS/Browser Targeting`) |
| `Value` | One **clean SSP-executable** entry per row: bare domain (`dailymotion.com`), bare app bundle/store ID (`in.playsimple.wordsearch`), city, OS, or hour range (`00:00-05:59`). Never put display labels like `Word Search Explorer (ID: in.playsimple.wordsearch)` in `Value` — put human display names in `OptLog.Notes` so `OptTargets` passes SSP API validation directly |
| `Added` | ISO date (`YYYY-MM-DD`) |
| `Source` | `agent` or the trader's name |

Write the targets **when the row is proposed**, in the same upload batch as the log. A Change that names a count ("Add the 21 sites") without targets rows is incomplete. If targets are missing at go-live time, check the values named in `Baseline`/`Notes`, mark the note `partial check (n of N values)`, and list the row under "Waiting on you" until the full list is supplied.

---

## 2. Guided Campaign Config Onboarding & Day-1 Preflight QA (`show_ui`)

Traders never need to hand-edit YAML files. When a trader asks to set up a campaign config — e.g., **"onboard `<Client>` `<CampaignCode>` to the optimization log"**, **"set up optimization tracking for `<Client>`"**, **"build a campaign config for `<Client>`"**, or selects **Onboard Campaign to Optimization Log** from the Prompt Library:

### Step 2.1 — Auto-Discovery & Day-1 DSP Report Preflight QA
Before showing the onboarding card, auto-discover everything available so the form is pre-filled and validated:
1. **Locate & Download DSP Reports:**
   - Search `shared/`, Fast.io (`mcp_fastio_helpers_find` if configured), and/or the report inbox (`mcp_explorer_find_latest_report`) to find the latest DSP report drop(s). For mailbox sources, `mcp_explorer_find_latest_report` returns message metadata (`s3_key`, attachment filenames, report links) only — always download the report payloads into your working directory via `mcp_explorer_get_download_link(s3_key=..., filename=...)` + `download_url(url=<its download_url>)` or `mcp_explorer_get_download_link(s3_key=..., url=...)` + `download_url(url=<its download_url>)` and verify the files exist on disk before inspecting them.
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
3. **Auto-Resolve SSP Seats & Deal IDs:**
   - Populate `campaign.curator` and `ssp_seats` (mapping each SSP to its Fleet connector seat/prefix).
   - Query live SSP connectors (`*_find_deals` / `*_get_deal`), deal profiles, or archived Deal Sheets to populate `deals` with each deal's public buyer-facing `deal_id` (e.g. `IX...`, `OX-...`, `MGNI-...`), `internal_deal_id`, `channel` (`Display`, `OLV`, `CTV`, `Native`), and direct console `deal_url`.

### Step 2.2 — Present the Interactive `show_ui` Campaign Config Builder Card
Present one structured `show_ui` card (`title: "Campaign Optimization Setup — <Client> (<CampaignCode>)"`) pre-populated with everything discovered in Step 2.1:
1. **Day-1 Preflight Status Banner (`status_list`):**
   - Shows pass/warn status for: (a) DSP Report Source & Date Window, (b) Dimensional Coverage (`overall`, `domain`, `geo`, `time_of_day`, `system`), (c) KPI Formula & Column Check, and (d) Active SSP Deals Linked (`N` deals mapped to SSP Deal IDs).
2. **Tab 1 — `1. Campaign, Team & Save Location`:**
   - `client_name` (`text_input`), `campaign_code` (`text_input`), `opt_id_prefix` (`text_input`, 2–4 uppercase letters, e.g. `ACM`, `NWO`, `CH`), `start_date` & `end_date` (`date`).
   - `trader_name` (`text_input`), `digest_to` (`text_input`, trader's internal email), `cc_emails` (`text_input`, optional), `backup_trader` (`text_input`, optional).
   - `optimization_days` (`multi_select` of `MON`..`FRI`, pre-filled with the trader's preferred day) and `light_days` (`multi_select` of `MON`..`FRI`).
   - **Where to Save (`storage_target` `select` + `storage_custom_path` `text_input`):**
     - `shared_files`: `Fleet Shared Workspace Files (shared/Optimizations/) (Recommended)`
     - `fastio_optimizations`: `Fast.io — /Optimizations/`
     - `fastio_campaign_folder`: `Fast.io — /Optimizations/<Client>_<Code>/`
     - `fastio_root`: `Fast.io — Workspace Root (/)`
     - `workspace_local`: `Conversation Workspace Only (Download YAML/CSVs in chat)`
     - `custom`: `Custom Folder or Path (specify below)`
3. **Tab 2 — `2. KPI, Benchmarks & Levers`:**
   - `primary_kpi` (`select`: `ROAS`, `CTR`, `CPA`, `VCR`, `Viewability`, `CPC`, `CPM`), `kpi_direction` (`choice`: `higher_is_better` vs `lower_is_better`), and `kpi_benchmark` (`number`, e.g. `33.0` for 33x ROAS or `0.08` for 0.08% CTR). When multiple DSPs are discovered during preflight (`len(dsps) > 1`), render **repeatable per-DSP KPI field groups** (`<dsp_id>_primary_kpi`, `<dsp_id>_kpi_direction`, `<dsp_id>_kpi_target`, `<dsp_id>_benchmark_ctv`, `<dsp_id>_benchmark_olv`, `<dsp_id>_benchmark_display`) for each DSP so every `dsps[]` entry receives its own KPI block.
   - `use_baseline_fallback` (`toggle`, default `false`): *"No client benchmark yet — compute initial baseline from the first report window."*
   - `channel_overrides` (`text_input`, optional): Per-channel benchmark or KPI overrides (e.g., `Display: CTR >= 0.08%, OLV: VCR >= 75%`).
   - `ssp_direct_levers` (`multi_select`, pre-checked: `domain_block_list`, `domain_allow_list`, `deal_targeting`, `daypart`, `geo_targeting`, `device_os_browser_targeting`) and `client_request_levers` (`multi_select`, pre-checked: `bid_adjustment`, `budget_shift_between_line_items`, `budget_shift_between_insertion_orders`).
   - `max_volume_cut_pct` (`slider` or `number`, default `40`): Scale warning threshold (% of deal volume).
4. **Tab 3 — `3. Active Deals & Report Sources (<N>)`:**
   - Multi-select `table` (`id="active_deals"`, `select="multi"`, `row_key="deal_id"`, all active campaign deals pre-selected) showing `Deal Name`, `SSP (Seat)`, `Channel`, `Deal ID`, and `7d Spend`.
   - `report_source_type` (`choice`: `mailbox` vs `fastio`), `report_source_path` (`text_input`), `report_mailbox` (`text_input`), and `deal_field` (`text_input`). When multiple DSPs are discovered during preflight (`len(dsps) > 1`), render **repeatable per-DSP report source controls** (`<dsp_id>_report_source_type`, `<dsp_id>_fastio_source_path`, `<dsp_id>_mailbox_address`, `<dsp_id>_deal_field`) for every DSP found so each `dsps[]` entry in `CampaignConfig` is completely populated.
5. **Card Actions:**
   - **Primary Submit (`id="save_config"`, `style="primary"`):** `Save Config & Initialize Log`
   - **Secondary Submit (`id="save_and_run"`, `style="secondary"`):** `Save Config & Run First Optimization Review Now`

*(Fast-path rule: if the trader explicitly says `"just create and upload the config now without showing the form"` and provided all required fields, skip the card and initialize the 3 files directly.)*

### Step 2.3 — Initialize & Save All 3 Files
When the trader submits the Onboarding Card:
1. Build and validate `CampaignConfig_<Client>_<Code>.yaml` via `yaml.safe_load` (`schema_version: "3.0"`), populating a complete `dsps[]` entry for every configured DSP.
2. Initialize `OptLog_<Client>_<Code>.csv` with header row:
   `Opt ID,DSP,Deal,Change,Status,Proposed,Live,Baseline,Result,Notes`
3. Initialize `OptTargets_<Client>_<Code>.csv` with header row:
   `Opt ID,Lever,Value,Added,Source`
4. Save all three files to the trader's selected `storage` destination (e.g., writing to `shared/Optimizations/` or uploading via `mcp_fastio_helpers_upload_file` when Fast.io is configured, and providing clickable download links in chat). Also upsert the campaign pointer in `shared/Optimizations/CampaignRegistry.yaml` (or `/Optimizations/CampaignRegistry.yaml` in Fast.io). If saved as `workspace_local`, remind the trader that local conversation files are manual-only and excluded from cross-session / scheduled multi-campaign discovery.
5. If the trader clicked **`Save Config & Run First Optimization Review Now`**, immediately run the initial Optimization Day scan (Section 5) and open the **Optimization Workbench card** (Section 6) in the same turn! For unattended weekday runs (Section 5), confirm whether a recurring weekday task (`Optimization Log — Weekday Campaign Runner` at `15:00 UTC` Mon–Fri) is active or remind the trader they can schedule/trigger it anytime via **Make recurring task…** or `"run optimization review for <Client>"`.

---

## 3. Log Columns (10) & Lifecycle

| Column | Content |
|---|---|
| `Opt ID` | `<PREFIX>-NNN.d`: NNN = one action, `.d` = one deal (`NWO-002.1`, `ACM-001`). Single-row items have no suffix. Never reused |
| `DSP` | DSP from the config (`DV360`, `Amazon DSP`, etc.) |
| `Deal` | Exact deal name from the report's `deal_field`, or `n/a (line-item level)` |
| `Change` | `<Lever>: <what changes>`, imperative. Add `— client request` when the lever is a client request in the config |
| `Status` | See lifecycle below |
| `Proposed` | ISO date (`YYYY-MM-DD`) |
| `Live` | ISO date the change went live (detected or trader-declared) |
| `Baseline` | At proposal: deal KPI and the targeted segment's numbers, with numerator/denominator, spend and window |
| `Result` | Early read (≥ 3d), then final before → after on the KPI with windows (≥ 7d), plus one plain-English value sentence with a number |
| `Notes` | Append only: `YYYY-MM-DD: <what happened, who>`. Execution path (`[Chat API]`, `[Console/DSP]`, `[Client Request]`), go-live check, scale warnings, human display names for app bundles, and open-row goal go here |

### Lifecycle

`Backlog` → `Proposed` → `Live` → `Kept` / `Reverted`

Any status → `Dropped` (reason required in `Notes`; use `"inconclusive"` when a change could not be judged). Dropped rows stay in the log so they aren't re-proposed unless the data materially changes (cite the old Opt ID).

- **Backlog**: qualifying ideas not in the current package, including chat ideas. Not re-scored. Listed in the Workbench card (Tab 3). Dropped as `"expired"` after 21 days without promotion.
- **Proposed**: in the current package, waiting on implementation.
- **Live**: implemented; being measured.
- **Kept / Reverted**: decided after at least 7 delivery days.

### Before the first row: lock the KPI

Do not log a `Proposed` row until the config's primary KPI (name, formula, direction) is final and `status: active`. A benchmark is captured at onboarding (or a baseline is computed only when no benchmark is provided). If the KPI changes after rows exist, do not mass-drop them: re-baseline open rows in `Notes` on the new KPI and keep going. If a KPI is `blocked` with an `unblock_check`, run that check on every run and email the trader once when it passes.

---

## 4. Config Keys, Automatic Schema Migration, Trader Routing & PTO Coverage

Canonical names below. Legacy names are still accepted so older configs keep running, and self-healing migration upgrades them automatically.

| Canonical key | Legacy alias (auto-migrated) | Default |
|---|---|---|
| `schema_version` | — | `"3.0"` (backward-compatible with `"2.8"`; triggers auto-migration when older) |
| `storage.type` | — | `"shared"` (`shared` \| `fastio` \| `workspace`) |
| `storage.workspace_id` | — | `null` (optional Fast.io workspace ID when `storage.type == "fastio"`) |
| `storage.folder_path` | — | `"shared/Optimizations"` (or `"/Optimizations"` in Fast.io) |
| `campaign.curator` | — | `"default"` (or seat identifier from `ssp_seats`) |
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
| `cycle.max_deal_volume_cut_share` | — | `0.40` (>40% impression/spend removal triggers a Scale Warning) |
| `cycle.materiality`, `cycle.min_sample`, `cycle.evaluation_days` | — | as in `protocols/CampaignConfig_TEMPLATE.yaml` |
| `ssp_seats` | — | required map of SSP → `{seat, connector_label, mcp_prefix, account_id}` |
| `deals` (or `dsps[].deals`) | — | required map/list of campaign deals (`deal_id`, `internal_deal_id`, `ssp`, `seat`, `channel`, `deal_url`, optional `optimization_block_list_id`) |
| `files.opt_log`, `files.opt_targets` | — | derived from `OptLog_<Client>_<Code>.csv` and `OptTargets_<Client>_<Code>.csv` |
| `state.last_optimization_run` | `state.last_full_review` | `null` (updated to ISO date after each Optimization Day run) |
| `state.last_light_review` | — | `null` (updated to ISO date after each light check) |
| `state.carried_over` | — | `false` (set `true` when deferred by the 5-campaign cap) |
| `state.carried_over_run_type` | — | `null` (`"full"` \| `"light"` \| `"closeout"` — records the exact deferred run type so a carried-over light check stays a light check) |
| `state.wrap_completed` | — | `false` (set `true` after the end-of-flight closeout run) |

`optimization_days` and `light_days` must not overlap. A day in both runs a full Optimization Day run.

### Automatic Schema Migration & Fleet-Wide Consistency Rule
Because the protocol evolves over time, **every existing `CampaignConfig_*.yaml` file is automatically kept in sync with the latest protocol version** without manual cleanup:
1. **`schema_version` Stamp:** Every config carries `schema_version: "3.0"` (or `"2.8"`) at the top level.
2. **Self-Healing on Every Run & Chat Touch:** Whenever the weekday scheduled runner or a chat session loads any `CampaignConfig_*.yaml` whose `schema_version` is missing or older than the current protocol version (or that still contains legacy keys like `review_days`, `review_day`, `last_full_review`, `max_opt_groups_per_campaign_week`, or missing default blocks like `storage`, `state.wrap_completed`, `cycle.max_deal_volume_cut_share`, `team.cc`, `team.backup_trader`), the agent **automatically migrates the keys in memory AND writes the upgraded config back to its storage location in place**.
3. **One-Command Fleet Sync (`"sync all campaign configs"`):** Whenever a trader says `"sync all campaign configs"`, the agent scans all `CampaignConfig_*.yaml` files in workspace storage (`shared/Optimizations/` and Fast.io if configured), upgrades every config to the current schema version in place, and prints a confirmation table so zero campaigns ever lag behind.

### Trader distribution, staggered Optimization Days & PTO coverage
Every campaign routes strictly to the trader specified in its own `CampaignConfig` under `team.digest_to`.
When multiple campaigns for the same trader run on the same day, the scheduled job groups them **by `team.digest_to`** so each trader only receives their own campaigns. When a trader is out on PTO, saying **"cover `<Trader>`'s campaigns for `<Backup Trader>` until `<Date>`"** (or **"add `<Backup>` as backup on `<Client>` until `<Date>`"**) sets both `team.backup_trader` and `team.backup_until: "<YYYY-MM-DD>"` on their active configs so the backup trader receives and can act on those notifications directly while `runtime_today <= team.backup_until`. Whenever a run or chat session observes `runtime_today > team.backup_until`, it automatically clears `team.backup_trader: null` and `team.backup_until: null` in `CampaignConfig` and stops copying the expired backup.

---

## 5. Review Runs, Carryover Queue & Short Notification Email

Any trader can trigger a review on demand in chat (`"run optimization review for <Client>"` or `"run weekday optimization queue"`), and operators/traders can run the queue unattended every weekday at **15:00 UTC** (after daily DSP reports drop) via a recurring task (`Optimization Log — Weekday Campaign Runner`, scheduled with **Make recurring task…**).

### Queue selection & deterministic carryover (max 5 campaigns per scheduled run)
1. Discover all `CampaignConfig_*.yaml` files where `campaign.active: true` (checking `shared/Optimizations/`, reading `CampaignRegistry.yaml`, and paginating `mcp_fastio_helpers_find` with `limit=100, offset=...` until `has_more` is false when Fast.io is configured).
2. **End-of-Flight Closeout Check (`runtime_today > campaign.end_date`):**
   - If `campaign.active: true`, `runtime_today > campaign.end_date`, and `state.wrap_completed != true`, the campaign is queued for **one final End-of-Flight Closeout Run** (`run_type = "closeout"`, see below).
3. A live campaign (`start_date <= runtime_today <= end_date`) is **due for an Optimization Day Run (Full Review, `run_type = "full"`)** today if:
   - `state.carried_over == true` **and** `state.carried_over_run_type in ("full", null)`, **OR**
   - Today's weekday (`MON`–`FRI`) is in `team.optimization_days` (or legacy `team.review_days` / `team.review_day`) **and** `state.last_optimization_run != runtime_today`, **OR**
   - Its scheduled `optimization_days` day occurred earlier this calendar week (since Monday) and `state.last_optimization_run` is older than that scheduled day (automatic catch-up if a prior run hit the cap or failed).
4. A live campaign is **due for a Light Check (`run_type = "light"`)** today if it is not due for an Optimization Day Run, and either:
   - `state.carried_over == true` **and** `state.carried_over_run_type == "light"`, **OR**
   - Today's weekday is in `team.light_days` and `state.last_light_review != runtime_today`.
5. **Priority order (up to 5 campaigns per scheduled run):**
   1. Carryover Optimization Day runs (`state.carried_over == true` and `state.carried_over_run_type in ("full", null)`, or missed earlier this week), oldest `state.last_optimization_run` first.
   2. Today's scheduled Optimization Day runs, oldest `state.last_optimization_run` first.
   3. End-of-flight closeout runs (`runtime_today > end_date` and `wrap_completed != true`).
   4. Carryover or today's scheduled light checks (`run_type == "light"`).
6. Any due campaign beyond the 5-campaign cap has `state.carried_over: true` **and** `state.carried_over_run_type: "<full|light|closeout>"` written to its `CampaignConfig` so the next weekday executes the exact deferred run type (never promoting a deferred light check into a full Optimization Day run).
7. `DRY_RUN` in the task prompt (or `"dry run"` in chat) writes nothing and sends nothing.

### Full review steps (1–6)
1. **Load.** Config, log, targets. Reports per each DSP's `source` in `dsps[]`:
   - `mailbox` → call `mcp_explorer_find_latest_report` to locate the latest report message(s), and then **download the report payload files** using `mcp_explorer_get_download_link(s3_key=..., filename=...)` + `download_url(url=<its download_url>)` (for attached reports) or `mcp_explorer_get_download_link(s3_key=..., url=...)` + `download_url(url=<its download_url>)` (for link-based reports) before parsing.
   - `fastio` → `mcp_fastio_helpers_download_partitions` (with explicit inclusive date bounds `since = runtime_today - max(low_spend_lookback_days, evaluation_days)` and `until = runtime_today` plus per-report filename filters).
   - `s3` → `mcp_aws_s3_helpers_download_partitions` with the same explicit inclusive `since` / `until` bounds.
   Always verify the downloaded files exist on disk before continuing. Strip footer rows, dedupe overlapping dates (newest drop wins), record the data window. Confirm every live deal in the config appears in the report; if one is missing, say so and don't treat it as zero delivery.
2. **Go-live.** For each `Proposed` row, check its deal in delivery data using the full value list from `OptTargets`:

   | Lever | Live when (2+ consecutive days) |
   |---|---|
   | Block List | Listed entries fall ≥ 90% below their prior-7-day daily average on that deal |
   | Allow List / inclusion targeting (e.g. OS include) | ≥ 90–95% of the deal's impressions fall inside the allowed set |
   | Daypart / geo / device exclusion | The excluded segment's share falls ≥ 90% |
   | Client request (bid, budget, pause) | Not auto-set; flag `"possible go-live"` and ask the trader |

   When `cycle.auto_detect_live` is `true` (or omitted), set Status `Live`, `Live` = first qualifying day, `Notes` `"auto-detected; confirm"`. When `cycle.auto_detect_live == false`, keep `Status = Proposed`, leave `Live` blank, and write `"possible go-live (<first qualifying day>); awaiting trader confirmation (auto_detect_live=false)"` in `Notes`. If `OptTargets` has no rows for the Opt ID, follow the partial-check rule under Section 1. A trader's date always wins. If a trader says live but data shows no change after 3 days, flag it.
3. **Results.** At ≥ 3 delivery days, write an early read in `Result`. At ≥ 7, compare pre vs post over equal windows (ratio of sums, using the deal's channel KPI/benchmark if configured, against unchanged deals where possible), write `before → after` and the value sentence, and set `Kept`, `Reverted` or `Dropped (inconclusive)`. Changes on the same deal that went live together are judged together.
4. **What's next (with SSP Capability Classification, Scale Protection & Supply Expansion).**
   Find candidates across every report, per deal, using only config levers and respecting `ssp_constraints`. Check each candidate against `skills/ssp-deal-capabilities/SKILL.md` and tag its execution path in `Notes`:
   - **`[Chat API]`**: Executable directly from the chat Workbench via `deal-booking` (`compile_update_plan`).
   - **`[Console/DSP]`**: Supported on the SSP console (`UI`) or buyer DSP only, not in the chat update plan.
   - **`[Client Request]`**: DSP-side bid or budget shift requiring client approval.
   - **Scale / Pacing Protection (`cycle.max_deal_volume_cut_share`, default `0.40` when absent):** Compare the proposed exclusion's share of the deal's impressions or spend against the campaign's configured `cycle.max_deal_volume_cut_share` (defaulting to `0.40` only when the key is absent). Whenever the cut exceeds `cycle.max_deal_volume_cut_share`, prepend **`[SCALE WARNING: removes XX% of deal volume]`** in `Notes`.
   - **Balance Exclusions with Supply-Expansion / Positive Levers:** Do not only propose subtractive exclusions (Block Lists, OS/Geo/Daypart cuts). On every Optimization Day run, check for **positive / expansion opportunities**:
     1. **Under-pacing high-performers:** If a deal beats the KPI benchmark by ≥ 20% but is under-delivering or had more than `cycle.max_deal_volume_cut_share` (default 40%) of its volume cut by prior Block Lists, recommend loosening restrictive targeting, lowering the floor, or expanding supply.
     2. **Curated Allow-List / High-Priority Deal Clone:** If 5–25 top domains/apps drive ≥ 2x the campaign KPI benchmark with strong scale, recommend a positive curation play (e.g., a dedicated high-priority Allow-List deal or DSP bid up-weighting).
     3. **Within-DSP / Cross-Deal Budget Reallocation (`[Client Request]`):** Shift spend toward deals or line items beating the benchmark by ≥ 30%.
   - Cap at `max_new_opt_groups_per_run` (default 5). Zero is valid. Qualifying extras go to `Backlog`.
5. **Write (with Read-Before-Write Merge & Opt ID Collision Guardrail):**
   - Immediately before saving `OptLog_<Client>_<Code>.csv` or `OptTargets_<Client>_<Code>.csv` (both in scheduled runs and in chat Workbench turns), re-fetch the latest file from storage and **merge by `Opt ID`**:
     - Preserve any status overrides, `Live` dates, or new rows added concurrently by another process.
     - **Opt ID Collision Resolution:** Allocate (or re-verify) new `Opt ID` numbers **after** re-fetching the latest `OptLog`. If a newly proposed `Opt ID` already exists in the re-fetched file with different content (e.g., a chat session and scheduled run both read the same prior max number), re-number the new group to `max(existing_opt_id_number_in_refetched_log) + 1` and update its corresponding `OptTargets` rows' `Opt ID` before merging and saving.
   - Save the merged `OptLog` and `OptTargets` in place (deferring the `CampaignConfig` `state.last_optimization_run` / `state.carried_over` update until Step 6 confirms notification email delivery so a failed send remains retryable).
6. **Short Notification Email ("Opts Ready in Chat") & State Completion:**
   - Because traders vet, question, and execute optimizations inside Fleet Chat via the interactive **Optimization Workbench card**, the scheduled email is a **compact 15-second notification** (never a 1,500-word report):
     - **Subject:** `Opts ready in Chat — <Client> (<CampaignCode>) — <N> recommendations` (or `Opts ready in Chat — <date> — <N> campaigns` when grouped for a trader).
     - **Block 1 (`headline_metrics`):** Up to 4 KPI & outcome snapshot cards (primary KPI vs. goal, spend, and any 7-day `Kept` / `Reverted` headline win).
     - **Block 2 (`callout`, `kind="opportunity"`):** **"1-Line Handoff to Fleet Chat"** box at the very top:
       > `Open Fleet Chat and type: review <Client> opts`
       > `Your interactive Optimization Workbench card will open so you can inspect the data, ask questions about any domain/deal, edit the target lists, or click Stage SSP Updates.`
     - **Block 3 (`table`):** One compact summary table per campaign (`Opt ID & Deal`, `Proposed Change & Execution Path [Chat API / Console / Client]`, `7d Spend & Projected Lift`).
     - **No wall-of-text prose, no formula lectures, no empty sections, and no storage plumbing footers.**
   - **Only after `mcp_ses_outbound_send_email` succeeds (`ok: true`)**, update `CampaignConfig` (`state.last_optimization_run: <runtime_today>`, `state.carried_over: false`, `state.carried_over_run_type: null`) and save it in place (if email delivery fails, keep the prior `last_optimization_run` and `carried_over` state so a retry sends the notification).

### Light review (optional mid-week check — steps 1–3 and 5L only)
- **Purpose:** A silent background check between full weekly reviews. It does **not** propose new optimizations (`What's next` is skipped) and sends **no email** on a quiet day.
- **Step 5L (Light Check Write):** Re-fetch and merge `OptLog` and `OptTargets` using the Read-Before-Write Merge Guardrail above, and write `state.last_light_review: <runtime_today>` (plus `state.carried_over: false`, `state.carried_over_run_type: null` if this was a carried-over light check) in `CampaignConfig` — leaving `state.last_optimization_run` **unchanged**.
- **When it emails:** Only if a change went live mid-week, a 7-day result finished, or a delivery guardrail tripped.

### End-of-Flight Closeout Run (`runtime_today > campaign.end_date`)
When an active campaign passes its `end_date` and `state.wrap_completed != true`:
1. Score any remaining `Live` rows (rows with ≥ 3 delivery days get a final pre-vs-post readout; any open `Proposed` or `Backlog` rows that never went live are marked `Dropped` with `"campaign flight ended"` in `Notes`) and save the updated `OptLog` in place.
2. Email the trader a **Campaign Optimization Wrap Summary** (`Optimization wrap — <Client> (<CampaignCode>)`) via `mcp_ses_outbound_send_email` containing all `Kept` optimizations with their before → after KPI lift and value sentences.
3. **Only after `mcp_ses_outbound_send_email` succeeds (`ok: true`)**, set `campaign.active: false` and `state.wrap_completed: true` in `CampaignConfig_<Client>_<Code>.yaml` and save the updated `CampaignConfig` in place (if email delivery fails, leave `campaign.active: true` and `state.wrap_completed: false` so the next run retries the wrap email).

---

## 6. The Chat-First Optimization Workbench (`show_ui`) & Execution Flow

Whenever a trader opens chat and says:
- **"review `<Client>` opts"** (or **"open `<Client>` workbench"**, **"show open opts for `<Client>`"**, **"review opts for `<Client>`"**) → Load `CampaignConfig`, `OptLog`, and `OptTargets` for that campaign and render its interactive **Optimization Workbench card** via `show_ui`.
- **"review my opts"** (or **"open my workbench"**, **"review `<Trader>`'s opts"**) → Multi-Campaign Workbench mode: Find **all active `CampaignConfig_*.yaml` files owned by that trader** (`team.trader` / `team.digest_to`), load their open `Proposed` and `Backlog` rows, and render **one unified Multi-Campaign Optimization Workbench card** so the trader can vet and stage their entire day's queue across all their accounts in a single card.
  - **Campaign-Qualified Keys in Multi-Campaign Mode:** Because two campaigns can independently use overlapping `Opt ID` prefixes or numbers, every selectable-table `row_key` value, `manual_confirmations` / `promote_backlog` option value, and `<opt_id>_domains` input ID in multi-campaign mode MUST be prefixed with the campaign code (`<campaign_code>:<opt_id>`, e.g., `CMP00166:CH-001` and `CMP00166_CH-001_domains`), while displaying `Campaign` and `Opt ID` (`CH-001`) in the visible table columns.

### Workbench Card Specification (`show_ui`)
1. **Top KPI & Result Strip (`columns` of `stat` cards):**
   - Primary KPI vs. benchmark/prior week for each DSP, plus any newly completed 7-day `Kept` / `Reverted` outcome.
2. **Three Structured `tabs`:**
   - **Tab 1 — `1. Chat-Executable SSP Opts (<N>)`:**
     - A multi-select `table` (`id="selected_opts"`, `select="multi"`, `row_key="opt_id"`) listing every open `Proposed` row tagged `[Chat API]` (including any stale carryovers from prior weeks).
     - **Pre-selection rule:** Clean `[Chat API]` rows are **pre-selected by default** (`value=[...]`). Rows carrying `[SCALE WARNING]` (cutting more than `cycle.max_deal_volume_cut_share`, default `0.40` when absent) or `[STALE]` are **unchecked by default** so the trader consciously opts in before cutting beyond their configured volume threshold.
     - **Inline Target Editor (`multi_select`):** For any multi-target optimization (e.g., a 5-domain or 9-domain Block List), include an editable `multi_select` (`id="<opt_id>_domains"`, or `<campaign_code>_<opt_id>_domains` in multi-campaign mode, with per-domain spend/clicks/KPI in `description`) pre-populated from `OptTargets_<Client>_<Code>.csv` so the trader can uncheck individual domains or add a custom domain right inside the card.
   - **Tab 2 — `2. Console & Client Requests (<N>)`:**
     - A display `table` of open `Proposed` rows tagged `[Console/DSP]` or `[Client Request]` (showing the exact console setting or client budget-shift recommendation), paired with a status selector (`id="<opt_id>_status_action"` or `manual_confirmations`) so the trader can tick `<Opt ID> is Live` (`Status = Live`, `Live = <today>`) or `<Opt ID> Sent to Client` (`Status = Proposed`, logging `"<today>: Sent to client (<trader>)"` in `Notes`) in one click.
   - **Tab 3 — `3. Backlog & Signals (<N>)`:**
     - A multi-select `table` (`id="promote_backlog"`, `select="multi"`, `row_key="opt_id"`) of the top `Backlog` candidates so the trader can check any row they want to promote early.
3. **Inline Validation / Question Box (`text_input`, `id="trader_question"`, `multiline=true`):**
   - Allows the trader to type any validation question (e.g., *"How did weather.com perform over 20 days?"* or *"What is left on OpenX Prospecting Display if we block dailymotion.com?"*) before committing to an SSP write.
4. **Two Footer Actions:**
   - **Primary Submit (`id="execute_selected"`, `style="primary"`):** `Stage SSP Updates for Selected ({{ len(selected_opts) }})`
   - **Secondary Submit (`id="ask_questions"`, `style="secondary"`, `validate=false`):** `Ask / Validate First`

### How Workbench Submissions Are Handled
- **Step 0 — Apply Status Confirmations, Backlog Promotions & Inline Target Edits on EVERY Submission (`ask_questions` AND `execute_selected`):**
  - Before running either action's specific logic, always process any submitted changes using the canonical 10-column `OptLog` lifecycle fields (`Backlog`, `Proposed`, `Live`, `Kept`, `Reverted`, `Dropped`):
    1. **`manual_confirmations` / `<opt_id>_status_action` (with strict `:live` > `:sent` precedence per `Opt ID`):**
       - If both `<Opt ID>:live` and `<Opt ID>:sent` are submitted for the same `Opt ID`, `:live` always takes precedence (`Status = Live`, `Live = <today>`, logging both `"<today>: Sent to client & confirmed live (<trader>)"` in `Notes` without reverting `Status` to `Proposed`).
       - For `<Opt ID>:live` (or `<campaign_code>:<Opt ID>:live`): set `Status = Live`, `Live = <today>`, and append `"<today>: Confirmed live (<trader>)"` to `Notes`.
       - For `<Opt ID>:sent` (or `<campaign_code>:<Opt ID>:sent`) when `:live` is not also checked for that `Opt ID`: keep `Status = Proposed` (so subsequent go-live detection still monitors delivery data) and append `"<today>: Sent to client (<trader>)"` to `Notes`.
    2. **`promote_backlog`:** Promote each checked `Backlog` row to `Status = Proposed`, `Proposed = <today>` in `OptLog`.
    3. **Inline target edits (`<opt_id>_domains` / `<campaign_code>_<opt_id>_domains`):** Update `OptTargets_<Client>_<Code>.csv` (and the row's target count in `OptLog`) if the trader unchecked or added target values.
    4. Persist those `OptLog` / `OptTargets` updates in place using the Read-Before-Write Merge Guardrail.
- **When the trader clicks `Ask / Validate First` (`action="ask_questions"`):**
  1. After applying Step 0 above, load the campaign's DSP reports in `run_python`, answer `trader_question` concisely with exact numbers, and **re-render the Workbench card** (`replaces=<old_card_id>`) preserving the trader's current selections so they can click **Stage SSP Updates** whenever they are satisfied.
- **When the trader clicks `Stage SSP Updates for Selected` (`action="execute_selected"`):**
  1. **Apply Step 0 above** (`manual_confirmations`, `promote_backlog`, and `<opt_id>_domains` inline target edits) first so no status or backlog selection submitted in the same card is ever dropped.
  2. **Verify Required Connectors First:**
     - Check `ssp_seats` and `deals` in `CampaignConfig` to identify each target deal's SSP and seat (`mcp_prefix`), plus `Deal Planner` (`mcp_deal_plan_*`).
     - **Never query or mutate the wrong seat for a deal.** If `Deal Planner` or the required SSP seat connector is not enabled in the chat's Tools picker, immediately name the exact `connector_label` and ask the trader to switch it on in the Tools picker.
  3. **Pre-Flight Live Deal Read — Additive Block-List Union vs. Allow-List Removal (Never Clobber or Cross-Contaminate Lists):**
     - Read each target deal live on the SSP first (and check `OptLog` + `OptTargets` for that exact `Deal` + `Lever`).
     - Verify whether the live deal uses a **Block List** or an **Allow List**:
       - **Block List (`add block` / `include block`):** Take the **additive union** of all block values from `OptTargets` rows with `Status` in `Kept` + `Live` + the newly selected `Proposed` row (excluding any `Reverted` or `Dropped` rows) **AND** union with any pre-existing baseline block list currently live on the SSP deal so a Week 3 Block List addition never overwrites a Week 1 `Kept` Block List.
       - **Allow List (`remove allow`):** Pass **ONLY** the selected removal target values (plus any prior `Kept`/`Live` removal targets in `OptTargets` for that deal) to `remove allow` — **NEVER** union removal targets with the live allow list (for example, removing 5 underperforming domains from a 2,684-domain allow list must pass only the 5 removal domains to `remove allow`, leaving the remaining 2,679 allowed domains intact).
     - **Magnite DV+ / Shared-List Isolation Rule (Create-Then-Swap):** On SSPs where domain lists are account-level shared objects (`list refs` on Magnite DV+), **never** edit a shared list used by other campaigns and **never** attach another campaign's block list. Always keep any baseline account blocklist intact and attach/update a **campaign-dedicated list** named `<Client>_<CampaignCode>_Optimization_Block_List`:
       - When `optimization_block_list_id` is `null` (no campaign-dedicated list exists yet on Magnite), follow the two-step Magnite targeting-list flow: first stage `magnite_create_targeting_list` (`name="<Client>_<CampaignCode>_Optimization_Block_List"`, initial block domains) and wait for trader approval; once created, persist the returned list ID to `deals["<Deal Name>"].optimization_block_list_id` in `CampaignConfig`, and only then compile and stage the deal update plan that attaches the new `optimization_block_list_id` (and detaches any foreign list in that update — never detaching protection before the replacement list ID exists).
  4. **Partition by Client Profile & Stage SSP Approval Card(s) via Deal Planner:**
     - In multi-campaign mode (`"review my opts"`), partition selected SSP changes by `client_id` first, because `compile_update_plan(client_id, changes)` requires a single `client_id` per plan.
     - For each client in the selection:
       1. Load the client profile with `get_deal_profile(client_id)`.
       2. Call `compile_update_plan(client_id, changes)` → `*_preview_plan` → `finalize_plan(plan_id, rows=<all compiled item row numbers 1..N corresponding to the trader's selected Workbench rows>)` (explicitly passing `rows` so `changes_active_deal` flagged items are not put in `deselected`) → stage one `*_execute_plan` approval card per `(client, SSP, seat)` (as documented in `skills/deal-booking/SKILL.md`, the Optimization Workbench card is a supported recommendation-review exception alongside `recommendation_card`).
  5. **Automatic Post-Approval Verification & Receipt (Mandatory on the Turn After Approval — Trader Never Has to Ask):**
     - As each `*_execute_plan` card is approved, **automatically** run a post-execution verification pass for that SSP's deals in that same turn without waiting for the trader to ask:
       1. **Check `plan_status` & SSP Readback:** Confirm the SSP connector reported `confirmed_after_update` (or call the SSP's read tool `*_get_deal` / `*_find_deals` if any item is `unclear`).
       2. **Audit Every Target Value Against `OptTargets`:** Verify that:
          - Every domain/app/value from `OptTargets` for the executed `Opt ID` is now present on the deal's Block List (or removed from its Allow List),
          - The prior `Kept` / `Live` entries count did not drop (no accidental overwrite),
          - Only this campaign's targeting lists are attached (flag immediately if a foreign list name appears), and
          - The deal's status remains `Active` (not paused by the update).
       3. **Render the Post-Execution Verification Receipt in Chat:** Output a compact table with one row per deal:
          - **`Opt ID` & Deal Name**
          - **SSP Deal ID + Direct Console Link** (`[Open in Index Exchange ↗](<deal_url>)`, `[Open in Magnite ↗](<deal_url>)`, etc.)
          - **`Before → After` Summary** (e.g. `Block List: 22 → 27 domains (+5 added)` or `Allow List: 2,684 → 2,679 domains (-5 removed)`)
          - **Verification Status:** `VERIFIED` (all `OptTargets` confirmed live on SSP) or `FLAGGED` (with exact discrepancy and immediate remediation)
       4. **Close the Loop in `OptLog`:** For `VERIFIED` rows, set `Status = Live`, `Live = <today>`, append `"<today>: Executed & verified live on SSP (<before -> after>) (<trader>)"` to `Notes`, and save `OptLog` and `OptTargets` in place.

---

## 7. Client-Facing Optimization Story Emails (`show_ui`) & Spend Elasticity Pitch

While scheduled review notifications are strictly internal (routed to `team.digest_to`), traders frequently ask in chat for a **client-facing showcase email** (to send to a curation partner, an agency buyer, or an end-client, or to their own inbox first to forward) that tells a compelling story around the optimizations we just executed or verified — or evaluates whether the campaign can absorb **incremental budget**.

### 7.1 Non-Negotiable Client-Facing Rules (Zero Jargon, Single-KPI Focus)
Every client-facing optimization email MUST follow these rules automatically — do **not** ask the trader whether to apply them:
1. **Strip All Internal IDs & Ad-Ops Plumbing:**
   - **NEVER include** internal campaign codes (`CMP00166`) in the subject line, header subtitle, or executive summary.
   - **NEVER include** internal Opt IDs (`CH-001`, `NWO-010`), raw SSP Deal IDs (`IX176...`, `MGNI-MD-...`), targeting list IDs (`70417`), or API mechanics (`NONE_OF`, `ANY_OF`, `"alongside our 4,355 baseline exclusions"`).
   - Refer to deals by their **human-readable program/trigger name + SSP/channel** (e.g., **Winter Weather OLV (Index Exchange)**).
2. **Laser-Focus on a Single Coherent Primary KPI per Section:**
   - Anchor every headline card, chart, and table on the selected DSP's `kpi.primary.name` (e.g., `ROAS`, `CTR`, or `CPA`) and its benchmark. When a campaign has multiple `dsps[]` entries or channel-level KPI overrides (`channel_kpis`, such as `CTR` on Display vs. `VCR` on OLV), the Story Builder card (`primary_kpi_scope`) requires selecting either one specific DSP/channel KPI for a single-metric story OR rendering separate per-DSP / per-KPI chart and table sections — never average or combine incomparable KPIs on a single chart axis or table column.
   - Do **not** clutter the story with secondary metrics (e.g., do not mention `CPA` when the selected KPI is `ROAS`) or internal formula/derivation footnotes (`*(Formula note: ...)*`).
3. **Punchy, Scannable Structure Over Long Prose:**
   - Keep the Executive Summary to **2 short, high-impact sentences**: (1) current 7-day KPI performance vs. benchmark, and (2) what optimizations were deployed today and their projected (or realized) KPI lift while preserving scale.
   - Show the optimizations in a **scannable table** rather than multi-sentence narrative bullets.
4. **Single-Panel, High-Legibility Chart (`1088px` wide):**
   - Never use side-by-side multi-subplot charts in an email — x-axis labels collide and in-plot legends obscure the bars.
   - Always generate a **single-panel horizontal bar chart** (`figsize=(10.88, 4.2)`, `dpi=100`, palette `#C5C5DC` for Actual vs. `#7272AB` for Post-Curation, dashed `#1A0B1E` benchmark line) with direct data labels at the end of each bar (`47.53x (+12.9%)`) and the legend placed **above** the plot area (`bbox_to_anchor=(0.5, 1.16)`, `ncol=3`, `frameon=False`).

### 7.2 Interactive Menu of Story Archetypes & Modular Sections (`show_ui`)
When a trader asks to craft a client-facing optimization email (e.g. `"craft a client email for <Client>"`, `"client story email for <Client> opts"`, unless they say *"just draft it with the standard layout"*), present a compact **`show_ui` Story Builder card** offering a menu of **Story Archetypes** and **Modular Building Blocks** pre-filled with smart defaults:

#### A. Pick the Story Archetype (`choice` — 1-Click Preset)
| Archetype | When to Use | Default Focus |
|---|---|---|
| **1. `just_executed` — "Just Executed" Proactive Curation Showcase** *(Default)* | Right after executing `Proposed` optimizations | Highlights current 7d momentum + exact changes made today + projected KPI lift + 28d proof that removed supply was non-performing |
| **2. `proven_lift` — "Proven Lift" Before-vs-After Readout** | After `Live` optimizations hit 7+ days (`Kept`) | Highlights verified pre-vs-post KPI improvement on curated deals and total media dollars saved/reallocated |
| **3. `expansion_pitch` — Strategic Allocation & Incremental Budget Pitch** | When top-performing deals beat benchmark and can absorb more spend | Pairs recent supply hygiene with the **4-Test Spend Elasticity Analysis** (Section 7.3) and a concrete incremental budget recommendation |

#### B. Pick the Modular Email Sections (`multi_select` Checkboxes — Trader Mixes & Matches)
Pre-check the recommended defaults (**Blocks 1, 2, 3, 4, 5, 6**) so the trader can either click **Generate Email** immediately or toggle sections on/off:
- [x] **Block 1 — Punchy 2-Sentence Executive Summary:** Current 7d KPI vs. benchmark + headline takeaway of the optimizations (no internal campaign code, no internal IDs).
- [x] **Block 2 — Headline KPI Snapshot Cards (`headline_metrics`):** 3–5 cards focused strictly on the primary KPI (7d Actual KPI, Post-Curation/Verified KPI + % Lift, 7d Attributed Outcome/Spend, 28d Context, and Total Spend Reallocated).
- [x] **Block 3 — Single-Panel Deal KPI Lift Chart (`chart`):** Clean horizontal bar chart comparing Actual vs. Post-Curation KPI by deal against the campaign benchmark.
- [x] **Block 4 — Deal Performance & Lift Table (`table`):** Per-deal breakdown (`Deal (SSP · Channel)`, `7d Spend`, `7d Outcome/Revenue`, `Actual 7d KPI`, `Post-Curation KPI (+Lift%)`).
- [x] **Block 5 — "Optimizations Implemented" Detail Table (`table`):** 4-column table (`Deal Updated`, `Exact Domains / Segments Removed or Added`, `Spend Trimmed (7d / 28d)`, `KPI Lift`).
  - *Granularity selector (`choice` on card):* **Full List of All Removed Domains/Values** *(Default — shows every domain/segment clearly, plus any notable high-performer intentionally spared)* vs. **Top Representative Examples** vs. **Category Summary Only**.
- [x] **Block 6 — Phase 2 Curation Roadmap Callout (`callout`):** Forward-looking note previewing the next wave of queued `Backlog` refinements (e.g., deal-scoped Geo or Browser precision) once the current wave's 7-day evaluation completes.
- [ ] **Block 7 — Spend Elasticity & Incremental Budget Pitch (`callout` + `table`, auto-checked for Archetype 3):** Quantifies how much incremental budget the campaign can safely absorb without dropping below the KPI benchmark (using the 4-Test Spend Elasticity Evaluator below).
- [ ] **Attach Updated Deal Sheet (`.xlsx`, optional toggle):** Attaches the branded deal sheet if the recipient needs deal IDs/hyperlinks.

#### C. Delivery & Framing Inputs on the Card
- **Recipients (`to_email`):** Defaults to the trader's own email for review/forwarding, or a comma-separated list of recipients.
- **Greeting (`greeting`):** Default `"Hi Team,"` (or partner names like `"Hi Brian & Jamie,"`).
- **Audience Framing (`choice`):** `Curation Partner Showcase` vs. `Agency / End-Client Buyer`.

Once the trader submits the card (or asks for a revision):
1. If `attach_deal_sheet == true`, locate the campaign's current `.xlsx` deal sheet in the workspace, recording its local path `deal_sheet_xlsx_path`.
2. Render the email via `mcp_ses_outbound_render_email` (following `protocols/email-style.yaml`) and stage both `preview_email` and `mcp_ses_outbound_send_email` (passing `attachments=[deal_sheet_xlsx_path]` when `attach_deal_sheet == true`) in the same turn.

### 7.3 The 4-Test Spend Elasticity & Incremental Budget Evaluator
Whenever a trader asks *"Can this campaign absorb more budget without hurting `<KPI>`?"* or selects **Archetype 3 (`expansion_pitch`)**, run these 4 empirical tests in `run_python` across the full 28-day report window before recommending a budget increase:
1. **Test 1 — Daily Spend vs. Daily KPI Elasticity (Quartiles & Correlation):**
   - Group daily performance across the 28-day window into **Low-Spend Days**, **Mid-Spend Days**, and **Peak-Spend Days** (and compute the Pearson/Spearman correlation between daily spend and daily KPI).
   - Check whether higher daily spend degrades the primary KPI (diminishing returns) or whether Peak-Spend Days hold or beat the KPI benchmark.
2. **Test 2 — Post-Curation Supply Headroom:**
   - Calculate the exact impression and spend share retained after applying the active Block Lists (e.g., `88.7%` of impressions retained, while trimming `11.3%` zero-return waste).
3. **Test 3 — High-Efficiency Channel / Deal Mix Headroom:**
   - Identify deals or channels massively outperforming the benchmark that currently receive a small share of impressions (e.g., Display triggers delivering `85x–95x` ROAS on only `23%` of impressions vs. OLV at `77%`).
4. **Test 4 — Recommended Safe Incremental Scaling Band & Direction-Aware KPI Guardrail:**
   - Translate Tests 1–3 into a concrete **Safe Daily & Weekly Budget Scaling Band** (e.g., `+25% to +50%` incremental budget, `$X/day → $Y–$Z/day`, with a specific deal/channel split) paired with a direction-aware **KPI Guardrail Bound** derived from `kpi.primary.direction`:
     - When `kpi.primary.direction == "higher_is_better"` (`ROAS`, `CTR`, `VCR`, `Viewability`): set a **KPI Guardrail Floor** (`max(benchmark, trailing_7d_actual * 0.90)`) and hold scaling if rolling KPI drops **below** the floor (e.g., *"Hold daily expansion as long as 7-day rolling ROAS remains ≥ 35.0x vs. the 33.0x benchmark"*).
     - When `kpi.primary.direction == "lower_is_better"` (`CPA`, `CPC`, `CPM`, `CPCV`): set a **KPI Guardrail Ceiling** (`min(benchmark, trailing_7d_actual * 1.10)`) and hold scaling if rolling KPI rises **above** the ceiling (e.g., *"Hold daily expansion as long as 7-day rolling CPA remains ≤ $18.00 vs. the $20.00 target"*).

---

## 8. Quick Text Commands Reference (Supported Anytime in Chat)

| Trader says | Agent does |
|---|---|
| `"onboard <Client> <CampaignCode>"` (or `"set up a campaign config for <Client>"`) | Runs Auto-Discovery & Day-1 Preflight QA, then opens the interactive **`show_ui` Campaign Config Builder Card** (Section 2) |
| `"review <Client> opts"` (or `"open <Client> workbench"`) | Renders the interactive **Optimization Workbench card** (`show_ui`, Section 6) for that campaign |
| `"review my opts"` (or `"open my workbench"`) | Renders a **unified Multi-Campaign Optimization Workbench card** across all active campaigns owned by that trader |
| `"execute NWO-018.1, NWO-019.1"` (or `"execute all clean"`) | Unions targets with prior `Kept`/`Live` rows and stages the `deal-booking` SSP update plan directly |
| `"craft a client email for <Client>"` (or `"client story for <Client> opts"`) | Opens the interactive **`show_ui` Client-Facing Optimization Story Builder** (Section 7) |
| `"can <Client> take incremental budget?"` (or `"spend elasticity for <Client>"`) | Runs the **4-Test Spend Elasticity & Incremental Budget Evaluator** (Section 7.3) |
| `"NWO-006 is live"` (optionally `"since 10/9"`) | Status `Live`, Live date, note who said it |
| `"revert NWO-008"` / `"keep NWO-005"` | Set the decision early, reason in `Notes` (and if reverting an SSP change, stage the removal via `deal-booking` keeping earlier `Kept` lists intact) |
| `"drop NWO-008, <reason>"` | Status `Dropped`, reason in `Notes` |
| `"log this"` / a concrete, data-backed idea in exploration | Add as `Backlog`, `Notes` `"Source: Chat <date> (<trader>)"`; confirm in one line |
| `"propose NWO-010"` | `Backlog` → `Proposed` (and write `OptTargets` rows if multi-value) |
| `"sync all campaign configs"` | Upgrades all `CampaignConfig_*.yaml` files in workspace storage to the latest `schema_version` in place |
| `"update the optimization protocol: <change>"` | Updates `shared/Protocols/optimization-log-protocol.md` (or `/Protocols/optimization-log-protocol.md` in Fast.io when configured) in place, bumps the version/changelog, and optionally stages a workspace skill override |
| `"cover <Trader>'s campaigns for <Backup> until <Date>"` (or `"add <Backup> as backup on <Client> until <Date>"`) | Sets `team.backup_trader` and `team.backup_until: "<YYYY-MM-DD>"` on `<Trader>`'s active configs so notifications also route to `<Backup>` until `<Date>` (auto-cleared once `today > team.backup_until`) |

---

## 9. Workflow Health (First Full Review of Each Month) & Guardrails

### Monthly Workflow Health (First Full Review of Each Month)
On the first full review of each month, include a compact 4-bullet Workflow Health note (omit if all metrics are `N/A` on a newly onboarded trader's first week):
1. Share of `Live` changes that reached a decision (target ≥ 80%)
2. Median days from `Proposed` to `Live`
3. Rows dropped for process reasons (setup or KPI changes) rather than data
4. Trader corrections to runs in chat

### Non-Negotiable Guardrails
- Levers, paths and platform limits come only from the config and `skills/ssp-deal-capabilities/SKILL.md`.
- Every number cites its report and window. Never report ROAS/CPA for a DSP with `no_conversion_tracking: true`.
- Use `"Block List"` / `"Allow List"`, never the older terms.
- Scheduled review notifications are strictly internal: automated runs send strictly to the config's `team.digest_to` (plus `team.backup_trader` while `not team.backup_until or runtime_today <= team.backup_until`, and `team.cc` if set; if `runtime_today > team.backup_until`, clear `team.backup_trader` and `team.backup_until` first) and never email a client directly. Client-facing optimization showcase emails are crafted on-demand in chat (Section 7) and sent only to the recipients the trader approves on the send card.

---

## Changelog

- **v3.0 (2026-10-22):** promoted to the workspace bundle (`protocols/optimization-log-protocol.md` + `skills/campaign-optimization-log/SKILL.md` + Prompt Library form starters); added the **Interactive `show_ui` Campaign Config Onboarding Card** with **Day-1 DSP Report Preflight QA** and flexible `storage` target selection (`shared/Optimizations/`, `fastio` `/Optimizations/`, client subfolder, root, or local workspace); unified the v2.8 **Chat-First Optimization Workbench (`show_ui`)** (`"review <Client> opts"` / `"review my opts"`), **Magnite DV+ Shared-List Isolation Rule**, **Supply-Expansion Levers**, **Read-Before-Write Merge Guardrail**, and **Automatic Post-Approval Verification Receipt** with **Section 7: Client-Facing Optimization Story Emails (`show_ui`)** and the **4-Test Spend Elasticity & Incremental Budget Evaluator**.
- **v2.8 (2026-10-22):** standardized `ssp_seats` and `deals` mappings; enforced bare SSP-executable values in `OptTargets.Value`; added Magnite DV+ Shared-List Isolation Rule, `"review my opts"` Multi-Campaign Workbench mode, Supply-Expansion levers, Read-Before-Write Merge Guardrail, and Section 7 Client-Facing Optimization Story Emails.
- **v2.7 (2026-10-21):** renamed `review_days` → `optimization_days` and `state.last_full_review` → `state.last_optimization_run`; added `schema_version` and Automatic Schema Migration; added mandatory Pre-Flight Live Deal Read and Automatic Post-Approval Verification Receipt.
- **v2.6 (2026-10-21):** introduced the Chat-First Optimization Workbench (`show_ui`) and compact "Opts Ready in Chat" notification email.
- **v2.5 (2026-10-16):** captured KPI `benchmark` upfront at onboarding; added `channel_benchmarks` and `channel_kpis`; added Pacing/Scale Protection (`max_deal_volume_cut_share: 0.40`), Additive SSP Execution, `team.cc` / `team.backup_trader`, and End-of-Flight Closeout Run.

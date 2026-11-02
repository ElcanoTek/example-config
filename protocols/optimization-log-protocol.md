# Optimization Log Protocol (v3.0)

A streamlined, tool-agnostic protocol for the campaign optimization lifecycle:
1. **Guided Onboarding & Day-1 Preflight QA (`show_ui`)**
2. **Persistent Optimization Log (`CampaignConfig.yaml`, `OptLog.csv`, `OptTargets.csv`)** in the user's chosen storage location
3. **Scheduled or On-Demand Optimization Reviews** across any SSPs, DSPs, and report sources
4. **Chat-First Optimization Workbench (`show_ui`)** for reviewing, questioning, editing target lists, and applying/exporting updates
5. **Client-Facing Optimization Story & 4-Test Spend Elasticity Builder (`show_ui`)**

Each review answers four questions in order: **What did we recommend? Is it live? Did it work? What's next?**

---

## 1. Files, Discovery & Storage Flexibility

Every campaign uses **3 co-located files** stored wherever the team chooses (`storage` block in `CampaignConfig`):
- **Persistent Conversation Workspace (`storage.type: workspace`, default):** Saved under `Optimizations/<Client> (<Code>)/` and indexed in `Optimizations/CampaignRegistry.yaml`.
- **Connected Cloud / Shared Storage (`storage.type: connected_storage`):** Offered and used when a writable cloud/shared storage connector is registered in `manifest.yaml` (`storage.backend` + `storage.folder_path`).
- **Custom Path (`storage.type: custom`):** Saved at the local workspace path in `storage.folder_path`.

| File | Purpose |
|---|---|
| `CampaignConfig_<Client>_<Code>.yaml` | Campaign settings: chosen `storage` location, trader owner, SSPs & DSPs, report sources, KPI & channel benchmarks, eligibility thresholds (`cycle`), allowed levers, and run state |
| `OptLog_<Client>_<Code>.csv` | 10-column lifecycle log: `Opt ID,DSP,Deal,Change,Status,Proposed,Live,Baseline,Result,Notes` |
| `OptTargets_<Client>_<Code>.csv` | 5-column target values per `Opt ID`: `Opt ID,Lever,Value,Added,Source` (bare domain, app bundle ID, geo, OS, or hour range in `Value`; composite key `(Opt ID, Lever, Value)`) |

### Campaign Discovery & Read-Before-Write Merge Guardrail
1. **Discovery Order:** Check the current conversation thread / attached files first, then look up `Optimizations/CampaignRegistry.yaml` (filtering `"review my opts"` to active campaigns matching the requesting trader's `team.trader` or `team.digest_to`).
2. **Read-Before-Write Merge Guardrail:** Before writing `OptLog.csv` or `OptTargets.csv`, re-read the latest file from storage, merge `OptLog` by `Opt ID` (preserving concurrent status or `Live` updates) and `OptTargets` by `(Opt ID, Lever, Value)` (keeping each `Opt ID`'s targets scoped to that `Opt ID`). For local `workspace` / `custom` paths, write atomically with `fcntl.flock` + temp-file `os.replace`; for `connected_storage`, read from `storage.backend`, merge in memory, and write back through the storage connector.

---

## 2. Guided Campaign Config Onboarding & Day-1 DSP Report Preflight QA (`show_ui`)

When a user asks to onboard a campaign (e.g., `"onboard <Client> <CampaignCode>"`):
1. **Auto-Discover Reports & Deals:**
   - Locate available delivery reports from any configured source: a connected report inbox (`mailbox`), a connected cloud storage / S3 / BI / warehouse feed (`connected_feed`), or files attached in chat (`uploaded_file`).
   - Run **Day-1 DSP Report Preflight QA** in Python: inspect report columns, date coverage, active deals, dimensional report coverage (`overall`, `domain`/`app`, `geo`, `time_of_day`, `system`/`device`), and KPI numerator/denominator columns. Surface any gaps in the card's `status_list` using dynamic counts from the inspected files.
2. **Present the Interactive `show_ui` Campaign Config Builder Card** (Blueprint 1 in `skills/campaign-optimization-log/REFERENCE.md`):
   - **Tab 1 — Campaign, Team & Save Location:** Client name, campaign code, `opt_id_prefix`, flight dates, optimization/light days, and `storage_target` (`workspace_default` or `custom`, plus `connected_storage` with `storage_backend` and `storage_custom_path` when a writable storage connector is registered in `manifest.yaml`).
   - **Tab 2 — KPI, Thresholds & Levers:** `primary_kpi`, `kpi_direction`, `kpi_benchmark` (or `use_baseline_fallback` toggle, which sets `benchmark: null`), optional `channel_overrides`, eligibility thresholds (`min_spend_share_pct`, `min_projected_kpi_lift_pct`, `min_impression_threshold`, `min_spend_usd`, `max_volume_cut_pct`), and allowed SSP/DSP levers.
   - **Tab 3 — Active Deals & Report Sources:** Multi-select `active_deals` table (`row_key="deal_name"`), `custom_deals_override` for manually adding/overriding any SSP/DSP/deal rows, `report_source_type` (`mailbox` with `report_mailbox`, `connected_feed` with `report_feed_source` / `<dsp_id>_feed_source`, or `uploaded_file`), and `deal_field`.
   - **Actions:** `save_config` (*Save Config & Initialize Log*) and `save_and_run` (*Save Config & Run First Optimization Review Now*).
3. **Validate & Initialize:**
   - Enforce KPI guardrails: if a numeric primary KPI benchmark is missing and `use_baseline_fallback` is false, set `kpi.primary.status: "blocked"` (which skips proposing new rows until resolved); if `no_conversion_tracking: true`, never configure or compute `CPA` or `ROAS`.
   - Convert percentage thresholds to decimal fractions (`min_spend_share_pct / 100.0`, `min_projected_kpi_lift_pct / 100.0`, `max_volume_cut_pct / 100.0`), write `CampaignConfig`, `OptLog`, and `OptTargets` to the selected storage location, index the campaign in `Optimizations/CampaignRegistry.yaml`, and if `save_and_run` was clicked, immediately run the initial review and open the Workbench card.

---

## 3. Log Lifecycle, Scheduled Queue & Review Runs

### Status Lifecycle & Scheduled Queue Cap
- **Statuses:** `Backlog` → `Proposed` → `Live` → `Kept` / `Dropped` (while regressed `Live` rows stay `Status = Live` with `[REVERT RECOMMENDED]` in `Notes` until the revert is executed and verified in the SSP).
- **Scheduled Queue & Carryover (`max 5 campaigns per run`):** On scheduled runs, process `carried_over: true` campaigns first (honoring `carried_over_run_type`: `"full"`, `"light"`, or `"wrap"`), then oldest `state.last_full_opt_date`. Any remaining eligible campaigns are marked `carried_over: true` and `carried_over_from: "<YYYY-MM-DD>"`. Advance `state.*` dates and `wrap_completed: true` only after saving files and completing any outbound digest notification.
- **Mid-Week Light Check (`cycle.light_days`):** Score active `Live` rows and check pacing (`>30%` spend drop); stay silent unless a `Live` row is regressing `>= 5%` at `>= 3` days in flight or pacing dropped `> 30%`.

### Full Optimization Review Steps
1. **Load:** Read `CampaignConfig`, `OptLog`, `OptTargets`, and the latest delivery reports from each DSP's configured `source` (`mailbox`, `connected_feed`, or `uploaded_file`). Deduplicate overlapping dates and verify active deals.
2. **Go-Live Check:** For each `Proposed` row, check whether targeted segments dropped ≥ 90% in impression share (for Block Lists / exclusions) or reached ≥ 90–95% share (for Allow Lists / inclusions) for 2+ consecutive days while remaining deal delivery stayed active. When `auto_detect_live` is `true`, mark `Status = Live`.
3. **Results (3d Early Read / 7d Decision):** Compare pre-change vs. post-change KPI performance over equal windows using ratio of sums:
   - **Days 3–6 (`3 <= days_in_flight < 7`):** Keep `Status = Live` (flagging `[EARLY WARNING]` if regressing `>= 5%`).
   - **Day 7+ (`days_in_flight >= 7`):**
     - Improved (`> +1%` toward goal) or held benchmark → `Status = Kept`.
     - Inconclusive (`-1%` to `+1%`) → `Status = Dropped` (with `"inconclusive"` in `Notes`).
     - Materially regressed (`< -1%`) → keep `Status = Live`, prepend `"[REVERT RECOMMENDED]"` to `Notes`, and surface at the top of the Workbench until `revert <Opt ID>` is verified in the SSP (then transition to `Dropped`).
4. **What's Next (SSP vs. DSP Levers, Scale Protection & Supply Expansion):**
   - If `kpi.primary.status == "blocked"`, skip proposing new rows and prompt the trader to set a primary KPI benchmark.
   - Identify candidate optimizations (capped at `cycle.max_new_opt_groups_per_run`, default `5`) meeting `cycle.min_sample` and `cycle.materiality` across the campaign's configured levers (`levers.ssp_direct` and `levers.client_request`) and `ssp_constraints`:
     - **SSP-Side Supply Shaping (`[SSP Console]` or `[Chat API]` when an SSP connector is configured):** Domain/app Block Lists & Allow Lists, geo targeting, device/OS/browser targeting, dayparting, viewability/format filters, and deal floor adjustments.
     - **DSP-Side Spend Shaping (`[Client Request]` / `[DSP]`):** Bid adjustments, line-item or insertion-order budget shifts, frequency caps, and pacing controls.
   - **Scale Warning (`[SCALE WARNING]`):** If a proposed exclusion removes more than `cycle.max_deal_volume_cut_share` (default `0.40`) of a deal's volume, prepend `[SCALE WARNING: removes XX% of deal volume]` in `Notes`.
5. **Save & Notify:** Persist `OptLog` and `OptTargets` via the Read-Before-Write Merge Guardrail and send a concise summary notification (via `ses_outbound` or the workspace's configured email tool when running as a scheduled task) pointing the trader to `"review <Client> opts"`.

### End-of-Flight Closeout (`today > campaign.end_date`)
Resolve any remaining open rows (`Kept` or `Dropped`), send or display the final Campaign Optimization Wrap Summary, and set `campaign.active: false`, `state.wrap_completed: true`.

---

## 4. The Chat-First Optimization Workbench (`show_ui`) & Execution

Triggered when a user says **`"review <Client> opts"`** (single campaign) or **`"review my opts"`** (unified multi-campaign Workbench across active campaigns, prefixing row keys and control IDs with `<campaign_code>`).

### Workbench Card Layout (Blueprint 2 in `skills/campaign-optimization-log/REFERENCE.md`)
- **KPI Summary Strip:** Top `stat` cards showing trailing KPI vs. benchmark, spend, and verified 7-day wins.
- **Tab 1 — Proposed SSP & Deal Optimizations:** Multi-select table (`id="selected_opts"`, `row_key="opt_id"`). Clean items are pre-selected; `[SCALE WARNING]` items are unchecked by default. Multi-target rows include an inline `multi_select` (`id="<opt_id>_domains"`, `allow_custom=true`) to check/uncheck individual targets.
- **Tab 2 — Console & Client DSP Requests:** Table + per-row `choice` (`id="<opt_id>_status_action"`: `none` | `live` | `sent`).
- **Tab 3 — Backlog & Signals:** Multi-select table (`id="promote_backlog"`, `row_key="opt_id"`) to promote backlog items to `Proposed`.
- **Footer:** Question box (`id="trader_question"`) + `Apply Selected Updates` (`id="execute_selected"`) and `Ask / Validate First` (`id="ask_questions"`, `validate=false`).

### Handling Workbench Submissions
- **`ask_questions` (Side-Effect Free):** Do not mutate `OptLog` or `OptTargets`. Answer `trader_question` from the report data in Python and re-render the Workbench card preserving current selections.
- **`execute_selected` (`Apply Selected Updates`):**
  1. Persist any `*_status_action` changes (`live` takes precedence over `sent`), promote checked `promote_backlog` rows to `Proposed` (materializing their `OptTargets` rows), and save inline `*_domains` edits to that `Opt ID` in `OptTargets.csv`.
  2. **Compute Additive Deal-Level Targets & Execute / Export (3 Execution Branches):**
     - For Block Lists, compute the deal-level union of `Kept` + `Live` + selected `Proposed` targets in memory (keeping `OptTargets.csv` rows scoped per `Opt ID`) and union with the live deal's existing block list. For Allow-List removals, pass only the removal targets.
     - **Shared Targeting-List Isolation Rule (Create-Then-Swap, Block-List Deals Only):** On any SSP where block lists are shared reusable objects across deals, never mutate a shared block list attached to other deals; create/attach a campaign-dedicated list (`<Client>_<CampaignCode>_Optimization_Block_List`) alongside preserved baseline lists.
     - **Case A (No SSP write connector registered in `manifest.yaml`):** Output a copy-paste-ready **Target List & Operator Execution Summary** and **Post-Execution Verification Receipt** table in chat.
     - **Case B (`deal_plan` + SSP connector registered in `manifest.yaml`):** Pre-flight read the live deal, stage the merged targets via `deal_plan`, execute on approval, and verify via a **Post-Execution Verification Receipt** before setting `Status = Live`.
     - **Case C (Direct SSP write connector registered in `manifest.yaml` without `deal_plan`):** Pre-flight read the live deal, stage the merged targets on the SSP connector's write tool, and verify via a **Post-Execution Verification Receipt** before setting `Status = Live`.

---

## 5. Client-Facing Optimization Story Emails (`show_ui`) & 4-Test Spend Elasticity

Triggered on **`"craft a client email for <Client>"`** or **`"can <Client> take incremental budget?"`** (Blueprint 3 in `skills/campaign-optimization-log/REFERENCE.md`).

### Zero-Jargon Client-Facing Rules
- Strip all internal campaign codes, `Opt ID`s, raw SSP deal IDs, and targeting list IDs from both the email body and any `.xlsx` attachment (`attach_kpi_summary_xlsx`), and include only `Live` and `Kept` rows.
- Focus each chart/table section on a single coherent primary KPI (`primary_kpi_scope`).
- Generate a single-panel horizontal bar chart (`1088x420`) styled with `protocols/email-style.yaml` (`chart_colors`: `secondary_series` for Actual, `primary_series` for Post-Curation, `text`, `background`, `grid`).
- When `ses_outbound` (or another outbound email tool) is registered and enabled in `manifest.yaml`, stage/send the email with the chart PNG and client-safe `.xlsx`; otherwise provide the rendered HTML, chart PNG, and client-safe `.xlsx` directly in chat.

### The 4-Test Spend Elasticity & Incremental Budget Evaluator
Run across the 28-day report window in Python before recommending budget changes:
1. **Daily Spend vs. Daily KPI Elasticity** (Low / Mid / Peak spend quartiles & correlation).
2. **Post-Curation Supply Headroom** (impression and spend share retained after active exclusions).
3. **High-Efficiency Channel / Deal Mix Headroom** (deals/channels beating benchmark with room to scale).
4. **Recommended Safe Daily & Weekly Scaling Band** paired with a direction-aware **KPI Guardrail Floor** (`higher_is_better`) or **KPI Guardrail Ceiling** (`lower_is_better`).

---

## 6. Config Sync & Self-Serve Customization
- Traders can say **`"sync all campaign configs"`** to re-check active campaigns in `Optimizations/CampaignRegistry.yaml` against the latest DSP reports and surface any newly active deals or report-feed gaps.
- Traders can say `"update the optimization protocol: <change>"` in chat to update `Optimizations/Protocols/optimization-log-protocol.md` in place, or use **Clone & edit** in **Settings → Skills** / **Prompt Library** to customize and share workspace-wide overrides.

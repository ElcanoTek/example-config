---
name: campaign-optimization-log
description: Onboard campaigns to the Optimization Log (`CampaignConfig`, `OptLog`, `OptTargets`), open the interactive Chat-First Optimization Workbench (`show_ui`), apply or stage additive SSP deal updates with verification receipts, build client-facing optimization story emails or spend-elasticity budget pitches (`show_ui`), and make self-serve updates to the protocol in workspace storage. Use whenever a trader says "onboard <Client>", "set up a campaign config", "review <Client> opts", "review my opts", "open <Client> workbench", "show open opts", "execute <Opt ID>", "<Opt ID> is live", "keep/revert/drop <Opt ID>", "log this to the backlog", "craft a client email for <Client> opts", "can <Client> take incremental budget", "sync all campaign configs", or "update the optimization protocol".
---

# Campaign Optimization Log & Workbench (`show_ui`)

The end-to-end campaign optimization workflow for programmatic traders. It pairs
`protocols/optimization-log-protocol.md` (the bundled baseline specification, synchronized with live `Optimizations/Protocols/optimization-log-protocol.md` in workspace storage) and
`protocols/CampaignConfig_TEMPLATE.yaml` with three interactive **`show_ui`**
cards (see `skills/campaign-optimization-log/REFERENCE.md` for validated card
blueprints):

1. **Campaign Config & Onboarding Builder (`show_ui`)** — auto-discovers DSP
   reports and SSP deals, runs Day-1 DSP Report Preflight QA, lets the trader
   confirm KPIs/benchmarks/eligibility thresholds/levers and choose **where to save**
   (`storage_target`: `Optimizations/<Client> (<CampaignCode>)/` in the persistent
   conversation workspace or a custom workspace subfolder), and initializes all 3
   files (`CampaignConfig_<Client>_<Code>.yaml`, `OptLog_<Client>_<Code>.csv`,
   `OptTargets_<Client>_<Code>.csv`).
2. **Chat-First Optimization Workbench (`show_ui`)** — opens on `"review <Client> opts"`
   (single campaign) or `"review my opts"` (unified multi-campaign queue across
   all active campaigns in the workspace) with pre-checked clean SSP
   changes, unchecked `[SCALE WARNING]` changes (exceeding `cycle.max_deal_volume_cut_share`),
   inline domain target editing, 1-click `[Console/DSP]` / `[Client Request]` status
   selectors, backlog promotion, and an inline **"Ask / Validate First"** question
   box.
3. **Client-Facing Optimization Story & Spend Elasticity Builder (`show_ui`)** —
   opens on `"craft a client email for <Client>"` with Story Archetypes
   (*Just Executed Showcase*, *Proven 7-Day Lift*, *Incremental Budget Pitch*),
   toggleable modular blocks, zero-jargon formatting rules, a single-panel
   `1088x420` horizontal bar chart styled with `protocols/email-style.yaml`'s
   `chart_colors` palette, and the **4-Test Spend Elasticity Evaluator**.

When a trader asks a general ad-hoc question on a campaign that has no
`CampaignConfig` and does not ask for the Optimization Log/Workbench, use
`protocols/optimization.md` (or `protocols/ssp-optimization.md` where present).

---

## Flow 1 — Guided Campaign Config Onboarding (`show_ui`)

Triggered when a trader says **"onboard `<Client>` `<CampaignCode>` to the optimization log"**,
**"set up a campaign config for `<Client>`"**, or uses the
`Optimization_Onboard_Campaign` Prompt Library starter.

1. **Auto-Discover & Run Day-1 Preflight QA FIRST (before calling `show_ui`):**
   - Search the conversation workspace (`Optimizations/`) and the report inbox
     (`mcp_explorer_find_latest_report` / `mcp_explorer_search_emails`) for the
     campaign code or client name. For mailbox sources, `mcp_explorer_find_latest_report`
     returns message metadata only — always download the report payloads into
     your working directory:
     - For attachment payloads: call `mcp_explorer_get_download_link(s3_key=..., filename=...)` + `download_url(url=<its download_url>)`.
     - For body download links (`delivery: download_link`): call `mcp_explorer_extract_download_links(s3_key=...)` first to extract the candidate URLs, then call `mcp_explorer_get_download_link(s3_key=..., url=...)` + `download_url(url=<its download_url>)`.
     - Verify the downloaded files exist in your working directory before inspecting them.
   - Download one sample report per report type into your working directory and
     inspect columns in `run_python`:
     - Identify `deal_field` (`Inventory Source`, `Deal name`, etc.), date window,
       and active campaign deal names.
     - Check whether all 5 dimensional report types exist (`overall`, `domain`/`app`,
       `geo`, `time_of_day`, `system`) and whether conversion/revenue columns are
       populated when the KPI is `ROAS` or `CPA`. Flag any missing report or
       filename quirk (e.g. a truncated campaign code token) in the card's `status_list`.
   - Resolve `campaign.curator`, `campaign.deal_plan_client_id`, `ssp_seats`, and `deals`
     (`deal_id`, `internal_deal_id`, `channel`, `deal_url`, `baseline_block_list_ids`):
     - When `manifest.yaml` registers `deal_plan` and SSP connectors, set
       `campaign.deal_plan_client_id` to the bundle's Deal Planner profile key in
       `deal_profiles/<id>.yaml` (for example `"reklaim"` — never an advertiser or
       campaign name) and read live deal details via the registered SSP read tools.
     - When `manifest.yaml` does not register `deal_plan` or SSP connectors, set
       `campaign.deal_plan_client_id: null` and populate `deals` from the discovered
       DSP report deal names and any user-provided deal metadata.
2. **Render the `show_ui` Campaign Config Builder Card:**
   - Follow Blueprint 1 in `skills/campaign-optimization-log/REFERENCE.md`. When
     multiple DSPs are discovered during preflight (`len(dsps) > 1`), render
     repeatable per-DSP source and KPI field groups (`<dsp_id>_report_source_type`,
     `<dsp_id>_mailbox_address`, `<dsp_id>_deal_field`,
     `<dsp_id>_primary_kpi`, `<dsp_id>_kpi_direction`, `<dsp_id>_kpi_target`,
     `<dsp_id>_benchmark_ctv`, `<dsp_id>_benchmark_olv`, `<dsp_id>_benchmark_display`)
     so every `dsps[]` entry in `CampaignConfig` receives a complete source and KPI block.
   - Pre-fill discovered values, default `optimization_days` / `light_days` for
     the trader, trader-confirmable eligibility thresholds (`min_spend_share_pct`,
     `min_projected_kpi_lift_pct`, `min_impression_threshold`, `max_volume_cut_pct`),
     and the **`storage_target`** selector (`workspace_default` for
     `Optimizations/<Client> (<CampaignCode>)/` or `custom`).
3. **On Submission (`save_config` or `save_and_run`):**
   - Normalize percentage fields from Blueprint 1 into decimal shares before writing `CampaignConfig.yaml`:
     - `cycle.materiality.min_addressable_spend_share = min_spend_share_pct / 100.0`
     - `cycle.materiality.min_projected_kpi_change = min_projected_kpi_lift_pct / 100.0`
     - `cycle.max_deal_volume_cut_share = max_volume_cut_pct / 100.0`
     - `cycle.min_sample.impressions = int(min_impression_threshold)`
   - Key `deals` in `CampaignConfig.yaml` by `"<dsp_id>::<Exact Deal Name in DSP Report>"`
     (or `"<Exact Deal Name in DSP Report>"` when the campaign has a single DSP) so
     multi-DSP campaigns never collide on shared deal names.
   - Build and validate `CampaignConfig_<Client>_<Code>.yaml` (`schema_version: "3.0"`),
     `OptLog_<Client>_<Code>.csv` (10 columns), and `OptTargets_<Client>_<Code>.csv`
     (5 columns: `Opt ID,Lever,Value,Added,Source`, with **bare SSP-executable**
     values in `Value`).
   - Save all 3 files to `Optimizations/<Client> (<CampaignCode>)/` (or `storage_custom_path`)
     and upsert the campaign pointer in `Optimizations/CampaignRegistry.yaml`.
   - Remind the trader how to click **"Make recurring task…"** (one weekday task per
     `team.digest_to` recipient at `15:00 UTC`) if they have not yet scheduled their
     weekday runner.
   - If the trader clicked `save_and_run`, immediately run the first Optimization
     Day review (`protocols/optimization-log-protocol.md` Section 5) and open the
     Workbench card!

---

## Flow 2 — The Chat-First Optimization Workbench (`show_ui`) & Execution

Triggered when a trader says **"review `<Client>` opts"**, **"open `<Client>` workbench"**,
**"review my opts"**, **"open my workbench"**, or **"review `<Trader>`'s opts"**.

1. **Discover & Load Campaign Files (with Self-Healing Migration):**
   - Locate `CampaignConfig_<Client>_<Code>.yaml` in `Optimizations/`. For `"review my opts"`,
     load all active configs where `team.trader` / `team.digest_to` matches the trader.
   - If `schema_version` is older than `"3.0"` or uses legacy keys (`review_days`,
     `dsps[].kpi.channel_benchmarks`, `dsps[].kpi.channel_kpi_overrides`,
     `last_full_review`), auto-migrate in memory (keeping KPI paths under each
     entry in `dsps[]`) and write back in place (unless running in `DRY_RUN` mode,
     which migrates in memory only without writing to disk). If `team.backup_until`
     has passed (`today > team.backup_until`), clear `team.backup_trader` and
     `team.backup_until`.
   - Load co-located `OptLog_<Client>_<Code>.csv` and `OptTargets_<Client>_<Code>.csv`.
2. **Render the `show_ui` Optimization Workbench Card:**
   - Follow Blueprint 2 in `skills/campaign-optimization-log/REFERENCE.md`.
   - In multi-campaign mode (`"review my opts"`), qualify **every** selectable row key,
     per-row target selector ID, and per-row status selector ID with the campaign code:
     - Table `row_key` values: `<campaign_code>:<opt_id>` (e.g. `CMP00128:ACM-005`)
     - Target multi-select IDs: `<campaign_code>_<opt_id>_domains` (e.g. `CMP00128_ACM_004_1_domains`)
     - Status choice IDs: `<campaign_code>_<opt_id>_status_action` (e.g. `CMP00128_ACM_006_status_action`)
     Decode the `<campaign_code>` prefix on submission so IDs never collide across campaigns.
   - **Tab 1 (`1. Proposed SSP Optimizations`):** Multi-select `table` (`id="selected_opts"`,
     `row_key="opt_id"`). Pre-select clean SSP recommendations; leave `[SCALE WARNING]`
     (exceeding `cycle.max_deal_volume_cut_share`, default `0.40` when absent) and
     `[STALE]` rows **unchecked by default**. Include an inline `multi_select`
     (`id="<opt_id>_domains"`) for multi-target rows so the trader can uncheck or add
     individual domains.
   - **Tab 2 (`2. Console & Client Requests`):** Display `table` + per-row mutually
     exclusive `choice` (`id="<opt_id>_status_action"`, options `none` | `live` | `sent`)
     to mark `<Opt ID> is Live` (`Status = Live`, `Live = <today>`) or
     `<Opt ID> Sent to Client` (`Status = Proposed`, with `"<today>: Sent to client (<trader>)"`
     appended to `Notes`; `:live` always takes strict precedence over `:sent` for the same `Opt ID`).
   - **Tab 3 (`3. Backlog & Signals`):** Multi-select `table` (`id="promote_backlog"`,
     `row_key="opt_id"`) to promote backlog items early.
   - **Footer:** Inline question `text_input` (`id="trader_question"`) +
     `Apply Selected Updates` (`id="execute_selected"`)
     and `Ask / Validate First` (`id="ask_questions"`, `validate=false`).
3. **Apply Status & Backlog Ticks on Every Submission (`ask_questions` AND `execute_selected`):**
   - On **both** `ask_questions` and `execute_selected`, first apply any selected
     `*_status_action` changes (`live` sets `Status = Live`, `Live = <today>`, and
     appends `"<today>: Confirmed live (<trader>)"` to `Notes`; `sent` keeps
     `Status = Proposed` and appends `"<today>: Sent to client (<trader>)"` to `Notes`),
     any checked `promote_backlog` rows (`Status = Proposed`, `Proposed = <today>`,
     **and materialize the candidate's target rows into `OptTargets.csv`**), and any
     inline target edits (`*_domains`), using the **Read-Before-Write Merge Guardrail**.
   - When the action is `ask_questions`: load the campaign's DSP reports in
     `run_python`, answer `trader_question` with exact numbers, and re-render the
     Workbench card (`replaces=<old_card_id>`) preserving unapplied `selected_opts`
     and `*_domains` selections while **resetting all applied `*_status_action` controls
     back to `"none"`** so a subsequent submission never duplicates status updates.
4. **When the Trader Clicks `Apply Selected Updates` (`action="execute_selected"`):**
   - **Check Bundle Connector Contract (`manifest.yaml`):**
     - **If this bundle's `manifest.yaml` does NOT register `deal_plan` and SSP seat connectors** (standard analytics/reporting bundles):
       - Do **not** ask the trader to enable unprovisioned Deal Planner or SSP connectors.
       - Persist all selected target edits to `OptTargets.csv` (unioned with prior `Kept`/`Live` targets for that deal) and apply any `*_status_action` updates to `OptLog.csv` using the **Read-Before-Write Merge Guardrail**.
       - Print a copy-paste-ready **Target List & Operator Execution Summary** (plus the **Post-Execution Verification Receipt** checklist with `Before → After` target counts and `[Open in <SSP> ↗](<deal_url>)` links when `deal_url` is configured) so the trader can apply the update in the SSP console and mark the row `Live`.
     - **If this bundle's `manifest.yaml` DOES register `deal_plan` and SSP seat connectors** (for example, `reklaim-config`):
       - Ensure `deal_plan` and each target deal's SSP connector (`ssp_seats.<SSP>.mcp_prefix`) are enabled (calling `enable_mcp_connectors` if listed under Available Optional MCP Connectors).
       - **Pre-Flight Live Deal Read & Additive Block-List Union vs Allow-List Removal:**
         Read each deal live on the SSP first and inspect whether the deal uses a **Block List** or an **Allow List**:
         - **Block List (`add block` / `include block`):** Union `Kept` + `Live` + selected `Proposed` block targets from `OptTargets` **AND** union with the live deal's existing block list so replace-all writes never drop prior blocks.
         - **Allow List (`remove allow`):** Pass **ONLY** the selected removal targets (plus any previously retained `Kept`/`Live` removal targets from `OptTargets` for that deal) to `remove allow` — **NEVER** union removal targets with the live allow list.
       - **Magnite DV+ Shared-List Isolation Rule (Create-Then-Swap):** Never edit a shared list used by other campaigns/clients and never attach a foreign campaign's list. Keep any baseline account blocklist intact (`baseline_block_list_ids`) and use a campaign-dedicated list named `<Client>_<CampaignCode>_Optimization_Block_List`. If `optimization_block_list_id` is `null`, stage `magnite_create_targeting_list` first, persist the returned list ID to `CampaignConfig` once approved, and only then stage the deal update that attaches the new list ID alongside preserved `baseline_block_list_ids` and detaches any foreign campaign list.
       - **Route Through the Canonical Deal Planner Recommendation & Update Flow:**
         Use `campaign.deal_plan_client_id` from `CampaignConfig.yaml` (the bundle's fixed Deal Planner profile ID such as `"reklaim"`, resolved via `get_deal_profile(client_id)` — never an advertiser or campaign name). Present the SSP deal changes on `mcp_deal_plan_recommendation_card` when required by `system_prompts/chat.md`, then call `mcp_deal_plan_compile_update_plan(recommendation_set=..., selected=...)` → `mcp_deal_plan_finalize_plan(plan_id, rows=<selected row numbers 1..N>)` → stage `mcp_deal_plan_execute_plan(plan_id)`.
5. **Mandatory Post-Approval Verification Receipt:**
   - When an SSP `execute_plan` approval completes, verify live SSP readback against
     `OptTargets` (every target present, prior `Kept`/`Live` count intact, preserved
     `baseline_block_list_ids` allowed while rejecting foreign campaign lists, deal
     status `Active`) and print the **Post-Execution Verification Receipt**
     table (`Opt ID & Deal`, `SSP Deal ID + [Open in <SSP> ↗](<deal_url>)`,
     `Before → After Summary`, `VERIFIED / FLAGGED`).
   - Update `OptLog` (`Status = Live`, `Live = <today>`, `Notes`) using the
     **Read-Before-Write Merge Guardrail** (acquire the per-campaign lock file
     `Optimizations/<Client> (<Code>)/.lock` via `fcntl.flock`, re-read the latest
     CSV from disk, merge `OptLog` by `Opt ID` and `OptTargets` by composite key
     `(Opt ID, Lever, Value)`, write to `<file>.tmp`, and atomically commit via
     `os.replace`).

---

## Flow 3 — Client-Facing Optimization Story Emails (`show_ui`) & Spend Elasticity

Triggered when a trader says **"craft a client email for `<Client>`"**,
**"client story for `<Client>` opts"**, or **"can `<Client>` take incremental budget?"**.

1. **Render the `show_ui` Client Story Builder Card** (Blueprint 3 in
   `skills/campaign-optimization-log/REFERENCE.md`) unless the trader asked to
   skip straight to the standard draft:
   - **Story Archetype (`choice`):** `just_executed` (*"Just Executed" Proactive
     Curation Showcase*), `proven_lift` (*"Proven Lift" 7-Day Readout*), or
     `expansion_pitch` (*Strategic Allocation & Incremental Budget Pitch*).
   - **Modular Sections (`multi_select`):** Blocks 1–6 pre-checked by default;
     Block 7 (*4-Test Spend Elasticity & Incremental Budget Pitch*) included for
     `expansion_pitch`.
   - **Domain Granularity (`choice`):** `full_list` (default), `top_examples`, or
     `category_summary`.
2. **Enforce Zero-Jargon Client-Facing Rules Automatically (`protocols/optimization-log-protocol.md` Section 7.1):**
   - **NEVER include** internal campaign codes (`CMP00166`), internal Opt IDs (`ACM-001`), raw
     SSP Deal IDs (`IX176...`), targeting list IDs, or API operator mechanics in the
     email body or any attached file.
   - Anchor strictly on the selected primary KPI (`ROAS`, `CTR`, `CPA`, `VCR`) without
     secondary-metric clutter or formula footnotes. When a campaign has multiple
     `dsps[]` entries or channel-level KPI overrides (`channel_kpis`), let the trader
     pick a single DSP/channel KPI on the Story Builder card (`primary_kpi_scope`) or
     render separate per-KPI sections — never average or combine incompatible KPIs on
     one chart axis.
   - Render a **single-panel horizontal bar chart** (`figsize=(10.88, 4.2)`,
     `dpi=100`, legend above plot `bbox_to_anchor=(0.5, 1.16)`) using the
     machine-readable **`chart_colors` palette read from `protocols/email-style.yaml`**
     (`comparison_series` for Actual, `primary_series` for Post-Curation, `text` for
     the dashed benchmark line and labels, `background` for figure/axes fill, and
     `grid` for gridlines) so the chart PNG always matches the deployment's brand theme.
   - For **Spend Elasticity / Incremental Budget**, run all 4 tests in `run_python`
     (28d Daily Spend Quartiles & Correlation, Post-Curation Supply Headroom,
     High-Efficiency Channel Mix Headroom, and Safe Daily/Weekly Scaling Band with a
     direction-aware KPI Guardrail Bound derived from `kpi.primary.direction`: a
     **KPI Guardrail Floor** when `higher_is_better` and a **KPI Guardrail Ceiling**
     when `lower_is_better`).
   - Call `mcp_ses_outbound_render_email` following `protocols/email-style.yaml`.
     Take the returned `content_file` and `inline_attachments` list (which carries
     the rendered brand header logo), append the chart PNG entry
     `{"path": chart_png_path, "cid": "story_chart"}` to `inline_attachments`, and
     stage `mcp_ses_outbound_send_email` with `content_file`, `inline_attachments`,
     and (when `attach_kpi_summary_xlsx == true`) `attachments=[kpi_summary_xlsx_path]`
     containing only client-safe aggregated KPI and channel metrics (never raw SSP
     deal IDs or targeting-list IDs).

---

## Flow 4 — Live Protocol Resolution & Self-Serve Protocol Updates

Traders can evolve the Optimization Log protocol and workflow **100% self-serve** without waiting on an engineering deploy:

1. **Live Protocol Check (`Optimizations/Protocols/optimization-log-protocol.md`):**
   - Check `Optimizations/Protocols/optimization-log-protocol.md` in the conversation workspace.
   - If the workspace copy has a newer version/date header (e.g., `v3.1+`) or trader-added rules beyond the bundled `protocols/optimization-log-protocol.md`, apply those live rules automatically alongside the `show_ui` blueprints in `skills/campaign-optimization-log/REFERENCE.md`. Fall back to `protocols/optimization-log-protocol.md` if no workspace override exists or if the bundled copy is newer.
2. **Updating the Protocol Self-Serve in Chat (`"update the optimization protocol..."`):**
   - When a trader asks to update, refine, or add a rule to the protocol (e.g., `"update the optimization protocol: ..."`, `"change the optimization log protocol so..."`):
     1. Read `Optimizations/Protocols/optimization-log-protocol.md` (if none exists yet or it is older than the bundled `protocols/optimization-log-protocol.md`, start from the bundled `v3.0` file and merge any trader-specific rules so nothing is lost).
     2. Apply the requested improvement, increment the version/date header (`# Optimization Log Protocol (v3.x, YYYY-MM-DD)`), and add a one-line entry under `## Changelog`.
     3. Save the updated file **in place** to `Optimizations/Protocols/optimization-log-protocol.md`.
     4. Remind the trader they can also click **Clone & edit** on `campaign-optimization-log` in **Settings → Skills** and check **Share with this workspace** so skill-level customizations are shared across the workspace immediately.

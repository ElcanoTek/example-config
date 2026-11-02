---
name: campaign-optimization-log
description: Onboard campaigns to the Optimization Log (`CampaignConfig`, `OptLog`, `OptTargets`), open the interactive Chat-First Optimization Workbench (`show_ui`), execute additive SSP deal updates with post-approval verification receipts, build client-facing optimization story emails or spend-elasticity budget pitches (`show_ui`), and make self-serve updates to the protocol in workspace storage. Use whenever a trader says "onboard <Client>", "set up a campaign config", "review <Client> opts", "review my opts", "open <Client> workbench", "show open opts", "execute <Opt ID>", "<Opt ID> is live", "keep/revert/drop <Opt ID>", "log this to the backlog", "craft a client email for <Client> opts", "can <Client> take incremental budget", "sync all campaign configs", or "update the optimization protocol".
---

# Campaign Optimization Log & Workbench (`show_ui`)

The end-to-end campaign optimization workflow for programmatic traders. It pairs
`protocols/optimization-log-protocol.md` (the bundled baseline specification, synchronized with live `shared/Protocols/optimization-log-protocol.md` or `/Protocols/optimization-log-protocol.md` in workspace storage) and
`protocols/CampaignConfig_TEMPLATE.yaml` with three interactive **`show_ui`**
cards (see `skills/campaign-optimization-log/REFERENCE.md` for validated card
blueprints):

1. **Campaign Config & Onboarding Builder (`show_ui`)** — auto-discovers DSP
   reports and SSP deals, runs Day-1 DSP Report Preflight QA, lets the trader
   confirm KPIs/benchmarks/levers and choose **where to save** (`shared/Optimizations/`,
   Fast.io `/Optimizations/` when configured, campaign folder, root, or workspace), and
   initializes all 3 files (`CampaignConfig_<Client>_<Code>.yaml`,
   `OptLog_<Client>_<Code>.csv`, `OptTargets_<Client>_<Code>.csv`).
2. **Chat-First Optimization Workbench (`show_ui`)** — opens on `"review <Client> opts"`
   (single campaign) or `"review my opts"` (unified multi-campaign queue across
   all active campaigns owned by the trader) with pre-checked clean `[Chat API]`
   changes, unchecked `[SCALE WARNING]` changes (>40% deal volume cut), inline
   domain target editing, 1-click `[Console/DSP]` / `[Client Request]` status
   toggles, backlog promotion, and an inline **"Ask / Validate First"** question
   box.
3. **Client-Facing Optimization Story & Spend Elasticity Builder (`show_ui`)** —
   opens on `"craft a client email for <Client>"` with Story Archetypes
   (*Just Executed Showcase*, *Proven 7-Day Lift*, *Incremental Budget Pitch*),
   toggleable modular blocks, zero-jargon formatting rules, a single-panel
   `1088x420` horizontal bar chart, and the **4-Test Spend Elasticity Evaluator**.

When a trader asks a general ad-hoc question on a campaign that has no
`CampaignConfig` and does not ask for the Optimization Log/Workbench, use the
`deal-optimization` skill (`mcp_deal_plan_recommendation_card`) when available or `protocols/optimization.md`.

---

## Flow 1 — Guided Campaign Config Onboarding (`show_ui`)

Triggered when a trader says **"onboard `<Client>` `<CampaignCode>` to the optimization log"**,
**"set up a campaign config for `<Client>`"**, or uses the
`Optimization_Onboard_Campaign` Prompt Library starter.

1. **Auto-Discover & Run Day-1 Preflight QA FIRST (before calling `show_ui`):**
   - Search `shared/`, Fast.io (`mcp_fastio_helpers_find` if configured),
     and/or the report inbox (`mcp_explorer_find_latest_report`) for the
     campaign code or client name. For mailbox sources, `mcp_explorer_find_latest_report`
     returns message metadata only — always download the report payloads into
     your working directory using `mcp_explorer_get_download_link(s3_key=..., filename=...)` + `download_url(url=<its download_url>)`
     or `mcp_explorer_get_download_link(s3_key=..., url=...)` + `download_url(url=<its download_url>)`
     and verify the downloaded files exist before inspecting them.
   - Download one sample report per report type into your working directory and
     inspect columns in `run_python`:
     - Identify `deal_field` (`Inventory Source`, `Deal name`, etc.), date window,
       and active campaign deal names.
     - Check whether all 5 dimensional report types exist (`overall`, `domain`/`app`,
       `geo`, `time_of_day`, `system`) and whether conversion/revenue columns are
       populated when the KPI is `ROAS` or `CPA`. Flag any missing report or
       filename quirk (e.g. a truncated campaign code token) in the card's `status_list`.
   - Resolve `campaign.curator`, `ssp_seats`, and `deals` (`deal_id`,
     `internal_deal_id`, `channel`, `deal_url`) from live SSP connectors (`*_find_deals` / `*_get_deal`),
     deal profiles (`deal_profiles/`), or archived deal sheets.
2. **Render the `show_ui` Campaign Config Builder Card:**
   - Follow Blueprint 1 in `skills/campaign-optimization-log/REFERENCE.md`. When
     multiple DSPs are discovered during preflight (`len(dsps) > 1`), render
     repeatable per-DSP source and KPI field groups (`<dsp_id>_report_source_type`,
     `<dsp_id>_fastio_source_path`, `<dsp_id>_mailbox_address`, `<dsp_id>_deal_field`,
     `<dsp_id>_primary_kpi`, `<dsp_id>_kpi_direction`, `<dsp_id>_kpi_target`,
     `<dsp_id>_benchmark_ctv`, `<dsp_id>_benchmark_olv`, `<dsp_id>_benchmark_display`)
     so every `dsps[]` entry in `CampaignConfig` receives a complete source and KPI block.
   - Pre-fill discovered values, default `optimization_days` / `light_days` for
     the trader, and the **`storage_target`** selector (`shared_files`
     for Fleet `shared/Optimizations/`, `fastio_optimizations` for Fast.io `/Optimizations/`,
     `fastio_campaign_folder`, `fastio_root`, `workspace_local`, or `custom`).
3. **On Submission (`save_config` or `save_and_run`):**
   - Build and validate `CampaignConfig_<Client>_<Code>.yaml` (`schema_version: "3.0"`),
     `OptLog_<Client>_<Code>.csv` (10 columns), and `OptTargets_<Client>_<Code>.csv`
     (5 columns: `Opt ID,Lever,Value,Added,Source`, with **bare SSP-executable**
     values in `Value`).
   - Save all 3 files to the chosen destination (`shared/Optimizations/` for shared
     workspace files, `mcp_fastio_helpers_upload_file` when using Fast.io, plus local
     markdown download links). Also upsert the campaign pointer in
     `shared/Optimizations/CampaignRegistry.yaml` (or `/Optimizations/CampaignRegistry.yaml`
     in Fast.io when configured) so cross-campaign discovery finds it; note that
     `workspace_local` is for manual single-conversation use only.
   - If the trader clicked `save_and_run`, immediately run the first Optimization
     Day review (`protocols/optimization-log-protocol.md` Section 5) and open the
     Workbench card!

---

## Flow 2 — The Chat-First Optimization Workbench (`show_ui`) & Execution

Triggered when a trader says **"review `<Client>` opts"**, **"open `<Client>` workbench"**,
**"review my opts"**, **"open my workbench"**, or **"review `<Trader>`'s opts"**.

1. **Discover & Load Campaign Files (with Self-Healing Migration):**
   - Locate `CampaignConfig_<Client>_<Code>.yaml` in `shared/Optimizations/` (or via
     `mcp_fastio_helpers_find(query="CampaignConfig_", node_type="file", limit=100, offset=...)`
     paginated until `has_more` is false when Fast.io is configured). For `"review my opts"`,
     load all active configs where `team.trader` / `team.digest_to` matches the trader.
   - If `schema_version` is older than `"3.0"` or uses legacy keys (`review_days`,
     `dsps[].kpi.channel_benchmarks`, `dsps[].kpi.channel_kpi_overrides`,
     `last_full_review`), auto-migrate in memory (keeping KPI paths under each
     entry in `dsps[]`) and write back in place. If `team.backup_until` has passed
     (`today > team.backup_until`), clear `team.backup_trader` and `team.backup_until`.
   - Load co-located `OptLog_<Client>_<Code>.csv` and `OptTargets_<Client>_<Code>.csv`.
2. **Render the `show_ui` Optimization Workbench Card:**
   - Follow Blueprint 2 in `skills/campaign-optimization-log/REFERENCE.md`.
   - In multi-campaign mode (`"review my opts"`), qualify every selectable row key,
     status checkbox value, and target input ID with the campaign code
     (`<campaign_code>:<opt_id>` for `row_key` / option values and
     `<campaign_code>_<opt_id>_domains` for target inputs) so IDs never collide
     across campaigns while keeping `Campaign` and `Opt ID` as visible columns.
   - **Tab 1 (`1. Chat-Executable SSP Opts`):** Multi-select `table` (`id="selected_opts"`,
     `row_key="opt_id"`). Pre-select clean `[Chat API]` rows; leave `[SCALE WARNING]`
     (exceeding `cycle.max_deal_volume_cut_share`, default `0.40` when absent) and
     `[STALE]` rows **unchecked by default**. Include an inline `multi_select`
     (`id="<opt_id>_domains"`) for multi-target rows so the trader can uncheck or add
     individual domains.
   - **Tab 2 (`2. Console & Client Requests`):** Display `table` + per-row mutually
     exclusive `choice` (`id="<opt_id>_status_action"`, options `none` | `live` | `sent`)
     to mark `<Opt ID> is Live` (`Status = Live`, `Live = <today>`) or
     `<Opt ID> Sent to Client` (`Status = Proposed`, with `"<today>: Sent to client (<trader>)"`
     appended to `Notes`; if `manual_confirmations` is used, `:live` always takes strict
     precedence over `:sent` for the same `Opt ID`).
   - **Tab 3 (`3. Backlog & Signals`):** Multi-select `table` (`id="promote_backlog"`,
     `row_key="opt_id"`) to promote backlog items early.
   - **Footer:** Inline question `text_input` (`id="trader_question"`) +
     `Stage SSP Updates for Selected ({{ len(selected_opts) }})` (`id="execute_selected"`)
     and `Ask / Validate First` (`id="ask_questions"`, `validate=false`).
3. **Apply Status & Backlog Ticks on Every Submission (`ask_questions` AND `execute_selected`):**
   - On **both** `ask_questions` and `execute_selected`, first apply any checked
     `manual_confirmations` (using canonical `OptLog` lifecycle fields: `<Opt ID> is Live`
     sets `Status = Live`, `Live = <today>`, and appends `"<today>: Confirmed live (<trader>)"`
     to `Notes`; `<Opt ID> Sent to Client` keeps `Status = Proposed` and appends
     `"<today>: Sent to client (<trader>)"` to `Notes`) and any checked `promote_backlog`
     rows (`Status = Proposed`, `Proposed = <today>`), plus any inline target edits
     (`<opt_id>_domains`).
   - When the action is `ask_questions`: load the campaign's DSP reports in
     `run_python`, answer `trader_question` with exact numbers, and re-render the
     Workbench card (`replaces=<old_card_id>`) preserving their selections.
4. **When the Trader Clicks `Stage SSP Updates for Selected` (`action="execute_selected"`):**
   - **Check Connectors:** Verify `Deal Planner` (`mcp_deal_plan_*`) and each
     target deal's SSP seat connector (`ssp_seats.<SSP>.mcp_prefix`) are enabled
     in the Tools picker. Never mutate the wrong seat for a deal; if a connector is
     missing, name its `connector_label` and ask the trader to enable it.
   - **Pre-Flight Live Deal Read & Additive Block-List Union vs Allow-List Removal:**
     Read each deal live on the SSP first and inspect whether the deal uses a
     **Block List** or an **Allow List**:
     - **Block List (`add block` / `include block`):** Union `Kept` + `Live` +
       selected `Proposed` block targets from `OptTargets` **AND** union with the
       live deal's existing block list so replace-all writes never drop prior blocks.
     - **Allow List (`remove allow`):** Pass **ONLY** the selected removal targets
       (plus any previously retained `Kept`/`Live` removal targets from `OptTargets`
       for that deal) to `remove allow` — **NEVER** union removal targets with the
       live allow list (which would remove the entire allow list!).
   - **Magnite DV+ Shared-List Isolation Rule (Create-Then-Swap):** Never edit a
     shared list used by other campaigns/clients and never attach a foreign campaign's list. Keep
     any baseline account blocklist intact and use a campaign-dedicated list named
     `<Client>_<CampaignCode>_Optimization_Block_List`. If `optimization_block_list_id`
     is `null` (no campaign-dedicated list exists yet), stage
     `magnite_create_targeting_list` first, persist the returned list ID to
     `CampaignConfig` once approved, and only then compile/stage the deal update
     that attaches the new list ID and detaches any foreign list (never detaching
     protection before the replacement list ID exists).
   - **Partition by Client Profile & Stage via Deal Planner:**
     In multi-campaign mode, group selected SSP rows by `client_id`
     first so each `compile_update_plan(client_id, changes)` call covers a single client profile:
     1. Load the client profile with `get_deal_profile(client_id)`.
     2. Run `compile_update_plan(client_id, changes)` → `*_preview_plan` →
        `finalize_plan(plan_id, rows=<all compiled item row numbers 1..N for the selected Workbench rows>)`
        (explicitly passing `rows` so `changes_active_deal` items are not deselected) →
        stage one `*_execute_plan` approval card per `(client, SSP, seat)`.
5. **Mandatory Automatic Post-Approval Verification Receipt (Next Turn After Approval):**
   - As each `*_execute_plan` card is approved, verify live SSP readback against
     `OptTargets` (every target present, prior `Kept`/`Live` count intact, no foreign
     lists attached, deal status `Active`) and print the **Post-Execution Verification Receipt**
     table (`Opt ID & Deal`, `SSP Deal ID + [Open in <SSP> ↗](<deal_url>)`,
     `Before → After Summary`, `VERIFIED / FLAGGED`).
   - Update `OptLog` (`Status = Live`, `Live = <today>`, `Notes`) using the
     **Read-Before-Write Merge Guardrail** (re-fetch latest CSV from storage,
     re-number any newly created `Opt ID` that collides with a concurrent writer's
     row before merging, and upload in place).

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
   - **NEVER include** internal campaign codes (`CMP00166`), internal Opt IDs (`NW-001`), raw
     SSP Deal IDs (`IX176...`), targeting list IDs, or API operator mechanics.
   - Anchor strictly on the selected primary KPI (`ROAS`, `CTR`, `CPA`, `VCR`) without
     secondary-metric clutter or formula footnotes. When a campaign has multiple
     `dsps[]` entries or channel-level KPI overrides (`channel_kpis`), let the trader
     pick a single DSP/channel KPI on the Story Builder card (`primary_kpi_scope`) or
     render separate per-KPI sections — never average or combine incompatible KPIs on
     one chart axis.
   - Render a **single-panel horizontal bar chart** (`figsize=(10.88, 4.2)`,
     `dpi=100`, `#C5C5DC` Actual vs `#7272AB` Post-Curation, dashed `#1A0B1E`
     benchmark line, legend above plot `bbox_to_anchor=(0.5, 1.16)`).
   - For **Spend Elasticity / Incremental Budget**, run all 4 tests in `run_python`
     (28d Daily Spend Quartiles & Correlation, Post-Curation Supply Headroom,
     High-Efficiency Channel Mix Headroom, and Safe Daily/Weekly Scaling Band with a
     direction-aware KPI Guardrail Bound derived from `kpi.primary.direction`: a
     **KPI Guardrail Floor** when `higher_is_better` and a **KPI Guardrail Ceiling**
     when `lower_is_better`).
   - Render via `mcp_ses_outbound_render_email` following `protocols/email-style.yaml`
     and stage both `preview_email` and `mcp_ses_outbound_send_email` (passing
     `attachments=[deal_sheet_xlsx_path]` when `attach_deal_sheet == true`).

---

## Flow 4 — Live Protocol Resolution & Self-Serve Protocol Updates

Traders can evolve the Optimization Log protocol and workflow **100% self-serve** without waiting on an engineering deploy:

1. **Live Protocol Check (`shared/Protocols/optimization-log-protocol.md` or Fast.io `/Protocols/optimization-log-protocol.md`):**
   - Check `shared/Protocols/optimization-log-protocol.md` (and `/Protocols/optimization-log-protocol.md` in Fast.io when configured).
   - If the workspace copy has a newer version/date header (e.g., `v3.1+`) or trader-added rules beyond the bundled `protocols/optimization-log-protocol.md`, apply those live rules automatically alongside the `show_ui` blueprints in `skills/campaign-optimization-log/REFERENCE.md`. Fall back to `protocols/optimization-log-protocol.md` if no workspace override exists or if the bundled copy is newer.
2. **Updating the Protocol Self-Serve in Chat (`"update the optimization protocol..."`):**
   - When a trader asks to update, refine, or add a rule to the protocol (e.g., `"update the optimization protocol: ..."`, `"change the optimization log protocol so..."`):
     1. Read `shared/Protocols/optimization-log-protocol.md` (or `/Protocols/optimization-log-protocol.md` in Fast.io if configured; if none exists yet or it is older than the bundled `protocols/optimization-log-protocol.md`, start from the bundled `v3.0` file and merge any trader-specific rules so nothing is lost).
     2. Apply the requested improvement, increment the version/date header (`# Optimization Log Protocol (v3.x, YYYY-MM-DD)`), and add a one-line entry under `## Changelog`.
     3. Save the updated file **in place** to `shared/Protocols/optimization-log-protocol.md` (or `/Protocols/optimization-log-protocol.md` in Fast.io via `mcp_fastio_helpers_upload_file` when configured, and update `optimization-trader-quickstart.md` if trader chat commands changed).
     4. If the change also alters skill-level instructions, call `propose_skill(name="campaign-optimization-log", ...)` (or remind the trader they can click **Clone & edit** on `campaign-optimization-log` in **Settings → Skills** and check **Share with this workspace**) so the skill override is shared across the workspace immediately.

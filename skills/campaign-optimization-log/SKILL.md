---
name: campaign-optimization-log
description: Onboard campaigns to the Optimization Log (`CampaignConfig`, `OptLog`, `OptTargets`), open the interactive Chat-First Optimization Workbench (`show_ui`), export or apply SSP/DSP deal updates with verification receipts, build client-facing optimization story emails or spend-elasticity budget pitches (`show_ui`), and make self-serve updates to the protocol. Use whenever a trader says "onboard <Client>", "set up a campaign config", "review <Client> opts", "review my opts", "open <Client> workbench", "show open opts", "execute <Opt ID>", "<Opt ID> is live", "keep/revert/drop <Opt ID>", "log this to the backlog", "craft a client email for <Client> opts", "can <Client> take incremental budget", "sync all campaign configs", or "update the optimization protocol".
---

# Campaign Optimization Log & Workbench (`show_ui`)

A flexible, partner-configurable campaign optimization workflow that pairs `protocols/optimization-log-protocol.md` and `protocols/CampaignConfig_TEMPLATE.yaml` with three interactive **`show_ui`** cards (`skills/campaign-optimization-log/REFERENCE.md`):

1. **Campaign Config & Onboarding Builder (`show_ui`, Blueprint 1)** — discovers delivery reports from any configured source (`mailbox`, `connected_feed`, or `uploaded_file`), runs **Day-1 DSP Report Preflight QA**, lets the user choose **where to store their data** (`storage_target`: `workspace_default` or `custom` by default, plus `connected_storage` when a writable cloud/shared storage connector is registered in `manifest.yaml`) and which SSPs/DSPs and thresholds to use, and initializes `CampaignConfig_<Client>_<Code>.yaml`, `OptLog_<Client>_<Code>.csv`, and `OptTargets_<Client>_<Code>.csv`.
2. **Chat-First Optimization Workbench (`show_ui`, Blueprint 2)** — opens on `"review <Client> opts"` or `"review my opts"` with pre-checked clean recommendations, unchecked `[SCALE WARNING]` items, inline target editing, 1-click status updates (`live` / `sent`), backlog promotion, and an inline question box.
3. **Client-Facing Optimization Story & Spend Elasticity Builder (`show_ui`, Blueprint 3)** — opens on `"craft a client email for <Client>"` or `"can <Client> take incremental budget?"` with Story Archetypes, modular sections, a brand-themed `1088x420` KPI lift chart (`protocols/email-style.yaml`), and the **4-Test Spend Elasticity** evaluator.

For one-off ad-hoc report analysis when no `CampaignConfig` is used, see `protocols/optimization.md`.

---

## Flow 1 — Guided Campaign Config Onboarding (`show_ui`)

1. **Auto-Discover & Run Day-1 DSP Report Preflight QA:**
   - Locate delivery reports from the workspace, attached files (`uploaded_file`), any connected cloud storage / S3 / BI / warehouse feed (`connected_feed`), or a configured report inbox (`mailbox`).
   - Inspect sample reports in `run_python` to identify the deal column (`deal_field`), active deals, date range, dimensional coverage (`overall`, `domain`/`app`, `geo`, `time_of_day`, `system`), and KPI columns. Flag any gaps in the card's `status_list` (computing dynamic counts from discovered data rather than hardcoding static numbers).
2. **Render Blueprint 1 (`skills/campaign-optimization-log/REFERENCE.md`):**
   - Pre-fill discovered deals, report sources (`report_source_type`: `mailbox` with `report_mailbox`, `connected_feed` with `report_feed_source` / `<dsp_id>_feed_source`, or `uploaded_file`), storage choice (`storage_target`: `workspace_default` and `custom`, plus `connected_storage` with `storage_backend` and `storage_custom_path` when `manifest.yaml` registers a writable storage connector), manual deal additions/overrides (`custom_deals_override`), KPI settings, and eligibility thresholds (`min_spend_share_pct`, `min_projected_kpi_lift_pct`, `min_impression_threshold`, `min_spend_usd`, `max_volume_cut_pct`).
3. **On Submission (`save_config` or `save_and_run`):**
   - Validate KPI guardrails: every active KPI requires a numeric benchmark unless `use_baseline_fallback == true` (which sets `kpi.primary.benchmark: null`); if a required target is missing and fallback is disabled, set `kpi.primary.status: "blocked"` (which blocks proposing new candidate rows while still scoring active `Live` rows). When `no_conversion_tracking: true`, never configure or compute `CPA` or `ROAS`.
   - Normalize percentage thresholds into decimal shares (`0.0–1.0`) and save `CampaignConfig_<Client>_<Code>.yaml`, `OptLog_<Client>_<Code>.csv` (10 columns), and `OptTargets_<Client>_<Code>.csv` (5 columns: `Opt ID,Lever,Value,Added,Source`) to the user's chosen storage location (indexing the path in `Optimizations/CampaignRegistry.yaml`).
   - If `save_and_run` was selected, immediately run the initial review and open the Workbench card.

---

## Flow 2 — The Chat-First Optimization Workbench (`show_ui`) & Execution

1. **Load Campaign Files:**
   - Check the current conversation thread / attached files first, then look up `Optimizations/CampaignRegistry.yaml` and load `CampaignConfig`, `OptLog`, and `OptTargets` from the campaign's configured storage location (or across all active campaigns matching `team.trader` / `team.digest_to` for `"review my opts"`, prefixing table `row_key`s and per-row control IDs with `<campaign_code>`).
2. **Render Blueprint 2 (`skills/campaign-optimization-log/REFERENCE.md`):**
   - Pre-select clean SSP recommendations; leave `[SCALE WARNING]` items unchecked by default. Provide inline `multi_select` target editors (`<opt_id>_domains`), status selectors (`<opt_id>_status_action`), and backlog promotion (`promote_backlog`).
3. **Handle Submissions:**
   - **`ask_questions` (Side-Effect Free):** Do not mutate `OptLog.csv` or `OptTargets.csv`. Answer `trader_question` in Python and re-render the card preserving current selections.
   - **`execute_selected` (`Apply Selected Updates`):**
     - Apply `*_status_action` updates (`live` takes precedence over `sent`), promote checked `promote_backlog` rows to `Proposed` (materializing their `OptTargets.csv` rows), and save inline target edits using the **Read-Before-Write Merge Guardrail**.
     - Compute the deal-level effective target union in memory (`Kept` + `Live` + selected `Proposed` block targets, while keeping `OptTargets.csv` rows scoped per `Opt ID`; for allow-list removals, pass only the removal targets).
     - Respect the **Shared Targeting-List Isolation Rule (Create-Then-Swap)** strictly on block-list deals on SSPs that share targeting lists across deals (`<Client>_<CampaignCode>_Optimization_Block_List`).
     - Follow the workspace's execution branch in `protocols/optimization-log-protocol.md`:
       - **Case A (No SSP write connector in `manifest.yaml`):** Output a copy-paste-ready **Target List & Operator Execution Summary** and **Post-Execution Verification Receipt** table in chat.
       - **Case B (`deal_plan` + SSP connector in `manifest.yaml`):** Pre-flight read the live deal, merge cumulative targets, stage via `deal_plan`, and verify via a **Post-Execution Verification Receipt** before setting `Status = Live`.
       - **Case C (Direct SSP write connector without `deal_plan`):** Pre-flight read the live deal, merge cumulative targets, stage the connector write call, and verify via a **Post-Execution Verification Receipt** before setting `Status = Live`.

---

## Flow 3 — Client-Facing Optimization Story & 4-Test Spend Elasticity (`show_ui`)

1. **Render Blueprint 3 (`skills/campaign-optimization-log/REFERENCE.md`)**:
   - Let the user pick the Story Archetype (`just_executed`, `proven_lift`, `expansion_pitch`), modular sections, `primary_kpi_scope`, `domain_granularity`, and optional client-safe `.xlsx` summary (`attach_kpi_summary_xlsx`).
2. **Generate Output (`generate_story_email`):**
   - Strip all internal campaign codes, `Opt ID`s, raw SSP deal IDs, and targeting-list IDs, and include only `Live` and `Kept` wins.
   - Generate the `1088x420` horizontal bar chart using `protocols/email-style.yaml` (`chart_colors`: `secondary_series`, `primary_series`, `text`, `background`, `grid`).
   - Run the **4-Test Spend Elasticity** analysis in Python when requested or when `expansion_pitch` is selected (28d Spend vs. KPI quartiles, Post-Curation Supply Headroom, Channel/Deal Mix Headroom, and Safe Scaling Band with direction-aware KPI Guardrail Floor/Ceiling).
   - When `ses_outbound` (or another outbound email connector) is registered and enabled in `manifest.yaml`, stage/send the email with the chart PNG and client-safe `.xlsx`; otherwise deliver the rendered HTML, chart PNG, and client-safe `.xlsx` directly in chat.

---

## Flow 4 — Config Sync & Self-Serve Protocol Customization

1. **Sync All Campaign Configs (`"sync all campaign configs"`):**
   - Iterate all active campaigns in `Optimizations/CampaignRegistry.yaml` (or a single campaign), re-inspect the latest DSP delivery reports across each campaign's configured report sources, and report any newly active deals, missing report feeds, or schema changes without overwriting trader-customized thresholds.
2. **Workspace Protocol Override (`Optimizations/Protocols/optimization-log-protocol.md`):**
   - Check `Optimizations/Protocols/optimization-log-protocol.md` first and apply any newer workspace rules alongside `protocols/optimization-log-protocol.md`.
   - When a user says `"update the optimization protocol: <change>"`, update `Optimizations/Protocols/optimization-log-protocol.md` in place and increment its version header.
3. **UI Clone & Edit:**
   - Users can also customize `campaign-optimization-log` in **Settings → Skills → Clone & edit** (or any starter in the **Prompt Library**) and check **Share with this workspace**.

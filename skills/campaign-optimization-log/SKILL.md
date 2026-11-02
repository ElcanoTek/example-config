---
name: campaign-optimization-log
description: Onboard campaigns to the Optimization Log (`CampaignConfig`, `OptLog`, `OptTargets`), open the interactive Chat-First Optimization Workbench (`show_ui`), export or apply SSP/DSP deal updates with verification receipts, build client-facing optimization story emails or spend-elasticity budget pitches (`show_ui`), and make self-serve updates to the protocol. Use whenever a trader says "onboard <Client>", "set up a campaign config", "review <Client> opts", "review my opts", "open <Client> workbench", "show open opts", "execute <Opt ID>", "<Opt ID> is live", "keep/revert/drop <Opt ID>", "log this to the backlog", "craft a client email for <Client> opts", "can <Client> take incremental budget", "sync all campaign configs", or "update the optimization protocol".
---

# Campaign Optimization Log & Workbench (`show_ui`)

A flexible, partner-configurable campaign optimization workflow that pairs `protocols/optimization-log-protocol.md` and `protocols/CampaignConfig_TEMPLATE.yaml` with three interactive **`show_ui`** cards (`skills/campaign-optimization-log/REFERENCE.md`):

1. **Campaign Config & Onboarding Builder (`show_ui`, Blueprint 1)** — discovers delivery reports from any configured source (`mailbox`, `connected_feed`, or `uploaded_file`), runs **Day-1 DSP Report Preflight QA**, lets the user choose **where to store their data** (`storage_target`: `workspace_default`, `connected_storage`, or `custom`) and which SSPs/DSPs and thresholds to use, and initializes `CampaignConfig_<Client>_<Code>.yaml`, `OptLog_<Client>_<Code>.csv`, and `OptTargets_<Client>_<Code>.csv`.
2. **Chat-First Optimization Workbench (`show_ui`, Blueprint 2)** — opens on `"review <Client> opts"` or `"review my opts"` with pre-checked clean recommendations, unchecked `[SCALE WARNING]` items, inline target editing, 1-click status updates (`live` / `sent`), backlog promotion, and an inline question box.
3. **Client-Facing Optimization Story & Spend Elasticity Builder (`show_ui`, Blueprint 3)** — opens on `"craft a client email for <Client>"` or `"can <Client> take incremental budget?"` with Story Archetypes, modular sections, a brand-themed `1088x420` KPI lift chart (`protocols/email-style.yaml`), and the **4-Test Spend Elasticity** evaluator.

For one-off ad-hoc report analysis when no `CampaignConfig` is used, see `protocols/optimization.md`.

---

## Flow 1 — Guided Campaign Config Onboarding (`show_ui`)

1. **Auto-Discover & Run Day-1 DSP Report Preflight QA:**
   - Locate delivery reports from the workspace, attached files (`uploaded_file`), any connected cloud storage / S3 / BI / warehouse feed (`connected_feed`), or a configured report inbox (`mailbox`).
   - Inspect sample reports in `run_python` to identify the deal column (`deal_field`), active deals, date range, dimensional coverage (`overall`, `domain`/`app`, `geo`, `time_of_day`, `system`), and KPI columns. Flag any gaps in the card's `status_list`.
2. **Render Blueprint 1 (`skills/campaign-optimization-log/REFERENCE.md`):**
   - Pre-fill discovered deals, report sources (`report_source_type`: `mailbox`, `connected_feed`, `uploaded_file`), storage choice (`storage_target`: `workspace_default`, `connected_storage`, `custom`), KPI settings, and eligibility thresholds (`min_spend_share_pct`, `min_projected_kpi_lift_pct`, `min_impression_threshold`, `min_spend_usd`, `max_volume_cut_pct`).
3. **On Submission (`save_config` or `save_and_run`):**
   - Normalize percentage thresholds into decimal shares (`0.0–1.0`), set `kpi.primary.benchmark: null` when `use_baseline_fallback == true`, and save `CampaignConfig_<Client>_<Code>.yaml`, `OptLog_<Client>_<Code>.csv` (10 columns), and `OptTargets_<Client>_<Code>.csv` (5 columns: `Opt ID,Lever,Value,Added,Source`) to the user's chosen storage location (indexing the path in `Optimizations/CampaignRegistry.yaml`).
   - If `save_and_run` was selected, immediately run the initial review and open the Workbench card.

---

## Flow 2 — The Chat-First Optimization Workbench (`show_ui`) & Execution

1. **Load Campaign Files:**
   - Load `CampaignConfig`, `OptLog`, and `OptTargets` from the campaign's configured storage location (or across all active campaigns for `"review my opts"`, prefixing table `row_key`s and per-row control IDs with `<campaign_code>`).
2. **Render Blueprint 2 (`skills/campaign-optimization-log/REFERENCE.md`):**
   - Pre-select clean SSP recommendations; leave `[SCALE WARNING]` items unchecked by default. Provide inline `multi_select` target editors (`<opt_id>_domains`), status selectors (`<opt_id>_status_action`), and backlog promotion (`promote_backlog`).
3. **Handle Submissions:**
   - **`ask_questions` (Side-Effect Free):** Do not mutate `OptLog.csv` or `OptTargets.csv`. Answer `trader_question` in Python and re-render the card preserving current selections.
   - **`execute_selected` (`Apply Selected Updates`):**
     - Apply `*_status_action` updates (`live` takes precedence over `sent`), promote checked `promote_backlog` rows to `Proposed` (materializing their `OptTargets.csv` rows), and save inline target edits using the **Read-Before-Write Merge Guardrail**.
     - Compute the deal-level effective target union in memory (`Kept` + `Live` + selected `Proposed` block targets, while keeping `OptTargets.csv` rows scoped per `Opt ID`).
     - Respect the **Shared Targeting-List Isolation Rule (Create-Then-Swap)** on any SSP that uses shared targeting lists across deals (`<Client>_<CampaignCode>_Optimization_Block_List`).
     - Output a copy-paste-ready **Target List & Operator Execution Summary** and **Post-Execution Verification Receipt** (or stage updates via any connected SSP/deal write tools configured in the workspace).

---

## Flow 3 — Client-Facing Optimization Story & 4-Test Spend Elasticity (`show_ui`)

1. **Render Blueprint 3 (`skills/campaign-optimization-log/REFERENCE.md`)**:
   - Let the user pick the Story Archetype (`just_executed`, `proven_lift`, `expansion_pitch`), modular sections, `primary_kpi_scope`, `domain_granularity`, and optional client-safe `.xlsx` summary (`attach_kpi_summary_xlsx`).
2. **Generate Output (`generate_story_email`):**
   - Strip all internal campaign codes, `Opt ID`s, raw SSP deal IDs, and targeting-list IDs.
   - Generate the `1088x420` horizontal bar chart using `protocols/email-style.yaml` (`chart_colors`: `secondary_series`, `primary_series`, `text`, `background`, `grid`).
   - Run the **4-Test Spend Elasticity** analysis in Python when requested or when `expansion_pitch` is selected (28d Spend vs. KPI quartiles, Post-Curation Supply Headroom, Channel/Deal Mix Headroom, and Safe Scaling Band with direction-aware KPI Guardrail Floor/Ceiling).
   - Render and stage via the workspace's configured outbound email tool, or deliver the rendered HTML, chart PNG, and client-safe `.xlsx` directly in chat.

---

## Flow 4 — Self-Serve Protocol & Skill Customization

1. **Workspace Protocol Override (`Optimizations/Protocols/optimization-log-protocol.md`):**
   - Check `Optimizations/Protocols/optimization-log-protocol.md` first and apply any newer workspace rules alongside `protocols/optimization-log-protocol.md`.
   - When a user says `"update the optimization protocol: <change>"`, update `Optimizations/Protocols/optimization-log-protocol.md` in place and increment its version header.
2. **UI Clone & Edit:**
   - Users can also customize `campaign-optimization-log` in **Settings → Skills → Clone & edit** (or any starter in the **Prompt Library**) and check **Share with this workspace**.

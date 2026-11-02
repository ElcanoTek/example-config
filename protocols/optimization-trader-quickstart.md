# Optimization Log & Workbench — Trader Quick-Start (v3.0)

Everything you need to onboard any campaign with an interactive **`show_ui` Campaign Config Builder**, review and manage optimizations from the **Optimization Workbench card**, and craft **Client-Facing Optimization Story & Budget Elasticity Emails** in one click.

---

## 1. Setting Up a New Campaign (`show_ui` Config Builder + Day-1 Preflight QA)
You never need to write or edit a YAML file by hand. Once DSP reports are flowing to the report inbox (`mcp_explorer_*`) or attached in chat, open Fleet Chat (or pick **"Onboard campaign to Optimization Log"** from the Prompt Library book icon) and say:

> **"Onboard `<Client Name>` (`<CampaignCode>`) to the optimization log"**
> *(Optional details you can include upfront: Trader owner, Flight dates, Primary KPI & Benchmark, or eligibility thresholds.)*

**What happens during onboarding:**
1. **Auto-Discovers Reports & Deals:** Finds your DSP reports in the report inbox (`mcp_explorer_*`) or attached files, inspects the columns in Python, and maps active deals.
2. **Runs Day-1 Preflight QA:** Checks whether all 5 dimensional report types (`overall`, `domain`, `geo`, `time_of_day`, `system`) and KPI conversion/revenue columns are present, flagging any missing report or truncated filename on Day 1.
3. **Opens the Interactive `show_ui` Campaign Config Builder Card:**
   - **Tab 1 — Campaign, Team & Save Location:** Confirm flight dates, optimization/light days, and the workspace folder (`Optimizations/<Client> (<CampaignCode>)/`).
   - **Tab 2 — KPI, Benchmarks & Eligibility Thresholds:** Confirm your KPI benchmark (or toggle *"compute initial baseline from report"*), per-channel overrides (e.g. Display CTR vs. OLV VCR), trader-confirmed eligibility thresholds (`min_spend_share_pct`, `min_impression_threshold`, `min_projected_kpi_lift_pct`, and `max_volume_cut_pct`), and allowed SSP/DSP levers.
   - **Tab 3 — Active Deals & Report Sources:** Confirm the mapped SSP deals and report sources (`mailbox` via Explorer or `uploaded_file`).
4. Click **Save Config & Initialize Log** (or **Save Config & Run First Optimization Review Now**) to create `CampaignConfig_<Client>_<Code>.yaml`, `OptLog_<Client>_<Code>.csv`, and `OptTargets_<Client>_<Code>.csv` in the campaign's `Optimizations/` folder.
5. **Schedule Your Weekday Review Runner (One-Time Setup per Trader):**
   - To enable automatic weekday scans and notification emails, click **"Make recurring task…"** on the thread (or create a task in Operations Center) set to weekdays at `15:00 UTC` (`0 15 * * 1-5`) scoped to your `team.digest_to` email address. Because `mcp_ses_outbound_send_email` sends at most one email per task run, create one weekday runner task per `team.digest_to` trader recipient.

---

## 2. Reviewing & Applying Optimizations (`show_ui` Optimization Workbench)
1. **Scheduled Optimization Day Reviews (Once Scheduled via "Make recurring task…"):**
   - On each campaign's configured `optimization_days` and `light_days`, your scheduled task evaluates active campaigns for your `team.digest_to` address and sends a short **"Opts Ready in Chat"** notification email when recommendations or alerts are ready.
2. **Open the Optimization Workbench in Fleet Chat:**
   - Type **`review <Client> opts`** (for one campaign) or **`review my opts`** (for a unified Workbench across all active campaigns in your workspace).
   - The interactive **Optimization Workbench card (`show_ui`)** opens with three tabs:
     - **Tab 1 — Proposed SSP & Deal Optimizations:** Clean recommendations are pre-checked; `[SCALE WARNING]` items (exceeding your configured `max_deal_volume_cut_share`) are unchecked by default so you consciously opt in. You can also **check/uncheck individual target domains inline** and set per-row status actions (`No status change`, `Sent to client`, or `Mark Live`).
     - **Tab 2 — Console & Client DSP Requests:** Items requiring an SSP console toggle or client DSP shift, with 1-click controls to record `Sent to client` or `Mark Live`.
     - **Tab 3 — Backlog & Signals:** Queued candidates you can check to promote into `Proposed` (automatically materializing their `OptTargets.csv` rows).
3. **Ask Questions First OR Apply Updates Immediately:**
   - **Want to gut-check something first?** Type a question in the card's **"Ask a Question or Validate Before Executing"** box (e.g., *"How did example.com perform over 20 days?"*) and click **Ask / Validate First**. The agent answers with exact numbers and re-renders the card (resetting any already-applied status selectors back to `"none"`).
   - **Ready to apply changes?** Click **Apply Selected Updates**:
     - Across all deployments, the agent persists your status updates (`Live` / `Sent to client`), saves your edited `OptTargets.csv` target lists (unioned with prior `Kept`/`Live` targets), and outputs copy-paste-ready target lists and operator instructions.
     - In deployments where `manifest.yaml` registers Deal Planner (`deal_plan`) and SSP seat connectors, the agent also routes selected SSP API updates through the Deal Planner recommendation/update flow behind its approval card, then verifies the live deal and updates `OptLog.csv`.

---

## 3. Client-Facing Optimization Story Emails & Budget Elasticity (`show_ui` Story Builder)
Want to show a client or partner the impact of your curation work — or pitch incremental budget?
- Type **`craft a client email for <Client>`** (or pick **"Client-facing optimization story & budget pitch"** from the Prompt Library).
- The **`show_ui` Client Story Builder Card** opens so you can:
  - Pick a **Story Archetype**: (1) *"Just Executed" Proactive Curation Showcase*, (2) *"Proven Lift" Before-vs-After Readout*, or (3) *Strategic Allocation & Incremental Budget Pitch*.
  - Toggle **Modular Email Sections** (2-Sentence Executive Summary, KPI Cards, Single-Panel `1088x420` Deal Lift Chart rendered in your brand's `protocols/email-style.yaml` `chart_colors` palette, Deal Table, 4-Column "Optimizations Implemented" Domain Table with Full/Representative/Summary granularity, Phase 2 Roadmap, and the **4-Test Spend Elasticity & Incremental Budget Pitch**).
  - Optionally attach a **client-safe KPI & channel summary `.xlsx`** (containing aggregated KPI metrics with zero raw SSP deal IDs or targeting-list IDs).
  - Click **Generate & Stage Email** to render the branded HTML email via `mcp_ses_outbound_render_email` and stage `mcp_ses_outbound_send_email` with the rendered `content_file` and `inline_attachments` behind the approval gate.

---

## 4. Quick Chat Commands Reference

| What you want to do | What to type in Fleet Chat |
|---|---|
| **Onboard a new campaign (`show_ui` Config Builder)** | `"onboard DemandCo CMP00166 to the optimization log"` |
| **Open the Workbench card (1 campaign)** | `"review DemandCo opts"` (or `"open DemandCo workbench"`) |
| **Open a unified Workbench across active campaigns** | `"review my opts"` (or `"open my workbench"`) |
| **Craft a client-facing optimization story email (`show_ui`)** | `"craft a client email for DemandCo"` |
| **Evaluate if a campaign can absorb incremental budget** | `"can DemandCo take incremental budget?"` |
| **Apply or export specific Opt IDs directly** | `"execute ACM-018.1, ACM-019.1"` |
| **Confirm a manual/console change is live** | `"ACM-020.1 is live"` (or select `Mark Live` in the Workbench card) |
| **Keep or revert a live change early** | `"keep ACM-011.1"` or `"revert ACM-011.2"` |
| **Skip/dismiss a recommendation** | `"drop ACM-012, client paused deal"` |
| **Save an ad-hoc finding while exploring** | `"log this to the backlog"` |
| **Upgrade all configs to the latest schema** | `"sync all campaign configs"` |
| **Hand off coverage for PTO** | `"cover Alex's campaigns for Jordan until 10/25"` |

---

## 5. Customizing Skills & Prompts in Your Workspace

1. **Customize & Share the `/campaign-optimization-log` Skill in `Settings → Skills`:**
   - Open **Settings → Skills**, find **`campaign-optimization-log`** under **Workspace pack**, and click **Clone & edit**.
   - Edit the instructions, check **Share with this workspace**, and click **Create skill** (or **Save changes**). Your override takes precedence immediately for your runs and appears in the Workspace pack for teammates to adopt or sync in one click.
2. **Clone & Edit Any Prompt Library Starter (Book Icon):**
   - Click the **Prompt library** book icon in Chat or Tasks, select any Optimization workflow, click **Clone & edit**, tweak the prompt, and check **Share with this workspace**.

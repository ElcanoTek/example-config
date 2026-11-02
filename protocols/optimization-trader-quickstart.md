# Optimization Log & Workbench — Trader Quick-Start (v3.0)

Everything you need to onboard any campaign with an interactive **`show_ui` Campaign Config Builder**, review and stage SSP optimizations from the **Optimization Workbench card**, and craft **Client-Facing Optimization Story & Budget Elasticity Emails** in one click.

---

## 1. Setting Up a New Campaign (`show_ui` Config Builder + Day-1 Preflight QA)
You never need to write or edit a YAML file by hand. Once DSP reports are flowing to the report inbox or workspace storage, open Fleet Chat (or pick **"Onboard campaign to Optimization Log"** from the Prompt Library book icon) and say:

> **"Onboard `<Client Name>` (`<CampaignCode>`) to the optimization log"**
> *(Optional details you can include upfront: Trader owner, Flight dates, Primary KPI & Benchmark, or where you want the files saved.)*

**What happens automatically:**
1. **Auto-Discovers Reports & Deals:** Finds your DSP reports in the report inbox or workspace storage, inspects the columns in Python, and looks up your active deals and SSP seats.
2. **Runs Day-1 Preflight QA:** Checks whether all 5 dimensional report types (`overall`, `domain`, `geo`, `time_of_day`, `system`) and KPI conversion/revenue columns are present, flagging any missing report or truncated filename on Day 1.
3. **Opens the Interactive `show_ui` Campaign Config Builder Card:**
   - **Tab 1 — Campaign, Team & Save Location:** Confirm flight dates, optimization/light days, and **where to save** (`shared/Optimizations/`, Fast.io `/Optimizations/` when configured, a dedicated `<Client>_<CampaignCode>/` folder, or a custom path).
   - **Tab 2 — KPI, Benchmarks & Levers:** Set or confirm your KPI benchmark (or toggle *"compute initial baseline from report"*), per-channel overrides (e.g. Display CTR vs. OLV VCR), and allowed SSP/DSP levers.
   - **Tab 3 — Active Deals & Report Sources:** Confirm the mapped SSP deals and report sources.
4. Click **Save Config & Initialize Log** (or **Save Config & Run First Optimization Review Now**) to create `CampaignConfig_<Client>_<Code>.yaml`, `OptLog_<Client>_<Code>.csv`, and `OptTargets_<Client>_<Code>.csv` automatically.

---

## 2. Reviewing & Executing Optimizations (`show_ui` Optimization Workbench)
1. **Your Scheduled Optimization Day Runs Automatically (15:00 UTC):**
   - Each campaign routes to the trader email configured in `team.digest_to` on its scheduled `optimization_days` and `light_days`.
2. **You Receive a Short "Opts Ready in Chat" Email:**
   - A 15-second snapshot showing the KPI headline, how many recommendations are ready, and the 1-line prompt: **`review <Client> opts`**.
3. **Open the Optimization Workbench in Fleet Chat:**
   - Type **`review <Client> opts`** (for one campaign) or **`review my opts`** (for a unified Workbench across all your active campaigns).
   - The interactive **Optimization Workbench card (`show_ui`)** opens with three tabs:
     - **Tab 1 — Chat-Executable SSP Opts:** Clean `[Chat API]` changes are pre-checked; `[SCALE WARNING]` items (>40% deal volume cut) are unchecked by default so you consciously opt in. You can also **check/uncheck individual target domains inline** before executing.
     - **Tab 2 — Console & Client Requests:** Items requiring an SSP console toggle or client DSP shift, with 1-click controls to mark them `Live`.
     - **Tab 3 — Backlog & Signals:** Queued candidates you can check to promote early.
4. **Ask Questions First OR Execute Immediately:**
   - **Want to gut-check something first?** Type a question in the card's **"Ask a Question or Validate Before Executing"** box (e.g., *"How did weather.com perform over 20 days?"*) and click **Ask / Validate First**. The agent answers with exact numbers and refreshes the card.
   - **Ready to push to the SSPs?** Click **Stage SSP Updates for Selected**. The agent automatically unions your new target list with prior `Kept`/`Live` lists on that deal (and isolates Magnite DV+ block lists per campaign) and stages the Deal Planner SSP approval card(s).
5. **Automatic Post-Approval Verification Receipt:**
   - As soon as you click **Approve** on the SSP card(s), the agent automatically verifies the live deal on the SSP against `OptTargets`, prints a **Post-Execution Verification Receipt** with `Before → After` counts and direct **`Open in <SSP> ↗`** links, and flips the verified rows to `Live` in `OptLog`.

---

## 3. Client-Facing Optimization Story Emails & Budget Elasticity (`show_ui` Story Builder)
Want to show a client or partner the impact of your curation work — or pitch incremental budget?
- Type **`craft a client email for <Client>`** (or pick **"Client-facing optimization story & budget pitch"** from the Prompt Library).
- The **`show_ui` Client Story Builder Card** opens so you can:
  - Pick a **Story Archetype**: (1) *"Just Executed" Proactive Curation Showcase*, (2) *"Proven Lift" Before-vs-After Readout*, or (3) *Strategic Allocation & Incremental Budget Pitch*.
  - Toggle **Modular Email Sections** (2-Sentence Executive Summary, KPI Cards, Single-Panel `1088x420` Deal Lift Chart, Deal Table, 4-Column "Optimizations Implemented" Domain Table with Full/Representative/Summary granularity, Phase 2 Roadmap, and the **4-Test Spend Elasticity & Incremental Budget Pitch**).
  - Click **Generate Email** to render a zero-jargon, branded HTML email (no internal campaign codes, no internal Opt IDs, no raw SSP Deal IDs) and stage both the preview and send cards.

---

## 4. Quick Chat Commands Reference

| What you want to do | What to type in Fleet Chat |
|---|---|
| **Onboard a new campaign (`show_ui` Config Builder)** | `"onboard <Client> <CampaignCode> to the optimization log"` |
| **Open the Workbench card (1 campaign)** | `"review <Client> opts"` (or `"open <Client> workbench"`) |
| **Open a unified Workbench across ALL your campaigns** | `"review my opts"` (or `"open my workbench"`) |
| **Craft a client-facing optimization story email (`show_ui`)** | `"craft a client email for <Client>"` |
| **Evaluate if a campaign can absorb incremental budget** | `"can <Client> take incremental budget?"` |
| **Execute specific Opt IDs directly** | `"execute NWO-018.1, NWO-019.1"` (or `"execute all clean"`) |
| **Confirm a manual/console change is live** | `"NWO-020.1 is live"` (or select it in Tab 2 of the Workbench card) |
| **Keep or revert a live change early** | `"keep NWO-011.1"` or `"revert NWO-011.2"` |
| **Skip/dismiss a recommendation** | `"drop NWO-012, client paused deal"` |
| **Save an ad-hoc finding while exploring** | `"log this to the backlog"` |
| **Upgrade all configs to the latest schema** | `"sync all campaign configs"` |
| **Update the protocol self-serve in workspace storage** | `"update the optimization protocol: <what to change>"` |
| **Hand off coverage for PTO** | `"cover Alex's campaigns for Jordan until 10/25"` |

---

## 5. Making Self-Serve Updates to the Protocol, Skill, or Prompts

You never need to wait on an engineering PR to refine a rule, threshold, or prompt:

1. **Update the Protocol via Chat (Updates Both Scheduled Runs & Chat Immediately):**
   - Just say in Fleet Chat: **`"update the optimization protocol: <describe your change>"`** (or upload/paste an updated section).
   - The agent updates `shared/Protocols/optimization-log-protocol.md` (or `/Protocols/optimization-log-protocol.md` in Fast.io when configured) in place and bumps the version header. Both the weekday scheduled runner and `/campaign-optimization-log` in chat read the live workspace protocol automatically.
2. **Customize & Share the `/campaign-optimization-log` Skill in `Settings → Skills`:**
   - Open **Settings → Skills**, find **`campaign-optimization-log`** under **Workspace pack**, and click **Clone & edit**.
   - Edit the instructions, check **Share with this workspace**, and click **Create skill** (or **Save changes**). Your override takes precedence immediately for your runs and appears in the Workspace pack for teammates to adopt or sync in one click.
3. **Clone & Edit Any Prompt Library Starter (Book Icon):**
   - Click the **Prompt library** book icon in Chat or Tasks, select any Optimization workflow, click **Clone & edit**, tweak the prompt, and check **Share with this workspace**.

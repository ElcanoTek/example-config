# Optimization Protocol

Analyze recent campaign delivery reports (from a report inbox, connected storage/data feed, or uploaded files) and produce actionable optimization recommendations across the campaign's SSPs and DSPs.

## Usage

```
Run the optimization protocol for '<source>' and send recommendations to '<recipient>'
```

## Inputs

| Parameter | Source | Description |
|-----------|--------|-------------|
| source | User task text | The report mailbox, connected storage/data feed, or attached report files to analyze |
| recipient | User task text | Where to send or deliver the optimization recommendations (optional for in-chat reviews) |
| lookback_days | Default: 14 | How far back to look for report data |

## Step 1 — Retrieve recent reports

Retrieve the campaign's recent delivery reports from whichever source is configured in the workspace:

- **Report email inbox** (when an inbox connector is configured): search for recent messages matching the mailbox/filter over `lookback_days` and download all CSV/XLSX report payloads (attachments or body download links) into the working directory. If the source is a report email inbox (`mailbox`) and fewer than 2 qualifying report emails are found in the window, flag the limited trend window in the report status.
- **Connected storage / data feed** (when a cloud storage, S3 feed, BI, or warehouse connector is configured): query or download the latest report snapshots covering `lookback_days` into the working directory.
- **Uploaded / workspace files**: read the CSV/XLSX/Parquet files attached in chat or placed in the workspace.

Confirm each file exists on disk before loading it, and record which reports were found and their date coverage. If no usable reports are available, report the coverage gap rather than inventing findings.

## Step 2 — Load, filter to `lookback_days`, and explore the data

- Load the report files into pandas, parse the report date column, **filter rows across all sources (`mailbox`, `connected_feed`, and `uploaded_file`) strictly to the trailing `lookback_days` window**, and deduplicate overlapping snapshots before aggregating.
- Identify available columns and metrics (spend, impressions, clicks, conversions, revenue, viewability, video completions, etc.).
- Derive channel (`CTV`, `OLV`, `Display`, `Native`, or `Other`) consistently from deal/campaign/placement/creative tokens using precedence: **CTV first, then OLV, then Native, then Display, else Other**.
- Use ratio-of-sums for all derived metrics:
  - CTR = `100.0 * SUM(clicks) / NULLIF(SUM(impressions), 0)`
  - VCR = `100.0 * SUM(video_completions) / NULLIF(SUM(video_starts), 0)`
  - CPA = `SUM(spend) / NULLIF(SUM(conversions), 0)`
  - CPC = `SUM(spend) / NULLIF(SUM(clicks), 0)`
  - ROAS = `SUM(revenue) / NULLIF(SUM(spend), 0)`

## Step 3 — Analyze dimensions

Explore every dimension supported by the data:
- **Inventory** (domains, apps, publishers, placements)
- **Temporal** (day of week, hour of day, date trends)
- **Geographic** (DMA, state, region)
- **Device / technical** (device type, OS, browser)
- **Supply path** (SSP, exchange, deal ID)
- **Creative** (format, size, creative ID)

Identify high-efficiency segments, low-efficiency spend drains, hidden gems, and concentration risks (>50% of spend in one bucket).

## Step 4 — Build recommendations

- **When the campaign KPI/goal is known:** optimize directly against that KPI and quantify expected lift.
- **When the KPI/goal is not known:** frame recommendations conditionally across awareness/reach (CPM, viewability), engagement (CTR, VCR), and conversions/performance (CPA, ROAS).
- **Classify SSP-side vs. DSP-side levers clearly:**
  - **SSP-side (supply shaping):** domain/app Block Lists and Allow Lists, geo/device/OS/format filters, viewability thresholds, and deal floors (applied in the SSP console or via any connected SSP tool configured in the workspace). Note that deal-level changes affect all buyers on that deal.
  - **DSP-side (spend shaping):** bid adjustments, line-item or insertion-order budget shifts, frequency caps, creative rotation, and pacing controls (applied by the buyer/trader in their DSP).
- Enforce scientific rigor: flag small samples (<1,000 impressions or <50 clicks), compare against the campaign/channel baseline, and provide within-channel alternatives alongside any cross-channel reallocation suggestion.

## Step 5 — Deliver the report

- **When `recipient` is provided:** if an outbound email connector (such as `ses_outbound`) is registered and enabled in `manifest.yaml`, send the formatted HTML optimization report (`protocols/email-style.yaml`) and CSV summary attachment to `recipient`; otherwise output the rendered email and CSV in chat for forwarding.
- **When `recipient` is omitted (in-chat review):** present the structured optimization report and CSV summary directly in chat.

Include in the report:
1. **Status** (`COMPLETE` or `COMPLETE_WITH_FLAGS`)
2. **Data Coverage** (date range, sources, and row counts per channel)
3. **Topline Metrics** (broken out by channel + total)
4. **Key Findings** (3–5 executive bullets)
5. **Recommendations** (prioritized table with quantified impact and execution path)
6. **Supporting Detail & Quality Flags**

When the user wants ongoing tracking or interactive target-list editing, use the `campaign-optimization-log` skill (`skills/campaign-optimization-log/SKILL.md` and `protocols/optimization-log-protocol.md`).

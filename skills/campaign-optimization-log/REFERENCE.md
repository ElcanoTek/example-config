# Campaign Optimization Log — `show_ui` Card Blueprints & Reference

This reference accompanies `skills/campaign-optimization-log/SKILL.md` and
`protocols/optimization-log-protocol.md` (v3.0). Each `show_ui` blueprint below is
validated against Fleet's `genui` specification (`mcp/tests/genui_lite.py`). Adapt
the dynamic values (client name, campaign code, discovered deals, open `OptLog` rows,
and `OptTargets` values) to the campaign in front of you.

---

## Blueprint 1 — Campaign Config & Onboarding Builder Card (`show_ui`)

Present this card after running **Step 2.1 (Auto-Discovery & Day-1 DSP Report
Preflight QA)** so every field is pre-filled from the discovered reports and
SSP deals, and any Day-1 report gaps appear right at the top in the
`status_list`.

**Normalization rule when saving `CampaignConfig.yaml` from Blueprint 1:**
- Convert percentage controls to decimal shares (`0.0–1.0`) before writing `CampaignConfig.yaml`:
  - `cycle.materiality.min_addressable_spend_share = min_spend_share_pct / 100.0` (e.g. `10` → `0.10`)
  - `cycle.materiality.min_projected_kpi_change = min_projected_kpi_lift_pct / 100.0` (e.g. `5` → `0.05`)
  - `cycle.max_deal_volume_cut_share = max_volume_cut_pct / 100.0` (e.g. `40` → `0.40`)
- Write `cycle.min_sample.impressions = int(min_impression_threshold)`.
- Persist `campaign.deal_plan_client_id` with the bundle's Deal Planner profile key when `deal_plan` is registered in `manifest.yaml` (e.g. `"reklaim"`), or `null` when Deal Planner is not registered in this bundle.

```json
{
  "title": "Campaign Optimization Setup — DemandCo (CMP00199)",
  "description": "Day-1 DSP Report Preflight QA & CampaignConfig Builder",
  "components": [
    {
      "type": "status_list",
      "items": [
        {
          "label": "DSP Report Source (demandco.reports@example.com)",
          "status": "pass",
          "detail": "Latest drop covers 2026-10-15 to 2026-10-21 ($4,210.50 spend, 1.32M imps)"
        },
        {
          "label": "Dimensional Report Coverage (5 of 5 report types)",
          "status": "pass",
          "detail": "overall, domain, geo, time_of_day, and system reports verified"
        },
        {
          "label": "Primary KPI Formula & Columns (ROAS)",
          "status": "pass",
          "detail": "CM360 Post-View + Post-Click Revenue populated across all 5 report types"
        },
        {
          "label": "Active SSP Deals Linked ({{ count(active_deals) }} deals selected)",
          "status": "pass",
          "detail": "Primary seat: default (Index Exchange + Magnite DV+)"
        }
      ]
    },
    {
      "type": "tabs",
      "tabs": [
        {
          "label": "1. Campaign, Team & Save Location",
          "children": [
            {
              "type": "columns",
              "children": [
                {
                  "type": "text_input",
                  "id": "client_name",
                  "label": "Client display name",
                  "value": "DemandCo",
                  "required": true
                },
                {
                  "type": "text_input",
                  "id": "campaign_code",
                  "label": "Campaign code",
                  "value": "CMP00199",
                  "required": true
                },
                {
                  "type": "text_input",
                  "id": "opt_id_prefix",
                  "label": "Opt ID prefix (2–4 letters)",
                  "value": "DCO",
                  "required": true
                }
              ]
            },
            {
              "type": "columns",
              "children": [
                {
                  "type": "date",
                  "id": "start_date",
                  "label": "Flight start date",
                  "value": "2026-10-01",
                  "required": true
                },
                {
                  "type": "date",
                  "id": "end_date",
                  "label": "Flight end date",
                  "value": "2026-12-31",
                  "required": true
                },
                {
                  "type": "text_input",
                  "id": "curator",
                  "label": "Curator / Seat owner",
                  "value": "default",
                  "required": true
                }
              ]
            },
            {
              "type": "divider"
            },
            {
              "type": "columns",
              "children": [
                {
                  "type": "text_input",
                  "id": "trader_name",
                  "label": "Trader owner",
                  "value": "Alex Rivera",
                  "required": true
                },
                {
                  "type": "text_input",
                  "id": "digest_to",
                  "label": "Notification email (internal trader address)",
                  "value": "alex.rivera@example.com",
                  "format": "email",
                  "required": true
                },
                {
                  "type": "text_input",
                  "id": "cc_emails",
                  "label": "Standing CC (comma-separated, optional)",
                  "value": ""
                }
              ]
            },
            {
              "type": "columns",
              "children": [
                {
                  "type": "multi_select",
                  "id": "optimization_days",
                  "label": "Optimization Days (Full review + Workbench notification)",
                  "options": [
                    {"value": "MON", "label": "Monday"},
                    {"value": "TUE", "label": "Tuesday"},
                    {"value": "WED", "label": "Wednesday"},
                    {"value": "THU", "label": "Thursday"},
                    {"value": "FRI", "label": "Friday"}
                  ],
                  "value": ["WED"],
                  "required": true
                },
                {
                  "type": "multi_select",
                  "id": "light_days",
                  "label": "Light Check Days (Mid-week go-live & 7d result check)",
                  "options": [
                    {"value": "MON", "label": "Monday"},
                    {"value": "TUE", "label": "Tuesday"},
                    {"value": "WED", "label": "Wednesday"},
                    {"value": "THU", "label": "Thursday"},
                    {"value": "FRI", "label": "Friday"}
                  ],
                  "value": ["FRI"]
                }
              ]
            },
            {
              "type": "divider"
            },
            {
              "type": "select",
              "id": "storage_target",
              "label": "Where should CampaignConfig, OptLog & OptTargets be saved?",
              "options": [
                {"value": "workspace_default", "label": "Persistent Conversation Workspace — Optimizations/<Client> (<Code>)/ (Recommended)"},
                {"value": "custom", "label": "Custom Workspace Subfolder (specify below)"}
              ],
              "value": "workspace_default",
              "required": true
            },
            {
              "type": "text_input",
              "id": "storage_custom_path",
              "label": "Custom workspace subfolder path",
              "placeholder": "Optimizations/CustomFolder",
              "visible_if": "storage_target == \"custom\""
            }
          ]
        },
        {
          "label": "2. KPI, Thresholds & Levers",
          "children": [
            {
              "type": "columns",
              "children": [
                {
                  "type": "select",
                  "id": "primary_kpi",
                  "label": "Primary Campaign KPI",
                  "options": [
                    {"value": "ROAS", "label": "ROAS (Return on Ad Spend)"},
                    {"value": "CTR", "label": "CTR (%)"},
                    {"value": "CPA", "label": "CPA ($)"},
                    {"value": "VCR", "label": "VCR (Video Completion Rate %)"},
                    {"value": "Viewability", "label": "Viewability (%)"},
                    {"value": "CPC", "label": "CPC ($)"},
                    {"value": "CPM", "label": "CPM ($)"}
                  ],
                  "value": "ROAS",
                  "required": true
                },
                {
                  "type": "choice",
                  "id": "kpi_direction",
                  "label": "KPI Direction",
                  "options": [
                    {"value": "higher_is_better", "label": "Higher is better"},
                    {"value": "lower_is_better", "label": "Lower is better"}
                  ],
                  "value": "higher_is_better",
                  "required": true
                }
              ]
            },
            {
              "type": "toggle",
              "id": "use_baseline_fallback",
              "label": "No client benchmark yet — compute initial baseline from report",
              "value": false
            },
            {
              "type": "columns",
              "children": [
                {
                  "type": "number",
                  "id": "kpi_benchmark",
                  "label": "Primary KPI Benchmark (e.g. 33.0 for 33x ROAS, 0.08 for 0.08% CTR)",
                  "value": 33.0,
                  "step": 0.01,
                  "visible_if": "!use_baseline_fallback"
                },
                {
                  "type": "text_input",
                  "id": "channel_overrides",
                  "label": "Optional Per-Channel KPI / Benchmark Overrides",
                  "placeholder": "e.g. Display: CTR >= 0.08%, OLV: VCR >= 75%"
                }
              ]
            },
            {
              "type": "divider"
            },
            {
              "type": "columns",
              "children": [
                {
                  "type": "number",
                  "id": "min_spend_share_pct",
                  "label": "Min Addressable Spend Share (%)",
                  "value": 10,
                  "min": 1,
                  "max": 100,
                  "step": 1,
                  "required": true
                },
                {
                  "type": "number",
                  "id": "min_projected_kpi_lift_pct",
                  "label": "Min Projected KPI Change (%)",
                  "value": 5,
                  "min": 1,
                  "max": 100,
                  "step": 1,
                  "required": true
                },
                {
                  "type": "number",
                  "id": "min_impression_threshold",
                  "label": "Min Impression Sample Floor",
                  "value": 1000,
                  "min": 100,
                  "step": 100,
                  "required": true
                }
              ]
            },
            {
              "type": "multi_select",
              "id": "ssp_direct_levers",
              "label": "Allowed SSP-Direct Levers (applied on SSP deals)",
              "options": [
                {"value": "domain_block_list", "label": "Domain / App Block List"},
                {"value": "domain_allow_list", "label": "Domain / App Allow List"},
                {"value": "deal_targeting", "label": "Deal Targeting (Format / Viewability / Floor)"},
                {"value": "daypart", "label": "Dayparting (Hour of Day)"},
                {"value": "geo_targeting", "label": "Geo Targeting (DMA / City / State)"},
                {"value": "device_os_browser_targeting", "label": "Device / OS / Browser Targeting"}
              ],
              "value": [
                "domain_block_list",
                "domain_allow_list",
                "deal_targeting",
                "daypart",
                "geo_targeting",
                "device_os_browser_targeting"
              ],
              "required": true
            },
            {
              "type": "multi_select",
              "id": "client_request_levers",
              "label": "Allowed Client-Request Levers (DSP buyer implements)",
              "options": [
                {"value": "bid_adjustment", "label": "DSP Bid Adjustment"},
                {"value": "budget_shift_between_line_items", "label": "Budget Shift Between Line Items / Deals"},
                {"value": "budget_shift_between_insertion_orders", "label": "Budget Shift Between Insertion Orders"}
              ],
              "value": [
                "bid_adjustment",
                "budget_shift_between_line_items",
                "budget_shift_between_insertion_orders"
              ]
            },
            {
              "type": "slider",
              "id": "max_volume_cut_pct",
              "label": "Scale Warning Threshold (max % of deal volume a single exclusion can cut before flagging; saved as fraction max_volume_cut_pct / 100)",
              "min": 15,
              "max": 75,
              "step": 5,
              "value": 40,
              "suffix": "%"
            }
          ]
        },
        {
          "label": "3. Active Deals & Report Sources",
          "children": [
            {
              "type": "table",
              "id": "active_deals",
              "label": "Discovered Active Deals (uncheck any paused or excluded deal)",
              "select": "multi",
              "row_key": "deal_id",
              "columns": [
                {"key": "deal_name", "label": "DSP :: Deal Name in Report"},
                {"key": "ssp_seat", "label": "SSP (Seat)"},
                {"key": "channel", "label": "Channel"},
                {"key": "deal_id", "label": "SSP Deal ID"},
                {"key": "spend_7d", "label": "7d Spend", "align": "right"}
              ],
              "rows": [
                {
                  "deal_id": "IX176164622210051708",
                  "deal_name": "DV360::PMP_DemandCo_WinterGear_OLV_IndexExchange_DV360",
                  "ssp_seat": "Index Exchange (default)",
                  "channel": "OLV",
                  "spend_7d": "$2,410.20"
                },
                {
                  "deal_id": "MGNI-MD-9001-90286",
                  "deal_name": "DV360::PMP_DemandCo_TrailApparel_Display_Magnite_DV360",
                  "ssp_seat": "Magnite DV+ (default)",
                  "channel": "Display",
                  "spend_7d": "$1,800.30"
                }
              ],
              "value": ["IX176164622210051708", "MGNI-MD-9001-90286"]
            },
            {
              "type": "columns",
              "children": [
                {
                  "type": "choice",
                  "id": "report_source_type",
                  "label": "Primary DSP Report Source",
                  "options": [
                    {"value": "mailbox", "label": "Report Email Inbox (Explorer)"},
                    {"value": "uploaded_file", "label": "Uploaded Report CSV/XLSX in Chat"}
                  ],
                  "value": "mailbox",
                  "required": true
                },
                {
                  "type": "text_input",
                  "id": "report_mailbox",
                  "label": "Report Mailbox Address",
                  "value": "demandco.reports@example.com",
                  "visible_if": "report_source_type == \"mailbox\""
                },
                {
                  "type": "text_input",
                  "id": "deal_field",
                  "label": "Deal Column Name in Reports",
                  "value": "Inventory Source",
                  "required": true
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "actions": [
    {
      "id": "save_config",
      "kind": "submit",
      "label": "Save Config & Initialize Log",
      "style": "primary"
    },
    {
      "id": "save_and_run",
      "kind": "submit",
      "label": "Save Config & Run First Optimization Review Now",
      "style": "secondary"
    }
  ]
}
```

---

## Blueprint 2 — Chat-First Optimization Workbench Card (`show_ui`)

Present this card when a trader says `"review <Client> opts"`, `"open <Client> workbench"`, or `"review my opts"`.
- Clean SSP recommendations are pre-selected (`value: [...]`).
- Rows with `[SCALE WARNING]` (exceeding `cycle.max_deal_volume_cut_share`, default `0.40` when absent) or `[STALE]` are **unchecked by default**.
- In multi-campaign mode (`"review my opts"`), prefix every selectable-table `row_key` value (`<campaign_code>:<opt_id>`), every per-row target selector ID (`<campaign_code>_<opt_id>_domains`), and every per-row status selector ID (`<campaign_code>_<opt_id>_status_action`) with the campaign code (e.g. `CMP00128:ACM-005`, `CMP00128_ACM_004_1_domains`, and `CMP00128_ACM_006_status_action`) while displaying `Campaign` and `Opt ID` (`ACM-005`) in the visible columns so IDs never collide across campaigns.
- Whenever re-rendering the Workbench after an intermediate submission (such as `ask_questions`), reset all already-applied `*_status_action` controls back to `"none"` so a later button click never re-applies `"sent"` or overwrites a declared `Live` date.

```json
{
  "title": "Optimization Workbench — Acme (CMP00128)",
  "description": "Optimization Day Review · Primary KPI: CTR (Benchmark >= 0.08% Display / VCR >= 75% OLV)",
  "components": [
    {
      "type": "columns",
      "children": [
        {
          "type": "stat",
          "label": "7d Display CTR",
          "value": "0.09%",
          "caption": "+12.5% vs 0.08% benchmark",
          "tone": "success"
        },
        {
          "type": "stat",
          "label": "7d OLV VCR",
          "value": "78.4%",
          "caption": "+3.4 pts vs 75.0% benchmark",
          "tone": "success"
        },
        {
          "type": "stat",
          "label": "7d Managed Spend",
          "value": "$6,420.18",
          "caption": "Across 4 active campaign deals",
          "tone": "info"
        },
        {
          "type": "stat",
          "label": "Verified 7d Wins",
          "value": "2 Kept",
          "caption": "ACM-001 & ACM-002 (+18% CTR)",
          "tone": "success"
        }
      ]
    },
    {
      "type": "tabs",
      "tabs": [
        {
          "label": "1. Proposed SSP Optimizations (2)",
          "children": [
            {
              "type": "callout",
              "tone": "info",
              "title": "Additive SSP Updates ({{ count(selected_opts) }} selected)",
              "text": "Clean SSP recommendations are pre-selected. Items marked [SCALE WARNING] (>40% deal volume cut) are unchecked by default so you consciously opt in."
            },
            {
              "type": "table",
              "id": "selected_opts",
              "label": "Select SSP optimizations to apply or export target lists",
              "select": "multi",
              "row_key": "opt_id",
              "columns": [
                {"key": "opt_id", "label": "Opt ID"},
                {"key": "deal", "label": "Deal (SSP · Seat)"},
                {"key": "change", "label": "Proposed Change"},
                {"key": "spend_impact", "label": "Window Spend / Share", "align": "right"},
                {"key": "projected_lift", "label": "Projected KPI Lift", "align": "right"}
              ],
              "rows": [
                {
                  "opt_id": "ACM-004.1",
                  "deal": "Acme Display (Index Exchange · default)",
                  "change": "Domain Block List: Add 3 zero-click domains",
                  "spend_impact": "$412.80 (14.2%)",
                  "projected_lift": "0.09% -> 0.11% CTR (+22%)"
                },
                {
                  "opt_id": "ACM-005.1",
                  "deal": "Acme OLV (Magnite DV+ · default)",
                  "change": "Domain Block List: Add 2 low-VCR domains [SCALE WARNING: removes 44% of deal volume]",
                  "spend_impact": "$890.00 (44.1%)",
                  "projected_lift": "78.4% -> 84.1% VCR (+5.7 pts)"
                }
              ],
              "value": ["ACM-004.1"]
            },
            {
              "type": "multi_select",
              "id": "ACM_004_1_domains",
              "label": "ACM-004.1 Target Domains (uncheck any domain to spare it, or add custom)",
              "allow_custom": true,
              "options": [
                {
                  "value": "dailymotion.com",
                  "label": "dailymotion.com ($198.40 spend · 64,200 imps · 0 clicks · 0.00% CTR)"
                },
                {
                  "value": "msn.com",
                  "label": "msn.com ($124.10 spend · 41,100 imps · 1 click · 0.002% CTR)"
                },
                {
                  "value": "yahoo.com",
                  "label": "yahoo.com ($90.30 spend · 29,800 imps · 0 clicks · 0.00% CTR)"
                }
              ],
              "value": ["dailymotion.com", "msn.com", "yahoo.com"]
            }
          ]
        },
        {
          "label": "2. Console & Client Requests (1)",
          "children": [
            {
              "type": "table",
              "label": "SSP Console & Buyer DSP Recommendations",
              "columns": [
                {"key": "opt_id", "label": "Opt ID"},
                {"key": "path", "label": "Path"},
                {"key": "deal", "label": "Deal / Line Item"},
                {"key": "recommendation", "label": "Action & Quantified Impact"}
              ],
              "rows": [
                {
                  "opt_id": "ACM-006",
                  "path": "[Client Request]",
                  "deal": "DV360 Display vs. OLV Insertion Order",
                  "recommendation": "Shift 15% of weekly budget toward Acme Display (IX), which is beating the CTR benchmark by +38%"
                }
              ]
            },
            {
              "type": "choice",
              "id": "ACM_006_status_action",
              "label": "ACM-006 Status Update (in multi-campaign mode prefix ID as <campaign_code>_<opt_id>_status_action)",
              "options": [
                {"value": "none", "label": "No change (keep Status = Proposed)"},
                {"value": "live", "label": "ACM-006 is Live in DSP (Status = Live, Live = today)"},
                {"value": "sent", "label": "ACM-006 Sent to Client (Status = Proposed, log date in Notes)"}
              ],
              "value": "none"
            }
          ]
        },
        {
          "label": "3. Backlog & Signals (1)",
          "children": [
            {
              "type": "table",
              "id": "promote_backlog",
              "label": "Check any Backlog candidate to promote to Proposed now (materializes OptTargets rows)",
              "select": "multi",
              "row_key": "opt_id",
              "columns": [
                {"key": "opt_id", "label": "Opt ID"},
                {"key": "deal", "label": "Deal"},
                {"key": "candidate", "label": "Candidate & Signal"},
                {"key": "spend", "label": "Window Spend", "align": "right"}
              ],
              "rows": [
                {
                  "opt_id": "ACM-007.1",
                  "deal": "Acme Display (Index Exchange · default)",
                  "candidate": "Daypart: Exclude 01:00–04:59 UTC (0.03% CTR across $84 spend)",
                  "spend": "$84.20"
                }
              ],
              "value": []
            }
          ]
        }
      ]
    },
    {
      "type": "text_input",
      "id": "trader_question",
      "label": "Ask a Question or Validate Before Executing (optional)",
      "placeholder": "e.g. How did msn.com perform over the full 28-day window, or what volume remains if we include ACM-005.1?",
      "multiline": true
    }
  ],
  "actions": [
    {
      "id": "execute_selected",
      "kind": "submit",
      "label": "Apply Selected Updates",
      "style": "primary"
    },
    {
      "id": "ask_questions",
      "kind": "submit",
      "label": "Ask / Validate First",
      "style": "secondary",
      "validate": false
    }
  ]
}
```

---

## Blueprint 3 — Client-Facing Optimization Story & Spend Elasticity Builder Card (`show_ui`)

Present this card when a trader asks to craft a client-facing optimization email (`"craft a client email for <Client>"`, `"client story for <Client> opts"`, or `"pitch incremental budget for <Client>"`).
- Populate `to_email.value` dynamically from the signed-in requesting trader's email (or `team.digest_to` from the campaign's `CampaignConfig`) — never hard-code one employee's email across all traders.

```json
{
  "title": "Client Optimization Story Builder — DemandCo",
  "description": "Zero-Jargon Curation Showcase · Primary KPI: ROAS (33.0x Benchmark)",
  "components": [
    {
      "type": "choice",
      "id": "story_archetype",
      "label": "A. Pick the Story Archetype",
      "options": [
        {
          "value": "just_executed",
          "label": "1. \"Just Executed\" Proactive Curation Showcase (7d momentum + today's SSP curation + projected lift)"
        },
        {
          "value": "proven_lift",
          "label": "2. \"Proven Lift\" Before-vs-After Readout (verified 7d+ Kept results + spend reallocated)"
        },
        {
          "value": "expansion_pitch",
          "label": "3. Strategic Allocation & Incremental Budget Pitch (curation + 4-Test Spend Elasticity scaling band)"
        }
      ],
      "value": "just_executed",
      "required": true
    },
    {
      "type": "multi_select",
      "id": "email_blocks",
      "label": "B. Modular Email Sections to Include",
      "options": [
        {"value": "block_exec_summary", "label": "Block 1 — Punchy 2-Sentence Executive Summary (no campaign codes or internal IDs)"},
        {"value": "block_kpi_cards", "label": "Block 2 — Headline KPI Snapshot Cards (Actual KPI, Post-Curation KPI + Lift%, Spend Reallocated)"},
        {"value": "block_chart", "label": "Block 3 — Single-Panel Deal KPI Lift Chart (1088x420 horizontal bar chart vs. benchmark)"},
        {"value": "block_deal_table", "label": "Block 4 — Deal Performance & Lift Table (per-deal Spend, Outcome, Actual vs. Post-Curation KPI)"},
        {"value": "block_opt_detail", "label": "Block 5 — \"Optimizations Implemented\" 4-Column Detail Table"},
        {"value": "block_phase2_roadmap", "label": "Block 6 — Phase 2 Curation Roadmap Callout (queued next-wave refinements)"},
        {"value": "block_spend_elasticity", "label": "Block 7 — 4-Test Spend Elasticity & Safe Incremental Budget Pitch"}
      ],
      "value": [
        "block_exec_summary",
        "block_kpi_cards",
        "block_chart",
        "block_deal_table",
        "block_opt_detail",
        "block_phase2_roadmap"
      ],
      "required": true
    },
    {
      "type": "choice",
      "id": "primary_kpi_scope",
      "label": "Primary KPI & DSP Scope (required when a campaign has multiple DSPs or channel KPI overrides)",
      "options": [
        {"value": "DV360:ROAS", "label": "DV360 — ROAS (33.0x Benchmark)"},
        {"value": "separate_per_kpi", "label": "Separate Section per DSP / Channel KPI (never combine incomparable metrics on one axis)"}
      ],
      "value": "DV360:ROAS",
      "required": true
    },
    {
      "type": "choice",
      "id": "domain_granularity",
      "label": "Block 5 Domain / Segment Detail Granularity",
      "options": [
        {"value": "full_list", "label": "Full List of All Removed Domains/Values + Notable Spared High-Performers (Recommended)"},
        {"value": "top_examples", "label": "Top Representative Examples Only"},
        {"value": "category_summary", "label": "High-Level Category Summary Only"}
      ],
      "value": "full_list",
      "required": true
    },
    {
      "type": "columns",
      "children": [
        {
          "type": "text_input",
          "id": "to_email",
          "label": "Recipient email(s) (leave blank to send to your signed-in trader inbox / campaign digest_to)",
          "placeholder": "Defaults to requesting trader inbox or campaign team.digest_to"
        },
        {
          "type": "text_input",
          "id": "greeting",
          "label": "Greeting line",
          "value": "Hi Team,",
          "required": true
        }
      ]
    },
    {
      "type": "columns",
      "children": [
        {
          "type": "choice",
          "id": "audience_framing",
          "label": "Audience Framing",
          "options": [
            {"value": "partner_showcase", "label": "Curation Partner Showcase"},
            {"value": "agency_buyer", "label": "Agency / End-Client Buyer"}
          ],
          "value": "partner_showcase",
          "required": true
        },
        {
          "type": "toggle",
          "id": "attach_kpi_summary_xlsx",
          "label": "Attach client-safe KPI & channel summary table (.xlsx — no SSP deal IDs)",
          "value": false
        }
      ]
    }
  ],
  "actions": [
    {
      "id": "generate_story_email",
      "kind": "submit",
      "label": "Generate & Stage Client Story Email",
      "style": "primary"
    }
  ]
}
```

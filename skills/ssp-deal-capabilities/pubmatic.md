# PubMatic (curated deals): deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the PubMatic
console · **DSP** = buyer's line item only · **No** = not possible / blocked
for us · **Create only** = fixed after booking (change = new deal, pause the
old one).

## How changes are applied

- Chat update plan: `pm_update_curated_deal` for name, description, dates,
  open-ended, fixed floor, minimum floor, priority, platforms, ad formats,
  Max Reach publisher settings, and status (pause / resume, sent through
  `pm_update_curated_deal_status`); `pm_merge_deal_publishers`; buyer swap
  (`pm_merge_deal_buyers`); and every targeting merge: geo, domains / app
  bundles, segments, tech, format, targeting lists, content (IAB categories,
  genre exclusion) and blocked genres.
- Each targeting change creates a new PubMatic targeting object that can
  never be deleted. The plan checks the live deal first and sends nothing when
  the change is already there (a retry never creates a second object), but
  still bundle targeting changes into as few writes as possible.
- **Paused deals.** PubMatic refuses every edit on a paused deal. A plan
  holds one status change per deal, and orders it for you: a resume runs
  before the deal's other changes, a pause after them. So "resume and change"
  is one plan, and "change, then pause" is one plan. Changing a paused deal
  that should stay paused takes **two plans**: resume + change, then a
  second plan that pauses it again. The deal is live and delivering between
  the two approvals. A change to a paused deal with no resume in its plan
  shows as blocked on the review card.
- **Max Reach deals over 200 publishers.** On a Max Reach deal PubMatic's read
  returns a live sample of the auto-managed publisher pool, and every edit
  re-sends it. When that sample is over the 200 publishers an edit may carry,
  PubMatic refuses the edit (`PMP_PP_001`), so our tools refuse it first with
  nothing written (`pm_max_reach_pub_sample_over_cap`): name, description,
  dates, floors, platforms, ad formats, Max Reach settings, buyer swaps,
  publisher merges and targeting merges are then **UI** on that deal. Pause /
  resume still works (it uses the separate status endpoint), and so does a
  publisher merge that changes nothing. The tools never trim or clear the
  publisher list to get an edit through, since that would also drop the
  deal's always-allowlisted publishers; an operator who knows that set exactly
  can pass it as `pub_ids` on `pm_update_curated_deal` (an empty list only
  when the deal has none).

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / paused) | API (created active) | API | `update` `status` (via `pm_update_curated_deal_status`) | No archive or delete. Resuming a completed deal needs a future end date in the same change (the review card blocks it otherwise). |
| Start / end date | API | API | `pm_update_curated_deal(start_date, end_date)` | |
| Open-ended | No | API | `open_ended=True` | |
| Name | API | API | | Name ≤ 250 chars. |
| Description | No | API | `pm_update_curated_deal(description)` | The create tool takes no description: book the deal, then set it with an update. PubMatic never echoes it, so it cannot be read back. |
| Deal ID (PM-…) | API (optional: `scheduleLifecycle.pubmatic.dealId`) | Create only | | Blank (the default) lets PubMatic make its own PM-XXXX-NNNN id. A chosen id is 1-64 letters, digits, `-`, `_`, `.` and is read back after the create: if PubMatic booked another, the booking fails loud (`pm_deal_field_readback_mismatch`; the deal exists and keeps its id, never re-created). |
| Auction type (First Price / Fixed Price) | API | Create only | | "Auction type can not be changed": new deal + pause the old one. |
| Fixed Price floor (`floor_ecpm`) | API | API | `pm_update_curated_deal(floor_ecpm)` (plan field `fixed_floor`) | Only works on Fixed Price deals. **At create**, passing it books the deal as Fixed Price at that exact CPM, which kills delivery for a deal meant to be First Price. **On a live First Price deal** PubMatic silently drops it (the tool warns) and nothing changes: use `minimum_floor` instead. |
| First Price minimum floor | API (optional: `pricingBuyers.pubmatic.minimumFloor`) | API | `pm_update_curated_deal(minimum_floor)` | The floor knob for First Price deals. Never required and never flagged when absent: a First Price deal with no floor is the house default. Refused beside Fixed Price (`pricing_overlap`). The card's Floor CPM is not booked on PubMatic (warning `pm_floor_cpm_not_applied`). |
| Curator fee | API (required, percent) | Create only | | Re-pricing the fee means a new deal. CPM fees are refused. Some seats cannot set a transaction fee at all. |
| DSP / buyer seat | API (exactly one) | API, swap only | `pm_merge_deal_buyers(merge_mode="replace")` | One buyer per deal; a second buyer needs a second deal. Needs the resolved DSP, buyer and seat ids. |
| Publishers allow / block | API: allow = the publisher list (Max Reach off); block = `pubmaticConfig.publisherBlockList` (publisher ids, Max Reach on) | API | `pm_merge_deal_publishers` | Batch-safe, no new targeting object. A block list with Max Reach off is refused (`pm_publisher_block_list_requires_max_reach`), and so is a publisher both always-allowlisted and blocked. |
| Max Reach: max publishers, auto-update publishers | API | API | `pm_update_curated_deal` | Switching Max Reach itself on or off: No. |
| Priority | API (optional: `pricingBuyers.pubmatic.priority`) | API | `priority` | Whole number, lower = higher priority; PubMatic's default 10 is sent when unset. |
| Platform (web, mobile web, iOS app, Android app, CTV) | API (channel + Environment; iOS-only / Android-only app deals: `deals[i].appPlatform` 1 / 2 on In-App or CTV and OTT) | API | `pm_update_curated_deal(platforms)` | Picks the inventory environment. An app platform on a deal that also books web (All) or no app pair (Web Only, CTV) is refused (`pm_app_platform_requires_in_app`). One app platform keeps CTV devices open on Display / OLV / Native / OTT (warning `pm_ctv_devices_not_excluded`, see the CTV row). On a deal that carries the CTV-off device block, the update is refused (`pm_ctv_device_block_platforms_unproven`) unless the new set matches the deal's block: All `[1,2,4,5]` / Web Only `[1,2]` for the block with desktop, In-App `[4,5]` for the block without it. |
| Ad format (banner / native / video) | API | API | `pm_update_curated_deal(ad_formats)` | Fixed Price allows one format. Mixing video with banner / native disables video targeting. |
| Ad sizes | API | API | `pm_merge_deal_format("ad_sizes")` | |
| CTV devices off (Display / OLV / Native / OTT) | Automatic | Kept on every re-mint | | The MCP writes PubMatic's per-class device block itself (desktop, phones, tablets on All; CTV on None) on All `[1,2,4,5]` and Web Only `[1,2]`, and the same block without desktop on In-App `[4,5]`; flag `pm_ctv_devices_excluded`. Other platform sets stay open (`pm_ctv_devices_not_excluded`): tell the trader to untick CTV in the Device step. |
| Device type, device make / model | No | No | | PubMatic does not save device type, so a device request is refused. `device_make_models={"desktop": [0]}` (desktop, All makes) is API (tool) only: chat never books it, because what it does to the other device classes is unproven and it replaces the CTV-off device block (CTV devices would stay open). Any other make/model is refused. |
| OS, carrier | API (include **or** exclude) | API | `pm_merge_deal_tech` | |
| Browser, connection type, device-ID type | API (include only) | API (include only) | `pm_merge_deal_tech` | |
| Geo: country / state / city / DMA | API (include and exclude) | API | `pm_merge_deal_geo` | By name (numeric PubMatic geo ids are tool-only). |
| Geo: postal | API (include **or** exclude) | API | `pm_merge_deal_geo` | EMEA postal sector prefixes (wildcard): API (tool) only. |
| Geo / postal / domain / brand-safety lists (existing list id) | API (tool) only: chat refuses the brief's list ids (`pm_targeting_list_route_unverified`) | API (add / replace) | `pm_merge_deal_inventory` | No list id has been written to a deal on our seats yet (no list exists on them; lists are made in the console). Cannot clear. Ship a list as a brief list file instead. |
| Domain allow / block | API (create default: allow) | API | `pm_merge_deal_domains` | Allow **or** block. Always state the direction (the plan requires it). |
| App bundles | API | API | `pm_merge_deal_domains` | Domains and bundles share one domainList (allow) / excludeDomainList (block) pair. A chat create ships a site list AND an app-bundle list together (a Display / OLV / Native deal with All inventory, an OTT deal): same direction = one branch, an allow and a block list = both branches. A site allowlist beside an app-bundle blocklist (or the reverse) is refused (`list_direction_mix`); a second list of one kind is refused (`list_multiple_per_kind`); a list the deal's inventory cannot reach is reported `list_not_applied`. |
| Audience segments | API (one OR group, include **or** exclude) | API | `pm_merge_deal_segments` | No AND across groups; include + exclude together fails. Check reach first (see watch-outs). |
| IAB categories | API (include **or** exclude) | API | `pm_merge_deal_content("iab_category")` | |
| Genre | API, exclude only (`contentTargeting.pubmatic.blockedGenres`: excluded right after the create) | API, exclude only | `pm_block_deal_genres`, or `pm_merge_deal_content("genre")` exclude | At create the exclusion is the item's post step (`pm_block_deal_genres` on the new deal); if it fails the deal is live without it and the plan says so (`partial`; running the plan again retries the step, or, when a targeting object may already have been minted, only checks the deal: then exclude the genres with an update. The deal is never re-created). Names as PubMatic spells them (no catalog check, no commas). Genre inclusion: No. |
| Content rating, network, channel, livestream, language, series, title | UI | UI | | Our tools refuse them (unproven wire format). |
| Sensitive verticals | UI | UI | | Refused by our tools (PubMatic errors on the API write). When enabled, the vertical is read back after the write: one PubMatic holds differently fails loud (`pm_deal_field_readback_mismatch` at create, `pm_sensitive_vertical_readback_mismatch` on an update), never re-sent. |
| Viewability threshold | API (10–90, step 10) | API (set / replace) | `pm_merge_deal_format("viewability")` | Cannot be removed once set (replace it). |
| VCR minimum | API (every video deal: `deals[i].vcr`, one of 60 / 70 / 80 / 90) | API (set / replace) | `pm_merge_deal_format("vcr")` | Any other value is refused (`pm_video_completion_rate_invalid`), never rounded. Not sent on a Display / Native deal (reported `vcr_not_applied`). Cannot be removed once set (replace it). |
| CTR, attention minimums | API | API (set / replace) | `pm_merge_deal_format` | Cannot be removed once set (replace them). |
| Brand safety (inline vendor segments) | No | No | | Only an existing brand-safety list by id. |
| IVT / bot filtering | Via an audience segment, if the seat has one | API | `pm_merge_deal_segments` | No IVT setting on the deal. |
| Video placement, position, playback, skippable, player size, ad types, rewarded, orientation, slot length | API | API | `pm_merge_deal_format` | Rewarded cannot be combined with skippable. Single-value settings (skippable, player size, orientation, slot length) can be replaced, not removed. VPAID compliance: No. |
| Integration type, directness | API (`pubmaticIntegrationType`, `pubmaticDirectness`) | API | `pm_merge_deal_format("integration_type" / "directness")` | Directness SPO segments: No (unproven wire). |
| Always-allowlisted publishers | API (`inventoryTargeting.pubmatic.alwaysAllowlistedPublisherIds` / `Names`, Max Reach on) | No | | With Max Reach off the publisher list is the allowlist (`pm_always_allowlisted_requires_max_reach`). |
| Domain match breadth (simple / exact / broad) | API (`inventoryTargeting.pubmatic.domainMatchBreadth`) | API | `pm_merge_deal_domains(domain_match_breadth)` | Simple is the default. |
| Key-values, deal bundles | No | No | | |
| Dayparting | DSP | DSP | | PubMatic's API documents it, but our tools refuse it (unproven wire format) and console support for curated deals is unverified. |
| Frequency cap | DSP | DSP | | |
| Goal-based KPI / budget (PubMatic feature) | No | No | | Our tools refuse it; console support for curated deals is unverified. |
| Pacing / spend budget | DSP | DSP | | Set on the buyer's line item. |

## Watch-outs before recommending a change

- **Never recommend a deal-level floor (`floor_ecpm`) for a First Price
  deal**, and never flag a First Price deal for having no floor (the house default). At create it books the deal as Fixed Price; on a live deal it is
  silently ignored. Use `minimum_floor`.
- **Auction type and fee are fixed** after creation.
- **Paused deals can't be edited** without resuming them first. Keeping a
  paused deal paused after a change takes two approved plans (resume + change,
  then pause), and the deal delivers in between.
- **Zero-reach auto-pause.** PubMatic pauses a deal whose only audience has
  zero reach (about 6 seconds after the change). The review card warns when a
  segment change would leave one zero-reach audience
  (`pm_single_zero_reach_segment`, or `pm_segment_reach_unknown` when reach
  can't be read). Before recommending a narrow single-segment audience, check
  reach with `pm_search_audiences` (`rtb_unique_users`).
- **OR-only audiences.** "AND these audiences" is not possible on PubMatic.
- **One buyer per deal.**

## Diagnostics (read tools)

- `pm_get_curated_deal`, `pm_get_targeting` — live config.
- `pm_run_preset_report` (deal_summary, dsp_daily, site_daily),
  `pm_run_standard_report`, `pm_run_report_from_prompt_inputs` — paid
  impressions, spend, eCPM, requests, win rate, bid rate, CTR, VTR, video
  quartiles, fees; by date, hour, deal, DSP, buyer, publisher, site,
  platform, domain, app.
- `pm_search_audiences` — segment reach.
- No forecast / avails tool.

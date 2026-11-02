# Equativ (Maestro): deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the Maestro
console · **DSP** = buyer's line item only · **No** = not possible / blocked
for us · **No (raw only)** = only the raw `eq_create_deal` `targeting` object
can set it (a scheduled run with resolved ids); the chat booking flow cannot ·
**Create only** = fixed after booking (change = clone to a new deal).

Chat create brief fields (the deal-booking brief; `get_brief_schema` lists them
under `ssp_fields.equativ`) are named in the Notes as `equativConfig.*` (every
Equativ deal of the brief) or `inventoryTargeting.equativ.*` /
`contentTargeting.equativ.*` / `audienceTargeting.equativ.*` (per deal).

## How changes are applied

- Every write is a full read → change → write → re-read inside the tool, so
  untouched fields are preserved. Pass `expected_updated_at` from the last
  read to refuse a write if someone edited the deal in between.
- Scalars (name, dates, price, status, margin, buyers): `eq_update_deal`.
- Targeting: one merge tool per dimension (`merge_mode` add / remove /
  replace): `eq_merge_deal_geo`, `_segments`, `_publishers`, `_tech`,
  `_format`, `_content`, `_domains`. All of these are in the chat update
  plan, including a geo-level switch (`switch_geo_type`), adding or removing
  a whole segment group (named by its members, so a re-run is a no-op) and a
  domain list from an uploaded file.
- A create-only setting can be changed only with `eq_clone_deal` (new paused
  deal, new deal ID the buyer must re-target). Clone is not on the chat plan
  path; it has its own approval card.

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / paused) | API | API | `eq_update_deal(is_active)` | Pausing is the only way to stop a deal. A deal whose start is still in the future goes live by itself at 00:00 ET on the start day: pausing it needs `acknowledge_goes_live_at_start: true` in the plan, and the review card warns that the pause does not hold past the start. |
| Archive | UI | UI | Maestro console | An archived deal refuses every write. The only way to stop a future-start deal. |
| Start / end date | API | API | `eq_update_deal(start_date, end_date, open_ended)` | US Eastern days. A start more than a day in the past is refused. Guaranteed deals: start locked once in flight, end can only be extended. |
| Name | API | API | `eq_update_deal(name)` | 1–255 chars; must not duplicate a live deal's name. |
| Public deal ID | No | No | | Assigned by Equativ. |
| Pricing model (variable floor / fixed / custom floor) | API | Create only | `pricing_model` | Brief `equativConfig.pricingModel`. Change = clone with a `pricing_model` override. |
| Guaranteed pricing | API (tool) | Create only | `eq_create_deal` / `eq_execute_deal_from_prompt_inputs(pricing_model=guaranteed)` | Not booked from a chat brief (house rule: it needs exactly one eligible buyer and Display / In-Stream / Out-Stream only); the brief refuses it (`equativ_pricing_model_invalid`). |
| Price (floor or fixed CPM) | API | API | `eq_update_deal(price)` | On variable floor, 0 = use the publisher floor. A price cut is re-checked against segment data cost (see watch-outs). |
| Currency | API | Create only | `currency` | Brief `equativConfig.currency` (USD by default): USD, EUR, GBP, CAD, AUD, NZD or CHF, the currencies the tool's 1,000 CPM typo guard covers; any other is refused (`equativ_currency_unsupported`). The deal CPM is in that currency; a non-USD custom-floor deal needs a CPM (the $0.10 house default floor is never re-labelled: `equativ_floor_currency_required`). |
| Curation margin | API | API | `eq_update_deal(margin_percent)` | 0–100 (exclusive). Equativ's own platform fee is fixed by Equativ. |
| Buyers (DSPs or buyer seats) | API | API (replaces the whole set) | `eq_update_deal(dsp_names / dsp_ids` or `buyer_names / buyer_ids + buyer_dsp)` | DSPs and seats are mutually exclusive; an update replaces the full list. To add one DSP, pass the full new list. |
| Geo | API | API | `eq_merge_deal_geo` | One geo type per deal (country, region/state, city, DMA or postal), include only — no geo exclusions. Changing the geo level is API: `switch_geo_type: true` in the plan; the review card lists every geo level and value it clears. A chat brief's states / provinces book on Equativ's `states` catalog. |
| Geo: Equativ "regions" catalog | API (tool) | API | `regions` at create; `eq_merge_deal_geo(geo_type=region)` | A separate Equativ catalog from states; what its rows cover is not documented by Equativ, so a chat brief's state never maps to it and no brief field books it. Set it in a chat update or in Maestro. |
| Hyperlocal radius | API | Create only | `hyperlocal_points` | Circles of latitude, longitude and a whole radius in metres (precision GPS and IP by default). A chat brief's radius circles (miles) are converted to metres (1 mi = 1609.344 m, rounded to the metre). Needs one other geo type on the deal, usually its country; without one the chat create is blocked (`equativ_hyperlocal_requires_geo`). |
| Publishers | API | API | `eq_merge_deal_publishers` | Either none or at least 2 (exactly 1 is refused); a merge cannot remove all. |
| Domain / app-bundle allow or block list (deal's own) | API | API | `eq_merge_deal_domains` | One list per deal, one direction. Domains and app bundles share it. Switching allow ↔ block means emptying it first. Values can come from an uploaded list file. Only numeric iOS ids and dotted Android bundles are accepted. A chat create ships a site list AND an app-bundle list together (`domain_file_path` + `app_bundle_file_path`) into that one list (an app-bundle file is booked whole: a row that is not an app id refuses the create), so both must be allowlists or both blocklists (`list_direction_mix` otherwise); a second list of one kind is refused (`list_multiple_per_kind`); a list the deal's inventory cannot reach is reported `list_not_applied`. |
| Shared Maestro site lists | API (by id) | UI | `site_list_ids` at create | Brief `inventoryTargeting.equativ.siteListIds` (numeric Maestro list ids, beside the deal's own list). The create read-back checks them. The merge tool for shared lists (`eq_merge_deal_lists`) has no live canary and is not enabled. |
| Channel / impression type | API | API | `eq_merge_deal_format(impression_type)` | The channel fills the impression types; brief `inventoryTargeting.equativ.impressionTypes` (Display, Native, In-Stream, Out-Stream, Audio) replaces them. Guaranteed deals: Display, In-Stream, Out-Stream only. |
| Creative sizes | API | API | `eq_merge_deal_format(creative_size)` | |
| Device / platform | API | API | `eq_merge_deal_tech(platform)` | |
| Inventory type (web / app / CTV / DOOH) | API | API | `eq_merge_deal_tech(inventory_type)` | |
| CTV flag | API | Create only | `is_ctv` | The channel sets it (on for CTV only); brief `inventoryTargeting.equativ.isCtv` overrides it either way. |
| OS / browser / carrier | DSP | DSP | | Not on Equativ deals. |
| Audience segments | API | API | `eq_merge_deal_segments(kind=audience)` | Groups AND-ed, members of a group OR-ed; exclusions supported. Adding / removing a whole AND group is API (the group is named by its segments). Billed per CPM — see watch-outs. |
| Contextual: semantic and domain-category segments | API | API | `semantic_*` / `domain_category_*` at create; `eq_merge_deal_segments` | Brief `audienceTargeting.equativ.semanticSegments` / `semanticSegmentGroups` / `semanticSegmentsExclude` and the same three `domainCategory*` (names or numeric ids; a list OR groups, not both). Every name resolves exactly; one miss fails the whole deal. Semantic data is billed. |
| Content: genre, category, channel, network, language, livestream | API | API | `eq_merge_deal_content` | Livestream on video deals only. Livestream "Not live" (false) is refused at create (not proven in our code); "Live" books. |
| Content: CTV catalogs (OTT, category, channel, distributor, network) | API | API | `ctv_*` at create; `eq_merge_deal_content` | Brief `contentTargeting.equativ.ctvOttTargets` / `ctvCategories` / `ctvChannels` / `ctvDistributors` / `ctvNetworks` (catalog names, resolved exactly; a miss fails the deal). |
| IAB category, key-values | DSP | DSP | | Not on Equativ deals. |
| Viewability / completion rate / CTR minimum | API | API | `eq_merge_deal_format(viewability / video_completion / ctr, merge_mode=replace)` | "0" clears the threshold. |
| Green PMP, cookieless, OM-SDK-only | API | Create only | `green_pmp`, `cookieless`, `om_sdk_only` | Brief `inventoryTargeting.equativ.greenPmp` / `cookieless` / `omSdkOnly` (true turns it on; the create read-back checks it). |
| IVT / IAS / DV pre-bid segments | DSP | DSP | | Not exposed by our tools; treat as buyer-side. |
| Dayparting | DSP | DSP | | Not on Equativ deals. |
| Frequency cap | DSP | DSP | | Not on Equativ deals through our tools. |
| Delivery capping (Equativ budget objects) | No | No | | Exists at Equativ, but our tools deliberately do not set it. |
| Pacing / spend budget / impression goals | DSP | DSP | | Set on the buyer's line item. |
| Video placement, ad break | API | API | `eq_merge_deal_format(video_placement / video_ad_break)` | |
| Max ad duration (bucket) | API | API | `video_max_ad_durations` at create; `eq_merge_deal_format(video_max_ad_duration)` | Only as short / medium / long / extra_long buckets, and Equativ publishes no seconds for them. Pick the buckets on the intake card (Max ad duration (Equativ); brief `inventoryTargeting.equativ.maxAdDurationBuckets`). A duration in seconds (max or allowed list) cannot be mapped to a bucket, so it blocks the create (`equativ_ad_duration_unmapped`), never guessed or dropped. Video deals only (In-Stream / Out-Stream impressions; else `equativ_duration_requires_video`). |
| Skippable, rewarded, minimum player size | API | API | `eq_merge_deal_format` | Skippable true / false / unset are three different settings. |
| DSP sync | Equativ default | UI | | Our tools never change it. |
| Templates, content ids / contexts / tags, player-size buckets, SCO node buckets, SPO targeting / trusted publishers, identity provider, caller / children deal ids, management vendor cost (CPM or rate) | No (raw only) | No | `eq_create_deal(targeting={templateIds, contentIds, contentContextIds, contentTagNames, videoPlayerSizeBucketIds, scoNodeBucketIds, spoTargetingIds, spoTrustedPublishersIds, identityProviderId, callerDealIds, childrenDealIds, managementVendorCostCpm, managementVendorCostRate})` | Raw Equativ ids only; no brief field, no merge tool. |

## Watch-outs before recommending a change

- **Segment data cost vs price.** Equativ refuses a deal whose audience data
  cost exceeds its price ("Fixed Cost too high"); each AND group is charged at
  its most expensive member and exclusions are billed too. "Lower the price"
  or "add a segment group" can be refused — suggest cheaper segments or a
  price that covers the data.
- **Pricing model and currency are fixed.** "Switch to fixed price" is a
  clone, i.e. a new deal ID.
- **One geo type, include only.** "Add Ohio" to a country-targeted deal is a
  geo-level switch that drops the country targeting; say so.
- **Future-start deals still go live at their start.** A pause before the
  start does not hold; only archiving in the console stops one for good.
- **Avails reflect the live targeting only.** `eq_get_deal_avails` cannot
  forecast a proposed change.
- A result with `success=false, written=true` means the write landed but the
  read-back disagreed: reconcile, never resend.

## Diagnostics (read tools)

- `eq_get_deal` — full current state (read before recommending).
- `eq_get_deal_troubleshooting` — bid funnel with the failure reason at each
  step (UTC days). Best first stop for "why isn't it delivering".
- `eq_get_deal_avails` — last-7-day auctions and median CPM for the live
  targeting.
- `eq_get_deal_delivery`, `eq_get_deal_pacing`, `eq_get_deal_metrics`
  (whole days, to yesterday).
- `eq_run_report` — CSV with bid requests, bids, wins, impressions, spend,
  curator margin, data cost, eCPM, clicks, viewability, completion, bid and
  win rate.

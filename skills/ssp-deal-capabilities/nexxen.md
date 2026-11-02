# Nexxen: deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the Nexxen
console · **DSP** = buyer's line item only · **No** = not possible / blocked
for us · **Create only** = fixed after booking (change = clone to a new deal).

Nexxen has the widest set of deal-level delivery controls of any SSP here
(frequency caps, dayparting, impression caps, quality filters), and all of
them are in the chat update plan.

## How changes are applied

- Chat update plan: every typed `nx_update_deal` field (name, dates, notes,
  status, floor, auction type, margin, currency, frequency caps, dayparting,
  viewability / CTR / VCR filters, impression and request caps, request
  pacing, video settings, supply floor range, livestream, OM SDK / VPAID /
  MRAID / SIMID / COPPA, supply-chain hops, and `clear_fields` resets), each
  shown with its current value on the review card; merges for geo, segments
  (including a new AND group), domains, tech (device / media type), format,
  buyers.
- Every update reads the full deal, writes it back and verifies it.
- Chat create (deal brief): the Nexxen-only settings ride per deal in
  `nexxenFormatSupply` (format & supply filters, supply floor range,
  `maxVideoDurationMinimumSecs`, per-deal media lists),
  `scheduleLifecycle.nexxen` (notes, external id, caps, pacing, frequency
  caps, dayparting) and `contentTargeting.nexxen`, and campaign-wide in
  `nexxenConfig` (auction type, the Nexxen DSP account, media lists). Every
  create reads the new deal back and verifies each field it sent.
- About 3–4 deals a minute (vendor rate limit).

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / inactive) | API (always active) | API | `nx_update_deal(status)` | House policy: every deal is created active; a brief asking for inactive (`createInactive`) is refused, never booked paused. No archive or delete; inactive is the only stop. |
| Start / end date | API | API | `nx_update_deal(start_date, end_date)` | **Maximum 1 year** from today (or start). Never open-ended. A running deal can be extended up to a year from today. |
| Name, notes | API | API | `nx_update_deal(name, notes)` | Name ≤ 128 chars, no "/". Notes ≤ 255. |
| External deal ID (NXN-…) | API | Create only | | |
| Description, data fee, play-through | UI | UI | | Not in Nexxen's public API. |
| Auction type (dynamic floor / fixed) | API | API | `nx_update_deal(auction_type)` | Fixed is not live-proven. |
| Floor | API | API | `nx_update_deal(bid_floor)` | USD 0–100. **The floor includes the margin.** |
| Margin | API (required: percent 0–80 or fixed USD 0–99) | API | `nx_update_deal(margin_percent)` or `nx_update_deal(margin_fixed)` | Exactly one. Raising margin raises what the buyer must clear without changing `bid_floor`. |
| Supply floor range | API | API | create: brief `nexxenFormatSupply.supplyFloorMin / supplyFloorMax`; update: `nx_update_deal(supply_floor_min / max)` | USD 0–100, 4 decimals, min ≤ max; either end alone is fine. Only supply whose own floor sits in the range. Hidden from partners in the console. |
| Currency | API (USD) | API | `nx_update_deal(currency)` | Display currency only; Nexxen bills in USD. Chat creates always book USD (there is no brief field for another currency); change the display currency afterwards with an update. |
| DSPs | API (one per deal) | API | `nx_merge_deal_buyers(dsp_names / dsp_ids)` | A create books ONE DSP: a brief with several DSP rows and "one deal per DSP" books one deal per DSP. To put more DSPs on one deal, add them afterwards with the `merge_buyers` update. Cannot remove the last DSP. Amazon DSP books through Bidswitch plus the Amazon seat. |
| Buyer seats | API | API | `nx_merge_deal_buyers(seat_ids)` | Free text, not validated until bid time; required for Bidswitch. |
| Geo: country / region / DMA / zip | API | API | `nx_merge_deal_geo(level, direction)` | Include **or** exclude per level; flipping needs `replace`. DMAs US only; max 2,000 zips. |
| Geo: city / radius / MSA | No | No | | Not on Nexxen. |
| Publishers / app or site ids | API (attach an existing Universal Media List) | API | create: brief `nexxenConfig.mediaLists / mediaListsExclude` (every Nexxen deal) or per deal `nexxenFormatSupply.mediaLists / mediaListsExclude`; update: `nx_merge_deal_domains(media_lists=…)` | A deal's own non-empty side replaces the campaign side for that deal (a warning names any campaign list it leaves off). Names or ids, resolved exactly against the live list catalog. Domain, publisher-id and placement-id lists all work (the tool sends each list's `universalMediaList` reference). List contents are console-only; our tools never create or edit lists. |
| Domain / app-bundle allow or block | API | API | `nx_merge_deal_domains(list_type)` | One inline list per side; domains and bundles share it. Include side = "buy only these". A chat create ships a site list AND an app-bundle list together (`domain_file_path` + `app_bundle_file_path`, each with its own list type): same type = one inline member, an allowlist and a blocklist = both sides. A site allowlist beside an app-bundle blocklist (or the reverse) is refused (`list_direction_mix`: the include side admits only its values); a second list of one kind is refused (`list_multiple_per_kind`); a list the deal's inventory cannot reach is reported `list_not_applied`. |
| Channel / impression type | API | Create only | | Change = `nx_clone_deal` (new deal ID). |
| Ad sizes (banner), video sizes | API | API | `nx_merge_deal_format(dimension=ad_size / video_size)` | |
| Device, media type (site / app) | API | API | `nx_merge_deal_tech` | Include only. |
| OS, browser, carrier, device make | DSP | DSP | | Not on Nexxen deals. |
| Audience segments | API | API | `nx_merge_deal_segments(segment_type=audience)` | OR within a group, AND across groups, one exclude group. An "add" joins an existing group (widens the audience); starting a new AND group (`add_group`) narrows it. In the chat plan a re-run is safe (an identical group already on the deal is skipped); a scheduled run calling `nx_merge_deal_segments` directly has no such check and would add a duplicate group, so don't repeat it there. Segment data CPM is billed on top. |
| Content genre / rating / language | API | API | `nx_merge_deal_segments(segment_type=contentgenre / contentrating / contentlanguage)` | Nexxen's own 23 genres, not IAB. |
| IAB category, keywords, page URL, key-values, series / title | DSP | DSP | | Not on Nexxen deals. |
| Livestream | API | API | `nx_update_deal(livestream)` | Video / audio. Live sports is set up by Nexxen on request. |
| Viewability % minimum | API | API | `nx_update_deal(viewability_percentage)` | Banner / native / video. |
| CTR %, VCR % minimum | API | API | `nx_update_deal(ctr_percentage, vcr_percentage)` | Supply filters, not goals. VCR on video / audio. |
| OM SDK, VPAID, MRAID, SIMID, COPPA, supply-chain hops | API | API | `nx_update_deal` | COPPA / VPAID / MRAID not live-proven. |
| Brand safety: premium source rating | API (always "premium") | No | | Every create sends `sourceRatingTarget: "premium"`, as the console does. The other values are not offered: what they do is unverified (open vendor question). No IVT or pre-bid vendor segment knob → DSP. |
| **Dayparting** | API | API | `nx_update_deal(dayparting=[{days, start_hour, end_hour}])` | Whole hours; **replaces the whole schedule** (read first, send the full list). Time zone unverified. |
| **Frequency cap** (daily, monthly) | API | API | `nx_update_deal(frequency_cap_daily, frequency_cap_monthly)` | The only SSP here with a deal-level frequency cap. Hourly / lifetime caps → DSP. |
| **Impression / request caps, request pacing** | API | API | `nx_update_deal(daily_impression_cap, overall_impression_cap, daily_request_cap, request_pacing)` | Delivery caps, not a spend budget. Pacing needs a daily request cap. |
| Budget / KPI goal | DSP | DSP | | |
| Video slots (pre / mid / post-roll), skippable, content length | API | API | `nx_update_deal` | At least one slot must stay on. |
| Video placement type, playback method | API | API | `nx_merge_deal_format` | |
| Supply max ad duration (minimum) | API | API | create: brief `nexxenFormatSupply.maxVideoDurationMinimumSecs`; update: `nx_update_deal(max_video_duration_minimum_seconds)` | Nexxen "Max Video Ad Duration (Minimum)": only supply whose MAXIMUM allowed ad length is at least N seconds (so a 30 s spot can serve). Video and audio deals; whole seconds ≥ 1. |
| Ad-length targeting | DSP | DSP | | `max_video_duration_minimum_seconds` is **not** ad-length targeting; a brief's allowed / max ad durations are reported as not applied. |
| Deal sync (TTD / DV360) | UI | UI | | |

## Watch-outs before recommending a change

- **Floor arithmetic:** the floor already includes margin and segment data
  CPM is added on top, so the buyer must clear floor + the dearest segment.
- **Replacing tools:** `nx_update_deal` replaces the whole DSP / seat lists
  and the whole daypart schedule; prefer `nx_merge_deal_buyers`.
- **1-year cap** on any end date.
- Impression type and the NXN- id are fixed; a format change is a clone with
  a new deal ID the buyer must re-target.

## Diagnostics (read tools)

- `nx_get_deal` — live config.
- `nx_run_report` / `nx_run_report_from_prompt_inputs` — requests, bids,
  wins, impressions, revenue, segment fee, margin, platform fee, eCPM, VCR,
  CTR, VTR; by deal, DSP, country, device, imp type, media type, domain,
  publisher. 10 calls a minute.
- `nx_list_segments` — segment data CPMs.
- No forecast / avails through the API (console only).

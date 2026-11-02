# TripleLift: deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the TripleLift
console · **DSP** = buyer's line item only · **No** = not possible / blocked
for us · **Create only** = fixed after booking (change = clone to a new deal).

## How changes are applied

- Chat update plan: `update` -> `tl_update_deal_lifecycle` (name, dates,
  status; both dates together); the merges for geo, tech, format, flat
  segments, supply domains, inventory and content; `merge_pricing` ->
  `tl_update_deal_fee` (floor and price type only); `merge_audience_groups`
  -> `tl_update_audience_groups` (one edit of one audience group or the
  exclusion set per change).
- Not in the update plan: the curation fee (the typed fee is gated), and
  advertiser domains (`tl_merge_deal_domains` refuses every deal: the
  controls are never returned by a read).
- Fields only writable through a raw `tl_update_deal` payload (curation fee,
  DSP, channel, formats, budget, KPI) have **no permitted path**: raw writes
  are forbidden in chat and in scheduled update runs.
- Every merge reads the deal, changes one leaf and writes the whole tree back.
  A raw `tl_update_deal` targeting tree is a full replacement — avoid it.
- The update preview blocks a change that would leave a country outside a
  targeted first-party segment's countries, and warns when the write would
  reset `creativeTags`.
- **Reads lag 1–2 minutes.** A result with `written=true, retryable=false`
  means reconcile, never resubmit: the plan reports it `unclear`
  (`tl_read_lag`); run it again after ~2 minutes and it re-reads the deal
  first. A preview within ~3 minutes of a plan write to the same deal warns
  that its "before" values may be stale.
- **A create is read back before it counts.** If TripleLift stored anything
  other than what was sent (price, dates, fee, buyer / seat, targeting, ...),
  the booking fails loud with `tl_readback_mismatch`, naming each field as
  sent and as read back. The deal exists and keeps its id: the result card
  row is "didn't match on read-back", it is never retried or created again;
  fix the field on the deal (an update plan or the console).
- **An update is read back too**, every field it sends (targeting, buyer /
  seat, goals, formats as well as name, dates and price). A field TripleLift
  holds differently fails loud (`tl_update_not_applied`, sent vs held) and is
  never sent again; one the read does not show is reported unverified.

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / inactive) | API | API | `tl_update_deal_lifecycle(status)` or `tl_toggle_deal_status` | No delete; pause instead. |
| Start / end date | API (both required) | API | `tl_update_deal_lifecycle(start_date, end_date)` | Both dates together. No open-ended deals. |
| Name | API | API | | |
| Description, external ID, labels | No | No | | |
| Price type (floor / fixed / ceiling) and price | API | API | `merge_pricing` -> `tl_update_deal_fee(floor_price, deal_price_type)` | USD only. No auction-type concept. |
| Curation fee | API (required; percent of media only) | No | | Create: the brief's Curated deal fee books as a percent of media (fee model 3) with no cap. A CPM or flat fee type is refused (`fee_type_wire`), and a typed fee (`pricingBuyers.triplelift.curationFeeType/Value/Cap`) is refused (`pricing_vocab_unverified`): no CPM fee or cap has been proven on TripleLift's create. Update: the typed fee update is gated (never live-proven on an existing deal), and the raw-payload route is forbidden in chat and in scheduled update runs. A fee change is refused as a whole, even next to a floor change. A create can come back `FEE_CREATION_ERROR`: the deal is live with no fee. |
| Post-auction discount | No | No | | TripleLift-internal only. |
| DSP + seat | API (exactly one of each) | No | | Raw payload only (not permitted). More buyers = more deals (clone). |
| Country | API (include and exclude) | API | `tl_merge_deal_geo(level=country)` | Every country must be in **every** targeted first-party segment's country list, or TripleLift rejects the write. |
| Region / state | API (include and exclude) | API | `tl_merge_deal_geo(level=region)` | ISO codes such as `US-CA`. |
| DMA, postal | API (include only) | API | `tl_merge_deal_geo(level=dma / postal)` | |
| City, MSA, radius | DSP | DSP | | Not on TripleLift. |
| Publisher / placement | UI | UI | | Our tools refuse it (unproven). |
| Seller category (e.g. Women Owned, Green Media), directness | API (include) | API | `tl_merge_deal_inventory` | |
| Key-values | API (include only) | API | Create: brief `pricingBuyers.triplelift.keyValues` -> `key_value_pairs`. Update: `tl_merge_deal_inventory(level=key_value)` | Include only (`is_any_of`; `is_none_of` is refused, `tl_key_value_exclude_unsupported`). Each value is one key=value pair. |
| App-name keywords, GPID, saved inventory lists | UI | UI | | |
| Supply domain allow / block | API | API | `tl_merge_deal_supply_domains` | Allow and block lists can coexist. A chat create merges every list the deal names after the create (one step per list; CTV app-bundle lists through `tl_merge_deal_inventory`, app bundles off CTV reported `list_not_applied`). Sites and CTV app bundles share one leaf, so a site allowlist beside an app-bundle blocklist is refused (`list_direction_mix`). |
| App bundles | API (CTV only) | API | `tl_merge_deal_inventory(level=app_bundle)` | Shares the domain field; refused on web deals. |
| Advertiser domains, brands, creative IAB categories (`brandAndCreativeControls`) | No (raw create only, not permitted) | No | | The raw `tl_create_deal` can send these creative-side controls (chat sends them empty), but chat cannot: no read returns `brandAndCreativeControls`, so nothing could verify them, `tl_merge_deal_domains` refuses every deal and the update plan does not carry them. Whether the console can change them is unverified. |
| Channel (web / CTV) | API | No | | Raw payload only (not permitted). Clone to change it. |
| Commercialized formats (incl. in-stream vs out-stream) | API (required) | No | Create: per deal `inventoryTargeting.triplelift.commercializedFormats`, else the brief-wide `tripleliftConfig.commercializedFormats`, else the channel's (Display: DISPLAY, IMAGE; OLV: OUTSTREAM, INSTREAM; CTV / OTT: INSTREAM; Native: DISPLAY) | Set them per deal on a brief that mixes Display and video deals: the brief-wide pick applies to every TripleLift deal. Update: raw payload only (not permitted). Clone to change it. |
| Environment: web inventory | API (web channels) | API | Create: the deal's Environment `Web Only` (or tech Environment `Web`) -> `inventory_type` 0. Update: `tl_merge_deal_tech(level=inventory_type)` | Refused on a CTV deal (`tl_inventory_type_channel_conflict`). |
| Environment: in-app inventory | No | No | | TripleLift's App inventory-type value is unknown (`tl_inventory_type_app_unknown`). An In-App deal books with a warning (`environment_not_applied`) and serves every environment; restrict it on the DSP line. |
| Ad sizes | API | API | `tl_merge_deal_format(level=size)` | Include only. |
| Interstitial, rewarded | DSP | DSP | | |
| Device type | API (include only, within the channel) | API | Create: tech include `device` (Desktop, Phone, Tablet, Connected TV) narrows `device_types`; blank = the channel's devices. Update: `tl_merge_deal_tech(level=device_type)` | Web channels book Desktop, Phone, Tablet; CTV books Connected TV; CTV and OTT all four. A device outside the channel is refused (`tl_device_type_channel_mismatch`). |
| OS, browser, manufacturer | API (include only) | API | `tl_merge_deal_tech` | No excludes — rewrite "exclude iOS" as an include list. |
| Carrier, API framework | DSP | DSP | | |
| Audience segments (flat include) | API | API | `tl_merge_deal_segments` | Refused on grouped audience trees. |
| Audience AND-of-OR groups, exclusions | API | API | `merge_audience_groups` -> `tl_update_audience_groups(action, group_index, segment_ids, group_segment_ids)` | One edit per change: `add_to_group`, `remove_from_group`, `add_group`, `remove_group`, `exclude_add`, `exclude_remove` (always named). Every group-addressed edit names the target group's exact current segments (`remove_group`: `segment_ids`; `add_to_group` / `remove_from_group`: `group_segment_ids`, from the preview's Audience groups line), so it never lands on another group; if the group changed since planning the change is refused and must be re-planned. A `remove_from_group` that would empty a group is refused (use `remove_group`). Same country-binding rule as geo. |
| Content language | API | API | `tl_merge_deal_content(level=language)` | Include only. |
| Content genre, rating, network, channel, series, title | UI | UI | | Our tools refuse them. |
| IAB category | No | No | | The create flow has no IAB binding (it reports `iab_not_applied` and sends none), raw creates are not permitted, and it is not readable after creation. Use content-language, regulatory policy or quality packages instead, or ask the buyer to target categories in the DSP. |
| Regulatory policy (political, cannabis, gambling, drinking age) | API | API | Create: `tripleliftConfig.allowPoliticalAds` / `allowCannabisCbd` / `allowGambling` / `allowDrinkingAgeAudience` (true allows the category; off unless set). Update: `tl_merge_deal_content(level=regulatory)` | Brief-wide: they apply to every TripleLift deal of the brief. Never guessed: a value other than true / false is refused (`tl_policy_flag_invalid`). The cannabis, gambling and drinking-age category ids are UI-observed only (no API write + read-back yet), like political: the create's read-back reports a deal that did not store them as unverified, so confirm the first live booking of each with `tl_get_deal`. |
| Creative tags, DSP format workflow | No (chat books `false` / none) | No | | Not settable in chat. No read returns `creativeTags`, and any write may reset it to off. |
| Viewability / quality "performance packages" (e.g. IAS viewability > 70%) | API | API | `tl_merge_deal_format(level=performance_package)` | Curated packages, not a numeric threshold. |
| Inventory-safety block segments | API | API | `tl_merge_deal_inventory(level=blocklist)` | Exclude only. |
| IVT / other pre-bid vendors | DSP | DSP | | Only what TripleLift's catalog offers as packages / block segments. |
| Dayparting | UI (CTV only) | UI (CTV only) | | Otherwise DSP. |
| Frequency cap | DSP | DSP | | |
| Video duration (one range) | API | API (replace / remove) | `tl_merge_deal_format(level=duration)` | |
| Companion ad | API (on only) | API | `tl_merge_deal_format(level=companion)` | |
| Skippable, playback, position, player size, aspect ratio | DSP | DSP | | |
| Budget (flat USD cap) | API | No | | Raw payload only (not permitted). No pacing or intervals. |
| Pacing / impression goals | DSP | DSP | | |
| Objective / KPI goal ids | API (numeric ids) | No | | Raw payload only (not permitted). Effect on delivery unverified. |

## Watch-outs before recommending a change

- **Include-only tech, DMA, postal, sizes and language.**
- **Countries vs segments**: check segment country lists before adding a
  country.
- **One DSP per deal.**
- **Any write may reset `creativeTags`** to off (no read returns it).
- **Avails ignore targeting**: `tl_get_avails` is a platform-wide total per
  format, not a forecast.

## Diagnostics (read tools)

- `tl_get_deal` — full envelope (targeting, quality profile, fee, deal type).
- `tl_run_report_from_prompt_inputs` / `tl_advertiser_deals_report` — per-deal
  spend, impressions, clicks, CTR, CPC, eCPM, bid requests / responses.
- `tl_run_async_site_report_to_csv` — by domain, no row cap, adds wins, win
  rate, render rate, video starts / completions, VCR. Prefer it over the sync
  site report (5,000-row cap) and the email report (blank above 10 MB).
- A seat with no deals returns empty rows rather than an error.

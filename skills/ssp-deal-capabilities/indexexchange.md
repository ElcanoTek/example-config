# Index Exchange (IX): deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the IX console ·
**DSP** = buyer's line item only · **No** = not possible / blocked for us ·
**Create only** = fixed after booking (change = clone to a new deal).

The **Create** column is the chat booking flow (intake card -> `compile_deal_plan` ->
`ix_execute_deal_from_prompt_inputs`); the brief field that sets a knob is in brackets.

## How changes are applied

Every change below goes through the chat update plan (intake card → plan →
approval card), one deal per plan item:

- `update`: `ix_update_deal` (name, dates, floor, auction type, status,
  reporting labels, the marketplace-margin fee, DSP, the whole seat list).
- `update_fee`: `ix_update_deal_fee`, the Transactions API deal fee row
  (percent, deal scope), shown on the card as **Transactions fee**, never
  mixed with the `update` op's **Fee**.
- Per-dimension merges with `merge_mode` `add`, `remove` or `replace` (the
  plan always asks for the mode and, where there is one, the side; it never
  defaults either): `merge_domains`, `merge_key_values`, `merge_geo`,
  `merge_language`, `merge_tech`, `merge_format`, `merge_content` (genre,
  rating, network / channel, IAB category, livestream), `merge_blocked_genres`
  (`add` / `replace`), `merge_schedule` (dayparting windows),
  `merge_segments` (plus `add_set`, a new AND-ed group), `merge_publishers`,
  `merge_ad_durations`, `merge_inventory` (existing lists, Deals with
  Publishers, supply balancing) and `merge_seats` (`replace` with no seats
  clears them all).
- The preview reads the live deal and shows the current value of each change
  as "before"; a change the deal already has is shown as "no change" and is
  skipped (nothing sent), so a plan run again after an unclear result never
  applies a change twice.
- **Not in the plan**: segment `remove_set` and a `remove` aimed at one
  group by position (positions shift once a group is dropped, so a retry
  could hit another group: use the IX console), app bundles (create
  only), creating or editing inventory lists (shared by other deals), a CPM
  or global / publisher-scope Transactions fee, and raw `targeting` through
  `ix_update_deal` (it replaces the whole targeting array).
- A `*_not_applied` result, or a write that answered but whose read-back
  failed, comes back **unclear**: the write already went out, so reconcile,
  do not retry.

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / paused) | API (created active) | API | `ix_update_deal(status="A" / "P")` | No archive through our tools. |
| Start / end date | API | API | `ix_update_deal(start_date, end_date)` | An end date is mandatory; IX cannot do open-ended. UTC days. |
| Name | API | API | `ix_update_deal(name)` | IX strips apostrophes. |
| External deal ID | API (`scheduleLifecycle.ix.externalDealId`) | Create only | | |
| Reporting labels (advertiser, agency, salesperson, external reference, custom) | API (`ixReportingLabels`, else the client's label template) | API (replaces the labels sent) | `ix_update_deal(labels)` | Only letters, digits, spaces and `$%&,-+./:?@\_{}` are safe. Other characters (`' = ! # ( ) –`) stop traders saving **any** edit in the IX console. |
| Auction type (first / fixed) | API (`ixConfig.auctionType`) | API | `ix_update_deal(auction_type)` | Fixed = the winner pays the floor. |
| Floor | API | API | `ix_update_deal(floor)` | $0.10 minimum. |
| Floor currency | No | No | | |
| Curation fee (marketplace margin) | API, required: percent (`curatedDealFee`, Fee type Percentage of Media) or USD CPM (Fee type Fixed CPM → `fee_type="cpm"`, marginCalculationType C). Flat Fee: No | API | create: `margin_percent` (+ `fee_type`); update: plan `fee` (percent) or `fee_cpm` on `ix_update_deal` → `marketplace_configurations` margin | The fee is **never returned on read**, only on the create response: the create checks that echo (a different fee fails the create as written, `ix_fee_not_applied`; a CPM fee IX did not echo is a warning). Afterwards verify in reporting (`marketplace_total_fee`); the update card shows no "before". A deal's `pricingBuyers.ix.feeType` must agree with the campaign Fee type (`fee_type_conflict`). Our sources disagree on whether IX applies a margin change sent after create (see next row). |
| Curation fee (Transactions API deal fee) | No | API (`update_fee`, card label "Transactions fee") | `ix_update_deal_fee` (percent, deal scope) | Live-proven 2026-09-17. The preview shows the current fee row as "before". IX refuses it on a deal with no deal fee row yet (account default). It writes a deal-scope fee row through IX's Transactions API, which may be a separate fee from the marketplace margin above. Until a house-seat test settles how the two interact, say which one you mean and check reporting afterwards. |
| DSP | API (one per deal) | API, unverified | `ix_update_deal` DSP field | Whether IX accepts a DSP change on a live deal is unproven; a new deal is safer. |
| Buyer seats | API (the DSP row's seat + `pricingBuyers.ix.additionalSeatIds`) | API: add / remove / replace (`merge_seats`; `replace` with no seats clears them all), or the whole list on `update` | create: `seat_name` + `seat_ids`; `ix_merge_deal_seats`; `ix_update_deal` seat ids | IX's create API refuses a hyphenated seat id (e.g. `562277-5529`); the create tool books the deal without it, attaches the whole seat list by a follow-up PATCH (which accepts it) and reads it back (`ix_seat_attach_not_applied` if the list does not read back). Seat edits may reset the fee — see watch-outs. |
| Country | API (include and exclude) | API (`merge_geo`) | `ix_merge_deal_geo(country)` | |
| State / region, DMA | API (include **or** exclude) | API (`merge_geo`) | `ix_merge_deal_geo(state / metro)` | Ambiguous names (Georgia) are refused. |
| Postal / ZIP | API | API (`merge_geo`) | `ix_merge_deal_geo(zip)` | |
| City, radius | DSP | DSP | | Not on IX. |
| Publishers | API (`ixConfig.publisherEntries`) | API (`merge_publishers`) on deals without direct publishers; UI on deals booked with them (the preview shows those as blocked) | `ix_merge_deal_publishers` (include or exclude) | Our create writes direct publishers (`publisherIDs`), which the merge tool refuses to touch; on those deals publisher changes are console work. Read the deal first to see which kind it has. |
| Deals with Publishers | API (`inventoryTargeting.ix.dealsWithPublishers`) | API (`merge_inventory`) | `ix_merge_deal_inventory(deals_with_publishers)` | Include only. |
| Inventory lists (existing) | API (`inventoryTargeting.ix.inventoryListIds`) | API (`merge_inventory`, attach / detach) | `ix_merge_deal_inventory(inventory_list)` | Our role cannot create lists. Editing a list changes every deal using it. |
| Domain allow / block | API (a site list file) | API | `ix_merge_deal_domains` | Include and exclude may not share values. Batchable. |
| App bundles | API (an app-bundle list file) | Create only | `app_bundle_file_path` at create (a list file; there is no inline bundle argument) | IX's app-bundle key is set only at create; `ix_merge_deal_domains` writes the domain key, never app bundles. Changing the bundles means a new deal. An existing app-bundle **inventory list** can still be attached / detached (see Inventory lists). Non-reverse-DNS CTV ids (Fire TV, Samsung, Xbox) need care. A chat create ships a site list AND an app-bundle list together (a Display / OLV / Native deal with All inventory, an OTT deal) into the one key-120 list: same direction = one set, an allow and a block list = an include and an exclude set. A site allowlist beside an app-bundle blocklist (or the reverse) is refused (`list_direction_mix`: IX checks sites and apps against one list, so the allowlist would shut out the other kind); a second list of one kind is refused (`list_multiple_per_kind`); a list the deal's inventory cannot reach (a site list on CTV, an app list on Web Only) is reported `list_not_applied`. |
| Ad format / sizes, interstitial, rewarded | API (`formatInclude`; an exclude is blocked) | API (`merge_format`) | `ix_merge_deal_format` | Include only. |
| Environment (in-app / web), device type | API (`inventoryType`, `techInclude`; an exclude is blocked) | API (`merge_tech`) | `ix_merge_deal_tech` | Include only on the house catalog. |
| OS / browser | API (`techInclude`; an exclude is blocked) | API (`merge_tech`) | `ix_merge_deal_tech` | Exact names ("Windows 11"). |
| Carrier, device make, API framework | DSP | DSP | | Not on IX. |
| Audience segments | API (up to 4 AND-ed include groups + 1 exclude group) | API (`merge_segments`: add / remove / replace, `add_set` for a new group); removing a whole group: UI | `ix_merge_deal_segments` | Removing from an exclude set after creation is not live-proven. Group-by-position removal (`remove_set`, `remove` with a group index) is not planned: a retry could hit another group. |
| Key-values | API (`pricingBuyers.ix.keyValues`) | API | `ix_merge_deal_key_values` | Registered keys only. |
| Genre, IAB category, content rating | API, include and exclude (`iabCategories`, `contentTargeting.ix`) | API (`merge_content`, or `merge_blocked_genres`) | `ix_merge_deal_content` (or `ix_block_deal_genres`) | |
| Network / channel, livestream | API, include only (`contentTargeting.ix`; a network/channel exclude or "not live" is blocked at compile, `ix_activation_type_violation`) | API (`merge_content`) | `ix_merge_deal_content` | Livestream takes no values: include restricts the deal to live content, and `merge_mode` `remove` takes that restriction off. Both keys are include-only, so an exclude is refused. |
| Content language | API (`language`, `contentTargeting.ix.contentLanguage`) | API (`merge_language`) | `ix_merge_deal_language` | The tool defaults to replace; the plan always asks for the mode. |
| Series / title / episode | No | No | | Not wired. |
| Viewability, VCR, CTR minimums | API (`viewabilityTarget`; `vcr` on a video deal, with or without a format pick; `ctrTarget`) | API (`merge_format`) | `ix_merge_deal_format` | Supply filters, include only. No VTR. A VCR target on a display deal is reported NOT APPLIED. |
| Brand safety / IVT / IAS / DV pre-bid | Via data segments only | API (`merge_segments`) | `ix_merge_deal_segments` | No dedicated setting. |
| **Dayparting** | API (`scheduleLifecycle.ix.scheduleParts`) | API (`merge_schedule`) | `ix_merge_deal_schedule` | Absolute `{start, end, tzID}` windows (local time, IANA time zone), not a weekly grid. "Weekdays 6–10pm for a month" = one window per day. One deal per call. |
| Video max ad duration | API (`adDurations`; a bare `maxAdDurationSecs` is refused until the buckets are named, `arg_unresolved`) | API (`merge_ad_durations`) | `ix_merge_deal_ad_durations` | Account-specific buckets. |
| Video placement, start delay, skippable, playback, aspect ratio, min player size | API (`formatInclude`, `playerMinWidth` / `playerMinHeight`) | API (`merge_format`) | `ix_merge_deal_format` | Include only. |
| Supply balancing (publisher %) | No | API (`merge_inventory`), unverified | `ix_merge_deal_inventory(supply_balancing)` | Not pacing. |
| Frequency cap | DSP | DSP | | |
| Budget / pacing / impression / KPI goals | DSP | DSP | | |
| Deal sync to DSP | No | No | | |

## Watch-outs before recommending a change

- **The marketplace margin is invisible on read**, and seat, DSP or
  supply-balancing edits rewrite the marketplace configuration without it —
  the fee may fall back to the account default (unverified). The plan flags
  those changes (`ix_margin_may_reset`); after any of them, check
  `marketplace_total_fee` in reporting.
- **Two fee routes, one open question**: the `update` op's Fee (marketplace
  margin) and `update_fee` (Transactions fee row) may or may not be the same
  fee. Say which one you mean and check reporting afterwards.
- **Clones lose the fee** unless the margin is passed explicitly.
- **Most excludes are not available** (device, OS, browser, environment,
  format, quality, network, livestream are include only on the house
  catalog). Narrow the include list instead. The chat create blocks every one
  of these excludes: it cannot read an account's catalog, so even on an
  account whose catalog does take excludes it refuses them; the merge tools
  decide from the live catalog, so add such an exclude after create.
- **At least one include must remain**; removing the last one is refused.
- **Bad label characters lock the deal** for console editing.

## Diagnostics (read tools)

- `ix_get_deal_settings` — live config (no fee). Takes the internal id, or `deal_id=` the public deal ID (looked up; one exact match).
- `ix_run_marketplace_draft_report`:
  - `deal_summary` — the bid funnel (requests, errors, timeouts, passes,
    blocked, **below floor**, eligible, chosen, bids) plus impressions, media
    spend and fee; by day, deal, DSP, device, channel, creative type. A high
    "below floor" count is the clearest signal to lower the floor or ask the
    buyer to bid up.
  - `supply_breakdown` — by publisher, site, domain, country, device.
  - `segment_performance`, `deal_labels`.
- No forecast / avails tool (console estimate only).

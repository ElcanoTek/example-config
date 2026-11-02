# Xandr (Microsoft Curate): deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the Curate
console · **DSP** = buyer's line item only · **No** = not possible / blocked
for us · **Create only** = fixed after booking (change = new deal).

A curated Xandr deal is three objects: the **deal** (status, dates, floor,
buyer), the curated **line item** (margin, ad types, flight windows) and the
**profile** (targeting). Read them together with `xandr_describe_deal`; never
judge a deal from `get_xandr_deal`, which returns the bare deal only.

## How changes are applied

Every change below goes through the chat update plan (intake card → plan →
approval card), one deal per plan item:

- `update`: `update_xandr_deal` (name, dates, floor, status, auction type,
  priority, currency, description, payment type, use-deal-floor). The fee is
  not an `update` field: use `merge_margin`.
- Per-dimension merges with `merge_mode` `add`, `remove` or `replace` (the
  plan always asks for the mode, it never defaults one): geo (countries,
  states, cities, metros, postal), deal lists, segments, supply types, device
  types, OS, browsers, languages, device models, carriers, sizes.
- `merge_margin` (curator margin, % or CPM, on the line item),
  `merge_content` (IAB categories, video genre / rating / network),
  `merge_video` (player context, playback, size, framework),
  `merge_inventory` (publishers, placements, url_lists attach / detach,
  seller deals, seller categories), `merge_inventory_settings` (URL allowlist
  applies to RTB / managed / deals), `merge_quality` (viewability, VCR,
  ads.txt-only, intended audience / COPPA), `merge_video_duration`,
  `merge_ad_types`, `merge_segment_group` (`add` / `remove` / `replace` on a
  group, or `add_group`). Despite the `merge_` names, the single-value ones
  take no merge mode.
- **Not in the plan:** removing a whole segment group (`remove_group` names a
  group by position, so a retry could delete the wrong one: UI), line-item
  flight windows (UI), the buyer (No), and labels / comments, dayparting and
  frequency caps (gated in the tools: UI).
- Only multi-deal batch runs are replace-only, and the plan never makes one.

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / inactive) | API | API | `update_xandr_deal(active)` | Changes the deal only, not the line item (see watch-outs). Archive / delete: UI. |
| Start / end date | API | API | `update_xandr_deal(start_date, end_date)` | Changes the deal dates only. Cannot clear an end date. |
| Line-item flight windows | API (one window) | UI | Curate console | Not in the chat plan: our tool can only add a window and refuses any result with more than one, so it can never move a flight. |
| Name, description | API | API | `update_xandr_deal` | Renames the deal, not the line item. Chat booking: the description is "<brand> — <theme>" unless the deal sets `pricingBuyers.xandr.description` (free text). |
| Deal code | API | Create only | | |
| Deal type | API (Curated only) | Create only | | `xandrConfig.dealType`; only Curated (id 5) shows in Curate and carries the curator line item, so a chat booking refuses any other (`xandr_deal_type_unsupported`). |
| Labels, comments, external ID | UI | UI | | Gated in our tools; refused in the chat plan. Our create has no external-id field: a deal's `externalReferenceId` is reported, not booked (`external_id_not_applied`). |
| Auction type | API | API | `update_xandr_deal(auction_type)` | |
| Floor (ask price) | API | API | `update_xandr_deal(ask_price, use_deal_floor)` | Updates the deal floor only; the line-item floor written at create is not changed (the review card warns when they differ). Which one Xandr enforces is unverified. |
| Fixed price (revenue type / value) | API | UI | Curate console, or book a new deal | Chat booking: `xandrConfig.revenueType` `cpm` books the line item at the deal CPM (`revenue_value`, also the deal ask price); `vcpm` (default) floors at it. A fixed-price deal with no CPM is refused (`xandr_fixed_price_required`), and so is any other revenue type (`xandr_revenue_type_invalid`). Our tools cannot change it on a live deal. |
| Priority | API (tool) | API | `update_xandr_deal(priority)` | 1-20 (20 highest); Xandr applies it to Private Auction deals. A chat booking is always Curated, so `pricingBuyers.xandr.priority` is refused (`xandr_priority_unverified`) rather than booked with no known effect. The create tool takes a typed `priority` for a direct call; a read-back that differs fails the create loud with the deal kept (`xandr_priority_not_applied`). |
| Currency | API (USD only) | API | `update_xandr_deal(currency)` | A curated deal is priced in its advertiser's currency, USD for every Curate advertiser we book under. Chat booking: `xandrConfig.currency` blank or USD; any other is refused (`xandr_currency_unsupported`). |
| Payment type | API | API | `update_xandr_deal(payment_type)` | |
| Curator margin | API (required): Percentage of Media (`margin_percent`) or Fixed CPM (`margin_cpm`, the fee in dollars) | API | `merge_margin` → `xandr_update_deal_lifecycle(margin_percent / margin_cpm)` | Lives on the line item; the review card shows the live margin. A Fixed CPM fee is the typed fee only (a client's percent default never stands in; the card's Fee row reads `$<fee> CPM`). Flat Fee: No (`fee_type_wire`). Some members cannot set a curator margin at all. |
| Insertion order + advertiser | API | Create only | `xandrConfig.insertionOrder` (+ `createInsertionOrderIfMissing`, `advertiser`) | Every deal's line item sits under an IO, and the IO's advertiser is who it bills to. The IO is matched by exact name on the deal's own member. With "Create if missing" on, a missing IO (and a missing named advertiser) is created first, seamless and open-ended like the Curate UI makes it; an ambiguous, inactive or other-advertiser match is refused. Chat booking: the flag must be a real true/false (`insertion_order_args_invalid`), and an advertiser given with the flag off is reported unused (`advertiser_not_applied`). Moving a live deal to another IO: UI. |
| Buyer (DSP member) | API (exactly one) | Create only | | Changing the buyer is a new deal. |
| Buyer seat (e.g. Amazon via bidder) | API (exactly one) | Create only | | Multiple seats: UI at create. |
| Geo: country / region / city / DMA / postal | API (include **or** exclude per level) | API | `xandr_merge_deal_geo_*` | Postal ≤ 4,000 codes, one country. |
| Publishers, placements | API (numeric ids) | API | `merge_inventory` (`publishers` / `placements`) | No catalog lookup; exclude not proven. |
| Seller member targeting | UI | UI | | |
| Deal lists, specific seller deals | API | API | `merge_lists`; `merge_inventory` (`seller_deals`) | Seller deals: include only. |
| Domain / app allow-block lists | API (existing list ids) | API | `merge_inventory` (`url_lists`), `merge_inventory_settings` | Attach / detach an existing list on the deal; raw domains are refused and creating or editing a list is UI. Settings: whether the allowlist applies to RTB / managed / deals. A chat create cannot book a list FILE: every site or app-bundle list the deal names is reported `list_not_applied` (name a Curate deal list in `xandrConfig.dealListNames` instead). |
| Ad types (banner / video / native) | API | API | `merge_ad_types` → `xandr_update_deal_ad_types` | Replaces the whole list; refuses to drop video while video targeting is set. |
| Sizes | API | API | `xandr_merge_deal_sizes` | Include only. |
| Supply type, device type, OS, browser, language, device model, carrier | API | API | `xandr_merge_deal_*` | One direction per dimension. |
| Audience segments (flat OR) | API | API | `merge_segments` | Refused on profiles with 2+ groups. |
| Audience AND-of-OR groups | API | API | `merge_segment_group` | One group per item: `add` / `remove` / `replace` segments in a group, or `add_group` (skipped when an identical group is already there). Removing a whole group: UI. |
| IAB content categories | API (include and exclude) | API | `merge_content` (`content_categories`) | Xandr's own category names / ids. |
| IAB allow unknown (serve where the category is unknown) | API | No (kept as booked) | | Chat booking: `contentTargeting.xandr.iabAllowUnknown: true` (default false). It rides the content-category targeting, so it books only beside an IAB or seller category; alone it is reported (`iab_allow_unknown_not_applied`). The update merge keeps the deal's value. |
| Seller custom categories | API (include) | API | `merge_inventory` (`seller_categories`) | Include only, values typed in (no list file). |
| Video content genre / rating / network | API | API | `merge_content` (`genre` / `content_rating` / `network`) | One direction per list. Delivery type, program type, content duration are gated: UI. |
| Viewability %, VCR % | API | API | `merge_quality` | VCR video only. Null clears. A chat create books both whether or not the deal has a Format & video selection, as a whole percent (0.7 = 70%, rounded half up); below 1% (before rounding) is refused (`xandr_quality_pct_invalid`), above 100% too (`viewability_out_of_range` / `vcr_out_of_range`). VCR on a banner or native deal is reported (`vcr_not_applied`). |
| ads.txt-only, COPPA | API | API | `merge_quality` (`ads_txt_authorized_only`, `intended_audience`) | COPPA is the `children` intended audience (what Curate's COPPA box writes); create and update both book it that way. Audiences: `general`, `children`, `young_adult`, `mature`. |
| IVT / IAS / DV pre-bid | DSP | DSP | | No field (unless such segments exist on the member as Xandr segments). |
| Video duration | API (minimum only) | API | `merge_video_duration` | Minimum only; null clears. Max / exact length → DSP. |
| Video player (context, playback, size, framework) | API (video deals) | API | `merge_video` | Not live-proven. Chat booking translates the card's words to the tool's tokens: placement Instream (pre/mid/post-roll), Outstream (in-article, in-banner, in-feed, interstitial), Both (all seven), or one context (Pre-roll ... Accompanying post-roll); playback Auto-play sound on / off / unknown, Click-to-play, Mouse-over; size Small / Medium / Large; framework VPAID 1.0 / 2.0, MRAID 1 / 2, ORMMA. Any other value is refused on its field (`xandr_video_context_unresolved`, `xandr_playback_method_unresolved`, `xandr_player_size_unresolved`, `xandr_video_framework_unresolved`), and so is video targeting on a banner or native deal (`xandr_video_targets_non_video`). |
| Dayparting | UI | UI | | Xandr has it; our tools refuse it. Otherwise DSP. |
| Frequency / recency cap | UI | UI | | Same. |
| Key-values | No | No | | |
| Budget / pacing / impression goals | DSP | DSP | | |

## Watch-outs before recommending a change

- **Resume may not restart delivery.** Pause / resume toggles the deal only.
  A deal created inactive has an inactive line item that no tool reactivates.
  The review card warns when the line item is not active
  (`xandr_line_item_inactive`); after a resume, check `line_item.state` in
  `xandr_describe_deal`.
- **Date and floor updates change the deal, not the line item.** The review
  card warns when the line item's flight or its own floor differs
  (`xandr_line_item_flight_unchanged`, `xandr_line_item_floor_unchanged`);
  moving the line item's flight or floor is console work.
- **Pick the merge mode deliberately.** `replace` wipes everything else in
  that dimension; "add X" is `add`, "drop X" is `remove`.
- **Inactive segments book silently and reach nobody.** Run
  `xandr_verify_references` before recommending a segment.
- **Changing the buyer or seat** is a new deal.

## Diagnostics (read tools)

- `xandr_describe_deal` — deal + line item (margin, ad types, flight, state)
  + profile. Use it, not `get_xandr_deal`.
- `xandr_run_curator_report_from_prompt_inputs` — impressions, revenue,
  margin, tech fees, net media, total cost, clicks, CTR, buyer CPM; by day,
  hour, deal, buyer, publisher, seller, brand, country, device. No bid
  request, win rate or viewability metrics.
- `xandr_verify_references` — flags inactive segments.
- `xandr_list_insertion_orders` / `xandr_list_advertisers` — this member's
  IOs (with the advertiser each bills to) and advertisers.
- No forecast / avails tool.

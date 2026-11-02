# Media.net: deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the Media.net
console · **DSP** = buyer's line item only · **No** = not possible / blocked
for us · **Create only** = fixed after booking (change = clone to a new deal).

Two servers: our `medianet_mcp` (all writes) and Media.net's hosted
`medianet_select`, which is **read-only** here (its create / update / status /
sync tools are deliberately not enabled; its update is a full replacement and
its status call can archive irreversibly).

## How changes are applied

- Chat create (`mn_execute_deal_from_prompt_inputs`), then up to two steps on
  the new deal: a web-domain list (`medianet_merge_deal_publisher_domains`)
  and IAB exclusions (`mn_block_deal_content`). A step that fails leaves the
  deal created and the item `partial`; the next run does only that step.
- Chat update plan, `update` op (`mn_update_deal`): name, dates, floor,
  margin, status, video duration (or clearing it), clearing the content
  genre, and the demand-partner (DSP) list as a whole-list change.
- `update_lifecycle` op (`medianet_update_deal_lifecycle`): open-ended (with
  the deal's start date), auction type, reporting labels.
- Merges: publisher domains, advertiser domains, geo, segments, buyer seats,
  tech, format, content, inventory (attach / detach existing groups and
  packages), and `mn_block_deal_content` (`merge_content_block`).
- **Not in the update plan:** ad format (book a new deal), Deal Sync (gated),
  creating or editing domain / URL / IP groups (shared objects; the console,
  or a scheduled run), and `payload_overrides`, which replaces fields blindly
  and is not read-back verified.
- Several changes to one deal run in a fixed order: a pause first, then the
  merges, then the other updates, and an activation last.

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / paused) | API | API | `mn_update_deal(status)` | Archive is UI-only; no delete. |
| Start / end date | API | API | `mn_update_deal(start_date, end_date)` | Only the plan path refuses a past end date; a past end date expires the deal irreversibly. |
| Open-ended | API (omit end date) | API | `medianet_update_deal_lifecycle(open_ended=True)` | The plan needs the deal's current start date with it. |
| Name | API | API | `mn_update_deal(display_name)` | |
| Deal ID | API (caller-chosen, ≤ 30 chars) | Create only | | |
| Reporting labels (advertiser, agency, sales rep, custom) | API | API | `medianet_update_deal_lifecycle(*_label)` | Chat: `scheduleLifecycle.medianet.advertiserLabel` / `agencyLabel` / `salesRepLabel` / `customLabel`; Media.net stores them in the deal's `metadata`. |
| Auction type (first / fixed) | API | API | `medianet_update_deal_lifecycle(deal_auction_type)` | |
| Floor | API | API | `mn_update_deal(bid_floor)` | Deals with custom audiences or inventory packages must keep a floor (the plan refuses a floor of 0 on them). |
| Margin | API (required: a percentage 0–50, or a fixed USD CPM $0–25 with the fee type Fixed CPM) | API | `mn_update_deal(margin, margin_type)` | Chat books the brief's fee type: Percentage of Media -> `margin_type` 1, Fixed CPM -> 0 (Flat Fee is refused, `fee_type_wire`). A fixed margin needs a floor of at least twice the margin (`mn_fixed_margin_floor`). |
| DSPs (demand partners) | API | API | `mn_update_deal(demand_partners)` | A whole-list replacement: give the complete new list of Media.net partner ids (`mn_list_demand_partners`). The preview blocks an id outside the deal's ad-format catalog and warns which partners the new list drops and adds (and whitelisted seats that may no longer apply). |
| Buyer seats | API | API | `medianet_merge_deal_publishers` | The DSP's buyer seat ids, grouped per demand partner (`whitelisted_seats`). At create the DSP row's seat id books for the deal's own partner, and `pricingBuyers.medianet.whitelistedSeats` adds more; a seat for a partner the deal does not buy through, or one the catalog marks seat-less, is refused (`mn_seat_partner_not_on_deal`, `mn_seats_forbidden_for_partner`). `remove` drops the whole partner's seat group. Seat ids cannot be validated. |
| Deal sync to DSP | No | No | | Readable only (`get-sync-status`). |
| Geo: country / state / DMA | API | API | `medianet_merge_deal_geo` | All countries share one direction. Country include + state exclude is fine; country include + same-country state include is rejected. |
| Geo: ZIP | API (include only) | API | `medianet_merge_deal_geo` | |
| Geo: city / MSA | No | No | | Not available through our tools. |
| Publishers / primary seller | No | No | | Refused (not proven to persist). |
| Inventory packages | API | API | `medianet_merge_deal_inventory(dimension='inventory_packages')` | Needs a floor. |
| Publisher domain allow / block | API | API | `medianet_merge_deal_publisher_domains` | One direction; cannot empty the list. A chat create merges ONE site list after the create; a second site list is refused (`list_multiple_per_kind`) and an app-bundle list is reported `list_not_applied`. |
| Publisher URLs, domain / URL / IP groups | API | API (attach / detach existing groups) | `medianet_merge_deal_inventory` | All four domain-family fields must share one direction. Creating or editing a group is not a chat change (groups are shared with other deals). |
| Advertiser domain allowlist | API | API | `medianet_merge_deal_domains` | Controls which **advertisers** may serve, not inventory. Include only. |
| App-bundle lists | DSP | DSP | | Not on Media.net; only app categories, ratings, price and platform. |
| App categories | API (App-only deals) | API | `medianet_merge_deal_inventory(dimension='app_categories')` | |
| App content ratings, app prices, app user ratings, app ratings | API (App-only deals; `inventoryTargeting.medianet.appContentRatings` / `appPrices` / `appUserRatings` / `appRatings`, catalog names or ids) | UI | | Not in the update plan yet: no merge tool reads these fields back. |
| Ad format (banner / native / video) | API | Create only | | DSP lists and format-only fields are per format; book a new deal. |
| Environment (web / app / CTV) | API (include only) | API | `medianet_merge_deal_tech(dimension='environments')` | Chat books it from the channel and Environment, or any mix (e.g. Web + CTV) from `inventoryTargeting.medianet.environments`. |
| Sizes, positions, placements, interstitial, rewarded | API | API | `medianet_merge_deal_format` | |
| Device, OS, browser, device language | API (include only) | API | `medianet_merge_deal_tech` | No excludes. No carrier, manufacturer or API framework. |
| Audience segments | API | API | `medianet_merge_deal_segments` (one library per call) | OR within one library only. No AND, no cross-library logic, no exclusions. |
| IAB content categories | API (include; exclude as a post-create step) | API (include or exclude) | `medianet_merge_deal_content(dimension='content_categories')` or `mn_block_deal_content` | One direction per deal. Exclusions on a deal with no includes book right after the create (`mn_block_deal_content`, the plan's `iab_exclude` step); with includes they are not applied (warning `iab_exclude_not_applied`): only the allowlist serves, but a page tagged with an allowed and an excluded category still can. A deal whose requested includes have no Media.net catalog entry is refused (`mn_iab_include_unrepresentable`), never switched to a blocklist. |
| Genre, livestream | API | API | `medianet_merge_deal_content` | Genre is include-only (a genre block or exclude is refused: the tool would include it instead); livestream takes no side. `mn_update_deal(clear_content_genre)` clears the genre. No rating, network, channel, series or title. |
| Viewability min / max | API (banner / native) | API | `medianet_merge_deal_format(viewability)` | Media.net rejects it on video deals. |
| VCR / CTR min / max | API | API | `medianet_merge_deal_format` | VCR video only; CTR as a 0–1 fraction. |
| IVT / IAS / DV pre-bid | DSP | DSP | | No field. Contextual or data-marketplace segment libraries may carry a proxy — check with `mn_find_segments`. |
| Video ad duration (one range, seconds) | API | API | `mn_update_deal(video_min, video_max)` or `clear_video` | A discrete list is widened to min..max. |
| Other video (placement, player size, playback, skippable, protocols, position) | API (video deals) | API | `medianet_merge_deal_format` | |
| Dayparting, frequency cap, budget, pacing, KPI | DSP | DSP | | Not on Media.net deals. |

## Watch-outs before recommending a change

- **Changing the DSP list replaces it.** Name every partner the deal should
  keep, not only the new one; read the preview's drop / add warning before
  approving.
- **Ad format is fixed** for a chat change: a different format is a new deal.
- **No exclusions** for device, OS, browser, language, environment, segments,
  genre or ZIP. Narrow the include list instead.
- **Direction locks:** countries share one direction; the domain-family
  fields share one direction; flipping takes `replace`.
- **Keep a floor** on deals with custom audiences or inventory packages.
- Only active segments can be added.

## Diagnostics (read tools)

- `mn_get_deal` — live config, including the DSP sync block.
- `mn_run_report_from_prompt_inputs` — impressions, spend, margin, bid rate,
  win rate, CPM, avails, video completion; by day, hour, DSP, deal, country,
  device, environment, domain, browser, OS, labels.
- `medianet_select`: `troubleshoot-deal`, `query-avails` (the only avails /
  forecast source), `deals-reports`, `get-sync-status`.

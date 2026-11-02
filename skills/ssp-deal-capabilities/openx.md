# OpenX: deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI** = trader in the OpenX
console · **DSP** = buyer's line item only · **No** = not possible / blocked
for us · **Create only** = fixed after booking (change = clone to a new deal).

Each deal has two IDs: the public `OX-…` id (deal sheets, DSPs, reports) and
the internal numeric `deal.id` that `ox_update_deal` and the merge tools take.
`ox_get_deal` takes either: given an `OX-…` id it searches the whole account and
returns the deal with its internal `deal.id`. `ox_list_deals(deal_id=...)` or
`ox_list_deals(name_contains=...)` searches the whole account too. Never page
`ox_list_deals` by hand to find a deal, and never ask the trader for the numeric id.

## How changes are applied

- Every per-deal knob below marked **API** is in the chat update plan:
  `ox_update_deal` (name, dates, floor, status, deal type, fee config,
  fixed-CPM flag, buyer-discount flag); the merges for domains, app bundles,
  audience and publishers (these four can also batch across deals); and one
  op per single-deal tool: geo (one level per change), geo radius,
  technographic, video, quality, inventory (incl. attaching / detaching an
  existing inventory list), content, blocked content, participants, ad
  duration, and open-ended (`ox_update_deal_lifecycle`).
- Each targeting tool reads the full deal, changes one node and writes the
  whole targeting back, so untouched targeting is preserved. The preview runs
  the same tool without writing and shows the node's current value; a change
  the deal already carries previews as "no change" and is skipped.
- A re-run after an unclear result is safe: a change that already landed is
  skipped with nothing sent. A write OpenX accepted but did not read back as
  asked is reported unclear and **never sent again** by the plan: check the
  deal in the console and re-plan.
- Never send a raw `package` through `ox_update_deal`: it replaces the whole
  package and can wipe targeting (the plan refuses it, as it refuses raw
  `url_targeting` / `deal_participants`).
- **Changes take 1–2 hours to affect delivery** (status is immediate). Don't
  judge an optimization early.

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / paused) | API | API | `ox_update_deal(status)` | Archive is console-only (withheld on purpose). |
| Start / end date | API | API | `ox_update_deal(start_date, end_date)` | |
| Open-ended | API | API | `ox_update_deal_lifecycle(open_ended=True)` | TTD deals always need an end date. |
| Name | API | API | | |
| Deal ID | API (chat: `scheduleLifecycle.openx.dealId`) | Create only | | A caller-chosen id is sent and read back after the create; if OpenX keeps its own id the deal exists anyway (`ox_deal_id_not_applied`, never re-created). Blank = OpenX assigns the `OX-…` id. Deal-ID prefix and group deal ID: UI only (gated `schedule_vocab_unverified` / `ox_group_deals_unverified`). |
| Description, labels, KPI | No | No | | |
| Deal type | API (Preferred Deal, Programmatic Guaranteed) | API | `ox_update_deal(pmp_deal_type)` | Private Auction is not possible through the API (`ox_private_auction_unsupported`). A house may book Preferred Deals only — a rule in its own `deal_rules` (`option_refusal`), not an OpenX limit. No first / second-price toggle. |
| Floor | API | API | `ox_update_deal(deal_price)` | > 0. `fixed_cpm_enabled` switches fixed vs dynamic. |
| Currency | API | Create only | | |
| Curation fee | API (Percent of Media) | API (send the whole config) | `ox_update_deal(third_party_fees_config)` | Chat books Percent of Media only (`gross_share`, the campaign Curated fee). A CPM fee (`revenue_method: CPM`, `gross_cpm_cap`) stays gated (`ox_cpm_fee_unverified`): no proven wire. The update path takes `{partner_id, revenue_method, gross_share, gross_cpm_cap}`; a bare percent is refused. |
| Fee partner | API | API (part of the fee config) | | The intake card asks for it (`openxConfig.feePartner`), prefilled from the profile's OpenX seat `fee_partner`. Blank books the seat's own default (a named seat: its account name); the house seat has none and the check refuses the deal (`missing_fee_partner`). |
| Buyer discount | API (on / off) | API (on / off) | `curated_deal_buyer_discount_enabled` | Per-seat discount values: UI. |
| DSPs / buyer seats | API | API | `ox_merge_deal_participants` | Every seat of the DSP books. The Trade Desk with several seats needs one main buyer: `pricingBuyers.openx.mainBuyer`, else the first seat (the card says so). The main buyer is one of the seats as listed: a seat listed as an OpenX buyer record id (it books as that record's seat) can be the main buyer by the same record id; a record naming several seats is refused as the main buyer (`ox_main_buyer_ambiguous`). `replace` makes that the **only** participant (the preview warns and names the participants it replaces). |
| Geo (continent, country, region, state, DMA, MSA, city, postal; include and exclude) | API | API | `ox_merge_deal_geo` (one level per call) | Chat geo type `region` is OpenX's region catalog (names or ids; other SSPs refuse it). Can be blocked by the US-only audience rule. |
| Geo radius | API | API | `ox_merge_deal_geo_radius` | |
| Publishers | API (include **or** exclude) | API | `ox_merge_deal_publishers` | Numeric ids. Flipping direction needs replace and drops the existing list. |
| Inventory package | API (reuse an existing one: `inventoryTargeting.openx.packageId`) | Create only | | A reused package keeps its own targeting: the deal books `package_id` alone, and a deal that also asks for geo, IAB, audience, lists, Inventory Type Web Only / In-App, viewability or VCR is refused (`ox_package_id_targeting_conflict`). The profile's default geo, the house CTV category and Environment All (the intake card's prefill for no environment) are left to the package, and a warning says so (`ox_package_defaults_not_applied`, `ox_package_environment_not_applied`). |
| Metacategory, instance hash, inventory lists | API | API | `ox_merge_deal_inventory` | Lists: replace / clear only. |
| Domain allow / block | API (allow **or** block) | API | `ox_merge_deal_domains` | **Refused on app-only / CTV deals** — use app bundles there. Flipping needs replace. A chat create ships both a site and an app-bundle list, but no site list on an App-only deal (CTV, CTV and OTT) (reported `list_not_applied`); a second list of one kind is refused (`list_multiple_per_kind`), and so is an app-bundle list beside a mixed block list (whose app half takes the app-bundle slot). |
| Page URL rules, keywords | API | API | `ox_merge_deal_inventory` | |
| App bundles allow / block | API | API | `ox_merge_deal_app_bundles` | Domain + app block lists together need operator AND. Bundles can be silently dropped — verify after. |
| Channel (web / app / CTV) | API | Create only | | Clone or new deal. Inventory Type Web Only / In-App books `distribution_channel` WEB / APP on Display, OLV, Native and OTT (read back after the create). CTV is app-only: Web Only CTV is refused. |
| Ad sizes | API | API | `ox_merge_deal_video(node=adunit_size)` | |
| Device type | API (`device_tiers`) | API, remove only | `ox_merge_deal_technographic(dimension=device_type, merge_mode=remove)` | Removing a legacy device-type node loosens the deal. Add / replace are refused: OpenX rejects a device-type node beside the deal's device tiers. To change the tiers themselves, clone. |
| OS, browser, language, API framework | API | API | `ox_merge_deal_technographic` | One direction per node. |
| Carrier, device make | DSP | DSP | | |
| Audience | API (one OR group) | API | `ox_merge_deal_audience` | No AND across groups, no exclusions. Needs an include country. |
| IAB categories | API (include; exclude as a post-create step) | API | include: `ox_merge_deal_content(dimension=iab_include)`; exclude: `ox_block_deal_content` (IAB names in `block_iab_categories`, resolved like the create's include; or v2 ids in `block_categories_iab_v2`) | One direction per field. A create-time exclusion is gated (`ox_iab_exclude_create_unverified`): a chat create books it with `ox_block_deal_content` right after the create (a plan step; if it fails the deal is reported partial, live without it). Display, OLV, OTT and CTV and OTT deals with no IAB include only: CTV blocks by genre, Native has no IAB block, and an include list already limits the deal (a warning says so). |
| CTV content: genre, rating, network, channel, content language, livestream, series | API | API | `ox_merge_deal_content` | **Deal channel must be CTV, not "CTV and OTT".** Series: is / is any only. Episode: not available. Title: not on OpenX. |
| Viewability tier, VTR tier, IQ score, non-direct exclusion, open bidding | API | API | `ox_merge_deal_quality` | IQ score only "30.00", web only. Chat books viewability as the threshold (any %) and VCR as the VTR tier on every video deal; VTR tiers are 10, 20, … 90% or greater, another VCR is refused (`ox_vtr_tier_unsupported`), never rounded. |
| MFA exclusion | API (always on) | No | | |
| Expected sensitive category (political, pharma…) | UI | UI | | Toggle "Sensitive Ads Accepted" in the console after creation. |
| IVT / IAS / DV pre-bid | DSP | DSP | | No field. |
| Dayparting | DSP | DSP | | Not on curated deals (OpenX Direct line items only). Or ask the OpenX rep. |
| Frequency cap | DSP | DSP | | |
| Video placement, format, position, skip, rewarded | API | API | `ox_merge_deal_video` | Position: in-stream only. |
| Ad-unit max duration | API | API | `ox_update_deal_ad_duration` | Filters eligible ad units; does not cap creative length. |
| Playback method, player size, aspect ratio, CTR | DSP | DSP | | VCR books as the VTR tier (above). |
| Budget / pacing / impression goals | DSP | DSP | | |
| Key-values | No | No | | |
| DV360 sync | No | No | | DV360 buyers add the `OX-` id manually. |

## Watch-outs before recommending a change

- **Content rules need a CTV-channel deal.** A "CTV and OTT" deal must be
  rebuilt as CTV first.
- **CTV deals refuse domain lists**; recommend app bundles.
- **Some read-backs under-report** (CTV genre blocks show fewer genres than
  are active; bundles can drop silently). Check in the console when it
  matters.
- **Participants `replace` drops every other DSP.** The preview says so;
  use `add` / `remove` to keep the others.
- **Device type can only be removed** on a live deal; changing the device
  tiers means a clone.
- **A write OpenX does not read back as asked is not retried** (it would
  loop): reconcile in the console, then re-plan.
- **Delivery reacts 1–2 hours after a change.**

## Diagnostics (read tools)

- `ox_get_deal(full_targeting=True)` — live config (internal or `OX-…` id).
- `ox_check_buyer`, `ox_verify_references`, `ox_validate_domains`.
- No OpenX reporting tool in this connector. Delivery volume (impressions
  only, ~4 hours lag) may be available from the S3 feeds connector if it is
  enabled. No forecast tool.

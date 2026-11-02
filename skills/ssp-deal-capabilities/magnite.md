# Magnite (DV+ / ClearLine and Streaming): deal knobs

Labels: **API** = our tools, in the chat booking flow · **API (tool)** = our
SSP tool only, not the chat update plan · **UI tool** = our tool drives the
Magnite console in a Browserbase browser, run from chat with its own approval
card per deal (not the update plan) · **UI** = trader in the Magnite console ·
**DSP** = buyer's line item only · **No** = not possible / blocked for us ·
**Create only** = fixed after booking (change = new deal or clone).

Two products with different knobs: **DV+** (deal IDs `MGNI-MD-…`; display
and online video) and **Streaming / SpringServe** (`MGNI-CD-…`; CTV / OTT).
Check which one a deal is on before reading a row.

## How changes are applied

- Chat update plan (intake card → plan → approval card), one deal per item:
  - `update`: `magnite_update_deal_lifecycle` (name, start and end together,
    open-ended, status).
  - `merge_pricing`: `magnite_update_pricing` (floor, minimum CPM, rev share,
    price type, auction / fixed, currency, Deal Sync Bid Floor). It sets
    values, so it takes no `merge_mode`.
  - The merges for geo, language, device, targeting lists, content,
    publishers, publisher groups, DSP buyers, video duration, viewability,
    sizes, Streaming audience segments and the MAUI audience expression.
  - `merge_domain_list_items` / `merge_app_bundle_list_items`: add or remove
    the sites (domains, or app bundles) in ONE saved list the deal uses
    (`magnite_update_targeting_list_items`). Name the list by `list_id`: the
    ids in the deal's `targeting.domainList` / `appBundleList`
    (`magnite_get_deal`). `merge_mode` `add` or `remove`; the values inline
    or, past a few dozen, as a `list_file`. The deal itself is not written.
    **Only while no other deal uses the list**: preview and execute read
    every deal in the account, and a list another deal also uses is refused
    (`magnite_list_shared`) with those deals named; so is a list whose other
    users cannot all be read (`magnite_list_usage_unknown`, e.g. a deal saved
    in Advanced mode). To change only this deal, copy the list (see the
    Domain list row), or edit it in the console when every one of those
    deals should change.
- **DV+ First Party segments** (UI tool, where the deployment has it): the
  Prompt Library form "Magnite: add First Party segments", or
  `magnite_plan_first_party` →
  `magnite_ui_start_session` → `magnite_apply_first_party` (one approval
  card per deal) → `magnite_ui_end_session`, then `magnite_read_first_party`
  (where the deployment has it) to confirm.
- **Not in the update plan** (API (tool)): `magnite_clone_deal`, the raw
  `magnite_update_deal`.
- **Most merges default to `merge_mode="replace"`.** "Add X" must say `add`.
- Every write needs its own approval card.

## Knobs

| Knob | Create | Update | How | Notes |
|---|---|---|---|---|
| Status (active / inactive) | API (always created active) | API | lifecycle `status` | No archive or delete; deactivate is the only stop. The review card warns about pause / resume side effects (see watch-outs). |
| Start / end date | API | API | lifecycle `start_date` + `end_date` | Both boundaries must be sent together. Open-ended: DV+ only. |
| Time zone | No | No | | The API has no time-zone field (dates are UTC); the lifecycle tool refuses `timezone`. Set it in the console. |
| Name | API | API | lifecycle `name` | |
| Description, external ID, labels | No | No | | Not in our tools. |
| Price type (Market Rate / CPM / Market Rate with Minimum) | API | DV+: API. Streaming: API, but UI when the deal has ad duration or content targeting | `merge_pricing` `price_type` | Market Rate with Minimum is DV+ only. A floor on a Market Rate with Minimum deal is refused (use `minimum_cpm`). Streaming: see the pricing watch-out. |
| Auction vs fixed (price behavior) | Auction: API (CPM price type; `magniteConfig.priceBehavior`, blank = Auction). Fixed: No | Auction: as price type. Fixed: No | `merge_pricing` `price_behavior` (or `auction_type`): Auction only | Fixed (the CPM is the clearing price) is not live-proven, so it is refused on create AND update (`magnite_price_behavior_fixed_unverified`): the chat create, the update plan's `merge_pricing`, and the tools themselves (typed and raw create, clone of a Fixed deal, `magnite_update_pricing`, a raw `magnite_update_deal` that sets it). Set Fixed in the console. An update that leaves price behavior out keeps a console-set Fixed. On a Market Rate deal there is no curator CPM, so a price behavior is not sent (warning `magnite_price_behavior_not_applied`). |
| Publisher floor | API | As price type | `merge_pricing` `floor` / `minimum_cpm`; one group's floor via `merge_publisher_groups` `replace` | `floor` sets every publisher group; the review card shows each group's current floor and warns (`magnite_pricing_every_group`). A floor below a publisher's minimum is refused. Never set the publisher floor to the buyer's bid CPM. |
| Curation rev share (margin) | API (required): fee type Percentage of Media (rev share model Percent) or Fixed CPM (model CPM, the fee in dollars) | As price type | `merge_pricing` `fee` (percent) / `fee_cpm`, or one group via `merge_publisher_groups` `replace` | Percent units (30 = 30%). A Fixed CPM fee is the typed fee, or the client profile's fee when its fee type is CPM; a percent default never stands in. It is checked on the read-back like a percent fee. Flat Fee: No (`fee_type_wire`). `fee` applies to every group; the card warns as for the floor. |
| Publisher rev-share groups (extra groups with their own publishers, rev share, CPM) | API (`inventoryTargeting.magnite.publisherRevShareGroups`) | As price type | `merge_publisher_groups` | Added after the first group (the deal's publishers and fee). Values are percents, so a deal with a Fixed CPM fee refuses them (`magnite_rev_share_groups_fee_type_conflict`). A value or CPM that is not a number is refused. |
| Deal Sync Bid Floor (the floor synced to the DSP) | API | API (both products) | `merge_pricing` `dsp_deal_bid_floor` | Streaming requires > 0 and a stated `dsp_deal_bid_floor_currency`. Without it Magnite syncs the highest publisher floor in the deal to the DSP. This write re-sends the deal's targeting and checks it after, so it is allowed on targeted Streaming deals (and any other pricing value in the same change goes with it). The card shows the console-only targeting warning. |
| Currency | API (USD only) | As price type, USD only | `merge_pricing` `currency` | Other currencies are refused. Magnite never shows the currency back, so the card has no "before" and the result is unverified. |
| DSPs and buyer seats | API | API | `merge_dsp_buyers` (one DSP per change) | Cannot remove the last DSP. Lifecycle `dsps` replaces the whole roster and is not planned. |
| Publishers | API (names, ids or "ALL") | As price type | `merge_publishers` (membership, optional `group_index`); `merge_publisher_groups` (`add` a whole group, `remove` / `replace` one by `group_index`) | "ALL" is a snapshot at the time it is set; new publishers are not added later. A change that names a group by `group_index` runs only if the groups still match the preview; preview again if they moved. |
| Domain allow / block list | DV+: API (existing list refs). Streaming: No | DV+: API | Which lists: `merge_targeting_lists` (`kind="domains"`). The sites in a list the deal uses: `merge_domain_list_items` (`list_id`, `add` / `remove`) | Deals point at curator lists, never raw domains. One direction per deal; Allow + Block together is **UI** only. Editing a list's sites changes **every deal using it**, so the plan edits a list only when this deal is its one user (`magnite_list_shared` names the others). To change only this deal when the list is shared: write the edited sites to a file, create a new list from it (`magnite_create_targeting_list`, its own approval card), then swap it in with a `merge_targeting_lists` change (`replace`, same `list_type`, `list_refs` = the new id plus any other list of that kind the deal keeps). A change that would empty the list is refused: take the list off the deal instead. |
| App-bundle allow / block list | API (both) | API | Which lists: `merge_targeting_lists` (`kind="app-bundles"`). The bundles in a list the deal uses: `merge_app_bundle_list_items` (both products) | Same rules as domains, the shared-list rule included. One bad bundle id fails the whole list upload. A chat create books every list the deal's inventory reaches as its own curator list: a DV+ Display / OLV / Native deal with All inventory books its site list AND its app-bundle list; several lists of one kind are several refs, all in one direction (`inventory_direction_conflict` otherwise). A list the deal's inventory cannot reach (a site list on CTV, an app list on Web Only) is reported `list_not_applied`. |
| Geo (country, region, DMA, city, postal) | API | API | `merge_geo` | Include **or** exclude per level; levels can be combined. No continent / MSA. |
| Channel / product (DV+ vs Streaming), DV+ ad format | Create only | No | | Moving between products or formats is a new deal. |
| Sizes | DV+: API (required, max 15). Streaming: No | DV+: API | `merge_sizes` | |
| Device type | DV+: Mobile / PC only (no CTV). Streaming: include only | API | `merge_device` | |
| Environment (desktop web / mobile web) | DV+: API (Environment "Web Only" books Desktop Web + Mobile Web). In-App: No | DV+: API | `merge_device` | DV+ knows no in-app environment id: an In-App deal is refused (`magnite_in_app_environment_unverified`) unless an app-bundle allowlist limits it to apps (then it books with a warning). Web Only beside an environment exclusion or an app-bundle allowlist, and In-App beside a web environment include, are refused (`magnite_environment_conflict`). Streaming folds the environment into the device values. |
| OS, browser | API | API | `merge_device` | Streaming: include only. |
| Carrier | DV+: API | DV+: API | `merge_device` | |
| Device brand | Streaming: API (include only) | Streaming: API | `merge_device` | |
| Language | DV+: API | DV+: API | `merge_language` | Streaming language is unverified. |
| Audience segments | Streaming: API (AND of OR groups, exclusions) | Streaming: API | `merge_audience_segments` with `group_operation` `merge`, `add_group` or `remove_group` | A group named by `group_index` must still match the preview. |
| First Party segments (DV+) | UI tool (add only) | UI tool (add only); removing: UI | `magnite_apply_first_party` (Browserbase); console: Edit Deal Details > Targeting > First Party | Only for accounts with a First Party catalog set up in `deal_rules/magnite_first_party`, and only where Browserbase is configured; elsewhere it is UI. Segments already on the deal are kept; a segment the catalog does not know is refused, never guessed. It can add the account's default block lists (Exclude) in the same save. Do any API targeting change **before** this, never after (see watch-outs). |
| Audience expression (MAUI, both products) | No: carried (`magniteAudienceExpression`), refused by the tool while the catalog is empty | API to clear; setting: No | `merge_audience_expression` `remove` (clear); `replace` | MAUI is not enabled for our accounts: the catalog is empty, so `replace` is refused at preview (`magnite_maui_catalog_unavailable`). Clearing an existing expression needs no catalog and works, which is one way to loosen a deal. |
| Content: genre, network, channel, content categories | Streaming: API (free text, contains / does not contain) | Streaming: API | `merge_content` | DV+ has no content targeting. "Content categories" is free text, not IAB. |
| Contextual signals | Streaming: API | Streaming: API | `merge_content` (`dimension="contextual_signal"`) | "Not In" not live-proven. |
| RTD signals (`targeting.contextual.rtdSignals`, DV+ "Magnite Context") | No | No | | Only raw targeting reaches them, which plans refuse; how the console's Magnite Context maps to RTD signal ids is an open vendor question. The RTD signal tools edit shared signals (every deal using one). |
| IAB category, series, title, episode, rating, livestream, keywords | DSP | DSP | | Not on Magnite deals. |
| Viewability (min / max decile) | DV+: API | DV+: API | `merge_viewability` | Streaming has none. |
| Brand safety / IVT / IAS / DV pre-bid segments | DSP | DSP | | Not in our tools. |
| Dayparting | UI (DV+) | UI (DV+) | Console | Wiped by API targeting writes. Otherwise DSP. |
| Frequency cap | DSP | DSP | | |
| Budget / pacing / impression goals | DSP | DSP | | |
| Video ad duration | Streaming: API (one min–max range, seconds) | Streaming: API | `merge_video_duration` | DV+ silently drops it (blocked). "15s or 30s only" becomes a 15–30 range. |
| Video placement / position / player size / completion | DV+: UI. Streaming: No | Same | | Our tools cannot set them. |
| Skippable, playback method | No | No | | |
| Audio feed types (DV+ Audio) | No | No | | The feed-type wire is unverified: an Audio deal is refused (`magnite_audio_feed_types_unverified`). |

## Watch-outs before recommending a change

- **API targeting writes wipe console-only targeting.** A Magnite targeting
  write replaces the whole targeting object, and First Party segments,
  dayparting and Allow + Block lists set in the console are invisible to the
  API. Any targeting change on such a deal silently erases them. The review
  card warns on every such change (`magnite_ui_only_targeting_at_risk`), but
  cannot tell whether the deal has any. Recommend API targeting changes
  first, First Party / console work last, and say so. A clone does not carry
  them either.
- **Streaming pricing and publisher changes are console-only on targeted
  deals.** On Streaming, a write sent without the deal's targeting strips
  the video ad duration and content targeting, and the loss lands before the
  tool can report it. Our pricing and publisher writes are sent that way
  (only a Deal Sync Bid Floor write re-sends the targeting). So on a
  Streaming deal with ad duration or content targeting, the review card
  **blocks** pricing, publisher and publisher-group changes
  (`magnite_streaming_targeting_strip_risk`), and execute refuses them before
  writing. Change them in the Magnite console. A name or date change has the
  same side effect: the card warns with the same code, and the tool reports
  the loss with the re-apply tool.
- **Pause / resume side effects** (the review card warns on both):
  - On Streaming a status change strips the video ad duration and content
    targeting (`magnite_status_change_stripped_targeting`); re-apply with
    `merge_video_duration` / `merge_content`.
  - On DV+ it can restore the create-time rev-share groups, discarding later
    publisher / margin edits (`magnite_status_change_reverted_pricing_groups`).
  - Never suggest "pause, edit, resume" without a re-check.
- **Floor and rev-share writes apply to every publisher group.** The review
  card shows each group's current value and warns when there is more than
  one group. To change one group, use `merge_publisher_groups` `replace`.
- **Group edits by `group_index` need the groups to stay put.** They run only
  while the deal's groups match the preview. A plan that also changes the
  same groups earlier on that deal (pricing floor / fee, a publisher merge, a
  DV+ status change) is refused at compile: put the indexed change in a
  second plan.
- **Deals with unreadable pricing** (`magnite_pricing_unreadable`, the
  "Select All Publishers" case) and **deals saved in Advanced mode**
  (`magnite_advanced_targeting_deal`) can only be edited in the console.
- **"Add" needs `merge_mode="add"`**; the default is replace.

## Diagnostics (read tools)

- `magnite_get_deal`: live config (read before recommending). Console-only
  targeting does not show in it.
- **First Party reads, only where the deployment has them** (not every
  deployment lists these tools; when yours does not, First Party is checked in
  the console):
  - `magnite_read_first_party`: the First Party segments on up to 200 DV+
    deals (include / exclude, segment id and name), read through the API the
    console itself uses. Read-only, no browser.
  - `magnite_ui_read_first_party`: the same, read from the console (needs a
    Browserbase session); the fallback when the API read fails.
- `magnite_list_publishers`: each publisher's minimum floor.
- `magnite_run_report_from_prompt_inputs`, **DV+ only**: by day, site, deal,
  DSP, country; paid impressions, buyer spend, bid requests, bid responses,
  rev share, platform fee, CPM.
- No Streaming reporting and no forecast / avails tool.

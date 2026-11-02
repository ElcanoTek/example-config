---
name: ssp-deal-capabilities
description: Per-SSP reference of every deal setting ("knob") that can be set when a deal is created and changed on a live deal, and who can change it - an SSP connector when registered in manifest.yaml, a trader in the SSP's own console, or only the buyer in their DSP. Read it BEFORE recommending any deal optimization, answering "what can we change on this deal", "can we add X to the deal", "how do we fix delivery / performance / pacing", or turning report findings into actions, so every recommendation names a change a trader can actually apply on that SSP (Index Exchange, Magnite, OpenX, PubMatic, Xandr, TripleLift, Equativ, Nexxen, Media.net). Flags settings that exist only in the DSP.
---

# SSP deal capabilities

Every SSP lets you change a different set of things on a deal, and some
changes exist only in the SSP's console or only in the buyer's DSP. A
recommendation a trader cannot apply is worse than none: it costs them time to
discover that, and it makes the rest of the list less trusted.

Use this skill to check every deal recommendation against what the SSP
actually allows, **before** you put it in front of anyone.

## Files

One file per SSP, plus one for the buyer side. Read only the files for the
SSPs the deals in front of you run on.

| SSP | File |
|---|---|
| Index Exchange | [indexexchange.md](indexexchange.md) |
| Magnite (DV+ / ClearLine and Streaming) | [magnite.md](magnite.md) |
| OpenX | [openx.md](openx.md) |
| PubMatic | [pubmatic.md](pubmatic.md) |
| Xandr (Microsoft Curate) | [xandr.md](xandr.md) |
| TripleLift | [triplelift.md](triplelift.md) |
| Equativ | [equativ.md](equativ.md) |
| Nexxen | [nexxen.md](nexxen.md) |
| Media.net | [medianet.md](medianet.md) |
| Buyer side (any DSP) | [dsp-side.md](dsp-side.md) |

If an SSP's file is missing from this folder, this reference catalog does not
cover that SSP: say so rather than guessing what it supports.

## Deployment connector availability (`manifest.yaml` contract)

`manifest.yaml` is the complete connector contract for this deployment:

- **When `manifest.yaml` does NOT register `deal_plan` or the SSP's MCP server** (for example, in analytics/reporting bundles that ship only `explorer`, `lens`, `pages`, and `ses_outbound`): direct SSP API read and write tools are **unavailable in this deployment**. Do not claim that this deployment can read or mutate the SSP via API; treat every SSP-supported setting (**API**, **API (tool)**, **UI tool**, **UI**) as **SSP console (trader, manual)** and track execution through the Optimization Log Workbench (`Sent to client` / `Mark Live` and exported target lists).
- **When `manifest.yaml` DOES register `deal_plan` and the SSP's MCP server**: use the table below to distinguish settings that can be staged via the Deal Planner update flow (**API**) from settings that must be applied by the trader in the SSP's console (**API (tool)** and **UI**).

## Where a change can be made

Every row in the SSP files gives one of these answers, for **create** (when
the deal is booked) and **update** (on a live deal):

| Label | Meaning | Who applies it |
|---|---|---|
| **API** | The SSP's REST API supports it, and when `deal_plan` plus that SSP's connector are registered in `manifest.yaml`, the chat update plan can carry it. | When `deal_plan` and the SSP connector are registered in `manifest.yaml`: you, through the Deal Planner update flow behind its approval card. When those connectors are not registered in `manifest.yaml`: the trader in the SSP's console (**SSP console (trader, manual)**). |
| **API (tool)** | An SSP API endpoint exists, but the chat update plan (`compile_update_plan`) does not carry it (`op_unsupported` or value refused), and raw SSP write tools are not exposed for ad-hoc chat mutation. | The trader, by hand, in the SSP's console (**SSP console (trader, manual)**). Say that it is not supported by the chat update plan and name the console setting. |
| **UI tool** | Requires a browser-driven console session (for example, Magnite First Party segments) when configured in `manifest.yaml`. | Where the deployment registers the browser tool in `manifest.yaml`, you in chat with one approval per deal; otherwise the trader in the SSP's console. |
| **UI** | The SSP supports it, but only in its web console. | The trader, by hand, in the SSP's console. Say exactly where. |
| **DSP** | The SSP deal has no such setting. | The buyer, on their DSP line item. See [dsp-side.md](dsp-side.md). |
| **No** | Not possible on this SSP, or blocked for our seats. | Nobody. Do not recommend it; say why if asked. |
| **Create only** | Settable when booking, fixed afterwards. | Changing it means booking a new deal and pausing the old one. Recommend that only when the gain is worth a new deal ID the buyer must re-target. |

When `deal_plan` is registered in `manifest.yaml`, the live source for what an
update plan accepts is `get_brief_schema("update")`; when it and a file here
disagree, trust the schema and say the file is out of date.

"Gated" in a file means the tool argument exists but refuses to write until
its wire format is proven. Treat a gated knob as **not available through the
tools**: if the SSP's console has it, it is **UI**; otherwise **No**.

## Before you recommend a deal change

1. **Find the SSP.** From the deal ID format, the report's SSP column, or (when
   the SSP's read tool is registered in `manifest.yaml`) a live read. Never
   infer settings from the deal *name*: tokens such as `_CTV_`, `_In-app_` or a
   segment phrase are labels, not live targeting.
2. **Read the SSP's file** and find the row for the knob you want to turn.
3. **Check whether the SSP read tool is registered in `manifest.yaml`.** When it
   is registered and enabled, read the live deal before proposing a value (if it
   already has the value, it is "already configured", not a recommendation). When
   this deployment does not register the SSP's read tool, base the recommendation
   on the delivery report evidence, state that live SSP config was not read via
   API, and label the action **SSP console (trader, manual)**.
4. **Label the recommendation** with where it is applied: *SSP (we can
   apply)* (only when `deal_plan` and the SSP connector are registered in
   `manifest.yaml`), *SSP console (trader, manual)*, *DSP (buyer)*, plus *new
   deal* if the knob is create-only. Keep DSP-side items in their own group so
   the trader can forward them to the buyer.
5. **Check the watch-outs** in the SSP's file. Several SSPs have changes that
   silently wipe other settings or pause delivery (e.g. Magnite console-only
   targeting, PubMatic floors on First Price deals, PubMatic paused deals).

## Choosing SSP-side vs DSP-side

- **A deal-level change affects every buyer on the deal.** Narrowing
  targeting, raising a floor or blocking inventory on the SSP applies to all
  DSPs and seats on that deal. If only one buyer's line item is
  underperforming, the fix usually belongs in that buyer's DSP.
- **Supply shaping is SSP-side; spend shaping is DSP-side.** Which
  impressions the deal *offers* (inventory, geo, device, format, audience,
  content, quality thresholds, floor) is set on the SSP. How much is *bought*,
  at what bid, how fast, how often per user and with which creative is set in
  the DSP.
- **Narrowing a deal reduces its scale.** When a deal is already
  under-delivering, prefer loosening (removing a restrictive filter, lowering
  a floor) over adding blocks, and say what the scale trade-off is.
- **No connector-wide QPS or bidstream control exists.** Never recommend
  "raise QPS", "unthrottle" or "send more bid requests" in general. The one
  deal-level exception is Nexxen's request controls (`daily_request_cap`,
  `request_pacing`; see [nexxen.md](nexxen.md)): if a Nexxen deal is held
  back by its own request cap, raising or clearing it is a valid change.
  Otherwise, if supply looks constrained, the action is to ask the SSP
  account team.

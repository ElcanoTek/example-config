---
name: ssp-deal-capabilities
description: Per-SSP reference of every deal setting ("knob") that can be set when a deal is created and changed on a live deal, and who can change it - this deployment's SSP tools, a trader in the SSP's own console, or only the buyer in their DSP. Read it BEFORE recommending any deal optimization, answering "what can we change on this deal", "can we add X to the deal", "how do we fix delivery / performance / pacing", or turning report findings into actions, so every recommendation names a change a trader can actually apply on that SSP (Index Exchange, Magnite, OpenX, PubMatic, Xandr, TripleLift, Equativ, Nexxen, Media.net). Flags settings that exist only in the DSP.
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

If an SSP's file is missing from this folder, this deployment does not book
on that SSP: say so rather than guessing what it supports.

The files were checked against this deployment's SSP tools on 2026-10-19.

## Where a change can be made

Every row in the SSP files gives one of these answers, for **create** (when
the deal is booked) and **update** (on a live deal):

| Label | Meaning | Who applies it |
|---|---|---|
| **API** | This deployment's SSP tools can set it, and the chat deal-booking flow (create or update plan) can carry it. | You, through the deal-booking flow (intake card → plan → approval card) or, for your own recommendation, the deal-optimization skill's recommendation card (the trader ticks it → plan → approval card), or a scheduled run behind its audit gate. |
| **API (tool)** | An SSP tool can write it, but the chat update plan cannot carry it yet (`compile_update_plan` answers `op_unsupported` or refuses the value). The raw SSP tool is hidden from chat (it serves scheduled runs only), so **it is not available in chat today**. | A scheduled multi-deal update run (`protocols/multi-deal-update.yaml`), or the trader in the SSP console if the console has it. Recommend it with that path, and say it is not yet a chat change. |
| **UI tool** | Our tool drives the SSP's console in a browser session (Magnite First Party segments). It runs from chat with its own approval card per deal, not through the update plan. | You, in chat, with the tool's prompt and one approval per deal, where the deployment has the browser session configured; otherwise the trader in the console. |
| **UI** | The SSP supports it, but our tools cannot write it. | The trader, by hand, in the SSP's console. Say exactly where. |
| **DSP** | The SSP deal has no such setting. | The buyer, on their DSP line item. See [dsp-side.md](dsp-side.md). |
| **No** | Not possible on this SSP, or blocked for our seats. | Nobody. Do not recommend it; say why if asked. |
| **Create only** | Settable when booking, fixed afterwards. | Changing it means booking a new deal and pausing the old one. Recommend that only when the gain is worth a new deal ID the buyer must re-target. |

The live source for what an update plan accepts is
`get_brief_schema("update")`; when it and a file here disagree, trust the
schema and say the file is out of date.

"Gated" in a file means the tool argument exists but refuses to write until
its wire format is proven. Treat a gated knob as **not available through the
tools**: if the SSP's console has it, it is **UI**; otherwise **No**.

## Before you recommend a deal change

1. **Find the SSP.** From the deal ID format, the report's SSP column, or a
   live read. Never infer settings from the deal *name*: tokens such as
   `_CTV_`, `_In-app_` or a segment phrase are labels, not live targeting.
2. **Read the SSP's file** and find the row for the knob you want to turn.
3. **Read the live deal** with that SSP's read tool (listed in its file)
   before proposing a value. If it already has the value, it is "already
   configured", not a recommendation. If you cannot read it, say so and do
   not propose config changes for it.
4. **Label the recommendation** with where it is applied: *SSP (we can
   apply)*, *SSP console (trader, manual)*, *DSP (buyer)*, plus *new deal* if
   the knob is create-only. Keep DSP-side items in their own group so the
   trader can forward them to the buyer.
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

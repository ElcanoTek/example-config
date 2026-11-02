# Buyer side (DSP) knobs

These are set on the buyer's DSP line item (The Trade Desk, DV360, Amazon DSP,
Xandr Invest, Yahoo DSP and so on), not on the SSP deal. This deployment has
no DSP write tools, so a recommendation here is advice for the buyer: label it
**DSP (buyer)** and keep it apart from the SSP changes.

A few SSPs also have a version of a knob below. Where both exist, the SSP
version applies to every buyer on the deal and the DSP version to one line
item, so prefer the DSP for a single buyer's problem.

| Knob | SSPs that also have it on the deal (see each file) |
|---|---|
| Frequency cap | Nexxen (daily / monthly, our tools). Xandr has one, console only. |
| Dayparting | Index Exchange (absolute time windows) and Nexxen (weekly hours), our tools. Magnite DV+, Xandr and TripleLift CTV in the console only. |
| Delivery caps / budget | Nexxen impression and request caps; TripleLift a flat budget cap. Neither is a pacing control. |

## Always DSP-side

| Knob | Notes |
|---|---|
| Bid / max CPM, bid strategy, bid shading | The SSP sets the floor; the buyer decides what to bid above it. "Bid up / bid down" is always a DSP change. A low win rate with bids near the floor is a reason to ask the buyer to bid higher, or to lower the SSP floor. |
| Bid multipliers / adjustments by domain, app, geo, device, hour | Per-segment bid changes inside one deal. SSPs can only include or exclude a segment, never price it differently. |
| Pacing (even / ASAP / front-load) and spend budgets | Under- or over-pacing is fixed in the DSP, even on SSPs with delivery caps. |
| Frequency caps and recency | DSP-only except on the SSPs in the table above. |
| Creative: rotation, weighting, swap-outs, sizes uploaded, landing pages | The SSP can restrict which *slots* the deal offers (sizes, formats, durations), not which creative serves. |
| Conversion / KPI optimisation, algorithmic goals (CPA, ROAS, CTR, VCR goals) | Set as the line item's goal. SSP "KPI filters" (viewability, CTR, VCR minimums) only restrict supply. |
| Retargeting, lookalikes, the buyer's own pixels and 1st-party audiences | The buyer's audiences live in the DSP. Curator audiences attached to the deal are the SSP-side equivalent. |
| Cross-channel and cross-deal budget moves | Moving spend between deals, channels or SSPs is a DSP budget decision. |
| Brand-safety and verification vendors applied to the buy (IAS / DV pre-bid segments in the DSP, post-bid blocking) | Some SSPs offer quality thresholds on the deal; see each file. The buyer's verification setup is theirs. |
| Line-item flight and dayparting on SSPs without deal dayparting | Most SSPs have no deal dayparting (see each file); then it is DSP-only. |
| Whether the deal ID is attached to a line item at all, and which seat | If a deal shows bid requests but no bids, first check the deal is targeted and active in the DSP. |

## Diagnosing which side a problem is on

| Symptom in reports | Usually means | Side |
|---|---|---|
| Bid requests / available impressions, but no bids | Deal not attached or not active in the DSP, or the line item targets too narrowly | DSP |
| Bids, low win rate | Bid too low for the floor or the competition | DSP (bid), or SSP (floor) |
| Low available volume | Deal targeting too narrow, floor too high, inventory blocked | SSP |
| Good delivery, poor performance on some domains / apps | Supply quality mix | SSP (block on the deal) if it affects every buyer, else DSP (block or bid down on the line item) |
| Spend front- or back-loaded | Pacing | DSP |
| Same users seeing ads too often | Frequency | DSP (Nexxen also on the deal) |

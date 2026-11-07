# Decisions: Import 2026 US midterm results

Brief: "look into importing the us mid term results" (2026-11-07). The 2026 midterms were held 2026-11-03.

## Settled
- Scope is House + Senate. Governors are out (no pipeline; possible separate issue). (2026-11-07)
- Import provisional results now, using re-runnable tools that refresh in place as counts finish. (2026-11-07)

## Open
- Data source: probe done (threads/sources.md). NYT's static JSON is open, AP-called, at district and county level, with call status and eevp; reuse terms unchecked. BBC is an AP embed under its own licence, so unusable. Choose: NYT JSON, Wikipedia, or NYT now with a Wikipedia cross-check.
- Uncalled / still-counting seats: how they are stored and shown while the tools are re-run.
- House geometry: map 21 / `map-21.topo.json` is the 2024 district set. Several states redrew mid-decade for 2026, so 2026 results don't fit 2024 boundaries. Needs new geometry plus a new map id, or per-state handling. Exact list of redrawn states to be confirmed.
- Senate specials: `convert_538_senate.py:20` skips specials; FL and OH 2026 are specials (configured in `senateSpecialElections` in the US manifest shell). How do they enter the 2026 Senate file?
- Downstream: after import, what moves? `current-us-house` / `current-us-senate` anchors, `predictBaselineElectionId`, `nextElectionYear` (2028), `senate-current.json` (chamber changes 2027-01-03), the US forecast and poll pages.

## Assumed
- Mode is `build` (inferred from the brief; not a pure question).
- The output follows the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`) and loads through `old_data/scripts/usa/us_import.py`, rather than a new import path.

# Decisions: Import 2026 US midterm results

Brief: "look into importing the us mid term results" (2026-11-07). The 2026 midterms were held 2026-11-03.

## Settled

## Open
- Races in scope: House (435), Senate (Class 2 + FL/OH specials), governors? Governors have no pipeline today (no map, election type or geometry), so including them is a new subsystem.
- Data source: the existing converters read FiveThirtyEight's `election-results` CSVs (`old_data/scripts/usa/convert_538_house.py`, `convert_538_senate.py`). 538 closed in March 2025, so that repo is unlikely to carry 2026. Alternatives: Wikipedia scrape (the `scrape_holyrood_2026.py` pattern), MIT Election Lab (usually months later), state SoS feeds, AP/NYT/DDHQ (licensed).
- Timing / provisional results: counts are still going (e.g. California runs for weeks). Import now and refresh (`--refresh` mode exists), or wait for certification?
- House geometry: map 21 / `map-21.topo.json` is the 2024 district set. Several states redrew mid-decade for 2026, so 2026 results don't fit 2024 boundaries. Needs new geometry plus a new map id, or per-state handling. Exact list of redrawn states to be confirmed.
- Senate specials: `convert_538_senate.py:20` skips specials; FL and OH 2026 are specials (configured in `senateSpecialElections` in the US manifest shell). How do they enter the 2026 Senate file?
- Downstream: after import, what moves? `current-us-house` / `current-us-senate` anchors, `predictBaselineElectionId`, `nextElectionYear` (2028), `senate-current.json` (chamber changes 2027-01-03), the US forecast and poll pages.
- Forecast scoring (comparing the forecast with the result): in scope, or a follow-up?

## Assumed
- Mode is `build` (inferred from the brief; not a pure question).
- The output follows the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`) and loads through `old_data/scripts/usa/us_import.py`, rather than a new import path.

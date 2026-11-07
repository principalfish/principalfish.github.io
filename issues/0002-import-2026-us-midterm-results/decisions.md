# Decisions: Import 2026 US midterm results

Brief: "look into importing the us mid term results" (2026-11-07). The 2026 midterms were held 2026-11-03.

## Settled
- Scope is House + Senate. Governors are out (no pipeline; possible separate issue). (2026-11-07)
- Import provisional results now, using re-runnable tools that refresh in place as counts finish. (2026-11-07)

## Open
- 2012-cycle geometry for House 2018/2020: Census source; NC's 2020 court map vs. PA's 2018 court map coverage.
- Senate specials: `convert_538_senate.py:20` skips specials; FL and OH 2026 are specials (configured in `senateSpecialElections` in the US manifest shell). How do they enter the 2026 Senate file?
- Downstream: after import, what moves? `current-us-house` / `current-us-senate` anchors, `predictBaselineElectionId`, `nextElectionYear` (2028), `senate-current.json` (chamber changes 2027-01-03), the US forecast and poll pages.

## Assumed
- Mode is `build` (inferred from the brief; not a pure question).
- The output follows the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`) and loads through `old_data/scripts/usa/us_import.py`, rather than a new import path.

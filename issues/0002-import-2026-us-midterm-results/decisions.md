# Decisions: Import 2026 US midterm results

Brief: "look into importing the us mid term results" (2026-11-07). The 2026 midterms were held 2026-11-03.

## Settled
- Scope is House + Senate. Governors are out (no pipeline; possible separate issue). (2026-11-07)
- Import provisional results now, using re-runnable tools that refresh in place as counts finish. (2026-11-07)

## Open
- House 2020 map: Census cd116 covers 2018 (incl. PA's court map) but not NC's 2019 remedial map used in 2020. Separate 2020 map (cd116 + NC 2019 plan) or one map for both years?
- Senate specials: `convert_538_senate.py:20` skips specials; FL and OH 2026 are specials (configured in `senateSpecialElections` in the US manifest shell). How do they enter the 2026 Senate file?
- Forecast after the result: `current-us-house`/`current-us-senate` are the us_*_model forecast elections (`scripts/export/naming.py:246`). Freeze the 2026 forecast for the scoring issue, and roll the predict baseline/nextElectionYear to 2028 here or in a separate issue?
- `senate-current.json` (prebuilt, `scripts/export/legacy.py:31`, from congress-legislators): regenerate after the new Senate sits on 2027-01-03, or derive it from the 2026 results now?

## Assumed
- Mode is `build` (inferred from the brief; not a pure question).
- The output follows the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`) and loads through `old_data/scripts/usa/us_import.py`, rather than a new import path.

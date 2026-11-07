# Decisions: Import 2026 US midterm results

Brief: "look into importing the us mid term results" (2026-11-07). The 2026 midterms were held 2026-11-03.

## Settled
- Scope is House + Senate. Governors are out (no pipeline; possible separate issue). (2026-11-07)
- Import provisional results now, using re-runnable tools that refresh in place as counts finish. (2026-11-07)

## Open
- Senate specials: `convert_538_senate.py:20` skips specials; FL and OH 2026 are specials (configured in `senateSpecialElections` in the US manifest shell). How do they enter the 2026 Senate file?
- Predict anchor after the roll: `predictAnchorElectionId` (`electionmapslogic/state.js:1344`) points at the 2026 forecast election. With no 2028 forecast yet, what does the predict view open on (the 2026 result, or keep the frozen 2026 forecast)?
- Senate 2028 baselines: class 3 is up in 2028, so most seats' baseline is 2022-us-senate, but FL and OH class 3 were just filled by the 2026 specials, so their baseline is 2026. Does the baseline config handle a per-seat override (like `senateSpecialElections`, `data/scripts/export/manifest.py:146`), or does that need new config?
- Derived Senate: uncalled 2026 seats (AK, MS now) in the incoming-chamber snapshot are shown as uncalled, not as the leader. `run_us_senate_model.py:55` reads the same file, so the model's chamber baseline changes with it.

## Assumed
- The NC 2020 boundary inaccuracy gets a short note in the source line for that election.
- Mode is `build` (inferred from the brief; not a pure question).
- The output follows the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`) and loads through `old_data/scripts/usa/us_import.py`, rather than a new import path.

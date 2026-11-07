# Decisions: Import 2026 US midterm results

Brief: "look into importing the us mid term results" (2026-11-07). The 2026 midterms were held 2026-11-03.

## Settled
- Scope is House + Senate. Governors are out (no pipeline; possible separate issue). (2026-11-07)
- Import provisional results now, using re-runnable tools that refresh in place as counts finish. (2026-11-07)

## Open
- Raw NYT files: commit them to the public repo (like `old_data/files/usa/*.json` today), or cache them locally and commit only the converted per-seat files?
- Runoffs and RCV: GA Senate 2020 (two races, Jan 2021 runoffs) and 2022 (Dec runoff), LA-5 2026 (Dec runoff), AK/ME ranked-choice rounds. Store the decisive round/runoff, or the November first round?
- Senate specials: `convert_538_senate.py:20` skips specials; FL and OH 2026 are specials (configured in `senateSpecialElections` in the US manifest shell). How do they enter the 2026 Senate file?

## Assumed
- In the derived Current Senate, uncalled 2026 seats (AK, MS now) show as uncalled, not as the leader. `run_us_senate_model.py:55` reads the same file, so the model's chamber baseline changes with it.
- Re-runs are manual CLI invocations (no scheduler).
- Presidential stays on its existing sources; NYT replaces 538 for House and Senate only.
- The NYT-vs-538 diff for 2020–2024 is a reported check, not a gate; differences get reviewed by hand.
- Tests use small hand-made fixtures in the NYT schemas, not copies of NYT files.
- The NC 2020 boundary inaccuracy gets a short note in the source line for that election.
- Mode is `build` (inferred from the brief; not a pure question).
- The output follows the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`) and loads through `old_data/scripts/usa/us_import.py`, rather than a new import path.

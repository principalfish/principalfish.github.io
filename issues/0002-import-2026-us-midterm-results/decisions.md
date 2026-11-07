# Decisions: Import 2026 US midterm results

Brief: "look into importing the us mid term results" (2026-11-07). The 2026 midterms were held 2026-11-03.

## Settled
- Scope is House + Senate. Governors are out (no pipeline; possible separate issue). (2026-11-07)
- Import provisional results now, using re-runnable tools that refresh in place as counts finish. (2026-11-07)
- Forecast scoring (forecast vs. result) is wanted but goes in a separate issue; noted, not built here. (2026-11-07)
- Source is NYT's static results JSON (2026: `static01.nyt.com/elections-assets/pages/data/2026-11-03/results-{house,senate}.json`), credited with a disclaimer (NYT/AP data, non-commercial use). BBC is out (it only embeds AP's licensed widgets). See threads/sources.md. (2026-11-07)
- Uncalled races: store votes so far, mark no one elected, and flag the seat as uncalled with % expected vote counted so the map can show it as leading/uncalled. Needs a small schema/export addition. (2026-11-07)
- Publication: 2026 results are exported to the public US maps site with the disclaimer, so export, manifest and front-end changes are in scope. (2026-11-07)
- Disclaimer: a source line under the map whenever a NYT-sourced election is selected ("Results: The New York Times / AP. Provisional until certified."). Not in seat popups. (2026-11-07)
- Past elections: one NYT-based toolchain for 2018–2026 House + Senate, with three schema adapters (2022–26, 2020, 2018). Re-import 2020–2024 with --refresh (ids kept) to replace the 538-sourced data, diffing against the 538 files as a check; add the missing House 2018/2020 and Senate 2018. (2026-11-07)
- House geometry: one map id per district set. Map 21 stays for 2022–24; new ids for 2026 and for the 2012-cycle set, each a full 435-district topojson. Same pattern as westminster-2010/-2024 in the UK manifest. (2026-11-07)
- 2026 House geometry: Census cb_2024 cd119 for the 41 unchanged states, plus each redrawn state's official enacted-plan shapefile (CA, TX, UT, OH, NC, FL, TN, LA, AL; threads/redistricting.md), stitched into the new map. A per-state source table lives in the build script; swap to Census 120th-Congress files when they appear. CO/NV/OR noise to be confirmed in the build. (2026-11-07)
- House 2018 and 2020 share one 2012-cycle map from Census cb_2018 cd116 (includes PA's 2018 court map). Known inaccuracy: NC 2020 used a 2019 court map that no Census file has, so NC 2020 shows on 2018 boundaries. (2026-11-07)
- Predict pages roll forward in this issue: House and Senate predictBaselineElectionId move to 2026-cycle results and nextElectionYear/nextElectionDate to 2028. The 2026 forecasts stay frozen for the scoring issue; building 2028 forecast models is out of scope. (2026-11-07)
- Predict anchor: House and Senate predict views open on the actual 2026 result. The frozen 2026 forecasts stay in the election list as archived entries. (2026-11-07)
- Senate 2028 baselines: add a per-seat baseline override list (FL, OH → 2026-us-senate). Builder note: `senateSpecialElections` entries already carry a per-seat `baselineElectionId` (US manifest shell), so generalise that mechanism rather than inventing a parallel one. (2026-11-07)
- Current Senate is derived from the 2026 results: 65 holdovers plus the 2026 winners, as new code alongside the congress-legislators converter (`scripts/export/legacy.py:31`, `senate-current.json`). (2026-11-07)
- Raw NYT downloads go to a gitignored local cache; only the converted per-seat election JSON is committed. (2026-11-07)
- Runoffs and RCV: store the decisive round (runoff or final RCV round) once available, falling back to the November first round until then. (2026-11-07)
- Senate specials: standalone specials (FL/OH 2026, AZ 2020) take the state's map slot. Where a state has a regular and a special in the same year (GA 2020; MN, MS 2018; CA, OK 2022; NE, CA 2024), the map shows the regular race and the special goes in a separate per-year specials list in the export, shown in the state popup and counted in the chamber tally. (2026-11-07)
- Local data console gets a US results tool to fetch the latest NYT files, convert them, preview the changes and import them (refresh in place), for any supported year and chamber. It follows the console's form → preview → confirm pattern (`console/blueprints/by_elections.py:22`, `:43`, `:83`) and shells out through `console/services/runner.py`. Per the console-transparency rule, the preview shows the intermediate numbers: seats changed, newly called, % counted per race, party mapping and warnings. (2026-11-07)
- Same-party races (CA/WA top-two, LA jungle) keep today's merge: one vote row per party per seat, totals summed, top candidate named. A per-seat `sameParty` flag lets the popup say so. No vote-schema or payload-format change. (2026-11-07)

- US House comparison: each House election compares against the previous House election regardless of map, full per-seat by district key (redrawn states included). Party totals ± are valid at any level; the user accepts same-number comparisons in redrawn states. (2026-11-07)

- Mid-count seat ± (totals table): uncalled current seats and their same-key comparison seats are left out of the seat delta, so it compares called seats only; comparison seats with no current key still count (reapportionment). Vote totals and vote ± unchanged. Added to piece 8 before its commit. (2026-11-07)
- Piece 9 must settle with the user how predict treats uncalled seats in the 2026 baseline before building it. (2026-11-07)

- Predict baseline uncalled seats (2026 House, 22 at 2026-11-07): project each from its votes counted so far as if final and mark the projection called; a zero-vote seat stays neutral and uncounted; the 2026 result (anchor) view keeps the uncalled flag. (2026-11-07)

- Senate baseline overrides carry a class: `{seat, class, baselineElectionId}`, applied only when that class is up (the builder's deviation, accepted at the piece 9 review). (2026-11-07)
- The archived 2026 Senate forecast after the rollover (FL/OH comparison unavailable; Full Senate merges into the current chamber) is left to the future forecast-scoring issue, which needs per-forecast archives anyway. (2026-11-07)
- The 2028 model-run follow-ups go into piece 11's runbook as a "before you start" list. (2026-11-07)

## Open

## Assumed
- The console import step does not export automatically. Export to the site stays a separate, explicit action (a button on the result page, as with the existing US model run).
- Runoff results (GA 2021-01-05, GA 2022-12-06, LA-5 2026) are located on NYT during the build. Where none is found, the seat keeps its first-round result and is flagged.
- In the derived Current Senate, uncalled 2026 seats (AK, MS now) show as uncalled, not as the leader. `run_us_senate_model.py:55` reads the same file, so the model's chamber baseline changes with it.
- Re-runs are manual CLI invocations (no scheduler).
- Presidential stays on its existing sources; NYT replaces 538 for House and Senate only.
- The NYT-vs-538 diff for 2020–2024 is a reported check, not a gate; differences get reviewed by hand.
- Tests use small hand-made fixtures in the NYT schemas, not copies of NYT files.
- The NC 2020 boundary inaccuracy gets a short note in the source line for that election.
- Mode is `build` (inferred from the brief; not a pure question).
- The output follows the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`) and loads through `old_data/scripts/usa/us_import.py`, rather than a new import path.

# Resume point: read this first

Updated 2026-11-07 (planner, Piece 1 done). Issue state: `planned`.

## Where we are

Split from 0001 on 2026-11-07. Nothing has been built here yet. The branch `refine-westminster-and-us-poll-error-models` doesn't exist until `/builder` cuts it from `poll-errors-simulation`.

State checks:
- `git rev-parse --short poll-errors-simulation` → `44dd8336` or later (later means 0001 has moved on; merge it in when this branch starts).
- `git show 44dd8336:data/models/westminster/uncertainty_conversion_historical_report.json | sha256sum` → `0893389afce7babd00c69e98bdea9331113e0ffe900568c64ce1d602ebc59ab4`.
- `git branch --list refine-westminster-and-us-poll-error-models` → empty until the build starts.

## Next step

Piece 1 is done (2026-11-07). Phil chose route (a), a pre-registered landslide/efficiency term, and "diagnose, then choose" for its form. **The first unticked checkpoint is 2a-L1: decompose the landslide miss (offline, fit-free)**, then pause to present it and a recommended term form. See `plan.md` piece 2. Run it with `/builder refine-westminster-and-us-poll-error-models`, which cuts the branch from `poll-errors-simulation`.

2d and 2e (pieces 8–9) and the national part of 2c (piece 7) don't depend on 2a and can go in any order alongside it.

## Still open (not blocking 2a-L1)

- The order of the US pieces (4b–4d) relative to Westminster. Recommendation: Westminster first, US before the 2028 cycle.
- 4d's data source for state correlations: historical presidential state swings, or census regions.

## Don't

- Lower the 17/20 gate or add older outcomes to its denominator.
- Integrate any failed candidate into the engine, or start 2b, 2c's λ part or 2f before 2a-L3 passes.
- Fit anything for the landslide term before Phil has approved its form and contract (2a-L2).
- Write to the live DB, rerun, backfill or publish without a fresh go-ahead.

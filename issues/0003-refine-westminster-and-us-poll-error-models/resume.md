# Resume point: read this first

Updated 2026-11-10 (planner, Piece 1 done; state checks refreshed after 0001 merged). Issue state: `planned`.

## Where we are

Nothing has been built here yet. 0001 merged into master (done 2026-11-08) and its branch is gone. This issue's branch `refine-westminster-and-us-poll-error-models` was cut locally at `44dd8336` on 2026-11-07 and isn't pushed. It keeps the offline conversion experiment (and the US code) that 0001 removed before merging. **When the build starts, merge master in and straight away revert 0001's Piece 1b removal commit** (see `plan.md` Context, Branch note).

State checks:
- `git rev-parse --short refine-westminster-and-us-poll-error-models` → `44dd8336` until the build starts.
- `git ls-remote --heads origin refine-westminster-and-us-poll-error-models` → empty until the first pushed piece.
- `git show 44dd8336:data/models/westminster/uncertainty_conversion_historical_report.json | sha256sum` → `0893389afce7babd00c69e98bdea9331113e0ffe900568c64ce1d602ebc59ab4`.
- `git merge-base --is-ancestor 44dd8336 origin/master && echo ok` → `ok` (0001 merged).

## Next step

Piece 1 is done (2026-11-07). Phil chose route (a), a pre-registered landslide/efficiency term, and "diagnose, then choose" for its form. **The first unticked checkpoint is 2a-L1: decompose the landslide miss (offline, fit-free)**, then pause to present it and a recommended term form. See `plan.md` piece 2. The planner chat starts `/builder refine-westminster-and-us-poll-error-models` on the existing local branch.

2d and 2e (pieces 8–9) and the national part of 2c (piece 7) don't depend on 2a and can go in any order alongside it.

## Still open (not blocking 2a-L1)

- The order of the US pieces (4b–4d) relative to Westminster. Recommendation: Westminster first, US before the 2028 cycle. Note: 4b–4d stay in this issue (Phil, 2026-11-07), but issue 0004 restores the US code that 0001 removed in `5a96e52c`, so the US pieces need either 0004 landed first or that revert here too.
- 4d's data source for state correlations: historical presidential state swings, or census regions.

## Don't

- Lower the 17/20 gate or add older outcomes to its denominator.
- Integrate any failed candidate into the engine, or start 2b, 2c's λ part or 2f before 2a-L3 passes.
- Fit anything for the landslide term before Phil has approved its form and contract (2a-L2).
- Write to the live DB, rerun, backfill or publish without a fresh go-ahead.

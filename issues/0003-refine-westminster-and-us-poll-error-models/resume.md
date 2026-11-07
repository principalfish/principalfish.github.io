# Resume point: read this first

Updated 2026-11-07 (planner, at creation). Issue state: `planned`.

## Where we are

Split from 0001 on 2026-11-07. Nothing has been built here yet. The branch `refine-westminster-and-us-poll-error-models` doesn't exist until `/builder` cuts it from `poll-errors-simulation`.

State checks:
- `git rev-parse --short poll-errors-simulation` → `44dd8336` or later (later means 0001 has moved on; merge it in when this branch starts).
- `git show 44dd8336:data/models/westminster/uncertainty_conversion_historical_report.json | sha256sum` → `0893389afce7babd00c69e98bdea9331113e0ffe900568c64ce1d602ebc59ab4`.
- `git branch --list refine-westminster-and-us-poll-error-models` → empty until the build starts.

## Pending decision (Phil)

**Piece 1: 2a's direction after the failed gate.** See `plan.md` Context for the three attempts. Options:
1. A pre-registered landslide or Labour-lead efficiency/regime term (recommended if 2a is pursued). Risk: with two landslide transitions, LOO is weak evidence.
2. 1983–92 history first (shared boundaries and source coverage unverified; both moderate, Con-led elections, which made the scales tighter last time).
3. Set 2a aside; do 2d and 2e first, since they don't depend on it.

Open after that: US piece order relative to Westminster; 4d's data source.

## Don't

- Lower the 17/20 gate or add older outcomes to its denominator.
- Integrate any failed candidate into the engine, or start 2b, 2c's λ part or 2f before 2a passes.
- Write to the live DB, rerun, backfill or publish without a fresh go-ahead.

# Resume point: read this first

Updated 2026-11-07 (planner, Piece 1 in progress). Issue state: `planned`.

## Where we are

Split from 0001 on 2026-11-07. Nothing has been built here yet. The branch `refine-westminster-and-us-poll-error-models` doesn't exist until `/builder` cuts it from `poll-errors-simulation`.

State checks:
- `git rev-parse --short poll-errors-simulation` → `44dd8336` or later (later means 0001 has moved on; merge it in when this branch starts).
- `git show 44dd8336:data/models/westminster/uncertainty_conversion_historical_report.json | sha256sum` → `0893389afce7babd00c69e98bdea9331113e0ffe900568c64ce1d602ebc59ab4`.
- `git branch --list refine-westminster-and-us-poll-error-models` → empty until the build starts.

## Pending decision (Phil)

**Piece 1, second half: the form of 2a's landslide term.** Phil chose route (a) on 2026-11-07: a pre-registered landslide/efficiency term (see `decisions.md` Settled). Asked next which form: (1) a fit-free decomposition diagnostic first, then choose (recommended); (2) a directional term that shifts expected seats, which changes the central projection; (3) a lead-dependent spread term that leaves the centre unchanged. Phil answered "something else" with no detail. Ask what he has in mind (a combination? a different driver, such as the incumbent's vote drop? a projection change, such as MRP or seat type?).

Why it matters: the central projection is uniform regional swing, with λ pushing towards proportional (`data/model_support/monte_carlo.py:61`, `:1038`). Even at λ=1 it gives Labour 354 against 411 in 2024, so the miss is mostly projection bias, not spread.

Once the form is settled: write the 2a pieces into `plan.md` (replacing the single 2a entry), tick Piece 1, then the remaining Open items (US piece order; 4d's data source).

## Don't

- Lower the 17/20 gate or add older outcomes to its denominator.
- Integrate any failed candidate into the engine, or start 2b, 2c's λ part or 2f before 2a passes.
- Write to the live DB, rerun, backfill or publish without a fresh go-ahead.

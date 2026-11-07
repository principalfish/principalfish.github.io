# Decisions: Refine the poll-error models for Westminster and the US

Brief (2026-11-07, from issue 0001): split the model-refinement research and testing off into its own branch and issue. "It is an entirely separate experiment, definitely worth doing to make the model better, but ultimately producing something that works and refining it after is best." 0001 ships the uncertainty as built; this issue improves it afterwards.

## Settled
- Scope is 0001 thread 4's Westminster Phase 2 (Pieces 2a–2f, review findings W-1, W-2, W-3, W-5, W-6, W-7, W-8) plus the US model refinements 4b–4d (W-3 for the US, US-1, US-2). (2026-11-07)
- US 4a (small US fixes, including the A-1 Senate regional-baseline bug) stays in 0001 and ships with it. 3a (display) also stays in 0001. (2026-11-07)
- Westminster ships from 0001 as built, with no caveat label about the missing conversion error or fat tails. (2026-11-07)
- The branch is based on `poll-errors-simulation`, because the engine, the validation harness, the frozen fixtures and the failed 2a reports all live there. Once 0001 merges into master, merge master into this branch. (2026-11-07, planner default, not raised with Phil)
- Everything already settled in 0001 thread 4 for these pieces still stands: the 17/20 gate on the original 20 cells, equal transition weights, training-only fits, λ centred at 0, the off-by-default config pattern, parity invariants, and the console transparency rule. (Carried over 2026-11-07.)

## Open
- **2a's direction after the failed gate.** H3 gave best 15/20 and candidate 14/20, with misses all in one direction (landslides under-called). Options: replan with explicitly new assumptions (a landslide or Labour-lead efficiency/regime term, pre-registered before fitting); add 1983–92 history first; or skip 2a and do 2c/2d/2e first. This decides whether 2b, 2c's λ part and 2f can start.
- The order of the US pieces relative to the Westminster ones. They're independent; recommendation: Westminster first, US before the 2028 cycle starts.
- 4d's data source for state correlations: historical presidential state swings, or census regions.

## Assumed
- Mode is `build`.
- Nothing from this issue goes live without a fresh go-ahead: the Westminster backfill under a new config, the US recalibration, and publication are each gated, as in 0001.

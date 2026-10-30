# Read-only review: Westminster Monte Carlo uncertainty

Verdict: **changes recommended**. One confirmed implementation bug and one confirmed modelling inconsistency. The implemented stages otherwise align with the current plan and its recorded decisions. No source changes, fixes, commits, live-database writes or remote actions were performed.

## Scope

- User-confirmed comparison: `master...HEAD`, plus all tracked working-tree changes and three untracked storage implementation/test files.
- Branch: `poll-errors-simulation`.
- HEAD: `769602c4840cd83e88457fe49357d81d07230b56`.
- Master: `d925fb208deb40f120462b66a1d226adfb685096`.
- Merge base: `d68f177544074acfdfacbeedfd91103aafa5301b`.
- 85 committed changed files; 90 files including uncommitted work.
- Acceptance reference: [plan.md](/home/philiph/.project-ai/principalfish.github.io/issues/add-monte-carlo-uncertainty-to-westminster-projection/plan.md), with the current issue and stage briefs. Pieces 1, 2, 3a, 3b and 4 are committed; piece 5 is present as uncommitted work. Pieces 6–11 remain planned and were not reported as missing implementation.

## Findings

### 1. [P2] Revalidate each poll before applying a delayed bases backfill

Location: [data/scripts/backfill_poll_bases.py:224](/home/philiph/workspace/principalfish.github.io/data/scripts/backfill_poll_bases.py:224), lines 224–229.

`plan_backfill()` loads the stored polls and then fetches every source before the write phase. During that potentially long interval, a poll can be deleted or changed through the console. `apply_backfill()` only checks whether the poll has gained bases; it does not verify that the parent still exists or still matches the metadata/shares used to validate the fetched source. Its raw SQLite connection does not enforce foreign keys.

Consequently, deleting a planned poll does not prevent its bases from being inserted. SQLite can reuse the deleted poll ID, at which point those stale bases attach to an unrelated new poll and silently determine its effective sample size and margins of error. Updates to the original poll can similarly make the checked source stale.

Verified with the actual planner and writer in a temporary database:

1. Plan a matching More in Common poll with sample size 2,113 and captured bases 1,491 nationally / 239 for London.
2. Delete the poll and its rows before applying the plan.
3. `apply_backfill()` returns `(1, 2)` and inserts both orphaned bases.
4. Insert a new poll with a different date/source and sample size 9,999. SQLite reuses ID 1; joining its bases returns the old 1,491 and 239.

Suggested fix: carry the original metadata and row/base fingerprint into `BackfillItem`, and revalidate it within `BEGIN IMMEDIATE` before inserting. Reject or skip deleted/changed polls. The regional repair script already uses `poll_fingerprint` under its write transaction and provides an existing pattern to reuse. Add regression tests for deletion, ID reuse and metadata/share changes during planning.

### 2. [P2, design] Preserve the analytic sampling variance through centring

Location: [data/model_support/monte_carlo.py:541](/home/philiph/workspace/principalfish.github.io/data/model_support/monte_carlo.py:541), together with the regional centring at [line 714](/home/philiph/workspace/principalfish.github.io/data/model_support/monte_carlo.py:714).

`error_scales()` uses the binomial variance of the weighted poll average directly as the variance of independent, uncentred regional draws. `draw_errors()` subsequently applies `e − q·sum(e)`, which changes those marginal variances. The sampling uncertainty reaching the simulation is therefore smaller than the analytic variance computed from the effective sample size. Unlike the calibrated national and seat terms, the sampling component has no correction for this transformation.

A two-party, 50/50 example makes the mismatch exact. With effective sample size 1,000 and every systematic/seat/swing term disabled, each party's sampling variance should be `50 × 50 / 1000 = 2.5` percentage-points squared. The centring weights are both 0.5, making each resulting error `(e1 − e2)/2`, whose variance is 1.25. One million draws through the actual input builder, scale builder and draw function produced **1.24924** for each party. The sampling SD, and thus its interval width, is approximately **29% too small** in this case.

This follows the literal draw-and-centre formula in the plan; it is a design inconsistency, not an undocumented implementation deviation. Resolve the intended sampling model before wiring it into published probabilities. A suitable approach is to separate sampling noise from the raw systematic term and construct zero-sum sampling errors with the intended covariance/marginal variances. Add a sampling-only test comparing empirical post-centring variances with the analytic values, including unequal shares and fallback inputs.

## Plan alignment and review passes

- Pieces 1–2: sample/date repair, base capture, regional repairs, fallback resolution and console coverage match the recorded scope and importer-specific decisions, apart from finding 1's delayed-apply guard.
- Pieces 3a–3b: effective-n calculations, deterministic equivalence, reproducible chunked simulation, summary shapes and raw simulation persistence are present. Finding 2 identifies an inconsistency between two planned mathematical contracts.
- Piece 4: historical calibration, the per-cell centring correction, generated config/report and console diagnostics match the latest recorded decisions. Previously rejected or superseded calibration approaches were not treated as requirements.
- Piece 5: the uncommitted table/migration, atomic summary persistence, scope isolation, chronological pruning, deletion and hub status match the current brief. In particular, pruning when a newer run has no summary is explicitly recorded as an accepted decision.
- Correctness/reuse/simplification and common-mistake checks: findings above; no additional material issue confirmed.
- Coverage: extensive tests cover the major contracts. One optional improvement is to commit tests injecting SQL failures specifically at the new uncertainty INSERT and pruning UPDATE. Existing tests cover early payload rejection and older vote-write failures, but do not pin these two new late-failure paths. Independent temporary-database probes confirmed that both currently roll back elections, votes and all summary blocks correctly; this is a regression-test suggestion, not a demonstrated implementation bug.
- Security: no actionable vulnerability confirmed in the changed SQL, HTML rendering, source-fetch integration, publication helpers or raw simulation loading. Data parameters are bound; report links are constrained; NPZ loading disables pickle.
- Style: reviewed manually against Python/HTML/CSS guides. No additional blocking finding. The data-driven inline histogram width is already documented in the previous review decisions and was not reopened. Ruff is unavailable in the current environment.

## Verification and limitations

- Strict mypy: **passed, 225 source files**.
- Full Python suite: **5,423 passed in 113.83 seconds**.
- Both ran against an isolated copy at `/tmp/westminster-review-6sc0fct9`, with bytecode generation disabled, mypy cache in the copy, and pytest's cache disabled. Mypy gated pytest as in the project runner.
- Temporary-database reproductions confirmed finding 1 and both storage rollback paths; one million simulated draws confirmed finding 2.
- A hash comparison of every snapshotted source file found no checkout changes during the review.
- Review used independent correctness, importer/security and coverage/style agents, with root verification and deduplication. Agent follow-ups hit a usage limit; the root completed the outstanding reproductions inline.
- No live source re-fetch, live migration, live database repair or historical backfill was run. External poll source data was reviewed through committed material and fixtures rather than re-audited online.
- No fixes were selected or applied. Later workflow stages remain pending.

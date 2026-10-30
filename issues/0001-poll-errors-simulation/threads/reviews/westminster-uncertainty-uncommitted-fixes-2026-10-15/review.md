# Review: uncommitted Westminster uncertainty fixes

Date: 15 October 2026

Verdict: **no blocking findings**. Both defects from the previous review are resolved, and the additional storage rollback tests close the earlier coverage gap. One low-priority diagnostic edge case remains; there is also a minor test-hygiene suggestion.

## Scope

Read-only review of the seven current uncommitted files against HEAD `46882f96` on `poll-errors-simulation`, using `git diff HEAD`. This deliberately excludes the earlier committed branch work: the user requested the latest uncommitted fixes. Scope comprises 995 added and 64 removed lines across:

- `data/model_support/monte_carlo.py`
- `data/scripts/backfill_poll_bases.py`
- `data/scripts/poll_sources.py`
- `data/tests/test_backfill_poll_bases.py`
- `data/tests/test_calibration.py`
- `data/tests/test_model_uncertainty_persistence.py`
- `data/tests/test_monte_carlo.py`

Acceptance references: the original review and the latest review-fixes note in [plan.md](/home/philiph/.project-ai/principalfish.github.io/issues/add-monte-carlo-uncertainty-to-westminster-projection/plan.md:826). Broader recommendations in independent-note.md are not treated as requirements for this focused fix.

## Finding

### [P3] Make the sampling exactness check independent of variance scale

Location: [data/model_support/monte_carlo.py:713](/home/philiph/workspace/principalfish.github.io/data/model_support/monte_carlo.py:713).

The two-party exactness check sets `rtol=1e-12` but leaves NumPy's default absolute tolerance, `atol=1e-8`. As a result, sufficiently small unequal target variances are marked exact even though the fallback changes them substantially in relative terms.

Reproduced directly through `regional_sampling_multinomial`, using two receiving parties and weights `[0.5, 0.5]`:

- Requested `[3, 1]`; delivered `[2, 2]`; `exact=False`.
- Requested `[3e-9, 1e-9]`; delivered `[2e-9, 2e-9]`; `exact=True`.

The sampling draw itself still follows the documented fallback; the defect is the diagnostic flag. No material impact on current polling inputs was established. Set `atol=0` and add a scale-parameterized test for the exactness flag.

## Previous findings verified

**Delayed backfill revalidation: resolved.** The plan carries the poll's metadata and row/base fingerprint. `BEGIN IMMEDIATE` is acquired before all revalidation and insertion. Deleted polls, changed metadata, changed rows/bases and polls moved off the map are skipped and reported. The documented decision to continue with unchanged polls is implemented consistently. All production callers handle the new `BackfillResult`.

Independent in-memory reproductions of both original failure modes now insert zero bases: deletion produces the deleted/moved message; automatic SQLite ID reuse with identical share rows but different poll metadata produces the changed-details message. Neither leaves stale bases behind. The shared fingerprint interface used by regional repair remains compatible; unrelated pre-existing repair limitations were not expanded into this review.

**Sampling variance shrinkage: resolved for feasible fits.** Sampling noise is now separate from the centred systematic term and is constructed with zero-sum multinomial covariance. Both quadratic branches, zero-variance cells and the documented infeasible fallbacks were inspected. Independent probes over 2,200 feasible multinomial-shaped cases found a maximum relative marginal-variance error of approximately 5.9e-12. A 200,000-draw two-party probe produced variance 2.50394 against a target of 2.5, with zero-sum residual below 1.4e-15.

The fallback explicitly preserves total variance rather than every marginal when the chosen covariance family cannot represent the targets. That is recorded in the updated plan and was assessed as the intended behaviour. The new per-simulation stream layout maintains chunk ordering; existing `ErrorScales` and `ErrorDraws` callers have been updated.

**Late storage-failure coverage: resolved.** New trigger-driven tests cover failure of the uncertainty INSERT, pruning UPDATE, and pruning without a new payload. They compare elections, votes and uncertainty rows by value, including a bystander scope where appropriate.

## Other review passes

- Correctness, reuse and simplification: no additional actionable issue found.
- Coverage: adequate for these fixes apart from the small exactness-flag regression case above. Tests cover deleted/reused IDs, per-column metadata changes, share changes, map changes, newly added bases, continuation past stale items, covariance, both solver branches, fallback, seed and chunk invariance.
- Security: no actionable finding. New production SQL binds data values; interpolated SQL fragments are constants.
- Common mistakes/style: one minor test-hygiene suggestion at [test_backfill_poll_bases.py:576](/home/philiph/workspace/principalfish.github.io/data/tests/test_backfill_poll_bases.py:576): the new test performs `conn.execute(...)` inside an assertion. The Python common-mistakes guide recommends executing the mutation first, assigning its result, and then asserting on `rowcount`. This is not a production blocker.
- Ruff remains unavailable in the environment; style review was manual. `git diff --check` passed.

## Verification

- Strict mypy: **passed, 225 source files**.
- Full Python suite: **5,449 passed in 115.17 seconds**.
- Mypy gated pytest, using the project virtualenv against an isolated source snapshot at `/tmp/westminster-fixes-review-zi27m4ra`. Bytecode generation and pytest's cache were disabled; mypy's cache was confined to the temporary copy.
- Independent correctness, importer/security and coverage/style review passes were performed by subagents, with root verification and deduplication.
- A source hash comparison confirmed the checkout matched the reviewed snapshot.
- No source files, guides, issue plans, live database contents or Git history were changed. No remote actions were performed. Only this review report was written to project data.

This review verifies the fixes and their software contracts. It does not constitute historical validation of published election probabilities; the separate methodological note remains applicable.

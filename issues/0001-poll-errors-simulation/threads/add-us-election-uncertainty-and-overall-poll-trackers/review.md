# Selected review fixes

User selected all five correctness findings from the independent review dated2026-10-25, reviewed HEAD03bbf46d against master. Source report: ../../reviews/add-us-election-uncertainty-and-overall-poll-trackers-20261025-03bbf46d-independent/review.md.

The shared checkout had moved to master. User explicitly authorised switching back to poll-errors-simulation and fixing these findings. The checkout was clean and switched successfully; the target branch still matched the reviewed commit. One scoped implementer owns source/tests; root owns checkpoint files and integration checks.

## Selected findings

- [x] P2: resolve the dated Senate field before CLI poll-scope/endpoint selection, including the cached-window uncertainty path.
- [x] P2: reject or explicitly mark unavailable actual legacy Senate arrays without validated full-chamber roster context.
- [x] P2: revalidate full relevant poll identity when applying Westminster sample-size repairs under the immediate transaction.
- [x] P2: pass the saved regular Senate class through frontend chamber substitution while preserving special overrides.
- [x] P3: refresh an already open race popup on both uncertainty-toggle directions.

## Scope and validation

Meaningful targeted regressions, focused strict Python types/tests, JS tests with the available jsdom harness, regenerated shared bundles, then isolated full Python checks. No automatic analyse/check-* workflow passes; the user selected fixes from a completed independent review. Calibration/release flags, the separately planned Senate anchor correction, live data/runs/backfills and publication remain outside this fix scope.

The review also noted optional-jsdom CI skips and visual-review gaps. These are recorded limitations, not authorisation to change dependencies/CI as part of the five chosen correctness fixes.

## Completed implementation

Committed locally as `76be5ea8` on `poll-errors-simulation`, under the existing issue-wide commit-and-continue authorization and the user's explicit selection of these five fixes. All20 changed files matched the isolated full-check copy before commit.

- Ordinary CLI configuration binds its dated Senate field before endpoint scope selection. Explicit retrospective ranges bind their start-date rather than today's field. The existing cross-cycle cap refusal remains intact; no new endpoint-skipping policy was introduced. Regression covers newer off-field California evidence, shifted-window contributors, exact fresh-estimator rows, synthetic accepted uncertainty, and today2035 with a supported2026 historical request.
- Legacy Senate arrays use the Senate adapter and cannot supply invented full-chamber history. A compact frozen two-point fixture copied from the reviewed35-seat payload protects this case independently of later generated exports.
- Repair plans retain complete poll heads and recorded shares before slow PDF fetching. Application checks the complete batch under BEGIN IMMEDIATE before any writes, then preserves existing per-column guards. Tests cover changed dates/source/map/pollster/shares, deleted/reused IDs, mutation during fetch, and multiple intended corrections on one poll without partial application. No live repair ran.
- Export propagates saved regularClass into frontend prediction configuration; forecast and interactive builders pass it to chamber substitution, retaining explicit special overrides and legacy2026 Class2 behavior. Class3 synthetic tests preserve holdovers and the100-member ledger. No roster/target/calibration was enabled.
- The shared uncertainty toggle refreshes an existing popup along with totals. An actual jsdom interaction opens a race, toggles both directions and verifies bands/probabilities without recreating/closing the popup.

## Verification results

- Focused strict mypy:281 source files clean.
- Focused Python:141 passed.
- Focused JS:135 passed across6files; after freezing the legacy fixture, affected parser/view38 passed.
- Isolated full Python: strict mypy281 files;5968 tests passed in422.32s. Source copied without.env; runtime/test temp under ignored workspace cache, with fixture-only databases.
- Full JS with PF_TEST_JSDOM pointing to the existing jsdom24 installation:635 passed across34 files, no skips. Existing vendor sourcemap warning did not affect execution.
- npm run minify:electionmaps succeeded; both shared map bundles regenerated. No other generated assets changed.
- git diff --check clean. No automatic analyse/check-* review workflow passes were launched.

Logs: data/.pytest_cache/review-fixes-{mypy,python,js,build,full-python,full-js}.log. Check copy: data/.pytest_cache/codex-us-review-fixes-full-copy.

## Remaining work

These are selected-fix results, not a new branch-wide review or production/calibration acceptance. Optional jsdom dependency/CI setup and manual visual review remain recorded gaps outside the selected five. Separate Senate anchor correction, House geography, calibration/evidence acceptance, actual approved pilots/backfills/publication and certified2026 scoring remain pending. No release/candidate files, live DB, forecasts, public export data or remotes were modified.


# Branch review 2026-10-26 (parked)

`/review self` of `poll-errors-simulation` at `134d1fc4` plus the uncommitted Senate anchor piece 4. Six read-only passes ran: an independent methodology audit, code-review, coverage, common-mistakes, security and style. **The review is parked: no fixes were applied and nothing was committed.** The user will assess it later.

Full consolidated review: ../../reviews/poll-errors-simulation-20261026-134d1fc4-full/review.md
Methodology report: ../../reviews/poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/review.md

Headline: the machinery is correct, but Westminster seat odds are overconfident. Seat errors are independent (W-1, P1), seat-level tails are thin (W-2), and the error scales are treated as known (W-3). The US is sound but its scales are unidentified, so it should stay gated (US-1, US-2). No code bugs were found at P1 or P2. Security is clean.

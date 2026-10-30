# Review: US election uncertainty and overall poll trackers

**Verdict: changes requested.** Five actionable correctness findings follow. Passing checks do not cover the reproduced Senate input-selection and legacy-payload failures. The implementation is substantial gated software; it is not an accepted or operationally validated odds release.

## Scope and checkout

- Requested mode: read-only review against `master`, including uncommitted changes, assessed against `add-us-election-uncertainty-and-overall-poll-trackers/plan.md`.
- Initially verified clean `poll-errors-simulation`, HEAD `03bbf46d57c8bb79c1a1f7ce0fc4c2d81d469947`.
- Approved base / merge base: `7736a80e4ad9c6d050ba03e1071f275392d0daf2`. Reviewed 272 changed files, including the earlier Westminster prerequisites; generated data accounts for much of the 325,844 added lines.
- On resumption, another actor had switched the shared checkout to `master` (`4925ef7e`). The target branch still pointed to `03bbf46d`, and its merge base with current master remained `7736a80e`. Review and checks continued against the preserved committed-source copy at `/tmp/us-review-zwo4CC`; the checkout was not switched.
- There were no uncommitted changes at initial inspection or resumption. No source, plan, guide, database, commit or remote changes were made by this review. Test and reproduction outputs are scratch files.
- The approved plan was read directly from `/home/philiph/.project-ai/principalfish.github.io/issues/add-us-election-uncertainty-and-overall-poll-trackers/plan.md`, including its subsequent staging decisions and open checkpoints. No implementation-chat claims were used as proof.

All locations below refer to the reviewed commit. Snapshot links remain usable while the shared workspace is on another branch.

## Actionable findings

### 1. [P2] Resolve the Senate cycle before selecting the CLI poll window

**Location:** `data/models/us/_common.py:3434` (also 3447–3458). [Snapshot](/tmp/us-review-zwo4CC/data/models/us/_common.py:3434)

`main_for_spec` calls `resolve_poll_scope(db, spec)` before `_build_config_from_args` binds the dated Senate field. The new Senate runner's unresolved spec has no seat allowlist, so endpoint selection admits tracked polls from any state on the Senate map. The selected window is then reused with the cycle-bound projection. An out-of-field poll can advance the as-of cap and latest-poll metadata; uncertainty input construction rejects local evidence outside the frozen field. This becomes especially relevant across the 2026/2028 rollover when the database retains polls for both classes.

Reproduced using the existing synthetic Senate fixture plus a tracked California poll ending 20 October 2026: the unresolved CLI scope selected `latest=2026-10-20`, local seat 36; the properly resolved 2026 scope selected `latest=2026-10-09`, with no local seats. California is not in that 2026 field. This is independent of calibration acceptance: central-only runs already select the wrong endpoint.

**Fix:** construct the dated configuration before resolving its poll scope, and pass `cfg.spec` into endpoint selection. Preserve the explicit cross-cycle cap refusal and resolve each history window against its own dated field. Add an actual CLI regression containing a newer tracked poll outside the current class and exercise the cached-window path with uncertainty enabled on synthetic approved settings.

### 2. [P2] Do not send legacy Senate arrays through the Westminster tracker adapter

**Location:** `electionmapslogic/features/polltracker.js:70–77`. [Snapshot](/tmp/us-review-zwo4CC/electionmapslogic/features/polltracker.js:70)

The model argument only adapts legacy arrays for President. A legacy Senate array therefore falls through to the Westminster descriptor and treats each party's `s` as overall seats, with vote-share controls and ordinary carry-forward. The committed `uselectionmaps/data/results/us-senate-trends.json` is exactly such an array, and this branch enables its tracker in the manifest. This produces a currently reachable misleading overall Senate chart even with all uncertainty releases disabled.

Bundling and calling the real parser on the committed payload with model `senate` produced Democratic 19 and Republican 16 seats at the latest point, and retained vote percentages: the contested 35-seat field, not the full 100-seat chamber. It bypasses the explicit rejection/gap handling in `senateTrackerEntries`.

**Fix:** route legacy Senate arrays through a Senate-specific unavailable-history adapter (or reject them with the existing explanation). Only render full-chamber central points when a validated saved roster supplies them. Test the actual shipped legacy array shape. The test named “explains unavailable legacy full Senate history” at `polltracker-view.test.js:156` currently supplies a new-schema envelope instead, so it misses this path.

### 3. [P2] Revalidate the poll identity used to plan sample-size repairs

**Location:** `data/scripts/repair_poll_records.py:110–114`; `apply_repairs` at 473–477. [Snapshot](/tmp/us-review-zwo4CC/data/scripts/repair_poll_records.py:110)

The Techne planner verifies source PDF and fieldwork dates, but the sample-size UPDATE only checks poll ID, old sample size and pollster. Fetching happens before `BEGIN IMMEDIATE`. If another console/import operation edits the poll's dates/source/map, or reuses its ID with the same pollster and old sample value, application can attach the old PDF's respondent count to a different poll despite the stale-row protection claim.

Independently reproduced in an in-memory SQLite database: plan a 4–5 February poll repair from 16361 to 1644; change its dates to 6–7 February and source URL while preserving ID/pollster/sample; `apply_repairs` returns 1 and writes 1644 to the changed row. No live database was used.

**Fix:** carry the full relevant poll identity/fingerprint in the repair plan and revalidate under the immediate transaction before updating. Reuse the guarded approach already used by the newer evidence backfills. Test changed dates, URL, map and deleted/reused IDs, not only changed sample or pollster. This is a prerequisite-branch finding, rather than a defect in the new US evidence backfill.

### 4. [P2] Pass the saved regular Senate class into full-chamber substitution

**Location:** `data/scripts/export/senate.py:123–138`; `electionmapslogic/features/senate-chamber.js:22–25`. [Snapshot](/tmp/us-review-zwo4CC/data/scripts/export/senate.py:123)

The new cycle metadata exports `regularClass`, but the configured frontend never passes it to the chamber builder. `buildSenateChamber` still defaults every non-special race to Class 2. The branch explicitly resolves 2027/2028 runs to Class 3. Once a verified 2028 roster is supplied and the existing configuration enables Full Senate, that view will replace Class-2 holdovers or leave a projected Class-3 winner unapplied. Saved backend full-chamber totals and the displayed member list can then disagree.

This is a latent 2028-readiness defect: current configuration correctly disables the 2028 full chamber because its roster is missing; it is not an assertion that 2028 odds are exposed today. It does not require speculative election results to test. A synthetic state with Class-1 Democratic and Class-3 Republican members plus a Democratic Class-3 forecast remains unchanged because neither member matches the hard-coded class.

**Fix:** thread the saved regular class, or the exact saved contested slot mapping, through the forecast adapter into `buildSenateChamber`, retaining special overrides. Add a Class-3 synthetic roster test that asserts the correct holdover survives and frontend totals match backend counting.

### 5. [P3] Refresh an open race popup when toggling uncertainty

**Location:** `electionmapslogic/dom.js:2149`, with the new conditional at 1466–1471. [Snapshot](/tmp/us-review-zwo4CC/electionmapslogic/dom.js:2149)

US popup bands now depend on `state.voteTotals.showUncertainty`, but the toggle callback only calls `renderVoteTotals`. A popup already open when the user selects Show/Hide uncertainty retains its old bands until it is reopened or another action refreshes it. This can leave probabilities visible after Hide or absent after Show while the totals table reflects the new state.

**Fix:** refresh the open popup alongside the table when this shared setting changes. Add a DOM interaction test that opens a race, toggles both directions and checks the existing popup without closing it. This finding is established by the event/render call paths; no visual-browser reproduction was performed.

## Plan coverage

| Plan area | Assessment |
|-|-|
| Pieces 1–2 and baseline/phantom prerequisites | Contracts, evidence provenance, guarded US repair tools, baseline audit and separate central fix are present. The older Westminster repair guard has finding 3. No live evidence application was attempted in this review. |
| Piece 3 calibration | Reproducible candidate/comparison machinery, pinned inputs, reports and console views exist. Calibration acceptance remains open by explicit plan decision. All three release files remain disabled. The separately recorded Senate anchor correction must precede accepted Senate calibration; it is not silently fixed here. |
| Pieces 4–7 | Dated Senate contexts, frozen US source graph, persistence/replay and console paths are implemented. Finding 1 breaks the ordinary CLI's selected-field boundary despite correct direct configuration paths. |
| Pieces 8–12 | Public schemas, identity checks, model descriptors, House/Senate/President trackers and Senate tab handling are present. Findings 2, 4 and 5 affect legacy history, future cycle display and popup synchronization. |
| Piece 13 | Preregistration, offline snapshot capture, explicit result/policy contracts, scorer and readiness views are present. Actual certified 2026 scoring and evaluation-basis approval are correctly pending. Synthetic scoring tests do not establish electoral calibration. |
| Piece 14 | Isolation/operations runbook is present. Actual accepted-input pilots, resource measurements, reviewed race cases and live backfills remain pending. |
| Publication | Not complete or authorized by this review. House geography, Senate anchor/calibration, President assumptions/allocation, contributor evidence and the model-specific acceptance gates remain documented prerequisites. |

## Checks and material testing gaps

- Independently ran the prescribed Python gate in a committed-source scratch copy without `.env`: strict mypy passed for 281 source files; pytest **5,955 passed** in 236.74 seconds. Configuration pointed to scratch database/raw/archive/temp paths. The original repository was not used for test output.
- Frontend reviewer's ordinary `npm test`: **620 passed, 10 skipped**. `package.json` does not install jsdom and the CI workflow does not supply `PF_TEST_JSDOM`, so the newly added ten actual-view tests silently skip in the normal/CI setup. Make this a required development test dependency or explicit CI installation, and fail rather than skip if the required harness is absent.
- Independently reran the snapshot frontend suite with the existing jsdom harness: **627 passed, 3 skipped** across 33 files. A focused verbose run confirmed **all 10 tracker-view tests passed**. These are the observed counts; this review does not equate the remaining skips with successful tests. Dependency binaries came from the available shared installation (Vitest 4.1.12), not a fresh lockfile install.
- The reproduced bugs require missing scenarios: actual legacy Senate array input; CLI scope selection with newer off-field polls; repair-plan identity changes during fetch; Class-3 full-chamber substitution; an open popup during toggle changes.
- Manual visual confirmation remains a plan gap, including the corrected House preview and narrow-layout/full-Senate views. DOM checks do not establish chart geometry or visual quality. No manual browser preview or production-calibrated performance benchmark was performed here.
- `git diff --check` reports trailing whitespace in the extracted YouGov text fixture and one extra EOF blank line in `test_console_uncertainty_runs.py`. These are not elevated to correctness findings.

## Review passes and limits

Correctness/reuse, coverage, common mistakes, security and language-style guidance were considered. Independent agents examined backend, frontend and security/prerequisite paths; after their usage-limit interruptions, the primary reviewer verified the reported concrete findings and completed the report inline. No additional concrete reachable injection/authentication/secret defect was established. Existing static-site architecture and surrounding file conventions take precedence over generic guide examples; cosmetic deviations were not treated as blockers. No guide-learning edits were made because this run expressly authorizes only review artifacts.

The diff includes a large prior Westminster implementation and extensive generated calibration data. Review attention concentrated on executable boundaries, persistence/export identities, contributor selection, cycle transitions, public interpretation and the approved US acceptance criteria; this report is not a claim that every generated data row was independently recertified. No findings were fixed, and no commits, issues or pull requests were created or published.

# Plan: us-poll-errors-uncertainty (draft, not approved)

**State:** investigating. This is a holding plan; nothing is scheduled. Phil wants the full US data pipeline working, and a more refined UK model, before this resumes (2026-11-07). Re-plan with `/planner resume us-poll-errors-uncertainty` when it does.

## Context

Everything here was built and reviewed under issue 0001. The detail is in 0001's thread folders, which stay where they are:
- `../0001-poll-errors-simulation/threads/add-us-election-uncertainty-and-overall-poll-trackers/` (thread 2: US uncertainty and US poll trackers);
- `../0001-poll-errors-simulation/threads/anchor-us-senate-swings-to-matching-house-national-baselines/` (thread 3: the Senate anchor);
- `../0001-poll-errors-simulation/threads/fix-poll-errors-simulation-review-findings/` Phase 4 (4a–4e);
- the branch reviews in `../0001-poll-errors-simulation/threads/reviews/`.

Issue 0003 (#78) still holds the US model refinements 4b–4d (Phil, 2026-11-07: leave 0003 as is). 0003 branches from `poll-errors-simulation`, which no longer has the US code after its Piece 1, so 4b–4d will need this branch. Reconcile that when either issue is next planned.

## Pieces carried over from 0001 (not yet re-planned)

1. **Bring the branch up to date.** Merge master in. Master by then contains 0001's US-removal commit, so **revert that commit on this branch straight after the merge**, then fix any conflicts with whatever master has gained (0002's imports, for example).
2. **4a: small US model fixes** (thread 4 Piece 4a). Briefed on 2026-11-07 with Phil's choices:
   - US-3: seed the national G stream by date only, one normal per party id (not per column position), so the chambers share G and the columns can't misalign; the scales stay per chamber.
   - US-4: always load the parent statewide residual on the ME/NE districts, plus a new district term, `district_scale` in `UsErrorConfig`, default 0 (Phil). Evidence for the calibration sources doc: district-vs-state swing differs by about 3.8 margin points RMS across 20 district-transitions, 2008–2024, or about 2.2 without 2016.
   - US-5: **document** the shrinkage (Phil). G stays on the 2.44 input error; the G-only output SD of 2.29 goes in the report, the ledger and the console.
   - Note: `validate_us_uncertainty.py` only captures and scores saved runs, so present this piece's deltas from read-only scratch simulations instead.
3. **A-1: the Senate regional own-poll branch** (`senate_fallback_swings` in `models/us/_common.py`). Replace the regional poll minus the 2020 Senate field pooled by division with the regional poll minus the 2020 House vote pooled by division. Needs 2020 House results by district (state → division is enough). Phil wants them imported in 0002, or a follow-up import, first. Dormant today: no US poll has a `region_id`.
4. **US calibration acceptance** (thread 2 Piece 3, thread 3): Phil's go/no-go on the House, Senate and President scales. House G comes from 6 cycles; Senate and President have no numeric scales yet.
5. **US back-run** (thread 2 Piece 14, thread 4 Piece 4e, thread 3 live reruns), including the anchored Senate rerun. Each live step needs a backup and Phil's go-ahead at the time.
6. **US scoring** (thread 2 Piece 13) against imported results. Two decisions: provisional or certified results; and whether a replay with pre-election inputs counts.
7. **US publication decision.** Flip the release flags, or keep them dark.
8. **Decide the ungated deterministic fixes.** Ship them with this issue, or extract them earlier as their own small change. They are the Senate anchor, the phantom-party fix and the Senate roster pin. Each one changes the published US output on its next run.

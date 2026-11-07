# Plan: poll-errors-simulation

**Build model:** claude-opus-5-5. The remaining work is model fixes, calibration judgement and gated live operations. The style piece is mechanical and runs on Sonnet.

## Context

Branch `poll-errors-simulation` (base `master`) holds all of the poll-error uncertainty work: 46 commits from 2026-10-12 to 2026-10-29. `master` was last merged in at `35e527a1` (2026-10-22). The work came from four separate plans. On 2026-10-30 they were consolidated into this one issue, moved here from `~/.project-ai`.

**The detail lives in the thread plans, kept unchanged** under `threads/<original-slug>/`: each plan's Context, Pieces, Verification, Notes, briefs and reviews, exactly as they were. This file is the single checklist and the index to them. When a piece starts, read its piece entry and Notes in its thread plan; don't re-derive anything the thread already settled.

| # | Thread | Folder | Done | State |
|-|-|-|-|-|
| 1 | Westminster Monte Carlo uncertainty: poll bases, n_eff/MoE, MC engine, calibration, storage, console, export, map, tracker bands | `threads/add-monte-carlo-uncertainty-to-westminster-projection/` | 12/13 | Built and committed (`4eb2646b` → `40d3933f`, plus tracker follow-ups `d50922ea`, `9bb8c2c8`). Only "Full backfill published" is open (see thread 4, Phase 5). |
| 2 | US uncertainty (House, Senate, President) and overall poll trackers | `threads/add-us-election-uncertainty-and-overall-poll-trackers/` | 16/20 | Software committed (`6c4de57d` → `03bbf46d`, review fixes `76be5ea8`). Open items are gated: calibration acceptance, certified-2026 validation, pilots/backfills, publication. |
| 3 | Anchor Senate swings to matching House national baselines (bug fix) | `threads/anchor-us-senate-swings-to-matching-house-national-baselines/` | 8/11 | Fix committed (`087afcfc`, `134d1fc4`, `282dd916`). Open: manual review of the corrected comparison, calibration acceptance, live runs. |
| 4 | Fix the branch review findings (validation harness, robustness, model changes, US fixes, backfill) | `threads/fix-poll-errors-simulation-review-findings/` | 4/18 | **Active thread.** Pieces 0/1a/1b/1c committed (`c067f150` → `92743751`). The 2a experiment and H1–H3 are committed as evidence that it failed (`ab3fc62e`, `44dd8336`). On 2026-11-07, 2a–2f and 4b–4d moved to issue 0003; 3a, 4a, 4e, Phase 5 and Phase 6 stay here. |

Reviews of the branch (all six) are in `threads/reviews/`.

### The ship decision (2026-11-07)

Phil decided to **ship this branch as built and refine the model afterwards.** "Producing something that works and refining it after is best."
- **Moved to issue 0003** (`refine-westminster-and-us-poll-error-models`, GitHub principalfish/principalfish.github.io#78, based on this branch): thread 4's Westminster Phase 2 (2a–2f) and the US model refinements 4b–4d. That includes the whole 2a investigation: H1 `eaa73607`, H2 `88bfc2a0`, H3 `44dd8336` (failed the gate at 14/20; report committed as evidence). Those commits stay on this branch; the code is offline-only and touches no production path.
- **Westminster ships as built, with no caveat label**, even though the review found its seat ranges and odds too narrow (W-1, W-2).
- **Stays here:** 4a (small US fixes, including the A-1 Senate bug), 3a (display), the style cleanup, the gated US calibration and 2026 back-run, the Westminster backfill, and the merge.
- **Merge order (Phil, 2026-11-07):**
  1. finish issue 0002 (2026 US midterm results import) and merge it into master;
  2. merge master into this branch;
  3. ship this branch.

## Approach

Get the branch to a mergeable, honest state with the smallest set of changes. Code first: 4a, 3a and style, then a self-review. Then bring master in once 0002 has landed. Then do the gated live work in an order that leaves the published data complete at merge time:
- US calibration acceptance;
- the US 2026 back-run;
- scoring;
- Westminster backfill and export last, so its history runs to the latest date.

**Merging is publishing.** GitHub Pages serves master, and the exported data files are committed. Once this branch merges, whatever Westminster uncertainty export it carries goes live. The US stays dark unless its release flags are flipped (Piece 10).

## Pieces

Full text for each piece is in the named thread plan under `threads/`. Below is what's specific to shipping.

1. **4a: small US model fixes** (thread 4, Piece 4a). Model: frontier.
   - Files: `data/models/us/_common.py`, `data/model_support/us_simulation.py`, the calibration script/report, tests.
   - What:
     - A-1: moved to Piece 5a (Phil, 2026-11-07); it needs 2020 House results by district, which 0002 is to import.
     - US-3: seed the national G stream by date only, so all chambers share G.
     - US-4: always load the parent statewide residual on the ME/NE districts, plus a district term.
     - US-5: document the shrinkage (Phil, 2026-11-07): G stays on the 2.44 input error; the G-only output SD of 2.29 is recorded in the calibration report, ledger and console.
     - US-4 district term: new `district_scale` in `UsErrorConfig`, default 0 (Phil, 2026-11-07); the historical district-vs-state swing evidence goes in the calibration sources doc and the value is set at Piece 6.
   - Depends on: none.
   - Acceptance: tests for each fix; parity invariants green; `validate_us_uncertainty.py` deltas presented.
2. **3a: Westminster and US display** (thread 4, Piece 3a; Phil chose to ship it, 2026-11-07).
   - What: W-4, the MC median seat count as the headline or alongside it (Phil picks at the brief), with the deterministic count labelled "uniform-swing projection" and flagged when it falls outside its own 90% range. S-1: integer percentages, "<1%" and ">99%" at the extremes, stored to 3 dp; same rules on the US views.
   - Depends on: none.
   - Acceptance: `npm test`, `npm run minify:electionmaps`, the jsdom harness on both pages; screenshots or text dumps of the headline and popups presented.
3. **Style cleanup** (thread 4, Phase 6). One formatting-only commit; no behaviour change; the full suite passes unchanged. Model: `claude-sonnet-5-5`.
   - Note: 0003 branches from this branch. Doing this before 0003 cuts its branch saves it a large formatting merge. If 0003 has already started, it merges this in later.
   - Depends on: 1, 2.
4. **Self-review of the branch.** Run `/review self poll-errors-simulation`. Nothing since the 2026-10-26 review has been reviewed: Pieces 0 and 1a–1c (`c067f150` → `92743751`), the 2a experiment and H1–H3, and Pieces 1–3 above. Fix what Phil picks.
   - Depends on: 3.
5. **Merge master in** (after 0002 is merged into master).
   - What: `git merge master` on this branch. Overlapping files:
     - regenerate the generated ones (`map-modes.json`, `map-modes.md`, `model_output_trends.json`, `prediction-simulation.json`, the minified bundles) rather than hand-merging them;
     - expect real conflicts in `uselectionmaps/data/map-modes-shell.json`, `data/scripts/export_elections.py` and `data/models/us/_common.py`.
   - Check: `run_us_senate_model.py` picks its races from `parliamentFeatures.us_senate.nextElectionYear`. If 0002 moves that to 2028, a 2026 Senate back-run would target the wrong class. Pin the cycle for the back-run, or confirm 0002 leaves it alone. House and President baselines are hard-coded to 2024 in their run scripts.
   - Depends on: 4 and 0002 merged. Needs Phil's go-ahead to push the merge commit.
   - Acceptance: full checks green; `export_elections.py --dry-run` clean.
5a. **A-1: Senate regional own-poll branch** (thread 4, Piece 4a's A-1; split out 2026-11-07).
   - What: `senate_fallback_swings` (`_common.py:2432`) uses the regional poll minus the 2020 Senate field pooled by census division. Replace that with the regional poll minus the matching-year House vote pooled by division, from 2020 House results by district (state → division is enough; 2020 district lines differ from map 21). Add a test.
   - Why it waits: the DB has House results for 2022 and 2024 only, and the anchor catalogue is national-only. Phil wants 0002 to import the 2020 House results first. No US poll has a `region_id` today, so the branch is dormant.
   - Depends on: 5 (0002 merged, with 2020 House results).
6. **US calibration acceptance** (thread 2 Piece 3; thread 3's corrected-comparison review and calibration acceptance). Phil's go/no-go on the House, Senate and President scales. The review found them weakly identified: House G from 6 cycles; Senate/President carry no numeric scales yet. Present each scale with its evidence and the 2026-11-07 caveat that 4b–4d are deferred to 0003.
   - Depends on: 1. It can be reviewed before 5; the back-run waits for 5.
7. **US 2026 back-run** (thread 2 Piece 14 pilots and backfill; thread 4 Piece 4e; thread 3's live reruns). Backup → scratch pilots reviewed → authorised backfill of the 2026 House and Senate runs with uncertainty, including the anchored Senate rerun (removes the legacy +5.4 pt D shift). The President has no 2026 race.
   - Depends on: 5, 6. Each live step needs Phil's go-ahead at the time.
8. **US 2026 scoring** (thread 2 Piece 13). Score the frozen pre-election forecasts against 0002's imported results.
   - Decisions when it starts:
     - 0002 imports provisional results (NYT), while Piece 13's contract asks for certified results with strict identities. Score provisionally now and re-score once certified, or wait.
     - No US uncertainty ran live before polling day. The back-run is a replay with pre-election inputs and pre-election code (`c7c994af`, 2026-10-25). Does that count?
   - Can be split into its own issue if Phil prefers; 0002 had planned scoring separately.
   - Depends on: 7 and 0002.
9. **Westminster backfill and export** (thread 4 Phase 5; thread 1 "Full backfill published"). Backup → rerun 2024-07-05 to the latest date under the current config → backup → `rebuild_model_trends.py` → staged export to `--output-base` and sanity checks → the real export with approval → commit the data files separately. Live uncertainty rows currently stop at 2026-10-22; master's daily runs since then have none.
   - Depends on: 5 (so it runs on the merged code), and 1–2 (so the export has 3a's keys). Do it last before the merge.
10. **US publication decision** (thread 2's publication checkpoint). Flip the US release flags for the back-run bands and history, or merge with the US dark.
    - Depends on: 7 (and 8, if scoring should come first).
11. **PR text.** `/builder poll-errors-simulation finish` drafts `pr.md`. Phil opens and merges the PR.
    - Depends on: everything above.

## Checkpoints

- [x] Threads 1–4: 40 completed checkpoints, recorded with their commits in each thread plan's `## Checkpoints` (thread 1: 12, thread 2: 16, thread 3: 8, thread 4: 4).
- [x] T4 · Piece 2a-H1: 1997→2001 and 2005→2010 fixture built and audited (`eaa73607`).
- [x] T4 · Piece 2a-H2: six-transition experiment contract revised and tested (`88bfc2a0`).
- [x] T4 · Piece 2a-H3: six-transition experiment run and reported to Phil (failed the gate; report committed as evidence, `44dd8336`).
- [x] T4 · Pieces 2a–2f and 4b–4d moved to issue 0003 (2026-11-07).
- [ ] 1 · T4 Piece 4a: Small US model fixes (US-3, US-4, US-5; A-1 is 5a).
- [ ] 2 · T4 Piece 3a: Westminster and US display changes.
- [ ] 3 · T4 Phase 6: Style cleanup.
- [ ] 4 · Branch self-review done and chosen fixes committed.
- [ ] 5 · Master (with 0002) merged in; checks green.
- [ ] 5a · T4 Piece 4a A-1: Senate regional own-poll branch uses a matching-year House regional reference.
- [ ] 6 · T2 Piece 3 and T3: US calibration accepted, or release stays gated.
- [ ] 7 · T2 Piece 14, T4 Piece 4e, T3 live reruns: US 2026 back-run done with authorisation.
- [ ] 8 · T2 Piece 13: US 2026 scoring report (or split to its own issue).
- [ ] 9 · T4 Phase 5 and T1: Westminster backfill and export done with authorisation.
- [ ] 10 · T2: US publication decided.
- [ ] 11 · PR text drafted.

When ticking a checkpoint here, also tick it in its thread plan and add the commit there, so the two never disagree.

## Verification

From thread 4, the latest and most complete set. Run from `data/` unless noted.
- `./run_tests.sh`: strict mypy gates it, then pytest. Baseline at the last commit: mypy 304 files, 6254 tests passed.
- `./election_data/bin/python scripts/export_elections.py --dry-run`
- `./election_data/bin/python -c "from console import create_app; create_app()"`
- After model pieces: `./election_data/bin/python scripts/validate_westminster_uncertainty.py`. Compare with the committed baseline report and present the deltas.
- After front-end pieces, from the repo root: `npm test` (baseline 658 passed), `npm run minify:electionmaps`, and the jsdom harness on the Westminster and US pages.
- `git diff --check`. Stage only that piece's files, and make sure `model_output_trends.json` isn't staged unintentionally.

## Notes

- 2026-10-30: Consolidated from four `~/.project-ai` plans and six reviews into this issue; the originals were copied unchanged into `threads/`.
  - Every non-merge commit on the branch is accounted for in a thread. `d50922ea` (tracker band smoothing) isn't cited by SHA, but it sits within thread 1's tracker work, between `423a8419` and `9bb8c2c8`.
  - The branch is on origin, with 10 local commits not yet pushed (`c067f150` onward and earlier). `/builder` pushes the branch after its next approved commit.
- **Decisions to take to Phil when their piece starts**, from thread 4:
  1. W-1 cluster definition and how 2024 is weighted in the fit (2a);
  2. whether to add the W-2 regime/fragmentation scaling (2b);
  3. S-2: trailing window or common random numbers (1c; the committed tracker look-ahead fix is `f417a14e`);
  4. W-4: MC median as the headline, or shown alongside (3a);
  5. the US-2 correlation data source (4d);
  6. go-aheads for each live run: 4e and Phase 5.
- **Authorisations:** the thread Notes record approvals given in earlier sessions (for example "existing all-pieces authorization"). Under the current workflow every piece still stops for review before its commit. Live database writes, backfills, reruns and publication each need a fresh, explicit go-ahead.
- 2026-10-30: Resumed. Explained the full flow for the historical-evidence route; Phil chose **scope A** (1997→2001, 2005→2010), **entry "both"** (training augmentation in every fold plus separate held-out diagnostics; gate stays 17/20 on the original 20 cells) and **storage as a frozen in-repo fixture** (no DB write or export). Next: Piece 2a-H1 (acquire and audit both transitions into the fixture, then pause), 2a-H2 (contract revision, then pause before the run), 2a-H3 (run and report). Resume check corrected: `3ead7c58…1428` is the report's internal `report_hash` (verified), not the file's `sha256sum` (`e732edbd…b317`).
- 2026-10-30 (builder): 2a-H1 implemented and paused before commit. Deviation: 1997/2001/Scotland-2005 taken from Parliament's psephology.db at a pinned commit rather than scraped HTML (Phil accepted). Phil confirmed Referendum→other, NI Con→con, NI/Scottish Greens→green, and the 2005n unknown_other limitation. CBP-8647 returned 403; no independent national totals available (recorded as `independent: false`). `_content_hash` in `westminster_validation_inputs.py` made public (`content_hash`) for reuse. Zero-vote 2005n party entries (5 seats): Phil chose to drop them in the builder and reject them in the loader.
- 2026-10-30: 2a-H1 committed as `eaa73607` and pushed (the push also published the 10 earlier local commits). Fixture sha256 `3e351456e756169699eacf15f53b7a06cb111fb8a9370c3ef4152b5b3ad88bb5`. Next: 2a-H2, after Phil answers its two questions.
- 2026-10-30: H2 questions answered by Phil: equal weight across all six transitions; the two new held-out folds are report-only (no gate, no guard). H2 not started.
- 2026-10-30: 2a-H2 implemented (uncommitted). New contract `bce3c3f55090dd05a4e1096308fd3161d04bf13b0038d5f83215178e4d7d82f2` via `--contract six-transition`; four-transition stays default and reproduces the committed report. Phil confirmed: 2005n unknown_other is its own additive, unfitted column (it swings out in 2010; 2010 Alliance/unnamed Greens are excluded new candidacies); Speaker votes count under Others in regional totals as in the DB path (seat still excluded); synthetic recovery trains on 5 synthetic transitions to match the folds; 1997/2001 seats get internal ids from 1,000,000. Timing correction: the 15/20 batch took ~28 min (checkpoints 09:02→09:30, 2026-10-29), not multiple hours.
- 2026-10-30: H2 analysis loop. Correctness found that elections.db stores all Scottish 2010 minor parties (Greens included) as Others; Phil chose to fold Scottish 2005n Green votes into Others for transitions into DB 2010 (fixture unchanged; recorded in the contract, new six-transition hash `0fbda040e90b9bef1a3960c260965b6c0dcd257ba06e9eb329d7f7a7e5c30b49`; four-transition hash unchanged). A party × nation audit found no other case of that pattern. Phil accepted as documented limits: NI Alliance/NI Greens and ~120 English Green seats unnamed in 2005n (their 2010 cells excluded as new candidacies); NI 2005n names no minor parties.
- **Open data-quality issue (logged 2026-10-30, not in scope for H2):** the DB's 2010 NI rows have no Conservative row (UCUNF appears stored as UUP) and no UUP votes in Belfast West, Mid Ulster and Fermanagh & South Tyrone (Fermanagh likely a genuine stand-aside). This also feeds the existing 2010→2015 transition. Investigate read-only against sources before any DB correction; any write needs Phil's go-ahead.
- 2026-10-30: 2a-H2 committed as `88bfc2a0` and pushed. Phil gave the go-ahead for the H3 batch the same day.
- 2026-10-30: 2a-H3 batch run 15:56→16:52 UTC (~57 min), exit 0. Report `data/models/westminster/uncertainty_conversion_historical_report.json` (sha256 `0893389afce7babd00c69e98bdea9331113e0ffe900568c64ce1d602ebc59ab4`, report_hash `50f2f5ea…`, verified by `read_report`), uncommitted pending Phil. Console route 200. **Result: fail, `stop_before_engine`.** No-cluster 13/20 (unchanged); cluster-gaussian 15/20 (unchanged); cluster-student incl. the candidate 14/20 (was 15; 2015 SNP and Con now missed). RMS fails in every variant (candidate 3.32/3.22 vs reference ~0.42–1.54); drift fails in 7/8. 2024 still 1/5 (Lab 322–358 vs 411; Con 188–237 vs 121). Report-only: 1997→2001 3/5 (Lab 376–407 vs 411, Con 168–205 vs 166: same direction as 2024); 2005n→2010 4/5 for the candidate. Next: Phil's call on the fail branch (replan with new assumptions; 1983–92 a candidate).
- 2026-10-30: Phil had the failed report committed as evidence: `44dd8336` (file sha256 `0893389a…9ab4`, matching the run), pushed to `origin/poll-errors-simulation`.
- 2026-11-07: Resumed. H3 ticked; `resume.md` rewritten. Open: the fail-branch decision.

- 2026-11-07: **Ship decision.** Phase 2 (2a–2f) and 4b–4d moved to issue 0003 (#78). Westminster ships as built with no caveat; 3a ships; merge order is 0002 → master, then master → this branch, then ship. Pieces and checkpoints rewritten around shipping.
- 2026-11-07 (builder, Piece 1 brief): Phil chose US-5 = document the shrinkage; US-4 = new `district_scale`, default 0. A-1 split out to Piece 5a: Phil wants 2020 House results by district imported in 0002 first (the DB has House 2022/2024 only). `validate_us_uncertainty.py` only captures and scores saved runs, so Piece 1's deltas come from read-only scratch simulations instead.

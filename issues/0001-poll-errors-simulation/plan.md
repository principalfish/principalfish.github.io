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
- **Stays here (after the UK-only re-scope, below):** 3a (display), the style cleanup, a self-review, merging master in, the Westminster backfill and export, and the PR text. All US work, 4a included, is in issue 0004.
- ~~Merge order: 0002 → master, then master → this branch, then ship.~~ Superseded the same day: the UK ship doesn't wait for 0002.

## Approach

Get the branch to a mergeable, honest state with the smallest set of changes. Code first: 4a, 3a and style, then a self-review. Then bring master in once 0002 has landed. Then do the gated live work in an order that leaves the published data complete at merge time:
- US calibration acceptance;
- the US 2026 back-run;
- scoring;
- Westminster backfill and export last, so its history runs to the latest date.

**Merging is publishing.** GitHub Pages serves master, and the exported data files are committed. Once this branch merges, whatever Westminster uncertainty export it carries goes live. The US stays dark unless its release flags are flipped (Piece 10).

## Pieces

**Re-scoped 2026-11-07 (second time): UK only.** Phil: "remove uncertainty from US and ship UK". All US work, including the US poll trackers and the three ungated US deterministic fixes (Senate anchor, phantom party, Senate roster pin), moved to issue 0004 (`us-poll-errors-uncertainty`). Its branch was created at `44dd8336` and pushed. The UK ship no longer waits for 0002. The earlier ship-plan pieces for US work (4a, 5a A-1, US calibration, back-run, scoring, publication) are in 0004's `plan.md`.

1. **Remove the US work from this branch.** Model: frontier.
   - What: one removal commit, so the branch's diff against master carries only UK work.
     - Revert or delete the US-only files to their merge-base (`7736a80e`) state.
     - Strip the US parts of the shared files. `40d3933f` (the last UK commit before the US thread) is the reference: the UK core is byte-identical between it and HEAD.
     - Hand-port the UK hunks of mixed commits. `f417a14e` (the tracker look-ahead fix) sits on the US descriptor refactor. `model-descriptors.js` exports `WESTMINSTER_DESCRIPTOR`, which the UK tracker imports.
     - Keep `4eb2646b`'s generic poll repair.
     - Rebuild the bundles.
     - The file map is in 0004's `us-file-commit-map.txt`.
   - Acceptance:
     - `git diff master...HEAD` shows no US files and no US hunks;
     - `uselectionmaps/` sources and data are identical to the merge base, and `uselectionmaps.min.js` differs only by the shared engine's UK code, which stays inert on the US page (no `uncertaintyDataPath`; the jsdom US run is the evidence);
     - full checks are green: mypy, pytest, `npm test`, minify, export dry-run, console smoke, and the jsdom harness on both pages, with the US page behaving as master.
1b. **Remove the offline conversion experiment from this branch** (Phil, 2026-11-07). Model: frontier. Depends on: 1.
   - What: one removal commit (no history rewrite, no force push), so the branch's diff against master carries only what ships. It covers everything the four experiment commits added or changed: `ab3fc62e` (offline joint conversion experiment), `eaa73607` (1997–2010 historical fixture), `88bfc2a0` (six-transition contract) and `44dd8336` (failed six-transition report).
     - Delete: `data/model_support/westminster_conversion_{checkpoint,design,report,validation}.py`, `data/model_support/westminster_historical_transitions.py`, `data/scripts/validate_westminster_conversion.py`, `data/scripts/build_westminster_historical_fixture.py`, `data/tests/test_westminster_conversion_{report,validation,historical}.py`, `data/tests/test_westminster_historical_transitions.py`, `data/console/templates/uncertainty_conversion_validation.html`, `data/models/westminster/conversion-prototype-method.md`, `data/models/westminster/uncertainty_conversion_prototype_report.json` (6.3 MB), `data/models/westminster/uncertainty_conversion_historical_report.json` (10.5 MB), `data/models/westminster/historical-transitions-v1.json` (0.7 MB).
     - Strip: the conversion routes and imports in `data/console/blueprints/uncertainty.py`, the experiment link in `data/console/templates/uncertainty_validation.html`, and the experiment sections of `README.md`.
     - Undo both refactors: inline `historical_pair` back into `load_pairs` in `data/model_support/westminster_validation.py` (as at `92743751`), and rename `content_hash` back to `_content_hash` in `data/model_support/westminster_validation_inputs.py`.
     - Keep the validation harness for the shipped model: `validate_westminster_uncertainty.py`, `validation-inputs-2026-10-22-v1.json` and `uncertainty_validation_report.json` (from `c067f150`, `92743751`).
     - The experiment is kept on issue 0003's branch (see Notes, 2026-11-07).
   - Acceptance:
     - `git diff master...HEAD --stat` lists none of the files above, and `westminster_validation.py` and `westminster_validation_inputs.py` match `92743751` apart from any non-experiment changes made since;
     - `git grep -n -E "westminster_conversion|historical_transitions|uncertainty_conversion|historical_pair"` on the branch is empty;
     - `validate_westminster_uncertainty.py` still reproduces the committed `uncertainty_validation_report.json`;
     - full checks are green: mypy, pytest, `npm test`, export dry-run, console smoke (the `/uncertainty` validation page still renders).
1c. **Regenerate the committed Westminster validation report** (Phil, 2026-11-07). Model: frontier. Depends on: 1b.
   - What: one small commit containing only `data/models/westminster/uncertainty_validation_report.json`, regenerated with `./election_data/bin/python scripts/validate_westminster_uncertainty.py`. It fixes a gap that predates 1b: the report was never regenerated after `92743751` switched the replay to frozen inputs. Reads the live DB only; the one write is that repo file (Phil's go-ahead given 2026-11-07).
   - Acceptance:
     - against the committed version, the diff touches only `replay` and `identity.source_sha256`, with `historical`, `national`, `methods` and `limitations` unchanged (present the diff summary);
     - a second run reproduces the new file byte-for-byte;
     - `./run_tests.sh` stays green, and no other file is staged (`model_output_trends.json` in particular).
2. **3a: Westminster display** (thread 4, Piece 3a). W-4 (confirmed by Phil 2026-11-07): the MC median seat count is the headline, with the deterministic count labelled "uniform-swing projection" and flagged when it falls outside its own 90% range. S-1: integer percentages, "<1%" and ">99%", stored to 3 dp. Westminster views only now.
   - Acceptance: `npm test`, `npm run minify:electionmaps`, the jsdom harness on both pages; screenshots or text dumps presented.
3. **Style cleanup** (thread 4, Phase 6). One formatting-only commit; no behaviour change. Model: `claude-sonnet-5-5`. Depends on: 1, 1b, 2.
4. **Self-review of the branch.** `/review self poll-errors-simulation` on the UK-only diff. Fix what Phil picks. Depends on: 3.
5. **Merge master in.** `git merge master`; regenerate the generated files rather than hand-merging them. Needs Phil's go-ahead to push. Depends on: 4. Acceptance: full checks green; `export_elections.py --dry-run` clean.
6. **Westminster backfill and export** (thread 4 Phase 5; thread 1 "Full backfill published"). Backup → rerun 2024-07-05 to the latest date under the current config → backup → `rebuild_model_trends.py` → staged export to `--output-base` and sanity checks → the real export with approval → commit the data files separately. Live uncertainty rows stop at 2026-10-22. Each live step needs Phil's go-ahead at the time. Depends on: 5. Do it last before the merge.
7. **PR text.** `/builder poll-errors-simulation finish`. Phil opens and merges the PR.

## Checkpoints

- [x] Threads 1–4: 40 completed checkpoints, recorded with their commits in each thread plan's `## Checkpoints` (thread 1: 12, thread 2: 16, thread 3: 8, thread 4: 4).
- [x] T4 · Piece 2a-H1: 1997→2001 and 2005→2010 fixture built and audited (`eaa73607`).
- [x] T4 · Piece 2a-H2: six-transition experiment contract revised and tested (`88bfc2a0`).
- [x] T4 · Piece 2a-H3: six-transition experiment run and reported to Phil (failed the gate; report committed as evidence, `44dd8336`).
- [x] T4 · Pieces 2a–2f and 4b–4d moved to issue 0003 (2026-11-07).
- [x] US work moved to issue 0004; branch `us-poll-errors-uncertainty` at `44dd8336` pushed (2026-11-07).
- [x] 1 · US work removed from this branch; checks green (`5a96e52c`).
- [x] 1b · Offline conversion experiment removed from this branch; checks green (`39610fb9`).
- [x] 1c · Westminster validation report regenerated and committed (`2618c0dd`).
- [x] 2 · T4 Piece 3a: Westminster display changes (`860b0d85`).
- [ ] 3 · T4 Phase 6: Style cleanup.
- [ ] 4 · Branch self-review done and chosen fixes committed.
- [ ] 5 · Master merged in; checks green.
- [ ] 6 · T4 Phase 5 and T1: Westminster backfill and export done with authorisation.
- [ ] 7 · PR text drafted.

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
- 2026-11-07: **Re-scoped to UK only** (Phil, during Piece 1's US-4a build, before any code was written). US work → issue 0004, branch `us-poll-errors-uncertainty` (`44dd8336`, pushed). Phil: move the US central fixes and US trackers too; leave 0003 as is (it still lists 4b–4d); don't wait for 0002. The 4a brief and Phil's US-4/US-5 choices are carried into 0004's plan.
- 2026-11-07: Piece 1 committed as `5a96e52c` and pushed (162 files; branch diff vs merge base 318 → 184 files). Checks: mypy 257 files, pytest 5971, vitest 591. Analysis loop: security, style and common-mistakes clean; correctness found no lost UK hunks and no US residue; coverage gaps fixed by porting the shared poll-tracker view, controller identity and trends tests to Westminster fixtures, and pinning the `_segment_id` hash. Deviation: `uselectionmaps.min.js` differs from the merge base by the shared engine's inert UK code (acceptance reworded). Live DB read-only: 840 uncertainty rows, all Westminster v1; US `evidence_kind` all NULL.
- 2026-11-07: **Experiment moved out** (Phil). The offline conversion experiment (`ab3fc62e`, `eaa73607`, `88bfc2a0`, `44dd8336`; ~17.5 MB of JSON) comes off this branch in one removal commit (Piece 1b), not a history rewrite. If the PR is squash-merged the blobs stay out of master; otherwise they're in its history. The experiment lives on in issue 0003. Note for any branch cut before 1/1b (0003, and 0004 at `44dd8336`): merging this branch or master into it later would apply the removals and delete its US work or experiment, so that merge needs the removal commits neutralised.
- 2026-11-07 (builder, Piece 1b): removal staged, paused for Phil's review (uncommitted). 21 files: 16 experiment-only files deleted; `uncertainty.py`, `uncertainty_validation.html`, `westminster_validation.py` and `westminster_validation_inputs.py` restored byte-for-byte to `92743751` (no other commit touched them); README's experiment section (28 lines) removed. Branch diff vs merge base 184 → 168 files. Acceptance grep empty. Checks: mypy 246 files clean; pytest 5668 passed (5971 → 5668 with the four experiment test files gone); vitest 588 passed / 3 skipped; export dry-run exit 0; console builds; `/westminster/uncertainty/validation` 200, `/…/conversion-validation` 404; `git diff --check` clean. Analysis loop: all five checks clean.
  - **Pre-existing gap (not caused by 1b):** the committed `uncertainty_validation_report.json` (from `c067f150`) was never regenerated after `92743751` switched the replay to frozen inputs. A run now matches it on `historical`, `national`, `methods` and `limitations`, but differs in `replay` (the frozen-input fields) and in `identity.source_sha256`. A run at `5a96e52c` (pre-1b) gives the same output as post-1b, apart from the two restored files' source hashes. So 1b changes no validation result. Regenerating the committed report is Phil's call.
- 2026-11-07: Piece 1b committed as `39610fb9` and pushed (21 files, −529,284 lines). Next: Piece 1c.
- 2026-11-07 (builder, Piece 1c): report regenerated, paused for review (uncommitted). The diff touches only `identity.source_sha256` (four hashes updated, plus `westminster_validation_inputs.py`, which was missing) and `replay`, which gains six metadata fields for the frozen-input source. `report_hash` goes 08266bae → 6c36090c. No number changed: `variants`, `seed_spread`, `historical`, `national`, `methods` and `limitations` are identical. A second run is byte-identical. Correctness check clean (hashes match HEAD files and the fixture; `read_report()` verifies). mypy 246 clean, pytest 5668 passed, `git diff --check` clean, console `/westminster/uncertainty/validation` 200 showing "Input source: committed frozen inputs". Only the report file is modified.
- 2026-11-07: Piece 1c committed as `2618c0dd` and pushed. Next: Piece 2 (3a); W-4 is settled (MC median headline).
- 2026-11-07 (builder, Piece 2 brief): Phil chose the following.
  - **Table:** the "90% seats" column becomes "median (lo–hi)", headed "Simulated (90%)". The Seats header reads "Uniform swing" (tooltip "uniform-swing projection") for saved models with uncertainty. A uniform-swing count outside its own 90% range gets a † marker, with a tooltip and visually hidden text. Map colours, sorting and ± stay on the uniform-swing count, because medians don't sum to 650.
  - **Subtitle headline:** leads with the leader's median and range, then the odds: "Labour 282 seats (199–359) · largest party >99% · majority 19% · hung 81% (if an election were held today)".
  - **S-1:** whole percentages, with "<1%" for anything that rounds to 0 (exact 0 included) and ">99%" for anything that rounds to 100 (exact 1 included).
  - Follow-up (Piece 2 build): Phil chose to leave the main subtitle ("Hung parliament - largest party Labour with 300 seats", uniform swing) unchanged above the new MC headline.
- 2026-11-07 (builder, Piece 2): implemented and paused for Phil's review (uncommitted).
  - **Checks:** mypy clean; pytest 5670 passed; Vitest 621 passed / 3 skipped with jsdom (`PF_TEST_JSDOM`); bundles rebuilt; `git diff --check` clean; jsdom harness shows 0 errors on both pages, and the US page is byte-identical to a HEAD build.
  - **Analysis loop, first pass:**
    - security: clean, with an optional escape suggestion;
    - style and common-mistakes: `map-modes.md` still said 4 dp (fixed);
    - correctness: popup escaping, half-percent float rounding, and the `_ROUNDING` slack for double rounding (stored 4 dp, published 3 dp) (all fixed);
    - coverage: no `renderVoteTotals` test (new `tests/vote-totals-view.test.js`).
  - **Second pass:** the tenths-first rounding misrounded 4 dp inputs, which the committed data still holds. It now uses `Math.round(Number((value * 100).toPrecision(12)))`, verified exact against half-up rounding for every 3 dp and 4 dp value. One low coverage gap remains: no jsdom popup test asserts that "<1%" is escaped. The escape itself is verified in the harness dump.
  - **Open note:** on a map with both uncertainty and an electoral-vote tally, "Uniform swing" would replace "EV". No such map exists.
- 2026-11-07: Piece 2 committed as `860b0d85` and pushed. Next: Piece 3 (style cleanup, Sonnet).
- 2026-11-07 (builder, Piece 3 brief): Phil chose the following.
  - **Tooling:** `ruff` (pinned 0.8.4) in a throwaway scratch venv; the project venv and requirements are untouched.
  - **Scope:** `ruff format` whole only on the 50 `.py` files this branch adds. In the 55 modified files, fix only the review's §E items (I001 import order, unused imports, comments on function-level imports, `Mapping` import), so master's code isn't churned.
  - **TypeError:** `console/services/trends.py:58` becomes `TypeError` only if no caller catches `ValueError` on that path. The other listed cases were US-only and are gone.

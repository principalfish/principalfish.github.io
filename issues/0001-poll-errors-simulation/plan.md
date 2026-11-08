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
6a. **Westminster poll weighting: 15-day half-life, 60-day window** (Phil, 2026-11-07). Model: frontier. Depends on: 5.
   - What: one commit changing the Westminster defaults so the console's daily run and the backfill use the same weighting.
     - `data/console/blueprints/westminster.py:142-143`: the form defaults `since_days_back` "30" → "60" and `half_life_days` "30.0" → "15.0", with 15.0 added to the `half_life_days` menu (`:79`, now 7/14/21/30/45/60/90).
     - `data/models/westminster/run_uns_model.py`: CLI defaults `--half-life-days` 30.0 → 15.0 (`:299`), `--since-days-back` 30 → 60 (`:319`), `--lookback-days` 365 → 60 (`:338`); docstrings to match (`:274-283`).
     - `README.md` (e.g. `:746` "`--lookback-days 365`", "`--half-life-days 30`") and any other Westminster doc or console text stating the old values.
     - Tests that pin these defaults.
     - Holyrood, US and other models are untouched.
   - Acceptance:
     - `git grep` finds no Westminster default of 30-day half-life, 30-day since or 365-day lookback left in code or docs;
     - console smoke: the Westminster form shows 60 and 15.0 selected;
     - the validation harness (`validate_westminster_uncertainty.py`) still reproduces `uncertainty_validation_report.json`, or, if its replay depends on these defaults or its hashed sources change, regenerate it in this commit and show which fields moved;
     - full checks green.
6. **Westminster backfill and export** (thread 4 Phase 5; thread 1 "Full backfill published"). Depends on: 6a. Steps, each with Phil's go-ahead at the time, relayed through the planner chat:
   1. backup (done 2026-11-07, pre-first-rerun);
   2. rerun 2024-07-05 → 2026-10-30 with `--half-life-days 15 --lookback-days 60` passed explicitly, plus `--continue-on-error`. The first rerun (2026-11-07, 365-day window and 30-day half-life by mistake) is superseded; its state is in backup `elections-2026-11-07-164913.db.gz`;
   3. backup;
   4. `rebuild_model_trends.py --model westminster --map-id 2`;
   5. staged export to `--output-base` in scratch, with sanity checks;
   6. the real export;
   7. commit the data files separately.
   - Precondition: no poll imports or model runs from the main checkout's console until this branch merges (master's console still has the old defaults and no uncertainty).
6b. **Westminster main headline line leads with the simulation** (Phil, 2026-11-07). Model: frontier. Depends on: 6 (its data commit can land first; this piece doesn't touch data).
   - Why: with the 15/60 data, the main subtitle line built from the uniform swing ("Labour majority: 58", 354 seats) contradicts the Monte Carlo line beneath it (319 seats, majority 44%, hung 56%). In Piece 2, Phil had chosen to leave the main line unchanged, when both lines said hung.
   - What: when uncertainty is shown (`activeUncertainty(state)`, so Westminster model runs only), the main line (`.maps-subtitle-main`, `electionmapslogic/dom.js:108-110`, text from `state.js`'s subtitle builder) leads with the simulation: the most likely outcome and its probability, then the largest party's probability, e.g. "Hung parliament likely (56%) · Labour largest party (99%)". The second line becomes the leader's median, range and majority odds: "Labour 319 seats (238–389) · majority 44% (if an election were held today)".
     - Most likely outcome: the highest probability among each party's majority and hung. Use "likely" when it is ≥50%, "most likely" when below. Ties and edge wording go to Phil at review.
     - Without uncertainty (historical elections, US page, poll tracker, predict view), the main line is unchanged.
     - The uniform-swing count stays in the table's "Uniform swing" column.
   - Files: `electionmapslogic/uncertainty.js` (headline text, `uncertaintyHeadline`), `electionmapslogic/state.js` (subtitle builder), `electionmapslogic/dom.js` (main-line choice), the bundles via `npm run minify:electionmaps`, and tests in `electionmapslogic/tests/`.
   - Tests: the main line from the simulation on a saved model with uncertainty (likely, most likely, majority-leader cases); the unchanged UNS line without uncertainty, in predict view and on the US page; escaping by `textContent`.
   - Acceptance: `npm test` with jsdom suites running (0 jsdom skips), `npm run minify:electionmaps`, and the jsdom harness on both pages against the step-6 data. Show the rendered subtitle text.
   - **Decisions (Phil, 2026-11-07, at the 6b briefing):**
     - keep the election-name prefix ("Current prediction · Hung parliament likely (56%) · Labour largest party (99%)");
     - majority case: keep the same shape, "Labour majority likely (72%) · Labour largest party (99%)", and the second line adds "· hung N%" whenever the main line doesn't name hung;
     - ties: name both. An outcome tie reads "Hung parliament or Labour majority equally likely (50%)"; a largest-party tie reads "Labour and Reform UK level as largest party (50%)", and the second line shows both parties' medians;
     - show it whenever the run has uncertainty (`activeUncertainty`), toggle or not;
     - builder's recommendations taken: the likely/most likely cut-off uses the displayed (rounded) %, from one shared rounding helper for both lines; the second line always follows the largest-party favourite; historical elections, US, predict view, user scenarios, the poll tracker and `document.title` are unchanged.
6c. **Self-review 2 fixes, and keep the election-day point in the tracker** (Phil, 2026-11-07; `reviews/02-2026-11-07.md`). Model: frontier. Depends on: 6b.
   - What:
     - Review fixes 1–3:
       - the `electionmapslogic/dom.js:59-60` subtitle comment names `simulatedSubtitle`;
       - `dom.js:67-71` `renderHeader` `@param text` says it's replaced by the simulated line when the run has uncertainty;
       - `README.md:745-747` says the 60-day lookback matches the single-date `--since-days-back` default.
       - Rebuild the bundles.
     - Election-day point: master's `model_output_trends.json` starts on 2024-07-05 with the unpolled election-result point (Lab 411 seats, 33.7%); the branch's starts on 2024-08-07, because `data/model_support/trends.py` drops runs with no national polls. Keep the history's first (election-day) run even when unpolled, with no uncertainty, so the tracker starts on 2024-07-05 as it does now. Later unpolled runs stay dropped.
   - **Decisions (Phil, 2026-11-07, at the 6c briefing):**
     - (a) "Separate, no bands": 2024-07-05 is the election result (Lab 411, 33.7%) without uncertainty, and the existing rule breaks the line at 2024-08-07, where uncertainty starts. Only `trends.py`'s keep rule changes; no JS segment-rule change.
     - (b) "Leading unpolled runs": keep unpolled runs before the history's first polled run, without their uncertainty; identical ones collapse to 2024-07-05. Unpolled runs after the first polled run stay dropped. Update the `trends.py` module docstring.
     - (c) live steps: the trends rebuild only (`rebuild_model_trends.py --model westminster --map-id 2`, reads the DB, writes the tracked trends file). No re-export, DB write or backup. Expected 384 points, 2024-07-05 → 2026-10-30, with one break at 2024-08-07.
     - Commits: code (review fixes 1–3, the `trends.py` rule, tests, bundles), then data (the trends file).
   - Tests: `trends.py` keeps the unpolled election-day run and still drops later unpolled runs; the tracker parse of the rebuilt file shows the chosen behaviour at 2024-07-05 → 2024-08-07, with no other breaks.
   - Acceptance: `./run_tests.sh` (then restore any test-written trends file), `npm test` with 0 jsdom skips, `npm run minify:electionmaps`, and the tracker parse of the rebuilt file. The live steps each need Phil's go-ahead, relayed through the planner chat. The code and data commits are separate.
6d. **Header reading note: below the latest poll, smaller, with the nowcast sentence** (Phil, 2026-11-07, after the manual check). Model: frontier. Depends on: 6c.
   - What:
     - Move the basis note (`#mapsUncertaintyBasis`, `.maps-subtitle-basis`, built in `electionmapslogic/uncertainty-view.js:15-22`) so it renders **below** "Latest poll used". Today it sits under the simulation line.
     - Stop stripping the exported "Nowcast: if an election were held today." sentence (`uncertainty-view.js:19-21`), so the note reads in full: "Nowcast: if an election were held today. 90% ranges and probabilities from 10,000 simulations, conditional on current polls and the model's error assumptions."
     - Drop "(if an election were held today)" from the second line (`uncertaintyHeadline` / `uncertaintyOutcomeLine` in `electionmapslogic/uncertainty.js`), since the note now says it.
     - Style the note smaller and not bold: about 0.85rem, normal weight, muted colour (`electionmapslogic/maps.css`, `.maps-subtitle-basis`).
     - Keep the `aria-describedby` hook (`dom.js:645`) working. Front end only: no export change, no re-export, no live step. Rebuild the bundles (`maps.min.css` too).
   - Tests: the note follows the latest-poll line in DOM order and contains the nowcast sentence; line 2 no longer ends "(if an election were held today)", in every headline case; pages without uncertainty show no note and are unchanged.
   - Acceptance: `npm test` with 0 jsdom skips, `npm run minify:electionmaps`, the jsdom harness on both pages, and the rendered header text shown in order.
6e. **Vote-totals table: one Seats column from the simulated median; the uncertainty toggle only adds the ranges** (Phil, 2026-11-07; queued, starts after 6d is committed). Model: frontier. Depends on: 6d.
   - Phil's words: "can we make uniform swing column just show the simulated median - and call that column Seats. toggling show uncertainty simply adds the (xxx-yyy) to the text in that column. +/- is based off the median. Similar for vote % - toggling uncertainty shows/hides the (xx.x%-yy.y%) text in the column."
   - What, when the run has uncertainty:
     - The "Uniform swing" column becomes **Seats**, showing the simulated median (e.g. 319). With "Show uncertainty" on it reads "319 (238–389)".
     - Its ± is median − 2024 seats.
     - **Vote %** shows the central share. With the toggle on it reads "27.45 (23.4–31.3%)", and the vote ± is unchanged.
     - The separate "Simulated (90%)" and "90% vote range" columns go. The toggle no longer adds columns, only the range text.
     - The † "outside its range" marker goes, since the uniform-swing count is no longer shown in the table.
   - Without uncertainty (historical elections, US page, predict view, user scenarios): the table is unchanged.
   - Files (expected): `electionmapslogic/dom.js` (`renderVoteTotals`, the seats header, the toggle), `electionmapslogic/uncertainty.js` / `uncertainty-view.js`, `electionmapslogic/maps.css`, the bundles, and tests in `electionmapslogic/tests/` (vote-totals and uncertainty views).
   - **Decisions (Phil, 2026-11-07, at the 6e briefing):**
     - (a) "Accept, add a note": a short note by the table, e.g. "Seats are simulated medians, so they needn't add up to 650. Map colours show each seat's uniform-swing winner."
     - (b) filtered views with no stored medians (party, majority, gains-only, upcoming): show the uniform-swing number under the plain **Seats** header (Phil: "Uniform swing number but column header of Seats"), with no ranges and the toggle hidden as today.
     - (c) "Drop it": the uniform-swing count doesn't appear in the whole-country or region table, not even as a hover title.
     - Tracker: "No, leave it". The poll tracker's seat line stays the uniform-swing central with the simulated band.
     - Builder defaults accepted: sort by the median (ties by name); the region view uses the region's medians, with ± against the region's 2024 seats; Vote % reads "27.45 (23.4–31.3%)", with vote ± unchanged; the toggle changes text only; remove the unused hide-columns CSS and the † code; predict view and US tables byte-for-byte unchanged.
   - Tests: Seats shows the median with and without ranges; ± from the median; Vote % with and without ranges; the toggle changes only the text; unchanged tables without uncertainty and on the US page; the filtered-view behaviour as decided.
   - Acceptance: `npm test` with 0 jsdom skips, `npm run minify:electionmaps`, the jsdom harness on both pages, and the rendered table text shown with the toggle on and off.
6f. **Poll tracker Chances view: remove the blank space on the left** (Phil, 2026-11-07; queued, starts after 6e is committed). Model: frontier. Depends on: 6e.
   - Phil's words: "blank space to the left on this polltracker view. Same with largest party", with a screenshot of the Chances view, Majority sub-view; the Largest-party sub-view does the same.
   - Seen in the screenshot (to confirm in the code): (1) an empty left-axis gutter. Horizontal gridlines run across a column with no labels, presumably the seat/vote axis the "Seats & votes" view uses, while Chances draws its probability axis on the right. (2) The x-axis starts on 2024-07-05 (the election-day point kept in 6c), but chance lines start on 2024-08-07, because the 07-05 point has no uncertainty, which leaves about a month of empty plot.
   - What: in the Chances views (Majority and Largest party), drop or collapse the unused left axis column, and start the x-domain at the first date with chance data; or whatever the builder's investigation shows is the cause. The "Seats & votes" view keeps its axes and its 2024-07-05 start.
   - Files (expected): `electionmapslogic/features/polltracker-view.js` (chart layout, axes, domain), `electionmapslogic/features/polltracker.js` if the domain comes from parsing, `electionmapslogic/maps.css`, the bundles, and tests in `electionmapslogic/tests/` (polltracker view).
   - **Decisions (Phil, 2026-11-07, at the 6f briefing):**
     - Causes, confirmed by the builder and checked by the planner: `pollTrackerDimensions()` always keeps `margin.left = 70`; `pollTrackerDrawGrid` (`polltracker-view.js:396`) draws `axisRight(...).tickSize(-innerWidth)` gridlines leftwards into the margin when seats are off (also on master, in "Seats & votes" with only Vote % ticked); and the x-domain starts on 2024-07-05 while chances start on 2024-08-07.
     - Gridlines cross the plot in every mode (a bug fix).
     - "Yes, same rule": no left axis drawn means no left gutter (about 16px), in Chances and in "Seats & votes" with only Vote % ticked. "Seats & votes" with Seats ticked is unchanged.
     - "First chance date": the Chances timeline starts on the first date with chance data (2024-08-07). "Seats & votes" keeps 2024-07-05.
   - Tests: in the Chances views, no empty left gutter and the domain as decided; "Seats & votes" unchanged; both sub-views.
   - Acceptance: `npm test` with 0 jsdom skips, `npm run minify:electionmaps`, the jsdom harness on the poll-tracker view (axes and domain dumped), and a before/after description.
6g. **Seats as the simulated mean, not the median, in the table and headline** (Phil, 2026-11-07; queued, starts after 6f). Model: frontier. Depends on: 6f.
   - Phil asked: "can the main seats figure in the table show the mean, not median"; chose "Mean everywhere".
   - Facts: the national block already exports each party's seat `hist`, so the national mean is computable (Labour 317.0 vs median 319 on 30 Oct). Region blocks store only `s: [p5, median, p95]`, with no mean or hist (DB `model_uncertainty.regions_json`). The latest run's raw draws are at `~/dbs/simulations/westminster-latest.npz` (written 18:36 by the 15/60 rerun). Means sum exactly to the seat total.
   - What:
     - export a mean seat count per party, national and per region, for the latest run;
     - the vote-totals Seats column and the headline's second line use the mean (with the 5–95 range unchanged), and ± is from the mean;
     - remove the "needn't add up to N" note (the displayed means add up, by largest-remainder rounding);
     - the history and trends are untouched, and no history rerun.
   - **Decisions (Phil, 2026-11-07, at the 6g briefing):**
     - (a) **Store it.** `data/model_support/monte_carlo.py`'s summary adds a `mean` per party (national and each region, 2 dp); `data/model_support/public_uncertainty.py` exports it plus the largest-remainder whole seats per block, which add up to the block's seat total. The rerun is "probably fine" (Phil): rerun only 2026-10-30 (same seed 739919, same simulations, mean added).
     - (b) Largest-remainder tie: **the larger unrounded mean first**, then party name. The rounding lives in the export (pytest-tested); the front end shows the integers, and ± comes from them.
     - **Fallback: fail the export** (Phil chose this over the recommended median fallback): exporting a run whose stored summary has no mean is an error, not a silent median.
     - (c) Headline: the main line's odds-based rules are unchanged; line 2's seat figure becomes the mean, e.g. "Labour 317 seats (238–389) · majority 44%". With two parties tied as largest, line 2 shows both parties' rounded means.
     - **Tracker line too (Phil: "Yes, in 6g"):** `data/model_support/trends.py` adds each run's national mean seats (computed from the stored national `hist`, so no model rerun for history), and the poll tracker's seat line draws the mean, with the band (5–95) unchanged. The election-day point (no simulation) stays the result (Lab 411). The builder proposes the field name, rounding and tooltip wording in the review.
     - The "needn't add up" note goes in mean views; filtered views are unchanged. "Median" wording goes from docs and comments.
   - **Analysis-loop decisions (Phil, 2026-11-07):**
     - UK vs region rounding: **"Accept it"**. Each block is rounded by largest remainder on its own, so a party can differ by one seat between the UK table and its region (SNP mean 13.51: 14 UK, 13 Scotland; Sinn Féin 7.43: 8 vs 7). Every figure stays within one seat of its mean.
     - Tracker merge: **"Always keep the latest"**. The latest run is never merged into its predecessor, so the tracker's last point always matches the headline and table. Earlier merged days (e.g. 10-25, 10-27) carry the previous day's mean.
     - Map note: **"Keep that sentence"**: "Map colours show each seat's uniform-swing winner." stays as a small note under the table in mean views.
   - Live steps (each with its own go-ahead): (1) backup; (2) single-date rerun `run_uns_model.py --as-of-date 2026-10-30 --since-date 2026-08-31 --half-life-days 15`, replacing only that run (DB and draws file); (3) the trends rebuild (the new mean line); (4) the re-export (`prediction-uncertainty.json` and `prediction-simulation.json` change); (5) the data commit. The validation report is regenerated in the code commit (`monte_carlo.py` is a hashed source; only `identity.source_sha256` and `report_hash` should move).
   - Supersedes: W-4 / 6b / 6e "median" wording becomes "mean" for the headline and table.
   - Tests: the mean in the table and headline, national and region; largest-remainder rounding sums to the seat total (including a case where plain rounding wouldn't); ± from the mean; no note; filtered views and other pages unchanged; the export carries the mean, and it sums to the seat total.
   - Acceptance: `./run_tests.sh` (then restore any test-written trends file), `npm test` with 0 jsdom skips, `npm run minify:electionmaps`, an export dry-run, the jsdom harness on both pages, and the rendered headline and table shown.
7. **Split the map page into a UNS default and an opt-in modelled-uncertainty view** (Phil, 2026-11-08, after PR #74's description was final). Model: frontier. Depends on: 6g.
   - Phil asked: "remove show uncertainty · default open to how it was before with UNS (and the text at the top) · have a tickbox button below the subheader text labelled Show with modelled uncertainty. Ticking this shows the map as it is. and the subheader text as it is · vote totals table and seats will need to account for whether that box is ticked · move Latest poll used: … to the bottom below the map in smaller non bold text, along with Nowcast: … (if appropriate to the mode)".
   - Facts: one gate already decides every uncertainty surface on the map page: `activeUncertainty(state)` (`electionmapslogic/uncertainty.js:62`) feeds the subtitle (`simulatedSubtitle`, `renderUncertaintyHeader`, `renderUncertaintyBasis` in `dom.js` `renderSubtitleText`), the table (`uncertaintyForTotals`) and the map-colour note; the seat popup checks the model run itself (`dom.js` ~1522, `seat.u`). The Show uncertainty button is `#mapsUncertaintyToggle` (`shell.html:143`, `uncertainty-view.js` `renderUncertaintyToggle`/`wireUncertaintyToggle`, state `voteTotals.showUncertainty`). Master's subtitle is the election summary line plus the latest-poll span. Every tracker run with polls carries both `s` (UNS) and `sm` (mean) (383 of 384; the election-day point has neither band nor mean), so the tracker switch needs no data change.
   - What (code only; no DB, model or export step):
     - **Remove** the Show uncertainty button and its state; the table never has a separate ranges toggle again.
     - **A checkbox labelled "Show with modelled uncertainty"** directly below the subtitle (`#mapsSubtitle`), shown only when the current view has a stored simulation (a saved Westminster run; hidden for historical elections, predict, scenarios, the US page and the poll tracker view). **Unticked by default.**
     - **Unticked (default):** the map page is master's UNS view: the subtitle is the election summary line (e.g. "… · Labour majority: …"), the Seats column is the UNS count with ± against 2024, Vote % has no ranges, seat popups show vote % and change only (no range or win %), and there is no map-colour note.
     - **Ticked:** exactly today's view: the simulation-led subtitle and its seats line (6b/6g), the table's Seats = largest-remainder mean with its 90% range and Vote % with its range (today's table with Show uncertainty on), popups with ranges and win %, and the map-colour note. Filtered views keep the UNS count, as now.
     - **Footer below the map,** smaller and not bold: "Latest poll used: …" for every view that shows it today (saved runs and the poll tracker), moved out of the subtitle in both modes; the Nowcast basis note joins it only when the box is ticked.
     - **Poll tracker:** its existing Uncertainty checkbox now also switches the seat line: off draws the UNS count `s`, on draws the simulated mean `sm` with the bands, as now. It **starts unticked**. Its note text says what the box switches.
     - The choice lives in memory only: it holds across region and filter changes, and any page load (an election change, predict or tracker link, or a reload) starts unticked. Not stored in the browser. (Refined 2026-11-08: election links are full page loads, so "across elections" was impossible without storage; Phil chose "Memory only (Recommended)".)
   - **Decisions (Phil, 2026-11-08):** ticked table "Mean seats + ranges"; unticked popups "UNS only"; tracker "Line follows its checkbox"; persistence "Session only"; tracker default "Start unticked"; footer "Poll always, nowcast if ticked".
   - Supersedes: 6d's placement of the latest poll and the nowcast note in the header; 6e's Show uncertainty toggle; the default (unticked) view no longer leads with the simulation (6b stays as the ticked view); 6g's tracker line is the mean only when the tracker's box is ticked.
   - Files: `electionmapslogic/shell.html`, `electionmaps/index.html` if the checkbox lives in the page header, `electionmapslogic/dom.js`, `uncertainty.js`, `uncertainty-view.js`, `state.js`, `features/polltracker.js`, `features/polltracker-view.js`, `features/polltracker-uncertainty.js`, `maps.css`, their tests, `electionmaps/data/map-modes.md` if it describes the toggle, and the rebuilt bundles.
   - Tests: default load shows the UNS subtitle, UNS seats and no ranges, popups without ranges, no map-colour note, and the checkbox present and unticked; ticking shows today's subtitle, mean seats with ranges, popup ranges and the note; unticking restores the UNS view; the choice survives a region change and resets on any page load; the checkbox is hidden for a historical election, predict and the US page; the latest poll renders below the map in both modes, and the nowcast note only when ticked; the tracker starts unticked on the UNS line, and ticking switches to the mean with bands; no Show uncertainty button remains.
   - Manual review (kept: Phil accepted the briefing, 2026-11-08): with the bundles rebuilt (`npm run minify:electionmaps` in the builder's worktree), reload http://127.0.0.1:8000/electionmaps/ (the planner chat's static server serves that worktree). Check the page opens unticked on the UNS view; tick and untick; change region; open a seat popup in both modes; check the footer below the map; then open the poll tracker and check its checkbox starts unticked and switches the line..
   - Acceptance: `./run_tests.sh` (then restore any test-written trends file), `npm test` with 0 jsdom skips, `npm run minify:electionmaps`, the jsdom harness on both pages (Westminster and the US page), and the rendered header, footer, table and popup shown in both modes.
8. **PR text.** `/publish poll-errors-simulation`, run from the planner chat: it drafts the final description into `pr.md` and, once Phil approves, puts it on PR #74 (opened by Phil 2026-10-13). Merging is Phil's.

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
- [x] 3 · T4 Phase 6: Style cleanup (`000fbd9f`).
- [x] 4 · Branch self-review done and chosen fixes committed (`eeee25d0`).
- [x] 5 · Master merged in; checks green (`af4405fb`).
- [x] 6a · Westminster weighting defaults: 15-day half-life, 60-day window (`a5cdf8e0`).
- [x] 6 · T4 Phase 5 and T1: Westminster backfill and export done with authorisation (`51c31966`).
- [x] 6b · Main headline line leads with the simulation (`c7a85eab`).
- [x] 6c · Self-review 2 fixes and the election-day tracker point (`ae61f984`, `b2c33428`).
- [x] 6d · Header reading note below the latest poll, smaller, with the nowcast sentence (`950b22a2`).
- [x] 6e · Vote-totals table: Seats column from the simulated median; toggle adds only ranges (`b788ae28`).
- [x] 6f · Poll tracker Chances view: no blank space on the left (`8d7c9d42`).
- [x] 6g · Seats as the simulated mean in the table and headline (`be4222f5`, `789e36ae`).
- [x] 7 · Map page split: UNS by default, "Show with modelled uncertainty" checkbox, latest poll and nowcast note below the map, tracker line follows its checkbox (`5902f98f`).
- [ ] 8 · PR text updated for piece 7 (the first final description went on PR #74 on 2026-11-08; `/publish` again after piece 7).

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
- 2026-11-07 (builder, Piece 3): style cleanup done, paused for Phil's review (uncommitted). 47 files, +2184/−860.
  - **Branch-added `.py` files (50):** `ruff format` and `ruff check --select I001,F401 --fix` applied. ruff ran with a scratch config `src = ["data", "data/tests"]` so first-party imports group correctly. Two `app` fixture imports are kept with `# noqa: F401`, and one `# isort: skip` stays where it is because the sys.path it needs is set by an earlier test import. 14 long prose-string lines are left as they are.
  - **Master-existing files:** one line changed in each of 4 (import order, plus `Pollster` removed from 2 importers).
  - **Other changes:** `monte_carlo.py` function-level imports now carry a comment (a real import cycle with `simulation_replay`). `uncertainty_explorer.py` takes `Mapping` from `collections.abc`. The two one-line console templates are reflowed.
  - **Not done, and why:** TypeError (the type checks are caught as ValueError); `style.css` tokens (the file has none, and master hard-codes the colour); `.uncertainty-col` naming (it matches its neighbours).
  - **Evidence of no behaviour change:** an AST comparison of all 45 changed `.py` files shows identical bodies, differing only in import order and six unused imports removed. Both templates are byte-identical to HEAD with whitespace removed.
  - **Checks:** mypy 246 clean; pytest 5670 (unchanged); Vitest 621 / 3 skipped (unchanged); export dry-run and console smoke OK; `git diff --check` clean.
  - **Analysis loop:** all clean except two template-indentation nits, both fixed.
- 2026-11-07: Piece 3 committed as `000fbd9f` and pushed. Next: Piece 4, `/review self poll-errors-simulation` in the planner chat.
- 2026-11-07 (builder, Piece 4 review fixes): all seven are implemented and paused for Phil's review (uncommitted). 15 files, +606/−105, plus the new `electionmapslogic/tests/election-load-view.test.js`.
  - **Fix 1:** the same boundary rule now holds in `polltracker.js` and `trends.py`. A line breaks on an uncertainty-presence change, or when a known id differs from the last *kept* known id; unknown→unknown is continuous, and null→"a" is kept without a break. A cross-check test (`test_kept_runs_carry_every_tracker_break`, 64+3 sequences) pins Python's kept output to the JS rule. On the committed trends file the tracker now draws 0 boundaries and bands on 804/804 days.
  - **Fixes 2–3:** jsdom tests drive the real `startApp()`: the popup band and its escaping, the MC headline, and stale-response guards.
  - **Fix 4:** 33 frozen-input raise tests, plus cause assertions.
  - **Fix 5:** `poll_list` catches `UncertaintyConfigError`. `poll_list.html` gets an alert.
  - **Fix 6:** a shared `outcome_tail()` in `services/uncertainty_explorer.py`.
  - **Fix 7:** the wording names `swing.pooled.sigma_seat_raw` / `sigma_lambda` (fitted 4.9029 / 0.3818 to 4 dp; 2 dp in the config).
  - **Validation report regenerated:** only `limitations[2]`, `limitations[3]`, 8 `source_sha256` entries and `report_hash` moved, and no numbers. Seven of those hashes had been stale since the ruff commit `000fbd9f`; I missed that at Piece 3. A rerun is byte-identical.
  - **Checks:** mypy 246 clean; pytest 5793; Vitest 638 / 3 skipped (with jsdom); bundle up to date (the US bundle is unchanged); export dry-run and console smoke OK; `git diff --check` clean.
  - **Re-checks:** correctness, coverage, style and common-mistakes were re-run on the fixes. Their follow-ups are fixed: the Python and JS kept-run mismatch, the wording keys, the stored-interval tail test, and two comments.
  - **Noted, not fixed (outside the chosen findings):** `poll_detail` for a Westminster poll still fails on a malformed uncertainty config. The review's premise that it catches the error was wrong.
- 2026-11-07: Review fixes committed as `eeee25d0` and pushed. Next: Piece 5, merge master in, which needs Phil's go-ahead to push.
- 2026-11-07 (builder, Piece 5 brief): Phil chose to resolve the conflicting exports (`model_output_trends.json`, `prediction-simulation.json`) with master's versions. Uncertainty displays stay hidden until Piece 6, because the stale `prediction-uncertainty.json` id no longer matches. He also chose to verify the auto-merged `map-modes.json` against a scratch `export_elections.py --output-base` run (live DB read-only, nothing written in the repo). Merging `origin/master` at `dc2fda3d`, since local `master` is behind it.
- 2026-11-07: Round-2 review fixes (R2-1 jsdom devDependency, R2-2 `poll_list` header guard, R2-4 `poll_detail` config error) were received mid-merge. Phil chose to finish the Piece 5 merge first and build R2 on top of it.
- 2026-11-07 (builder, Piece 5): the merge of `origin/master` (`dc2fda3d`, 36 commits) is staged with `--no-commit` and paused for Phil's review.
  - **Resolution:** the conflicting bundles were regenerated with `minify:electionmaps`. `model_output_trends.json` and `prediction-simulation.json` were taken from master.
  - **Scratch export check:** `export_elections.py --output-base <scratch>` read the live DB read-only. All 63 files it writes, `map-modes.json` included, are byte-identical to the merged tree. It would also delete the stale branch-only `prediction-uncertainty.json`, which stays in the merge. It's inert: master's `prediction-simulation.json` has no `uncertaintyId`, so it's never fetched. Piece 6 handles it.
  - **Merge review:** correctness check clean (config/env, conftest, dom/state, CSS, README, home page).
  - **Checks:** mypy 246 clean; pytest 5781 (master drops some Drive-backup tests); Vitest 1449 / 3 skipped. A plain `npm test` with no `PF_TEST_JSDOM` now runs every jsdom suite, because master adds `jsdom@^24.1.3` as a devDependency (installed with `npm ci` in the worktree's own `node_modules`). The 3 skipped are referdle local-baseline benchmarks. Export dry-run and console smoke OK; jsdom harness shows 0 errors on both pages. The merged Westminster page shows no uncertainty, as expected.
  - **Diff check:** `git diff --cached --check origin/master` flags only the branch's existing YouGov fixture whitespace.
- 2026-11-07: Piece 5 merge committed as `af4405fb` (parents `eeee25d0` and `dc2fda3d`) and pushed with Phil's go-ahead. Next: round-2 review fixes, then Piece 6.
- 2026-11-07 (builder, round-2 review fixes): built and paused for Phil's review (uncommitted). 7 files, +45/−16.
  - **R2-1:** already met by the master merge, which adds `jsdom ^24.1.3` (it can't resolve to 25+). A plain `npm test` runs every jsdom suite; only 3 referdle benchmarks are skipped. Only the three stale "optional harness" comments are updated.
  - **R2-2:** the `poll_list.html` header reads "MoE" when the settings are invalid, and the malformed-config test asserts it.
  - **R2-4:** `poll_detail` catches `UncertaintyConfigError` for Westminster polls and shows "-" for n_eff and Adjustment, no margins, and an alert. The explanation paragraph is guarded. The old raise-expecting test now expects 200 and the alert. An unused import was removed.
  - **Checks:** mypy 246 clean; pytest 5781; Vitest 1449 / 3 skipped; console smoke OK; `git diff --check` clean.
  - **Re-check:** correctness and coverage clean after two comment-accuracy fixes.
- 2026-11-07: Round-2 review fixes committed as `a832137d` and pushed. Next: Piece 6, the Westminster backfill and export (each live step needs Phil's go-ahead).
- 2026-11-07 (builder, Piece 6 brief, read-only):
  - **Live DB:** 848 `model_uns` runs, 2024-07-05 → 2026-10-30. 840 have uncertainty (all config `730a3349d3836bc6`, 10,000 sims, through 2026-10-22). The 8 runs 2026-10-23 → 10-30 have none, because master's console made them. The latest Westminster fieldwork end is 2026-10-30.
  - **Backups:** the DB is unchanged since its newest backup (`elections-2026-11-03-090450.db.gz`).
  - **Dry run 2026-10-22 under HEAD** (no writes; DB and sims-file mtimes checked):
    - It takes about 6 s per date with 10k sims.
    - It gives Labour 322 / 220–298–369, P(maj) 27.5% and hung 72.5%, against the stored run's 300 / 199–282–359, 18.9% and 81.0%.
    - Cause: More in Common 10-16→19 and Find Out Now 10-21→22 (ids 2025/2026) were imported after that run, so the stored late-October history is out of date. A rerun moves it.
  - **Estimate:** a full rerun is about 85–100 min.
  - **Running processes:** `server.sh` and `http.server` are running in this builder worktree (since about 13:20).
- 2026-11-07 (builder, Piece 6): Phil gave the go-ahead for the backup and the full rerun. Step 1: `backup_db.py` copied the DB and found it identical to `elections-2026-11-03-090450.db.gz`, so it made no new archive; that one is the backup. Step 2 started 15:20Z: `run_uns_model.py --start-date 2024-07-05 --end-date 2026-10-30 --continue-on-error` from the builder worktree (log in the builder scratchpad).
- 2026-11-07 (planner): the first Piece 6 rerun used range mode's `--lookback-days 365`, whereas the history and the console use 30. Read-only dry runs for 2026-10-30 reproduced both exactly (30 days: Lab 358; 365 days: Lab 317), so the window was the whole difference. Phil then chose a **15-day half-life and a 60-day cutoff**, with the console and CLI defaults changed to match (Piece 6a), then a full rerun. Calibration isn't affected: the national error is fitted on final-poll errors (3-day window, `build_final_poll_errors.py:73`), and sampling error follows the actual weights (`monte_carlo.py:679`).
- 2026-11-07 (builder, Piece 6a): defaults changed in `data/console/blueprints/westminster.py` (form: 60 / 15.0, 15.0 added to the menu) and `data/models/westminster/run_uns_model.py` (CLI: half-life 15.0, since 60, lookback 60, docstring), plus README and the pinned tests, and a new GET test that the form preselects 60 and 15.0. The console's daily run after a poll import (`console/blueprints/poll_import.py:101`) calls `run_uns_model.py` with no flags, so it picks up the new defaults too. The validation report is unaffected: none of its hashed sources changed, and a scratch regeneration was byte-identical. `monte_carlo.py:318` still says "30-day averages since 2024"; that describes a past measurement, and editing it would change a hashed source, so it's left alone. Checks: mypy clean, pytest 5782 passed, Vitest 1449 passed, console smoke and `git diff --check` clean, changed-lines coverage 100% (3 lines). All five analysis checks were clean on round 1, so no verifier ran. The 365-day trends file was restored to HEAD (a copy is in builder scratch).
- 2026-11-07: Piece 6a committed as `a5cdf8e0` and pushed with Phil's approval ("Approve, commit and push"). Next: Piece 6 step 2, the 15/60 full rerun, which needs its own go-ahead.
- 2026-11-07 (planner): the step-5 trial export showed the main subtitle line (uniform swing, "Labour majority: 58") contradicting the MC line (majority 44%, hung 56%). Phil chose to change it in a new piece, **6b**, and picked "Lead with the simulation" over "Label it uniform swing" and "Hide the main line". Order: the step-7 data commit first, then 6b, then the PR text.
- 2026-11-07: Piece 6 finished. The 15/60 rerun (848/848), backup `elections-2026-11-07-183903.db.gz`, trends rebuild (383 points), staged and real export, and the data commit `51c31966` were each pushed or run with Phil's go-ahead. Next: 6b (main headline line), once its go-ahead comes.
- 2026-11-07 (builder, Piece 6b): `uncertainty.js` gains `probabilityPercent` (the one rounding helper; `formatProbability`, the likely/most-likely cut-off and both tie tests use it), `headlineOutlook` (shared by both lines), `uncertaintyOutcomeLine` and `simulatedSubtitle`. `uncertaintyHeadline` now follows the largest-party favourite and adds "· hung N%" when the main line doesn't name hung. `dom.js` picks the main line, and `state.js` gets a doc note only. Ties are listed by party name; outcome ties put hung first. A US-style config test stands in for booting the real US page in Vitest; the jsdom harness covers the real US page. The `election-load-view` afterEach now stops the d3 zoom left over from a seat click. That fixes an "SVGElement is not defined" unhandled error that already happened on HEAD. Analysis loop: round 1 had 5 verified findings, all fixed (tie order, US test, afterEach order, 2 docstrings), and 2 dropped. Round 2 was clean on all five checks. No changed-lines coverage report: the Vitest coverage plugin isn't installed in this worktree.
- 2026-11-07: Piece 6b committed as `c7a85eab` and pushed with Phil's approval ("Approve, commit and push"). The builder's work is done; Piece 7 is `/publish poll-errors-simulation` in the planner chat.
- 2026-11-07 (planner): self-review 2 on `a832137d..c7a85eab`: correctness and security clean, coverage 38/38, three low doc items verified. The planner noted that master's tracker starts on 2024-07-05 with the election-result point, which the branch drops as unpolled. Phil chose all three fixes and to keep the election-day point: new Piece **6c**.
- 2026-11-07 (builder, Piece 6c code): review fixes 1–3 are in (`dom.js` comments, README lookback wording). In `trends.py`, a `seen_polled` flag means unpolled runs before the first polled run are published central-only (no summary or segment), so equal ones collapse to the first. Unpolled runs after it are dropped. Runs with no stored block, or a non-Westminster budget, count as polled. The bundles are unchanged, because the minifier drops comments. A read-only rebuild to scratch gives 384 points from 2024-07-05 (Lab 411, 33.7%, central-only), and the tracker breaks once, at 2024-08-07. Analysis loop: round 1 had 2 verified findings (a 90-column assert; two missing tests: different-central leading runs and an all-unpolled history), both fixed. Round 2 was clean on all five checks. Changed-lines coverage on `trends.py` is 100% (7/7).
- 2026-11-07: Piece 6c code committed as `ae61f984` and pushed with Phil's approval ("Approve, commit and push"). Next: the trends rebuild live step, then the data commit.
- 2026-11-07: Piece 6c data committed as `b2c33428` and pushed with Phil's approval ("Approve, commit and push"). All builder pieces are done; Piece 7 is `/publish` in the planner chat.
- 2026-11-07 (planner): Phil's manual check of the header led to new Piece **6d**: the basis note moves below "Latest poll used", smaller and not bold, with the exported nowcast sentence no longer stripped; line 2 drops "(if an election were held today)". Phil chose "Nowcast, today" (not "tomorrow", which would need a re-export) and "Smaller, not bold".
- 2026-11-07 (planner): Phil asked for the vote-totals table rework (Piece **6e**) and to queue it until 6d is done. Its open points (a)–(c) go to Phil at the 6e briefing.
- 2026-11-07 (builder, Piece 6d): `renderUncertaintyHeader` now draws only the odds line, and a new `renderUncertaintyBasis` draws the note in full (no stripping), with the same id. `dom.js` orders the lines main, odds, latest poll, note. The "(if an election were held today)" suffix is gone from line 2. `.maps-subtitle-basis` is 0.85rem, weight 400, colour #475569: an existing grey, because `--muted` is the subtitle's own colour. `aria-describedby` is unchanged. Checks: Vitest 1467 passed with 0 jsdom skips; JS and CSS bundles up to date; the jsdom harness on both pages had 0 errors. Analysis loop: all five checks clean on round 1, so no verifier ran.
- 2026-11-07 (planner): Phil reported blank space on the left of the poll tracker's Chances views (Majority and Largest party). Queued as Piece **6f**, after 6e.
- 2026-11-07: Piece 6d committed as `950b22a2` and pushed with Phil's approval ("Approve, commit and push"). Next: 6e (vote-totals table), once the planner relays its go-ahead.
- 2026-11-07 (builder, Piece 6e): `renderVoteTotals` shows Seats as the median ("319 (238–389)" with the toggle on), with ± and sort from the median. Vote % gains "(lo–hi%)". The separate range columns, the † marker and the hide-columns CSS are removed. A new `#mapsSeatMedianNote` gives the visible seat total (650, or the region's own) and hides when the card collapses. A party with votes but no stored median shows "—". Filtered views show the uniform-swing number under "Seats", with no note. Predict, scenario, historical and US tables are unchanged. Analysis loop: round 1 had 1 verified finding (note not hidden on collapse), fixed with a test, and 3 dropped. Round 2 had 1 verified nit (JSDoc `@param`), fixed. Round 3 re-ran only the style check, because that fix was comments only; it was clean.
- 2026-11-07: Piece 6e committed as `b788ae28` and pushed with Phil's approval ("Approve, commit and push"); the orphaned JSDoc at `dom.js:587-591` was left alone (Phil). Next: 6f look-ahead, then 6f on its go-ahead.
- 2026-11-07 (planner): 6f choices made and 6f started ("Start 6f"). Phil then chose "Mean everywhere" for the seats figure, which becomes new Piece **6g**, queued after 6f.
- 2026-11-07 (planner): Phil wants the table to add up to 650. Asked which seats figure, he chose the mean with largest-remainder rounding (not one decimal, not seats-by-favourite, which is about the uniform-swing count). He had no preference on a map-colours label, so none is added. The uniform-swing number stays unnamed: it's the map colours, the tracker seat line, and filtered-view Seats.
- 2026-11-07 (builder, Piece 6f): `polltracker-view.js`: the grid is drawn from the left on the active y scale, so lines run 0 → innerWidth in every mode. `margin.left` is 70 with Seats ticked and 16 otherwise. In Chances, the window start is max(range start, `firstChanceIndex`), with every series sliced from it. With the 16px gutter, the x-axis drops ticks whose rotated label would run off the left edge (kept when margin.left + x ≥ 56). The 70px path keeps its original tick call. `firstChanceIndex` is exported from `polltracker-uncertainty.js` and also drives capabilities' `chances`. Real data, Chances: 815 points from 2024-08-07, translate(16,14), gridline 0→660. An end-date sweep over 2026 shows 0 clipped labels after the fix, against up to 365/365 before. Analysis loop: round 1 had 3 verified findings (clipped labels, a duplicated presence check, a missing pLargest-only test), all fixed, and 2 dropped. Round 2 was clean on all five checks.
- 2026-11-07: Piece 6f committed as `8d7c9d42` and pushed with Phil's approval ("Approve, commit and push"). Next: 6g look-ahead, then 6g on its go-ahead.
- 2026-11-07 (builder, Piece 6g code):
  - `monte_carlo.py` stores `mean` (2 dp) per party, national and regional.
  - `public_uncertainty.py` exports `mean` and `m`: whole seats by largest remainder, computed in exact hundredths. Ties go to the larger mean, then the DB `Party.name`. The national total comes from the histograms; a region's total is its rounded sum of means, and the regions must add up to the national total. A run with no stored mean makes the export raise.
  - `trends.py` adds `sm` (1 dp, from the stored hist) and always keeps the latest keepable run (the last polled run once any is polled).
  - Front end: the table, headline and tracker use `m`/`sm`. The tracker tooltip reads "Seats (simulated mean): N". The "needn't add up" note is replaced by `#mapsMapColourNote`.
  - The validation report moved only `source_sha256["model_support/monte_carlo.py"]` and `report_hash`.
  - Analysis loop:
    - Round 1: 5 verified findings. Two were decided by Phil (per-block rounding accepted; always keep the latest run) and three fixed, and he chose to keep the map-colour sentence.
    - Round 2: 2 verified findings, fixed (latest = last keepable run; bool mean rejected).
    - Round 3: 2 verified findings, fixed (a rigged bool test; comments).
    - Round 4 re-checked only those test and comment edits, with coverage, style and common-mistakes: clean.
  - Changed-lines coverage 99%: one line, an accepted type guard.
- 2026-11-07: Piece 6g code committed as `be4222f5` and pushed with Phil's approval ("Approve, commit and push"). Next: live steps backup → 10-30 rerun → trends rebuild → re-export → data commit, each with its own go-ahead.
- 2026-11-07 22:51Z, 6g step 1 (Phil: "Go ahead with backup"): `backup_db.py` printed "Unchanged since the last archive", so `elections-2026-11-07-210040.db.gz` is the backup. Between 18:39 and 21:00 the shared DB gained 9 US elections (ids 106320–106328, issue 0002's imports). Westminster polls and UNS runs are unchanged, so restore from the 21:00 archive, not 18:39.
- 2026-11-07 22:56Z, 6g step 2 (Phil: "Go ahead with rerun"): the 10-30 single-date rerun (exit 0) replaced election 106319 with 106329.
  - Against the 21:00 archive, the stored national, regional and seats blocks match field for field apart from `mean`, with the same seed (739919) and config. The vote rows are byte-identical (sha256 match).
  - Means: national 650.02 (2 dp); the region sums round to their seat counts.
  - The draws file now names 106329. There are still 848 UNS runs, and no other elections changed. No auto-archive was made.
  - `export_elections.py --dry-run` now passes. The worktree trends file (384 points, Lab `sm` 317.0) is byte-identical to a scratch rebuild.
  - It also rewrote the tracked `model_output_trends_meta.json`, whose `since_date` went from 2026-09-30 to 2026-08-31. That fixes metadata left over from the 30-day window, and it becomes a 4th file for the data commit.
- 2026-11-07, 6g step 3 (Phil: "Go ahead with rebuild"): the trends rebuild published 384 points, byte-identical to the rerun's file. The meta file goes in the data commit (Phil: "Include it").
- Step-4 scoping evidence (scratch only):
  - A full `--output-base` export now fails with `Pre-built map not found … uselectionmaps/data/maps/map-24.topo.json`, because it tries to export 0002's new US elections. So a full export must not be used on this branch.
  - `--current-simulation --output-root <copy of electionmaps/data>` writes only `prediction-simulation.json` and `prediction-uncertainty.json`. These are byte-identical to the full export's output, and nothing else differs.
  - The staged page renders "Labour 317 seats (238–389) · majority 44%" with the map-colour note, 0 errors.
- 2026-11-07, 6g step 4 (Phil: "Go ahead with export"): `export_elections.py --current-simulation` (exit 0) wrote only `prediction-simulation.json` and `prediction-uncertainty.json`, byte-identical to the scratch output. The worktree has exactly the 4 data files modified. The jsdom harness on the real repo data: electionmaps "Labour 317 seats (238–389) · majority 44%", uselectionmaps unchanged, 0 errors. Next: data-commit review.
- 2026-11-07: Piece 6g data committed as `789e36ae` and pushed with Phil's approval ("Approve, commit and push"). Every builder piece is done; Piece 7 is `/publish` in the planner chat.
- 2026-11-08: Piece 7 committed as `5902f98f` and pushed with Phil's approval ("Tried it, approve"). Persistence settled as "Memory only" (Phil, 2026-11-08): the choice holds across region and filter changes, and any page load resets it, election changes included, because the nav links are full page loads. Analysis loop: one correctness finding verified and fixed (right panel height re-synced on toggle), its test tightened to catch ordering; a claimed late-load variant was dropped as unreachable. After those small fixes only correctness was re-run. Piece 8 (`/publish`) is the planner chat's.

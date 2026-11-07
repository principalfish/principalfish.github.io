# Self-review: poll-errors-simulation

2026-11-07, planner chat. Diff `master...poll-errors-simulation` at `000fbd9f` (169 files). Changed-lines coverage 97% (171 of 6,084 uncovered; builder's diff-cover run against `origin/master`).

Checks and models: correctness (claude-opus-5-5), security (claude-opus-5-5), coverage (claude-sonnet-5-5), style incl. documentation (claude-sonnet-5-5), common-mistakes (claude-sonnet-5-5). Every finding below was verified against the branch with `git show poll-errors-simulation:<file>`.

## Findings (all chosen by Phil for fixing)

1. **medium · correctness** · `electionmapslogic/features/polltracker.js:144-146` (also `:180`, `:208`), `data/model_support/trends.py:~295`. A run with no `segment_id` counts as a segment boundary. All 440 rows in the committed `electionmaps/data/results/model_output_trends.json` have `hung` but no `segment_id`, because they were written at `40d3933f`, before `f417a14e`. So the tracker breaks its lines at every run, and its bands only show on run days. Fix: break only when a known segment changes, or when uncertainty appears or disappears; treat unknown→unknown as continuous, in both the JS and `trends.py`. Add tests with legacy (no-segment) rows.
2. **medium · coverage** · `electionmapslogic/dom.js:1465-1468`: no test covers the seat popup's uncertainty band. Add jsdom tests: the band text shows on a saved model with `seat.u`; it's absent in predict view or without a model; `<`/`&` are escaped.
3. **medium · coverage** · `electionmapslogic/app.js:153-155, 162-163, 188, 191-192` (0%): add a jsdom test that the MC headline renders after an election load, and that switching election mid-fetch doesn't render the old uncertainty.
4. **low-medium · coverage** · `data/model_support/westminster_validation_inputs.py:88-200`: add tests that mismatched or malformed frozen inputs, and a tampered hash, each raise rather than replay.
5. **low · correctness** · `data/console/blueprints/polls.py:83`: `poll_list` loads the uncertainty config on every request, so a malformed config breaks `/polls` for every parliament. Fix: load it only when needed for Westminster, or catch `UncertaintyConfigError` and show no MoE, matching `poll_detail` at `:188-190`.
6. **low · common-mistakes** · `data/console/blueprints/uncertainty_explorer.py:161-162, 180`: the quantile tail is computed by float subtraction without rounding, and `interval` defaults silently to 0.9 with no range check. Fix: match `data/console/services/uncertainty_explorer.py:92-95` (validate 0 < interval < 1; `round(..., 12)`).
7. **low · style/correctness** · `README.md:81-86`, `data/model_support/westminster_validation.py:3-5`, `:460-463`: "Gaussian conversion experiment" reads like the removed offline experiment, and "the original audit used 4.9029 and 0.3818" points at something not in the repo. Fix: say "calibration experiment", and state what the original values were (the unrounded fitted scales) inline.

## Dropped after verification
- `data/tests/test_opinium_import.py:54` "piece 16" comment: already on master, so not this branch's.
- `monte_carlo.py:1265` "probabilities to 4": correct, since storage is 4 dp and only publication is 3 dp.
- `data/config.py` default `SIMULATIONS_PATH` under `/home/philiph/`: same pattern as the existing `DATABASE_PATH` default.
- Common-mistakes' note that the headline odds may not sum to 100%: they're overlapping, independent probabilities.

## Clean
- Security: clean (escaping, SQL binding, route params, `allow_pickle=False`, autoescape, atomic writes, no secrets).
- No US or experiment residue. `uselectionmaps/` sources match master. Bundles and `maps.min.css` rebuild byte-identically. The shared engine is inert on the US page.
- Tracked exports look like real exports, not test output.

## Fixes
Sent to the builder 2026-11-07. Commit: `eeee25d0` (all seven, pushed; Phil approved in the builder chat). 16 files, +859/−105. Checks: mypy 246 clean, pytest 5793, Vitest 638 / 3 skipped (jsdom), `electionmaps.min.js` rebuilt, US bundle unchanged. `uncertainty_validation_report.json` was regenerated in the same commit: no numbers changed, only `limitations[2]`/`[3]`, 8 `source_sha256` entries (7 of them stale since `000fbd9f`) and `report_hash`.
- Builder's analysis loop on the fixes: correctness found that `trends.py` decided boundaries using runs it then dropped (fixed before commit), a non-existent `sigma_seat` key in the new wording (fixed), and a weak interval test (strengthened).
- Outside the brief, kept: `poll_list.html` (the error alert) and `services/uncertainty_explorer.py` (the shared `outcome_tail()` now lives there).
- Correction to finding 5: `poll_detail` (`polls.py:188-190`) doesn't catch the config error; it only skips loading the config for non-Westminster polls. A Westminster poll's detail page still errors on a malformed uncertainty config. Possible follow-up; not fixed here.
- Planner re-check of `000fbd9f..eeee25d0` (correctness claude-opus-5-5, coverage claude-sonnet-5-5): **all seven resolved.** The branch's `parsePollTrackerData` on the committed trends file gives 0 boundaries (was 439). The load-view tests ran under jsdom 24 and fail if the `app.js` guards are removed.

## Round 2 (from the re-check; verified)
- R2-1 · medium · coverage · `package.json`: `jsdom` isn't a devDependency, so `npm test` silently skips every `describe.skipIf(!JSDOM)` suite (`election-load-view`, `vote-totals-view`, `polltracker-view`, the "3 skipped"). They only ran with `PF_TEST_JSDOM` pointing at a scratch jsdom 24. Fix: add `jsdom@24` (pinned; later majors fail on Node 21 per `AGENTS.md`) as a devDependency.
- R2-2 · low · correctness · `data/console/templates/poll_list.html:46`: `ref_pct` is only set inside `{% if config is not none %}` (`:12-13`), so with a malformed config the header reads "MoE at %". Fix: guard the header text, and assert on it in the malformed-config test.
- R2-3 · low · coverage · `data/model_support/trends.py:~292`: no test of an unpolled (dropped) run between runs with different identities. Fix: identities a, (unpolled, b), a; assert the middle run is excluded and doesn't change the surrounding decisions.
- R2-4 · low · correctness · `data/console/blueprints/polls.py:188-190`: `poll_detail` still errors for a Westminster poll on a malformed config. Fix: catch `UncertaintyConfigError` and show no MoE with an alert, as `poll_list` now does.
- R2-5 · nit · `test_tampered_inputs_raise_before_any_replay` claims more than it checks. Fix: rename it, or assert no replay ran.

Round 2 choice (Phil, 2026-11-07): fix **R2-1** (jsdom@24 devDependency), **R2-2** and **R2-4** (console config guards). Skipped by Phil: R2-3 (unpolled-run test) and R2-5 (test-name nit). Commit: (pending).

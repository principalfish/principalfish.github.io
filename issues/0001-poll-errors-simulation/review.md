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
Sent to the builder 2026-11-07. Commits: (pending).

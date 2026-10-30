# Assessment: Westminster conversion uncertainty and the Piece 2a design

## Summary

W-1 remains present on `poll-errors-simulation`, HEAD `282dd916`, with the reviewed Phase 0/1 and frozen-input prerequisite changes uncommitted. The runtime still draws independent seat errors conditional on the existing national, regional and lambda terms. The proposed party × nation × baseline-share-band Gaussian component has not been added to the engine.

The initial Piece 2a calibration does not meet its own acceptance criterion. Changing the estimator to full-loading residual covariance, or approximately removing regional aggregate drift, did not rescue it. The user approved revising Piece 2a to test the already-approved heavy seat tails and lambda-scale uncertainty jointly with the cluster model, entirely offline, before adding engine infrastructure. This is an experiment with an explicit failure gate, not an established statistical fix.

## Verdict and evidence

**Present.** The historical audit benchmark covers 12/20 party/election seat totals with its existing conditional Gaussian model. All three exploratory cluster approaches cover 15/20; every 2024 cell still misses. The required threshold remains at least 17/20, alongside the standardized-error and parity requirements.

| Approach | Covered / 20 | 2024 Labour 90% interval | 2024 Conservative 90% interval |
|-|-|-|-|
| Direct nonlinear seat-total moments | 15 | 321–361 | 189–240 |
| Full-loading residual covariance | 15 | 320–363 | 187–242 |
| Residual covariance with regional balancing diagnostic | 15 | 323–360 | 191–238 |

Actual 2024 totals are Labour 411 and Conservatives 121. Each held-out experiment used 6,000 evaluation draws. These are exploratory scratch implementations, not results from a new production kernel.

Direct moment calibration fits a cluster scale near 7.3 percentage points when 2024 is in training, but 1.295 when 2024 is held out. Its other 2024 intervals are Liberal Democrats 42–63 (actual 72), SNP 11–21 (9), and Others 1–3 (7). Overall leader/runner-up standardized RMS errors are approximately 2.95/3.00 rather than near one.

Residual-covariance fits are steadier but still inadequate: held-out cluster scales are 2.264, 2.341, 2.486 and 1.468 points; leader/runner-up RMS errors are 2.78/2.85. The regional-balancing diagnostic gives scales 3.375, 3.495, 3.738 and 2.130, with RMS errors 3.18/3.38. Those evaluations retained the existing training-pair MAD seat scale; they do not establish a jointly fitted heavy-tail model. No all-zero fallback was encountered in these diagnostic evaluations.

### Reproduction contract

- Use the four same-boundary transitions in `data/scripts/calibrate_uncertainty.py:96`, their existing party alignment and Speaker exclusions, and read-only historical inputs.
- Derive each party's three baseline-share quantile bands within nation from the earlier election; keep tied baseline shares together. Use the existing share-scale function and party centring. Give transitions equal weight.
- In each fold, fit on the other three transitions only. Retain zero-centred shared lambda and the existing independent-seat training calibration for the original diagnostic comparison. Score the five largest actual party totals in the held-out transition with 5/50/95 quantiles.
- Direct nonlinear fitting used 3,000 common-random-number training draws per pair, a 0.02-point root tolerance, and 6,000 separate evaluation draws. Its moment equates the equal-transition average squared actual-minus-simulated-mean error to simulated variance. Scratch sources/results remain at `/tmp/wm_cluster_direct.py`, `/tmp/wm-cluster-direct-results.json` and `/tmp/wm_cluster_pilot.py`; these paths are session evidence, not durable repository dependencies.
- The residual experiment uses the full centred cluster loading matrix, projects out fitted lambda, and accounts for independent-seat covariance in two variance-component moment equations. It does not infer shared covariance by averaging marginally corrected residual products.
- The balancing diagnostic subtracts later-turnout-weighted regional means during residual fitting, and earlier-turnout-weighted regional means in simulated shocks, before clamping/normalization. This is an approximation with different weights, not exact conditioning of simulated regional totals and not an approved production transformation.
- Preserve the audit baseline as a separate experiment. The revised plan requires a repository implementation and report for future reproducibility; scratch numbers alone cannot establish completion.

## Root cause and limits of the original plan

Independent seat errors cannot represent coherent conversion misses across many similar seats. However, a single pooled Gaussian cluster scale is not demonstrated to predict an unusually large transition when trained on the other three. The four transitions show strong variation, and the observed failures are too large to attribute to float precision or fallback handling.

The original plan treated the cluster-scale estimator as sufficient evidence for the proposed structure. The failed experiments falsify that claim for the tested fits. They do not prove that every correlated conversion model must fail, or that the remaining approved terms will succeed.

Party centring couples parties with different cluster memberships. The scalar marginal correction in `seat_residuals` is adequate for its existing marginal-scale purpose; it is not an estimator of the complete shared covariance. Any replacement fit must use the full loading transformation and account for lambda fitting and independent noise.

The benchmark fixes actual regional swing as the **central input**. It does not condition every simulated draw on exact observed regional aggregates. Cluster shocks can change those aggregates; at large scales this may overlap polling uncertainty. Preserve the benchmark definition for comparison, report the additional regional drift, and do not relabel it as exact conditional coverage. Exact aggregate preservation would be a separate design decision, complicated by clamping/normalization and deterministic parity.

These four transitions have already informed model choice. Training-only fold fitting is still required, but subsequent leave-one-pair-out results are retrospective diagnostics, not fresh external validation. Neither coverage alone nor extra Monte Carlo draws resolves the small historical sample.

## Minimal next change and decision gates

Replace the direct-to-engine Piece 2a with a bounded offline joint experiment. Preserve nation × three baseline-share bands, equal transition weights, zero-centred lambda, the existing benchmark and the 17/20 threshold. Test fixed ablations for clustered Gaussian errors, added heavy seat tails, and added lambda predictive-scale uncertainty, with training-only fitting and explicit uncertainty/nonidentifiability diagnostics. Do not introduce a cluster-scale prior, regime covariate, full cluster covariance, regional balancing or new historical data silently.

The fit must distinguish cluster, seat and lambda contributions; plugging pooled marginal tail estimates into a correlated model without accounting for that mixture is insufficient. A staged approximation may be reported as exploratory, but cannot establish that the model is ready for the engine. If the candidate cannot be fitted coherently or fails the existing gates, report failure and seek a concrete new design/data decision before engine/archive work. Leave W-1 unresolved.

## Relevant code

- `data/model_support/monte_carlo.py:499` — scale structures; `:723` scale construction; `:771` child streams; `:920` independent seat draws and party centring; `:1022` projection, clamping, normalization and fallback.
- `data/model_support/calibration.py:641` — historical swing cells; `:875` marginal residuals; `:1060` pair fitting; `:1147` pooling.
- `data/model_support/westminster_validation.py:156` — existing audit-compatible historical experiment; `:297` its reported methodology.
- `data/model_support/westminster_validation_inputs.py` and `data/model_support/westminster_validation_replay.py` — frozen current-input provenance and runtime replay, distinct from the historical experiment.
- `data/scripts/calibrate_uncertainty.py:96` — four historical transitions.

## Regression and verification strategy

Use synthetic known-component recovery, zero-component boundaries, band ties/empty bands, explicit nation mapping, full-loading covariance checks, and strict holdout-exclusion tests. Compare the offline transformations against the current projection's centring, clamping, fallback and normalization. Preserve audit-baseline reproduction, zero-error deterministic parity, existing stream output, seed/chunk invariance, exact zero new terms at zero scale, and the 0.15-point vote-mean tolerance. Report fold-specific intervals, standardized errors, favourite reliability, drift and fit diagnostics rather than only a pass count.

For implementation, run `cd data && ./run_tests.sh` (strict mypy followed by pytest), the offline validation report, console validation smoke checks and `git diff --check`. A future frontend change additionally requires `npm test`, `npm run minify:electionmaps` and the documented two-page jsdom checks. Assessment/planning alone requires no new source tests or live-data operations.

## Joint approved-components experiment completed — 2026-10-29

The fixed eight-way offline experiment has completed. All four models without cluster errors cover 13/20 retrospective held-out seat totals; all four with cluster errors cover 15/20. No variant meets the required 17/20. The intended full candidate uses nation × baseline-share clusters, a per-seat × party Student scale mixture, and training-fold predictive lambda uncertainty. All four transitions retain equal training weight; each held-out election is excluded from its fit. These elections already informed model design, so this is retrospective diagnostic evidence, not untouched validation.

For held-out 2024, the full candidate gives Labour 317–361 seats (actual 411), Conservatives 181–242 (121), Liberal Democrats 41–64 (72), SNP 11–21 (9), and party 13 6–8 (7). Four of five cells miss. Across folds, leader/runner standardized RMS errors are 2.7201/2.4726 versus predictive 5–95% references 0.4053–1.5694/0.3989–1.5579. Mean-vote drift exceeds the declared 0.15-point guard in all four folds (maxima 0.1879, 0.2428, 0.2302, 0.1853). The held-out-2024 tail fit reaches nu=2.25, the lower profile boundary, and its shared scale fails the 128/256 fitting-precision stability guard. Another fold has materially different near-optimal tail-profile predictions. These are additional failed guards, not excuses to relax coverage.

Software and recovery checks pass: strict mypy 304 files; 6254 tests; synthetic recovery and kernel parity; saved report content/source/config hashes; actual console HTTP 200. The authoritative report is `data/models/westminster/uncertainty_conversion_prototype_report.json`, hash 3ead7c5851e96e1fd143a26c9370de1fca963b31925c55648a3a4dd889201428. It records every comparison and profile. Its decision is `stop_before_engine`; no production integration has been made.

The evidence does not support implementing this candidate. A user decision is pending between investigating additional comparable historical evidence first (recommended) and replanning the conversion model on the existing four transitions with explicitly new assumptions. A new prior, regime model, exact-regional-total projection, or weaker acceptance criterion is not implicitly authorized.

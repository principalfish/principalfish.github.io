# Local checkpoint commits

Branch: `poll-errors-simulation`; range `282dd916..ab3fc62e`.

Committed at the user’s explicit request. Final working tree is clean. Piece 2a remains scientifically unresolved; separate self-review is pending. No remote publication or live database operations.

## c067f150 — feat: add reproducible Westminster uncertainty validation

Add historical vote-to-seat checks, saved-run replay diagnostics and a console view with input/configuration provenance. Preserve the failing historical coverage as a baseline for subsequent model work.

Files:

- `README.md`
- `data/console/blueprints/uncertainty.py`
- `data/console/templates/uncertainty_hub.html`
- `data/scripts/calibrate_uncertainty.py`
- `data/console/templates/uncertainty_validation.html`
- `data/model_support/westminster_validation.py`
- `data/model_support/westminster_validation_replay.py`
- `data/models/westminster/uncertainty_validation_report.json`
- `data/scripts/validate_westminster_uncertainty.py`
- `data/tests/test_westminster_validation.py`

## f1e7d0cb — fix: harden uncertainty simulation and Senate replay

Consolidate Senate anchor provenance and replay/explorer helpers, pin legacy comparison inputs and cover the robustness fixes with regression tests. Preserve existing model outputs and release gates.

Files:

- `data/console/blueprints/us.py`
- `data/console/services/us_uncertainty_explorer.py`
- `data/console/templates/us_uncertainty_comparison.html`
- `data/model_support/monte_carlo.py`
- `data/model_support/us_calibration_comparison.py`
- `data/model_support/us_replay.py`
- `data/model_support/us_run.py`
- `data/model_support/us_senate_anchors.py`
- `data/model_support/us_simulation.py`
- `data/models/us/_common.py`
- `data/models/us/calibration/senate-comparison-report-house-anchor-v2.json`
- `data/models/us/calibration/senate-comparison-report-house-anchor-v2.md`
- `data/tests/test_us_calibration_comparison.py`
- `data/tests/test_us_house_public.py`
- `data/tests/test_us_robustness.py`
- `data/tests/test_us_senate_anchor_comparison.py`

## 73fadb68 — test: cover uncertainty exports and simulation safeguards

Extend Python and JavaScript regression coverage for uncertainty persistence, exports, replay guards and public display behaviour.

Files:

- `data/tests/test_console_uncertainty_runs.py`
- `data/tests/test_senate_cycles.py`
- `data/tests/test_us_calibration.py`
- `data/tests/test_us_calibration_comparison.py`
- `data/tests/test_us_house_persistence.py`
- `data/tests/test_us_house_public.py`
- `data/tests/test_us_review_export.py`
- `data/tests/test_us_review_guards.py`
- `data/tests/test_us_uncertainty_explorer.py`
- `data/tests/test_westminster_uncertainty_integration.py`
- `electionmapslogic/tests/seat-popup-uncertainty.test.js`
- `electionmapslogic/tests/uncertainty-view.test.js`
- `electionmapslogic/tests/us-house-uncertainty.test.js`
- `electionmapslogic/tests/us-senate-uncertainty.test.js`

## f417a14e — fix: remove look-ahead from tracker uncertainty bands

Use trailing averages of up to five updates within matching configuration and simulation-kernel segments. Preserve gaps and legacy identity boundaries, record kernel provenance and rebuild both map bundles.

Files:

- `data/model_support/monte_carlo.py`
- `data/model_support/trends.py`
- `data/tests/test_console_uncertainty_runs.py`
- `data/tests/test_model_trends.py`
- `data/tests/test_monte_carlo.py`
- `electionmaps/electionmaps.min.js`
- `electionmapslogic/features/polltracker-uncertainty.js`
- `electionmapslogic/features/polltracker.js`
- `electionmapslogic/tests/polltracker-uncertainty.test.js`
- `electionmapslogic/tests/polltracker-view.test.js`
- `electionmapslogic/tests/polltracker.test.js`
- `uselectionmaps/uselectionmaps.min.js`

## 92743751 — feat: freeze inputs for Westminster validation replay

Check in aggregate inputs for the 22 October baseline and validate their content/configuration/provenance identities. Replay the frozen inputs by default so archive-format changes cannot invalidate comparisons.

Files:

- `data/console/templates/uncertainty_validation.html`
- `data/model_support/westminster_validation_inputs.py`
- `data/model_support/westminster_validation_replay.py`
- `data/models/westminster/validation-inputs-2026-10-22-v1.json`
- `data/scripts/validate_westminster_uncertainty.py`
- `data/tests/test_westminster_validation_inputs.py`
- `README.md`

## ab3fc62e — feat: add offline Westminster conversion-error experiment

Test eight combinations of clustered seat errors, heavy seat tails and predictive lambda uncertainty using bounded training-only fits. Add durable checkpoints, exact latent-draw reuse, recovery checks and a saved-report console view.

Record the complete failed experiment: the combined candidate covers 15/20 outcomes against the unchanged 17/20 gate and fails additional calibration guards. Piece 2a remains unresolved; no production integration or activation is included.

Files:

- `README.md`
- `data/console/blueprints/uncertainty.py`
- `data/console/templates/uncertainty_validation.html`
- `data/console/templates/uncertainty_conversion_validation.html`
- `data/model_support/westminster_conversion_design.py`
- `data/model_support/westminster_conversion_validation.py`
- `data/model_support/westminster_conversion_report.py`
- `data/models/westminster/conversion-prototype-method.md`
- `data/scripts/validate_westminster_conversion.py`
- `data/tests/test_westminster_conversion_validation.py`
- `data/tests/test_westminster_conversion_report.py`
- `data/model_support/westminster_conversion_checkpoint.py`
- `data/models/westminster/uncertainty_conversion_prototype_report.json`


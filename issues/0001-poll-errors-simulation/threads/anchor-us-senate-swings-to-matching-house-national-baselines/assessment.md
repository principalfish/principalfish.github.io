# Assessment: Senate national generic-ballot baseline anchor

## Summary

The bug is present at `poll-errors-simulation`, assessed against `aafc7be2` on 2026-10-25. Senate national polling measures the House generic ballot, while its fallback subtracts the turnout-weighted shares of the selected Senate baseline field. This treats cohort composition as national swing.

## Verdict and evidence

**Present.** `data/models/us/_common.py:708` filters baseline votes to selected Senate seats; lines 679–686 apply special-race baseline overrides, and lines 731–744 derive the filtered national shares. `prepare_us_projection` at lines 2413–2420 and 2442–2450 passes these to `compute_region_diffs`, whose subtraction is at lines 953–955. The Senate's national series is documented as House generic ballot at lines 190–192.

A read-only reproduction using existing pure helpers starts with a 70% D / 30% R Senate baseline and unchanged 50% / 50% House levels. The current fallback applies −20 / +20 points and projects 50% / 50%; a matching 50% / 50% House baseline should produce zero national swing and retain the Senate race's 70% / 30% baseline.

Committed baseline-only historical comparisons retain observed-minus-projected raw D−R means of +12.4973 points in 2022 (31 singleton units, RMS 18.7754) and +12.5325 in 2024 (30 units, RMS 15.4735), even with official House national inputs. A read-only alternative subtracting the House national levels from each race's baseline year reduced these means to +0.6436 and +1.6955, respectively. These diagnostics do not establish complete calibration or remove candidate effects. The 2024 diagnostic uses the qualified 2018 House proxy: NC-09 November returns were uncertified, so it is not a certified complete national anchor.

`git blame` identifies subtractor history at `31b24aae8`, special override plumbing at `7460c1239`, and later presidential filtering at `ed2719ea7`. Recent Senate field, uncertainty, tracker and comparison commits did not correct this reference.

## Relevant code

- `data/models/us/_common.py`: `UsModelSpec`, `build_baseline_vote_state`, `compute_region_diffs`, `prepare_us_projection`, `blend_seat_swings`, `freeze_house_central_inputs`, `house_central_diagnostics`, central-only context persistence.
- `data/model_support/senate_cycles.py` and `data/models/us/senate_cycles/`: race baseline-year and special selection.
- `data/model_support/us_run.py`, `us_outcomes.py`, `us_replay.py`, `trends.py`: frozen options, identity, legacy replay and segment separation.
- `data/model_support/us_calibration_comparison.py`, `us_comparison_senate.py` and committed comparison inputs/reports: current independent reconstruction and source qualifications.

No source, live database, saved forecast or public export changed during assessment.

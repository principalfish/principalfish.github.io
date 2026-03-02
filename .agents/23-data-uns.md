# Area: UNS models (`data/models/uns/`)

## Purpose

Runs a regionalized Uniform National Swing-style simulation and persists modeled election outputs.

## Key scripts

- `run_uns_model.py`
  - Single simulation execution.
  - Supports dry-run and write modes.
  - Persists `model_uns` elections and votes when not dry-run.
  - Updates trend cache output.

- `run_retrospective_uns.py`
  - Loops by date across a range (default from 2024-07-05 to today).
  - Uses lookback window and half-life decay.
  - By default, clears existing `model_uns` elections/votes and deletes `data/models/uns/output/model_output_trends.csv` before backfill; use `--no-reset-existing` to keep prior outputs.
  - Supports `--continue-on-error`.

- `backfill_model_output_trends.py`
  - Rebuilds/repairs trend cache from stored outputs.
  - Supports optional wipe mode with `--reset-existing` to delete existing `model_uns` elections/votes and remove trend cache CSV before rebuilding.

## Operational note

Retrospective runs can be large (hundreds of model days) and should be planned with DB volume/runtime in mind.

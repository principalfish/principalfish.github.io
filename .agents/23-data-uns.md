# Area: UNS models (`data/models/uns/`)

## Purpose

Runs a regionalized Uniform National Swing-style simulation and persists modeled election outputs.

## Key scripts

- `run_uns_model.py`
  - Single simulation execution.
  - Supports dry-run and write modes.
  - Persists `model_uns` elections and votes when not dry-run.
  - Updates trend cache output.
  - In non-dry-run mode, automatically backfills missing daily dates between the latest prior trend-cache date and the requested `as_of_date`.

- `run_retrospective_uns.py`
  - Loops by date across a range (default from 2024-07-05 to today).
  - Uses lookback window and half-life decay.
  - By default, clears existing `model_uns` elections/votes and deletes `electionmaps/data/results/model_output_trends.csv` before backfill; use `--no-reset-existing` to keep prior outputs.
  - Supports `--continue-on-error`.

- `backfill_model_output_trends.py`
  - Rebuilds/repairs trend cache from stored outputs.
  - Supports optional wipe mode with `--reset-existing` to delete existing `model_uns` elections/votes and remove trend cache CSV before rebuilding.

## Operational note

Retrospective runs can be large (hundreds of model days) and should be planned with DB volume/runtime in mind.

## Schema compatibility note

- `run_uns_model.py` now fetches seat scope using a minimal SQL query (`id`, `region_id`) instead of ORM-loading full `Seat` rows.
- This avoids hard runtime dependence on `seats.electorate` for UNS execution when working against older/drifted DB schemas.

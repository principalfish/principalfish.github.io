# Area: UNS models (`data/models/uns/`)

## Purpose

- Run model elections and maintain trend outputs.

## Key scripts

- `run_uns_model.py`: single run, persistence, trend updates, date-gap backfill support.
- `run_retrospective_uns.py`: range runner; resets existing outputs by default unless `--no-reset-existing`.
- `backfill_model_output_trends.py`: rebuild/repair trend cache; optional reset mode; skips consecutive unchanged seat snapshots.

## Durable caveats

- Retrospective runs are long-running and can generate high DB volume.
- Keep trend dates canonical/normalized to avoid duplicate or drifted timelines.
- Trend cache writes now skip unchanged consecutive seat snapshots to avoid no-movement date spam.
- UNS runtime avoids strict dependence on newer seat columns by using minimal seat-scope queries.

# Area: UNS models (`data/models/uns/`)

## Purpose

- Run model elections and maintain trend outputs.

## Key scripts

- `run_uns_model.py`: single run, persistence, trend updates. Pass `--start-date`/`--end-date` for a retrospective date-range backfill (with optional `--reset-existing`, `--continue-on-error`, `--progress-every`).
- `backfill_model_output_trends.py`: rebuild/repair trend cache from scratch (reads both Postgres and SQLite archive); skips consecutive unchanged seat snapshots.

## Durable caveats

- Model run output (elections + votes) is written directly to local SQLite (`model_uns.db`), never to PostgreSQL or Supabase. PostgreSQL is only used to read polls, maps, seats, regions, and parties.
- Retrospective runs (`--start-date`/`--end-date`) are long-running and can generate high SQLite volume.
- Keep trend dates canonical/normalized to avoid duplicate or drifted timelines.
- Trend cache writes now skip unchanged consecutive seat snapshots to avoid no-movement date spam.
- UNS runtime avoids strict dependence on newer seat columns by using minimal seat-scope queries.

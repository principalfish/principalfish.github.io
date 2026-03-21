# Area: Tests (`data/tests/`)

## Purpose

- Validate DB-backed behavior for maps, seats, elections, votes, and polls.

## How to run

- From `data/`: `./run_tests.sh`
- Or: `../election_data/bin/python -m pytest tests/`

## Frontend tests

- Location: `tests/core.test.js` — unit tests for pure functions in `electionmaps/core.js`
- Runner: `vitest`
- Commands (from repo root): `npm test` (single run), `npm run test:watch` (watch mode)
- No DB required — these are pure function tests only.

## Caveats

- Backend test failures can be environment-driven (DB/auth/config), not only logic regressions.
- **`conftest.py` must use `DatabaseConfig.local()`** — never `from_env()`. The `url` computed field hardcodes `/postgres` for Supabase connections and ignores `config.database`, so setting `config.database = "election_maps_test"` has no effect when `SUPABASE_*` env vars are active. Using `from_env()` causes `drop_tables()` to run against production Supabase.

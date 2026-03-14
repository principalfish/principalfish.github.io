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

## Caveat

- Backend test failures can be environment-driven (DB/auth/config), not only logic regressions.

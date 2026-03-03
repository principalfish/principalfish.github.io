# Area: Tests (`data/tests/`)

## Purpose

- Validate DB-backed behavior for maps, seats, elections, votes, and polls.

## How to run

- From `data/`: `./run_tests.sh`
- Or: `../election_data/bin/python -m pytest tests/`

## Caveat

- Failures can be environment-driven (DB/auth/config), not only logic regressions.

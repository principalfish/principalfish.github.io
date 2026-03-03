# Area: Data core (`data/`)

## Purpose

- Main backend for election data ingest, storage, exports, and local tooling.

## Core files

- `models.py`, `db.py`, `config.py`, `server.py`
- `docker-compose.yml`, `start_db.sh`

## Schema truths

- Geography: maps/regions/seats
- Results: elections/votes
- Polling: pollsters/polls/poll_rows
- `seats.electorate` is canonical; turnout is derived from vote totals

## Operational notes

- Local runtime expects PostgreSQL/PostGIS.
- Backend orchestration is centered on `server.py` + script entrypoints.
- Local `/models/run` execution in `data/server.py` now auto-refreshes `electionmaps/data/results/prediction-simulation.json` after a successful non-dry-run UNS run.
- Local console also exposes a manual `/exports/current-simulation` action for one-click refresh of `prediction-simulation.json` without triggering a model run.

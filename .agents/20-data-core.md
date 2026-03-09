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
- `elections.parent_election_id` + `elections.election_date` can tag by-elections as children of a base general election.

## Operational notes

- Local runtime expects PostgreSQL/PostGIS.
- Backend orchestration is centered on `server.py` + script entrypoints.
- Local `/models/run` execution in `data/server.py` now auto-refreshes `electionmaps/data/results/prediction-simulation.json` after a successful non-dry-run UNS run.
- Local console also exposes a manual `/exports/current-simulation` action for one-click refresh of `prediction-simulation.json` without triggering a model run.
- `data/scripts/export_non_simulation_elections.py` now emits by-election overlay payloads and `settings.byElectionFilesByElectionId` entries in `electionmaps/data/elections.json` when child by-elections are linked to a base election.

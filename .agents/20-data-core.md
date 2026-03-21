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

## Export scripts (`data/scripts/`)

- `export_non_simulation_elections.py` — primary exporter for all non-model elections; emits result JSON and by-election overlays
- `export_manifest_metadata.py` — metadata-only manifest refresh without re-exporting result files
- `run_export_targets.py` — wrapper that exports all elections plus the latest simulation in one pass
- `migrate_results_to_v4.py` — reformats on-disk result files from v3 → v4 schema without a DB round-trip
- `normalize_uns_trend_dates.py` — normalises trend timeline date fields after import
- `split_ukip_reform_parties.py` — one-time split of historical UKIP/Reform vote rows

## Operational notes

- Local runtime expects PostgreSQL/PostGIS.
- Backend orchestration is centered on `server.py` + script entrypoints.
- Local `/models/run` execution in `data/server.py` now auto-refreshes `electionmaps/data/results/prediction-simulation.json` after a successful non-dry-run UNS run.
- Local console also exposes a manual `/exports/current-simulation` action for one-click refresh of `prediction-simulation.json` without triggering a model run.
- `data/scripts/export_non_simulation_elections.py` now emits by-election overlay payloads and `settings.byElectionFilesByElectionId` entries in `electionmaps/data/elections.json` when child by-elections are linked to a base election.

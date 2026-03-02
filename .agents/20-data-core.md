# Area: Data core (`data/`)

## Purpose

`data/` is the main election-data backend and operations toolkit.

## Core components

- `data/models.py`: canonical SQLAlchemy schema.
- `data/db.py`: DB wrapper + convenience APIs.
- `data/config.py`: DB connection config via env defaults.
- `data/server.py`: local Flask UI and orchestration endpoints.

## Main schema groups

- Geography: `maps`, `regions`, `seats`
- Elections: `elections`, `votes` (with `seats.electorate` for electorate)
- Polling: `pollsters`, `polls`, `poll_rows`

## Election data notes

- `seat_results` has been removed from the active schema.
- Seat electorate is stored directly on `seats.electorate`.
- Seat turnout is treated as a derived metric from `votes.vote_total` per `election_id` + `seat_id`.

## Runtime behavior

- Flask app calls importers and model scripts via subprocess or module calls.
- Poll CSV export supported via `polls/export_poll_rows_csv.py`.
- Model UI supports `run_uns_model.py` execution from browser workflow.

## Critical dependency

- PostgreSQL/PostGIS container from `docker-compose.yml`.

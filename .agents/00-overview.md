# Repo Overview

This repository has three practical product areas:

1. Static website hosting at repo root
2. `data/` election data system (Postgres + Flask + import pipelines + UNS model)
3. `guesstheyear/` standalone quiz-style web app

## High-level architecture

- Root (`404.html`, `CNAME`, `server.sh`) supports static hosting/local static preview.
- `data/` is a Python service layer backed by PostgreSQL/PostGIS.
- `data/server.py` is the canonical local app entrypoint for data operations.
- Import pipelines under `data/old_data/` and `data/polls/` populate relational tables.
- Modeling under `data/models/uns/` generates model elections and trend outputs.
- `guesstheyear/` appears independent from `data/` DB workflows.

## Operational flow (typical)

1. Create/activate Python env (`election_data`)
2. Start DB with `data/start_db.sh`
3. Run base imports (`old_data/*`)
4. Run poll imports (`polls/update_mapping_and_import_new.py`)
5. Run UNS (`run_uns_model.py` or retrospective runner)
6. Launch local Flask UI (`data/server.py`)

## Current important caveats

- Poll importers currently may write `0.0` for missing regional values for several pollsters.
- `data/start_db.sh` handles local port 5432 conflicts by trying to stop host Postgres.
- Retrospective UNS can run over long ranges and create many model outputs.
- `data/models/uns/run_retrospective_uns.py` now resets existing `model_uns` elections/votes and rewrites `model_output_trends.csv` by default (use `--no-reset-existing` to preserve prior outputs).
- Seat-level turnout should be computed from `votes.vote_total`; electorate is stored on `seats.electorate`.

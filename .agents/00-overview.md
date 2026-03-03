# Repo Overview

## Product areas

1. Root static site (`/`, `bio/`, `electionmaps/`)
2. Data backend (`data/`: Postgres + Flask + imports + UNS)
3. Guess The Year app (`guesstheyear/`)

## Typical backend flow

1. Activate `election_data` environment
2. Start DB via `data/start_db.sh`
3. Run base imports (`old_data`)
4. Run poll imports (`polls/update_mapping_and_import_new.py`)
5. Run model scripts (`data/models/uns/*`)

## Durable caveats

- Poll importers can zero-fill missing regional values.
- DB startup assumes local Docker Postgres on `5432`.
- Retrospective UNS can reset model outputs by default.
- Turnout is derived from votes; electorate is stored on seats.

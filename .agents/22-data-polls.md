# Area: Poll imports (`data/polls/`)

## Purpose

- Import and normalize polling data into `polls` and `poll_rows`.

## Pipeline

1. Build mappings (`build_wikipedia_poll_mappings.py`)
2. Sync pollsters (`sync_pollsters_from_mapping.py --apply`)
3. Import new polls (`update_mapping_and_import_new.py`)

## By-election import route

- Local console at `data/server.py` exposes a `/by-elections` route for manual by-election result ingestion.
- Paste a Wikipedia by-election URL, preview scraped candidates, then confirm to insert into DB.
- Module: `data/polls/importers/by_election_import.py`

## Durable caveat

- Some importer paths zero-fill missing regional values; this can inflate `% zero` rows in incomplete regional crosstabs.

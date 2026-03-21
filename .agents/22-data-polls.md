# Area: Poll imports (`data/polls/`)

## Purpose

- Import and normalize polling data into `polls` and `poll_rows`.

## Pipeline

1. Refresh mappings and sync pollsters (`polls/importers/refresh_poll_mappings.py --apply`)
2. Import new polls (`update_mapping_and_import_new.py`, which calls step 1 automatically)

Run `update_mapping_and_import_new.py` for normal operation. Run `refresh_poll_mappings.py` standalone to rebuild mapping files and sync pollsters without triggering a full import.

## Shared types

- `polls/importers/types.py` — `PollImportResult` returned by all per-pollster importers.

## By-election import route

- Local console at `data/server.py` exposes a `/by-elections` route for manual by-election result ingestion.
- Paste a Wikipedia by-election URL, preview scraped candidates, then confirm to insert into DB.
- Module: `data/scripts/by_election_import.py`

## Durable caveat

- Some importer paths zero-fill missing regional values; this can inflate `% zero` rows in incomplete regional crosstabs.

# Area: Poll imports (`data/polls/`)

## Purpose

- Import and normalize polling data into `polls` and `poll_rows`.

## Pipeline

1. Build mappings (`build_wikipedia_poll_mappings.py`)
2. Sync pollsters (`sync_pollsters_from_mapping.py --apply`)
3. Import new polls (`update_mapping_and_import_new.py`)

## Durable caveat

- Some importer paths zero-fill missing regional values; this can inflate `% zero` rows in incomplete regional crosstabs.

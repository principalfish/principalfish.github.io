# Area: Poll import pipeline (`data/polls/`)

## Purpose

Automates poll ingestion from Wikipedia-mapped source URLs into `polls`/`poll_rows` tables.

## Pipeline

1. `build_wikipedia_poll_mappings.py`
   - Scrapes Wikipedia national poll references and builds mapping CSV/JSON.
2. `sync_pollsters_from_mapping.py --apply`
   - Ensures pollster identifiers exist in DB.
3. `update_mapping_and_import_new.py`
   - Runs mapping refresh + pollster sync + importer dispatch.

## Importers

- One file per pollster under `data/polls/importers/`.
- Each importer generally exposes `build_import_plan` + `commit_import_plan` + CLI.

## Known behavior (important)

Several importers currently default missing regional entries to `0.0`, which can create high `% zero` rows for some polls when regional crosstabs are absent or partial.

Observed in importer code paths such as:
- `find_out_now_import.py`
- `more_in_common_import.py`
- `focaldata_import.py`
- `survation_import.py`
- (and similar fallback patterns in others)

## Useful outputs

- `data/polls/mappings/` contains generated map/profile/registry and skip reports.
- `data/recovery/` has ad-hoc audit files (e.g., zero-percentage reports).

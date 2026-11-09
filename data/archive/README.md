# Archive

Finished one-off scripts and retired setup files, kept as a record of how the
data got to where it is.

- **Not maintained.** Nothing here is type-checked or tested, and nothing in the
  live code imports from it.
- **May not run.** Later folder moves are not applied to these files, so their
  imports, relative paths and usage lines can be stale. Read them for what they
  did, not as commands to run.
- **Not a rebuild step.** A fresh setup is rebuilt with
  `scripts/rebuild_database.py`, not by replaying these.

Files that stop being needed are moved here with `git mv`, so their history
follows them.

## Retired converters

These turned a source dataset into the project's election JSON. The JSON they
produced is committed under `inputs/us/`, which is now the source of truth.

- `convert_538_house.py`, `convert_538_senate.py`, `convert_538_presidential.py`:
  FiveThirtyEight results CSVs to House, Senate and presidential files.
  `convert_538_senate.py` and `convert_538_presidential.py` reuse
  `convert_538_house.aggregate_unit`, so the three stay together.
- `convert_wiki_presidential.py`: scraped Wikipedia for the presidential years
  FiveThirtyEight does not cover. It reuses `convert_538_presidential`.
- `convert_senate_current.py`: first built the Current Senate snapshot from the
  congress-legislators list. `importers/results/us/nyt/current_senate.py` now
  rebuilds that snapshot from the 2026 Senate election file.

`convert_538_presidential.py`, `convert_wiki_presidential.py` and
`convert_senate_current.py` import `regions` and `us_electoral_votes` by bare
name, and those modules have moved under `importers/`, so those three no longer
run as they stand.

## Retired migrations and repair scripts

Idempotent one-offs that upgraded an existing database to a schema or data change that
`schema.py` and the importers now build from scratch, so a rebuild never needs them.

- `migrate_add_poll_bases.py`, `migrate_add_model_uncertainty.py`,
  `migrate_add_us_poll_scope.py`, `migrate_add_us_result_status.py`,
  `migrate_add_electoral_votes.py`, `migrate_drop_seat_geometry.py`,
  `migrate_rename_model_uns.py` (a PostgreSQL enum rename from before the SQLite move):
  schema migrations.
  `migrate_add_us_poll_scope.py` keeps its own copy of the helpers that
  `core/migration_helpers.py` now provides, because it needs them itself. The other archived
  scripts import them with `from scripts.migrate_add_us_poll_scope import ...`, which no
  longer resolves, so those scripts do not run.
- `backfill_poll_bases.py`, `repair_poll_records.py`, `repair_regional_poll_rows.py`,
  `reset_us_poll_matchups.py`, with `poll_sources.py` (their shared fetch helpers): data
  repairs run once against the live database.
- `scrape_holyrood_2026.py`: wrote the 2026 Holyrood result files under
  `inputs/holyrood/`, which are committed.

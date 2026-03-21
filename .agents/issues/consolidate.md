# Data folder script inventory

## `data/` — Root files

**[config.py](../../data/config.py)** — Database connection configuration using pydantic-settings. Reads env vars for either local Docker Postgres (`DB_*` vars) or remote Supabase (`SUPABASE_*` vars). Computes a `DATABASE_URL` accordingly. Has a `.local()` class method that forces local mode regardless of env, used by model scripts that must never accidentally write to Supabase.

**[models.py](../../data/models.py)** — SQLAlchemy ORM model definitions. Defines all DB tables: `Party`, `Map`, `Region`, `Seat`, `Election`, `Vote`, `Pollster`, `Poll`, `PollRow`, plus the `ElectionType` enum. This is the schema layer — no business logic, just column definitions, foreign keys, and relationships.

**[db.py](../../data/db.py)** — The main database access layer. A `Database` class wrapping SQLAlchemy, providing typed CRUD methods for every entity (add/get/bulk_add for parties, maps, regions, seats, elections, votes, pollsters, polls, poll rows). Handles session lifecycle with commit/rollback. This is what all scripts import to talk to the DB.

**[server.py](../../data/server.py)** — A Flask web server (local only) providing a UI for poll imports, by-election imports, model runs, and data exports. Has Pydantic-validated form models, integrates with all the importer modules, and exposes endpoints like `POST /import/preview` and `POST /models/run`. This is the human-operated admin interface.

---

## `data/old_data/` — One-time setup scripts (run at DB initialisation time)

Run all of these via `data/old_data/import_all.sh`. Scripts live in `old_data/scripts/`.

**[scripts/import_topojson.py](../../data/old_data/scripts/import_topojson.py)** — Seeds the DB with constituency boundary geometry. Reads two TopoJSON files (`650map.json` pre-2019, `650map_new.json` post-2022), decodes arcs manually (no topojson library), creates Map + Region + Seat rows with MultiPolygon geometries. Run once when setting up a fresh DB.

**[scripts/import_parties.py](../../data/old_data/scripts/import_parties.py)** — Seeds the `parties` table with a hardcoded list of UK parties (Labour, Conservative, SNP, etc.) with their hex brand colours and generated short names. Upserts — safe to re-run.

**[scripts/import_general_elections.py](../../data/old_data/scripts/import_general_elections.py)** — Imports historical UK general election results (2010–2024) from JSON files into the `elections` and `votes` tables. Normalises seat names for fuzzy matching, routes each year to the correct boundary map, upserts parties, and sets electorate counts. Has `--dry-run`, `--skip-existing`, and `--only-year` flags.

**[scripts/import_region_populations.py](../../data/old_data/scripts/import_region_populations.py)** — Updates the `population` column on region rows from `files/region_populations.csv`. Case-insensitive name matching. Has `--dry-run`.

**[migrate_to_supabase.sh](../../data/old_data/migrate_to_supabase.sh)** — One-time shell script for migrating local Postgres data to Supabase. Kept for reference.

---

## `data/polls/` — Ongoing poll ingestion

**[update_polls.sh](../../data/polls/update_polls.sh)** — Shell entrypoint. Resolves the virtualenv Python and runs `update_mapping_and_import_new.py` from the `data/` root. The standard way to trigger a poll update.

**[update_mapping_and_import_new.py](../../data/polls/update_mapping_and_import_new.py)** — The main poll ingestion orchestrator. Calls `importers/refresh_poll_mappings.py --apply` first, then iterates over new mapping rows (newest first), skipping already-imported URLs, unimportable URLs (wrong format, social media, etc.), and parsers with no prior polls. Invokes the appropriate per-pollster importer subprocess for each new poll. Re-runs the UNS model if anything was imported.

**[importers/refresh_poll_mappings.py](../../data/polls/importers/refresh_poll_mappings.py)** — Scrapes the Wikipedia UK opinion polling page, extracts the national results table, resolves citation references to source URLs, infers file formats (pdf/xlsx/html etc.), and normalises pollster names to snake_case identifiers. Outputs `mappings/wikipedia_national_polls_mapping.csv` and `mappings/parser_registry.json`. Then creates any missing `Pollster` rows in the DB (dry-run by default, writes with `--apply`). Can also be run standalone.

**[importers/types.py](../../data/polls/importers/types.py)** — `PollImportResult` Pydantic model returned by all per-pollster importers.

**[importers/](../../data/polls/importers/)** — Per-pollster importer modules (yougov, techne, opinium, more_in_common, find_out_now, focaldata, bmg_research, survation, deltapoll, ipsos, lord_ashcroft). Each exposes `build_import_plan` and `commit_import_plan`.

**[mappings/](../../data/polls/mappings/)** — Generated output files. `wikipedia_national_polls_mapping.csv` (poll source rows from Wikipedia) and `parser_registry.json` (parser identifier → module + status). Both read by `update_mapping_and_import_new.py`.

---

## `data/scripts/` — Export and utility scripts

**[export_non_simulation_elections.py](../../data/scripts/export_non_simulation_elections.py)** — The main data export script. Run with no flags to export all elections + full `elections.json` manifest. `--metadata-only` updates only `settings.parties`/`settings.regionsByMapId` in `elections.json` without re-exporting result files. `--election-name` or `--current-simulation` with `--output-file` exports a single target. Handles by-elections by compositing them into a "Current Parliament" overlay.

**[by_election_import.py](../../data/scripts/by_election_import.py)** — Scrapes a Wikipedia by-election article and inserts an `Election` (type `by_election`) with `Vote` rows into the DB. Primarily used by `server.py` to power the `/by-elections` admin UI. Can also be run from the CLI with `--url` and `--dry-run`.

**[archive_old_model_runs.py](../../data/scripts/archive_old_model_runs.py)** — Archives `model_uns` election rows older than N days from Postgres to a local SQLite file (`model_uns.db`), then deletes them from Postgres. Keeps the DB lean. Called automatically by `run_uns_model.py` after each simulation run; can also be run standalone.

---

## `data/models/uns/` — UNS seat projection model

**[run_uns_model.py](../../data/models/uns/run_uns_model.py)** — The core Uniform National Swing model. Takes a baseline election (e.g. 2024 GE) and a window of recent polls, computes exponentially-decayed weighted average vote shares (nationally and per region), applies the swing to each seat's actual result, and writes a new `model_uns` election to the DB. Appends a row to `model_output_trends.csv` for the frontend chart. Auto-archives old runs via `archive_old_model_runs`. Single-date mode by default; pass `--start-date` and `--end-date` for retrospective backfill across a date range (with `--reset-existing`, `--continue-on-error`, `--progress-every`).

**[backfill_model_output_trends.py](../../data/models/uns/backfill_model_output_trends.py)** — Repair/rebuild tool. Reads persisted `model_uns` elections from both PostgreSQL and the local SQLite archive, aggregates seat counts and vote percentages per party per election, and rewrites `model_output_trends.csv` from scratch. Skips consecutive elections with identical seat distributions to keep the file compact. Use when the trend CSV is stale or corrupt.

---

## `data/tests/`

**[conftest.py](../../data/tests/conftest.py)** — Pytest fixture providing a fresh `Database` instance per test against a dedicated `election_maps_test` database (drop+create on setup, drop on teardown). Ensures test isolation.

**[test_maps.py](../../data/tests/test_maps.py)** — Tests `Map` CRUD: creation, duplicate name constraint, lookup by ID and name.

**[test_parties.py](../../data/tests/test_parties.py)** — Tests `Party` CRUD: creation with optional fields, duplicate constraint, lookup by ID and name.

**[test_regions.py](../../data/tests/test_regions.py)** — Tests `Region` CRUD: basic creation, hierarchical parent/child, population, invalid map FK rejection.

**[test_seats.py](../../data/tests/test_seats.py)** — Tests `Seat` CRUD: creation with/without region, geometry acceptance (Shapely MultiPolygon and raw GeoJSON), invalid FK rejection.

**[test_elections.py](../../data/tests/test_elections.py)** — Tests `Election` CRUD: all four `ElectionType` values, duplicate name constraint, lookup by ID and name.

**[test_votes.py](../../data/tests/test_votes.py)** — Tests vote insertion, turnout calculation, winner query, bulk insertion, and seat electorate setting.

**[test_polls.py](../../data/tests/test_polls.py)** — Tests `Pollster`, `Poll`, and `PollRow` CRUD: creation, weight, regions mapping, duplicate identifier constraint, poll row bulk insertion, querying by pollster/map/date.

# principalfish.github.io

The GitHub Pages static site for **principalfish.co.uk**, plus the Python data
pipeline that powers its interactive UK election maps.

## Repository structure

| Path | What it is |
|------|------------|
| `index.html`, `404.html`, `CNAME` | Landing page, error page, GitHub Pages custom domain |
| `site/` | Shared frontend assets — styles, top bar, Google Analytics, vendored `d3`/`topojson` bundles |
| `bio/` | Static bio page |
| `electionmapslogic/` | Shared election-maps engine (D3 + TopoJSON): core modules (`state`/`dom`/`utils`/`files`/`app`), `features/` (predict + poll tracker, opt-in per page), shared app markup (`shell.html` + opt-in `fragments/`, injected per page by `shell-loader.js`), `maps.css`, `mobile-sidebar`, `tests/` = Vitest specs |
| `electionmaps/` | UK election-maps page shell — thin `index.html` (header + shell mount; loads fragments `postcode`/`referendum-info`/`polltracker`), the `electionmaps.js` entry (bundled to `electionmaps.min.js`), and `data/` = UK exported maps/results + `map-modes(-shell).json` |
| `uselectionmaps/` | US election-maps page shell — thin `index.html` (header + shell mount; no fragments), the `uselectionmaps.js` entry (bundled to `uselectionmaps.min.js`), and `data/` = US exported maps/results + `map-modes(-shell).json` |
| `guesstheyear/` | "Guess the year" game — static frontend + Python helpers (`app.py`, `wiki.py`, `export.py`) |
| `referdle-solver/` | Referdle solver — static frontend (`js/`, `css/`, `data/`) |
| `imgs/` | Shared images and logos |
| `data/` | Python election-data pipeline — see below |
| `server.sh` | Local dev server: builds frontend assets, then serves on `:8000` |
| `package.json` | Frontend build tooling (esbuild, terser, clean-css) + Vitest tests |

### `data/` layout

| Path | What it is |
|------|------------|
| `models.py`, `db.py`, `config.py` | SQLAlchemy schema, DB access, configuration |
| `console/` | Local web console for the data (`create_app`) |
| `server.py` | Local data preview server (`:5000`) |
| `old_data/` | Base-data importers (TopoJSON maps, parties, general elections) |
| `polls/` | Wikipedia-driven poll importers |
| `models/` | Election models — `westminster/`, `holyrood/` (UNS retrospective) |
| `scripts/` | Static export scripts that partition elections by parliament into the per-page data dirs (`electionmaps/data`, `uselectionmaps/data`) |
| `tests/` | Pytest suite — run with `./run_tests.sh` (strict mypy, then pytest) |

## Static site local preview (`/`, `/bio`, `/electionmaps`)

Prerequisites:
- Node.js + npm (for frontend asset build steps)
- Python 3 (for local static server)

From repo root:

```bash
./server.sh
```

`server.sh` now runs frontend asset build steps before serving:
- `npm run vendor:d3`
- `npm run minify:electionmaps`

Then it starts `python3 -m http.server` (default port `8000`).
To use a different port:

```bash
PORT=8001 ./server.sh
```

## Data subsystem setup and runbook

This guide covers local setup for the `data/` part of the repo end-to-end:
- Python environment
- SQLite database
- Full base-data import
- Poll import (Wikipedia-driven)
- UNS retrospective run
- Local server and validation

---

## 1) Prerequisites

- Linux/macOS shell
- Python 3.10+
- `sqlite3` CLI (for inspection/backups)
- Network access (poll importers fetch remote PDFs/XLSX/HTML)

---

## 2) Python environment

From repo root:

```bash
python3 -m venv election_data
source election_data/bin/activate
pip install -r data/requirements.txt
```

If `election_data` already exists, just activate it:

```bash
source election_data/bin/activate
```

---

## 3) Database

The app uses a single local SQLite database. Point it at a file with the
`DATABASE_PATH` environment variable (see `.env_example`); the default is
`/home/philiph/dbs/elections.db`. The ORM and the raw-sqlite model read/write
paths both read `DATABASE_PATH`. Copy `.env_example` to `.env` and adjust the
paths if needed.

Tables are created automatically by `Database.create_tables()`
(`Base.metadata.create_all`). A fresh database can be populated with the base-data
importers below, or recovered from the Google Drive snapshot (under `DRIVE_DBS_DIR`).

The live database stays on local disk — SQLite must not run off the Drive mount.
`data/scripts/backup_to_drive.sh` snapshots it to Google Drive (`DRIVE_DBS_DIR`);
`data/scripts/restore_from_drive.sh` restores it back. The data console's Backup /
Restore buttons run these, and a scheduled run can call `backup_to_drive.sh` daily
(without `--force` it skips if a snapshot was already taken today).

---

## 4) Rebuild the database from source data

`scripts/rebuild_database.py` re-imports **every** base dataset — parties, maps,
regions, seats, and all historical election results (Westminster, Holyrood, US,
by-elections) — then re-exports the static site data. It runs each importer in
**ID-preserving `--refresh` mode**, so it never deletes a map/region/seat/party
or a historical-election row; it only clears+reinserts that election's votes.
**Polls and model runs are preserved** (their foreign keys stay valid), and one
failed step does not abort the run (a per-step summary is printed).

One command (from `data/`, environment active):

```bash
./election_data/bin/python scripts/rebuild_database.py            # full rebuild
./election_data/bin/python scripts/rebuild_database.py --dry-run  # list steps only
```

The data console exposes the same thing as a **"Rebuild Database"** button (Site
card). Both are byte-idempotent: re-running produces no diff when the source data
hasn't changed.

### Underlying importers

The orchestrator chains these (all under `old_data/scripts/`, run from `data/`);
you can also run any one directly with `--refresh`:

```bash
./election_data/bin/python old_data/scripts/import_parties.py
./election_data/bin/python old_data/scripts/westminster/import_topojson.py --refresh
./election_data/bin/python old_data/scripts/import_region_populations.py \
	--map-name "UK Constituencies post 2022" \
	--input old_data/files/westminster/region_populations.csv
./election_data/bin/python old_data/scripts/westminster/import_general_elections.py --refresh
./election_data/bin/python old_data/scripts/holyrood/import_holyrood_seats.py --refresh
./election_data/bin/python old_data/scripts/holyrood/import_holyrood_elections.py --refresh
./election_data/bin/python old_data/scripts/usa/import_house_elections.py \
	--file old_data/files/usa/house-2024.json --year 2024 \
	--name "2024 US House Election" --refresh      # + senate / presidential per file
./election_data/bin/python scripts/by_election_import.py \
	--url <wikipedia-url> --refresh                # one per line in
	                                               # old_data/files/westminster/by_elections.txt
```

Seat boundary geometry is **not** stored in the database — the site renders from
the committed `electionmaps/data/maps/map-*.topo.json`. `import_topojson.py`
(Westminster) and `import_holyrood_seats.py` create seats from those committed
TopoJSON files; no PostGIS is required.

---

## 5) Import polls (Wikipedia-driven)

### Mapping refresh only

```bash
../election_data/bin/python polls/build_wikipedia_poll_mappings.py
```

### Full poll import pipeline

```bash
../election_data/bin/python polls/update_mapping_and_import_new.py --include-unimported-parsers
```

Notes:
- `--include-unimported-parsers` is important for fresh databases.
- Without it, parsers with no historical rows can be skipped.

Wrapper script alternative:

```bash
./update_polls.sh --include-unimported-parsers
```

---

## 6) Run UNS retrospective

From `data/models/westminster/`:

```bash
cd data/models/westminster
../../../election_data/bin/python run_retrospective_uns.py --continue-on-error
```

Useful options:
- `--start-date YYYY-MM-DD`
- `--end-date YYYY-MM-DD`
- `--lookback-days 365`
- `--half-life-days 30`
- `--dry-run`
- `--no-reset-existing` (preserve existing `model_uns` elections and trend CSV; default behavior is to clear them before backfill)
- `--reset-existing` (explicitly force reset behavior; enabled by default)

---

## 7) Run local server

From `data/`:

```bash
../election_data/bin/python server.py
```

Server URL:
- `http://127.0.0.1:5000/`

---

## 8) Quick validation queries

From `data/`:

```bash
sqlite3 "$DATABASE_PATH" "SELECT count(*) FROM maps;"
sqlite3 "$DATABASE_PATH" "SELECT count(*) FROM elections;"
sqlite3 "$DATABASE_PATH" "SELECT count(*) FROM polls;"
sqlite3 "$DATABASE_PATH" "SELECT type, count(*) FROM elections GROUP BY type ORDER BY type;"
```

---

## 9) Common troubleshooting

- **Wrong database / empty results**
	- Ensure `DATABASE_PATH` points at the intended `elections.db` file (see `.env`).

- **Database is locked**
	- SQLite uses WAL journaling; make sure no other writer (e.g. a model run)
	  is mid-transaction, and never open the live DB off the Google Drive mount.

- **Poll importer skips everything**
	- Use `--include-unimported-parsers` on a fresh DB.

- **Many `0` regional poll rows**
	- Current importers can default missing regional values to `0.0` for some source formats.
	- Audit directly in DB, for example:

```bash
sqlite3 "$DATABASE_PATH" "SELECT poll_id, COUNT(*) AS zero_rows FROM poll_rows WHERE percentage = 0 GROUP BY poll_id ORDER BY zero_rows DESC LIMIT 25;"
```

---

## 10) Static election-map export (manifest + files)

Use scripts under `data/scripts/` to generate static files for `electionmaps/`.

### Bulk export (all non-simulation elections)

From repo root:

```bash
./election_data/bin/python data/scripts/export_elections.py
```

Dry-run:

```bash
./election_data/bin/python data/scripts/export_elections.py --dry-run
```

### Targeted exports

```bash
./election_data/bin/python data/scripts/export_elections.py --election-name "2019 General Election" --output-file /tmp/2019.json
./election_data/bin/python data/scripts/export_elections.py --current-simulation --output-file /tmp/current-simulation.json
```

### Wrapper export (all elections + latest simulation)

```bash
./election_data/bin/python data/scripts/run_export_targets.py
```

### Metadata-only manifest refresh

```bash
./election_data/bin/python data/scripts/export_manifest_metadata.py
```

### UKIP/Reform DB split migration

```bash
./election_data/bin/python data/scripts/split_ukip_reform_parties.py --dry-run
./election_data/bin/python data/scripts/split_ukip_reform_parties.py
```

### Manifest contract used by webpage

`electionmaps/data/elections.json` now includes:

- `defaultElection`
- `settings.mapFilesById` (map_id -> `maps/map-<id>.topo.json`)
- `settings.dataFilesByElectionId` (election id -> `results/<file>.json`)
- `settings.parties`, `settings.partiesByKey` (party metadata + colour lookups)
- `settings.regionsByMapId` (region metadata grouped by map)
- `elections[]` entries containing at least `id`, `name`, `year`, `type`, `mapId`, and optional `comparisonElectionId`

The webpage (`electionmaps/electionmaps.js`) resolves files from `settings` using election `id` + `mapId`.

### Results schema

- Exported result payloads use compact schema `pf-results-v4`:
	- top-level: `{"schema":"pf-results-v4","seats":[...]}`
	- seat keys: `n` (seat name), `r` (region), `w` (winner), `e` (electorate), `m` (majority), `t` (turnout), `p` (party rows)
	- party row: `[partyKey, total, name]`
- Frontend loader supports both compact and legacy result formats.

### Topo output behavior

- Bulk export writes TopoJSON per map (`maps.id`), not per election.
- For the current DB this yields two topo files in `electionmaps/data/maps/`.
- Stale per-election topo files are removed during bulk export.

---

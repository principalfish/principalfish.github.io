Hosting for principalfish.co.uk

Contributor workflow notes (plans, internal docs, and task learnings) are maintained in `AGENTS.md` and `.agents/README.md`.

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
- PostgreSQL bootstrap
- Full base-data import
- Poll import (Wikipedia-driven)
- UNS retrospective run
- Local server and validation

---

## 1) Prerequisites

- Linux/macOS shell
- Python 3.10+
- Docker + Docker Compose plugin (`docker compose`)
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

## 3) Database bootstrap

From `data/`:

```bash
cd data
./start_db.sh
```

Expected local connection:
- Host: `localhost`
- Port: `5432`
- DB: `election_maps`
- User: `election_maps`
- Password: `election_maps_dev`

`start_db.sh` will try to stop a local host Postgres on port 5432 if needed.
If that cannot be done automatically, run:

```bash
sudo systemctl stop postgresql
./start_db.sh
```

---

## 4) Import every base dataset (from scratch)

These are the core loaders for the current SQLAlchemy schema in `data/models.py`.

From `data/` with environment active:

```bash
../election_data/bin/python old_data/import_topojson.py
../election_data/bin/python old_data/import_parties.py
../election_data/bin/python old_data/import_general_elections.py
```

Optional regional populations (requires your own CSV/JSON input):

```bash
../election_data/bin/python old_data/import_region_populations.py \
	--map-name "UK Constituencies post 2022" \
	--input old_data/files/region_populations_template.csv
```

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

From `data/models/uns/`:

```bash
cd data/models/uns
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
docker compose exec -T db psql -U election_maps -d election_maps -c "SELECT count(*) FROM maps;"
docker compose exec -T db psql -U election_maps -d election_maps -c "SELECT count(*) FROM elections;"
docker compose exec -T db psql -U election_maps -d election_maps -c "SELECT count(*) FROM polls;"
docker compose exec -T db psql -U election_maps -d election_maps -c "SELECT type, count(*) FROM elections GROUP BY type ORDER BY type;"
```

---

## 9) Common troubleshooting

- **DB auth fails / wrong DB in client**
	- Ensure you are connecting to Docker DB on `localhost:5432` with `election_maps/election_maps_dev`.
	- Re-run `./start_db.sh`.

- **Port 5432 already in use**
	- Stop local Postgres (`sudo systemctl stop postgresql`) and re-run bootstrap.

- **Poll importer skips everything**
	- Use `--include-unimported-parsers` on a fresh DB.

- **Many `0` regional poll rows**
	- Current importers can default missing regional values to `0.0` for some source formats.
	- Audit directly in DB, for example:

```bash
docker compose exec -T db psql -U election_maps -d election_maps -c "SELECT poll_id, COUNT(*) AS zero_rows FROM poll_rows WHERE pct = 0 GROUP BY poll_id ORDER BY zero_rows DESC LIMIT 25;"
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

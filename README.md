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
| `server.py` | Local data preview server (`:5055`) |
| `old_data/` | Base-data importers (TopoJSON maps, parties, general elections) |
| `polls/` | Wikipedia-driven poll importers |
| `models/` | Election models — `westminster/`, `holyrood/` (UNS retrospective), `us/` (House, President, Senate forecasts) |
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
- US polls and forecasts (section 11)

---

## 1) Prerequisites

- Linux/macOS shell
- Python 3.10+
- `sqlite3` CLI (for inspection)
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

`.env` is loaded with `override=True`, so a `DATABASE_PATH` set in `.env` beats one
exported in the shell: `DATABASE_PATH=/tmp/copy.db ./election_data/bin/python …`
still opens the database named in `.env`. To run a script against a copy, edit
`.env` (or call the script's functions with an explicit connection).

Tables are created automatically by `Database.create_tables()`
(`Base.metadata.create_all`). A fresh database can be populated with the base-data
importers below, or restored from a backup (below).

### Backups

The live database stays on local disk — SQLite must not run off the Drive mount.
`data/backup.py` backs it up with SQLite's own backup API (safe mid-write) and
integrity-checks the copy:

- **Local archives** (`ELECTIONS_ARCHIVE_DIR`, default `~/dbs/elections`): a dated
  `elections-<UTC stamp>.db.gz` whenever the data has changed, plus a plain newest
  `elections.db`. The newest `ELECTIONS_BACKUP_KEEP` (default 30) are kept.
- **Google Drive** (`ELECTIONS_BACKUP_DIR`, e.g. `/mnt/g/My Drive/dbs`): one file,
  `elections.db.gz`, overwritten at most once a day. Drive is nearly full and each
  overwrite leaves a revision that counts against the quota, so history lives
  locally. If the Drive folder isn't mounted the push is skipped, not failed.

The data console backs up by itself after any request that could write (including
the scripts it launches) and once on startup. Its **Backup now** button archives
immediately and refreshes the Drive copy regardless of the daily limit; **Restore
newest archive** restores from the newest local archive, or the Drive copy if
there is none, keeping the replaced database as `<db>.prerestore`.

From a terminal — after running a writing script directly, for example:

```bash
./election_data/bin/python scripts/backup_db.py            # archive if changed; Drive if due
./election_data/bin/python scripts/backup_db.py --push     # ...and refresh Drive now
./election_data/bin/python scripts/backup_db.py --dry-run  # show paths and state only
./election_data/bin/python scripts/backup_db.py restore    # restore (stop the console first)
```

A backup of the full database takes about 20 s (most of it gzip and the integrity
check); the archive is about 136 MB.

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
- `http://127.0.0.1:5055/`

It answers only `127.0.0.1` / `localhost`, and refuses a POST made from another
site's page (`data/console/csrf.py`).

The Werkzeug debugger and auto-reloader are off by default: the debugger runs
arbitrary code for anything that can reach the port. For reload-on-save while
developing:

```bash
CONSOLE_DEBUG=1 ../election_data/bin/python server.py
```

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

## 11) US polls and forecasts

Everything below runs from `data/` (environment active). The US side has three
maps — "US House Districts 2024", "US Presidential 2024" and "US Senate 2024" —
all regioned by the 9 Census divisions. State and district polls are attached to
a **seat** on the poll, never by re-pointing seats to state regions (front-end
Predict depends on the divisions).

### Contests

Polls come from Wikipedia, as four contests:

| Contest (slug) | Map | Source | Poll scope |
|---|---|---|---|
| House national generic ballot (`house_national`) | House | The "Generic congressional ballot aggregate polls" table on the 2026 House elections article. It is an aggregation table (there are no individual generic-ballot polls on Wikipedia); its closing "Average" row is skipped | National. Also the Senate's national swing |
| House district polls (`house_districts`) | House | Each state's 2026 House article: "District N › General election › Polling", or the at-large page's "General election › Polling" | District seat (`TX-07`, at-large `AK-01`) |
| Senate race polls (`senate_races`) | Senate | The 35 race pages linked from the 2026 Senate elections article (33 regular races plus the Florida and Ohio specials) | State seat |
| Presidential polls (`president`) | President | The nationwide 2028 polling article, plus the statewide article (404 today, reported as a note) | National, or a state / `Maine CD-n` / `Nebraska CD-n` seat |

What gets imported:
- Only **visible general-election** tables. Primary sections, poll-aggregation
  tables (apart from the generic ballot) and collapsed hypotheticals are skipped.
- **Every matchup is its own poll** (`polls.matchup`, e.g. `Paxton (R) vs Talarico (D)`),
  with the candidates' names on its rows. One poll of three line-ups is three polls.
- DFL and D-NPL count as Democratic; I, L and G map to Independent, Libertarian and
  US Green. An unknown suffix such as `(IA)` or `(WCP)` excludes that candidate,
  with a warning.
- Same-party candidates (Alaska's top four, same-party top-two races) are summed.
- Pollsters are per chamber: `<slug>_us_house`, `_us_senate`, `_us_president`.

### Review queue (`/us/import`)

The normal way in. Start the console (section 7), then **Import US Polls** on the
home page. The start form has:
- **Contests** — at least one.
- **Only these states** — optional, names or postal codes, comma-separated; limits
  the per-state race pages.
- **Only polls ending on or after** — optional. Blank means per race: each
  (map, seat, matchup) only offers rows newer than its latest stored poll, and a
  race with no stored polls offers everything. A date applies to every race.
- **Run US models when finished**.
- **Include collapsed tables for races with no visible table** — opt-in. Those
  tables never set a race's automatic matchup.

Fetching takes roughly 20–40 s (about 90 pages). The queue then shows one row at a
time, with the matchup, candidates, heading path and any warnings (new pollster,
partisan tag, summed or excluded candidates, not the race's lead table, …):
- **Approve** / **Skip** / **Retry** (after a failure), as in the UK queue.
- **Approve all N remaining in \<race\>** commits every pending row of that race;
  a row that fails is marked failed and the rest still commit.

Finishing applies automatic matchup tracking once, then runs the models and the
export if the checkbox was ticked and the run imported anything or moved a race
onto a new matchup. Only today's trend point follows a moved matchup; the summary
says so, and a history rebuild on the matchup pages moves the earlier points.
Abandoning still applies tracking but skips the model run. The summary lists page
failures and 404 notes, collapsed-only races, unknown suffixes, dropped variants,
unmatched seats and the tracking outcomes.
A page over 8 MiB, or cut off mid-download, is listed as a page failure; the rest
of the import carries on. So is a page that would take the pages kept past 256 MiB
of memory.

### Matchups

The model only uses the polls of a seat's **tracked matchup**.
- **President** — you pick the national matchup at `/us/president/matchup` (from
  the stored labels, with counts and latest date; "None" clears it). State polls
  follow the same matchup. With none set, `run_us_presidential_model.py` exits 2
  and **Run US Models** skips only the President
  (`SKIPPED: no tracked presidential matchup set`).
- **Senate and House** — tracked automatically: each race follows its **lead**
  table's matchup (the first visible general-election table), set when its polls
  are committed. Override a race at `/us/matchups?chamber=senate` (or `house`):
  **set** a stored label, **ignore** the race's polls, or go back to **auto**. An
  automatic update never overwrites a manual override.

Both matchup pages have an optional "rebuild history" checkbox for their own
chamber, and **Run US Models** on the home page has one for all three (see
`--rebuild-history`).

### Model runs

**Run US Models** (home page) runs House → President → Senate → export. Its
**Rebuild all US history** box appends `--rebuild-history` to all three runners
(each under the long rebuild timeout), after a confirmation: one blocking request
that can take hours. The console runs one US model run at a time — ordinary runs,
rebuilds and the import queue's finish step alike — and refuses any other while
one is in progress, since each ends with an export of every chamber. (The
console's other full exports — site data, by-elections, Holyrood — are not
serialised with them.) The CLI equivalent, with the console's poll windows:

```bash
./election_data/bin/python models/us/run_us_house_model.py --since-days-back 60
./election_data/bin/python models/us/run_us_presidential_model.py --since-days-back 120
./election_data/bin/python models/us/run_us_senate_model.py --since-days-back 60
./election_data/bin/python scripts/export_elections.py
```

Each runner also takes `--dry-run`, `--as-of-date`, `--half-life-days`, and
`--start-date` + `--end-date` for a retrospective backfill. Seat polls are
**blended** with the national swing:
- `--seat-prior-weight K` (default `1.0`) — per party,
  `swing = α·(poll share − baseline) + (1 − α)·fallback`, with `α = W / (W + k)`
  and `W` the seat's in-window polls in its tracked matchup, weighted by recency
  and pollster weight. One fresh poll gives α = 0.5 and three give 0.75; `0`
  follows a seat's polls outright. A party missing from the seat's polls (other
  than "Others") is pulled toward 0. The fallback is the region/national swing,
  or the parent state's blended swing for `Maine CD-n` / `Nebraska CD-n`.
- `--ignore-seat-polls` — the pure uniform national swing (the old model).
- **As-of cap** — the run date is capped at the latest fieldwork end of the polls
  the run uses, which now **includes seat polls**: a race poll newer than the last
  generic-ballot update moves the cap forward. With `--ignore-seat-polls` only the
  national polls count.
- Each polled seat prints a `SEAT_POLL <seat> n= W= α= matchup=` line.

`--rebuild-history` first recomputes every date already in the trend series
(within the poll window, using the daily run's window rather than
`--lookback-days`), then does the normal run. Use it after anything that moves
the whole history: a new tracked matchup, a baseline override, or new Senate
seats. **Run it once for the Senate**, so its trend series moves from 33 to 35
seats. It picks its own range and its own as-of date, so combining it with
`--start-date`/`--end-date` or `--as-of-date`/`--as-of-days-back` is a usage
error (exit 2): a past as-of would delete every trend point above it and rebuild
only up to it. If the whole series lies outside the poll window, the points are
still dropped and the poll window `[first poll, as-of]` is rebuilt in their place.

### Senate specials (Ohio, Florida)

Specials are listed in `uselectionmaps/data/map-modes-shell.json`, map mode `"23"`,
under `senateSpecialElections`:

```json
{ "seat": "Ohio", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate" }
```

Entries whose `year` matches `parliamentFeatures.us_senate.nextElectionYear` join
the Senate model (35 seats instead of the 33 class-2 seats), using that baseline
election's votes for that seat only. The export copies the key into
`map-modes.json` (dropping past years), and the Senate Predict "Full Senate"
replaces that state's class-3 member. Edit the shell and re-export when the cycle
changes. A state with both a regular and a special race in one cycle cannot be
represented; the queue reports it as a page failure.

### CLI importers

The same scrape without the review step. **The default is a dry-run listing**;
`--commit` writes and applies automatic matchup tracking.

```bash
./election_data/bin/python polls/importers/us/us_house_generic_ballot_import.py   # house_national + house_districts
./election_data/bin/python polls/importers/us/us_senate_import.py
./election_data/bin/python polls/importers/us/us_presidential_import.py
./election_data/bin/python polls/importers/us/us_senate_import.py --state Michigan --commit
```

Options: `--contest SLUG` (repeatable), `--state STATE` (repeatable; name or postal
code), `--include-collapsed-for-uncovered`, `--commit`.

### One-off upgrade (existing database)

Run once, in order, on a database from before seat and matchup polls. Stop the
console first. Until step 2 the console, models and export cannot read polls from
that database.

1. **Back up** — `sqlite3 "$DATABASE_PATH" ".backup /home/philiph/dbs/elections.pre-us-seat-polls-<date>.db"`,
   plus the console's **Backup now**.
2. **Migrate** — adds `polls.matchup`, `polls.seat_id`, `poll_rows.candidate_name`
   and the `tracked_matchups` table. Idempotent, one transaction.
   ```bash
   ./election_data/bin/python scripts/migrate_add_us_poll_scope.py --dry-run
   ./election_data/bin/python scripts/migrate_add_us_poll_scope.py
   sqlite3 "$DATABASE_PATH" "PRAGMA table_info(polls);"   # matchup, seat_id listed
   ```
3. **Reset the legacy polls** — deletes the old matchup-averaged presidential polls
   and the Senate map's copy of the generic ballot (polls with no seat and no
   matchup on those two maps, with their rows), then the `*_us_senate` pollsters
   left without polls. House, UK and new-style polls are never touched. It refuses
   to run before step 2. Dry run by default (read-only); expect 22 presidential
   polls, 12 Senate polls and 6 pollsters. A second `--apply` finds nothing.
   ```bash
   ./election_data/bin/python scripts/reset_us_poll_matchups.py
   ./election_data/bin/python scripts/reset_us_poll_matchups.py --apply
   ```
4. **First import** — `/us/import` with all contests and a blank cutoff (about 530
   rows across 130 races); bulk-approve race by race. Leave "Run US models" unticked.
5. **Set the president matchup** at `/us/president/matchup` (e.g.
   `Vance (R) vs Newsom (D)`). Check the automatic Senate/House matchups at
   `/us/matchups`.
6. **Run the models with history rebuilt**, then export:
   ```bash
   ./election_data/bin/python models/us/run_us_house_model.py --since-days-back 60 --rebuild-history
   ./election_data/bin/python models/us/run_us_presidential_model.py --since-days-back 120 --rebuild-history
   ./election_data/bin/python models/us/run_us_senate_model.py --since-days-back 60 --rebuild-history
   ./election_data/bin/python scripts/export_elections.py
   ```
   Then `uselectionmaps/data/results/us-senate-forecast.json` should have 35 seats,
   including Ohio and Florida.

---

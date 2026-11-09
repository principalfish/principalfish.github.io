# Database

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

## Backups

The live database stays on local disk — SQLite must not run off a network or
synced mount. `data/backup.py` backs it up with SQLite's own backup API (safe mid-write) and
integrity-checks the copy:

- **Local archives** (`ELECTIONS_ARCHIVE_DIR`, default `~/dbs/elections`): a dated
  `elections-<UTC stamp>.db.gz` whenever the data has changed, plus a plain newest
  `elections.db`. The newest `ELECTIONS_BACKUP_KEEP` (default 30) are kept.
- **Off-machine (Backblaze B2)**: an hourly restic job outside the repo
  (`restic-backup.timer` → `~/.config/restic/backup.sh`) runs `backup_db.py` to
  refresh the plain newest copy, then sends `~/dbs/elections/elections.db` to B2
  (24 hourly, 30 daily, 12 weekly, 24 monthly snapshots). Google Drive was
  dropped on 2026-10-29.

The data console backs up by itself after any request that could write (including
the scripts it launches) and once on startup. Its **Backup now** button archives
immediately; **Restore newest archive** restores from the newest local archive,
keeping the replaced database as `<db>.prerestore`.

From a terminal in `data/` — after running a writing script directly, for example:

```bash
./election_data/bin/python scripts/backup_db.py            # archive if changed
./election_data/bin/python scripts/backup_db.py --dry-run  # show paths and state only
./election_data/bin/python scripts/backup_db.py restore    # restore (stop the console first)
```

From B2 (no local archives, or an older version): list versions with
`~/.config/restic/versions.sh elections`, then, with the console stopped:

```bash
set -a; . ~/.config/restic/env; set +a
restic dump <snapshot> /home/philiph/dbs/elections/elections.db > /tmp/elections.restored.db
sqlite3 /tmp/elections.restored.db "PRAGMA integrity_check"
# move ~/dbs/elections.db and any -wal/-shm aside, then copy the restored file in
```

A backup of the full database takes about 20 s (most of it gzip and the integrity
check); the archive is about 136 MB.

## Rebuild the database from source data

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

The rebuild bootstraps US electoral-vote allocations immediately after importing
parties, before historical imports and export. It passes legacy forecast target
2028; review and classify legacy presidential runs before using it on an existing
database. A failed bootstrap is reported in the per-step summary, so check the
summary before treating the rebuilt outputs as complete.

### Underlying importers

The orchestrator chains these (all under `importers/`, run from `data/`);
you can also run any one directly with `--refresh`:

```bash
./election_data/bin/python importers/seed/import_parties.py
./election_data/bin/python importers/seed/migrate_us_electoral_votes.py \
  --legacy-forecast-target-year 2028
./election_data/bin/python importers/results/westminster/import_topojson.py --refresh
./election_data/bin/python importers/seed/import_region_populations.py \
	--map-name "UK Constituencies post 2022" \
	--input inputs/westminster/region_populations.csv
./election_data/bin/python importers/results/westminster/import_general_elections.py --refresh
./election_data/bin/python importers/results/holyrood/import_holyrood_seats.py --refresh
./election_data/bin/python importers/results/holyrood/import_holyrood_elections.py --refresh
./election_data/bin/python importers/results/us/import_house_elections.py \
	--file inputs/us/house-2024.json --year 2024 \
	--name "2024 US House Election" --refresh      # + senate / presidential per file
./election_data/bin/python importers/results/westminster/by_election_import.py \
	--url <wikipedia-url> --refresh                # one per line in
	                                               # inputs/westminster/by_elections.txt
```

Seat boundary geometry is **not** stored in the database — the site renders from
the committed `electionmaps/data/maps/map-*.topo.json`. `import_topojson.py`
(Westminster) and `import_holyrood_seats.py` create seats from those committed
TopoJSON files; no PostGIS is required.

### Presidential electoral-vote allocations

Database tables `us_electoral_vote_eras` and `us_electoral_vote_allocations`
are authoritative for presidential imports, forecasts, results, trends and
console comparisons. Actual elections select allocations by `elections.year`;
forecasts use the required `elections.target_election_year`. A forecast's
`year`, `election_date` and dated name still identify its **as-of run**, not its
target cycle. Current and baseline comparisons resolve their own allocations.
The old `seats.electoral_votes` column is no longer authoritative. Converter JSON
weights are descriptive only: the retired offline converters (now in `archive/`) used
the same bounded bootstrap dataset, independently of later operator edits in the database.

For an existing database, stop writing processes, confirm the configured file,
back it up ([Backups](#backups)), then inspect migration's read-only report. Run these
commands from `data/`, stopping on failure between stages:

```bash
EV_DB=$(./election_data/bin/python -c \
  'from config import DatabaseConfig; print(DatabaseConfig.from_env().database_path)')
printf '%s\n' "$EV_DB"
./election_data/bin/python scripts/backup_db.py
./election_data/bin/python importers/seed/migrate_us_electoral_votes.py --dry-run
```

Migration opens the configured **existing** file; it never creates a missing
database. `.env` overrides shell environment values, so use the printed path
and correct `.env` before proceeding rather than assuming an inline
`DATABASE_PATH=...` selects another file. The report shows legacy NULL-target
forecast counts, map IDs and run-date ranges, plus allocation and map-label
changes. Only when all reported legacy forecasts belong to the current 2028
pipeline, apply:

```bash
./election_data/bin/python importers/seed/migrate_us_electoral_votes.py \
  --dry-run --legacy-forecast-target-year 2028
./election_data/bin/python importers/seed/migrate_us_electoral_votes.py \
  --legacy-forecast-target-year 2028
```

If legacy runs mix target cycles, do not assign 2028 to them all. First classify
them from their provenance, not their as-of dates. In a backed-up database,
explicitly add `target_election_year INTEGER` to `elections` if absent and fill
the classified rows' targets in a transaction. Review any remaining NULL rows
before passing one target for that remaining group; omit the flag when none
remain. Migration never overwrites non-NULL targets. Reruns insert only missing
allocation rows and preserve edited weights. It also normalizes legacy
`maps.parliament = 'us_president'` to the sole canonical `us_presidential`;
there is no runtime alias. Pollster IDs ending in `_us_president` are unrelated.

For a fresh database, the party import creates the ORM schema, then the migration
seeds all seven eras (56 tally units and 538 EVs per era) before any presidential
import or forecast. The two commands appear in the underlying-importer sequence
above; the full rebuild runs them automatically. Neither consumers nor
`create_tables()` seed allocations on demand.

The presidential runner defaults to target **2028**, matching its current poll
input. Its presidential-only `--target-election-year` override changes allocation
selection, not the poll importer's cycle or tracked matchup. For example:

```bash
./election_data/bin/python models/us/run_us_presidential_model.py \
  --since-days-back 120 --target-election-year 2028 --dry-run
```

The target is retained through date caps, retrospective runs and history
rebuilds. Retargeting replaces the same map/type/as-of result rather than storing
two cycles for one date. A history rebuild uses the requested target throughout
its recomputed range; ordinary gap filling preserves unrelated stored targets.

Seeded finite intervals end at 2028. An unsupported year, overlapping era or
missing unit fails instead of inheriting the newest weights. To add a future
era, obtain verified official allocations, then transactionally insert a unique
`census_year`, inclusive `first_election_year`/`last_election_year` in
`us_electoral_vote_eras` and all 56 `(era_year, unit_name, electoral_votes)` rows
in `us_electoral_vote_allocations`. Match existing unit names exactly, including
DC, Maine/Nebraska statewide units and their five districts; require positive
integer weights, total 538, and no overlap with existing intervals. Do not clone
the latest weights or extend its range without verified allocations. Before
committing, check the full era's count and sum, for example for census 2030:

```sql
SELECT COUNT(*), SUM(electoral_votes)
FROM us_electoral_vote_allocations WHERE era_year = 2030;
-- Expected: 56, 538; also verify the interval and every unit/weight.
```

Database-added eras work for application readers; supporting them in offline
converters or future fresh rebuilds also requires updating the single canonical
bootstrap dataset in `data/importers/seed/us_electoral_votes.py` and its
bounded interval metadata.

After migration or a weight correction, republish old EV totals without
recalculating popular votes or model winners. First resolve the intended
presidential map (normally 22) and inspect its stored forecast targets:

```bash
sqlite3 -readonly "$EV_DB" \
  "SELECT id, name, parliament FROM maps WHERE parliament = 'us_presidential';
   SELECT map_id, name, year, target_election_year FROM elections
   WHERE type = 'us_presidential_model' ORDER BY map_id, election_date;"
./election_data/bin/python scripts/rebuild_model_trends.py \
  --database "$EV_DB" --model us-president --map-id <verified-map-id> --dry-run
./election_data/bin/python scripts/rebuild_model_trends.py \
  --database "$EV_DB" --model us-president --map-id <verified-map-id>
./election_data/bin/python scripts/export_elections.py --dry-run
./election_data/bin/python scripts/export_elections.py
```

Replace the map placeholder and repeat cache repair for each affected map before
export. These are operator actions that write derived site files. Existing
historical JSON files need not be reconverted or reimported just to repair EVs;
recompute projections only when their underlying votes or winners need to change.

# Supabase Migration + SQLite Archive

## Context

The project currently relies on a local Docker PostgreSQL instance. To work from multiple machines, the database needs to be hosted remotely. Supabase (hosted PostgreSQL) is the chosen target. Because model simulation runs accumulate significantly (5.2M+ votes) and only recent runs are needed live, we'll keep a 30-day rolling window of model runs in Supabase and archive older ones to a local SQLite file.

The `model_uns.db` file was deleted, so we're building the archive from scratch.

---

## Phases (in build + execution order)

### Phase 1: Update config for Supabase

File: `data/config.py`

- Add support for `DATABASE_URL` env var (Supabase connection string) that overrides the default localhost connection
- Supabase connection strings are standard PostgreSQL URIs; SQLAlchemy picks them up without further code changes

### Phase 2: Build SQLite archive for old model runs

New file: `data/scripts/archive_old_model_runs.py`

- Accepts `--pg-url` (defaults to `DATABASE_URL` / local config) so it can target local PostgreSQL before migration
- Finds all `type = 'model_uns'` elections with `election_date < (today - 30 days)`
- Creates/opens `data/model_uns.db` SQLite with schema:
  ```sql
  CREATE TABLE elections (id, map_id, year, name, election_date)
  CREATE TABLE votes (id, election_id, seat_id, party_id, candidate_name, vote_total, elected)
  ```
- Copies those elections + votes from PostgreSQL into SQLite
- Deletes them from PostgreSQL
- Run manually or as a periodic maintenance step

### Phase 3: Run archive against local PostgreSQL (pre-migration)

- Run `archive_old_model_runs.py` pointed at the local Docker DB
- Moves old model runs to `model_uns.db`, slimming down the local DB before export

### Phase 4: Migrate to Supabase (manual)

1. Create a Supabase project at supabase.com and enable PostGIS extension (required for `seats.geom`) under Extensions
2. Retrieve the connection string from Supabase dashboard (Settings > Database > Connection string, "URI" mode)
3. **Migrate schema + data** using `data/scripts/migrate_to_supabase.sh`:
   - Schema: `pg_dump --schema-only` from local Docker, `psql` into Supabase
   - Data: `pg_dump --data-only` of the now-slimmed local DB into Supabase
4. Set `DATABASE_URL` env var and verify Flask server connects

### Phase 5: Update trend cache backfill to read from SQLite + Supabase

File: `data/models/uns/backfill_model_output_trends.py`

- Currently reads only from PostgreSQL
- Add `--include-sqlite` flag (default on) that also reads from `data/model_uns.db` when rebuilding the full trend cache
- Allows full historical trend reconstruction even after archiving

### Phase 6: Model run scripts

Files: `data/models/uns/run_uns_model.py`, `data/models/uns/run_retrospective_uns.py`

- No write logic changes needed — they already write to PostgreSQL, which will now be Supabase
- Optionally trigger `archive_old_model_runs.py` after a run to keep the 30-day window clean (can defer to manual initially)

---

## Files to Modify

| File | Change |
|------|--------|
| `data/config.py` | Add `DATABASE_URL` env var support |
| `data/models/uns/backfill_model_output_trends.py` | Add SQLite source support |

## New Files

| File | Purpose |
|------|---------|
| `data/scripts/archive_old_model_runs.py` | Archive model runs > 30 days old from Supabase to SQLite |
| `data/scripts/migrate_to_supabase.sh` | One-time migration script (schema + data dump/restore) |

---

## Migration Order

1. **Regenerate historical model runs** — the local DB currently has no `model_uns` elections; run the retrospective to rebuild them:
   ```bash
   cd data && source election_data/bin/activate
   python models/uns/run_retrospective_uns.py \
     --map-name "UK Constituencies post 2022" \
     --baseline-election-name "2024 General Election" \
     --start-date 2024-07-05 \
     --end-date 2026-03-21
   ```
2. Run `archive_old_model_runs.py` against **local PostgreSQL** to move runs older than 30 days to `model_uns.db`:
   ```bash
   python data/scripts/archive_old_model_runs.py --dry-run  # preview first
   python data/scripts/archive_old_model_runs.py
   ```
3. Run `migrate_to_supabase.sh` to export the now-slimmed local DB and import into Supabase:
   ```bash
   export SUPABASE_DB_URL="$(grep SUPABASE_DB_URL .env | cut -d= -f2)"
   ./data/scripts/migrate_to_supabase.sh
   ```
4. Add `DATABASE_URL` to `.env` (same value as `SUPABASE_DB_URL`), verify Flask server connects
5. Test: run a new UNS model run, verify it lands in Supabase
6. Test: rebuild trend cache — should pull from both SQLite (archived) + Supabase (recent):
   ```bash
   python data/models/uns/backfill_model_output_trends.py
   ```

---

## Verification

- Connect via Flask server, load election maps frontend
- Run a fresh UNS model run, check it appears in Supabase elections table
- Confirm `model_uns.db` has archived elections after running archive script
- Rebuild trend CSV and check historical data is present

---

## Implementation status

| Item | Status |
|------|--------|
| `data/config.py` — `DATABASE_URL` support | Done |
| `data/scripts/archive_old_model_runs.py` | Done |
| `data/scripts/migrate_to_supabase.sh` | Done |
| `data/models/uns/backfill_model_output_trends.py` — SQLite support | Done |

## Notes

- **PostGIS**: Must be enabled in Supabase dashboard under Extensions before schema import
- **Supabase free tier**: 500MB database limit — the 30-day rolling window should stay well under this
- **Trend cache**: `model_output_trends.csv` is a local file used by the frontend. Running exports from a second machine requires the CSV to be present locally (or committed to the repo)
- **Poll imports from remote**: Once config points to Supabase, poll imports work unchanged from any machine with `DATABASE_URL` set
- **backfill `--no-sqlite`**: The old `--include-sqlite` flag defaulting to True means SQLite is always checked; pass `--no-include-sqlite` to skip it

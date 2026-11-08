# US results from NYT (House and Senate, 2018-2026)

One toolchain, `data/scripts/us_results/`, fetches the New York Times / AP results for
every US House and Senate general election from 2018 to 2026, converts them to the
committed election JSON the importers already read, and loads them into the database.
The House 2022-24 and Senate 2020-24 files replace the FiveThirtyEight data (election ids are
kept); House 2018 and 2020 and Senate 2018 are new.

- **Source**: NYT's static results JSON on its public CDN only (never the HTML pages,
  which sit behind a captcha). The URL table, schema adapters (A: 2024-26, A2: 2022,
  B: 2020, C: 2018) and the runoff files (GA Senate 2021 and 2022, MS Senate 2018, LA-05 2020) are
  in `scripts/us_results/sources.py` and `adapters.py`. LA-05 2026 has no runoff file yet
  (its December runoff); until then the seat stays uncalled on its November first round.
- **Credit**: an election imported from NYT carries a source note ("Results: The New York
  Times / AP. Provisional until certified." while the newest cycle has uncalled or
  uncertified races), shown as a line under the map. It is not in seat popups.
- **Cache**: downloaded files are kept outside git, in `NYT_CACHE_DIR` (default
  `/home/philiph/dbs/nyt-cache`, see `.env_example`), as `<year>/<chamber>-<kind>-<UTC
  timestamp>.json` plus a `-latest` pointer. Only the converted election JSON is committed.

## Commands

From `data/`, once per cycle and chamber. `fetch` downloads, `convert` writes
`inputs/us/{house,senate}-<year>.json` (plus `senate-<year>-special.json` when a
special shares a state with its regular race), `import` loads the committed files, `all`
does the three. Every run prints a diff against the committed file and the database
(winner changes, newly called seats, % counted, the largest vote changes); against the 538
files for 2020-2024 it doubles as a check of the NYT data.

```bash
./election_data/bin/python scripts/us_results/cli.py all --year 2026 --chamber house --dry-run   # writes nothing
./election_data/bin/python scripts/us_results/cli.py all --year 2026 --chamber house --refresh   # update in place, ids kept
./election_data/bin/python scripts/us_results/cli.py all --year 2026 --chamber senate --offline --refresh
```

Options: `--refresh` (update existing elections in place), `--dry-run` (no cache, file or
database writes), `--offline` (`fetch` and `all`: use the newest cached file instead of
downloading; `convert` always reads the cache), `--cache-dir`, `--out-dir`.
Importing never exports: run `scripts/export_elections.py` afterwards. `scripts/rebuild_database.py`
replays the committed `house-*`/`senate-*` files (specials right after their regular year).

The data console has the same flow at `/us/results` (the **Import US Results (NYT)** button on
the home page): choose year and chamber, preview every number (files and times, a row per
race, the party mapping, warnings, the diff), confirm to import (existing elections are refreshed in place, as with `--refresh`), then press the separate
**Export to site** button on the result page.
**Rebuild Current Senate** (see [Current Senate snapshot](../../uselectionmaps/README.md#current-senate-snapshot)) is a third, separate
button.

**Refresh cadence**: manual. While counts finish, re-run the import for the cycle (`all
... --refresh`); each run replaces that election's votes and statuses in place. Re-export
when you want the site to update. After the December LA-05 runoff is published, add its URL
to `sources.py` and re-run House 2026.

## Maps

The Senate uses map **23** for every year. District sets for the House, one map id each: **21** (cb_2024 cd119) for 2022 and 2024, **24** for 2026,
**25** (cb_2018 cd116) for 2018 and 2020. Map 24 is map 21 with the nine states that were
redrawn (AL, CA, FL, LA, NC, OH, TN, TX, UT) replaced by the Census 120th-Congress TIGER/Line
files; Missouri passed a new plan but 2026 ran on the 2024 one, so it is not replaced. Known
inaccuracy: North Carolina's 2019 court plan (used in 2020) is in no Census file, so NC 2020 is
drawn on the 2018 boundaries. The per-state source table is
`old_data/scripts/usa/house_2026_sources.py`; when Census publishes cartographic 120th-Congress
files, drop `REDRAWN_2026` there and rebuild.

Rebuild a map (needs `ogr2ogr`/GDAL and `mapshaper` on the PATH; downloads about 100 MB of
Census files into the scratch folder, once):

```bash
./election_data/bin/python old_data/scripts/usa/build_house_topojson.py --map 24 --fetch /tmp/house-maps \
  --out ../uselectionmaps/data/maps/map-24.topo.json                 # --map 25 for the 2012-cycle set
```

To check a map against an election, `old_data/scripts/usa/check_redistricting.py` scores it
against NYT's county results; give it the old map too and it lists the states whose score
drops, i.e. the redrawn ones:

```bash
./election_data/bin/python old_data/scripts/usa/check_redistricting.py \
  --nyt <nyt-cache>/2026/house-general-<timestamp>.json \
  --districts ../uselectionmaps/data/maps/map-24.topo.json \
  --old ../uselectionmaps/data/maps/map-21.topo.json \
  --counties cb_2024_us_county_500k/cb_2024_us_county_500k.shp   # Census county shapefile
```

The importer refuses to refresh an election onto a different map, or to reuse a map id
that belongs to another parliament; confirm ids 24 and 25 are free in the target database
before the first import.

## Live runbook (existing database)

Run in this order. Each step writes to the live database or the site data, so back up first
and take them one at a time.

1. **Back up** — `./election_data/bin/python scripts/backup_db.py` (and the console's
   **Backup now**). Stop the console and any server that holds the database.
2. **Migrate** — adds `election_seat_status` and `elections.source_note`. Idempotent, one
   transaction. Until it has run, the console, export and importers on this code fail on any
   `elections` query.
   ```bash
   ./election_data/bin/python scripts/migrate_add_us_result_status.py --dry-run
   ./election_data/bin/python scripts/migrate_add_us_result_status.py
   ```
3. **Import**, dry run first, for 2018, 2020, 2022, 2024 and 2026 and both chambers. Read the
   diff for House 2022-24 and Senate 2020-24 against the 538 data: no winner should change (known differences: NYT
   has Alaska's 2022 Senate RCV final round and Osborn as an independent in Nebraska 2024).
   ```bash
   for year in 2018 2020 2022 2024 2026; do for chamber in house senate; do
     ./election_data/bin/python scripts/us_results/cli.py all --year $year --chamber $chamber --refresh --dry-run
   done; done
   # then without --dry-run, one cycle at a time
   ```
4. **Current Senate** — needs the converted `senate-2026.json` (and its specials file if there
   is one) from step 3; it reads the files, not the database. `current-senate --dry-run` prints the
   holdover, winner and uncalled counts; then write it.
   ```bash
   ./election_data/bin/python scripts/us_results/cli.py current-senate --dry-run
   ./election_data/bin/python scripts/us_results/cli.py current-senate
   ```
   `EXPECTED_SPECIALS_2026` in `scripts/us_results/current_senate.py` is hard-coded to the
   Florida and Ohio Class-3 specials of 2026 and the build refuses without them: edit it at
   the next cycle that has Senate specials.
5. **Export** — `scripts/export_elections.py --dry-run` to see the plan (the maps 24 and 25
   must exist), then the real run. This regenerates `map-modes.json` from the shell (mapModes
   24 and 25, the 2028 predict rollover) and writes the result files, including the new
   per-seat status fields and `sourceNote`.
6. **Check** in a browser (`./server.sh`): the uncalled seats are hatched or neutral, the
   source line shows under the map, and the Predict views open on the 2026 result.

## 2028 model run: before you start

The predict views already roll over to 2028, but the models were not re-run. Before the first
2028 House or Senate run:

- Bump `US_MODEL_COMPARISON_IDS` in `scripts/export/manifest.py` to `2026-us-house` and
  `2022-us-senate`. It is keyed by election type, not by cycle, so it still points at 2024 and
  2020 and the new forecasts would compare against the wrong results. The bump also moves the
  baselines of the archived 2026 forecasts, so archive them first (last item).
- The House runner now writes on map 24 (it was map 21), so its trend publish rebuilds
  `us-house-trends.json` from map 24 only and drops the archived 2026 House trend series. Keep
  a copy of the file, or archive the series, first.
- House district polls are stored on map-21 seats, so on map 24 the House model sees no
  district polls (the national generic ballot is unaffected). Repoint the poll importers to
  the new map before the 2028 House run.
- Set `EXPORT_US_CHAMBER_FORECASTS = True` in `scripts/export_elections.py`. It is off, so the
  House and Senate forecasts are not exported at all until it is switched back on.
- Every House and Senate forecast is exported as `current-us-house` / `current-us-senate` and
  labelled "2026 Prediction" (`scripts/export/naming.py`, `manifest_name_for_election`), so a
  2028 run would collide with the 2026 one; bump the label with the cycle. Archive the 2026 forecast under its own id first; the
  archived forecast also needs its own Florida/Ohio baselines and chamber (currently it shows
  "Comparison unavailable" in Seats up, and its Full Senate merges into the current chamber).

# principalfish.github.io

The GitHub Pages static site for **principalfish.co.uk**, plus the Python data
pipeline that powers its interactive UK election maps.

## Repository structure

| Path | What it is |
|------|------------|
| `index.html`, `404.html`, `CNAME` | Landing page, error page, GitHub Pages custom domain |
| `site/` | Shared frontend assets — styles, top bar, Google Analytics, vendored `d3`/`topojson`/`leaflet` bundles |
| `bio/` | Static bio page |
| `electionmapslogic/` | Shared election-maps engine (D3 + TopoJSON): core modules (`state`/`dom`/`utils`/`files`/`app`), `features/` (predict + poll tracker, opt-in per page), shared app markup (`shell.html` + opt-in `fragments/`, injected per page by `shell-loader.js`), `maps.css`, `mobile-sidebar`, `tests/` = Vitest specs |
| `electionmaps/` | UK election-maps page shell — thin `index.html` (header + shell mount; loads fragments `postcode`/`referendum-info`/`polltracker`), the `electionmaps.js` entry (bundled to `electionmaps.min.js`), and `data/` = UK exported maps/results + `map-modes(-shell).json` |
| `uselectionmaps/` | US election-maps page shell — thin `index.html` (header + shell mount; no fragments), the `uselectionmaps.js` entry (bundled to `uselectionmaps.min.js`), and `data/` = US exported maps/results + `map-modes(-shell).json` |
| `guesstheyear/` | "Guess the year" game — static frontend + Python helpers (`app.py`, `wiki.py`, `export.py`) |
| `referdle-solver/` | Referdle solver — static frontend (`js/`, `css/`, `data/`) |
| `routeweather/` | Route weather — static page (`js/`, `css/`, `tests/` = Vitest specs): import a GPX and see the forecast along the route at each point's arrival time and altitude. See [Route weather](#route-weather) |
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

## Route weather

`/routeweather/` takes a GPX file, an activity speed and a start time up to 7
days ahead, and shows the weather you would meet at each point of the route
when you get there. Everything runs in the browser; there is no build step
(native ES modules) and no API key.

**Data sources**
- Forecast: [Open-Meteo](https://open-meteo.com/) `models=ukmo_seamless` (Met
  Office UKV 2 km over the UK and Ireland, Met Office Global 10 km elsewhere),
  CC-BY 4.0, non-commercial free tier. Only the sampled point coordinates leave
  the browser.
- Elevation: the GPX's own `<ele>` values; Open-Meteo's elevation API only when
  the file has none.
- Map: [OpenTopoMap](https://opentopomap.org/) tiles (CC-BY-SA, OSM data) on
  Leaflet 1.9.4, vendored with `npm run vendor:leaflet`.

**How it works**
- The forecast is requested from now to 7 days ahead. Open-Meteo returns
  nulls past the model's last step (the Met Office global model's run started
  hours earlier, so its 7 days end a little sooner), and the page uses the
  last hour with data everywhere as the forecast's end: the status line says
  when that is, and the latest valid start follows it.
- The route is sampled at up to 150 points (about every 1 km, plus every
  summit and valley with at least 30 m of prominence), and every sample is
  fetched once per GPX load. Speed, start and units changes only recompute.
- Requests use `elevation=nan`, so each location comes back with its model
  grid cell's own height. Temperature and wind are then interpolated through
  the model's vertical profile — the 2 m / 10 m values at cell height plus the
  1000/925/850/700 hPa levels at their geopotential heights — to the walker's
  GPX altitude. Below the lowest profile point temperature follows a 6.5 K/km
  lapse rate; above 700 hPa it clamps. Gusts at altitude are the altitude wind
  plus the 10 m gust's excess over the 10 m mean wind.
- The wind chart shows wind speed and gusts at your altitude; wind relative
  to the direction of travel (headwind and crosswind) is in the readout and
  table. The summary gives the coldest temperature, the coldest feels-like and
  the strongest wind on the route, with where and when.
- Sky: cloud cover and sunshine per hour, shown as a "Sky" chart strip, in
  the readout and table, and as a route total ("~X h of Y h daylight").
  `ukmo_seamless` only has `sunshine_duration` for about the first 2¾ days
  (the UKV range); after that it is estimated as the hour's daylight minutes
  × (1 − cloud cover) and labelled "est.".
- Sunrise and sunset are computed in the browser with NOAA's solar position
  algorithm (no API call), at the walker's position and arrival time. The
  summary gives the start day's times at the start point and says when the
  route starts before sunrise or runs past sunset; the charts shade the dark
  stretches and mark ☀↑ / ☀↓ where you pass them; the readout says whether
  it is light and how long until the next change. They are for a flat
  horizon: terrain can hide the sun for longer.
- Feels like: Steadman's apparent temperature without radiation, as the
  Australian Bureau of Meteorology uses it: AT = T + 0.33·e − 0.70·v − 4.00,
  with T the altitude-corrected temperature (°C), v the altitude-corrected
  wind (m/s) and e = (RH/100)·6.105·exp(17.27·T/(237.7+T)) hPa from the
  model's 2 m relative humidity. Shown in the table, the readout, a dotted line
  on the temperature chart and the summary (coldest on the route). Its fixed
  −4 °C term means calm, dry air already "feels" 4 °C colder than it is, and
  it only reads warmer than the air when it is humid and still; it ignores
  sunshine.
- Units: Metric / Imperial is the preset (distance, elevation, speed);
  "Customise units" overrides wind (km/h | mph), rain (mm | in) and
  temperature (°C | °F) one by one, and picking a preset resets them. The URL
  carries `wind`, `rain` and `temp` only when they differ from the preset.
- Times: once a route loads, one extra `timezone=auto` request for its first
  point gives the route's IANA time zone, and every time on the page — the
  start input included — is shown in it (labelled with the zone name). Until
  then, or if that lookup fails, times are in the browser's zone. The URL's
  `start` is always UTC. A wall time that happens twice when clocks go back
  reads as the earlier one; one skipped when clocks go forward moves on by the
  gap.

**Limits of the altitude adjustment.** It is linear interpolation between a
handful of heights, so it captures broad inversions and stronger winds aloft but
not local effects: valley cold pools below the model terrain, wind acceleration
over a ridge, or sheltering in a corrie. Beyond about 2 days the forecast comes
from the 10 km global model, whose terrain is much smoother. The profile tops
out at the 700 hPa level (about 3,000 m): above it, temperature and wind are
held at that level's values, and the page says so (a summary note, a "Held"
badge on the table row and a note in the readout).

**API budget.** A full 150-point route costs about 390 of Open-Meteo's weighted
calls (26 hourly variables count as 2.6 calls per location), plus 1 for the
time zone lookup. The free tier allows
600 a minute and 10,000 a day per IP. Open-Meteo sends no rate-limit headers, so
the page prints an estimate of this browser's usage to the console after each
load (`routeweather/js/usage.js`).

**Tests:** `npm test` runs the `routeweather/tests/` Vitest specs (pure modules
in Node, page/map/charts under jsdom). No browser is needed.

## Referdle solver correctness and timing benchmark

Requires Node.js 22 or later and the existing npm development dependencies.
Fast harness tests run with `npm test`. The separate benchmark uses 100 frozen
games spread across days #1000–1414, with pool probes first and expanded probes
second (200 cases), through the application's actual daily controller and the
production Worker client/handler using native Node `worker_threads`. The default
backend is `worker`; a single Worker owns and decodes the matrix once, then
reuses it across the sequential cases. Logical hashes and frozen-answer checks
are computed inside that Worker; the host does not load another decoded matrix.

The full reference archive is local and ignored by Git. On a fresh checkout,
generate it from the current solver before running full verification:

```bash
npm run benchmark:referdle -- --record
```

This records all 100 games in both modes (200 traces) and their current timings.
It takes roughly ten minutes on the development machine; other machines vary.
Recording refuses to replace an existing archive. Keep a baseline unchanged
while checking later solver changes; regenerating it records the current
version, not the original unoptimized timing baseline.

From the repository root, verify against the local reference:

```bash
npm run benchmark:referdle
npm run benchmark:referdle -- --days 1000,1414 --report /tmp/referdle-report.json
npm run benchmark:referdle -- --backend direct --days 1000,1414 --report /tmp/referdle-direct.json
```

The explicit `direct` backend is for focused compatibility checks; it does not
validate native Worker transport and has no message roundtrip measurements.
Worker benchmarks fail rather than silently substituting a direct backend.
Both backends close their resources on success or failure.

Verification never creates or replaces a reference. Missing/corrupt references,
changed logical inputs/strategy, incomplete games and any exact trace mismatch
exit nonzero. Traces compare each move, board, feedback, candidate array/order,
ranking and score, plus the actual final board and outcome. Scores retain their
full precision; missing fields, undefined, null and negative zero remain distinct.

Initial recording and deliberate replacement are explicit actions:

```bash
npm run benchmark:referdle -- --record
npm run benchmark:referdle -- --record --overwrite
```

The complete local baseline lives in `referdle-solver/benchmarks/references/`, with one
compressed trace per case under `pool/` and `expanded/`, plus
`reference-manifest.json` and `timing-baseline.json`. Recording stages all cases
before publishing the directory; a failed run preserves an existing baseline.
The initial archive has 200 compressed traces and two metadata files, totaling
36,606,099 bytes (36.61 MB). Initial recording took 35m49.4s; an independent
36m16.5s verification reproduced all 200 traces exactly. The existing local
archive is preserved; these files are no longer tracked or shipped with the
site. The production matrix under `referdle-solver/data/` remains tracked.
Three fast-suite integration checks use the local archive when present and are
explicitly skipped without it. The remaining unit and Worker transport tests
run in fresh checkouts and CI without generating an archive.
Replacement refuses directories containing unrelated files. A writer lock
prevents simultaneous recording; after an interrupted process, confirm it has
stopped before manually removing the sibling `.references.lock` (or the matching
lock for a custom baseline directory).

For an isolated two-case smoke recording and verification:

```bash
npm run benchmark:referdle -- --record --days 1000 --baseline-dir /tmp/referdle-smoke
npm run benchmark:referdle -- --days 1000 --baseline-dir /tmp/referdle-smoke
```

Turn a saved JSON report into a standalone HTML report without rerunning any games:

```bash
npm run report:referdle -- /tmp/referdle-report.json --output /tmp/referdle-report.html
```

The offline report plots per-game computation seconds with shared duration bands
and count scales for pool/expanded modes and baseline/current series, alongside
duration means. Worker reports additionally plot advance-job roundtrip seconds
and show roundtrip minus compute for the cases with both measurements. This
overhead includes benchmark evidence cloning/transfer and is not pure messaging
latency. Older/direct reports leave unavailable roundtrip values explicit; no
historical roundtrip baseline is inferred. It also plots total played moves, including closing moves, and
their means; baseline move counts are unavailable in timing rows. Comparison
means/deltas use the same cases with both finite timings and show the paired
count. Missing measurements are excluded, and partial/failed runs are labeled
prominently. Record reports have no baseline series. With the initial maximum
34.4s duration, the report uses nine 4-second bands covering 0–36s. The HTML
includes its SVG charts and styles, uses no network or scripts, and must be
written outside the repository/baseline, separately from the input JSON.

Partial recording requires an external baseline directory. Selected days must
belong to the frozen sample, and both probe modes always run. Optional report
files must be outside the repository and baseline; their parent directory must
already exist. Incomplete games retain compressed trace evidence in a temporary
directory, whose path appears in the report.

Progress goes to stderr and the JSON report to stdout (npm also prints its command
banner; invoke `node referdle-solver/benchmarks/runner.mjs` directly for pure JSON).
Reports include per-case times/deltas, per-mode and overall computation totals,
medians, slowest cases, separate Worker roundtrip totals/means/medians and paired
transport overhead, setup/elapsed wall time and total reference bytes. Runtime
footprint records unminified component HTML/CSS/JavaScript and shipped data file
bytes separately from decoded matrix bytes. It excludes references and shared
site assets; these file counts are not an initial network-transfer measurement. Source
and raw asset hashes are provenance; logical matrix hashes accept a different
storage type or compression when every pattern code remains identical. Changes
to Node/platform/CPU produce a timing comparability note instead of failing
correctness.

Compute time sums synchronous computation-service intervals for daily advances
inside the Worker (or the explicit direct service). It excludes Promise waiting,
asset loading/decoding, bundling, controller publication/rendering, animation
waits, trace normalization/encoding, transfer, compression and comparison.
Roundtrip time sums each advance job from host send through accepted reply;
initialization and day loading remain outside those totals in setup/wall time.
Render helpers are capture/no-op shims; these timings do not measure browser
rendering. The original baseline included synchronous `nextTurn` controller and
publication work, so CPU percentage comparisons across that boundary are
informative rather than identical-boundary measurements. Original timings are
never erased or rebased. Each run is one sequential pass without warmup; early
cases include JIT startup, and host load can change timings. Performance deltas
have no pass/fail speed threshold; failed correctness runs are invalid for
performance acceptance.

The original 200-case compute total was 35m41.3s. Recorded optimization passes
were 12m59.9s after pattern-block reuse, 12m06.0s after lossless uint8 storage,
10m34.1s after daily-result reuse and 9m22s after shared suggestion analysis.
The engine extraction pass was 9m25s. These are individual passes, not repeated
statistical estimates; each reproduced all 200 complete traces exactly.

### Referdle Worker behavior and browser checks

The browser uses one dedicated module Worker, shared by daily and manual mode.
The Worker owns the decoded matrix; the page receives lightweight words,
results and accepted continuation state. Day/mode/probe/reset and manual-input
controls remain usable while calculations run. Changing inputs invalidates
obsolete replies and physically terminates active obsolete work. An idle Worker
is reused; after cancellation it restarts lazily from accepted continuation or
manual input without replaying all prior guesses. Manual typing does not trigger
unsolicited analysis; Suggest and selected replay steps request computation.

If a Worker cannot start, the page visibly reports compatibility mode and uses
the direct service after disposing the failed Worker. That mode retains solver
functionality but performs calculations on the page. Missing/corrupt assets stay
explicit errors. Runtime/message errors preserve accepted state and permit an
explicit retry; failed day loading has a Reload day control.

Serve with `./server.sh` and open `/referdle-solver/`. Browser acceptance includes
both probe modes, a slow daily solve with responsive controls, interruption by
mode/day/probe/reset changes, manual Suggest, clue-only input, lazy scrubbing,
and failure/retry. Native Worker tests verify exact computation/transport and
host-event-loop progress, but cannot establish browser module URLs, rendering
or frame/input responsiveness. Expanded words are currently loaded at startup.

An isolated headless Edge check on 9 October 2026 passed both daily probe modes,
slow-solve frame/control responsiveness, cancellation on probe/day/mode changes,
reset, manual clue-only Suggest, lazy replay and rapid scrubbing, on-demand
manual edits, and retry after a simulated Worker error event. The page produced
no unhandled JavaScript errors during these checks; the error event was injected
to exercise recovery rather than an actual engine crash.

The full native Worker verification on 9 October 2026 matched all 200 frozen
traces. Synchronous service computation totaled 556.55 seconds (9m17s), and
advance-job roundtrips totaled 559.52 seconds (9m20s): 2.97 seconds of paired
transport/job overhead, averaging 14.85 ms per game. Mean computation was 2.604
seconds with pool probes and 2.962 seconds with expanded probes. The run took
567.01 seconds overall, including setup and trace comparisons. These are one
sequential pass's measurements; the original baseline also included controller
publication in its compute interval, so comparisons across that boundary remain
informative rather than an exact CPU speedup claim.

The current matrix is 7,491,562 gzip bytes and 16,378,209 decoded bytes, compared
with 8,857,127 gzip bytes and 32,756,418 decoded bytes before uint8 conversion.
At that measurement, unminified component HTML/CSS, JavaScript and shipped data totaled
7,804,246 file bytes, excluding the benchmark archive, stats page and shared
site assets. This file inventory is separate from browser startup transfer or
peak process memory.

### Referdle matrix storage

The deployed solver has one matrix, `referdle-solver/data/pool_matrix.uint8.gz`.
`data/manifest.json` declares its component-local filename, `uint8` storage,
dimensions, compressed/decoded lengths and SHA256 hashes. The browser loads the
declared gzip with `DecompressionStream`, checks dimensions, decoded length and
pattern codes (0–242), and keeps a `Uint8Array`. Word order and logical pattern
values are unchanged.

The 4,047 × 4,047 matrix now occupies 16,378,209 decoded bytes, half the former
32,756,418 bytes. Its gzip is 7,491,562 bytes, down from 8,857,127 bytes (15.42%).
The logical SHA256 remains
`27ac836c6842a1076564d7197b8af46840d42ca24d4768016b090127dbc3547d`.
The legacy archive is retained in Git history rather than deployed alongside
the byte matrix. Reference traces are development evidence, not page downloads.

Reproduce the conversion in an isolated directory using the last revision with
the legacy asset, from the repository root:

```bash
REFERDLE_REPRO_DIR=$(mktemp -d /tmp/referdle-matrix.XXXXXX)
git show b0670d30aa7ff18b523860822eb5489a75cf8c38:referdle-solver/data/pool_matrix.int16.gz > "$REFERDLE_REPRO_DIR/legacy.int16.gz"
node referdle-solver/benchmarks/convert-matrix.mjs \
  --input "$REFERDLE_REPRO_DIR/legacy.int16.gz" \
  --output "$REFERDLE_REPRO_DIR/pool_matrix.uint8.gz" \
  --dimension 4047 --manifest "$REFERDLE_REPRO_DIR/manifest.json"
```

The converter reads signed little-endian int16 with `DataView`, validates every
value before narrowing, and verifies every converted value, the complete
logical hash and the gzip round trip before publication. It uses gzip level 9
with zero mtime and no filename; identical inputs produce identical gzip under
the same Node/zlib version. Output overwrite and input/output aliases are
refused. Replacing an existing manifest requires `--replace-manifest` and
matching dimensions. Output and manifest must share an existing directory;
failed publication removes the new output and preserves an existing manifest.

Focused loader tests exercise native Node `DecompressionStream` and streamed
responses. These verify loading/validation, not browser rendering or browser
module support; an actual browser smoke check remains a separate acceptance
step. Serve with `./server.sh` and open `/referdle-solver/` to check asset loading
and both probe modes in a browser. Full trace verification uses the benchmark
command above and never replaces the original references.

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

From `data/`, where the data subsystem's commands below also run:

```bash
python3 -m venv election_data
source election_data/bin/activate
pip install -r requirements.txt
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

From a terminal in `data/` — after running a writing script directly, for example:

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

The rebuild bootstraps US electoral-vote allocations immediately after importing
parties, before historical imports and export. It passes legacy forecast target
2028; review and classify legacy presidential runs before using it on an existing
database. A failed bootstrap is reported in the per-step summary, so check the
summary before treating the rebuilt outputs as complete.

### Underlying importers

The orchestrator chains these (all under `old_data/scripts/`, run from `data/`);
you can also run any one directly with `--refresh`:

```bash
./election_data/bin/python old_data/scripts/import_parties.py
./election_data/bin/python scripts/migrate_us_electoral_votes.py \
  --legacy-forecast-target-year 2028
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

### Presidential electoral-vote allocations

Database tables `us_electoral_vote_eras` and `us_electoral_vote_allocations`
are authoritative for presidential imports, forecasts, results, trends and
console comparisons. Actual elections select allocations by `elections.year`;
forecasts use the required `elections.target_election_year`. A forecast's
`year`, `election_date` and dated name still identify its **as-of run**, not its
target cycle. Current and baseline comparisons resolve their own allocations.
The old `seats.electoral_votes` column is no longer authoritative. Converter JSON
weights are descriptive only: both offline converters use the same bounded
bootstrap dataset, independently of later operator edits in the database.

For an existing database, stop writing processes, confirm the configured file,
back it up using section 3, then inspect migration's read-only report. Run these
commands from `data/`, stopping on failure between stages:

```bash
EV_DB=$(./election_data/bin/python -c \
  'from config import DatabaseConfig; print(DatabaseConfig.from_env().database_path)')
printf '%s\n' "$EV_DB"
./election_data/bin/python scripts/backup_db.py
./election_data/bin/python scripts/migrate_us_electoral_votes.py --dry-run
```

Migration opens the configured **existing** file; it never creates a missing
database. `.env` overrides shell environment values, so use the printed path
and correct `.env` before proceeding rather than assuming an inline
`DATABASE_PATH=...` selects another file. The report shows legacy NULL-target
forecast counts, map IDs and run-date ranges, plus allocation and map-label
changes. Only when all reported legacy forecasts belong to the current 2028
pipeline, apply:

```bash
./election_data/bin/python scripts/migrate_us_electoral_votes.py \
  --dry-run --legacy-forecast-target-year 2028
./election_data/bin/python scripts/migrate_us_electoral_votes.py \
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
bootstrap dataset in `data/old_data/scripts/usa/us_electoral_votes.py` and its
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

---

## 5) Import polls (Wikipedia-driven)

Start the console (section 7), then open [Import Poll](http://127.0.0.1:5055/import)
and use **Catch Up from Wikipedia** for Westminster polls.

1. Optionally set **Only polls ending on or after**. The cutoff includes polls
   whose fieldwork ends on that date. Leaving it blank uses the latest stored
   Westminster poll's fieldwork end date; if none is stored, every row in the
   fetched index is in the window. Set an earlier date to backfill older gaps.
2. Choose whether to keep **Run the UNS model and export once the queue is
   finished** checked. This is optional and runs once at the end if at least one
   poll was imported.
3. Click **Catch Up from Wikipedia**. The console fetches the Westminster
   voting-intention index, excludes polls already stored, and presents missing
   polls oldest first for review and import or skip.
4. Check the summary for failed imports, rows without an importer, and unreadable
   Wikipedia rows.

The queue lives in memory. Restarting the console, including a development
auto-reload, discards a catch-up in progress; confirmed polls remain stored.

---

## 6) Run UNS retrospective

From `data/`, use the main runner's retrospective mode:

```bash
./election_data/bin/python models/westminster/run_uns_model.py \
  --start-date <first-date> --end-date <last-date> --continue-on-error
```

Useful options:

- `--start-date YYYY-MM-DD`
- `--end-date YYYY-MM-DD`
- `--lookback-days 365`
- `--half-life-days 30`
- `--dry-run`
- `--reset-existing` / `--no-reset-existing` (retained compatibility flags; both recompute every requested date and retain its previous result until replacement succeeds)

Backfills commit one date at a time. A failed calculation or insertion preserves
that date's previous result; successful neighbours remain committed. With
`--continue-on-error`, other dates still run, but the command reports failed dates
and exits unsuccessfully. The trend cache is regenerated from stored results at
the end, including after partial failure.

### Model input and output policies

These rules apply to Westminster, Holyrood, US House, US Senate and US
President. Their electoral allocation remains specific to each contest.

- **Incomplete national polls:** omission means no new swing evidence. If a
  party's baseline share is 10%, omitting it gives zero swing; explicitly
  reporting 0% gives a swing of −10 percentage points. A new party reported at
  5% starts from a zero baseline and receives +5 points. Shares are then applied
  to baseline turnout and normalised within each seat; zero swing does not
  guarantee an unchanged final share when other parties move.
- **Regional omissions:** a missing regional observation inherits that party's
  national swing. Holyrood applies these rules independently to constituency
  and list polling. When no list polling contributes, its list swing falls back
  to the constituency swing. Latest-poll metadata considers contributors from
  both ballots, with poll ID resolving otherwise equal latest dates.
- **Aliases and weights:** rows for one canonical party are summed inside each
  poll before averaging. Thus 3% `Other` plus 2% `Others` in the same poll is
  5%, counted with that poll's weight once. Missing/`None` pollster weight is
  1; zero or negative weight excludes the poll. Recency decay then applies.
- **US seat matchups:** only the tracked, usable matchup contributes. An absent
  named party in a complete seat matchup has zero support; the `Others`
  fallback remains. Polls missing a material candidate are rejected using
  reference polls from the selected window. Senate national swing still uses
  House generic-ballot polling; its contested field and special-election
  baselines resolve from versioned, dated configuration. Presidential districts inherit
  their parent state's blended swing where applicable.
- **Date caps:** ordinary database-poll runs use the freshest contributing poll
  endpoint. Each candidate endpoint is checked in its own window of the
  requested length, including matchup/materiality, weight and row filters.
  With no contributor the requested date remains. Retrospective dates are
  explicit and uncapped. Holyrood manual `--poll-shares` is also an uncapped,
  single snapshot: omitted parties retain zero swing and no database-poll
  metadata is attached.
- **Allocation and recorded counts:** Westminster/US use their existing FPTP
  rules and baseline-share fallback when all projected shares clamp to zero;
  Holyrood skips an all-zero constituency or list allocation. Exact FPTP and
  D'Hondt ties keep the engine's first maximum in its existing iteration order.
  Holyrood retains two ballots and seeds list D'Hondt divisors with constituency
  wins. Allocation happens before whole-vote rounding (`round`, ties to even);
  stored winner flags survive a rounded vote-count tie. Popular-vote summaries
  use those recorded counts. Holyrood counts only constituency votes while
  retaining constituency and list seats. Presidential popular totals exclude
  Maine/Nebraska districts where their statewide parent is present; orphan
  districts still count, and every unit's winner and EV weight remains.

### Persistence, trends and previews

Stored outputs are identified by model election type, map ID and the model's
canonical dated name (including supported legacy suffixes). Replacing one date
deletes and inserts its election/votes in one SQLite transaction. A failed
insertion rolls back to the previous result; unrelated types/maps survive,
including a same-name row whose global uniqueness causes replacement to fail.

SQLite holds every successful date. Trend JSON is a derived, chronologically
ordered series that retains a point whenever recorded vote share (`v`), seats
or units won (`s`), or presidential electoral votes (`e`) change. Comparisons
use the serialized vote-share precision. Every publication reconstructs the
full scoped history, so changing a formerly compressed date can restore a
previously omitted successor. Historical batches publish once at finalization.

Trend files are published through a temporary sibling and atomic replacement.
SQLite and files are separate transactions: a publication failure after commit
reports that results were saved, returns failure, and supplies a repair command.
Regeneration reads SQLite directly and works with a missing, malformed or stale
cache. It repairs trends without recalculating elections or latest-poll metadata.

Default `--dry-run` writes no database results, trends, predictions or metadata.
Explicit previews are supported by Westminster `--output-csv` (also writes its
sibling regional-differences CSV) and Holyrood `--output` (also writes sibling
`<stem>-meta.json`). For example, from `data/`:

```bash
./election_data/bin/python models/westminster/run_uns_model.py \
  --dry-run --output-csv /tmp/westminster-preview.csv
./election_data/bin/python models/holyrood/run_holyrood_uns_model.py \
  --dry-run --poll-shares '{"snp": 34, "lab": 29}' \
  --output /tmp/holyrood-preview.json
```

Holyrood `--no-output` suppresses prediction and metadata even with an explicit
`--output`; it does not suppress database/trend writes in a non-dry run. US dry
runs have no file-preview option. The trend repair CLI's `--dry-run` validates
and reconstructs without writing, even when `--output` is supplied.

### Refresh existing model history

This is an operator task after source deployment. Cache regeneration can recover
recorded vote/seat/EV changes from existing rows. Changed polling admission,
omission/weight rules and presidential baseline popular aggregation require
actual model recomputation for affected historical dates first.

1. Stop concurrent model writers and back up the configured SQLite database,
   using section 3's backup commands. Resolve the intended map, baseline,
   affected date interval and polling window from that database and the model
   configuration. The runners load `.env` with override enabled, so merely
   setting a shell `DATABASE_PATH` does not select a different database; select
   the intended configured database before running them. The repair CLI accepts
   its source explicitly via `--database`.
2. Recompute affected history using the commands below. Replace angle-bracket
   placeholders with verified values. UK baseline names select their map
   (Westminster also accepts `--map-name`). US map and baseline names come from
   each runner's `SPEC`; Senate specials and overrides come from the manifest.
   US runners do not expose CLI map/baseline overrides.
3. After successful history refresh, run the ordinary current-date models to
   refresh latest-poll metadata and Holyrood's current prediction. UK
   retrospective mode does not publish those current snapshot files. Keep
   matching baseline/map and polling-window choices for this run.
4. Regenerate scoped trend caches from the refreshed database, then perform the
   final site export. Resolve each numeric map ID from its intended output map;
   the repair command checks that the map belongs to the selected model.

Run these from `data/`; choose the relevant models and ranges. First resolve
the configured path and inspect map/baseline identities without writes:

```bash
MODEL_DB=$(./election_data/bin/python -c \
  'from config import DatabaseConfig; print(DatabaseConfig.from_env().database_path)')
sqlite3 -readonly "$MODEL_DB" \
  'SELECT id, name, parliament FROM maps ORDER BY id;
   SELECT map_id, name, type FROM elections ORDER BY map_id, election_date;'
```

Use the map ID whose name/parliament matches the selected baseline and runner
scope; exclude model-output elections when choosing a baseline. Run each
applicable command separately, stopping on failure before the next stage:

```bash
./election_data/bin/python models/westminster/run_uns_model.py \
  --map-name '<map-name>' --baseline-election-name '<baseline-name>' \
  --start-date <first-date> --end-date <last-date> \
  --lookback-days <window-days> --continue-on-error
./election_data/bin/python models/holyrood/run_holyrood_uns_model.py \
  --election-name '<baseline-name>' \
  --start-date <first-date> --end-date <last-date> \
  --lookback-days <window-days> --continue-on-error

# Automatic US rebuild range, using the console's normal polling windows:
./election_data/bin/python models/us/run_us_house_model.py --since-days-back 60 --rebuild-history
./election_data/bin/python models/us/run_us_senate_model.py --since-days-back 60 --rebuild-history
./election_data/bin/python models/us/run_us_presidential_model.py --since-days-back 120 --rebuild-history

# For an explicit affected US interval instead of automatic rebuild:
./election_data/bin/python models/us/<runner>.py \
  --start-date <first-date> --end-date <last-date> \
  --lookback-days <window-days> --continue-on-error

# Ordinary current snapshot, using the chosen UK scope and polling window:
./election_data/bin/python models/westminster/run_uns_model.py \
  --map-name '<map-name>' --baseline-election-name '<baseline-name>' \
  --since-days-back <window-days>
./election_data/bin/python models/holyrood/run_holyrood_uns_model.py \
  --election-name '<baseline-name>' --since-days-back <window-days>
./election_data/bin/python models/us/run_us_house_model.py --since-days-back 60
./election_data/bin/python models/us/run_us_senate_model.py --since-days-back 60
./election_data/bin/python models/us/run_us_presidential_model.py --since-days-back 120

# Repeat for westminster, holyrood, us-house, us-senate and us-president:
./election_data/bin/python scripts/rebuild_model_trends.py \
  --model <model> --map-id <map-id> --database "$MODEL_DB" --dry-run
./election_data/bin/python scripts/rebuild_model_trends.py \
  --model <model> --map-id <map-id> --database "$MODEL_DB"
# Optional isolated destination: add --output /tmp/repaired-trends.json

./election_data/bin/python scripts/export_elections.py
```

Stop before export on any failed refresh. Successful dates are retained, failed
dates keep their old results, and `--continue-on-error` still exits unsuccessfully.
Automatic US rebuild prunes out-of-scope history only after all required
replacements succeed. Retry failed refreshes or the supplied cache repair command
before exporting. Historical per-era EV modelling remains separate work.

---

## 7) Run local server

From `data/`:

```bash
./election_data/bin/python server.py
```

Server URL:
- `http://127.0.0.1:5055/`

It answers only `127.0.0.1` / `localhost`, and refuses a POST made from another
site's page (`data/console/csrf.py`).

The Werkzeug debugger and auto-reloader are off by default: the debugger runs
arbitrary code for anything that can reach the port. For reload-on-save while
developing:

```bash
CONSOLE_DEBUG=1 ./election_data/bin/python server.py
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
	- Check the Catch Up from Wikipedia summary's window and already-imported count.
	  Set an earlier cutoff on the import form to include older missing polls, and
	  inspect its failed, no-importer, and unreadable-row reports.

- **Many `0` regional poll rows**
	- Current importers can default missing regional values to `0.0` for some source formats.
	- Audit directly in DB, for example:

```bash
sqlite3 "$DATABASE_PATH" "SELECT poll_id, COUNT(*) AS zero_rows FROM poll_rows WHERE percentage = 0 GROUP BY poll_id ORDER BY zero_rows DESC LIMIT 25;"
```

---

## 10) Static election-map export (manifest + files)

Use scripts under `data/scripts/` to generate static files for `electionmaps/`
and `uselectionmaps/`.

All commands in this section run from `data/`.

### Full export (elections and latest forecasts)

```bash
./election_data/bin/python scripts/export_elections.py
```

The full export includes supported UK and US elections and, when present in the
database, the latest Westminster UNS simulation and latest forecast for each US
contest. It writes each page's manifest and data files.

Election placement is owned by `data/scripts/export/ordering.py`. The previous
generated manifest remains an ordering input; the shell does not define election
order. After initial construction and restoration, final placement applies these
rules in sequence:

1. Restore the previous manifest's ID order. New entries prefer placement after
   their first inbound comparer in built order if that comparer appears in the
   previous manifest; later comparers are not searched. Otherwise they go before
   their own remembered comparison baseline, or at the end if neither anchor is
   remembered. Ties retain built order; without a previous order, the built order
   is retained.
2. Reapply supplemental anchors. A non-null `insertBeforeId` takes precedence
   over `insertAfterId`; a missing selected anchor appends the entry.
3. Promote forecasts to their parliament's first occurrence, preserving relative
   forecast and non-forecast order without regrouping interleaved parliaments.

Initial supplemental registration replaces existing IDs in place. Current
Parliament initially follows `current-prediction` (or leads if absent); restored
Westminster models initially lead, while other restored entries precede the first
Holyrood entry (or append if absent). Final rules can override those initial
positions. Comparison assignment still runs both before restoration and after
final ordering, retaining links already set. Default selection is independent of
final order and preserves a valid configured default.

Dry-run:

```bash
./election_data/bin/python scripts/export_elections.py --dry-run
```

Scratch export (writes every page under `DIR/<page>/data`, seeding each from the
repository's page data directory on first use; the repository is not touched):

```bash
./election_data/bin/python scripts/export_elections.py --output-base /tmp/export-check
```

`--output-base` works with every mode. `--output-root DIR` applies only to
`--current-simulation` (the UK page directory to write under) and is refused for the
other modes.

### Targeted exports

```bash
./election_data/bin/python scripts/export_elections.py --election-name "2019 General Election" --output-file /tmp/2019.json
./election_data/bin/python scripts/export_elections.py --current-simulation --output-file /tmp/current-simulation.json
```

### Metadata-only manifest refresh

```bash
./election_data/bin/python scripts/export_elections.py --metadata-only
```

### Manifest contract used by webpage

Each page loads its own generated manifest:
[UK `map-modes.json`](electionmaps/data/map-modes.json) or
[US `map-modes.json`](uselectionmaps/data/map-modes.json). Both use the same contract:

- `defaultElection` and ordered `elections` entries with `id`, `name`, `type`,
  `mapId`, `parliament`, and optional behavior/comparison fields.
- `files.elections.mapsById`, `files.elections.electionsById`, and `files.meta`,
  with paths relative to that page's data directory.
- `parties`, `partyKeyAliases`, and `mapModes[mapId].regions` for party and region metadata.
- `misc`, `parliamentFeatures`, and `mapModes` for branding, features, prediction,
  and map options.

The [shared engine](electionmapslogic/app.js) initializes the manifest, derives
party/region lookups, and resolves results and topology through `files.elections`.
Edit each page's `map-modes-shell.json` for hand-authored configuration and
regenerate its manifest. See the [full manifest reference](electionmaps/data/map-modes.md)
for supported fields, selection rules, and shell transformations.

Saved Senate forecasts supply their pinned target year, baselines, specials and
roster reference during export. Those forecast fields override the shell so a
later shell edit cannot reinterpret an existing forecast.

### Results schema

- Exported result payloads use compact schema `pf-results-v4`:
	- top-level: `{"schema":"pf-results-v4","seats":[...]}`
	- seat keys: `n` (seat name), `r` (region), `w` (winner), `e` (electorate), `m` (majority), `t` (turnout), `p` (party rows)
	- party row: `[partyKey, total, name]`
	- model run only, optional: `u` (per-party share range and win probability, aligned to `p`)
- The current Westminster model run also writes `results/prediction-uncertainty.json`
  (`pf-uncertainty-v1`: national and regional ranges, odds and seat histograms) when it
  has full detail; see the [manifest reference](electionmaps/data/map-modes.md).
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

### Offline uncertainty calibration comparisons

The Senate and President comparison reports use committed, hash-pinned evidence
under `data/models/us/calibration/`. They remain **unaccepted** and do not nominate
runtime coefficients or enable odds. Their official-outcome-conditioned references
measure combined fallback error and alternative state/race allocations; they do not
validate historical chamber control, presidential district outcomes, or early
held-today forecasts. Local polling averages remain separate total-error proxies,
without invented sample sizes or blend weights.

The Senate baseline-only panel has an observed-minus-projected D−R mean of about
+12.5 points in both 2022 and 2024, even with official House national inputs.
The selected Senate field and national House generic ballot need a compatible
baseline anchor. Operational Senate calibration awaits that separate central-model
correction and a rerun of these comparisons; the current central model is unchanged.

From `data/`, reproduce the JSON and Markdown reports without database access:

```bash
./election_data/bin/python scripts/calibrate_us_uncertainty.py --model senate --output-dir models/us/calibration
./election_data/bin/python scripts/calibrate_us_uncertainty.py --model president --output-dir models/us/calibration
```

Omit `--output-dir` to print the review document without writing files. The default
model remains House. The read-only console views are
`/us/senate/uncertainty/calibration` and `/us/president/uncertainty/calibration`.
Each report records source normalization, raw/decided denominator alternatives,
exact central parity, independent kernel recovery, whole-election conditional
holdouts, and the assumptions still requiring a decision before release.

### 2026 validation preregistration

The [2026 validation preregistration](data/models/us/validation-2026.md) defines
offline House/Senate capture and scoring. The read-only console views at
`/us/house/uncertainty/validation` and `/us/senate/uncertainty/validation` show fixed
definitions and missing-input requirements; certified 2026 scoring and evaluation
basis review remain pending. `scripts/validate_us_uncertainty.py` requires explicit
database, snapshot, official-source manifest and new local output paths for the
respective commands. It never imports results, reconstructs simulations or changes
releases. Scoring requires an explicitly reviewed policy frozen with the snapshot.

### Model runs

For gated uncertainty rollout, scratch isolation, timing, retained snapshots and
separately approved live steps, see the
[US uncertainty operations runbook](data/models/us/uncertainty-operations.md).
All three releases remain disabled pending model-specific prerequisites; ordinary
runs retain central-only behavior, while an explicit `--simulations` request
requires an accepted release.

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
  national polls count. A Senate cap cannot silently cross into another cycle.
- Each polled seat prints a `SEAT_POLL <seat> n= W= α= matchup=` line.

`--rebuild-history` first recomputes every date already stored in SQLite
(within the poll window, using the daily run's window rather than
`--lookback-days`), then does the normal run. Use it after anything that moves
the whole history: a new tracked matchup, a baseline override, or new Senate
seats. **Run it once for the Senate**, so its trend series moves from 33 to 35
seats. It picks its own range and its own as-of date, so combining it with
`--start-date`/`--end-date` or `--as-of-date`/`--as-of-days-back` is a usage
error (exit 2): a past as-of would delete every trend point above it and rebuild
only up to it. If the whole series lies outside the poll window, the points are
replaced by the poll window `[first poll, as-of]`. Points outside the new scope
are dropped only after all required replacement dates succeed. A partial failure
retains those points and reports failure, so the console does not export the run.

### Senate cycles and specials

The model resolves its field from the run's as-of date using
[`cycles-v1.json`](data/models/us/senate_cycles/cycles-v1.json):

- Every 2026 date uses 33 Class-2 races with 2020 baselines, plus the Florida and
  Ohio Class-3 specials with 2022 baselines.
- From 1 January 2027 through 2028, the target is the 34 Class-3 races of 2028,
  using 2022 baselines. Full-chamber context awaits a verified post-2026 roster.
- Other periods need an explicit configuration before the model can run.

Each saved run retains its field, roster and control policy atomically with its
vote rows. Export uses that saved context and writes a separate pinned forecast
roster; `senate-current.json` continues to represent actual membership. Update
the versioned model configuration when adding a cycle or changing its inputs.
Editing the shell alone does not change the model. A state with both a regular
and a special race in one cycle cannot be represented by the current map.

The saved Senate forecast's **Seats up** view compares each seat with its previous
election: for 2026, the regular seats use 2020 results and Ohio and Florida use
their declared 2022 baselines. These combined results drive seat and vote-share
changes, gains and seat popups. If a required baseline is unavailable, the forecast
still displays but its comparison is hidden and labelled "Comparison unavailable".
**Full Senate** compares the projected chamber with its pinned Senate membership;
returning to **Seats up** restores the per-seat election comparison.
Without a verified roster, only **Seats up** is offered. See
[`senate-cycles.md`](data/models/us/senate-cycles.md) for configuration,
persistence and future chamber-activation details.

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

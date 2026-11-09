# Models

## Run UNS retrospective

From `data/`, use the main runner's retrospective mode:

```bash
./election_data/bin/python models/westminster/run_uns_model.py \
  --start-date <first-date> --end-date <last-date> --continue-on-error
```

Useful options:

- `--start-date YYYY-MM-DD`
- `--end-date YYYY-MM-DD`
- `--lookback-days 60` (default: the poll window per date, the same 60 days as
  the single-date `--since-days-back` default)
- `--half-life-days 15` (default, also the console's)
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
  baselines remain restricted by the manifest. Presidential districts inherit
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
   using the [backup commands](database.md#backups). Resolve the intended map, baseline,
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

./election_data/bin/python export/export_elections.py
```

Stop before export on any failed refresh. Successful dates are retained, failed
dates keep their old results, and `--continue-on-error` still exits unsuccessfully.
Automatic US rebuild prunes out-of-scope history only after all required
replacements succeed. Retry failed refreshes or the supplied cache repair command
before exporting. Historical per-era EV modelling remains separate work.

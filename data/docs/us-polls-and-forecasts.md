# US polls and forecasts

Everything below runs from `data/` (environment active). The US side has these
maps — "US House Districts 2024", "US House Districts 2026" (the 2026 redraw), "US House Districts
2012 cycle" (2018-20), "US Presidential 2024" and "US Senate 2024" —
all regioned by the 9 Census divisions. State and district polls are attached to
a **seat** on the poll, never by re-pointing seats to state regions (front-end
Predict depends on the divisions).

## Contests

Polls come from Wikipedia, as four contests:

| Contest (slug) | Map | Source | Poll scope |
|-|-|-|-|
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

## Review queue (`/us/import`)

The normal way in. Start the console ([Run local server](../README.md#run-local-server)), then **Import US Polls** on the
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

## Matchups

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

## Model runs

Note: the House and Senate forecasts are currently not exported (`EXPORT_US_CHAMBER_FORECASTS`
in `scripts/export_elections.py`, see [Full export](export.md#full-export-elections-and-latest-forecasts)); the runs and the poll tracker still work.

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

`--rebuild-history` first recomputes every date already stored in SQLite
(within the poll window, using the daily run's window rather than
`--lookback-days`), then does the normal run. Use it after anything that moves
the whole history: a new tracked matchup, a baseline override, or new Senate
seats. **Run it once for the Senate** after the cycle rolls, so its trend series
rebuilds onto the new field (the 34 Class-3 seats for 2028). It picks its own range and its own as-of date, so combining it with
`--start-date`/`--end-date` or `--as-of-date`/`--as-of-days-back` is a usage
error (exit 2): a past as-of would delete every trend point above it and rebuild
only up to it. If the whole series lies outside the poll window, the points are
replaced by the poll window `[first poll, as-of]`. Points outside the new scope
are dropped only after all required replacement dates succeed. A partial failure
retains those points and reports failure, so the console does not export the run.

## Senate specials and per-seat baselines (Ohio, Florida)

See [Senate specials and per-seat baselines](../../uselectionmaps/README.md#senate-specials-and-per-seat-baselines-ohio-florida).

## CLI importers

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

## One-off upgrade (existing database)

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
   While `EXPORT_US_CHAMBER_FORECASTS` is off (the default) the export does not publish the
   House or Senate forecast; with it on, `uselectionmaps/data/results/us-senate-forecast.json`
   should have 34 seats (the 2028 Class 3 field, including Ohio and Florida).

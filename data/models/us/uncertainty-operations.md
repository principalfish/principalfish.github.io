# US uncertainty: scratch pilots and operational rollout

Status on 25 October 2026: the software is available, but all three production
releases and calibrations remain unaccepted. No calibrated operational pilot,
uncertainty backfill or publication is recorded by this runbook. Existing saved
central projections can be previewed without presenting synthetic odds as data.

## Release prerequisites

| Model | Outstanding prerequisite |
|-|-|
| All three | Accept model-specific calibration and labelled assumptions; resolve the actual contributing polls' evidence, population and sample sizes |
| House | Approve target-cycle geography and compatible baseline; the current 2024 map cannot be assumed to match the 2026 field |
| Senate | Correct the House-generic/Senate-field baseline anchor and repeat calibration comparisons; retain the pinned field, roster, Independent alignments and VP policy |
| President | Accept the national total-error proxy and state/local/district assumptions; verify target allocation and baseline provenance |

Inspect `house-release.json`, `senate-release.json`, `president-release.json` and
their referenced calibration files. The console hubs at
`/us/{house|senate|president}/uncertainty` show release and retention status.
Comparison reports are evidence for review, not accepted runtime settings. Do
not enable a candidate just to obtain a pilot. Acceptance and release changes
must be separately reviewed and committed before these operational steps.

House and Senate use two-year target windows with January rollover. Every
2026-dated run retains the 2026 target. The 2028 Senate field has no verified
post-2026 full-chamber roster; it cannot produce approved chamber-control odds.
Presidential runs pin their target year, matchup and allocation. Do not join
history across incompatible fields, matchups, allocations or estimator versions.

Ordinary model commands preserve central-only operation while release is gated.
`--no-uncertainty` explicitly selects central-only output even after approval.
`--simulations N` explicitly requests uncertainty and fails when release is
gated. `--simulations` and `--no-uncertainty` cannot be combined.

## Prepare an isolated local copy

Run this from the repository root after committing the code to be tested. Use a
local disk with room for the database, raw arrays, exports and archives. These
commands create a scratch copy; they do not run models or change the source DB.
Set `US_MODEL_SOURCE_DB` to the explicit database whose committed contents are to
be copied. Do not copy a live SQLite file with `cp`: its WAL may contain commits.

```bash
export US_MODEL_SOURCE_DB=/absolute/path/to/elections.db
export US_PILOT_PY="$PWD/data/election_data/bin/python"
mkdir -p data/.pytest_cache
export US_PILOT_ROOT="$(mktemp -d "$PWD/data/.pytest_cache/us-pilot.XXXXXXXX")"
mkdir -p "$US_PILOT_ROOT/source" "$US_PILOT_ROOT/raw" "$US_PILOT_ROOT/tmp"
git rev-parse HEAD > "$US_PILOT_ROOT/source-commit.txt"
git archive HEAD | tar --exclude=.env --exclude='*/.env' -xf - -C "$US_PILOT_ROOT/source"
test ! -e "$US_PILOT_ROOT/source/.env"
"$US_PILOT_PY" - <<'PY'
import os
import sqlite3
from contextlib import closing
from pathlib import Path

source = Path(os.environ['US_MODEL_SOURCE_DB']).resolve(strict=True)
target = Path(os.environ['US_PILOT_ROOT']) / 'elections.db'
if target.exists() or target.resolve() == source:
    raise SystemExit('Refusing an existing or overlapping snapshot destination')
with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as reader:
    reader.execute('PRAGMA query_only=ON')
    with closing(sqlite3.connect(target)) as writer:
        reader.backup(writer)
        if writer.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise SystemExit('Snapshot failed quick_check')
print(target)
PY
export DATABASE_PATH="$US_PILOT_ROOT/elections.db"
export SIMULATIONS_PATH="$US_PILOT_ROOT/raw/westminster-latest.npz"
export ELECTIONS_ARCHIVE_DIR="$US_PILOT_ROOT/archives"
export ELECTIONS_BACKUP_DIR=''
export TMPDIR="$US_PILOT_ROOT/tmp"
cd "$US_PILOT_ROOT/source/data"
"$US_PILOT_PY" - <<'PY'
import os
from pathlib import Path
from config import DatabaseConfig

root = Path(os.environ['US_PILOT_ROOT']).resolve()
config = DatabaseConfig.from_env()
for value in (config.database_path, config.simulations_path,
              os.environ['ELECTIONS_ARCHIVE_DIR'], os.environ['TMPDIR']):
    if not Path(value).resolve().is_relative_to(root):
        raise SystemExit('Resolved output escaped scratch root')
print('Scratch database:', config.database_path)
print('Scratch raw base:', config.simulations_path)
PY
```

The repository `.env` overrides shell variables. Its absence in the scratch
source is essential. The runners have no CLI database, map or baseline override;
resolve `DatabaseConfig` in this copy before proceeding. Trend and metadata
destinations derive from the copied source tree. Per-model raw files are
`us-house-latest.npz`, `us-senate-latest.npz` and `us-president-latest.npz` beside
the configured raw base. Keep the original Python virtualenv; do not install or
modify packages as part of a pilot. Use `data/.venv/bin/python` instead if that
is the project's installed environment.

## Readiness, schema and evidence

Reproduce the read-only inventory described in
[uncertainty-readiness.md](uncertainty-readiness.md) against the snapshot. Its
October 22 counts and map IDs are historical: re-resolve map IDs by exact name,
inspect current table columns, and use a read-only connection with `query_only`
and a read transaction. Do not use a default `Database()` for an inventory.
Record source commit, snapshot checksum, resolved paths, map/baseline IDs, poll
window, tracked matchups, target context and configuration hashes.

From the scratch `data/` directory, inspect migrations first:

```bash
"$US_PILOT_PY" scripts/migrate_add_us_poll_evidence.py --dry-run
"$US_PILOT_PY" scripts/migrate_add_model_uncertainty.py --dry-run
"$US_PILOT_PY" scripts/migrate_us_electoral_votes.py --dry-run
```

Stop on an unexpected schema or ambiguous legacy forecast target. Do not guess
`--legacy-forecast-target-year`. Apply only the reviewed required migrations to
the scratch copy, by rerunning the corresponding command without `--dry-run`.
The nullable evidence migration and reviewed House baseline corrections were
already separately applied to the live database; inspect rather than repeat them.

The evidence tool defaults to a proposal and fetches external source pages:

```bash
"$US_PILOT_PY" scripts/backfill_us_poll_evidence.py --database "$DATABASE_PATH"
```

Review failed pages, exact identities and proposed repairs before adding
`--apply` against the scratch database. It revalidates stored fingerprints;
unmatched or stale records remain unresolved. Aggregate-average N is not
applicable. Missing N on an actual individual-poll contributor stops uncertainty;
do not invent N or relabel a survey as an aggregate. A poll outside the window
or rejected by the actual contributor rules need not block that run. Historical
dates can have different contributors, so a successful latest date does not
establish readiness of the whole history.

## Pilot one date per model after acceptance

Choose and record an explicit supported `US_PILOT_DATE` for each model. Record
both the requested date and the effective as-of date: ordinary runs can cap the
date at usable fieldwork. Use identical polling windows and central options for
the uncertainty-on/off comparison. The examples use the console's 60-day House
and Senate windows and 120-day President window.

```bash
export US_PILOT_DATE=2026-10-19
"$US_PILOT_PY" models/us/run_us_house_model.py --as-of-date "$US_PILOT_DATE" --since-days-back 60 --no-uncertainty --dry-run
"$US_PILOT_PY" models/us/run_us_senate_model.py --as-of-date "$US_PILOT_DATE" --since-days-back 60 --no-uncertainty --dry-run
"$US_PILOT_PY" models/us/run_us_presidential_model.py --as-of-date "$US_PILOT_DATE" --since-days-back 120 --target-election-year 2028 --no-uncertainty --dry-run
```

These compute central projections without persistence. An approved uncertainty
dry run can validate evidence/configuration, but **skips Monte Carlo draws**.
It is not a runtime or stochastic-spread measurement.

For each model, persist a central-only run in scratch and save canonical sorted
`(seat_id, party_id, vote_total, elected)` rows. Then run the same date/options
with uncertainty. Compare every row exactly, not just chamber winners or rounded
shares. Same-date persistence replaces that date, so retain the first row dump
before the second run. Record the new result/artifact/config identities and seed.

The following is an actual simulation and scratch write; run it only after the
accepted release is present in the copied source:

```bash
/usr/bin/time -v -o "$US_PILOT_ROOT/house-time.txt" \
  "$US_PILOT_PY" models/us/run_us_house_model.py \
  --as-of-date "$US_PILOT_DATE" --since-days-back 60 --simulations 10000 \
  > "$US_PILOT_ROOT/house-run.log" 2>&1
```

Repeat with `run_us_senate_model.py` and its 60-day window, then
`run_us_presidential_model.py` with its 120-day window and explicit target year.
Give each timing/log a distinct filename. Capture exit code, elapsed time, peak
RSS, draw count, field size, party count, raw size and DB/trend/export sizes.
Current console limits are **300 seconds per ordinary model/export step** and
**3600 seconds per model history rebuild**. Measure export separately. Leave
headroom for console/export work; do not infer a timeout fit from a dry run or
extrapolate a short pilot as a guaranteed full-history duration.

Review exact saved replay and decomposition in the console explorer. Check:

- safe, marginal, unpolled, Independent, special and genuinely
  uncontested races where present; a single-party baseline is not proof of an
  uncontested candidate election;
- zero eligibility masks, admitted local-poll alpha, source weights, clipping,
  normalization and shared national/state errors;
- House totals of 435, Senate contested totals and full 100 with frozen
  holdovers, caucus/VP control and separate 50–50 chances;
- President's 56 allocation-unit winners summing to 538 EV, with 51 state/DC
  reference units supplying national popular votes; separately inspect Maine
  and Nebraska districts and their parent fallback;
- exact simulation return navigation, two-decimal displays, party ordering,
  missing-detail messages and national/region versus party/majority filters;
- all three US trackers plus Westminster after a shared frontend/export change.

Record sample IDs, why each case qualifies, central rows, intervals, probabilities,
error components, replay identity and reviewer outcome. Synthetic fixtures test
software; they do not replace this review of actual approved inputs.

| Pilot | Date/context | Central parity | Runtime/RSS | Sample review |
|-|-|-|-|-|
| House | Pending prerequisites | Pending | Not measured | Pending |
| Senate | Pending corrected anchor/calibration | Pending | Not measured | Pending |
| President | Pending acceptance/allocation review | Pending | Not measured | Pending |

## History, retention and scratch export

Start with a short explicit interval in scratch. For example, after approval:

```bash
"$US_PILOT_PY" models/us/run_us_house_model.py \
  --start-date 2026-10-17 --end-date 2026-10-19 \
  --lookback-days 60 --simulations 10000
```

An explicit interval uses `--lookback-days` (default 365), not the ordinary
`--since-days-back` window. Use 60 for House/Senate and 120 for President when
matching the console convention; also pass the President target year. Model
range/rebuild paths batch trend publication. Do not rebuild/export after every
date. `--rebuild-history` chooses its own range/as-of and cannot be combined with
explicit interval/as-of flags. Inspect the selected scope before a long rebuild.
Split work at context boundaries and stop on errors; avoid `--continue-on-error`
when deciding whether a batch is ready for export.

National summaries/private replay inputs persist for each saved uncertainty run;
race summaries and raw arrays are retained for the latest run in scope. Range
runs keep raw arrays only at their final date. `--no-raw-sims` suppresses the raw
archive, not DB or trend writes. A later run can prune earlier race detail, and a
central-only replacement can remove uncertainty for that date. Capture needed
pre-result snapshots before replacement/pruning, following
[validation-2026.md](validation-2026.md). The capture CLI supports House/Senate
2026 and bounded optional history; it never reconstructs missing detail.
Capturing after a range batch cannot recover its pruned intermediate race detail.
If that detail must be preserved, run and capture each date before advancing.

Reconstruct a trend cache from saved outputs without rerunning forecasts when
needed; re-resolve the actual map ID rather than copying this example's placeholder:

```bash
"$US_PILOT_PY" scripts/rebuild_model_trends.py --model us-house \
  --map-id "$US_PILOT_MAP_ID" --database "$DATABASE_PATH" \
  --output "$US_PILOT_ROOT/rebuilt-house-trends.json" --dry-run
```

Remove `--dry-run` to write that explicit scratch destination. Other model keys
are `us-senate` and `us-president`. This repairs caches from committed DB outputs;
it does not calculate missing odds or restore pruned draws.

```bash
"$US_PILOT_PY" scripts/export_elections.py --dry-run
"$US_PILOT_PY" scripts/export_elections.py --output-base "$US_PILOT_ROOT/export"
```

`--output-base` writes the full export under scratch page directories. The
`--output-root` option applies only to `--current-simulation` and is not a full-site
scratch destination. Inspect the copied site's generated manifests and compatible
forecast/sidecar identities. Public uncertainty requires both current release
approval and an approved saved run; changing today's release cannot approve an
old unaccepted snapshot. Never copy a synthetic sidecar into public data.

## Live handover and recovery

Before live work, retain the reviewed pilot report, exact code/config hashes,
scope/date windows, measured resources, evidence repair proposal, migration dry
runs and expected export differences. Obtain separate authorization for each
live schema/data repair, forecast/backfill and publication operation. Make a fresh
SQLite backup before each approved live mutation; verify its integrity and keep
its path. Stop competing model/export writers. Shell DB assignments alone do not
override the repository `.env`: verify the resolved live paths explicitly.

Review partial success before retrying. Forecast replacements commit per date;
a timeout can leave a mixture of old and new dates. Prior dates are retained
until each replacement succeeds, and rebuild pruning waits for required success.
Trend publication or raw publication can fail after DB commit: read the reported
committed scope, repair the scoped trend cache or rerun the exact latest run as
appropriate, and verify identities before export. A previous raw archive must
not be represented as matching a new result merely because its filename exists.

Do not restore over open connections or delete history to conceal a partial
failure. A live restore is a separate approved operation with writers stopped
and a backup of the state being replaced. Scratch failures can be investigated
or restarted from a fresh consistent copy.

Run the complete export into scratch first, review it, then request publication
of the specific reviewed outputs separately. Retrospective backfilled odds must
be labelled as reconstructed history; they are not forecasts proven to have been
published before results. Actual 2026 scoring and any 2028 recalibration remain
separate steps under the preregistration.

# The data console

A local Flask app for the data: poll imports, model runs, outputs, the site export
and the database. Start it as in [Run local server](../README.md#run-local-server);
it listens on `http://127.0.0.1:5055/`. The app is `console.create_app`, and the code
is under `console/`.

## Layout

The console has five sections, one per contest it manages: **Westminster**
(`/westminster`), **Holyrood** (`/holyrood`), **US House** (`/house`), **US Senate**
(`/senate`) and **US President** (`/president`). Each has a landing page with a
section bar of its tools, and a **Recent runs** link to its entries in the
[run log](#run-log).

The top bar on every page holds the five sections, then **All polls** (`/polls`),
**Export** (`/export`), **Runs** (`/runs`) and **Admin** (`/admin`). The front page
(`/`) holds links only.

### What each section offers

Every section has the same four tools:

- **Polls** at `/<section>/polls`: that section's polls, with Delete. A poll's own
  page is `/polls/<poll_id>`. **All polls** at `/polls` lists every section's polls.
- **Import**: the way new polls get in (below).
- **Run model** at `/<section>/model`: the model run form. Every form has a dry run
  (default Yes) and an **Export when done** checkbox.
- **Outputs** at `/<section>/outputs`, with `/<section>/outputs/<election_id>` for
  one run: the section's saved model runs, with Delete and bulk delete.

Beyond those:

| Section | Import | Also |
|-|-|-|
| Westminster | **Import poll** at `/westminster/polls/import`: the single-poll import and the **Catch Up from Wikipedia** queue | **By-elections** (`/westminster/by-elections`), **Poll coverage** (`/westminster/polls/coverage`), **Uncertainty** (`/westminster/uncertainty`, see [Westminster uncertainty validation](westminster-uncertainty.md)) |
| Holyrood | **Import Scottish Polls**, a one-click button on the landing page (no import page) | |
| US House | **Auto import** at `/house/polls/import` | **Matchups** (`/house/matchups`), **Results** (`/house/results`) |
| US Senate | **Auto import** at `/senate/polls/import` | **Matchups** (`/senate/matchups`), **Results** (`/senate/results`), and **Rebuild Current Senate** on the results page (`/senate/results/current-senate`) |
| US President | **Auto import** at `/president/polls/import` | **Matchup** (`/president/matchup`) |

The US auto import is the review queue described in
[US polls and forecasts](us-polls-and-forecasts.md#review-queue); the NYT results
flow is in [US results from NYT](us-results.md). The Westminster catch-up queue is
walked through in [Import polls](../README.md#import-polls-wikipedia-driven).

**Export when done** works differently from the Export page: on the Westminster form
it refreshes only the current simulation file (`--current-simulation`), and on the
Holyrood and US forms it runs the full export. It follows a successful run only, and
never a dry run.

## Export

`/export` (**Export**) is a global form. Tick the sections and what to write
(results, current forecast, metadata), then **Run export**. Dry run is on by default
and lists what would be written; choosing No writes the files into the repository,
after a confirmation. It runs `export/export_elections.py` once with `--sections`
and `--content`. The form and the flags are described in [Export](export.md).

Other pages link here with the boxes pre-ticked (for example **Export to site** after an
NYT results import); a link never changes the dry-run choice.

## Background runs and the run slot

A model run (with its **Export when done** step), the "rebuild history" box on
`/house/matchups`, `/senate/matchups` and `/president/matchup`, the Export page and
**Rebuild Database** run as background jobs. Submitting the form redirects to
`/runs/<id>`, a page that polls once a second and shows the live output; when the job
ends it shows the result block. The job carries on if you leave the page.

The console runs **one thing at a time**, through a single run slot. A job holds it for
its whole life, and so do the other long actions: the Holyrood import, confirming a
US results import, **Rebuild Current Senate**, the model step that finishes a poll
queue (Westminster or US), a Westminster single-poll model step and a by-election's
export step. A second action is refused, not queued, with "another run is in
progress" and, when a background job holds the slot, a link to it. Where an import
has already been committed (a poll queue's polls and matchup tracking, a Westminster
single-poll import, a by-election) only the model or export step after it is refused.
The slot lives in the console's process: it does not guard against a second console
or a script run by hand.

**Restore newest archive** (`/db/restore`, on the Admin page) is refused while a run
holds the slot, since the database file would be replaced under it. **Backup now**
is not.

The console asks for a [backup](database.md#backups) after any request that could
have written, including the POST that starts a job (even a dry run), and a
non-dry-run job asks for one again when it ends.

## Run log

Every command the console launches is recorded in `console-runs/`, a folder beside
the database file (the folder that holds `DATABASE_PATH`; by default
`~/dbs/console-runs/`). Each run has its own folder with:

- `record.json`: the run's id, section, label, argument vector, working directory,
  start and end times, duration, status, exit code, time limit, whether it was a dry
  run, and any error. It never holds the environment.
- `stdout.txt` and `stderr.txt`: the output, kept whole, including the partial
  output of a run that timed out.

The status is `ok`, `failed` (non-zero exit), `timeout`, `crashed` (it could not
start, or raised something unexpected), `running` or `interrupted`.

The newest 200 runs are kept; set `CONSOLE_RUN_LOG_KEEP` to change that (a value that
is not a whole number of at least 1 means 200). The folder is not in git and not in
the off-machine backup.

`/runs` (**Runs**) lists the runs newest first; `?section=<slug>` keeps one section's
(`?section=other` the runs with no section, such as the export and the database
rebuild). `/runs/<id>` shows one run with its output. The pages are read-only. Backup
and Restore run inside the console, not as commands, so they are not in the log.

A record still marked `running` when the console starts is marked **interrupted**:
the console stopped while the run was in progress. Its command may have been stopped
or may have finished, and may still be writing the database, so check the output and
the database before running again.

## Model runs and the site files

Unticking **Export when done** skips the site export, but a model run can still
change a file in the repository, because the runners write some of them themselves:

- A Westminster model run writes `electionmaps/data/results/model_output_trends.json`,
  and `model_output_trends_meta.json` beside it when the run is the latest one.
- A Holyrood model run writes `electionmaps/data/results/holyrood-prediction.json`
  and its meta file itself, and publishes `electionmaps/data/results/holyrood-trends.json`.
  The export only keeps Holyrood's manifest entry, so there is no Holyrood forecast
  to export (see [Export](export.md#filtered-exports)).
- The US runners write their poll-tracker trend JSON themselves, and a history
  rebuild rewrites it.

A dry run writes none of these and never exports, with one exception: a Westminster
dry run with an Output CSV chosen still writes that CSV and its `_regional_diffs`
sibling, under `models/westminster/output/` (relative to `data/`).

## Debug mode

The Werkzeug debugger and reloader are off. `CONSOLE_DEBUG=1` turns them on (see
[Run local server](../README.md#run-local-server)). A reload discards an in-flight
catch-up queue and marks a running job interrupted.

## Limits

- A stopped job (a timeout or a crash) does not stop processes its script started.
- A `CONSOLE_DEBUG=1` reload marks a running job interrupted.
- Restore is refused while a run is going.

# Data pipeline

Python election-data pipeline: local setup and everyday operation. Everything else is by topic:

- [Database, backups and rebuild](docs/database.md)
- [Models: UNS retrospective, input and output policies, history refresh](docs/models.md)
- [Static election-map export](docs/export.md)
- [US polls and forecasts](docs/us-polls-and-forecasts.md)
- [US results from NYT](docs/us-results.md)
- [Westminster uncertainty validation](docs/westminster-uncertainty.md)

## Layout

| Path | What it is |
|-|-|
| `core/` | Shared modules: `schema.py` (SQLAlchemy tables), `db.py` (database access), `config.py` (settings; loads the repo-root `.env`), `backup.py`, `electoral_votes.py`, plus the US geography names and the SQLite migration helpers |
| `console/` | Local web console for the data (`create_app`) |
| `server.py` | Local data preview server (`:5055`) |
| `archive/` | Finished one-offs and retired setup files (not maintained, not type-checked; see its README) |
| `inputs/` | Committed source data files per jurisdiction (`westminster/`, `holyrood/`, `us/`) read by the importers |
| `importers/` | `seed/` (parties, region populations), `results/` (Westminster, Holyrood and US elections, boundaries, by-elections; US has `nyt/` for the New York Times fetch-and-convert pipeline and `geometry/` for the TopoJSON builders) and `polls/` (Wikipedia-driven poll importers, `registry.py` lists the Westminster pollsters) |
| `models/` | Election models — `westminster/` (runner, uncertainty calibration and validation, with their JSON artefacts), `holyrood/` (UNS retrospective), `us/` (House, President, Senate forecasts) and `support/` (the simulation, persistence, trends and uncertainty code they share) |
| `export/` | The static export: `export_elections.py` partitions elections by parliament into the per-page data dirs (`electionmaps/data`, `uselectionmaps/data`), with its helpers beside it |
| `scripts/` | Database rebuild, model-trend rebuild and backup |
| `tests/` | Pytest suite — run with `./run_tests.sh` (strict mypy, then pytest) |

## Prerequisites

- Linux/macOS shell
- Python 3.10+
- `sqlite3` CLI (for inspection)
- Network access (poll importers fetch remote PDFs/XLSX/HTML)

## Python environment

From `data/`, where the data subsystem's commands below also run:

```bash
python3 -m venv election_data
source election_data/bin/activate
pip install -r requirements.txt
pip install --no-deps -e . --config-settings editable_mode=compat
```

The last line is an editable install of `data/` itself (see `pyproject.toml`). **It
is required once per virtualenv:** nothing in `data/` adjusts `sys.path` any more, so
a script run by path (`python export/export_elections.py`, `scripts/backup_db.py`, and the
scripts the console launches as subprocesses) fails with `ModuleNotFoundError: core`
without it. The console and the tests started from `data/` happen to import without it,
because the working directory or pytest's rootdir is on the path, but don't rely on
that. It is a one-time step and survives later pulls; nothing is built or copied, and
new top-level folders under `data/` need no reinstall.

If `election_data` already exists, activate it. A venv created before the editable
install was added needs the install once:

```bash
source election_data/bin/activate
pip install --no-deps -e . --config-settings editable_mode=compat
```

The hourly restic job (`~/.config/restic/backup.sh`) runs `data/scripts/backup_db.py` with
the main checkout's `data/election_data` interpreter, so that venv needs the install too.

## How imports work

Every module is imported by its package path (`core.db`, `importers.polls...`,
`models.support...`, `export.payload`), never by a bare file name. The editable
install puts `data/` on the import path for any working directory, which gives two
equivalent ways to run a script. Both examples below run from `data/`; with the
install, the by-path form also works from any other directory (give the interpreter
and script paths in full):

```bash
./election_data/bin/python export/export_elections.py --dry-run       # by path
./election_data/bin/python -m export.export_elections --dry-run       # as a module
```

Code and tests must not add to `sys.path`; a test fails if they do (`archive/` is exempt,
and its scripts are not maintained).

## Import polls (Wikipedia-driven)

Start the console ([Run local server](#run-local-server)), then open [Import Poll](http://127.0.0.1:5055/import)
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

## Run local server

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

## Quick validation queries

From `data/`:

```bash
sqlite3 "$DATABASE_PATH" "SELECT count(*) FROM maps;"
sqlite3 "$DATABASE_PATH" "SELECT count(*) FROM elections;"
sqlite3 "$DATABASE_PATH" "SELECT count(*) FROM polls;"
sqlite3 "$DATABASE_PATH" "SELECT type, count(*) FROM elections GROUP BY type ORDER BY type;"
```

## Common troubleshooting

- **Wrong database / empty results**
	- Ensure `DATABASE_PATH` points at the intended `elections.db` file (see `.env`).

- **Database is locked**
	- SQLite uses WAL journaling; make sure no other writer (e.g. a model run)
	  is mid-transaction, and never open the live DB off a network or synced mount.

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

# Data pipeline

Python election-data pipeline: local setup and everyday operation. Everything else is by topic:

- [The data console](docs/console.md), including importing polls
- [Database, backups and rebuild](docs/database.md), including quick checks and troubleshooting
- [Models: UNS retrospective, input and output policies, history refresh](docs/models.md)
- [Static election-map export](docs/export.md)
- [US polls and forecasts](docs/us-polls-and-forecasts.md)
- [US results from NYT](docs/us-results.md)
- [Westminster uncertainty validation](docs/westminster-uncertainty.md)

## Layout

| Path | What it is |
|-|-|
| `core/` | Shared modules: `schema.py` (SQLAlchemy tables), `db.py` (database access), `config.py` (settings; loads the repo-root `.env`), `backup.py`, `electoral_votes.py`, plus the US geography names and the SQLite migration helpers |
| `console/` | Local web console for the data (`create_app`): five sections (Westminster, Holyrood, US House, US Senate, US President), the Export page, the Runs log and Admin; see [the console](docs/console.md) |
| `server.py` | Local data preview server (`:5055`) |
| `archive/` | Finished one-offs and retired setup files (not maintained, not type-checked; see its README) |
| `inputs/` | Committed source data files per jurisdiction (`westminster/`, `holyrood/`, `us/`) read by the importers |
| `importers/` | `seed/` (parties, region populations), `results/` (Westminster, Holyrood and US elections, boundaries, by-elections; US has `nyt/` for the New York Times fetch-and-convert pipeline and `geometry/` for the TopoJSON builders) and `polls/` (Wikipedia-driven poll importers, `registry.py` lists the Westminster pollsters) |
| `models/` | Election models — `westminster/` (runner, uncertainty calibration and validation, with their JSON artefacts), `holyrood/` (UNS retrospective), `us/` (House, President, Senate forecasts) and `support/` (the simulation, persistence, trends and uncertainty code they share) |
| `export/` | The static export: `export_elections.py` partitions elections by parliament into the per-page data dirs (`electionmaps/data`, `uselectionmaps/data`), with its helpers beside it |
| `scripts/` | Database rebuild, model-trend rebuild and backup |
| `tests/` | Pytest suite — run with `./run_tests.sh` (strict mypy, then pytest) |

## Prerequisites

A Linux or macOS shell, Python 3.12+, the `sqlite3` CLI (for inspection), and network
access (the poll importers fetch remote PDFs, XLSX and HTML).

## Python environment

From `data/`, where the data subsystem's commands below also run:

```bash
python3 -m venv election_data
source election_data/bin/activate
pip install -r requirements.txt
pip install --no-deps -e . --config-settings editable_mode=compat
```

`requirements.txt` also installs the lint tools `../lint.sh` uses (ruff and
shellcheck). An existing venv picks up new requirements with
`./election_data/bin/pip install -r requirements.txt`.

The last line is an editable install of `data/` itself (see `pyproject.toml`). **It
is required once per virtualenv:** nothing in `data/` adjusts `sys.path`, so a script
run by path (`python export/export_elections.py`, `scripts/backup_db.py`, and the
scripts the console launches as subprocesses) fails with `ModuleNotFoundError: core`
without it. The console and the tests started from `data/` happen to import without
it, but don't rely on that. It survives later pulls, and new top-level folders under
`data/` need no reinstall. A venv that lacks it needs it once:

```bash
source election_data/bin/activate
pip install --no-deps -e . --config-settings editable_mode=compat
```

The hourly restic job (`~/.config/restic/backup.sh`) runs `data/scripts/backup_db.py` with
the main checkout's `data/election_data` interpreter, so that venv needs the install too.

Settings come from `.env` at the repo root (copy `.env_example`). It beats the shell:
see [the database page](docs/database.md) before pointing a script at another database.

| Variable | Default | What it sets |
|-|-|-|
| `DATABASE_PATH` | `~/dbs/elections.db` | The live SQLite database; keep it on local disk |
| `SIMULATIONS_PATH` | `~/dbs/simulations/westminster-latest.npz` | Latest Westminster Monte Carlo draws |
| `ELECTIONS_ARCHIVE_DIR` | `~/dbs/elections` | Local database backups |
| `ELECTIONS_BACKUP_KEEP` | `30` | How many local backups to keep |
| `NYT_CACHE_DIR` | `~/dbs/nyt-cache` | Downloaded NYT results |

## How imports work

Every module is imported by its package path (`core.db`, `importers.polls...`,
`models.support...`, `export.payload`), never by a bare file name. The editable
install puts `data/` on the import path for any working directory, so a script runs
either way (from `data/` here; by path it also works from anywhere, given full paths):

```bash
./election_data/bin/python export/export_elections.py --dry-run       # by path
./election_data/bin/python -m export.export_elections --dry-run       # as a module
```

Code and tests must not add to `sys.path`; a test fails if they do (`archive/` is exempt,
and its scripts are not maintained).

## Run local server

From `data/`:

```bash
./election_data/bin/python server.py
```

The console serves `http://127.0.0.1:5055/`. It has five sections (Westminster,
Holyrood, US House, US Senate, US President), each with its polls, imports, model run
and outputs, plus **Export**, **Runs** (a log of every command it launched) and
**Admin** (rebuild, backup, restore). Model runs, the export and the rebuild run in the
background, one at a time; [the console](docs/console.md) has the details, including
how to import polls.

It answers only `127.0.0.1` / `localhost`, and refuses a POST made from another
site's page (`data/console/csrf.py`). The Werkzeug debugger and auto-reloader are off
by default, because the debugger runs arbitrary code for anything that can reach the
port. For reload-on-save while developing:

```bash
CONSOLE_DEBUG=1 ./election_data/bin/python server.py
```

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
| `models.py`, `db.py`, `config.py` | SQLAlchemy schema, DB access, configuration |
| `console/` | Local web console for the data (`create_app`) |
| `server.py` | Local data preview server (`:5055`) |
| `archive/` | Finished one-offs and retired setup files (not maintained, not type-checked; see its README) |
| `old_data/` | Base-data importers (TopoJSON maps, parties, general elections) |
| `polls/` | Wikipedia-driven poll importers |
| `models/` | Election models — `westminster/`, `holyrood/` (UNS retrospective), `us/` (House, President, Senate forecasts) |
| `scripts/` | Static export scripts that partition elections by parliament into the per-page data dirs (`electionmaps/data`, `uselectionmaps/data`) |
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

The last line is an editable install of `data/` itself (see `pyproject.toml`). It
puts `data/` on the import path from any working directory; nothing is built or
copied. New top-level folders under `data/` need no reinstall. Scripts are being
moved over to rely on it instead of their own `sys.path` adjustments, so install
it even where a script still works without.

If `election_data` already exists, activate it. A venv created before the editable
install was added needs the install once:

```bash
source election_data/bin/activate
pip install --no-deps -e . --config-settings editable_mode=compat
```

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

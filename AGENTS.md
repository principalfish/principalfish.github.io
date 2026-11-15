# principalfish.github.io

A GitHub Pages static site with a Python data pipeline. [README.md](README.md) has the
overview and setup; each area's README holds its detail, and
[data/README.md](data/README.md) leads to the data runbook.

## Checks

Run from the repo root, lint first:

    ./lint.sh                   # ruff, Biome, shellcheck; ./lint.sh --fix applies fixes first
    cd data && ./run_tests.sh   # strict mypy (gates the run), then pytest
    npm test                    # Vitest

`run_tests.sh` forwards extra args to pytest after `tests/`, so they filter the whole
suite (a file path doesn't narrow it). One file, or a single step, runs
with the venv interpreter directly:

    cd data && ./run_tests.sh -k model_outputs -q                       # by keyword
    cd data && ./election_data/bin/python -m pytest tests/test_export_payload.py -q   # one file
    cd data && ./election_data/bin/python -m mypy             # type-check only (data/mypy.ini, strict)
    cd data && ./election_data/bin/python -m pytest tests/ -q # tests only

Quick smoke checks (no test runner):

    cd data && ./election_data/bin/python -c "from console import create_app; create_app()"   # console app builds
    cd data && ./election_data/bin/python export/export_elections.py --dry-run                 # export plan, writes nothing
    cd data && ./election_data/bin/python export/export_elections.py --output-base /tmp/x      # real export into /tmp/x/<page>/data, repo untouched

`--output-root` applies only to `--current-simulation`; use `--output-base` for any
scratch export of the full site.

## Rules

- **The data venv needs the one-time editable install** ([data/README.md](data/README.md#python-environment)).
  No code or test adds to `sys.path`, so scripts run by path fail without it.
- **Never write to the live database.** `.env` is loaded with override, so setting
  `DATABASE_PATH` in the shell does not redirect a script: it still opens the live
  database. Tests are safe: each gets a fresh temporary SQLite database (shared
  fixtures in `data/tests/conftest.py`).
- **Rebuild the bundles after editing `electionmapslogic/`** (`npm run minify:electionmaps`);
  both map pages share the engine, so both bundles regenerate, and CI fails on a stale one.
- **`map-modes.json` is generated.** Edit `map-modes-shell.json` (`parliamentFeatures` is
  copied through verbatim) and re-export: `cd data && ./election_data/bin/python export/export_elections.py`.
- **There is no browser on this machine.** To check the maps engine, drive it under jsdom as
  [the engine README](electionmapslogic/README.md#driving-the-pages-headlessly) describes;
  its stubs and traps have each bitten before.
- **A lint suppression carries its reason** (`# noqa: <code> - <why>`,
  `// biome-ignore <rule>: <why>`); there are no per-file ignores or file-wide suppressions.

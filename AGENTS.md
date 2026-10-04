# principalfish.github.io

A GitHub Pages static site with a Python data pipeline. Read
[README.md](README.md) for the repository structure and setup/runbook.

## Testing

The Python data pipeline and console are tested under `data/`. The test commands
use the `data/election_data` virtualenv (falling back to `data/.venv`).

Run the full check suite — strict `mypy` first (it gates the run), then `pytest`:

    cd data && ./run_tests.sh

`run_tests.sh` forwards extra args to pytest, so you can narrow a run:

    cd data && ./run_tests.sh tests/test_export_payload.py    # one file
    cd data && ./run_tests.sh -k model_outputs -q             # by keyword

Run a single step with the venv interpreter directly:

    cd data && ./election_data/bin/python -m mypy             # type-check only (data/mypy.ini, strict)
    cd data && ./election_data/bin/python -m pytest tests/ -q # tests only

Tests live in `data/tests/`; shared DB fixtures are in `data/tests/conftest.py`
(each test gets a fresh temporary SQLite database, so real data is never touched).

Quick smoke checks (no test runner):

    cd data && ./election_data/bin/python -c "from console import create_app; create_app()"   # console app builds
    cd data && ./election_data/bin/python scripts/export_elections.py --dry-run                # export plan, writes nothing

## Running the front end

Build the assets and serve on port 8000:

    ./server.sh

After editing anything under `electionmapslogic/`, rebuild the bundles or the served
pages keep the old logic (both pages share the engine, so both are regenerated):

    npm run minify:electionmaps

`map-modes.json` is generated. Edit `map-modes-shell.json` (the hand-authored source of
truth) and re-export — `parliamentFeatures` is copied through verbatim:

    cd data && ./election_data/bin/python scripts/export_elections.py

### Driving the maps page headlessly

`server.sh`'s server does not survive the shell that spawned it, so for an unattended
run start one detached instead:

    (setsid nohup python3 -m http.server 8000 --bind 127.0.0.1 &)

There is no browser on this machine — Playwright's `chromium-headless-shell` installs
but fails on a missing `libatk-1.0.so.0`, and installing the deps needs sudo. To
exercise the real modules without one, drive them under `jsdom` from a scratch dir:

- **Pre-bundle with esbuild.** This repo's `package.json` has no `"type": "module"`, so
  Node loads `electionmapslogic/*.js` as CJS and their `import`s throw.
- **Inject `electionmapslogic/shell.html`** into the jsdom document before importing the
  bundle. `shell-loader.js` does this at runtime and `#mapsElectionList` /
  `#mapsElectionCountdown` live there — without it `renderLeftBar()` silently renders an
  empty nav, which reads as a regression but is a harness bug.
- **Pin `jsdom@24`** (later majors throw `ERR_REQUIRE_ESM` on Node 21), set `navigator`
  via `Object.defineProperty` (it is getter-only), and stub `ResizeObserver` /
  `IntersectionObserver`.

Check a second parliament (e.g. Westminster) alongside the one you changed — it catches
both harness bugs and accidental cross-page regressions from the shared engine.

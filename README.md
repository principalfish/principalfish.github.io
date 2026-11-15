# principalfish.github.io

The GitHub Pages site for **principalfish.co.uk**: UK and US election maps with
forecasts and poll trackers, a Referdle solver, a route-weather planner, a "guess the
year" game and a bio page. A Python pipeline in `data/` keeps one SQLite database of
results and polls, runs the election models, and exports the maps' data files.

## Set up

Needs Node.js 22 with npm, Python 3.12+ and the `sqlite3` CLI. From a fresh clone:

```bash
npm ci
cd data && python3 -m venv election_data && ./election_data/bin/pip install -r requirements.txt
./election_data/bin/pip install --no-deps -e . --config-settings editable_mode=compat
cd .. && cp .env_example .env    # then edit the paths in .env
```

The editable install is needed once per virtualenv, and `.env` sets where the
database and backups live: both are explained in [data/README.md](data/README.md).
To have `git blame` skip the one-off formatting commits:

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

## Develop

```bash
./server.sh                                       # build the front end and serve on :8000
cd data && ./election_data/bin/python server.py   # data console on 127.0.0.1:5055
```

Every check, from the repo root:

```bash
./lint.sh                  # ruff, Biome and shellcheck (--fix applies the fixes first)
cd data && ./run_tests.sh  # strict mypy, then pytest
npm test                   # front-end tests (Vitest)
```

After editing anything under `electionmapslogic/`, rebuild the bundles with
`npm run minify:electionmaps` (`./server.sh` also does this).

## Ship

GitHub Pages serves `master` as committed: there is no build at deploy, so the
minified bundles, vendored libraries and exported map data are all committed. The
data console's **Export** page, or `export/export_elections.py`, writes the maps'
data files into the repo; commit and push them to publish. CI
(`.github/workflows/ci.yml`) runs `./lint.sh`, the Python suite and Vitest, and fails
if a committed bundle is out of date with its source.

## More

- [data/README.md](data/README.md): the data pipeline's setup, layout and console,
  with a page per topic (database, models, export, US polls and results)
- [electionmaps/README.md](electionmaps/README.md): the UK maps page
- [uselectionmaps/README.md](uselectionmaps/README.md): the US maps page
- [electionmapslogic/README.md](electionmapslogic/README.md): the shared maps engine,
  manifest contract and results schema
- [referdle-solver/README.md](referdle-solver/README.md): the Referdle solver and its
  benchmark
- [routeweather/README.md](routeweather/README.md): the route-weather planner
- [guesstheyear/README.md](guesstheyear/README.md): the "guess the year" game and its
  challenge builder
- [site/README.md](site/README.md): shared styles, top bar, vendored libraries, the
  landing and bio pages, and the local preview server
- [AGENTS.md](AGENTS.md): conventions and traps for coding agents

# principalfish.github.io

Election maps: a GitHub Pages static site for principalfish.co.uk, plus the Python data pipeline that powers its interactive UK and US election maps, forecasts and poll trackers.

**Languages:** python, javascript, html, css, shell · **Base branch:** master

**Components:** electionmaps, uselectionmaps, electionmapslogic, guesstheyear, referdle-solver, site, data, bio

**Checks:** `cd data && ./run_tests.sh` (strict mypy gates the run, then pytest; forwards args, e.g. `-k <kw>` or a test path) · `npm test` (Vitest) · `npm run minify:electionmaps` after front-end changes · `git diff --check`

**Commit style:** conventional commits, `feat:` / `fix:` / `test:` / `docs:` / `chore:` / `data:` + an imperative summary, e.g. `feat: add presidential map uncertainty and electoral vote tracker`.

## Instructions

- See the in-repo `CLAUDE.md` / `AGENTS.md` (loaded automatically) and `README.md` for the full structure and runbook.
- **Two subsystems:**
  - The **front end** (repo root): built and served with `./server.sh` (esbuild/terser/clean-css, port 8000).
  - The **Python pipeline** (`data/`): SQLAlchemy models, importers, election models, static exports, a local console. It uses the `data/election_data` venv, falling back to `data/.venv`.
- **Database:** one local SQLite file, `DATABASE_PATH` (default `/home/philiph/dbs/elections.db`). `.env` is loaded with override, so it beats the shell. Tests use a fresh temporary DB per test, so real data is never touched.
- **Live operations are never implied** by approval of a piece: database writes, imports, backfills, model reruns, exports into the repo, publication. Each needs Phil's explicit go-ahead at the time.
- Exported data files (e.g. `model_output_trends.json`) are tracked. Stage only the files a piece owns.

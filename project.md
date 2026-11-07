# principalfish.github.io

Election maps: a GitHub Pages static site for principalfish.co.uk, plus the Python data pipeline that powers its interactive UK and US election maps, forecasts and poll trackers.

**Languages:** python, javascript, html, css, shell · **Base branch:** master

**Components:** electionmaps, uselectionmaps, electionmapslogic, guesstheyear, referdle-solver, site, data, bio

**Checks:** `cd data && ./run_tests.sh` (strict mypy gates the run, then pytest; forwards args, e.g. `-k <kw>` or a test path) · `npm test` (Vitest) · `npm run minify:electionmaps` after front-end changes · `git diff --check`

**Worktree setup:** `scripts/setup-worktree.sh` (creates the `data/election_data` venv from `data/requirements.txt`, runs `npm ci`, copies `.env` from the main checkout if there is one; safe to re-run). The database is outside the repo (`DATABASE_PATH`, default `/home/philiph/dbs/elections.db`) and shared by every worktree: a builder must not write to it unless the plan says so.

**Coverage:** `scripts/coverage-changed.sh [<base>]` (default `origin/master`): runs pytest under coverage and Vitest with coverage, then `diff-cover` lists the changed lines no test runs, including uncommitted changes. Takes about 2–3 minutes; exits non-zero only when a test run fails.

**Commit style:** conventional commits, `feat:` / `fix:` / `test:` / `docs:` / `chore:` / `data:` + an imperative summary, e.g. `feat: add presidential map uncertainty and electoral vote tracker`. Builder and quick-build commits put their source in the scope, per `git.md`: `feat(<slug>): piece 3/5 — <summary>`, `fix(<slug>): review fixes — <summary>`, `chore(quick-build): <summary>`, then bullets.

## Instructions

- See the in-repo `CLAUDE.md` / `AGENTS.md` (loaded automatically) and `README.md` for the full structure and runbook.
- **Two subsystems:**
  - The **front end** (repo root): built and served with `./server.sh` (esbuild/terser/clean-css, port 8000).
  - The **Python pipeline** (`data/`): SQLAlchemy models, importers, election models, static exports, a local console. It uses the `data/election_data` venv, falling back to `data/.venv`.
- **Database:** one local SQLite file, `DATABASE_PATH` (default `/home/philiph/dbs/elections.db`). `.env` is loaded with override, so it beats the shell. Tests use a fresh temporary DB per test, so real data is never touched.
- **Live operations are never implied** by approval of a piece: database writes, imports, backfills, model reruns, exports into the repo, publication. Each needs Phil's explicit go-ahead at the time.
- Exported data files (e.g. `model_output_trends.json`) are tracked. Stage only the files a piece owns.

# chore: add lint and format checks for Python, JavaScript and shell

Formatting, naming and coding idioms were checked by review, and most review noise came from them. This hands that job to tools:

- **ruff** for Python under `data/` and `guesstheyear/`. It checks the default rules plus naming (N), pathlib (PTH) and comprehension (C4) rules, and `ruff format` formats at width 88.
- **Biome** for every tracked JavaScript file. It checks the recommended rules and formats with 2-space indents, double quotes and width 100. Warnings fail too.
- **shellcheck** for the tracked shell scripts outside `data/archive/`.

Every existing finding is fixed, so the checks gate from the day they land. The tools are pinned: ruff 0.17.5, Biome 2.6.7 and shellcheck-py 0.11.0.1. Strict mypy is unchanged.

## One command

`./check.sh` at the repo root runs the four lint steps, then strict mypy with pytest (`data/run_tests.sh`), then Vitest. Every step runs even when an earlier one fails, and the failed steps are named at the end. Ctrl-C stops the whole run.

- `--lint-only` runs just the lint steps; CI's new `lint` job uses it.
- `--fix` applies the safe fixes and formatting first.
- `-- ARGS` passes ARGS to pytest.

CI's existing Python and front-end jobs still run the suites.

## The reformat

The one-off reformat is one formatting-only commit per language:

- `5fb62b38`, Python. It was proven same-meaning by comparing each file's syntax tree before and after, with docstrings normalised. The one hand edit: 6 `type: ignore`/`noqa` comments moved back onto the lines they apply to.
- `36dfaf34`, JavaScript. It was proven same-meaning by minifying each file before and after. The one hand edit: a test that patches source text now matches it with a whitespace-tolerant regex.

Both commits are in `.git-blame-ignore-revs`. **Merge with a merge commit, not a squash or rebase,** or those SHAs won't exist on `master`. Locally, `git config blame.ignoreRevsFile .git-blame-ignore-revs` makes `git blame` skip them.

## Fixes that change behaviour

Most fixes are mechanical. These are not:

- **Poll importers:** catch-all `except`s in the four importers' fetch loops (BMG, Focaldata, Techne, Deltapoll) now catch only network and parse errors (`OSError`, `HTTPException`, `ValueError`). Any other error while fetching now shows as "Import preview failed: …" instead of "Could not fetch …".
- **Deltapoll's regional table:** an unexpected error parsing it now fails the import instead of falling back to national figures.
- **Exception renames:** `JobBusy` became `JobBusyError` and `TrackedMatchupMissing` became `TrackedMatchupMissingError`.
- **Dead code removed:**
  - the never-used `turnout_pct` in `export/payload.py`;
  - in guesstheyear, `getDisplayName`, `addFeedbackUI` and `displayDate`;
  - in referdle, `parseClue`, `codeFor`, `setCode` and a duplicate `LOOKAHEAD_MAX`.

Twelve console error boundaries and guesstheyear's scraper keep their catch-all `except`s, each with a reason. Every suppression in the code carries its reason, and there are no per-file ignores. The only rule-wide ignore is ruff's EXE001/EXE002. Ruff skips those rules on WSL, so they could only ever fail in CI, and the scripts are run with the venv's Python, not by their shebang.

Ruff's own unsafe `dict()` rewrite broke every export at runtime. It was caught by comparing full exports in a scratch folder and fixed, and a test now covers it. A full export into a scratch folder is byte-identical to `master`'s.

## Other changes

- `requires-python` is now `>=3.12`, matching mypy and CI.
- Shared test fixtures are registered with `pytest_plugins`.
- There are 14 new tests, 7,424 pytest tests in all.
- **Docs:**
  - The README, `AGENTS.md` and `data/README.md` are rewritten as short front doors.
  - Their detail moved to `data/docs/console.md`, `data/docs/database.md` and the area READMEs.
  - guesstheyear gets a README.
  - `CLAUDE.md` is now the single line `@AGENTS.md`.

## Review

Each piece had its own review round: correctness, security and coverage, plus docs where the piece touched docs, then a verifier and a light re-check.

A whole-branch self review then found three things, all fixed:

- `biome ci` passed on warnings, so they couldn't fail the run; it now uses `--error-on-warnings`.
- The `archive/` row in `data/README.md` was inaccurate.
- `CLAUDE.md` was three lines instead of one.

`./check.sh` came after that review and had its own round. That round fixed Ctrl-C starting the next step, and `--lint-only` silently ignoring arguments given after `--`.

## Check by hand

- **The pages:** a browser click-through from the branch (`PORT=8001 ./server.sh`). Only jsdom drives have been run: 52 map routes and a guesstheyear round, all identical to `master`.
  - `/electionmaps/`: switch elections and modes, the predict panel, the poll tracker, postcode search.
  - `/uselectionmaps/`: President, Senate and House; predict; hovering a state.
  - `/guesstheyear/`: a round, history, settings, clearing data.
  - `/referdle-solver/`: auto-solve and manual mode.
  - `/routeweather/`: a GPX loaded by picker and by drag-and-drop; units; the timeline.
- **A poll import** through the console, for the narrowed error handling.
- **An existing venv** needs `./data/election_data/bin/pip install -r data/requirements.txt` before `./check.sh` will run.

Closes #86

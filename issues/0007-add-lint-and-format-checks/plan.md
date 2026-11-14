# Plan: add-lint-and-format-checks

**Build model:** claude-opus-5-5 (frontier). The lint fixes need judgment about keeping behaviour; the config and formatting pieces are mechanical and say so.

## Context

The review's style check became docs-only on 2026-11-14, so formatting, naming and idioms now have to come from each repo's linters, run as project checks. This repo has none: strict mypy plus pytest under `data/` (`data/run_tests.sh`), Vitest (`npm test`) and CI's built-asset sync check (`.github/workflows/ci.yml`) are its only checks.

Measured on `master` at `aa033121` (scratch installs; nothing in the repo touched):
- Python: 309 files under `data/` excluding `archive/` (already outside mypy, `data/mypy.ini`). ruff 0.17.5 defaults (ruff 0.17's default set is about 400 rules, already including B904, TRY002, B006, BLE001, S110 and the f-string and `UP` rules): 659 findings, 469 in `tests/`. F811 ×283 is one pattern: the fixtures `app` and friends imported from `tests/console_fixtures.py` (3 fixtures) and taken as test parameters (`tests/test_console_by_elections.py:26-34`). Then ISC004 ×83, I001 ×41, F401 ×30, RUF059 ×20, UP045 ×18, BLE001 ×17, UP017 ×15, RUF012 ×15, RUF046 ×14, PLW1510 ×13, SIM117 ×11, UP037 ×11, B017 ×10, then a long tail; 146 safe autofixes. Outside `tests/`: importers 68, console 48, models 27, core 27, export 19, scripts 1.
- Extras over the defaults: `N` +4 (e.g. `console/shared/jobs.py:177` `JobBusy`, `export/export_elections.py:1916`), `PTH` +14 (e.g. `console/shared/run_log.py:409`, `models/support/io.py:82`), `C4` +21.
- `ruff format` at 88: 151 of 309 files change, +9,401/−3,406.
- `guesstheyear/*.py` (3 files): 6 findings, 3 files to reformat.
- JS: 139 tracked source files (not `*.min.js`, not `vendor/`), 138 of them 2-space. Quotes: `electionmapslogic` mostly single (4,235 ' vs 1,914 "), every other area double.
- Biome 2.6.7 recommended: 137 diagnostics (40 errors, 85 warnings, 12 infos): useIterableCallbackReturn 37, useOptionalChain 36, noUnusedVariables 18, useTemplate 11, noUnusedFunctionParameters 11, useDomNodeTextContent 8, useConst 4, noUnusedImports 4, then singles. By area: referdle-solver 53, guesstheyear 35, routeweather 26, electionmapslogic 19, site 2, data 2.
- Biome format (2-space, double quotes, semicolons, trailing commas, width 100): 128 files, +13.8k/−6.9k.
- Shell: `server.sh`, `data/run_tests.sh`, `data/archive/import_all.sh`; shellcheck 0.11 is clean on all three.

Settled decisions (all 2026-11-14):
- Strict mypy keeps gating `data/run_tests.sh`; its settings stay in `data/mypy.ini`.
- Python: ruff's defaults plus `N`, `PTH` and `C4`, with every finding fixed in this issue (no blanket per-rule ignores). The F811 fixture pattern is fixed by registering `tests/console_fixtures.py` with pytest (`pytest_plugins` in `tests/conftest.py`) and dropping the imports, not by ignoring F811.
- `guesstheyear/*.py` is linted and formatted too, using `data/pyproject.toml`'s settings through a small extending config.
- JavaScript: Biome, line width 100, 2-space indent, double quotes (`electionmapslogic` switches from single), semicolons always, trailing commas everywhere; recommended lint rules, findings fixed here. It covers every tracked JS source file: electionmapslogic, electionmaps, uselectionmaps, site, data/console/static and static-tests, guesstheyear, referdle-solver, routeweather. Not `*.min.js`, not `vendor/`.
- Shell: shellcheck only; no shfmt, no optional shellcheck checks.
- A root `./lint.sh` runs ruff check, ruff format --check, `biome ci` and shellcheck; `--fix` applies fixes and formatting. It becomes the first item on `project.md`'s Checks line, the other items stay; `ci.yml` gets a lint job.
- Formatting lands as one formatting-only commit per language (Python: `ruff format`; JS: `biome format` plus the `.min` bundle rebuild), no hand edits in either, both SHAs in a new `.git-blame-ignore-revs`. The PR merges with a merge commit (as this repo's PRs already do), not squashed, or the SHAs won't exist on `master`.
- Pins: `ruff==0.17.5` and `shellcheck-py==0.11.0.1` (shellcheck 0.11) in `data/requirements.txt` beside `mypy==2.1.0`; `@biomejs/biome` exactly `2.6.7` in `package.json` devDependencies.

Assumed, confirmed with the ledger:
- Python line width 88 (house guide; least churn); `target-version = "py312"` (mypy and CI use 3.12); ruff settings in `data/pyproject.toml`; `data/archive/` excluded.
- Only the lint tools are pinned; runtime dependencies stay as they are.
- Biome formats JS only (not CSS, HTML or JSON).
- `lint.sh` finds the venv as `run_tests.sh` does (`data/election_data`, then `data/.venv`) and says how to install the tools if they're missing; `.ruff_cache/` is gitignored.
- Lint fixes keep behaviour. Genuine top-level boundaries that log keep their `except Exception` (ruff exempts handlers that call `logger.exception`; otherwise a `noqa` with a one-line reason). Where a fix would change what a user or a caller sees, the builder stops and asks.
- README/AGENTS.md gain the lint command. After the merge, the planner chat updates `project.md`'s Checks line.
- The house-guide mismatches (`shell.md` forbids a global `set -euo pipefail`, which both live scripts use; `javascript.md` assumes no bundler) are fixed in `~/.claude`, not here.

Shared state never to write: the live database (`DATABASE_PATH`, default `/home/philiph/dbs/elections.db`). No piece runs an import, a model or an export into the repo. `data/run_tests.sh` rewrites the tracked `model_output_trends.json`, so stage files explicitly, never `git add -A`.

## Approach

Config first, then one mechanical reformat per language, then the lint fixes area by area, then the gate (`lint.sh`, CI, docs). The existing checks pass after every piece; nothing new gates until piece 7, so no piece leaves a red check.

Rejected: gating first with per-rule ignores to burn down later (the ignores tend to stay); formatting only touched code (the format check couldn't gate for years); ESLint + Prettier (two tools, more config, Biome matches earth_sim); one combined formatting commit (harder to review); width 80 or 120 for JS (80 doubles the churn, 120 isn't the norm in the code today); `ANN401`, `T20`, `PLC0415`, `DTZ` (219, 440, 91 and 13 findings, mostly deliberate `Any`, CLI prints, deferred imports and local dates); Biome's naming rule (465 findings, mostly snake_case JSON keys); a root `check.sh` running the tests too (every lint run would wait for the full suite); shellcheck from the system (not installed here, apt needs sudo, unpinned).

## Pieces

1. Pin the tools and add their settings
   - Files: `data/requirements.txt`, `data/pyproject.toml`, `guesstheyear/ruff.toml` (new), `package.json`, `package-lock.json`, `biome.json` (new), `.gitignore`
   - What: add `ruff==0.17.5` and `shellcheck-py==0.11.0.1` to `data/requirements.txt` next to `mypy==2.1.0`. In `data/pyproject.toml` add `[tool.ruff]` with `target-version = "py312"`, `line-length = 88`, `extend-exclude = ["archive", "election_data", ".venv"]`, and `[tool.ruff.lint]` `extend-select = ["N", "PTH", "C4"]`. `guesstheyear/ruff.toml` is `extend = "../data/pyproject.toml"` (check ruff resolves it and that `data/`'s excludes don't hide guesstheyear). `npm install --save-dev --save-exact @biomejs/biome@2.6.7`. `biome.json`: schema 2.6.7, `vcs` enabled with `useIgnoreFile` so `.claude/`, `node_modules/` and caches are skipped, `files.includes` limited to `**/*.js` and `**/*.mjs` minus `**/*.min.js` and `**/vendor/**`; formatter 2-space, width 100; JS formatter double quotes, semicolons always, trailing commas all; linter recommended rules; CSS, JSON and HTML formatting and linting off. Add `.ruff_cache/` to `.gitignore`.
   - Functions: none
   - Depends on: none
   - Acceptance: `setup-worktree.sh` (re-run) installs ruff and shellcheck into the venv; `ruff check` from `data/` and from `guesstheyear/` pick up the settings (counts match the Context, with the 39 extra findings for N/PTH/C4); `npx biome ci` covers exactly the 139 files; `shellcheck server.sh data/run_tests.sh` is clean.
   - Tests: config only, nothing to test; `cd data && ./run_tests.sh` and `npm test` still pass.
   - Model: claude-sonnet-5-5

2. Reformat the Python once
   - Files: the Python files `ruff format` changes under `data/` (not `archive/`) and `guesstheyear/`
   - What: run `ruff format` and nothing else, as one formatting-only commit (`chore(add-lint-and-format-checks): piece 2/7 — format the Python with ruff`). No hand edits.
   - Functions: none
   - Depends on: 1
   - Acceptance: `ruff format --check` is clean in `data/` and `guesstheyear/`; every changed file is AST-identical to its parent (check with a short `ast.dump` comparison and say so in the review request).
   - Tests: no behaviour change; `./run_tests.sh` passes with the same test count as before the piece.
   - Model: claude-sonnet-5-5

3. Reformat the JavaScript once
   - Files: the JS files `biome format --write` changes; the `.min.js` bundles `npm run minify:electionmaps` regenerates
   - What: run `npx biome format --write .` and then `npm run minify:electionmaps`, and commit the result as one formatting-only commit. No hand edits. The bundles go in the same commit so CI's sync check stays green; they may well come out unchanged. Don't run this while `./server.sh` runs in this worktree (it watches the engine sources and rebuilds).
   - Functions: none
   - Depends on: 1
   - Acceptance: `npx biome format .` reports nothing to change; `npm run vendor:d3 && npm run minify:electionmaps` leaves `git diff` empty after the commit.
   - Tests: no behaviour change; `npm test` passes with the same test count.
   - Model: claude-sonnet-5-5

4. Fix the ruff findings in the tests
   - Files: `data/tests/**` (conftest.py, console_fixtures.py and the test files the findings name), `.git-blame-ignore-revs` (new)
   - What: make `ruff check` clean under `data/tests/` (469 findings in the Context). F811: register `tests/console_fixtures.py` with `pytest_plugins = ["tests.console_fixtures"]` in `tests/conftest.py` and drop the fixture names from the test imports (non-fixture helpers like `RecordingRunner` stay imported). If pytest rejects `pytest_plugins` there as a non-top-level conftest, define the three fixtures in `conftest.py` instead (moving them, not re-importing). Then the rest: safe autofixes first, hand fixes after (ISC004 by parenthesising, B017 by naming the exception the test expects, RUF059 by `_`-prefixing, and so on). Any `noqa` carries a one-line reason. Add `.git-blame-ignore-revs` listing pieces 2 and 3's SHAs, each with a comment saying what it is.
   - Functions: the fixtures in `tests/console_fixtures.py` and whatever the findings name
   - Depends on: 2
   - Acceptance: `ruff check tests` from `data/` is clean; no per-file or blanket ignores were added for `tests/`.
   - Tests: `./run_tests.sh` passes with the same test count as before the piece (a change means a test was lost or added; the review request says which); a B017 fix that narrows `pytest.raises(Exception)` must still pass, which proves the named exception is the one raised.

5. Fix the ruff findings in the pipeline code
   - Files: `data/console/**`, `data/core/**`, `data/export/**`, `data/importers/**`, `data/models/**`, `data/scripts/**`, `data/server.py`, `guesstheyear/*.py`
   - What: make `ruff check` clean everywhere else (190 default findings plus the N/PTH/C4 extras, and 6 in guesstheyear). Safe autofixes first, then hand fixes. Judgment calls:
     - BLE001 ×17 (e.g. `console/shared/run_log.py:628`, `console/westminster/by_elections.py:72`): keep `except Exception` only at a real boundary that logs (ruff exempts `logger.exception`; otherwise `noqa: BLE001` with the reason), else narrow to what the call can raise.
     - PLW1510 ×13 (e.g. `console/shared/runner.py:62`): add `check=False` where the code reads the return code itself, `check=True` only where a failure is meant to raise.
     - N818 (`JobBusy`, `TrackedMatchupMissing`): rename to `…Error` and update every reference; N806 (`export/export_elections.py:1916`): lowercase or hoist to a module constant.
     - PTH ×14: move to `pathlib`, keeping the types callers rely on; convert at the boundary.
     - RUF012 ×15: annotate mutable class attributes `ClassVar` (mypy strict must stay clean).
   - Stop and ask if a fix would change what a console page, an export file or a CLI shows.
   - Functions: those the findings name
   - Depends on: 2
   - Acceptance: `ruff check` clean in `data/` and `guesstheyear/`; strict mypy clean; every `noqa` has a code and a reason.
   - Tests: `./run_tests.sh` passes with the same test count; for each fix that narrows an `except` or changes a `subprocess.run` check, a test of the edge it now handles (the expected error still handled; an unexpected one no longer swallowed), unless an existing test already covers it, which the review request names.

6. Fix the Biome findings
   - Files: the JS files the findings name (all areas), regenerated `.min.js` bundles
   - What: make `npx biome lint .` clean (137 diagnostics in the Context). Safe fixes first (`biome lint --write`), then hand fixes; unsafe fixes only one by one after reading each. Judgment calls: useIterableCallbackReturn ×37 (a `forEach` callback that returns a value: use a block body, or `map` if the value is wanted); useDomNodeTextContent ×8 (`innerText` → `textContent` changes what hidden or styled text yields, so check each use); noUnusedFunctionParameters ×11 (prefix `_` where the signature is fixed by a caller, like an event handler, else drop). Any `biome-ignore` carries a reason. Then `npm run minify:electionmaps` and commit the bundles.
   - Functions: those the findings name
   - Depends on: 3
   - Acceptance: `npx biome ci .` is clean; bundles in sync (`npm run vendor:d3 && npm run minify:electionmaps` leaves `git diff` empty).
   - Tests: `npm test` passes with the same test count; drive the election maps engine under jsdom for Westminster and US as `AGENTS.md` describes, and say what was checked.
   - Manual review: `./server.sh` from the worktree (it serves on 8000, so stop the main checkout's if it runs, or `PORT=8001 ./server.sh`), then open `/electionmaps/` and `/uselectionmaps/` (switch a few elections and modes, open the predict and poll-tracker panels), `/guesstheyear/`, `/referdle-solver/` and `/routeweather/`, and check each behaves as on master.

7. One lint script, CI and docs
   - Files: `lint.sh` (new), `.github/workflows/ci.yml`, `AGENTS.md`, `README.md`, `data/README.md`
   - What: `lint.sh` at the repo root in the style of `data/run_tests.sh` (`#!/usr/bin/env bash`, `set -euo pipefail`, tabs, `[[ ]]`, `cd "$(dirname "$0")"`): finds the venv like `run_tests.sh`, checks that `ruff`, `shellcheck` and `node_modules/.bin/biome` exist and prints how to install them if not (`./data/election_data/bin/pip install -r data/requirements.txt`, `npm ci`); then runs, each under an echo line, `ruff check` and `ruff format --check` for `data/` and `guesstheyear/`, `biome ci .`, and `shellcheck` on `git ls-files '*.sh' ':!data/archive/*'`. It runs every step and exits non-zero if any failed, naming the failed steps. `--fix` runs `ruff check --fix`, `ruff format`, `biome check --write` and then the checks; `-h`/`--help` prints the header; any other argument is refused with exit 2. `ci.yml` gains a `lint` job (Python 3.12 with pip cache + `pip install -r data/requirements.txt`, Node 22 + `npm ci`, then `./lint.sh`). `AGENTS.md` Testing gets `./lint.sh` (and `--fix`) before the test commands; `README.md` key commands and `data/README.md`'s Python environment section say the venv now holds ruff and shellcheck, how to add them to an existing venv, and how to switch on `.git-blame-ignore-revs` (`git config blame.ignoreRevsFile .git-blame-ignore-revs`).
   - Functions: none
   - Depends on: 4, 5, 6
   - Acceptance: `./lint.sh` exits 0 on the branch; a lint error, a format change, a Biome error and a shellcheck warning each make it exit non-zero, naming its step; `--fix` repairs the fixable ones; shellcheck passes on `lint.sh` itself.
   - Tests: script only; prove each failure mode by hand once (each edit reverted) and list them in the review request. CI is proven when the draft PR runs it.
   - Model: claude-sonnet-5-5

## Checkpoints
- [x] 1. Tools pinned; ruff, Biome and shellcheck settings picked up
- [ ] 2. Python formatting-only commit; `ruff format --check` clean
- [ ] 3. JS formatting-only commit with bundles; `biome format` clean
- [ ] 4. `ruff check` clean in `data/tests/`; fixtures registered; `.git-blame-ignore-revs` lists pieces 2 and 3
- [ ] 5. `ruff check` clean everywhere; strict mypy still clean
- [ ] 6. `biome ci` clean; bundles in sync
- [ ] 7. `./lint.sh` runs all checks and fails at the right steps; CI lint job; docs updated

## Verification

After every piece: `cd data && ./run_tests.sh` (strict mypy then pytest; note the test count, which must not change unless the piece says why), `npm test`, `npm run minify:electionmaps` after any front-end change with the bundles committed, and `git diff --check`. From piece 1, also report the `ruff check`, `ruff format --check`, `npx biome ci .` and `shellcheck` counts until they gate; from piece 7, `./lint.sh`. Coverage: `"$(python3 ~/.claude/lib/planner.py tools)/coverage-changed.sh"` after each piece; uncovered changed lines get a test or a one-line reason (for the formatting pieces: reformatted only, already uncovered on master).

After the merge, the planner chat updates `project.md`: Checks gains `./lint.sh` (`--fix` applies fixes) as its first item.

## Notes
- Piece 1 (2026-11-14): `biome.json` uses `"linter": {"rules": {"preset": "recommended"}}`, not `"recommended": true`, which Biome 2.6.7 reports as deprecated (a notice that made `biome ci` count biome.json as a 140th file).
- Piece 1: `biome.json` sets `"assist": {"enabled": false}`. With Biome 2's default assist on, `biome ci` also enforces organizeImports: 89 import-sorting errors the plan didn't measure or schedule (piece 3 runs only `biome format`, piece 6 only lint), and reordering ESM imports can change side-effect order. Put to the user at the piece 1 review.
- Piece 1: `data/pyproject.toml` `requires-python` bumped `>=3.10` → `>=3.12` (verified review finding) so it agrees with ruff's py312 target, mypy.ini and CI; the code already needed 3.11+ (StrEnum, Self, datetime.UTC). The explicit `target-version = "py312"` stays, so guesstheyear's `extend` doesn't rely on inference.
- Piece 1 measurements with the pins: ruff from `data/` 698 findings (659 + 39 N/PTH/C4), 164 safe fixes; format 151 of 309; guesstheyear 8 findings (the 6 defaults + PTH123 + N806), 3 to reformat; `biome ci .` covers exactly the 139 tracked JS files, 128 to format, 138 lint diagnostics (41 errors, 85 warnings, 12 infos; Context said 137/40); shellcheck clean.
- Piece 1: the first `planner.py setup` raced the `requirements.txt` edit and didn't install ruff/shellcheck; a re-run did (acceptance met).
- Piece 1 committed 68720459 (2026-11-14). User decisions at review: README.md and data/README.md changed to "Python 3.12+" in piece 1 ("Fix in piece 1"); Biome assist stays off ("Keep it off"), so `biome ci` checks format and lint only.
- Piece 2 (2026-11-14), deviation from "no hand edits": `ruff format` moved 4 `# type: ignore[attr-defined]` comments off their `.rowcount` lines (`console/shared/model_outputs.py` ×3, `console/polls/views.py` ×1), which broke strict mypy (8 errors), and stranded 2 `# noqa: F811` when parameter lists were split (`tests/test_console_job_flows.py`, `tests/test_console_run_log.py`). Each comment was moved back onto the line it applies to. These are comment-only edits, so parse trees are unchanged and `ruff format --check` stays clean. The formatting commit is therefore `ruff format` plus 6 comment moves. Put to the user at the piece 2 review.
- Piece 2 same-meaning check (docstrings normalised, user's choice): 154 files, 153 identical parse trees, 1 docstrings-only (`data/tests/test_uk_fixtures.py`: `""""Other"…` → `""" "Other"…`), 0 different. `ruff check` from `data/` 698 → 691.

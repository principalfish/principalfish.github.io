# Decisions: Add lint and format checks

## Settled
- Goal: formatting, naming and idioms are enforced by linters run as project checks, not by review (the review's style check became docs-only on 2026-11-14). (2026-11-14)
- strict mypy keeps gating `data/run_tests.sh`. (2026-11-14)
- Python: ruff defaults + `N`, `PTH`, `C4`, all findings fixed in this issue. The F811 fixture pattern is fixed by registering `tests/console_fixtures.py` through `pytest_plugins` in `conftest.py` and dropping the imports, not by ignoring F811. (2026-11-14)
- `guesstheyear/*.py` is linted and formatted too, using `data/pyproject.toml`'s settings through a small extending config. (2026-11-14)
- JavaScript: Biome, line width 100, 2-space indent, double quotes (electionmapslogic switches from single), semicolons, trailing commas; recommended rules, findings fixed here. (2026-11-14)
- Shell: shellcheck only; no shfmt, no optional shellcheck checks. (2026-11-14)
- A root `./lint.sh` runs ruff check, ruff format --check, biome ci and shellcheck (`--fix` applies fixes); it becomes the first item on `project.md`'s Checks line, the other items stay; `ci.yml` gets a lint job. (2026-11-14)

## Open
- Formatting commits and `.git-blame-ignore-revs`.
- Tool pinning and how shellcheck is installed.
- JS scope: every tracked source file, including routeweather, referdle-solver, guesstheyear, data/console/static.

## Assumed

## Measured (2026-11-14, master at aa033121; scratch installs, nothing in the repo touched)
- Python: 309 files under `data/` excl. `archive/` (archive is outside mypy too). ruff 0.17.5 defaults: 659 findings, 469 in `tests/`. F811 ×283 is one pattern: fixtures imported from `tests/console_fixtures.py` and used as test parameters. Then ISC004 ×83, I001 ×41, F401 ×30, RUF059 ×20, UP045 ×18, BLE001 ×17, UP017 ×15, RUF012 ×15, RUF046 ×14, PLW1510 ×13, SIM117 ×11, UP037 ×11, B017 ×10, long tail. 146 safe autofixes.
- Extras over defaults: N +4, PTH +14, C4 +21, RET +3, DTZ +13 (mostly deliberate `date.today()`), PLC0415 +91 (65 in tests), ANN401 +219, T20 +440 (CLI prints), ERA001 0; B904/TRY002/B006/S110 are already in the defaults.
- `ruff format` (88): 151 of 309 files change, +9,401/−3,406. At 100 or 120, 226/234 files change.
- `guesstheyear/*.py` (3 files): 6 findings, 3 files to reformat.
- JS: 139 tracked source files (excl. `.min.js`, `vendor/`); 138 use 2-space indent. Quotes: `electionmapslogic` mostly single (4,235 ' vs 1,914 "), every other area double. Lines: 40,007, >80: 6,150, >100: 1,364, >120: 444.
- Biome 2.6.7 recommended lint: 137 diagnostics (40 errors, 85 warnings, 12 infos): useIterableCallbackReturn 37, useOptionalChain 36, noUnusedVariables 18, useTemplate 11, noUnusedFunctionParameters 11, useDomNodeTextContent 8. By area: referdle-solver 53, guesstheyear 35, routeweather 26, electionmapslogic 19.
- Biome format (2-space, double quotes, semicolons, trailing commas): lw80 133 files +20.8k/−8.2k; lw100 128 files +13.8k/−6.9k; lw120 130 files +10.8k/−6.4k; single quotes at lw100 is larger (+15.8k/−8.9k).
- ESLint 10 recommended: 58 (no-unused-vars 30, no-undef 22), and no formatter without Prettier.
- Shell: `server.sh`, `data/run_tests.sh`, `data/archive/import_all.sh`. shellcheck 0.11 clean on all three; shfmt 3.14 flags only the archive script.
- CI (`.github/workflows/ci.yml`) runs mypy+pytest and vitest + a built-asset sync check; PRs merge with merge commits.

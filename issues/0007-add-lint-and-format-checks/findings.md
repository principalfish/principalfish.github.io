# Findings: add-lint-and-format-checks

## piece 1 · 2026-11-14 · Pin the tools and add their settings

Cost: 6 subagents (3 checks, verifier, 1 re-check, re-check verifier) · ~298k tokens · ~3 min; user-requested change round: 3 checks, all clean · ~136k tokens · <1 min

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | correctness | low | cleanup | verified | fixed | `data/pyproject.toml:9` | `requires-python = ">=3.10"` disagreed with ruff's py312 target, mypy.ini and CI 3.12 (code already needs 3.11+); bumped to `>=3.12` |
| round | correctness | - | cleanup | dropped | - | `data/pyproject.toml:23` | `.venv`/`election_data` in `extend-exclude` repeat built-in excludes — dropped: harmless, nothing to change |
| round | other | low | docs | noticed | fixed | `README.md:8`, `data/README.md:31` | Both say "Python 3.10+", already stale (predates the piece) |
| re-check | correctness | low | docs | verified | fixed | `README.md:8`, `data/README.md:31` | "Python 3.10+" now contradicts `requires-python = ">=3.12"`, so following the README on 3.10/3.11 fails the editable install; change both to "Python 3.12+" |
| manual | user | low | docs | - | fixed | `README.md:8`, `data/README.md:31` | User chose "Fix in piece 1": both now say "Python 3.12+" (round of correctness, security, coverage: clean) |

## piece 2 · 2026-11-14 · Reformat the Python once

Cost: 4 check subagents (security; correctness twice, the first stopped by a session restart before it reported; coverage) · tokens partly unknown (security ~48k, correctness rerun ~67k) · ~3 min · no verifier (every check clean)

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|

## piece 3 · 2026-11-14 · Reformat the JavaScript once

Cost: 3 check subagents (correctness, security, coverage) · ~199k tokens · ~5 min · no verifier (every check clean)

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|

## piece 4 · 2026-11-14 · Fix the ruff findings in the tests

Cost: 4 subagents (correctness, security, coverage, verifier) · ~249k tokens · ~6 min · no re-check (nothing verified)

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | correctness | low | risk | dropped | - | `.git-blame-ignore-revs:5,8` | Lists the branch's own SHAs, inert if the PR is squash- or rebase-merged — dropped: plan Context already requires a merge-commit merge, as PRs #74/#80/#84/#85 were; worth a line in the PR description |

## piece 5 · 2026-11-15 · Fix the ruff findings in the pipeline code

Cost: 5 subagents (correctness, security, coverage, verifier, correctness re-check) · ~376k tokens · ~8 min

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| build | builder | high | bug | - | fixed | `data/export/uncertainty.py:85` | ruff's C416 fix turned `party_names` into `dict(session.execute(...))`, a TypeError at runtime (a SQLAlchemy Result has `.keys()`), which crashed every export; caught by comparing a full-site scratch export with HEAD's (and by 64 export/console tests); now `dict(....tuples().all())` with a direct test |
| round | correctness | low | cleanup | verified | fixed | `data/models/support/polling.py:46-47`, `data/models/support/monte_carlo.py:97`, `data/console/shared/wikipedia_queue.py:58` | Module-level TypeVars left orphaned by the PEP 695 rewrites; deleted with their imports, RowT's comment moved onto the function |
| round | correctness | low | cleanup | verified | fixed | `data/importers/polls/holyrood/holyrood_wikipedia_import.py:28-37`, `data/importers/seed/migrate_us_electoral_votes.py:24-30` | isort split `as` imports into separate blocks; `combine-as-imports = true` added to `[tool.ruff.lint.isort]`, which also merged import blocks in 13 test files (import lines only) |
| re-check | correctness | - | - | noticed | - | `data/models/westminster/calibrate_uncertainty.py:85` | Says the restored `noqa: PLC0414` comments name an unselected rule — builder: mistaken, ruff 0.17's defaults include PLC0414 (it raised these 3) and RUF100 is active and doesn't flag them |

## piece 6 · 2026-11-15 · Fix the Biome findings

Cost: 3 check subagents (correctness, security, coverage) · ~222k tokens · ~3 min · no verifier (every check clean)

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|

## piece 7 · 2026-11-15 · One lint script and CI

Cost: 5 subagents (correctness, security, coverage, verifier, correctness re-check) · ~300k tokens · ~6 min · the three checks started before their input files were written (a git_guard parse block); each reviewed the worktree directly

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | correctness | low | bug | verified | fixed | `lint.sh:71` | Under `--fix` a failing `ruff format` (e.g. a syntax error) aborted the run via `set -e`, so no check or summary ran; now `|| true` like the other fixers, and the check steps report it |
| round | correctness | low | bug | verified | fixed | `lint.sh:19` | `--help` failed when run from another directory (`usage` read `$0` after the `cd`); now reads the script's own name |
| round | security | low | risk | dropped | - | `.github/workflows/ci.yml:3` | No top-level `permissions:` block — dropped: pre-existing for every job, not made worse here; could be its own hardening task |
| round | coverage | low | other | dropped | - | `lint.sh:65` | `git ls-files` skips untracked `.sh` files locally — dropped: the header and plan promise tracked scripts |
| round | coverage | info | other | dropped | - | `.github/workflows/ci.yml:9-35` | The CI job is only proven by the PR run — dropped: the plan says so |

## piece 8 · 2026-11-15 · Bring the front-door docs up to the project-docs guide

Cost: 6 subagents (correctness, security, coverage, verifier, correctness re-check; plus a light correctness check on the EXE-ignore fix) · ~430k tokens · ~8 min

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| build | builder | medium | bug | - | fixed | `data/pyproject.toml` | PR CI's new lint job failed: 28 × EXE001 (shebang scripts committed 100644); ruff skips EXE001/EXE002 on WSL so `./lint.sh` passed locally. Planner's fix: `ignore = ["EXE001", "EXE002"]` with a reason, committed separately as cfcfa05b; CI lint now passes |
| build | builder | low | docs | - | fixed | `AGENTS.md:15` | Old AGENTS.md said `./run_tests.sh <file>` runs one file; run_tests.sh always passes `tests/`, so it ran the whole suite; now documents `python -m pytest tests/<file>` |
| round | correctness | medium | docs | verified | fixed | `data/docs/database.md:65` | The moved sqlite3 checks read the shell's unset `$DATABASE_PATH` (opening an empty temp DB); the block now loads `.env` first |
| round | correctness | low | docs | verified | fixed | `AGENTS.md:16` | "a file path adds to it" was wrong (pytest dedupes it); now "doesn't narrow it" |
| round | correctness | low | docs | verified | fixed | `electionmapslogic/README.md:86` | `../site/vendor/` ambiguous; now "the repo's `site/vendor/`" |
| round | correctness | low | docs | verified | fixed | `electionmapslogic/README.md:90` | Stale "ERR_REQUIRE_ESM on Node 21" reason (Node 22 here loads later jsdom); dropped, the jsdom 24 pin kept |
| round | correctness | low | docs | dropped | - | `data/pyproject.toml:27` | Comment over-claimed which EXE rules WSL skips — dropped: already fixed in cfcfa05b |
| round | correctness | info | other | dropped | - | plan account | Account still described the reverted chmod fix — dropped: not code, corrected |
| round | security | info | risk | dropped | - | `guesstheyear/app.py:202` | Flask preview binds `0.0.0.0` — dropped: pre-existing, outside the change; possible separate fix (bind 127.0.0.1) |
| round | coverage | low | other | dropped | - | `data/docs/database.md` | Moved sqlite3 queries not run — dropped: the builder ran them against a backup copy |
| re-check | correctness | low | docs | verified | fixed | `data/docs/database.md:69` | With no `.env` (a supported state: DATABASE_PATH has a default), `. ../.env` fails, `$DATABASE_PATH` stays empty and the queries open an empty temp DB; the :76 bullet also assumes a `.env`. Options: fall back to the code default (`DB=${DATABASE_PATH:-~/dbs/elections.db}` after an optional source), or say so in the lead-in |
| manual | user | low | docs | - | fixed | `data/docs/database.md:65-77` | Planner chose option (a) under the standing instruction: source `.env` if present, fall back to the shell value then `~/dbs/elections.db`; the :76 bullet matches |

## review 01 · 2026-11-15

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | correctness | medium | defect | verified | fixed | `lint.sh:89` | `biome ci` exits 0 on warnings, so ~85 of the fixed diagnostics could return without failing lint.sh or CI; add `--error-on-warnings` |
| round | docs | low | docs | verified | fixed | `CLAUDE.md:1` | 3 lines where the project-docs guide says one line, `@AGENTS.md`; fixed in 3d1edb61 |
| round | docs | low | docs | verified | fixed | `data/README.md:20` | `archive/` row says not type-checked; it is also excluded from ruff and shellcheck |
| round | common-mistakes | low | cleanup | dropped | - | `data/pyproject.toml` | Global EXE001/EXE002 ignore vs "no blanket ignores" — dropped: deliberate recorded decision; rule-level, not a file-wide suppression |
| round | security | low | defect | dropped | - | `.github/workflows/ci.yml:8` | No top-level `permissions:` — dropped: pre-existing, outside the change |
| round | security | low | defect | dropped | - | `guesstheyear/wiki.py:143` | `requests.get` without timeout — dropped: pre-existing, outside the change |

## review fixes · 2026-11-15 · /review self 01 fixes

Cost: 1 re-check subagent (correctness) · ~60k tokens · ~1 min

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| manual | user | medium | bug | - | fixed | `lint.sh:89` | `biome ci` exited 0 on warnings; now `--error-on-warnings` (a 2-warning probe fails "biome", the tree passes) |
| manual | user | low | docs | - | fixed | `data/README.md:20` | `archive/` row now says not maintained, type-checked or linted (true of ruff, shellcheck, mypy and Biome) |
| manual | user | low | docs | - | fixed | `CLAUDE.md:1` | Now exactly `@AGENTS.md` (3d1edb61), on the user's direct go-ahead in the builder chat; a relayed request had been declined |

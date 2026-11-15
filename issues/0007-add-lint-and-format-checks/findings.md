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

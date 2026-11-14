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

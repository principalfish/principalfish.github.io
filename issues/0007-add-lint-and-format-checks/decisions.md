# Decisions: Add lint and format checks

## Settled
- Goal: formatting, naming and idioms are enforced by linters run as project checks, not by review (the review's style check became docs-only on 2026-11-14). (2026-11-14)
- strict mypy keeps gating `data/run_tests.sh`. (2026-11-14)

## Open
- Python: ruff check + ruff format under `data/`, settings in `data/pyproject.toml`; which rule set.
- JavaScript: Biome or ESLint, and a `lint` script in `package.json`.
- Shell: shellcheck, and whether shfmt too.
- How the checks join `project.md`'s Checks line (one script vs separate commands).
- Formatting-only commit and `.git-blame-ignore-revs`.
- Tool pinning.

## Assumed

# Decisions: Run the Python test suite in parallel with pytest-xdist

## Settled
- Pages deploy gated on CI: dropped. Folded in from the 0007 chat, then after the trade-off (CI checks little of a data push, and only the front-end jobs guard what ships) Phil chose "Drop the Pages change": keep GitHub's built-in deploy. Excluding `data/` from the site can be done with a `_config.yml` `exclude: [data]` instead (2026-11-15).
- The suite is parallel-safe on the evidence: 4 full runs (serial, -n 4, -n auto, -n auto + coverage) all failed the same 5 local-only tests, nothing new, and left git status clean; see measurements.md (2026-11-15).
- `-n` goes in `data/run_tests.sh` (`pytest tests/ -n … "$@"`), so CI and check.sh get it; a later `-n 0` in the args turns it off (2026-11-15).
- Default workers: CPU count / 2 ("CPU / 2 i think") (2026-11-15).
- The repo-scanning tests list data/'s .py files through one shared helper using `git ls-files --cached --others --exclude-standard`, so gitignored folders are skipped (2026-11-15).
- Add a session guard: the run fails if tracked files under electionmaps/data or uselectionmaps/data changed during it, naming them (2026-11-15).
- Coverage works under xdist (identical XML on a subset); this chat adds `-n` to the planner branch's coverage-changed.sh, only when xdist is importable (2026-11-15).

## Open
- CPU/2 gives CI (4 vCPU) only 2 workers: does CI also use CPU/2, or `auto`?

## Assumed
- Issue 0007 merged (PR #87, 2026-11-15 12:18); the builder branches from `master`.

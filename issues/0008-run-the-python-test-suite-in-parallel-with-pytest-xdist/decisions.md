# Decisions: Run the Python test suite in parallel with pytest-xdist

## Settled
- Pages deploy gated on CI: dropped. Folded in from the 0007 chat, then after the trade-off (CI checks little of a data push, and only the front-end jobs guard what ships) Phil chose "Drop the Pages change": keep GitHub's built-in deploy. Excluding `data/` from the site can be done with a `_config.yml` `exclude: [data]` instead (2026-11-15).
- The suite is parallel-safe on the evidence: 4 full runs (serial, -n 4, -n auto, -n auto + coverage) all failed the same 5 local-only tests, nothing new, and left git status clean; see measurements.md (2026-11-15).
- `-n` goes in `data/run_tests.sh` (`pytest tests/ -n … "$@"`), so CI and check.sh get it; a later `-n 0` in the args turns it off (2026-11-15).
- Default workers: CPU count / 2 ("CPU / 2 i think") (2026-11-15).
- The repo-scanning tests list data/'s .py files through one shared helper using `git ls-files --cached --others --exclude-standard`, so gitignored folders are skipped (2026-11-15).
- Add a session guard: the run fails if tracked files under electionmaps/data or uselectionmaps/data changed during it, naming them (2026-11-15).
- Coverage works under xdist (identical XML on a subset); this chat adds `-n` to the planner branch's coverage-changed.sh, only when xdist is importable (2026-11-15).
- CI uses all 4 workers: CPU/2 stays the default (an xdist hook in conftest that the `PYTEST_XDIST_AUTO_NUM_WORKERS` env var overrides), and ci.yml sets it to 4 for the Python job (2026-11-15).
- Pytest's temp files go in RAM: when `/dev/shm` exists, run_tests.sh points `--basetemp` at a fresh per-run folder under it and deletes it on exit; an env var keeps them on disk instead (2026-11-15).
- Piece 2: helper `data/tests/source_files.py` with `python_files(under=None)`; the sub-folder scans (`test_importer_packages.py:27/32`, `test_console_templates.py:339`) switch too ("Switch them too"); no git or not a checkout fails loudly ("Fail loudly"); no caching (2026-11-15).

## Open

## Assumed
- `pytest-xdist>=3.8` in data/requirements.txt, matching pytest's `>=` style (no `psutil`).
- `--pdb` keeps working: xdist turns itself off for `-n auto` when `--pdb` is passed. `-s` needs `-n 0`, which gets documented.
- After the PR merges, this chat adds `-n auto` and the RAM temp folder to the planner branch's coverage-changed.sh, only when xdist is importable.
- Clearing the 6.5 GB `data/.pytest_cache` in the main checkout is Phil's call, outside the plan.
- Issue 0007 merged (PR #87, 2026-11-15 12:18); the builder branches from `master`.

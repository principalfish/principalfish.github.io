# Decisions: Run the Python test suite in parallel with pytest-xdist

## Settled
- Scope adds "make the Pages deploy wait for CI", on this issue's branch and PR: Phil chose "Fold into 0008 (xdist)" in the 0007 planner chat (relayed 2026-11-15).

## Open
- Is the suite safe to run in parallel (temp DBs, tracked-file writes, shared files, ports, module caches, order dependence)?
- What speedup do `-n auto` and `-n 4` give over serial?
- Does coverage (`coverage-changed.sh`) still work under xdist?
- Where does `-n` live: `data/run_tests.sh`, root `check.sh`, or pytest config? (`tests/test_project_layout.py:62` forbids `data/pytest.ini` and pins `pytest tests/` in run_tests.sh.) Does CI use it?
- The 6.5 GB of old scratch copies in `data/.pytest_cache` (3,016 .py files) make 5 repo-scan tests fail locally and cost about 70s; skip `.pytest_cache` in the scans, clear the folder, or both?
- Pages deploy: switch Pages source to GitHub Actions, and add a `deploy` job in `ci.yml` that needs lint, data and frontend, on push to `master` only.
  - Jekyll: no front matter outside `data/`, no `_`-prefixed published files. Today Jekyll renders the tracked `*.md` to HTML, and without it they're served raw. Add `.nojekyll`, or drop Jekyll silently?
  - The custom domain `principalfish.co.uk` (CNAME file, HTTPS cert approved) must carry over.
  - What goes in the artifact (excluding `data/`, `node_modules/`, `.planner`, dotfolders, tests)? About 50 MB is tracked, far under the limits.
  - Order of the switch: the flip and the workflow must land together, or deploys stop.
  - `workflow_dispatch` for a manual redeploy?
  - Top-level `permissions: contents: read`?
  - Branch protection (probably not: direct data pushes to `master`)?

## Assumed
- Issue 0007 (PR #87, root `check.sh`) lands first; the builder branches from `master` after it merges.

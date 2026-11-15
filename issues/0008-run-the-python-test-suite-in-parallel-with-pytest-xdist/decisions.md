# Decisions: Run the Python test suite in parallel with pytest-xdist

## Settled
- Scope adds "make the Pages deploy wait for CI", on this issue's branch and PR: Phil chose "Fold into 0008 (xdist)" in the 0007 planner chat (relayed 2026-11-15).
- The suite is parallel-safe on the evidence: 4 full runs (serial, -n 4, -n auto, -n auto + coverage) all failed the same 5 local-only tests, nothing new, and left git status clean; see measurements.md (2026-11-15).
- `-n` goes in `data/run_tests.sh` (`pytest tests/ -n … "$@"`), so CI and check.sh get it; a later `-n 0` in the args turns it off (2026-11-15).
- Default workers: CPU count / 2 ("CPU / 2 i think") (2026-11-15).
- The repo-scanning tests list data/'s .py files through one shared helper using `git ls-files --cached --others --exclude-standard`, so gitignored folders are skipped (2026-11-15).
- Add a session guard: the run fails if tracked files under electionmaps/data or uselectionmaps/data changed during it, naming them (2026-11-15).
- Coverage works under xdist (identical XML on a subset); this chat adds `-n` to the planner branch's coverage-changed.sh, only when xdist is importable (2026-11-15).

## Open
- CPU/2 gives CI (4 vCPU) only 2 workers: does CI also use CPU/2, or `auto`?
- Pages deploy: switch Pages source to GitHub Actions, and add a `deploy` job in `ci.yml` that needs lint, data and frontend, on push to `master` only.
  - Jekyll: no front matter outside `data/`, no `_`-prefixed published files. Today Jekyll renders the tracked `*.md` to HTML; an Actions deploy serves them raw.
  - The custom domain `principalfish.co.uk` is held in the repo's Pages settings (HTTPS cert approved), so it carries over.
  - What goes in the artifact? Today Jekyll publishes every tracked file except dotfiles and `_*`, including the Python sources in `data/`. About 50 MB is tracked, far under the limits.
  - Order of the switch: the flip and the workflow must land together, or deploys stop.
  - `workflow_dispatch` for a manual redeploy?
  - Top-level `permissions: contents: read`?
  - Branch protection (probably not: direct data pushes to `master`)?

## Assumed
- Issue 0007 merged (PR #87, 2026-11-15 12:18); the builder branches from `master`.

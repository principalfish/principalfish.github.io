# Decisions: Run the Python test suite in parallel with pytest-xdist

## Settled
- Scope adds "make the Pages deploy wait for CI", on this issue's branch and PR: Phil chose "Fold into 0008 (xdist)" in the 0007 planner chat (relayed 2026-11-15).
- The suite is parallel-safe on the evidence: 4 full runs (serial, -n 4, -n auto, -n auto + coverage) all failed the same 5 local-only tests, nothing new, and left git status clean; see measurements.md (2026-11-15).
- `-n` goes in `data/run_tests.sh` (`pytest tests/ -n … "$@"`), so CI and check.sh get it; a later `-n 0` in the args turns it off (2026-11-15).
- Default workers: CPU count / 2 ("CPU / 2 i think") (2026-11-15).
- The repo-scanning tests list data/'s .py files through one shared helper using `git ls-files --cached --others --exclude-standard`, so gitignored folders are skipped (2026-11-15).
- Add a session guard: the run fails if tracked files under electionmaps/data or uselectionmaps/data changed during it, naming them (2026-11-15).
- Coverage works under xdist (identical XML on a subset); this chat adds `-n` to the planner branch's coverage-changed.sh, only when xdist is importable (2026-11-15).
- Pages deploys from GitHub Actions: a `deploy` job in `ci.yml` needs lint, data and frontend, runs on push to `master` (and `workflow_dispatch`) only (2026-11-15).
- Artifact: `git archive HEAD` minus dotfiles, `_`-prefixed paths and `data/`; every site URL keeps working, the pipeline's sources stop being published (2026-11-15).
- Drop Jekyll: files upload as they are; only the stray `.md` → `.html` renderings (e.g. `/AGENTS.html`, `/CLAUDE.html`) disappear (2026-11-15).
- Switch order: Phil flips Settings → Pages → Source to GitHub Actions just before merging the PR (2026-11-15).
- Extras: `workflow_dispatch` only. No top-level `permissions: contents: read`, no branch protection (Phil didn't pick them) (2026-11-15).
- The custom domain is held in the repo's Pages settings (`principalfish.co.uk`, HTTPS cert approved), so it carries over; `CNAME` stays in the artifact (2026-11-15).

## Open
- CPU/2 gives CI (4 vCPU) only 2 workers: does CI also use CPU/2, or `auto`?

## Assumed
- Issue 0007 merged (PR #87, 2026-11-15 12:18); the builder branches from `master`.

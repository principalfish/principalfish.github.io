# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `c7a85eab…` (Piece 6b).
- Builder worktree clean.

The builder's pieces are all done and pushed:
- 6a `a5cdf8e0`
- 6 data `51c31966`, after the 15/60 rerun, backup `elections-2026-11-07-183903.db.gz`, trends rebuild and export
- 6b `c7a85eab`

Nothing is pending. Next: Piece 7, `/publish poll-errors-simulation` from the planner chat (PR #74). See `plan.md` for the full record.

Precondition until merge: no poll imports or model runs from the main checkout's console, which still has the old 30/30 defaults and no uncertainty.

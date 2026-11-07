# Resume point: read this first

Updated 2026-11-07 (builder, after the UK-only re-scope). Issue state: `building`.

## Where we are

On 2026-11-07 Phil re-scoped this issue to **ship the UK (Westminster) uncertainty only**. All US work moved to issue 0004 (`us-poll-errors-uncertainty`). Its branch, `us-poll-errors-uncertainty`, was created at `44dd8336` and pushed. The UK ship no longer waits for 0002. `plan.md` `## Pieces` and `## Checkpoints` are rewritten.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `44dd8336514cb20953b76f0c8d24b87df814aa95` until Piece 1 is committed.
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95`.

## Next step

**Piece 1: remove the US work from this branch** in one commit; the spec is in `plan.md`. The file and commit map is `../0004-us-poll-errors-uncertainty/us-file-commit-map.txt`. No decision is pending; Phil asked for this directly.

## Don't

- Write to the live DB, rerun, backfill, export or publish without Phil's explicit go-ahead at the time, after `scripts/backup_db.py backup`.
- Push anything but this branch (and the planner branch), and never with force. Phil opens and merges the PR.
- Touch `us-poll-errors-uncertainty`. It is 0004's branch.

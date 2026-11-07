# Resume point: read this first

Updated 2026-11-07 (planner, after the ship decision). Issue state: `building`.

## Where we are

On 2026-11-07 Phil decided to ship this branch as built and refine the model later. The model refinements (2a–2f, 4b–4d) moved to issue 0003, `refine-westminster-and-us-poll-error-models` (#78). `plan.md` `## Pieces` and `## Checkpoints` were rewritten around shipping.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `44dd8336514cb20953b76f0c8d24b87df814aa95` (later is fine if Pieces 1–3 have been committed since).
- `git log --oneline poll-errors-simulation..master | wc -l` → 32 on 2026-11-07, more once 0002 lands.
- `python3 ~/.claude/lib/planner.py meta 0002` → issue 0002 (2026 US midterm results import) is still open. Piece 5 waits for it to be merged into master.

## Next step

**No blocking decision. The next step is Piece 1 (4a, small US model fixes):** brief Phil with current numbers, and get his US-5 choice (calibrate House G on projected-output error, or document the shrinkage). Then build with `/builder poll-errors-simulation`. Pieces 1–4 don't depend on 0002.

The decisions still to come, each when its piece starts, are listed in `decisions.md` Open.

## Merge order (Phil, 2026-11-07)

1. 0002 is finished and merged into master.
2. Master is merged into this branch (Piece 5).
3. This branch ships (Pieces 6–11).

Merging this branch into master publishes whatever Westminster export it carries, so Piece 9 (backfill and export) goes last.

## Don't

- Rework the conversion model or any 2a–2f/4b–4d piece here. That's 0003.
- Write to the live DB, rerun, backfill, export or publish without Phil's explicit go-ahead at the time, after `scripts/backup_db.py backup`.
- Flip US release flags without the Piece 10 decision.
- Push anything but this branch, and never with force. Phil opens and merges the PR.

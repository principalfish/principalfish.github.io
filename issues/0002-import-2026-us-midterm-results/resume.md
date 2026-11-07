# Resume point — read this first

## Where we are
Issue 0002 "Import 2018–2026 US House and Senate results from NYT data" is building (GitHub #79). Pieces 1–5 are committed and pushed.
State checks: `git -C <build worktree> rev-parse --short HEAD` = `147ad9d4` on branch `import-2026-us-midterm-results`, clean tree, pushed (`git status -sb` shows no "ahead"); `python3 ~/.claude/lib/planner.py meta 0002` shows state building.
If anything differs, stop and tell the user.

No pending decision. Next step: checkpoint 6 in plan.md (House maps 24/25), which is blocked until `ogr2ogr` and `mapshaper` are installed. Piece 7 (export) is unblocked and may go first with the user's OK. See `plan.md`.

## Don't
- Run imports, migrations or exports against the live DB without Phil's explicit go-ahead at the time.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.

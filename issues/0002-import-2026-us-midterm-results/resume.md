# Resume point — read this first

## Where we are
Issue 0002 "Import 2018–2026 US House and Senate results from NYT data" is planned (GitHub #79). The discussion is closed; the ledger is in decisions.md and the full plan in plan.md.
State checks: `python3 ~/.claude/lib/planner.py meta 0002` shows state planned and gh_issue 79; master at or after `9ac8b04d` (`git rev-parse --short master`); branch `import-2026-us-midterm-results` not yet created (`git branch --list import-2026-us-midterm-results`).
If anything differs, stop and tell the user.

No pending decision. Next step: checkpoint 1 in plan.md (seat status/source schema + migration), via `/builder import-2026-us-midterm-results`. See `plan.md`.

## Don't
- Run imports, migrations or exports against the live DB without Phil's explicit go-ahead at the time.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.

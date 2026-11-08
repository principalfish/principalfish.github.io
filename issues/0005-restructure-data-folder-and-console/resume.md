# Resume point — read this first

## Where we are
Planning discussion for 0005, state `investigating`. Three read-only sweeps are saved under `findings/`. `decisions.md`'s Open list is empty. The full ledger is with the user for confirmation; only an explicit go-ahead ends the discussion.
State checks: `git rev-parse --short origin/master` should be `35e5326c`. If it has moved, re-check the findings' line numbers. No build branch yet. The console issue hasn't been created.

## How it got here
Every answered decision is in `decisions.md` Settled, dated.

## The pending decision (exact)
Is the ledger complete and correct? "That's everything" or "go ahead" moves to drafting.

## What the next session does
1. On go-ahead: draft 0005's `issue.md`, iterate to explicit approval, write it, `gh issue create`, then `plan.md` with current and proposed structure, every caller (from `findings/`), and the scratch rebuild as the behaviour proof.
2. Create the console issue with `planner.py new --mode build`, and propose a theme title for this chat since it will own two issues.
3. Plan the console issue the same way.

## Don't
- Don't build in this chat. Don't write to the live database or run importers, exports or models against it.
- Don't test scripts by setting `DATABASE_PATH`: `.env` overrides it. Use a code export with its own `.env`.
- Don't edit `.planner/` while a `planner.py save` is running.

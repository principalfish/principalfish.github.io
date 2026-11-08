# Resume point — read this first

## Where we are
0005 (GitHub #82), state `investigating`. `issue.md` is approved and on GitHub. `plan.md` is written and with the user for approval. `callers.md` and `tools/rebuild-proof.sh` are on the planner branch.
State checks: `git rev-parse --short origin/master` should be `35e5326c`; if it has moved, `callers.md` line numbers have drifted. No build branch yet. The console issue hasn't been created.

## How it got here
Every answered decision is in `decisions.md` Settled, dated.

## The pending decision (exact)
Approve `plan.md`, including the proposed manual reviews on pieces 1, 8 and 10 and the live step on piece 8.

## What the next session does
1. On approval: add the `## Plan` checklist to `issue.md`, `gh issue edit 82`, `planner.py set 5 state=planned`.
2. Create the console issue with `planner.py new --mode build`, rename this chat `planner: data-restructure`, and plan it the same way.
3. Offer to start the builder only with a next-stage briefing for piece 1.

## Don't
- Don't build in this chat. Don't write to the live database or run importers, exports or models against it.
- Don't test scripts by setting `DATABASE_PATH`: a `.env` overrides it when present.
- Don't edit `.planner/` while a `planner.py save` is running.

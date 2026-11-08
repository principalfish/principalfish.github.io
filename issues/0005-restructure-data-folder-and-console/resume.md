# Resume point — read this first

## Where we are
Planning for 0005, state `investigating`. The ledger in `decisions.md` is confirmed ("Go ahead", 2026-11-08). The folders `issue.md` draft is with the user for approval and isn't written yet.
State checks: `git rev-parse --short origin/master` should be `35e5326c`. If it has moved, re-check the findings' line numbers. No build branch yet. The console issue hasn't been created.

## How it got here
Every answered decision is in `decisions.md` Settled, dated.

## The pending decision (exact)
Approve the draft issue text: title "Reorganise data/ by pipeline role", covering the folder work only.

## What the next session does
1. If the draft was lost, redraft it from `decisions.md`, covering the folders only, and get explicit approval.
2. On approval: write `issue.md`, `planner.py set 5 title=…`, set the branch to `restructure-data-folder`, `gh issue create`, then write `plan.md` with current and proposed structure, every caller (from `findings/`), and the scratch rebuild as the behaviour proof.
3. Create the console issue with `planner.py new --mode build`, and rename this chat `planner: data-restructure`.
4. Plan the console issue the same way.

## Don't
- Don't build in this chat. Don't write to the live database or run importers, exports or models against it.
- Don't test scripts by setting `DATABASE_PATH`: a `.env` overrides it when present. Use a code export with its own `.env`.
- Don't edit `.planner/` while a `planner.py save` is running.

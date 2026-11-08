# Resume point — read this first

## Where we are
Planning discussion for 0005, state `investigating`. Three read-only sweeps are finished and saved under `findings/`. The first round of questions is with the user; nothing is decided beyond the scope.
State checks: `git rev-parse --short origin/master` should be `35e5326c`. If it has moved, re-check the findings' line numbers. No build branch yet.

## How it got here — decisions already answered, in order
1. 2026-11-08 — Scope → the whole of `data/` (UK and US) plus a console rethink, not just the US importers.

## The pending decision (exact)
1. One issue or two (recommendation: two, folders and console, planned together in this chat).
2. Axis for `data/`: role first, jurisdiction second (recommended), or jurisdiction first.
3. `model_support/`: leave for after 0003 (recommended), or move now and regenerate the hashed report.
4. Console scope (recommendation: nav, consolidation, URL renames and confirms; defer jobs and new diagnostics).
5. Open: what hurts in the console today?

## What the next session does
1. Read `decisions.md`, then the `findings/` files as needed.
2. Put any unanswered question above to the user.
3. Continue the discussion; then the later questions in `decisions.md`'s Open list.

## Don't
- Don't build in this chat. Don't write to the live database or run importers, exports or models.
- Don't edit `.planner/` while a `planner.py save` is running.

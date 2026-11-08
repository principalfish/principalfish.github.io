# Resume point — read this first

## Where we are
Planning discussion for 0005, state `investigating`. Three read-only sweeps are saved under `findings/`. The issue split, axis, `model_support/` timing and console scope are settled. The second round of questions went unanswered, because the user replied with a separate instruction instead.
State checks: `git rev-parse --short origin/master` should be `35e5326c`. If it has moved, re-check the findings' line numbers. No build branch yet. The console issue hasn't been created.

## How it got here — decisions already answered, in order
1. 2026-11-08 — Scope → the whole of `data/` (UK and US) plus a console rethink.
2. 2026-11-08 — One issue or two → two, planned together in this chat.
3. 2026-11-08 — Axis → role first, jurisdiction second.
4. 2026-11-08 — `model_support/` → moves now.
5. 2026-11-08 — Console scope → family nav, consolidated templates, consistent URLs, confirms and small fixes, run log, new Holyrood/US diagnostics. Background jobs were not offered and are out unless the user adds them.
6. 2026-11-08 — 0003 and 0004 → not a constraint ("lets not worry about issues 0003 and 0004").

## The pending decision (exact)
Unanswered, with recommendations in `decisions.md` Open: what hurts in the console; which issue goes first (folders); the hashed report (regenerate once); finished one-off scripts (keep, grouped); `core/` (yes, last).

## What the next session does
1. Take the user's reply on the defaults above. Don't treat any of them as agreed until they say so.
2. Empty the Open list, present the full ledger, and get an explicit go-ahead.
3. Draft `issue.md` and create the console issue.

## Don't
- Don't build in this chat. Don't write to the live database or run importers, exports or models.
- Don't edit `.planner/` while a `planner.py save` is running.

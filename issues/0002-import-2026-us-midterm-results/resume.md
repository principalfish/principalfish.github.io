# Resume point — read this first

## Where we are
Discussion stage of issue 0002 (build, state open). Open is empty; the full ledger was presented and is awaiting the user's confirmation ("that's everything" / corrections) before issue.md is drafted.
State checks: planner record only, no source changes. `python3 ~/.claude/lib/planner.py meta 0002` shows state open; master at or after `9ac8b04d` (`git rev-parse --short master`). `grep -A1 '^## Open' decisions.md` shows Open empty.
If anything differs, stop and tell the user.

## How it got here — decisions already answered, in order
1. 2026-11-07 — Which races? → House + Senate; governors out.
2. Timing? → Provisional now, re-runnable tools.
3. Scoring? → Separate issue.
4. Source? → NYT static JSON plus a disclaimer.
5. Uncalled? → Store votes, no winner, flag uncalled.
6. Publish? → Yes, with the disclaimer.
7. Past years? → NYT 2018–2026 replaces 538 for House + Senate.
8. Maps? → One map id per district set.
9. Disclaimer? → Source line under the map.
10. 2026 shapes? → cd119 plus the state enacted plans, stitched.
11. 2018/2020? → One cd116 map (NC 2020 inaccuracy accepted).
12. Forecasts? → Roll baselines to 2028 here; freeze the 2026 forecasts.
13. Current Senate? → Derived from the 2026 results.
14. Anchor? → The 2026 result.
15. FL/OH 2028 baseline? → Per-seat override, generalising senateSpecialElections.
16. Raw files? → Local cache; commit converted only.
17. Runoffs/RCV? → Decisive round.
18. Clashing specials? → Regular on the map; special in a separate list.

## The pending decision (exact)
Confirmation that the ledger in decisions.md is complete and correct. The user may also promote any Assumed item to Open.

## What the next session does
1. Run the state checks.
2. On confirmation, draft issue.md (Planning step 1) and iterate until approved.

## Don't
- Edit source (planner is read-only on source).
- Run imports or any DB write.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.

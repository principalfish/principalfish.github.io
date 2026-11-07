# Resume point — read this first

## Where we are
Discussion stage of issue 0002 (build, state open). Scope, timing, source (NYT JSON) and uncalled-seat handling are settled. Pending: whether the results are published to the public site.
State checks: planner record only, no source changes. `python3 ~/.claude/lib/planner.py meta 0002` shows state open; master at or after `6f2129d7` (`git rev-parse --short master`).
If anything differs, stop and tell the user.

## How it got here — decisions already answered, in order
1. 2026-11-07 — Which races? → House + Senate; governors out.
2. 2026-11-07 — Timing? → Import provisional results now, with re-runnable tools.
3. 2026-11-07 — Forecast scoring? → Wanted, but as a separate issue.
4. 2026-11-07 — Source? → NYT static JSON, with a disclaimer (BBC only embeds AP widgets; see threads/sources.md).
5. 2026-11-07 — Uncalled races? → Store the leader's votes, no one elected, flag uncalled with % counted.

## The pending decision (exact)
Publication. The user said the work is "not for profit or even published", but `uselectionmaps/data/results/` is public on GitHub Pages. Options: export 2026 to the site with the disclaimer, or keep it in the DB/console only. Recommendation: export with the disclaimer, since that's what the maps are for. It decides whether export/manifest/front-end work is in scope.

## What the next session does
1. Run the state checks.
2. Get the publication answer, then the disclaimer wording and placement.
3. Next questions: House geometry (map 21 is the 2024 district set; several states redrew for 2026, list to confirm), then downstream anchors (current-us-house/senate, predictBaselineElectionId, nextElectionYear, senate-current.json after 2027-01-03).

## Don't
- Edit source (planner is read-only on source).
- Run imports or any DB write.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.

# Resume point — read this first

## Where we are
Discussion stage of issue 0002 (build, state open). Scope, timing and the scoring split are settled; the data source is the blocker. NYT and BBC were requested but could not be fetched: the session's web-fetch tool refused both hosts, so their data format and terms are unknown.
State checks: planner record only, no source changes. `python3 ~/.claude/lib/planner.py meta 0002` shows state open; master at or after `6f2129d7` (`git rev-parse --short master`).
If anything differs, stop and tell the user.

## How it got here — decisions already answered, in order
1. 2026-11-07 — Which races? → House + Senate; governors out.
2. 2026-11-07 — Timing? → Import provisional results now, with re-runnable tools.
3. 2026-11-07 — Forecast scoring? → Wanted, but as a separate issue.
4. 2026-11-07 — Source? → User asked to check NYT or BBC (pending, see below).

## The pending decision (exact)
How to inspect NYT/BBC, or pick another source. Options put to the user:
- curl the pages from this machine (the existing scrapers already make live HTTP requests), into a fresh scratch dir;
- inspect the pages in the user's own Chrome (Claude in Chrome: network tab finds the JSON endpoint);
- fall back to Wikipedia (CC BY-SA, the scrape_holyrood_2026.py pattern).
Caveat raised: NYT and BBC results are AP/licensed, copyrighted data; reuse terms are unchecked.
Also still Open, not yet asked: House geometry (map 21 is the 2024 district set; several states redrew for 2026), Senate specials (FL/OH), uncalled-seat handling, and the downstream anchors (see decisions.md).

## What the next session does
1. Run the state checks.
2. Act on the user's source answer; report the data format (keys, candidates, votes, call flag, % reporting).
3. Ask the House geometry question next.

## Don't
- Edit source (planner is read-only on source).
- Run imports or any DB write.

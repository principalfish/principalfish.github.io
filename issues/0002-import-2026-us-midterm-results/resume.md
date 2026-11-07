# Resume point — read this first

## Where we are
Discussion stage of issue 0002 (build, state open). Settled: scope, source (NYT JSON, all years 2018–2026), uncalled handling, publication with disclaimer, one map id per district set. Pending: where the 2026 district shapes come from. The user asked whether the state files are county-level shapes; answered in chat (they are district polygons).
State checks: planner record only, no source changes. `python3 ~/.claude/lib/planner.py meta 0002` shows state open; master at or after `9ac8b04d` (`git rev-parse --short master`).
If anything differs, stop and tell the user.

## How it got here — decisions already answered, in order
1. 2026-11-07 — Which races? → House + Senate; governors out.
2. 2026-11-07 — Timing? → Import provisional results now, with re-runnable tools.
3. 2026-11-07 — Forecast scoring? → Wanted, but as a separate issue.
4. 2026-11-07 — Source? → NYT static JSON with a disclaimer (threads/sources.md).
5. 2026-11-07 — Uncalled? → Store votes, no one elected, flag uncalled with % counted.
6. 2026-11-07 — Publish? → Export to the public site with the disclaimer.
7. 2026-11-07 — Past years? → NYT for 2018–2026, replacing the 538 data via --refresh; three schema adapters.
8. 2026-11-07 — Map organisation? → One map id per district set (21 = 2022–24; new ids for 2026 and the 2012 cycle).
9. 2026-11-07 — Disclaimer placement? → Source line under the map only.

## The pending decision (exact)
2026 district geometry source, for the 9 redrawn states (CA, TX, UT, OH, NC, FL, TN, LA, AL; threads/redistricting.md). Options: (a) recommended: cd119 for 41 states plus each state's official enacted-plan shapefile, stitched into a new map; (b) Redistricting Data Hub; (c) wait for Census 120th-Congress files (likely 2027) and hold the House map. Also open: the 2012-cycle geometry for 2018/2020 (Census cd116; NC 2020 and PA 2018 court maps).

## What the next session does
1. Run the state checks.
2. Get the geometry answer, then ask about the 2012-cycle map and the downstream anchors (current-us-house/senate, predictBaselineElectionId, nextElectionYear, senate-current.json after 2027-01-03).

## Don't
- Edit source (planner is read-only on source).
- Run imports or any DB write.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.

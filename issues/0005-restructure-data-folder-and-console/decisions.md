# Decisions: Restructure the data folder and rethink the data console

Full sweep reports, read-only at master `35e5326c`: `findings/us-importers.md`, `findings/data-structure.md`, `findings/console.md`.

## Settled
- Scope is wider than the original US importer tidy: a full assessment of `data/`, UK and US, plus a rethink of the data console (user, 2026-11-08)
- Nothing is built in this chat; done means approved `issue.md` and `plan.md` with current and proposed structure, every caller of what moves, and how behaviour preservation is checked (2026-11-08)
- Two issues, planned together in this chat, with separate branches and PRs: this one for the folders, a new one for the console (user, 2026-11-08)
- `data/` is organised role first, jurisdiction second (user, 2026-11-08)
- `model_support/` and the Westminster calibrate/validate scripts move now (user, 2026-11-08). How the hashed validation report on master is handled is still open
- Issues 0003 and 0004 are not a constraint on this work: no sequencing, path or merge decision here is made for their sake, and their plans aren't edited (user, 2026-11-08)
- Console scope: navigation by family with a fixed stage order, consolidated templates, consistent URLs (28 routes, redirects on GET pages only), missing confirms and small fixes, a run log, and new Holyrood/US diagnostic views (user, 2026-11-08)
- Folders issue goes first; the console issue follows and mirrors the finished folders (user, 2026-11-08)
- The hashed Westminster validation report is regenerated after `model_support/` moves, with the user's go-ahead at the time (user, 2026-11-08)
- Finished one-off scripts go in one `data/archive/` folder, outside the normal checks (user, 2026-11-08). The TopoJSON builders stay live
- Console pain, all four: finding things, running and waiting, inconsistent pages, looks and readability (user, 2026-11-08)
- Console has five uniform sections: Westminster, Holyrood, House, Senate, President (user: "maybe", 2026-11-08). Each has auto import polls, import poll, view polls, run models with params, view model runs, and delete
- One global "export elections" with tick boxes for its options (user, 2026-11-08)
- Section-specific tools stay where they belong, e.g. by-election import for the UK (user, 2026-11-08)
- Rebuild, backup and restore move to an admin page (user, 2026-11-08)

## Open
- "Import poll" for Holyrood, House, Senate and President: no single-poll importer exists for them today (Westminster has 11 pollster importers). Recommendation: show it only where an importer exists.
- What the export tick boxes are. Recommendation: sections, then what to write (results, current forecast, metadata), with dry run on by default. Today's flags are mutually exclusive and there is no section filter.
- Background runs with live progress: the user named running and waiting as a pain, but only a run log has been agreed. Recommendation: include, as the console issue's final pieces.
- Is `core/` worth 271 rewritten imports? Recommendation: yes, as the last piece, with a test pinning `.env` to the repo root.

## Assumed
- Local `master` and `origin/master` are both `35e5326c` (user pulled during this chat; confirmed)
- "delete polls, delete polls" in the user's console answer means deleting polls and deleting model runs; both exist today
- `UNOPPOSED_NOMINAL_VOTES` moves into `scripts/us_results/convert.py` before `convert_538_house.py` is archived, because the console needs it at startup
- Tests that only exercise archived scripts are retired with them; test files that also cover live importers are split, not dropped
- Background rebuild test on a scratch copy is running; its result decides whether rebuild needs fixing before it moves to the admin page

## Checks run 2026-11-08 (no live database touched)
- Restore works: `backup.restore_latest` unpacked the newest archive to a scratch path, passed the integrity check, kept the old file as `.prerestore`, and all 15 tables match live row counts

## Brief corrections, verified against master
- `convert_538_house.py` is still needed: `scripts/us_results/convert.py:75` imports its constant at module level, and the console imports that at startup. `convert_538_senate.py` is test-only.
- Both presidential converters are manual one-shots; only tests call them.
- `cli.py:57-60` inserts on `sys.path`; only `convert.py` and `current_senate.py` append.
- `old_data/scripts/usa/` also holds `regions`, `convert_senate_current`, `house_2026_sources` and `check_redistricting`.
- `us_electoral_votes` is live: rebuild step 2, through `migrate_us_electoral_votes.py:37`.

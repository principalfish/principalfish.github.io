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
- "Import poll" appears only where an importer exists: Westminster keeps it, the other four show auto import only (user, 2026-11-08)
- Export page tick boxes: the five sections, then what to write (results, current forecast, metadata), with dry run ticked by default. This needs a section filter added to the export script (user, 2026-11-08)
- Background runs with live progress are in scope, as the console issue's final pieces (user, 2026-11-08)
- Shared root modules move to `data/core/` as the folder issue's last piece, with a test pinning `.env` to the repo root; `models.py` becomes `schema.py` (user, 2026-11-08)
- `data/` becomes an editable install (`editable_mode=compat`, confirmed in isolation), replacing the `sys.path` adjustments; scripts keep running by path (user, 2026-11-08)
- `backfill_poll_bases`, `repair_poll_records`, `repair_regional_poll_rows`, `reset_us_poll_matchups` and `scrape_holyrood_2026` also go to `archive/`, with their tests retired (user, 2026-11-08)
- The five archived US converters go to `archive/` unchanged, with the archive README saying why; they keep their old imports and aren't kept running (user, 2026-11-09, by starting piece 5 on that recommendation)

## Open

## Assumed
- Local `master` and `origin/master` are both `35e5326c` (user pulled during this chat; confirmed)
- "delete polls, delete polls" in the user's console answer means deleting polls and deleting model runs; both exist today
- `UNOPPOSED_NOMINAL_VOTES` moves into `scripts/us_results/convert.py` before `convert_538_house.py` is archived, because the console needs it at startup
- Tests that only exercise archived scripts are retired with them; test files that also cover live importers are split, not dropped

## Checks run 2026-11-08 (no live database touched)
- Restore works: `backup.restore_latest` unpacked the newest archive to a scratch path, passed the integrity check, kept the old file as `.prerestore`, and all 15 tables match live row counts
- Full rebuild works: on that scratch copy, from a code export of `35e5326c`, 43 of 43 steps succeeded in about a minute. All 15 table counts are unchanged, per-election vote counts and totals are identical for all 1,757 elections, and the exported `electionmaps/data` and `uselectionmaps/data` are byte-identical to the repo. Live database hash and mtime unchanged, repo clean
- The check above is repeatable, so it becomes the folder issue's end-to-end proof of behaviour preservation. Method: a code export with its own `.env` pointing at a scratch restore (`.env` is loaded with override when present, so a `.env` beside the code is what reliably decides the database path)
- Not exercised: the console's own Rebuild and Restore buttons. Those routes have tests with the work mocked

## Brief corrections, verified against master
- `convert_538_house.py` is still needed: `scripts/us_results/convert.py:75` imports its constant at module level, and the console imports that at startup. `convert_538_senate.py` is test-only.
- Both presidential converters are manual one-shots; only tests call them.
- `cli.py:57-60` inserts on `sys.path`; only `convert.py` and `current_senate.py` append.
- `old_data/scripts/usa/` also holds `regions`, `convert_senate_current`, `house_2026_sources` and `check_redistricting`.
- `us_electoral_votes` is live: rebuild step 2, through `migrate_us_electoral_votes.py:37`.

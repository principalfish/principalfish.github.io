# Decisions: Redesign the data console into five uniform sections

Follows 0005 (GitHub #82), which reorganises `data/`. The route inventory and page-by-page assessment are in `../0005-restructure-data-folder-and-console/findings/console.md`, read-only at master `35e5326c`.

## Settled
- This is a separate issue from the folder work and is built after it; the console's internal layout mirrors the finished folders (user, 2026-11-08)
- Five uniform sections: Westminster, Holyrood, House, Senate, President (user, 2026-11-08)
- Each section has auto import polls, view polls, run models with parameters, view model runs, and delete for polls and runs (user, 2026-11-08)
- "Import poll" appears only where an importer exists: Westminster keeps it, the other four show auto import only (user, 2026-11-08)
- Section-specific tools stay in their section, e.g. by-election import for the UK (user, 2026-11-08)
- One global "export elections" page: tick the five sections, then what to write (results, current forecast, metadata), with dry run on by default. Needs a section filter added to the export script (user, 2026-11-08)
- Rebuild, backup and restore live on an admin page (user, 2026-11-08)
- Also in scope: consolidated templates, consistent URLs with redirects on GET pages only, missing confirms and small fixes, a run log, and background runs with live progress as the final pieces (user, 2026-11-08)
- The pain to fix: finding things, running and waiting, inconsistent pages, looks and readability (user, 2026-11-08)
- Must stay exactly: every transparency view and number, CSRF and host pinning, the backup hook, preview tokens, `expected_index`, locks, dry-run defaults, existing confirms
- Each model run form has an "export when done" tick box, on by default for real runs (user, 2026-11-08)
- Run log and background job records are files beside the database, one per run and pruned to a set number; no schema change (user, 2026-11-08)
- No "run all US" action: House, Senate and President each run on their own (user, 2026-11-08)
- Full stage-by-stage transparency for Holyrood and US (polls and weights, sample sizes, averaged shares, swings, each seat's calculation and history) is wanted, as its own issue after the console. No uncertainty models are added (user, 2026-11-08)
- Section slugs are `westminster`, `holyrood`, `house`, `senate`, `president`; piece 4 builds the URLs from them (user, 2026-11-09, by starting piece 1 on that recommendation)
- Piece 1 moves each route file whole; `westminster.py` and `holyrood.py` are split into model and outputs only when piece 3 rewrites the outputs routes (user, 2026-11-09, by starting piece 1 on that recommendation)
- `home.py` goes to `console/home.py` and `site.py` to `console/admin/site.py` (user, 2026-11-09, by starting piece 1 on that recommendation)
- `mypy.ini` gets one `type-var` section per new package holding routes, keeps the `arg-type` exception for the US routes, and the pin in `tests/test_project_layout.py` follows; no blanket `console.*` section (user, 2026-11-09, by starting piece 1 on that recommendation)
- The three US sections are titled "US House", "US Senate" and "US President"; slugs stay `house`, `senate`, `president` (user, 2026-11-09, during piece 2's review; the slugs are unchanged by assumption, stated to the user)
- Chart.js is vendored into `console/static/` at one pinned version; a third-party file in the repo is accepted so charts work offline (user, 2026-11-09, by starting piece 3 on that recommendation)
- Queue pages merge one at a time, stopping short of anything that changes the rendered HTML (user, 2026-11-09, by starting piece 3 on that recommendation)
- The uncertainty template reformat is proven by comparing rendered HTML with whitespace collapsed (user, 2026-11-09, by starting piece 3 on that recommendation)
- Piece 3 gets a short manual review of the charts (user, 2026-11-09, by starting piece 3 on that recommendation)
- The console gets a favicon, as the plan's final piece (user, 2026-11-09)
- The shared US pages (`/us/import…`, `/us/results…`, `/us/run-models`, the matchup save action) stay until piece 5, which carries their redirects (user, 2026-11-09, by starting piece 4 on that recommendation)
- Piece 4's URLs are as listed in `plan.md`, including `/westminster/model`, `/westminster/outputs`, `/holyrood/model`, `/holyrood/polls/import` and `/house/matchups`, `/senate/matchups`, `/president/matchup` (user, 2026-11-09, by starting piece 4 on that recommendation)
- `/site/*`, `/db/*`, `/polls` and `/polls/<id>` keep their URLs in piece 4 (user, 2026-11-09, by starting piece 4 on that recommendation)
- The old queue `/finish` URLs get no redirect and return 404, because GET there runs the model and export (user, 2026-11-09, by starting piece 4 on that recommendation)
- Piece 4 has no manual review; the redirects are proven by tests (user, 2026-11-09, by starting piece 4 on that recommendation)
- The nav finds the current section from a table of endpoints, plus the `chamber` value for the shared US endpoints, not from the URL (user, 2026-11-09, by starting piece 2 on that recommendation)
- `/polls` and poll detail sit under a top-level "All polls" link with no current section; `/polls/coverage` sits under Westminster (user, 2026-11-09, by starting piece 2 on that recommendation)
- New pages are `/westminster`, `/holyrood`, `/house`, `/senate`, `/president`, `/admin`; the 75 existing rules stay unchanged (user, 2026-11-09, by starting piece 2 on that recommendation)
- Until piece 5 replaces it, the "Run US models" form appears on all three US landing pages through one shared partial (user, 2026-11-09, by starting piece 2 on that recommendation)

## Open

## Assumed
- US matchups go to their own section (President, Senate, House); NYT results to House and Senate (there is no presidential NYT source); Current Senate to Senate
- US auto import uses the scraper's existing contest filter to run per section
- URLs follow one shape: `/<section>/polls`, `/<section>/polls/import`, `/<section>/model`, `/<section>/outputs`

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
- Also in scope: consolidated templates, consistent URLs with redirects on GET pages only, missing confirms and small fixes, a run log, new Holyrood and US diagnostic views, and background runs with live progress as the final pieces (user, 2026-11-08)
- The pain to fix: finding things, running and waiting, inconsistent pages, looks and readability (user, 2026-11-08)
- Must stay exactly: every transparency view and number, CSRF and host pinning, the backup hook, preview tokens, `expected_index`, locks, dry-run defaults, existing confirms

## Open
- Does a model run still export automatically, now that export has its own page?
- Where the run log and background job state live.
- How deep the new Holyrood and US diagnostics go.
- Whether a "run all US" action survives the split into three sections.

## Assumed
- US matchups go to their own section (President, Senate, House); NYT results to House and Senate (there is no presidential NYT source); Current Senate to Senate
- US auto import uses the scraper's existing contest filter to run per section
- URLs follow one shape: `/<section>/polls`, `/<section>/polls/import`, `/<section>/model`, `/<section>/outputs`

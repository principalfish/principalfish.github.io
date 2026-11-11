# feat: redesign the data console into five uniform sections

The data console had grown page by page. It had no navigation, "run model", "outputs" and "import" each came in three URL shapes, Westminster, Holyrood and the US did the same jobs in different ways, and long runs blocked the page and lost their output once you left. This rebuilds it around five uniform sections (Westminster, Holyrood, US House, US Senate, US President) without losing any diagnostic number or safeguard.

## What changed

- **Sections and navigation.** One section registry drives a top bar on every page and a landing page per section. Every section uses the same URL shape (`/<section>/polls`, `/polls/import`, `/model`, `/outputs`), each US chamber has its own poll list, auto import, model form and results pages, and the home page is links only. Old addresses are gone and return 404, with no redirect table.
- **Run forms.** Every model form has dry run on by default and an "Export when done" box; a dry run never exports. Each US chamber runs on its own with its poll window, half-life, seat prior weight, ignore-seat-polls and rebuild options.
- **Export page.** `/export` ticks sections and what to write (results, current forecast, metadata), dry run first, as one run of `export_elections.py` with new `--sections` and `--content` flags. A one-section export leaves other sections' files and manifest entries as they were; a run with neither flag is byte-identical to before. `/site/rebuild` and the per-chamber results export are gone.
- **Admin page, confirms and fixes.** Rebuild database, Backup and Restore live on `/admin`. Poll and output deletes (single and bulk), matchup history rebuilds and queue abandon now ask first. A queue finish that writes on GET requests a backup, and Westminster's finish takes a per-token lock.
- **Run log.** Every command the console runs is recorded in `console-runs/` beside the database (newest 200, `CONSOLE_RUN_LOG_KEEP`), with its command, times, exit code and full output, viewable read-only at `/runs` and `/runs/<id>`.
- **Background runs.** Model runs, matchup-page history rebuilds, the Export page and Rebuild database run as background jobs: the form returns at once and `/runs/<id>` shows live output. One run slot covers every job, import step, queue finish and Restore, so runs are refused (not queued) while another holds it. Runs left running when the console stops are marked interrupted at the next start.
- **Looks.** Colour and spacing tokens, dark mode following the system setting, right-aligned numbers, tables that scroll sideways at narrow width, no inline styles, and the n_eff columns shown only where they're computed (Westminster and All polls).
- **Docs and favicon.** A new `data/docs/console.md`, the US, results, database and export docs updated, and a test that every console path and relative link in the docs resolves. The console has its own tab icon.

## Behaviour changes to know

- Westminster and Holyrood model runs are refused while any other run holds the slot, and so is Restore.
- A Westminster queue finish refused for a busy slot stays refused on refresh; run the model from its page afterwards.
- A model form posted without a `dry_run` field runs as a dry run.
- A Holyrood model run writes `results/holyrood-prediction.json` itself, even with "Export when done" unticked.

## Review

Every piece was checked by correctness and security (Opus) and coverage, style and common-mistakes (Sonnet), with a fresh verifier, and pieces with visible changes were reviewed by hand. The self review of the whole branch found no blocking issues; all 13 low findings were fixed in `5644269f` (a refused Westminster finish now recorded, dry run as the form default, a record for jobs that run no step, the US form redrawn on refusal, a Vitest test for `run_live.js`, direct tests for the export page-skip guards, two refactors and wording). A re-check confirmed them; two small leftovers were skipped: the slot-holder comment in `data/console/us/models.py` doesn't list Restore, and the Westminster finish reads the running job's id through `jobs.busy_refusal(...)` rather than `jobs.running_job_id()`.

Known limits, documented in `data/docs/console.md`: stopping or timing out a job stops only the script it started, not processes that script starts; a `CONSOLE_DEBUG=1` reload marks a running job interrupted.

Worth checking by hand: a dry-run model run watched live at `/runs/<id>`, a second run refused while it goes, and a one-section dry run on `/export`.

Closes #83

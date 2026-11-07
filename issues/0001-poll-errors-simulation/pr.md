# feat: add Monte Carlo poll-error uncertainty to Westminster forecasts

Adds honest uncertainty to the Westminster nowcast. Poll sample sizes and bases now feed an effective n and a margin of error per poll. A Monte Carlo engine (10,000 simulations per run), calibrated against historical final-poll errors, turns the poll average into seat ranges and outcome probabilities. These appear in the data console, on the map page and on the poll tracker. A reproducible validation harness checks the shipped model against frozen inputs.

**What readers see on the Westminster map page**
- The headline leads with the simulation, e.g. "Current prediction · Hung parliament likely (56%) · Labour largest party (99%)", then "Labour 317 seats (238–389) · majority 44%". Below the latest poll line, a smaller note explains that this is a nowcast (if an election were held today) built from 10,000 simulations. Ties and party majorities get their own wording, and both lines share one rounding helper, so the percentages always agree.
- The vote-totals table shows each party's simulated mean seats, rounded by largest remainder so the table adds up to 650 (or to a region's total), with ± against 2024. "Show uncertainty" adds the 90% ranges to the Seats and Vote % cells, e.g. "317 (238–389)" and "27.45 (23.4–31.3%)". A short note says the map colours show each seat's uniform-swing winner. Filtered views (party, majority, gains, upcoming) have no stored simulation and keep the uniform-swing count. Seat popups show each party's vote range and win chance.
- The poll tracker draws the simulated mean seats with a 90% band, and a Chances view shows majority, largest-party and hung odds over time. The history starts on the 2024-07-05 election result, with bands from 2024-08-07 when the first polls arrive. The blank left gutter in the Chances and vote-only views is gone.
- Historical elections, predict view, user scenarios and the US page are unchanged.

**Model settings and data**
- Westminster poll weighting is now a 15-day half-life over a 60-day window, in both the console form defaults and `run_uns_model.py` (it was a 30-day half-life over 30 days). The console's automatic run after a poll import picks this up too.
- The full history (848 daily runs, 2024-07-05 to 2026-10-30) was rerun with these settings. Every run stores its uncertainty summary, and the latest also stores mean seats. The committed data files are that backfill: the tracker trends, their meta, and the 2026-10-30 prediction and uncertainty files. The site changes only when this merges.
- The export refuses a run whose stored summary has no mean seats, rather than silently showing something else.

**Scope**
Westminster only. The US uncertainty work moved to its own branch (`us-poll-errors-uncertainty`). The offline conversion-error experiment and the model refinements the branch review called for (correlated conversion error, heavier seat tails, parameter uncertainty) moved to the refinement branch (`refine-westminster-and-us-poll-error-models`). The seat ranges are known to be on the narrow side; that work is where they get revisited.

**Before and after merging**
- Until the US results import lands on master, a full `export_elections.py` fails on this branch: it tries to export the new US elections in the shared database, whose map isn't here. Use `export_elections.py --current-simulation` for Westminster in the meantime.
- Don't run Westminster poll imports or model runs from a checkout of master until this merges. Master's console still has the old weighting and stores no uncertainty, so its runs would break the history.

**Review**
Two self-reviews, each with fresh check subagents (correctness, security, coverage, style, common-mistakes) and a separate verifier. Every piece also went through the builder's own check loop.
- The first, of the whole branch, found that the tracker broke its lines at every run on legacy data. It also found test gaps in the seat popup, the election load and the frozen validation inputs, plus console error handling. All of these were fixed, and a follow-up round added a pinned jsdom dev dependency so the DOM tests actually run under `npm test`. Two low items were skipped by choice: a test for an unpolled run between differently configured runs, and a test-name nit.
- The second, of the later commits, found three documentation items (fixed). It also surfaced that the tracker had lost its election-day starting point, which was restored.
- Correctness and security were clean throughout the later pieces.

**Worth checking by hand**
- The map page header, the table with "Show uncertainty" on and off, a region view and a party filter.
- The poll tracker in Seats & votes and Chances, including the Last 30 and Last 90 ranges.
- The console's Westminster model-run form, which should default to 60 days back and a 15-day half-life.

**Follow-up**
A single-date `run_uns_model.py --dry-run --as-of-date` for a past date measures its poll window from today rather than from that date. This predates the branch and is left as is.

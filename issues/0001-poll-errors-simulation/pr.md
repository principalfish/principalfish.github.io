# feat: add Monte Carlo poll-error uncertainty to Westminster forecasts

Adds honest uncertainty to the Westminster nowcast. Poll sample sizes and bases now feed an effective n and a margin of error per poll. A Monte Carlo engine (10,000 simulations per run), calibrated against historical final-poll errors, turns the poll average into seat ranges and outcome probabilities. The map page and poll tracker still open on the uniform-swing view, and the simulated figures sit behind a tickbox on each. The data console shows every stage's numbers, and a reproducible validation harness checks the shipped model against frozen inputs.

Closes #81

**What readers see on the Westminster map page**
- The page opens on uniform swing, with the same headline and table as before. A new "Show with modelled uncertainty" tickbox below the header switches it to the simulation. The old "Show uncertainty" button is gone. The tickbox state lasts until the page is next loaded.
- Ticked, the headline leads with the simulation, e.g. "Current prediction · Hung parliament likely (56%) · Labour largest party (99%)", then "Labour 317 seats (238–389) · majority 44%". Ties and party majorities get their own wording, and both lines share one rounding helper, so the percentages always agree.
- Ticked, the vote-totals table shows each party's simulated mean seats with 90% ranges, e.g. "317 (238–389)" and "27.45 (23.4–31.3%)". Means are rounded by largest remainder so the table adds up to 650 (or to a region's total), with ± against 2024. A short note says the map colours show each seat's uniform-swing winner. Filtered views (party, majority, gains, upcoming) have no stored simulation and keep the uniform-swing count. Seat popups add each party's vote range and win chance.
- "Latest poll used" moves to a smaller line below the map. When the box is ticked, it's joined by a note that this is a nowcast from 10,000 simulations, and by "Beta: this model is new and may change significantly."

**The poll tracker**
- It also opens as before: Seats and Vote % ticked, the uniform-swing line, and nothing else new except its Uncertainty box and the latest-poll line.
- Ticking Uncertainty draws the simulated mean seats with a 90% band and adds a Chances view of majority, largest-party and hung odds over time. The history starts on the 2024-07-05 election result, with bands from 2024-08-07 when the first polls arrive.
- The blank left gutter in the Chances and vote-only views is gone.

Historical elections, predict view and user scenarios are unchanged. The US page is unchanged apart from its latest-poll line moving below the map.

**Model settings and data**
- Westminster poll weighting is now a 15-day half-life over a 60-day window, in both the console form defaults and `run_uns_model.py` (it was a 30-day half-life over 30 days). The console's automatic run after a poll import picks this up too.
- The full history (848 daily runs, 2024-07-05 to 2026-10-30) was rerun with these settings. Every run stores its uncertainty summary, and the latest also stores mean seats. The committed data files are that backfill: the tracker trends, their meta, and the 2026-10-30 prediction and uncertainty files. The site changes only when this merges.
- On 2026-10-30 Labour is 354 on uniform swing and 317 as the simulated mean. The new weighting and data fixes moved the uniform-swing figure a few seats; most of the gap to the mean is the cost of error falling on Labour's many narrow seats.
- The export refuses a run whose stored summary has no mean seats, rather than silently showing something else.
- New operational scripts and `SIMULATIONS_PATH` are documented in the README, with their run order.

**Scope**
Westminster only. The US uncertainty work moved to its own issue and branch (`us-poll-errors-uncertainty`). The offline conversion-error experiment and the model refinements the review called for (correlated conversion error, heavier seat tails, parameter uncertainty) moved to #78. The seat ranges are known to be on the narrow side; that work is where they get revisited.

**Before and after merging**
- Master (including the US results import, #80) is merged into this branch, and the conflicts are resolved. The test suites pass on the merged tree, and a full `export_elections.py` completes and reproduces the committed data files exactly.
- Don't run Westminster poll imports or model runs from a checkout of master until this merges. Master's console still has the old weighting and stores no uncertainty, so its runs would break the history.

**Review**
Three self-reviews, each with fresh check subagents (correctness, security, coverage, style, common-mistakes) and a separate verifier. Every piece also went through the builder's own check loop.
- The first, of the whole branch, found that the tracker broke its lines at every run on legacy data. It also found test gaps in the seat popup, the election load and the frozen validation inputs, plus console error handling. All of these were fixed, and a follow-up round added a pinned jsdom dev dependency so the DOM tests actually run under `npm test`. Two low items were skipped by choice.
- The second, of the later commits, found three documentation items (fixed). It also surfaced that the tracker had lost its election-day starting point, which was restored.
- The third, again of the whole branch, came with a separate check that the first load matches master. Security was clean. The tracker differed from master on load in six ways, all fixed, and 10 of the 31 findings were fixed: documentation, console error tests, oversized JSON integers, a repair script's stale check, and a bad config hiding a valid report. One was waived: a small US poll-importer guard stays in this branch. The remaining 20, mostly test gaps and refactors, and 3 low items from the re-check of the fixes, are on #78.
- The master merge and the beta note did not get their own self-review; the merge's checks are the test suites and the jsdom page harness.

**Worth checking by hand**
- The map page with the tickbox off and on, a region view and a party filter.
- The poll tracker with Uncertainty off and on, in Seats & votes and Chances, including the Last 30 and Last 90 ranges.
- A US results page, since this merge combined both pieces of work in the shared engine.
- The console's Westminster model-run form, which should default to 60 days back and a 15-day half-life.

**Follow-up**
A single-date `run_uns_model.py --dry-run --as-of-date` for a past date measures its poll window from today rather than from that date. This predates the branch and is left as is.

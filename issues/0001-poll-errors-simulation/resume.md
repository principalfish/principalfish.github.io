# Resume point: read this first

Updated 2026-10-30 (builder, Piece 2a-H1 at its review pause). Issue state: `building`.

## Where we are

**Piece 2a-H1 is implemented, checked and through three analysis-loop rounds, but NOT committed.** Waiting on Phil's review and one pending data decision (below).

Check before doing anything else:
- branch `poll-errors-simulation`, HEAD `ab3fc62e`;
- working tree: untracked `data/scripts/build_westminster_historical_fixture.py`, `data/model_support/westminster_historical_transitions.py`, `data/tests/test_westminster_historical_transitions.py`, `data/models/westminster/historical-transitions-v1.json`; modified `data/model_support/westminster_validation_inputs.py` (`_content_hash` → public `content_hash`, behaviour unchanged);
- fixture `sha256sum` = `3e351456e756169699eacf15f53b7a06cb111fb8a9370c3ef4152b5b3ad88bb5`;
- raw sources in the 2026-10-30 session scratchpad `hist-sources/` (may not survive; the builder rebuilds byte-identically from them, and provenance SHA-256s are in the fixture);
- checks at the pause: mypy clean (307 files), 6438 tests passed, export dry-run lists no new elections, console builds, `git diff --check` clean.

## Pending

Phil's approval to commit H1. No open data decision.

## Decisions taken in this session (2026-10-30)

- Source for 1997, 2001 and Scotland 2005: Parliament's psephology.db behind api.parliament.uk at a pinned commit (accepted instead of scraping ~1,377 HTML pages; 3 index + 10 sampled pages matched exactly).
- Referendum Party → other (not UKIP). NI Conservatives → con; Green (NI) and Scottish Greens → green.
- 2005n unknown_other limitation accepted and recorded in the fixture audit (NI Alliance and unnamed minors in unknown_other; Greens named in 190 seats).
- Zero-vote party entries (5 seats in 2005n): option (a), dropped by the builder and rejected by the loader.
- Not retrieved: Commons Library CBP-8647 (403). No independent published national totals exist in the retrieved sources; the fixture records `independent: false`.

## After the pause

Commit H1 on approval (stage only the five files above), push, tick H1, then stop. H2 needs Phil's answers first: weight of the new transitions (equal across six recommended), and whether their held-out folds feed any guard or are report-only.

## The test 2a has to pass

- Leave-one-transition-out over the four existing Westminster transitions, holding out **2015, 2017, 2019 and 2024**, × 5 party seat-total cells = **20 outcomes**. A 90% interval must cover the actual result in **≥17/20**.
- Also: the stability guard (128 vs 256 training draws), leader/runner standardised RMS within the paired predictive 5–95% reference, national mean-vote drift ≤0.15 points, and lambda attribution.
- Benchmark: actual regional swing as the central input. Equal transition weights. Training-only fits.
- These four transitions already shaped the design, so LOO results are **retrospective diagnostics**, not fresh validation.
- Statistical contract `ec72d608bec5c35b341390d41fba27910cb171de5b624416c44b29b285db7437`. Frozen inputs: `data/models/westminster/validation-inputs-2026-10-22-v1.json`. Method: `data/models/westminster/conversion-prototype-method.md`.

## How it got here: decisions already answered, in order

1. **2026-10-28.** The direct cluster fit gave 15/20, with every 2024 cell missed. Question: investigate a more stable calibration first, or build the infrastructure switched off and defer 17/20 to joint Piece 2f? → **Phil chose to investigate.**
2. **2026-10-28.** Read-only reassessment (`assessment.md`): full-loading residual calibration and a regional-balancing diagnostic still gave 15/20. → **Phil approved revising 2a into a bounded offline experiment.** It tests the already-approved components together (nation × baseline-share clusters + heavy seat tails + lambda predictive uncertainty), with no new prior, regime term, regional balancing or new data.
3. **2026-10-29.** The eight-variant experiment completed. No-cluster controls gave 13/20 and cluster variants 15/20. The full candidate also failed RMS, stability, identification and drift. For held-out 2024: Labour 317–361 vs actual 411, Conservatives 181–242 vs 121. Decision recorded in the report: `stop_before_engine`. Question: **investigate more comparable historical evidence first (recommended), or replan the conversion model on the existing four transitions with explicitly new assumptions?** → **Phil chose to investigate more historical evidence.**
4. **2026-10-29.** Read-only research found two usable-looking transitions (details below). Then Phil asked for the active work to be committed (six commits, `c067f150` → `ab3fc62e`) and said he'd come back to the model investigation later.
5. **2026-10-30.** After the full-flow explanation, Phil chose:
   - **scope A:** 1997→2001 and 2005→2010; the 1980s/early 1990s are not included;
   - **entry "both":** the new transitions train in every original fold and are also held out as separate diagnostics; the gate stays 17/20 on the original 20 cells;
   - **storage:** a frozen, hashed in-repo fixture; no DB write, no export.

   The risk was stated and accepted: both are low/moderate-change elections, and they could lower the training cluster scales.

## Next open point

**Piece 2a-H1, not started.** Acquire and audit both transitions into `data/models/westminster/historical-transitions-v1.json`, built by `data/scripts/build_westminster_historical_fixture.py`, with loader/fingerprint tests. Then pause and show Phil the audit before any fitting.

After that:
- **2a-H2:** revise the experiment contract. Ask first: the new transitions' weight (equal across six is recommended), and whether their held-out folds feed any guard or are report-only. Write a new report file; the committed 15/20 report stays untouched. Pause before the batch.
- **2a-H3:** run the multi-hour checkpointed batch and report.

The approved plan for H1–H3 is in this issue's `plan.md` Notes (2026-10-30) and the thread 4 Notes.

### What's known about each candidate transition (from read-only research; nothing written to disk)

- **1997→2001:** 659 matching normalised constituency index names; common boundaries confirmed in Commons briefing CBP-8647. Still needed: a full audit of candidate totals, party mapping and geography.
- **2005→2010:** Parliament's new CSV has 591 notional constituencies, all matching 2010 by code and region, and every candidate-vote sum reconciles. The missing 59 Scottish seats were all retrieved from the older historical results site (382 candidate rows; 2,333,887 valid votes). Still needed: a persisted boundary/code crosswalk and party-quality checks. The old site's constituency IDs don't encode boundary changes.
- **Source limits:**
  - 349 rows in the 2005 notional file are unnamed minor-party aggregates. Don't split them, and don't turn unknown values into zeros.
  - Labour Co-op and Speaker labels need explicit handling.
  - The Commons unified CSV/XLSX endpoints returned 403.
  - The Norris 1992–2005 workbook has missing or error-valued 2005 fields, so it isn't a verified replacement.
- **Earlier decades (option B):** not researched beyond that. They'd need their own boundary and notional-results work.
- **Sources:**
  - https://electionresults.parliament.uk/general-elections/7 (candidate CSV `/candidacies.csv`)
  - https://api.parliament.uk/uk-general-elections/general-elections/42 (1997), /43 (2001), /44 (2005)
  - https://commonslibrary.parliament.uk/research-briefings/cbp-8647/
  - https://api.parliament.uk/uk-general-elections/constituencies (boundary-identity warning)

## What the next session does

1. `/planner resume poll-errors-simulation`, or `/builder`, for H1. Run the checks under **Where we are**.
2. Brief 2a-H1 to Phil: sources, crosswalks, party mapping, audit checks, and the files it writes. Then implement it, check it, and pause for review before the commit.
3. Don't start H2 or H3 unasked.

## Don't (standing constraints from thread 4)

- Don't lower the 17/20 gate, or dilute it by adding older outcomes to its denominator.
- Don't silently add a cluster-scale prior, regime/fragmentation dependence, regional balancing or exact-regional-total projection.
- Don't claim 2a complete, integrate the failed candidate into the engine, or start 2b/2c on this evidence.
- No live DB writes, model or config changes, backfills, reruns, activation or publication without a fresh, explicit go-ahead. The historical data goes into a repo fixture only. Don't import it into `elections.db`, and don't add it to production `ELECTION_PAIRS`.
- The six commits from 2026-10-29 haven't had a self-review. Don't describe the branch as reviewed.

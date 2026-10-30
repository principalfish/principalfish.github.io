# Resume point: read this first

Written 2026-10-30, when the plans were consolidated, from thread 4's Notes (2026-10-28/29) and its `assessment.md`. Nothing has happened on the branch since `ab3fc62e` (2026-10-29).

## Where we are

Thread 4, **Piece 2a** (correlated vote-to-seat conversion error, decision W-1) is **stopped, waiting on a decision from Phil**. All work is committed. No job is running. Nothing is staged, and nothing is half-done in the working tree.

Check before doing anything else:
- branch `poll-errors-simulation`, HEAD `ab3fc62e`, clean tree;
- `sha256sum data/models/westminster/uncertainty_conversion_prototype_report.json` = `3ead7c5851e96e1fd143a26c9370de1fca963b31925c55648a3a4dd889201428`.

If anything differs, stop and tell Phil before going further.

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

## The pending decision (exact)

**Question:** which historical scope to bring in first?
- **(A) 1997→2001 and 2005→2010 now.** This is the one recommended to Phil.
- **(B) Also include the 1980s / early 1990s now.**

**Phil hasn't chosen.** He asked for **an explanation of the full flow** first, then left. So the next session's first job is that explanation, not the question.

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

1. `/planner resume poll-errors-simulation`. Read this file, `decisions.md`, thread 4's `plan.md` Notes from "Piece 2a prerequisite" (2026-10-28) onward, its `assessment.md`, and `data/models/westminster/conversion-prototype-method.md`. Run the checks under **Where we are**.
2. **Explain the full flow** to Phil, end to end, for each scope option. Cover:
   - **Acquiring and auditing the data:** sources, crosswalks, party mapping, the minor-party aggregates, Labour Co-op/Speaker. What gets written and where; data writes need his approval.
   - **How the new transitions would enter the experiment.** They're additional evidence and **stay out of the original 20-outcome gate's denominator**. Exactly how they enter the fits isn't designed yet; that design is part of what he approves, so present the options rather than deciding.
   - **Rerunning the fixed offline experiment** under the same contract and gates, and what it would show.
   - **The two outcomes:**
     - if the gate and guards pass, the W-1 decision, then engine integration of 2a, then 2b–2f;
     - if they fail, back to the replan-with-new-assumptions question.
   - **What changes with option B:** more boundary/notional work, unverified sources, and more time before any result. Say what's still uncertain about each step.
3. **Then ask the scope question** (AskUserQuestion, with A as the recommended option). Record the answer in `decisions.md` and in this issue's `plan.md` Notes. Rewrite this file's "pending decision" section to the next open point.
4. Only then start the work the answer implies, as a planned piece with the usual pauses.

## Don't (standing constraints from thread 4)

- Don't lower the 17/20 gate, or dilute it by adding older outcomes to its denominator.
- Don't silently add a cluster-scale prior, regime/fragmentation dependence, regional balancing or exact-regional-total projection.
- Don't claim 2a complete, integrate the failed candidate into the engine, or start 2b/2c on this evidence.
- No live DB writes, model or config changes, backfills, reruns, activation or publication without a fresh, explicit go-ahead.
- The six commits from 2026-10-29 haven't had a self-review. Don't describe the branch as reviewed.

# Resume point: read this first

Updated 2026-11-07 (planner resume). Issue state: `building`.

## Where we are

**2a-H1, H2 and H3 are done. H3 failed the gate:** best 15/20, candidate 14/20, decision `stop_before_engine`. Phil had the failed report committed as evidence (`44dd8336`), and it's pushed. Numbers are in `plan.md` Notes (2026-10-30, H3).

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `44dd8336514cb20953b76f0c8d24b87df814aa95`.
- `git show 44dd8336:data/models/westminster/uncertainty_conversion_historical_report.json | sha256sum` → `0893389afce7babd00c69e98bdea9331113e0ffe900568c64ce1d602ebc59ab4` (the file's hash; the report's internal `report_hash` is `50f2f5ea…`).
- `git log --oneline poll-errors-simulation..master | wc -l` → 32 on 2026-11-07 (master last merged in at `35e527a1`, 2026-10-22).

## Pending decision (Phil)

**The fail branch for Piece 2a.** What the evidence says: the misses are directional, not just too narrow. In held-out 2024 Labour is 322–358 vs 411 and Con 188–237 vs 121. In the report-only 1997→2001 fold, Labour is 376–407 vs 411 and Con 168–205 vs 166, the same direction. Adding two low/moderate-change transitions tightened the cluster scales and dropped the candidate from 15/20 to 14/20.

Options:
1. **Replan the conversion model with explicitly new assumptions**, e.g. a Labour-lead/efficiency or regime term. It's the only option aimed at the visible cause. Caveat: with two landslide transitions, any term fitted to them will look good in LOO, and LOO is already retrospective. It needs a pre-registered term before fitting.
2. **Add 1983–92 history first.** 1983→87 and 87→92 likely share the 1983 boundaries (unverified, as is source coverage). But both are Con-led, moderate-change elections, so H3 suggests they'd tighten the scales further.
3. **Park 2a.** Leave Westminster without correlated conversion error and move on to the pieces that don't depend on it: Phase 3 (3a, display) and Phase 4 (4a–4d, US model). 2b–2f stay blocked. Come back to option 1 later.

Planner recommendation (2026-11-07): 3 now, with 1 as a later, separately briefed experiment. Not decided.

If 3 is chosen, update `plan.md` `## Pieces` order and checkpoints so 3a/4a come next (Resume step 5) before `/builder`.

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
6. **2026-10-30.** H1 (`eaa73607`) and H2 (`88bfc2a0`) committed; Phil gave the go-ahead for the H3 batch. H3 failed (best 15/20, candidate 14/20). Question: commit the failed report as evidence? → **Yes; committed as `44dd8336` and pushed.**

## Don't (standing constraints from thread 4)

- Don't lower the 17/20 gate, or dilute it by adding older outcomes to its denominator.
- Don't silently add a cluster-scale prior, regime/fragmentation dependence, regional balancing or exact-regional-total projection.
- Don't claim 2a complete, integrate the failed candidate into the engine, or start 2b/2c on this evidence.
- No live DB writes, model or config changes, backfills, reruns, activation or publication without a fresh, explicit go-ahead. The historical data goes into a repo fixture only. Don't import it into `elections.db`, and don't add it to production `ELECTION_PAIRS`.
- The six commits from 2026-10-29 haven't had a self-review. Don't describe the branch as reviewed.

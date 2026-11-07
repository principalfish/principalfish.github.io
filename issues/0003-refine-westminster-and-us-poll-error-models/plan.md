# Plan: refine-westminster-and-us-poll-error-models

**Build model:** claude-opus-5-5. Every piece is statistical modelling with evidence gates, so each needs frontier judgement.

## Context

Split from issue 0001 (`poll-errors-simulation`) on 2026-11-07. 0001 ships the Monte Carlo uncertainty as built. This issue holds the model refinements that a 2026-10-26 review of that branch called for. That was a full `/review self` at `134d1fc4`, with a methodology audit. Its verdict: the machinery is correct, but the Westminster odds are overconfident and the US error scales can't be identified from the data.

**Read these first. They're on the planner branch, under `issues/0001-poll-errors-simulation/`:**
- `threads/fix-poll-errors-simulation-review-findings/plan.md`: the full text of every piece here (Files, What, Acceptance), the Phase 2 approach (each new term behind an off-by-default config field, and a joint recalibration at the end), the working rules, and the Notes holding 2a's whole history.
- `threads/reviews/poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/review.md`: the evidence for each finding (W-1…W-8, US-1, US-2).
- `resume.md` and `plan.md` (2026-10-30 Notes): the 2a-H1 → H3 decisions and results.

**Branch:** `refine-westminster-and-us-poll-error-models`, cut from `poll-errors-simulation` at `44dd8336` or later. After 0001 merges into master, merge master into this branch.

**Where 2a stands.** The test: feed the engine the actual regional results, so only seat noise and λ are left. Over the four transitions 2015, 2017, 2019 and 2024, × 5 parties, do the 90% seat-total intervals cover the actual seats in at least 17 of 20 cells? Also: the stability guard (128 vs 256 training draws), leader/runner standardised RMS within the paired predictive 5–95% reference, national mean-vote drift ≤0.15 pt, and lambda attribution.

| Attempt | Commit | Best | Candidate | Note |
|-|-|-|-|-|
| Direct cluster fit, then reassessment | `assessment.md` (0001 thread 4) | 15/20 | — | Every 2024 cell missed. |
| Eight-variant offline experiment (4 transitions) | `ab3fc62e`, report `uncertainty_conversion_prototype_report.json` | 15/20 | 15/20 | No-cluster controls 13/20. Candidate also failed RMS, stability, identification and drift. |
| Six transitions (adds 1997→2001, 2005n→2010 as training and report-only folds) | fixture `eaa73607`, contract `88bfc2a0`, report `44dd8336` | 15/20 | 14/20 | RMS fails in every variant; drift fails in 7/8. 2024: Lab 322–358 vs 411, Con 188–237 vs 121. Report-only 1997→2001 misses the same way. |

The misses are directional: the model under-calls landslides.

**Settled (carried from 0001; don't re-derive):**
- The gate stays 17/20 on the original 20 cells. Don't lower it, and don't add older outcomes to its denominator.
- Equal transition weights, training-only fits, λ centred at 0. The LOO results are retrospective diagnostics, not fresh validation, because these transitions shaped the design.
- No cluster-scale prior, regional balancing or exact-regional-total projection without an explicit decision. **A landslide/lead-dependent term for 2a is now authorised (Phil, 2026-11-07)**, but only in the exact form Phil approves at 2a-L2, before any fit.
- Historical data lives in a frozen in-repo fixture. No `elections.db` write, and nothing added to production `ELECTION_PAIRS` without a separate go-ahead.
- Every new term sits behind an off-by-default config field that reproduces today's draws bit-for-bit. Every new term gets its own child stream, appended after the existing ones in `ErrorStreams.from_seed`.
- Parity invariants: zero error reproduces the deterministic projection; seeds reproduce; draws don't change with chunk size; MC vote-share means stay within 0.15 pt of central; a term is exactly 0 when its σ is 0.
- Console transparency: each new term shows its inputs, formula and intermediate numbers in the data console, in the same piece as the term.
- Live operations (DB writes, reruns, backfills, exports, publication) only with Phil's explicit go-ahead at the time, after `scripts/backup_db.py backup`.

**Scope:** Westminster Pieces 2a–2f and US Pieces 4b–4d. **Out of scope:** US 4a and display 3a, which ship in 0001. Also out are thread 4's documented non-goals: Sinn Féin abstention, a full-pipeline backtest, and a joint House/Senate/President probability.

## Approach

Each model change is an experiment first, behind a gate, and only then integrated (off by default), so a failed idea never reaches the engine. The pieces follow thread 4's order.

**2a route (Phil, 2026-11-07): a pre-registered landslide/efficiency term, diagnosed first.** The three attempts all under-call landslides. Today's central seat projection is uniform regional swing, with λ pushing towards proportional (`data/model_support/monte_carlo.py:61`, `:1038`). Even at λ=1 it gives Labour 354 against 411 in 2024, so the miss looks like projection bias rather than missing spread. Before choosing the term's form, a fit-free diagnostic splits each transition's miss into its parts (2a-L1). Phil then approves the exact form, its fit and its gate before anything is fitted (2a-L2). The run follows (2a-L3), and only a pass leads to integration (2a-L4). This mirrors H1–H3.
- Evidence value: 1997→2001 is in every fold's training set, so the 2024 fold's term is fitted without 2024 and then tested on it. That's a thin but real out-of-sample test. With one training landslide per fold it is weak evidence, and the LOO stays retrospective. Phil accepted that risk.
- Rejected: (b) 1983–92 history first (boundaries and sources unverified; Alliance mapping; moderate Con-led elections, which tightened the scales in H3; 1983 itself needs notional 1979 results). (c) Deferring 2a (W-1 stays open; 2b, 2c's λ part and 2f stay blocked). Committing now to a directional, spread or combined form without the diagnostic.

2d and 2e have no stated dependency on 2a in thread 4's text, so they can go ahead in parallel with the 2a-L pieces. 2b, the λ part of 2c, and 2f can't.

The US pieces are independent of Westminster and can be done in any order relative to it, before the 2028 cycle.

## Pieces

For 2b–2f and 4b–4d, each entry's full text is in 0001 thread 4's plan, under the same piece name (2b, 2c…); what follows adds only what's changed since then. The 2a-L pieces are new and complete here; thread 4's 2a text and its H1–H3 notes are history.

1. **Decide 2a's direction.** Done 2026-11-07: route (a), a pre-registered landslide/efficiency term, with a decomposition diagnostic first (see Approach and `decisions.md`).
   - Files: this issue's `decisions.md` and `plan.md` only.
   - Depends on: none.
   - Acceptance: Phil chose; `plan.md` has the 2a-L pieces.
2. **2a-L1: decompose the landslide miss (offline, fit-free).** Then pause and present it to Phil.
   - Files: a new pure helper `data/model_support/westminster_conversion_decomposition.py`; a CLI entry (a `--decompose` mode of `scripts/validate_westminster_conversion.py`, or its own script, whichever fits the existing CLI better); a committed report `data/models/westminster/uncertainty_conversion_decomposition_report.json`; a console view beside the conversion-validation route; focused tests.
   - What: for each of the six transitions (the four gated ones plus 1997→2001 and 2005n→2010) × the five scored parties, report:
     - actual seats;
     - the deterministic projection at λ = 0, at each fold's λ̂, and at λ = 1;
     - the no-cluster Gaussian MC median and 5–95%, read from the committed H3 report (`44dd8336`; no new draws).
     - The miss is split three ways: actual − deterministic(λ̂) is projection bias; deterministic(λ̂) − MC median is the noise shift; and the rest is spread.
     - Transition covariates: the leader's national lead, the change in that lead, each party's national share change (in particular the largest loser's drop), the effective number of parties, and total absolute swing.
     - A seat-level breakdown of the misses for 2024 and 1997→2001: wrongly called seats by the incumbent party's baseline-share band and by the actual winner, so the diagnostic shows *where* the projection loses seats (for example Con-held seats lost beyond swing).
     - Inputs are opened read-only (DB transitions and the frozen fixture `eaa73607`). Nothing is fitted, and no engine, config or draw-format code changes.
     - Console transparency: the view shows every number above.
   - Depends on: 1.
   - Acceptance: the report reproduces the H3 report's MC medians and intervals exactly for the cells it reads, and its deterministic λ=1 2024 Labour figure matches the review's 354 (or any difference is explained). Tests cover the decomposition arithmetic and the read-only inputs. Then pause: present the decomposition and a recommended term form (directional, spread or both; its driver; its parameter count), with what the evidence can and can't support. Phil decides the form before 2a-L2.
3. **2a-L2: pre-register the landslide term and its contract.** Then pause before the run.
   - Files: `data/model_support/westminster_conversion_design.py` and `westminster_conversion_validation.py` (the term as an offline component); `scripts/validate_westminster_conversion.py` (a new `--contract` value; `four-transition` and `six-transition` stay as they are and reproduce their committed reports); the experiment-method document; focused tests.
   - What: implement exactly the form Phil approved after 2a-L1, behind its own contract.
     - Write down before any fit: the term's formula, its driver and parameters, the training-only fit, its stream (appended after the existing ones), and the declared variant set (the fixed comparison, including controls without the term).
     - Write down the gate too: unchanged 17/20 on the original 20 cells, leader/runner standardised RMS within the paired predictive 5–95% reference, 128 vs 256 stability, national mean-vote drift ≤0.15 pt, and λ attribution. The two older held-out folds stay report-only.
     - If the term is directional, it changes the deterministic projection. Then the contract must say how the "zero error reproduces the deterministic projection" invariant is restated, and that the production central forecast isn't touched until 2a-L4.
     - Synthetic known-component and zero-component recovery before any historical scoring.
   - Depends on: 2, and Phil's decision on the form.
   - Acceptance: contract hash recorded; synthetic recovery passes; the existing contracts reproduce their committed reports bit-for-bit; tests pass. Pause for Phil's go-ahead for the batch.
4. **2a-L3: run the pre-registered experiment and report.**
   - Files: the new report under `data/models/westminster/` (committed either way, as evidence); console route.
   - What: run the batch in the background (`OPENBLAS_NUM_THREADS=1`, checkpointed; H3 took about 57 min). Report every variant, fold, guard and failure reason, all 2024 misses, and the report-only folds. Results are called retrospective LOO diagnostics.
   - Depends on: 3, and Phil's go-ahead for the run.
   - Acceptance: report read back through its hash reader and the console route returns 200. **Pass** (every declared gate): go to 2a-L4. **Fail:** stop before any engine change, keep W-1 open, and take the result to Phil. Don't tune, reseed or widen to force a pass.
5. **2a-L4: integrate the passed term into the engine, off by default.**
   - Files: `data/model_support/uncertainty.py` (`UncertaintyConfig` field), `data/model_support/monte_carlo.py` (`error_scales`, `draw_errors`, the seat-vote assembly near `:1038`, the draws format), `data/models/westminster/uncertainty_config.json`, the console Uncertainty view, tests.
   - What: the term as it passed in 2a-L3, behind an off-by-default config field that reproduces today's draws bit-for-bit, with its own child stream appended in `ErrorStreams.from_seed`. Bump the draws format and header so old snapshots are refused. Its parameters come from the full training set, using the 2a-L3 fit method. Console shows inputs, formula and intermediate numbers. If the term is directional, say whether the published deterministic projection moves with it (Phil's call at the time, since it changes the headline forecast).
   - Depends on: 4 passing.
   - Acceptance: parity invariants hold with the field off; with it on, the offline and engine draws agree on a fixed seed; full checks pass. Not enabled in production config until 2f.
6. **2b: heavy seat-level tails (W-2).** Thread 4's acceptance: fewer residuals beyond the Gaussian 99.9% band than the current 2.6%; 2024 ≥95% favourites lose ≤ about 5% in combination with 2a; no seat at "win 100%". Brief the optional regime term and let Phil choose.
   - Depends on: 5 (2a-L4).
7. **2c: parameter uncertainty in the error scales (W-3).** σ_nat² from a scaled inverse-χ² with 9 df, so national errors become t₉. σ_λ the same, from 4 pairs (or the training-fold count). One χ² per simulation, from a new stream.
   - Depends on: 5 (2a-L4) for the λ part. The national-scale part can go first.
8. **2d: house effects and sparse windows (W-6, W-5).** Add the between-pollster term σ_h²·max(0, ΣW_p² − ΣW_cal²), and a low-confidence flag below a minimum effective pollster count (suggested 3).
   - Depends on: none.
9. **2e: nation-only parties and the lead (W-7, W-8).** Scale SNP/Plaid national error by in-nation share. Add a top-two swing factor only if the lead z RMS is still above about 1.1 after 2a–2d.
   - Depends on: none for W-7; W-8 is judged after 2a–2d (pieces 5–8).
10. **2f: joint recalibration.** Extend `calibrate_uncertainty.py`, write `uncertainty_config.json` and the calibration report, extend the console Calibration view, rerun the Piece 0 harness and commit its report.
   - Depends on: 5–9, with 2a-L3's gate passed.
   - Acceptance: all Phase 2 targets met at once, invariants held, and the before/after table presented.
11. **4b: t-predictive US national G (W-3 for the US).** t₆ for the House, t₃ for the President, using 2c's mechanism.
   - Depends on: 7 (national part).
12. **4c: Senate state/race allocation (US-1).** Brief first. Calibrate the polled-race error against the 24 retrieved final Senate averages. At least two conditional cycles; 2026 can be one once its results are imported (issue 0002).
   - Depends on: none, though it's stronger after 0002.
13. **4d: correlated US state errors (US-2).** Brief first; data-source decision. Low-rank factor model, validated by whole-cycle holdout in `validate_us_uncertainty.py`.
    - Depends on: none.
14. **Gated: Westminster rerun and publish under the new config.** Backup → rerun 2024-07-05 to the latest date → `rebuild_model_trends.py` → staged export → real export with approval. Plus US recalibration acceptance for 4b–4d.
    - Depends on: 10 (and 11–13 for the US part), and Phil's go-ahead at the time.

## Checkpoints

- [x] Piece 1: 2a direction chosen and written up as pieces.
- [ ] 2a-L1: Decompose the landslide miss (fit-free diagnostic), then pause for Phil's choice of term form.
- [ ] 2a-L2: Pre-register the landslide term and contract, then pause before the run.
- [ ] 2a-L3: Run the pre-registered experiment and report (pass → L4; fail → stop and ask).
- [ ] 2a-L4: Integrate the passed term into the engine, off by default.
- [ ] 2b: Heavy seat-level tails.
- [ ] 2c: Error-scale parameter uncertainty.
- [ ] 2d: House effects and sparse windows.
- [ ] 2e: Nation-only parties and lead calibration.
- [ ] 2f: Joint recalibration.
- [ ] 4b: US national parameter uncertainty.
- [ ] 4c: Senate state/race allocation.
- [ ] 4d: Correlated US state errors.
- [ ] Gated rerun, backfill and publish, with explicit authorisation.

## Verification

Run from `data/` unless noted.
- `./run_tests.sh`: strict mypy gates it, then pytest.
- `./election_data/bin/python scripts/export_elections.py --dry-run`
- `./election_data/bin/python -c "from console import create_app; create_app()"`
- Westminster model pieces: `./election_data/bin/python scripts/validate_westminster_uncertainty.py`, compared against the committed baseline report.
- Conversion experiments: `OPENBLAS_NUM_THREADS=1 ./election_data/bin/python scripts/validate_westminster_conversion.py`, with the relevant contract flag. These are checkpointed long runs (about 30–60 min), so run them in the background.
- US pieces: `validate_us_uncertainty.py`.
- Front end, from the repo root: `npm test`, `npm run minify:electionmaps`, and the jsdom harness on both pages.
- `git diff --check`. Stage explicitly; `run_tests.sh` rewrites the tracked `model_output_trends.json`.

## Notes

- 2026-11-07: Created by splitting 0001. Thread 4's Phase 2 and 4b–4d moved here; 0001 keeps 3a, 4a, the gated US/Westminster live work, and style.
- 2026-11-07: Piece 1 done. Phil chose route (a), a pre-registered landslide/efficiency term, over 1983–92 history first or deferring 2a. For its form he chose "diagnose, then choose" over committing now to directional, lead-dependent spread, or both. 2a is now 2a-L1…L4 (pieces 2–5); later pieces renumbered 6–14.

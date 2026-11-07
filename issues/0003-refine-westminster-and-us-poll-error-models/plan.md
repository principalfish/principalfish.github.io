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
- No cluster-scale prior, regime/fragmentation term, regional balancing or exact-regional-total projection without an explicit decision. (The 2a replan may make one of these decisions explicitly.)
- Historical data lives in a frozen in-repo fixture. No `elections.db` write, and nothing added to production `ELECTION_PAIRS` without a separate go-ahead.
- Every new term sits behind an off-by-default config field that reproduces today's draws bit-for-bit. Every new term gets its own child stream, appended after the existing ones in `ErrorStreams.from_seed`.
- Parity invariants: zero error reproduces the deterministic projection; seeds reproduce; draws don't change with chunk size; MC vote-share means stay within 0.15 pt of central; a term is exactly 0 when its σ is 0.
- Console transparency: each new term shows its inputs, formula and intermediate numbers in the data console, in the same piece as the term.
- Live operations (DB writes, reruns, backfills, exports, publication) only with Phil's explicit go-ahead at the time, after `scripts/backup_db.py backup`.

**Scope:** Westminster Pieces 2a–2f and US Pieces 4b–4d. **Out of scope:** US 4a and display 3a, which ship in 0001. Also out are thread 4's documented non-goals: Sinn Féin abstention, a full-pipeline backtest, and a joint House/Senate/President probability.

## Approach

Each model change is an experiment first, behind a gate, and only then integrated (off by default), so a failed idea never reaches the engine. The pieces follow thread 4's order. 2d and 2e have no stated dependency on 2a in thread 4's text, so they can go ahead while the 2a decision is pending. 2b, the λ part of 2c, and 2f can't.

The US pieces are independent of Westminster and can be done in any order relative to it, before the 2028 cycle.

## Pieces

Each entry's full text is in 0001 thread 4's plan, under the same piece number. What follows adds only what's changed since then.

1. **Decide 2a's direction.** A `/planner resume` discussion, not a build.
   - Files: this issue's `decisions.md` and `plan.md` only.
   - What: present the three attempts and the directional misses. Options:
     - (a) a pre-registered landslide or Labour-lead efficiency/regime term, written down before any fit, with the risk stated that two landslides make the LOO weak evidence;
     - (b) acquire 1983–92 history first (1983→87 and 87→92 probably share boundaries; unverified);
     - (c) put 2a aside and do 2d/2e first.
     Write the chosen route as full piece entries here.
   - Depends on: none.
   - Acceptance: Phil chooses; `plan.md` has the new pieces.
2. **2a: correlated vote-to-seat conversion error (W-1).** As chosen in piece 1. Engine integration (off by default) only after the gate passes.
   - Depends on: 1.
3. **2b: heavy seat-level tails (W-2).** Thread 4's acceptance: fewer residuals beyond the Gaussian 99.9% band than the current 2.6%; 2024 ≥95% favourites lose ≤ about 5% in combination with 2a; no seat at "win 100%". Brief the optional regime term and let Phil choose.
   - Depends on: 2.
4. **2c: parameter uncertainty in the error scales (W-3).** σ_nat² from a scaled inverse-χ² with 9 df, so national errors become t₉. σ_λ the same, from 4 pairs (or the training-fold count). One χ² per simulation, from a new stream.
   - Depends on: 2 for the λ part. The national-scale part can go first.
5. **2d: house effects and sparse windows (W-6, W-5).** Add the between-pollster term σ_h²·max(0, ΣW_p² − ΣW_cal²), and a low-confidence flag below a minimum effective pollster count (suggested 3).
   - Depends on: none.
6. **2e: nation-only parties and the lead (W-7, W-8).** Scale SNP/Plaid national error by in-nation share. Add a top-two swing factor only if the lead z RMS is still above about 1.1 after 2a–2d.
   - Depends on: none for W-7; W-8 is judged after 2–5.
7. **2f: joint recalibration.** Extend `calibrate_uncertainty.py`, write `uncertainty_config.json` and the calibration report, extend the console Calibration view, rerun the Piece 0 harness and commit its report.
   - Depends on: 2–6, with 2a's gate passed.
   - Acceptance: all Phase 2 targets met at once, invariants held, and the before/after table presented.
8. **4b: t-predictive US national G (W-3 for the US).** t₆ for the House, t₃ for the President, using 2c's mechanism.
   - Depends on: 4 (national part).
9. **4c: Senate state/race allocation (US-1).** Brief first. Calibrate the polled-race error against the 24 retrieved final Senate averages. At least two conditional cycles; 2026 can be one once its results are imported (issue 0002).
   - Depends on: none, though it's stronger after 0002.
10. **4d: correlated US state errors (US-2).** Brief first; data-source decision. Low-rank factor model, validated by whole-cycle holdout in `validate_us_uncertainty.py`.
    - Depends on: none.
11. **Gated: Westminster rerun and publish under the new config.** Backup → rerun 2024-07-05 to the latest date → `rebuild_model_trends.py` → staged export → real export with approval. Plus US recalibration acceptance for 4b–4d.
    - Depends on: 7 (and 8–10 for the US part), and Phil's go-ahead at the time.

## Checkpoints

- [ ] Piece 1: 2a direction chosen and written up as pieces.
- [ ] 2a: Correlated vote-to-seat conversion error.
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

# Plan: fix-poll-errors-simulation-review-findings

## Context

The `poll-errors-simulation` branch adds Monte Carlo uncertainty to the Westminster projection and the US House, Senate and President models. It also fixes the Senate swing anchor. A full `/review self` on 2026-10-26 found the machinery correct. It also found that the Westminster seat odds are overconfident and that the US error scales are not identified. The user has asked for **every finding to be fixed** on this branch.

Westminster uncertainty is **not in prod**. Its error model and calibration can change freely on this branch, followed by a full backfill. The "gated" labels in the review do not apply to Westminster. The US stays behind its release gates: fix the model, but do not flip any release flag.

**Source documents. Read these first; this plan cites their finding IDs:**
- Consolidated review: `~/.project-ai/principalfish.github.io/reviews/poll-errors-simulation-20261026-134d1fc4-full/review.md`
- Methodology audit, with the evidence and suggested fixes for each W-/US- finding: `~/.project-ai/principalfish.github.io/reviews/poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/review.md`
- The audit's numerical check scripts and their baseline outputs: `.../poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/checks/*.py` and `checks/results/*.txt`. They ran against a scratch copy via a `REPO=` environment variable and opened the DB read-only.
- Existing issue plans, with design history and decisions already made:
  - `~/.project-ai/principalfish.github.io/issues/add-monte-carlo-uncertainty-to-westminster-projection/{plan,briefs,independent-note}.md`
  - `.../add-us-election-uncertainty-and-overall-poll-trackers/plan.md`
  - `.../anchor-us-senate-swings-to-matching-house-national-baselines/plan.md`

**Branch state:** `poll-errors-simulation` at `ab3fc62e`. Senate anchor piece 4 is now committed, so "uncommitted piece 4" in the review is done. The user has checked the branch out in the main checkout at `/home/philiph/workspace/principalfish.github.io`. Work there.

**Out of scope:** these are already documented decisions; do not change them unless the user asks.
- λ is centred at 0. This was the user's decision.
- σ_reg = 1.5 is a guess.
- The model is framed as a nowcast.
- Sinn Féin abstention is not modelled.
- There is no full-pipeline backtest, because pre-2024 polls aren't stored.
- No joint House/Senate/President probability is offered.

## Working rules for whoever picks this up

These come from the user's standing preferences and the repo instructions. They are mandatory.

- **One piece at a time.**
  - Before each piece, present a short plain-language brief using real current numbers, then wait for the go-ahead.
  - After each piece, run the checks, present the result, and **stop for the user's manual review**.
  - Do not launch analyse or check-* passes unless asked.
- **Commits:** local only. Never push and never open a PR. Do not add a Co-Authored-By line. Stage files explicitly and never run `git add -A`: `run_tests.sh` rewrites the tracked `model_output_trends.json`.
- **Console transparency:** every new error term or calibration parameter must show its inputs, its formula and its intermediate numbers in the data console (`data/console`). That means the error budget, simulation explorer and calibration views. Build the console part in the same piece as the term it shows.
- **Live data:**
  - `.env` loads with `override=True`, so scripts always hit the live DB at `/home/philiph/dbs/elections.db`. Never "test" a script by changing an environment variable.
  - Any DB write, model rerun, backfill or real export is run **only with the user's explicit go-ahead**, after `cd data && ./election_data/bin/python scripts/backup_db.py backup`.
  - Use `--dry-run` and `--output-base /tmp/x` for scratch work.
- **Front end:**
  - After any edit under `electionmapslogic/`, run `npm run minify:electionmaps`.
  - `server.sh` watches the engine sources, so don't temp-edit watched files while it runs.
  - Use the jsdom harness described in AGENTS.md, and check both Westminster and the US page.
- **Parity invariants must hold after every model change.** Their tests already exist; keep them green.
  - At zero error the model reproduces the deterministic projection exactly.
  - Seeds reproduce, and draws don't change with chunk size.
  - MC vote-share means stay within 0.15 pt of central.
  - Every new term must be exactly 0 when its σ is 0.
- **Random streams:** give each new term its **own** child stream. Append children after the existing four in `ErrorStreams.from_seed` (`data/model_support/monte_carlo.py:790`). `SeedSequence.spawn` children are indexed, so the existing national, regional, seat and λ normals stay bit-identical.

## Phase 0: acceptance harness (do first)

**Piece 0. Promote the audit checks into a repo validation script.**
- New `data/model_support/westminster_validation.py` and `data/scripts/validate_westminster_uncertainty.py`. Model them on the US equivalent, `data/scripts/validate_us_uncertainty.py`.
- Port the logic from the audit's `checks/` into these files:
  - `wm_seat_total_backtest.py`: the perfect-regional-swing seat-total coverage, leave-one-pair-out.
  - `wm_seat_reliability.py`: win-probability reliability bins and the ≥95% favourite loss rate.
  - `wm_national_loo.py`: national leave-one-out coverage and the lead z RMS.
  - `wm_seat_coverage.py`: seat-residual tails and kurtosis.
  - `wm_param_uncertainty.py`: MC SE and seed spread.
  - `wm_bias.py`: central vs MC median.
- Write a JSON report next to `data/models/westminster/uncertainty_calibration_report.json`. Add a console "Validation" view (US template: `us_uncertainty_validation.html`).
- Tests use fixture elections in a temporary DB (`data/tests/conftest.py`).
- **Acceptance:** run against the current config, the script reproduces the audit's baseline numbers within MC noise:
  - 12/20 seat-total cells covered;
  - 2024 favourites lost 7.5–8.6% of the time;
  - leave-one-out national coverage 85.7%;
  - lead z RMS 1.18.

  Commit this baseline report. Every later model piece re-runs it.

## Phase 1: code robustness and test gaps (no change to numbers)

These are independent and small; each line below is one commit, or batch them as the user prefers. Line numbers are from `134d1fc4`, so re-locate them.

**Piece 1a. Group C fixes (the review, table C):**
- **C-1:** check the legacy Senate report by its pinned hash only, not by `kernel_version == US_KERNEL_VERSION`. Remove the dead `legacy_senate` parameter, or wire it to a CLI flag. File: `us_calibration_comparison.py` around lines 134, 146 and 653–664.
- **C-2:** take `anchor_catalogue_sha256` from `resolve_senate_house_anchors`, or raise `ComparisonError` instead of a bare `StopIteration` (around line 568).
- **C-3:** derive the availability years from the pinned policy, and render the "only 2022 usable" text and counts from `reference_availability`.
- **C-4:** resolve the policy and catalogue once per cycle, and drop `provenance` from the availability rows. Target: comparison page well under 6.4 MB and 0.43 s.
- **C-5:** the console explorer imports `is_others_party` and `senate_fallback_swings` instead of keeping its own copies.
- **C-6:** add `EOFError` to the errors caught by `us_replay.load_us_draws` (line 216).
- **C-7:** the US interval quantiles use the shared rounding helper behind `monte_carlo._interval_quantiles`. Files: `us_run.py`, `us_simulation.py`.
- **C-8:** add a constant for `"senate-comparison-report.json"` beside `SENATE_REPORT_STEM`.
- **C-9:** extract the paired validation and identity helpers.
- **C-10:** fix the test selectors: use `senate_reference_availability(...)["available"]`, match "anchor identity", and extend the tamper loop.

**Piece 1b. Group D test gaps:**
- Add the four high-risk tests exactly as specified in the review, §D items 1–4:
  - US replay mismatch and temp cleanup;
  - zero average → zero variance;
  - parametrized identity-mismatch skips;
  - the House trends export and stale-file removal, including under `--dry-run`.
- Then the medium-risk list.
- Then the JS tests listed under Frontend in §D.

**Piece 1c. Tracker infrastructure (S-3, S-2):**
- **S-3:** put a config and kernel identity in the Westminster trend points and break segments on it, mirroring the US `segment_id` in `data/model_support/trends.py:548–553`. This must land **before** the Phase 2 recalibration, so old and new runs are never joined.
- **S-2:** use a trailing band-smoothing window in `electionmapslogic/features/polltracker-uncertainty.js`. Alternatively, use common random numbers across dates and drop the smoothing; ask the user which.

## Phase 2: Westminster error model (W-1, W-2, W-3, W-6, W-5, W-7, W-8)

**Approach:**
- Add each component behind a config field whose default is "off". The off default reproduces today's draws bit-for-bit, so parity and existing tests stay green.
- Then recalibrate everything jointly in one piece (2f).
- `config_hash` changes automatically when the config changes. That drives S-3 segments and the replay checks.
- Extend `ErrorScales` and `ErrorDraws`, and the `save_draws`/`load_draws` format, with each new term. Bump the draws format and header so old snapshots are refused cleanly.

Engine anchor points:
- `error_scales` at `monte_carlo.py:720`;
- `draw_errors` at `:917` (seat draw `:954–964`, national `:939`);
- config fields in `data/model_support/uncertainty.py` (`UncertaintyConfig`, around line 60) and `data/models/westminster/uncertainty_config.json`;
- fitting in `data/model_support/calibration.py` (`robust_spread` at around 937, `pool_pairs` at 1147, `national_error_fit` at 304) and `data/scripts/calibrate_uncertainty.py`.

**Piece 2a. W-1: evidence-gated correlated vote-to-seat conversion error (P1).**

The original single-scale Gaussian design failed its feasibility gate. See `assessment.md` in this issue folder. The user chose to test the already-approved heavy seat tails and lambda-scale uncertainty together with the cluster model **offline first**. Do not build disabled engine infrastructure and defer this failed acceptance to Piece 2f.

- **Keep the settled design constraints:** party × nation × three baseline-share quantile bands; each party's own earlier-election shares; tied shares stay together, so empty bands are possible; equal weight for all four transitions; lambda centred at zero. Nation mapping must be explicit and reject unsupported region names rather than silently treating them as England. No regime-dependent seat-noise scaling, cluster prior, regional-balancing transform or newly selected factor structure is authorized by this revision.
- **First build a reproducible offline experiment**, with no production kernel, config, raw-draw-format, saved-run or live-database changes:
  - Files: a focused helper such as `data/model_support/westminster_conversion_validation.py`; the existing `westminster_validation.py` / validation CLI; focused tests; the console Validation view; an additional, clearly experimental report under `data/models/westminster/`. Keep the Piece 0 baseline report and its audit-compatible method intact.
  - Functions: pure cluster-membership and full-loading construction, training-only candidate fitting, offline draws through the existing projection rules, fold evaluation and report generation. Reuse the production projection operations where possible; do not create an unchecked second kernel.
  - Baseline inputs: the four existing historical transitions opened read-only. The in-repository 22 October frozen fixture is for current-run comparisons and is not a substitute for historical held-out inputs.
  - Fixed factorial comparison, declared before the next evaluation: cluster off/on × Gaussian/Student-t seat errors × Gaussian/predictive lambda, giving eight variants. Report every variant, including failures and non-cluster controls. This is a bounded experiment, not an open-ended search over cluster definitions or tail widths.
  - Seat tails use the approved per-seat × party Student-t scale mixture with a separately fitted core scale (Piece 2b); lambda predictive uncertainty uses the approved scaled inverse-chi-square construction (Piece 2c). National predictive uncertainty has no effect in the perfect-regional-swing experiment because polling errors are zero; evaluate it separately in Piece 2c.
- **Fit and attribution contract:**
  - Every fold's parameters, tail fit, scale corrections and any nuisance fits use only its three training transitions. Each transition has equal weight. Prove this by perturbing the held-out outcomes and checking unchanged fitted parameters and training identities.
  - Construct the complete party-centred cluster loading matrix, including cross-party membership differences and the fitted-lambda nuisance projection. Account for independent-seat covariance; do not estimate shared covariance by averaging products of `seat_residuals.raw`.
  - Declare and document the training fit before evaluating the joint candidate. Tail shape and core scale must account for the Gaussian shared component and lambda contribution: their mixture is not a sample of independent Student-t errors. Use a training-only forward/profile fit consistent with the complete loading, finite-variance tail candidates and a Gaussian endpoint. Report component variance allocation, degrees-of-freedom profiles, optimizer/boundary diagnostics and identification limitations. A staged marginal approximation may be shown, labelled exploratory, but cannot clear the production gate.
  - In a leave-one-pair-out fold, the zero-mean lambda posterior-predictive construction uses three training lambda estimates and its corresponding degrees of freedom, rather than the full-data four. Do not variance-normalize away the approved predictive widening. Document assumptions behind the scale inference; do not invent extra degrees of freedom or a cluster-scale prior.
  - Run synthetic known-component recovery and zero-component cases before claiming historical fit quality. If the fitting method is not identified or requires an additional structural assumption, stop and ask with that concrete evidence.
- **Preserve and describe the benchmark accurately:** actual regional swing is the central input; simulated regional totals are not constrained to equal it. Report regional and national aggregate drift from the conversion term separately. The earlier regional-balancing experiment remains a failed diagnostic, not an approved transformation. Any exact-total conditioning requires its own design decision and parity analysis.
- **Evidence gate before production integration:**
  - Run at least the previous 6,000 independent evaluation draws per fold, with fixed, separate fitting/evaluation streams; report fitting and evaluation seeds and input hashes. Increase precision only for a genuinely borderline result, not to search seeds for a pass.
  - At least 17/20 held-out 90% seat-total cells covered; leader/runner standardized RMS errors consistent with model SD rather than the observed approximately 2.8–3.4 (define the predictive-reference criterion before scoring, including the within-election correlation); report all 2024 misses, favourite reliability and the Piece 2b tail diagnostics. Quantify Monte Carlo uncertainty where it affects a decision. Preserve the existing parity and vote-mean requirements.
  - Report all four fold fits and all ablations with their training membership, component parameters, intervals, means, SDs, standardized errors, drift and failure reasons. These transitions have already informed model selection: describe results as retrospective leave-one-pair-out diagnostics, not untouched external validation.
  - A numerical pass is insufficient if covariance allocation, fit identification, excessive aggregate drift or kernel agreement is unresolved. Show the evidence and resolve any material design question before integration.
  - **If the joint prototype fails, stop before engine/archive changes.** Keep W-1 unresolved, record the failed experiment, and seek a specific new evidence/design decision. Do not broaden a prior against the known 2024 miss, lower 17/20, or silently postpone the requirement to joint calibration.
- **Only after that gate, integrate the demonstrated candidate:**
  - Extend the actual scale/draw structures, append child streams after the existing four, and preserve their disabled-component output bit-for-bit. Keep all new components off by default until joint Piece 2f activation.
  - Add the necessary config/calibration, raw archive/replay and kernel identity changes together. The frozen aggregate fixture preserves comparison inputs when the raw format changes; reject incompatible archives cleanly.
  - Console error budget, calibration and explorer show the fitted cluster scale/assumptions, membership counts, complete component allocation and the selected cluster draws in the same piece. Show experimental evidence on the Validation page before integration; do not present prototype parameters as active runtime parameters.
  - Repeat zero-error parity, zero-scale identity, seed/chunk invariance, existing-stream identity, vote means within 0.15 points, replay and end-to-end console checks.
- **Dependencies:** implemented Pieces 0 and 1a–1c and the frozen-input prerequisite. The offline experiment borrows the approved statistical definitions from Pieces 2b/2c; it does not claim their production integration is complete or activate them early. Piece 2a remains one execution unit with an internal evidence gate.

**Piece 2b. W-2: heavy seat-level tails.**
- The approved statistical definition is prototyped in Piece 2a's offline joint gate first. Production integration remains in this piece; re-use the demonstrated fit/draw helpers, account for component overlap, and do not claim independent acceptance from a marginal-tail fit. If the 2a gate fails, no production integration proceeds on the strength of that failed prototype.
- Make the seat noise Student-t through a scale mixture: z / √(χ²_ν/ν), with the χ² drawn per seat×party from a new stream.
- Fit ν and the core scale consistently with the shared components, using the training-only forward/profile fit demonstrated in Piece 2a. The pooled marginal excess kurtosis of 22.6 is baseline evidence, not a seat-only tail estimate after adding other components.
- Re-fit the scale so the core matches the MAD-based σ.
- Brief the optional regime term, σ_seat scaled by the effective number of parties, and let the user choose. The audit found that tails alone don't fix 2024.
- **Acceptance:** fewer residuals beyond the Gaussian 99.9% band than the current 2.6% observed; 2024 ≥95% favourites lose ≤ about 5% in combination with 2a; no seat shows "win 100%".

**Piece 2c. W-3: parameter uncertainty in the error scales.**
- Prototype the lambda part in Piece 2a's offline joint gate first, using training-fold degrees of freedom. National-scale inference and production integration remain here. This does not add a prior or predictive distribution for the cluster scale.
- Each simulation draws σ_nat² from its scaled inverse-χ² posterior. Degrees of freedom are the number of elections, 9, so national errors become t₉.
- Do the same for σ_λ, from 4 pairs.
- Use one χ² per simulation from a new stream. Expose the degrees of freedom in config.
- **Acceptance:** Reform P(maj) and the Labour seat range move toward the audit's t₉ figures (0.02% → about 0.29%; 201–359 → about 191–365) before recalibration; leave-one-out national coverage approaches 90%.
- The US gets the same mechanism in Phase 4.

**Piece 2d. W-6 + W-5: house-effect variance and sparse windows.**
- Add a between-pollster term σ_h²·max(0, ΣW_p² − ΣW_cal²) to the national (and regional) sampling variance. W_p is the window's pollster weight share, and ΣW_cal² is the calibration-era concentration.
  - The subtraction avoids double counting, because σ_nat already contains house effects averaged over 5–19 final pollsters.
  - Estimate σ_h from pollster-mean dispersion; the audit gives 0.45–1.00 pt by party. Code area: `uncertainty.py:408–452`, `westminster_uncertainty.py:104–130`.
- **W-5:** below a minimum effective pollster count, mark the trend point as low-confidence in the export, and the tracker greys out or hides its odds. Suggested threshold: 3, configurable.
- **Acceptance:** windows with ≤2 pollsters no longer produce one-day jumps above about 0.2 in P(hung), or they are flagged; the console shows ΣW_p², the effective pollster count and the added variance.

**Piece 2e. W-7 + W-8: nation-only parties and the lead.**
- **W-7:** for parties polled in only one nation (SNP, Plaid), scale the national error by the in-nation share, not the GB share (`monte_carlo.py:736`). Update the trade-off docstring at `:54–57`.
- **W-8:** add a zero-sum "top-two swing" factor. It is one shared draw per simulation that moves share between the two national leaders. Calibrate it on the lead-error moment. Keep it only if the lead z RMS is still above about 1.1 after 2a–2d.
- **Acceptance:** the SNP polling SD in Scotland is about 2.5–3 pt; lead z RMS ≈ 1.0 leave-one-out.

**Piece 2f. Joint recalibration.**
- Depends on the demonstrated Piece 2a candidate passing its evidence gate and actual-runtime verification of the integrated components. Joint recalibration is not a waiver of earlier failed coverage or fit-identification requirements.
- Extend `calibrate_uncertainty.py` to fit the national, house-effect and lead parameters, then use the demonstrated joint component fit for seat t-scale/ν, cluster σ and lambda. Preserve the component-attribution and training-fold degrees-of-freedom rules rather than treating those final parameters as separately identified from marginal residuals.
- Write `uncertainty_config.json` and the calibration report, and extend the console Calibration view with each parameter's evidence.
- Re-run the Piece 0 harness and commit its report.
- **Acceptance:** all Phase 2 targets are met at once, and parity, seed and chunk invariance hold. Present the before/after table (the audit's §6 numbers vs new) to the user.

## Phase 3: Westminster display (W-4, S-1)

**Piece 3a.**
- **W-4:** show the MC median seat count as the headline point estimate, or alongside it. Label the deterministic count as the "uniform-swing projection", and flag when it falls outside its own 90% range. The user picks between headline and alongside, and the brief comes first.
  - Files: the export in `data/scripts/export/uncertainty.py` and `data/model_support/public_uncertainty.py`, plus `electionmapslogic/uncertainty.js` and the popup/headline views.
- **S-1:**
  - Show probabilities as integer percentages, displayed as "<1%" and ">99%" at the extremes. Locations: `uncertainty.js:86–87, 98`.
  - Store 3 dp, not 4, in `public_uncertainty.py:28`.
  - Apply the same display rules to the US views.
- Run `npm test`, `npm run minify:electionmaps`, and the jsdom harness on both pages.

## Phase 4: US model (gated: model work only, no release flags flipped)

**Piece 4a. A-1 + US-3 + US-4 + US-5 (small).**
- **A-1:** the regional own-poll branch in `models/us/_common.py:2431–2439` subtracts the matching House anchor instead of the Senate-field regional baseline. Add a test.
- **US-3:** seed the national G stream by date only, so all chambers share G (`us_simulation.py:176–180`). Keep per-source streams otherwise.
- **US-4:** always load the parent statewide residual on the ME/NE districts, plus a district term (`:370–376`).
- **US-5:** calibrate House G on the projected-output national error, not the input error, or document the shrinkage in the calibration report.

**Piece 4b. W-3 for the US.**
- Apply the t-predictive national G: t₆ for the House, t₃ for the President. Use the Piece 2c mechanism, shared code where possible.

**Piece 4c. US-1: Senate state/race allocation. Brief first.**
- Treat the Senate fallback residual as race-level, so that polls inform it.
- Calibrate the polled-race error (local floor plus sampling) directly against results, using the 24 retrieved final Senate polling averages.
- Import H from the House state component or from presidential state errors.
- The acceptance rule requires at least two conditional cycles. Wire this through `calibrate_us_uncertainty.py` and the comparison report.

**Piece 4d. US-2: correlation between states. Brief first; needs a data-source decision.**
- Add a low-rank regional/demographic factor model shared across states. The recommended source is the covariance of historical state-level presidential swings, if the DB has enough cycles; otherwise census-region factors.
- Calibrate it on the state error table in the audit (US-2).
- Validate the EV and Senate-control distributions by whole-cycle holdout in `validate_us_uncertainty.py`.

**Piece 4e. Anchored Senate rerun (operational, user-run).**
- After a backup, and with the anchor plan's operational approval, rerun the Senate model with the anchor fix, so the published `us-senate-forecast.json` no longer carries the legacy +5.4 pt D shift.
- A new trend segment starts automatically.
- Releases stay disabled.

## Phase 5: Westminster backfill and publish (user-run)

1. Make a backup with `scripts/backup_db.py backup`.
2. Rerun all Westminster runs from 2024-07-05 to the latest date under the new config. Use `data/models/westminster/run_uns_model.py` with `--since-date`, `--as-of-date` and `--continue-on-error`; this is the path used in the Westminster plan's piece 11. Raw draws are written for the final date only.
3. Back up again.
4. `scripts/rebuild_model_trends.py`.
5. Stage the export with `export_elections.py --output-base /tmp/x`, and review the sanity checks from the Westminster plan's Verification section. Then run the real export with approval. Commit the data files separately.

## Phase 6: style (E), last

Use one formatting-only commit, so the fix diffs above stay readable and the review's line numbers stay valid until then:
- `ruff format` on the changed `.py` files;
- I001 import order and the unused imports listed in §E;
- `TypeError` for the type checks;
- `Mapping` import, function-level import comments, template line breaks and heading order, CSS tokens and BEM.

No behaviour change. The full suite must pass unchanged.

## Checkpoints

- [x] Piece 0: Westminster validation harness and baseline report.
- [x] Piece 1a: Group C robustness fixes.
- [x] Piece 1b: Group D test gaps.
- [x] Piece 1c: Tracker identity and smoothing.
- [ ] Piece 2a: Correlated vote-to-seat conversion error.
- [ ] Piece 2b: Heavy seat-level tails.
- [ ] Piece 2c: Error-scale parameter uncertainty.
- [ ] Piece 2d: House effects and sparse windows.
- [ ] Piece 2e: Nation-only parties and lead calibration.
- [ ] Piece 2f: Joint recalibration.
- [ ] Piece 3a: Westminster and US display changes.
- [ ] Piece 4a: Small US model fixes.
- [ ] Piece 4b: US national parameter uncertainty.
- [ ] Piece 4c: Senate state/race allocation.
- [ ] Piece 4d: Correlated US state errors.
- [ ] Piece 4e: Approved anchored Senate rerun.
- [ ] Phase 5: Approved Westminster backfill and publish.
- [ ] Phase 6: Style cleanup.

## Verification (every piece)

- `cd data && ./run_tests.sh`: strict mypy gates the run, then pytest. Narrow it with `-k uncertainty -q`.
- Smoke checks:
  - `./election_data/bin/python models/westminster/run_uns_model.py --dry-run`, which prints odds without writing;
  - `./election_data/bin/python scripts/export_elections.py --dry-run`;
  - `./election_data/bin/python -c "from console import create_app; create_app()"`.
- After model pieces: `./election_data/bin/python scripts/validate_westminster_uncertainty.py` (Piece 0). Compare with the committed baseline report and present the deltas.
- After front-end pieces: `npm test`, `npm run minify:electionmaps`, and the jsdom harness on Westminster and the US page.
- `git diff --check`. Stage only that piece's files, and confirm `model_output_trends.json` is not staged unintentionally.

## Decisions to take to the user (when their piece starts)

1. W-1 cluster definition and how 2024 is weighted in the fit (2a).
2. Whether to add the W-2 regime/fragmentation scaling (2b).
3. S-2: trailing window, or common random numbers (1c).
4. W-4: MC median as the headline, or shown alongside (3a).
5. The US-2 correlation data source (4d).
6. Go-aheads for each live run: 4e and Phase 5.

## Notes

- 2026-10-28: Builder reconciled this plan with the main checkout. Branch
  `poll-errors-simulation`, HEAD `282dd916`, clean working tree. The Senate
  anchor comparison is committed, as described above; none of this plan's
  implementation pieces is present. Added explicit checkpoints for tracking.
- 2026-10-28: User authorized proceeding through all pieces unless questions
  arise. This supersedes the per-piece pre-implementation and advancement
  pauses; design decisions and live-data actions still require explicit input.
  Local commits remain pending approval. Continue sequentially and do not
  launch the separate analyse/check-* workflow passes automatically.
- 2026-10-28: Piece 0 started with a scoped Codex implementer. Baseline targets
  are the saved audit figures, not newly measured results yet.

- 2026-10-28: User selected a trailing five-update average for Piece 1c.
  Keep the current random seeding policy; remove future-update look-ahead
  from the displayed bands and describe the trailing window in the UI.

### Piece 0 implemented and verified — 2026-10-28 (awaiting commit approval)

- Added historical validation, saved-replay precision/bias diagnostics, read-only
  CLI, baseline JSON, console Validation view, README instructions and 9 tests.
- Measured baseline: seat-total coverage 12/20; 2024 favourite losses 34/396
  (8.586%) leave-one-pair-out and 28/371 (7.547%) current config; national coverage
  30/35 (85.7%); lead z RMS 1.177. Excess kurtosis 22.599; 2.601% outside Gaussian
  99.9% band. Saved replay date 2026-10-22 and config 730a3349d3836bc6 match.
- Labour deterministic seats 300, MC median 282; majority MC SE 0.392 pp,
  five-seed SD 0.266 pp. Runtime kernel seed spread differs from audit custom
  draws; reported without claiming an exact audit match.
- Baseline repeat /tmp/westminster-validation-reproduced.json is byte-identical;
  all seven recorded source hashes match final implementation.
- ./run_tests.sh: strict mypy passed 292 files; 6056 tests passed in 249.24s.
  Model/export --dry-run and console startup passed. Validation route returned
  200 with baseline markers. git diff --check clean; trend cache untouched.
- Conditional history retains audit Gaussian experiment; extend it with future
  model components. Runtime diagnostics already use actual kernel. Ruff is
  unavailable in PATH/project venv; no installation attempted.
- No commit yet; checkpoint awaits approval/commit, but implementation is done.
  Review snapshot: /tmp/poll-errors-piece0-files.tar.gz and matching .txt list.
- Initial Piece 1a delegation and this note update were interrupted by a usage
  limit before any Piece 1a edits. User resumed; verified same HEAD and Piece 0
  worktree. Continuing Piece 1a.

- 2026-10-28: User chose Piece 2a party-specific nation × three baseline-share
  bands, with one shared shock per group, and equal weights for all four
  historical election transitions. This resolves both W-1 design questions.

- 2026-10-28: User chose heavier seat tails only for Piece 2b; do not add
  fragmentation-dependent error scaling. Chose MC median as headline for
  Piece 3a, retaining deterministic count labelled uniform-swing projection.
  Chose historical state-swing factors for Piece 4d rather than census-region
  factors. Read-only DB inspection confirms 16 presidential elections with
  56 units each from 1964 to 2024. All planned design choices are now resolved;
  live reruns/backfills/exports and local commit approval remain separate.

### Piece 1a implemented and verified — 2026-10-28 (awaiting commit approval)

- Completed C1–C10. Frozen legacy Senate report reads validate exact pinned
  SHA independent of active kernel; dead generation argument removed. Verified
  policy/catalogue reused and availability derived from their evidence, with
  duplicate provenance removed. No-reference reports are handled explicitly.
- Shared comparison identity/baseline helpers and console Others, average and
  fallback helpers. Archive EOFError normalized; national/regional US discrete
  intervals use the shared rounded quantiles. Tamper/selector regressions added.
- Corrected Senate report regenerated offline. Only availability, identity and
  limitations changed; all numerical fields and original legacy/input bytes
  are unchanged. Current page 4,617,603 bytes/0.187s vs 6,398,797/0.422s baseline;
  frozen legacy page loads successfully.
- 89 focused tests passed; first full run found a stale test expectation that
  recomputed the old unrounded tail probabilities. Replaced that expectation
  with explicit 5th/50th/95th percentiles; affected module 13 tests passed.
- Final ./run_tests.sh passed strict mypy (293 files) and 6062 tests in 237.20s.
  Model/export dry runs and console startup passed; git diff --check clean.
- No commit or live-data changes. Piece 1b now started sequentially; separate
  review passes remain unrequested. Checkpoint unticked pending commit approval.

### Piece 1b implemented and verified — 2026-10-28 (awaiting commit approval)

- All four high-risk test gaps covered: resealed numerical replay mismatch and
  explorer withholding; staging cleanup; true zero sampling variance; identity
  tampering through publication/export/trends; actual full House trends export
  and stale House/Senate sidecar cleanup, including dry run.
- Added kernel/replay and Westminster snapshot tampering, optimizer failure
  exits, comparison legacy/boundary handling and Senate export guards. Existing
  tests cover the remaining Senate trend and pinned/unavailable report cases.
- Frontend regressions cover central/summary mismatches, regional decoding,
  tied-caucus constraints and basis-only headers. Fixed missing Node global in
  optional jsdom popup test harness. Runtime sources are unchanged by this piece.
- Focused checks: 120 replay/public/WM tests, 64 guard/comparison tests,
  9 optimizer/Senate tests, 4 actual export cases, 8 snapshot variants all pass.
- Full run initially stopped at 4 new test typing errors; corrected imports and
  type narrowing. Final ./run_tests.sh: strict mypy 295 files, 6128 tests passed
  in 246.74s. All 649 JS tests passed; minify build byte-identical.
- Actual-module jsdom current-tree checks: Westminster 650 seats, House 435,
  Senate 35/full100; House tracker109 and Senate tracker5 points. Harness at
  /tmp/piece1b-smoke, logs /tmp/piece1b-*.log. Model/export dry runs and console
  startup pass. git diff --check clean; no data/release changes or commits.
- Piece snapshots in /tmp/poll-errors-piece1b-files.tar.gz and matching .txt.
  Piece 1c started sequentially with user's trailing five-update choice.
- Further source-data check for 4d: 16 complete presidential elections,
  51 statewide units, 15 transitions. Excluding ME/NE district rows from national
  sums, first three centred relative-swing PCA components explain about
  57.7%, 13.5%, 8.3%. This is source characterization, not calibrated poll error.

- 2026-10-28: Before Phase 2, identified that required raw-draw format bumps
  will invalidate the baseline replay archive used by validation. User approved
  adding a versioned frozen validation-input fixture to the repo, alongside the
  baseline report, rather than retaining it locally only. Measured input JSON
  size is about 110 KB for 650 seats (2026-10-22). Contents are model input
  arrays/IDs, aggregated shares and turnout, not individual polls or simulated
  draws. Implement in Piece 2a before bumping the draw format; record source
  archive hash/date/config and use it for comparable kernel diagnostics.

### Piece 1c implemented and verified — 2026-10-28 (awaiting commit approval)

- New Westminster summaries record WESTMINSTER_KERNEL_VERSION
  pf-westminster-mc-v1; trend segments derive persisted kernel/config identity.
  Unidentified historical summaries are separated conservatively. Compression
  retains identity transitions and explicit missing-summary gaps even when
  central counts match. Summary-free central-only legacy shape is preserved.
- Bands now average at most five trailing distinct valid updates, never future
  data, and do not bridge missing/segment gaps. Tooltip numbers remain exact.
- Full strict mypy passes295files; focused243Python tests pass. Real frozen
  2026-10-22 archive (10,000draws) loaded read-only and replayed exactly at
  indices0/5000/9999. Legacy missing-kernel stored-summary replay also tested.
- Final ./run_tests.sh: 6139tests passed in255.17s. npm:658tests pass; both
  shared bundles rebuilt. Actual-module jsdom: Westminster650, WM tracker7points
  with2segments/trailingnote, House435/tracker109, Senate35→100/tracker5 pass.
- Full-history synthetic payload313,842bytes; test budget300,000→330,000 to
  account for required64-character segment hashes. Model/export dry runs,
  console startup and gitdiffcheck pass. Tracked trendcache unchanged.
- Review snapshot /tmp/poll-errors-piece1c-files.tar.gz and matching.txt.
  No commit/live-data writes/releasechanges. Phase0+Phase1 are implemented;
  Piece2a starts sequentially with user-approved frozenfixture+cluster choices.

### Piece 2a prerequisite and feasibility — 2026-10-28

- Captured the approved 2026-10-22 aggregate input fixture before any draw-format
  changes: data/models/westminster/validation-inputs-2026-10-22-v1.json (111,570
  bytes), with original archive SHA, date, seed, config and input fingerprint.
  Added loader, CLI default frozen-input validation, console provenance labels,
  README guidance and malformed/provenance fixture tests. Simulation core and
  config are unchanged. Parent verified full mypy297files and26focused tests.
- Scratch equal-weight three-band nation×party cluster experiment first used
  unit-variance sensitivity, then direct nonlinear MC moment matching with
  common random numbers. Direct fits use3,000training draws/pair and6,000
  independent held-out draws; root tolerance0.02points. Moment is the equal-pair
  mean of (actual minus MC mean)^2 minus MC variance, including existing noise.
- Direct LOO cluster scales: 2015holdout7.295, 2017holdout7.365,
  2019holdout7.236, 2024holdout1.295points. All15earlier cells covered; no2024
  cells covered:15/20 total, failing17/20 target. Heldout2024 intervals:
  Labour321–361(actual411), Conservative189–240(121), LD42–63(72),
  SNP11–21(9), Others1–3(7). Leader/runner standardized misses5.89/−5.99.
- Scratch artifacts: /tmp/wm_cluster_pilot.py, /tmp/wm_cluster_direct.py,
  /tmp/wm-cluster-direct-results.json and associated logs/grid JSON. These
  diagnostics are not the production kernel and did not change the live DB.
- Core cluster implementation paused for user choice: investigate a more stable
  calibration (e.g. within-cluster vote-share residuals) before implementation,
  or build OFF infrastructure and defer17/20 acceptance to joint Piece2f.
  Do not tune heldout2024 to force acceptance or silently relax the threshold.
- Scoped implementer hit usage limit after prerequisite work. User resumed;
  parent confirmed same branch/HEAD/worktree and completed final focused checks.

### Piece 2a reassessment and approved offline route — 2026-10-28

- User chose investigation before cluster implementation, then approved architect-assessbug to revise Piece 2a. Read-only investigation found that full-loading residual calibration and a regional-balancing diagnostic each still cover only 15/20 cells, with all 2024 outcomes missed. See assessment.md for numbers, definitions and limitations. The latter diagnostic is not an approved runtime transform.
- User explicitly selected testing the already-approved heavy seat tails and lambda-scale uncertainty jointly with the cluster model, entirely offline, before engine infrastructure. Piece 2a now has a bounded reproducible experiment and evidence gate; the original 17/20 and calibration/parity requirements remain. No new structural prior, regime scaling, exact-regional-total projection or extra historical data is implicitly approved.
- Preserve the benchmark's actual-regional-swing central inputs; describe simulated aggregate drift accurately. Require full loadings and training-only fits, report all fixed factorial variants, and call subsequent LOO evidence retrospective because these transitions have already informed design.
- The frozen-input prerequisite passed the complete strict-mypy run (297 files) and 6,156 Python tests, plus 26 focused fixture/validation tests. Validation console returned HTTP 200. The latest frontend suite remains 658 passing tests from Piece 1c; this reassessment changes no source and requires no new source checks.
- Phase 0/1 and the input-fixture work remain uncommitted and intact. No production cluster/tail/scale implementation, raw-format change, live-data operation, activation, commit or remote write was performed during reassessment. Statistical acceptance is unresolved; do not mark Piece 2a complete.

### User pause checkpoint: offline joint prototype — 2026-10-28

- User asked for a pause point. Stop source edits and workflow advancement here. The already-authorized fixed validation batch remains running in the background and may finish writing its one offline report. Do not start new pieces, commit, run reviews or integrate engine changes while paused.
- Implemented the bounded offline joint prototype: new westminster_conversion_design.py, westminster_conversion_validation.py and westminster_conversion_report.py; dedicated scripts/validate_westminster_conversion.py; experiment-method document; saved-report console route/template; focused tests. Runtime kernel/config/raw formats and live data are unchanged.
- Verification complete: strict mypy303files; 6,176 Python tests passed in276.20s. Includes13 helper tests and7 reporting tests. Model dry-run, export dry-run and console startup pass. Full eight-variant console fixture renders HTTP200,169,978bytes. gitdiffcheck clean; tracked trend cache unchanged. No JavaScript changes in this piece; last frontend suite remains658passing.
- Audit rerun /tmp/poll-errors-joint-audit-baseline.json remains12/20 seat totals,85.7% national coverage,lead zRMS1.177, with frozen-input runtime diagnostics complete.
- Numerical recovery was repaired BEFORE historical scoring: coarse coordinate search missed a known better synthetic point; final bounded Gauss-Newton uses96-evaluation budget, explicit local axis+diagonal resolution probes, and one-sided optimizer-regret guard. Removed an uncalibrated two-sided score-support heuristic: a fitted point may legitimately improve on the generating point in a finite sample. Same seeds/model/nu grid/summary objective; original17/20 target unchanged. All changes and assumptions are in the repository method document and contract.
- Final synthetic recovery PASS: Gaussian seat1.5051/cluster0.0390 versus truth1.5/0; joint seat1.5547/cluster1.0099/nu4 versus truth1.5/1/4. Both128/256stable; all4 real-kernel parity checks pass. Final contract ec72d608bec5c35b341390d41fba27910cb171de5b624416c44b29b285db7437.
- Background process: exec session92863. Command from data/: OPENBLAS_NUM_THREADS=1 ./election_data/bin/python scripts/validate_westminster_conversion.py. Log: /tmp/poll-errors-joint-prototype-run.log. Final output: data/models/westminster/uncertainty_conversion_prototype_report.json. It loads historical data read-only and closes the DB transaction before lengthy fitting. Do not launch a duplicate run.
- At last status, six of eight comparisons finished: all four no-cluster controls13/20; both Gaussian-seat cluster models15/20. All six fail the offline gate. Seventh (cluster+Student seats+Gaussian lambda) was fitting its final2024heldout fold; eighth (cluster+Student seats+predictive lambda) remains. These partial counts are not the final joint result.
- Resume by reading the final report if present, validating its content/source identities and inspecting every fold/gate. If absent, poll session92863 and inspect the log; restart only if the original batch has ended without a usable report. Run the real saved-report console smoke check after completion. Do not rerun already-passing software checks unless source changes or a concern justifies it.
- If joint candidate fails17/20, stability, attribution, vote-mean or other declared gates, keep W-1 unresolved and ask for a concrete new design/evidence decision before engine/archive work. No scientific acceptance or Piece2a completion has been claimed.
- Source checkpoint: /tmp/poll-errors-piece2a-prototype-files.tar.gz plus.txt (11files; generated report pending), with source hashes /tmp/poll-errors-joint-prototype-source-freeze.json. Pre-prototype cumulative snapshot including frozen-input prerequisite: /tmp/poll-errors-piece2a-inputs-files.tar.gz plus.txt (51files). Earlier per-piece snapshots0/1a/1b/1c remain available. All work is uncommitted, unstaged, same branch poll-errors-simulation and HEAD282dd916. No remote writes or operational runs.

### Resumed: durable validation checkpoints — 2026-10-29

- User resumed. Same branch poll-errors-simulation/HEAD282dd916, all work unstaged and uncommitted; the11prototype source files exactly matched the pause snapshot before changes. The old exec session92863 no longer exists and no final report was written. Log records seven completed comparisons (all no-cluster13/20, all completed cluster15/20); the final candidate stopped during its last fold. Detailed fits were only in memory and cannot be reconstructed from those log counts.
- Fixed execution durability before rebuilding the results: new westminster_conversion_checkpoint.py atomically saves recovery, each128/256 fit and each evaluated fold in separate content-hashed files. Default directory /tmp/westminster-conversion-checkpoints/<identity-hash>/. Reuse requires matching source/config/NumPy/contract/counts, plus training hashes or heldout+fit hashes per stage. Corrupt or mismatched data fail closed. Complete reports remain separate from scratch checkpoints; paired standardized draw references are retained only in scratch state. CLI --checkpoint-dir selects scratch storage; --no-checkpoint disables it.
- Exact per-profile latent caching preserves original float32 operations, streams, seeds, model and statistical contract. 88helper tests include bit-identical cached/uncached draws/objectives/full fixture fits, zero components, chunk sizes, immutability and profile mismatch checks. Representative650seat×13party benchmark1.233s uncached versus0.805s including cache preparation (1.53×);128draw latent memory12.6MiB. No approximations or new statistical assumptions.
- Full candidate now executes first to resolve its acceptance earlier; all eight models still run and the completed report retains canonical variant order. Statistical contract remains ec72d608bec5c35b341390d41fba27910cb171de5b624416c44b29b285db7437. The source identity necessarily changes for execution fixes.
- Verification so far: strict mypy304files and98focused tests pass (88helpers+10report/checkpoint tests); interruption/resume yields exactly the same report as fresh computation, changed historical inputs recompute affected fits, and corruption/source separation are checked. Full suite is running in exec session24303, log /tmp/poll-errors-joint-prototype-checks-2026-10-29.log. Do not confuse this pending suite with yesterday's6176pass.
- Restarted checkpointed batch: exec session86298, log /tmp/poll-errors-joint-prototype-run-2026-10-29.log. Same command from data/: OPENBLAS_NUM_THREADS=1 ./election_data/bin/python scripts/validate_westminster_conversion.py. Output remains data/models/westminster/uncertainty_conversion_prototype_report.json. Do not launch a duplicate while this session remains active. If interrupted later, repeat the identical command to reuse completed stages; inspect the logged checkpoint directory and do not edit source before reusing it.
- Source snapshot /tmp/poll-errors-piece2a-prototype-files-2026-10-29.tar.gz (12files), hashes /tmp/poll-errors-joint-prototype-source-freeze-2026-10-29.json. The original October28 snapshot is retained. Runtime/config/raw archives/live DB/release flags remain unchanged. Original17/20, attribution, stability, bias and parity gates remain; Piece2a is still unresolved until evidence is reviewed. No commits, remote writes or separate review workflow passes.

### Full joint candidate scored; control batch still running — 2026-10-29

- Current source checks: strict mypy passes on 304 files; full Python suite **6254 passed** in 291.51 s. All 98 focused conversion checks pass. `git diff --check` is clean. Source hashes still match `/tmp/poll-errors-joint-prototype-source-freeze-2026-10-29.json`; earlier-piece hashes are unchanged except the three deliberately extended integration files (README, uncertainty blueprint, baseline validation template). The tracked trend cache is unchanged.
- The complete `cluster-student-predictive_lambda` candidate has now been scored at the fixed 128/256 training precisions and 6000 evaluation draws per fold. Coverage is **15/20**, below 17/20: 2015 4/5, 2017 5/5, 2019 5/5, 2024 1/5. This is retrospective diagnostic evidence, not untouched validation.
- Held-out 2024 90% intervals: Labour **317–361** versus 411 actual; Conservatives **181–242** versus 121; Liberal Democrats **41–64** versus 72; SNP **11–21** versus 9; party 13 **6–8** versus 7 (the only covered cell). The 256-draw fit has seat core 2.93314033 points, cluster 1.35203737 points, seat nu 2.25 (lower grid boundary), lambda scale 0.2954317492 and lambda df 3. Its cluster scale also fails the declared 128/256 stability guard.
- Leader/runner RMS standardized errors are **2.7201 / 2.4726**, outside the paired predictive 5–95% references **0.4053–1.5694 / 0.3989–1.5579**. National mean-vote drift maxima are 0.1879, 0.2428, 0.2302 and 0.1853 points, all above the fixed 0.15-point guard. The 2015 fit additionally has materially different near-optimal tail-profile predictions. No gate has been relaxed and no production component has been integrated.
- A concise user decision is pending: investigate additional comparable historical evidence first (recommended), or replan the conversion model on the existing four transitions with explicitly new assumptions. Do not perform dependent design/engine work until answered.
- Session 86298 continues the remaining seven controls, checkpointing completed stages in the recorded namespace. The final saved eight-variant report and actual console smoke remain pending; do not describe the batch as complete yet. Session 24303 has completed successfully.

### Completed offline joint validation; awaiting model-direction answer — 2026-10-29

- Session **86298 completed successfully (exit 0)**. All eight canonical comparisons are saved in repository file `data/models/westminster/uncertainty_conversion_prototype_report.json`; report hash **3ead7c5851e96e1fd143a26c9370de1fca963b31925c55648a3a4dd889201428**. No validation job remains running. Report size is 6,280,481 bytes; it retains all profile diagnostics, not raw seat simulation draws.
- All four no-cluster controls cover **13/20**; all four cluster variants cover **15/20**. None meets the unchanged 17/20 coverage gate. The full cluster + heavy-seat-tail + predictive-lambda candidate also fails standardized RMS, fit stability, fit identification guards and mean-vote drift. Lambda-attribution and declared precision checks pass, without constituting proof of identification. The final decision is **stop_before_engine**.
- Independently read the completed report through its strict hash reader; verified current configuration and every recorded source/input hash, successful synthetic recovery, and all eight comparisons. Actual saved-report console GET `/westminster/uncertainty/conversion-validation`: **HTTP 200**, 9,260,817 bytes, approximately 0.91 s. Labour/Conservative labels and real holdout/variant content render. Strict mypy **304 files**, full pytest **6254 passed**, and `git diff --check` remain clean. No source was edited during or after the fixed batch.
- Completed source/report archive: `/tmp/poll-errors-piece2a-prototype-complete-2026-10-29.tar.gz`, 13 files; matching SHA-256 manifest `/tmp/poll-errors-piece2a-prototype-complete-2026-10-29.json`. The source-freeze manifest and all protected earlier-piece hashes pass. The tracked trend cache remains unchanged. Existing earlier-piece archives remain available for separating local commits later.
- **Stop here pending the user's answer** to the already-presented question: investigate more comparable historical evidence first (recommended), or replan the conversion model on existing history with explicitly new assumptions. Do not silently add a cluster-scale prior/regime dependence/regional balancing, lower the gate, claim Piece 2a complete, or proceed with its production integration/2b/2c on this failed evidence. No new answer has arrived as of this checkpoint. Existing all-pieces authorization still applies once material questions are resolved.
- All current source and results remain uncommitted/unstaged. No live DB/model/config/archive/release flags were changed; no backfill, operational rerun, activation, commit or remote write was performed. Pieces 0/1a/1b/1c and frozen-input prerequisite remain implemented but await local commit approval. No automatic review workflows have been started.

### Active work committed locally at the user's request — 2026-10-29

- User explicitly requested committing active work after saying they would return to the model investigation later. This authorizes saving the current checkpoints; do not resume research, change the statistical contract, integrate the failed model or advance other pieces without the user's return.
- Created six local commits on `poll-errors-simulation`, preserving each piece's archived versions of overlapping files. The final tree matches every saved source/report hash and the previously tested working tree. The index and worktree are clean. No push, PR/issue publication, database write, import, backfill or operational rerun occurred.
- `c067f150` — feat: add reproducible Westminster uncertainty validation
- `f1e7d0cb` — fix: harden uncertainty simulation and Senate replay
- `73fadb68` — test: cover uncertainty exports and simulation safeguards
- `f417a14e` — fix: remove look-ahead from tracker uncertainty bands
- `92743751` — feat: freeze inputs for Westminster validation replay
- `ab3fc62e` — feat: add offline Westminster conversion-error experiment
- Pieces 0/1a/1b/1c are now committed and their implementation checkpoints are ticked. The frozen-input prerequisite is committed. **Piece 2a remains unticked and unresolved**: its offline experiment is committed as evidence of failure (15/20 versus the unchanged 17/20 gate), not production integration or scientific acceptance. All later implementation checkpoints remain open. A separate self-review of these new changes has not been requested or performed; do not claim review finalization has passed.
- Verification retained: strict mypy 304 source files; 6254 Python tests; 658 JavaScript tests and rebuilt map bundles. Sources have not changed since those checks. Each staged checkpoint passed whitespace validation; all61active file versions match the archived end state. Commit record is `commits.md`; local WIP PR text is `pr.md` (not published).
- The user previously chose investigating more historical evidence. Read-only research found two promising extra transitions: 1997→2001 has659matching normalized constituency index names, with common boundaries confirmed in Commons CBP-8647; candidate totals, party mapping and geography still need a full audit. For2005→2010, Parliament's new CSV has591notional constituencies, all code/region matches to2010 and every candidate-vote sum reconciled; its missing59Scottish constituencies were all retrieved from the older historical results site (382candidate rows;2333887valid votes). A persisted boundary/code crosswalk and party-quality checks are still required. The old site does not encode boundary changes in its constituency IDs.
- Source limitations: 349rows in the2005notional file are unnamed minor-party aggregates; do not infer separate minor-party votes or turn unknown values into known zeros. Labour Co-op and Speaker labels also require explicit handling. Commons unified CSV/XLSX endpoints returned403 during this research. A Norris1992–2005workbook downloads but its2005result fields are missing/error-valued, so it is not a verified replacement. No new historical pair has entered fitting and no data files were written by the investigation.
- Primary sources: https://electionresults.parliament.uk/general-elections/7 (candidate CSV /candidacies.csv); https://api.parliament.uk/uk-general-elections/general-elections/42 (1997), /43 (2001), /44 (2005); https://commonslibrary.parliament.uk/research-briefings/cbp-8647/ (boundary documentation); https://api.parliament.uk/uk-general-elections/constituencies (boundary-identity warning).
- A historical-scope question was presented: start with1997→2001and2005→2010(recommended), or include the1980s/early1990snow. The user asked for an explanation of the full flow rather than selecting either scope, then said they would return later. **That scope remains undecided.** The original20-outcome17/20gate must not be diluted by adding older outcomes to its denominator.

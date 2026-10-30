# Full review: poll-errors-simulation (uncertainty for Westminster and US)

**Date:** 2026-10-26. **Mode:** `/review self`. **Status: parked.** No fixes applied and no commits made. Waiting for the user's assessment.

**Branch:** `poll-errors-simulation` at `134d1fc4`. Base `master`, 41 commits, 286 files.
**Also in scope:** the uncommitted Senate anchor piece 4 (8 modified files and 4 untracked).
**Issues covered:**
- `add-monte-carlo-uncertainty-to-westminster-projection`
- `add-us-election-uncertainty-and-overall-poll-trackers`
- `anchor-us-senate-swings-to-matching-house-national-baselines`

**Not reviewed:** generated artifacts. That means the calibration and audit JSON, test fixtures, `electionmaps/data/**` and the minified bundles.

## Passes run

All passes were read-only. The repo's `git status` and HEAD were checked as unchanged afterwards. Scratch copies were used for running anything.

| Pass | Model | Outcome |
|-|-|-|
| Independent methodology audit | Opus, max | 1 P1, 6 P2, 12 P3. Full report: [../poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/review.md](../poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/review.md) |
| code-review (max) | Opus | No P1 or P2 bugs. 7 robustness and maintenance findings |
| check-coverage | Opus | Coverage is strong (85–99% line+branch). 4 high-risk gaps |
| check-common-mistakes | Opus | 8 findings, mostly overlapping code-review. No guide entries added |
| check-security | Opus | Clean. 2 low notes that need no action |
| check-style | Sonnet | Formatting, imports and template layout only |

Earlier reviews were read and are not repeated here:
- Westminster, 2026-10-14 and 2026-10-15;
- US line-level, 2026-10-25 at `03bbf46d`, whose 5 fixes are in `76be5ea8`. code-review found 4 of these working; it didn't trace the open-popup refresh or run the JS tests.

## Verdict

**The uncertainty machinery is implemented correctly. The Westminster error model's dependence structure and tails are not right, so the Westminster seat odds are overconfident.**

*User clarification, 2026-10-26:* Westminster uncertainty has not been pushed to prod. Changing its error model and calibration on this branch is fine, followed by a backfill. The "gated" label on Group A findings therefore does not apply. They can go in this branch's fix loop, and the separate-issue recommendation is only about how big W-1 is.

What is right:
- Zero-error parity is exact.
- The zero-sum sampling covariance is correct.
- The national error is drawn once per simulation and shared.
- MC vote-share means land within 0.15 pt of the central projection.
- Seeds reproduce, and draws don't change with chunk size.
- The Senate holdover, caucus and VP logic is correct, and so is the presidential EV and no-majority logic.
- The Senate anchor fix is correct: corrected 2022 mean +0.643 vs legacy +12.497, and the 70/30 recipe gives zero swing.
- No shipped or gated Senate scale comes from the legacy biased residuals.

What is wrong:
- Seat deviations are independent across seats, so seat-total and majority/hung odds are far too narrow.
- Seat-level tails are thin.
- Error scales are treated as known, although they come from only 9 elections.
- There is no end-to-end validation. Pre-2024 polls aren't stored, so none is possible.

US: the software is sound and correctly gated. All three releases are disabled. The Senate and President candidate configs carry no numeric scales, so they fail closed. The calibration doesn't identify the scales, so the US should stay gated.

### Can the US models be run?

- **Central US projections: yes.** House and President behaviour is unchanged.
- **The Senate central projection:**
  - The published `us-senate-forecast.json` comes from 113 legacy runs (latest 2026-10-19). They carry the cohort-composition error, about +5.4 pt D on the margin for unpolled 2026 races (A-1).
  - Rerunning on this branch applies the anchor fix and starts a new trend segment. That is an improvement, but it still needs the gated operational approval in the anchor plan.
- **US uncertainty: not as published odds.**
  - The House has a candidate calibration, so a gated pilot run is fine as a diagnostic. Its odds shouldn't be published: the national scale rests on 6 cycles, and the state/race split rests on one transition.
  - The Senate and President can't produce odds today. Their candidates have no scales, by design.
  - Before release, fix US-1 (Senate state/race allocation) and US-2 (no regional correlation between states).

## Findings

**Effort key:** S = small, M = medium, L = needs a design and its own issue.
**Gate:** whether fixing it changes calibration or published numbers, which all three plans keep behind separate approval.

### A. Westminster error model (methodology)

| ID | Pri | Where | Finding | Evidence | Fix | Effort / gate |
|-|-|-|-|-|-|-|
| W-1 | **P1** | `model_support/monte_carlo.py:761, 954–956, 964`; `calibration.py:937–947, 1147–1172` | Uncertainty in turning votes into seats is missing. Seat deviations are independent normals, so they cancel in national totals. λ is the only shared conversion term. | Given the actual regional votes, the 90% seat-total ranges cover 12 of 20 party×election cells. 2024: Lab 411 vs 328–354, Con 121 vs 197–234. Leader/runner-up RMS 36/48 seats vs a model SD of about 6–8 (`checks/wm_seat_total_backtest.py`, `wm_2024_swing_type.py`) | Add correlated conversion error across seats: party×region-type or seat-cluster factors, or a fitted seat-total residual term. Validate by leave-one-pair-out on seat totals | L / gated |
| W-2 | P2 | `monte_carlo.py` seat draws | Seat-level tails are too thin: Gaussian, against an excess kurtosis of 22.6 | 2.6% of residuals fall beyond the Gaussian 99.9% band. 2024 ≥95% favourites lost 7.5–8.6% of the time. 24 seats export "win 100%" (`wm_seat_reliability*.py`) | A Student-t or mixture seat term, calibrated on 2024 fragmentation | M / gated |
| W-3 | P2 | Plug-in σ throughout, Westminster and US | Errors use point-estimate scales, so estimation uncertainty is dropped | t₉ predictive: Reform P(maj) 0.02% → 0.29%, Lab P(largest) 94.0% → 91.8%. Leave-one-out national coverage 85.7% vs a nominal 90%. Heavier seat tails alone move P(Lab maj) 18.6% → 14.9% (`wm_param_uncertainty.py`, `wm_national_loo.py`) | Draw σ per simulation from its sampling distribution, or use t_ν marginals | M / gated |
| W-6 | P3 | Sampling variance V | House effects are not in V | Adds about 0.6–1.0 pt SD, roughly 5–9% of national variance; 4.8 effective pollsters (`wm_house_effects.py`) | Add a between-pollster variance term | S–M / gated |
| W-7 | P3 | Share scaling of the national error | SNP and Plaid national error is scaled by their GB share | SNP in Scotland gets about 1.6 pt polling SD vs 2.75 for an equal-sized GB party | Scale by the share within the party's nation | S / gated |
| W-8 | P3 | Calibration | The lead between the top two parties is not calibrated | Lead z RMS 1.18. The 2024 7.2-pt lead error was z = 1.75 | Calibrate the rival-difference variance, or add a negative-correlation term | M / gated |

### B. Westminster display and tracker (presentation; the model is unchanged)

| ID | Pri | Finding | Evidence | Fix | Effort |
|-|-|-|-|-|-|
| W-4 | P2 | The headline seat count is the deterministic uniform-swing count, not the centre of the published distribution | Lab 300 vs an MC median of 282. In 37 stored runs the central count fell outside its own 90% range (`results/wm_central_outside_band.txt`) | Label it as the uniform-swing projection and/or show the MC median, and flag when it falls outside the band | S |
| S-1 | P3 | Probabilities shown to 0.01 pp, but the MC SE is about 0.4 pp, and there is no >99% cap | Seed-to-seed SD of P(Lab maj) is 0.33 pp. 24 seats show 100%, 103 show ≥99% | Round to 1 pp and show "<1%" / ">99%" | S |
| S-2 | P3 | The centred 5-update band smoothing uses up to 2 later runs at past dates, and lags at the latest date | | Use a trailing window, or label it | S |
| S-3 | P3 | The Westminster tracker has no segment break when the config changes | US splits correctly via `central_options_id` | Put a config identity in the trend points and split on it | S–M |
| W-5 | P3 | Sparse early windows publish volatile odds | 12 trend entries have ≤2 pollsters. One-day jumps in P(hung) reach 0.53 (`results/wm_window_density.txt`) | Set a minimum-pollster threshold to publish odds, or mark those points | S |

### C. Code robustness (mostly the uncommitted anchor comparison; code-review and common-mistakes agree)

| ID | Pri | Where | Finding | Fix | Effort |
|-|-|-|-|-|-|
| C-1 | P3 | `model_support/us_calibration_comparison.py:134,146, 653–664`; `console/blueprints/us.py:142` | The legacy Senate report can't be regenerated, because `legacy_senate` is never passed True. Its reader still requires `kernel_version == US_KERNEL_VERSION`. The next kernel bump breaks `?reference=legacy` and `test_original_reports_and_input_bytes_remain_pinned` | Check the frozen legacy file by its pinned hash only, or wire `legacy_senate` to a CLI flag. Otherwise remove the parameter | S |
| C-2 | P3 | `us_calibration_comparison.py:568` | `anchor_catalogue_sha256 = next(...)` with no default raises a bare `StopIteration` when no Senate cycle is available | Take the sha from `resolve_senate_house_anchors`, as the read side does (~:671), or raise `ComparisonError` | S |
| C-3 | P3 | `us_calibration_comparison.py:104, 110`; limitations ~:546–556; `render_comparison_report` ~:712–736; `templates/us_uncertainty_comparison.html:32–37` | Availability years `{2018, 2024}` / `(2016, 2020, 2022)` are hard-coded apart from the pinned policy. "Only 2022 is usable" is fixed text, not computed from `reference_availability` | Derive availability from the policy, or catch `HouseAnchorError` per race. Render the counts and years from the data | S–M |
| C-4 | P3 | `us_calibration_comparison.py:102–111, 665`; template :38 | Each request re-reads and re-hashes the anchor policy and catalogue 77 times. The page is 6.4 MB / 0.43 s vs 4.8 MB / 0.15 s for the legacy page. About 1.47 MB of it is duplicated `provenance` in the availability `<pre>` | Resolve once per cycle, and drop `provenance` from the availability rows | S |
| C-5 | P3 | `console/services/us_uncertainty_explorer.py:297–299` | The console has its own copy of the "Others" rule, and of the fallback route and weighted average, separate from `is_others_party` / `senate_fallback_swings` | Import the model's helpers | S |
| C-6 | P3 | `model_support/us_replay.py:216` | `load_us_draws` doesn't catch `EOFError`, unlike `:346` and `monte_carlo.load_draws`. A truncated archive gives a console 500 | Add `EOFError` to the caught set | S |
| C-7 | P3 | `us_run.py:~267`, `us_simulation.py:672` | The US interval quantiles miss the rounding fix used in `monte_carlo._interval_quantiles`. This is latent: with `method="nearest"` it only bites at .5 rank boundaries | Use the shared rounding helper | S |
| C-8 | P3 | `blueprints/us.py:142`, `us_calibration_comparison.py:578, 712` | `"senate-comparison-report.json"` is written out 3 times | Add a constant next to `SENATE_REPORT_STEM` | S |
| C-9 | P3 | `_senate_anchors:95` / `senate_reference_availability:108`; `make_`/`read_comparison_report` identity dict; `_common.prepare_us_projection ~2525` / `_validate_history_inputs ~3146` | Validation and identity-building code is copied in pairs | Extract helpers | S |
| C-10 | P3 | `tests/test_us_calibration_comparison.py:95`; `tests/test_us_senate_anchor_comparison.py` | The parity test skips `cycle == 2024` by year. The anchor test matches `"stale/mismatched"`, which fires for both checks. `reference_availability` is never tampered | Use `senate_reference_availability(...)["available"]`. Match "anchor identity". Add `reference_availability` and `comparison_version` to the tamper loop | S |

### D. Test gaps (check-coverage)

The modules under review are at 85–99%: `monte_carlo` 98%, `calibration` 99%, `uncertainty` 99%, `us_calibration_comparison` 97%, `westminster_uncertainty` 96%, `us_simulation` 87%, `us_replay` 84%, `us_validation` 81%, `us_run` 78%. The JS coverage tool isn't installed, so the JS was reviewed by reading.

**High risk.** A wrong branch here changes published numbers or lets stale data through.
1. **US replay mismatch is never exercised.** `us_replay.py:427`, staging `:107`, and the temp cleanup `:130–133`; the explorer withholding output at `us_uncertainty_explorer.py:390`. The Westminster equivalent is tested. *Test:* corrupt a saved array, re-seal it, and assert the replay raises. Patch `project_us_seats` and assert no `.tmp` or destination file is left.
2. **Zero poll average → zero sampling variance is untested** in `run_uns_model.py:1588–1591` and `westminster_uncertainty.py:114`. *Test:* a party with a national and a regional 0% average gets variance 0, not the fallback.
3. **Identity-mismatch skips are untested.** `trends.py:503, 506, 512`; `export/us_uncertainty.py:77, 97`; `us_uncertainty.py:103, 108, 123`. Only the edited-vote-row case is tested. *Test:* parametrized tampering (summary without artifact_id, result_identity, renamed seat, dropped band party) → `absent` / no trend summary.
4. **The House trends export block never runs.** `export_elections.py:1042–1051` writes `us-house-trends.json`. Stale-file removal at `:1053, 1060` is untested too. *Test:* a full export with one House election; a no-election export removes stale files, but not under `--dry-run`.

**Medium risk:**
- Raw-destination overlap guards: `_common.py:3032`, `run_uns_model.py:1505`.
- Senate trend reconstruction skips: `trends.py:430, 434, 456`.
- Tampered replay snapshots: `simulation_replay.py:61–119`, `us_run.py:66, 110–122`, `us_replay.py:165–315`.
- Comparison report: default read rejects a legacy version; the tamper loop; C-2; `legacy_senate`; `fit_boundary` "target not bracketed" at `:329`.
- House calibration Gauss–Newton exits: `us_calibration.py:612–663`.
- Kernel input guards: `us_simulation.py:212–451`.
- Senate export validation: `export/senate.py:48, 61, 68, 102`.

**Frontend:**
- `polltracker-us.js:178–181, 232–233` (summary dropped when central ≠ metrics) has no House or Senate test.
- `us-uncertainty.js:114–118, 125–147` (decodeRegions edge cases; tied caucus ≤ no-majority).
- `uncertainty-view.js:183–185` (basis-only header).

### E. Style (check-style)

- **Python formatting:** 143 of 163 changed `.py` files aren't `ruff format` clean. The base is roughly 53% unclean, but the new code is much worse; `us_calibration_comparison.py` has 85 lines over 88 columns, the longest 366.
- **Imports:** I001 import order in about 70 files, and these unused imports:
  - `Pollster` at `polls/importers/westminster/bmg_research_import.py:24` and `survation_import.py:22`;
  - `Any` at `scripts/calibrate_uncertainty.py:47`;
  - in the tests: `test_calibration.py:15`, `test_monte_carlo.py:14`, `test_us_validation.py:7`, `test_console_uncertainty_runs.py:24`, `test_us_house_persistence.py:29`, `test_us_house_public.py:20, 28, 30`.
- **Other Python:**
  - `monte_carlo.py:1748, 1850, 1958, 1969` import inside functions with no comment saying why;
  - `raise ValueError` on type checks should be `TypeError`, at `console/services/trends.py:58`, `senate_cycles.py:110, 371`, `us_release.py:85` and `us_replay.py:188`;
  - `Mapping` comes from `typing`, not `collections.abc`, at `console/services/uncertainty_explorer.py:8`.
- **HTML and CSS:**
  - Long one-line console templates: `uncertainty_history.html:3`, `uncertainty_simulations.html:7`, and the `us_uncertainty_*` templates.
  - An `<h4>` comes before the first `<h2>` at `us_uncertainty_explorer.html:7`.
  - `min-width: 160px` and a hard-coded `#2563eb` in `console/static/style.css`.
  - The `.uncertainty-col` classes in `maps.css` aren't BEM.
- **JS:** clean.
- **Advice:** if this is done, do it as a separate formatting-only commit, so the diff stays readable.

### F. US design notes (gated; needed before any US release)

| ID | Pri | Finding | Fix |
|-|-|-|-|
| US-1 | P2 | The Senate state/race split can't be identified from the data, yet it decides polled-race variance. National and state sources load at full weight whatever α is (`us_simulation.py:473–474`). A state-only allocation puts about 15 pt of margin SD on every polled race. The fit rests on one cycle, 2022 (centred MS 197.4, vs 82.4 for legacy 2024) | Treat the fallback residual as race-level. Calibrate polled-race error from the 24 final Senate averages. Import H from the House or presidential state errors. Require ≥2 conditional cycles |
| US-2 | P2 | States share nothing beyond the national factor (`us_simulation.py:456–476`), yet historical state errors cluster (2016: WI +7.3, IA +6.4, OH +5.9 vs CA −7.8, IL −5.6) | Add regional and demographic factors calibrated on the state errors. Validate the EV distribution by whole-cycle holdout |
| US-3 | P3 | The national draw G is independent per chamber (`us_simulation.py:176–180` seeds by model name). Marginal odds are unaffected | Seed the G stream by date only, or build a joint simulation |
| US-4 | P3 | ME/NE districts inherit the statewide residual only when the state is polled (`us_simulation.py:370–376`). Correlation ≈ 0.17 otherwise | Always load the parent residual, plus a district term |
| US-5 | P3 | The House G is fitted to the generic-ballot input error (2.44), but the engine's output margin SD is 2.29 | Calibrate on projected-output error, or document the shrinkage |
| A-1 | P3 (ops) | No live or published Senate run uses the anchor fix yet. The legacy method shifts unpolled 2026 races about +5.4 pt D−R. The regional own-poll branch (`_common.py:2431–2439`) still subtracts the Senate-field regional baseline, but no US regional polls exist | An approved anchored rerun. Correct the regional branch before any regional polls arrive |

### Security (check-security): clean

- No SQL, HTML or DOM injection.
- Console routes only read and validate their inputs.
- There is no path traversal and no secrets.
- Low notes:
  - exception text, possibly including local paths, shows in console error banners (escaped, localhost only);
  - backfill and repair scripts fetch the stored `source_url`, which is the same trust model as import.

## Already documented, not raised

These are acknowledged in the plans (methodology report §5):
- λ centred at 0, although all 4 fitted λ̂ > 0. This was a user decision: λ = 0.34 would make the central count Lab 284 / Ref 137 / Con 98.
- σ_reg = 1.5 is a guess.
- Nowcast framing.
- Sinn Féin abstention is not modelled.
- No full-pipeline backtest.
- US House geography and assumptions.
- The Senate's 2018 anchor is unavailable, which removes 2024.
- The President has a 3-cycle national proxy.
- No joint cross-chamber probability.

## Recommended next steps (for the user to decide)

1. **A (W-1, W-2, W-3 and W-6–W-8):** Westminster isn't in prod, so these can be done on this branch and then backfilled. W-1 needs a design decision on how to correlate seat errors, so plan it first (`architect-build`, or a plan section on the Westminster issue). W-2, W-3, W-7 and W-8 are smaller calibration changes, and W-6 adds a variance term. Re-run the seat-total backtest (`checks/wm_seat_total_backtest.py`) as the acceptance check. The **B** display fixes, especially W-4 and S-1, are cheap either way.
2. **B, C and D:** suitable for a `/review self` fix loop on this branch.
3. **F:** fold into the US calibration/acceptance work before any US release gate is lifted.
4. **E:** an optional formatting-only commit.
5. **Uncommitted piece 4:** still waiting for manual review. Commit it on its own before any fix commits.

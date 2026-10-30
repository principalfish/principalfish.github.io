# Methodology audit: Monte Carlo uncertainty for Westminster and US projections

Date: 26 October 2026. Independent statistical and election-modelling review. Read-only.

## 1. What was reviewed

- Repo: `/home/philiph/workspace/principalfish.github.io`, branch `poll-errors-simulation`.
- HEAD: `134d1fc4` (`feat: explain frozen Senate anchor inputs in console`). Base: `master`. Merge base: `7736a80e`. Diff: `git diff master...HEAD`, 286 files. The review concentrated on `data/model_support/`, `data/models/`, `data/scripts/` and the tracker/uncertainty JS.
- Uncommitted work included in scope (the Senate anchor v2 historical comparison):
  - modified: `README.md`, `data/console/blueprints/us.py`, `data/console/templates/us_uncertainty_comparison.html`, `data/model_support/us_calibration_comparison.py`, `data/model_support/us_comparison_inputs.py`, `data/model_support/us_comparison_senate.py`, `data/scripts/calibrate_us_uncertainty.py`, `data/tests/test_us_calibration_comparison.py`;
  - untracked: `data/models/us/calibration/senate-comparison-house-anchor-v2-review.md`, `senate-comparison-report-house-anchor-v2.json/.md`, `data/tests/test_us_senate_anchor_comparison.py`.
- Stated intent: issue/plan/independent-note for the Westminster uncertainty issue, the US uncertainty issue, and the Senate anchor issue (`assessment.md` and `plan.md`), plus the v2 review proposal.
- Earlier reviews read so their points are not repeated: the 2026-10-14 and 2026-10-15 Westminster reviews, the 2026-10-25 independent US review at `03bbf46d`, and the 2026-10-26 anchor review.
- Evidence used, all read-only:
  - the live DB, opened with SQLite URI `mode=ro`;
  - the saved Westminster raw-simulation replay snapshot `/home/philiph/dbs/simulations/westminster-latest.npz` (run of 2026-10-22, election 104583, exact inputs and config);
  - the committed calibration inputs and reports;
  - the exported site JSON.
- No repo file, DB, git state or test cache was touched. Scratch scripts and outputs are listed in §6.

## 2. Top-line verdict

**Mechanically, yes. Structurally, not yet, for seat outcomes, and that matters most for the Westminster odds already published.**

What works:

- The engines do what their specifications say. Zero-error parity is exact. The zero-sum sampling covariance is correct. The national error is drawn once per simulation and shared. Centring and engine-unit calibration are consistent.
- Monte Carlo vote-share means land within 0.15 pt of the central projection.
- Seeds reproduce exactly.
- The US release gates stop any uncalibrated US odds from being stored or published. No shipped or gated Senate scale comes from the biased legacy residuals.

What is wrong is the dependence structure and the tails:

- **Seat-total (vote-to-seat) uncertainty is close to absent.** Westminster seat deviations are independent Gaussian noise. Given perfect knowledge of the actual regional votes, the model's 90% seat-total ranges covered only 12 of 20 party×election cells. In 2024 the actual Labour and Conservative totals fell 57 and 76 seats outside those ranges (70 and 95 seats from the medians).
- **Seat-level tails are too thin.** In 2024, seats the model rated as ≥95% favourites lost 7.5–8.6% of the time.
- **Plug-in Gaussian errors drop parameter uncertainty.** The national error scale comes from only 9 elections (US: 6 or 3 cycles). Treating it as known understates underdog tail probabilities by up to 14×.
- **No end-to-end validation exists.** The published "90%" intervals have not been validated end to end, and cannot be with the stored poll history.

The US layer is sound software held correctly behind its gates. Its scales are not identified:

- the Senate rests on one conditional cycle, and its state/race split decides whether polls reduce uncertainty at all;
- there is no inter-state correlation beyond the national factor.

It should stay gated until those are addressed.

## 3. Per-question verdicts

| Q | Topic | Verdict | Key evidence | Findings |
|-|-|-|-|-|
| 1 | Decomposition, double-counting, effective n | **Sound** (minor overlap; house effects missing) | National sampling SD 0.18 pt vs national term 2.28 pt (0.6% of national variance). Sampling inside the PollBase final-poll averages is roughly 2–7% of σ_nat². Double counting is under 5% of variance. V = Σw²p(1−p)/n_eff/(Σw)² is the correct variance for the deployed decay-weighted estimator. House effects are in neither V nor the window-specific σ: the deployed average's house-effect SD is 0.6–1.0 pt, with 4.8 effective pollsters. US aggregates get no invented N, and the President gets no extra national SRS. | W-6, W-5 |
| 2 | Share-space Gaussian, s(p), clamp/renormalise, "parity" | **Votes sound; seats questionable** | MC mean minus central vote share: ≤0.15 pt for every party (Others +0.15, LD +0.09). Seat medians move: Labour central 300 vs MC median 282 (mean 281); Reform 109 vs 122. Across 840 runs, Labour central minus median ranged −21 to +42 seats, and 37 runs had central outside the 90% range. "Parity" means a bit-exact zero-error reproduction plus an unchanged point estimate. It does not mean the MC is centred on the point estimate. | W-4 |
| 3 | Correlation structure and tails | **Questionable** | Shares sum to 100 exactly. The only negative rival correlation comes from centring: ρ(Lab,Ref) = −0.35 now, −0.36…−0.61 at historical shares; history gives −0.53 (centred), −0.63 (zero-origin). Regions are independent given the national error, and seats are independent given their region. Seat residual excess kurtosis is 22.6, and 2.6% of residuals fall beyond the Gaussian 99.9% band (26× nominal). The US has no inter-state factor beyond G, although historical state errors cluster regionally. | W-1, W-2, W-8, US-2 |
| 4 | Calibration validity, leakage, coverage | **Questionable / unvalidated** | 9 elections (national) and 4 pairs (seat/λ). There is no look-ahead in the Westminster backfill: every date is after the last calibration election. LOO national cell coverage 85.7% (30/35). LOO seat-component coverage 88/88/91/72%; 66% for 2024 contenders. Seat-total coverage given actual votes: 12/20 cells. A full-pipeline backtest is impossible (no pre-2024 polls). US House national LOO: 5/6. Senate: one usable conditional cycle. President: 3 national cycles. | W-1, W-2, W-3 |
| 5 | Tracker, backfill, Chances history | **Mostly sound; display issues** | Each past point uses that date's window under the single current config (no look-ahead in the inputs). 12 published entries have ≤2 pollsters in the window; one-day hung-probability jumps reach 0.53. The 5-update band smoothing is centred: it uses up to 2 later runs at past dates, and at the latest date it is a 3-run trailing mean. The Westminster history has no config segment boundary. The US labels reconstructed history correctly. | W-5, S-2, S-3 |
| 6 | Monte Carlo error, near-0/1 reporting, seeds | **Engine sound; display over-precise** | 10k draws give SE ≈0.4 pp on 19%. The seed-to-seed SD of P(Lab majority) is 0.33 pp. The display shows 0.01 pp. 24 seats show "win 100%" and 103 show ≥99%. Seeds (date ordinal; US sha256(model:date) with per-source streams) reproduce, and draws are chunk-invariant (replay verified). | S-1 |
| 7 | Generic-ballot budget identified or overfit? | **Not identified; correctly gated** | No US scale is used at runtime: all three releases are disabled, and the Senate/President candidates carry no numeric scales, so they fail closed. House candidate: G from 6 cycles (RMS 2.44, including +1.67 bias). H and R are exactly identified by 2 moments of one transition. Local floor, design effect, cap and population fractions are assumptions. Senate v2: the state/race split is unidentified and decides polled-race variance. | US-1, W-3 |
| 8 | Senate holdovers/caucus/VP/50–50; President EV | **Sound** | 100-member substitution by (state, class). Explicit King/Sanders/Osborn alignment. Holdover alignments cannot leak into contested races. The VP resolves only an exact 50–50. Control and unresolved partition to 1. The 270 threshold is derived from the 538 total. No-majority includes 269–269. Popular vote counts the 51 reference units only. | US-2, US-4 |
| 9 | Anchor interaction, legacy vs v2 | **Sound** | No shipped or gated Senate scale comes from legacy residuals. Legacy zero-origin fits were bias-inflated: 18.5 vs 12.9 centred, +44%. Reproduced: corrected 2022 mean +0.643 vs legacy +12.497; centred mean square (MS) 197.37 vs 196.33. The 70/30 recipe gives zero swing. Segments split through `central_options_id`. No live or published Senate run uses the fix yet. | A-1, US-1 |
| 10 | Cross-chamber national draw | **Deliberately independent; defensible for marginals** | Per-model seeds give independent G per chamber. No joint quantity is published, and the plan explicitly disclaims joint probabilities. | US-3 |
| Extra | Swing-type centre, nation-only parties, district nesting, G target units | Various | λ centred at 0 although all 4 fitted λ̂ > 0. Moving the centre to the historical mean changes central seats by 16–28 for the top two (documented). SNP/Plaid national error is scaled by GB share. ME/NE districts are not nested in the parent residual. | W-7, US-4, US-5 |

## 4. Findings

Priorities:

- **P1:** wrong, or materially misstates published probabilities.
- **P2:** material, or would be material if a gate were lifted.
- **P3:** minor.

Line numbers refer to HEAD plus the working tree.

### P1

#### W-1 [Westminster] Seat-total (vote-to-seat conversion) uncertainty is missing: seat deviations are independent

**Where.** `data/model_support/monte_carlo.py:761` (seat SD `σ_seat·s(p̂)` per seat×party), `:954–955` and `:964` (independent normals per seat, centred per seat), and `:956` (one shared λ). The calibration is pooled as a robust spread of per-cell residuals (`data/model_support/calibration.py:937–947`, `:1147–1172`). No component correlates deviations across seats.

**What is wrong.** National vote error is shared, which avoids the "538 2016" failure for votes. Deviations from regional swing are independent across all 650 seats, so they average out in national seat totals. Historically those deviations are strongly correlated across seats through tactical voting, vote efficiency and party-specific concentration. The λ term is the only shared conversion term, and it cannot span them.

**Evidence: perfect-regional-swing backtest.**

- Method: actual regional result shares are fed to the engine, leaving only seat noise and λ, with leave-one-pair-out parameters. Script: `checks/wm_seat_total_backtest.py`.

| Election pair | Party | Actual seats | Simulated 5/50/95 | Actual percentile |
|-|-|-|-|-|
| 2010→2015 | Con | 330 | 305/315/326 | 99.4th |
| 2010→2015 | Lab | 232 | 237/246/256 | 0.4th |
| 2010→2015 | SNP | 56 | 48/52/55 | outside |
| 2015→2017 | top 5 | | | all inside |
| 2017→2019 | top 5 | | | all inside |
| 2019n→2024 | Lab | 411 | 328/341/354 | 100th |
| 2019n→2024 | Con | 121 | 197/216/234 | 0th |
| 2019n→2024 | LD | 72 | 45/52/59 | outside |
| 2019n→2024 | SNP | 9 | 11/16/20 | outside |
| 2019n→2024 | Others | 7 | 1/2/3 | outside |

- Coverage of the conditional 90% seat-total intervals: 12 of 20 party×pair cells.
- Swing type cannot reproduce 2024 even with actual regional shares (`checks/wm_2024_swing_type.py`):
  - Labour 333 / 346 / 354 at λ = 0 / λ̂ = 0.57 / 1, against an actual 411;
  - Conservatives 223 / 205 / 188, against an actual 121.
- Actual minus simulated median, RMS over the 4 pairs: 36 seats for the eventual leader and 48 for the runner-up. The model's conditional SD is about 6–8 seats.
- Excluding 2024 the RMS is 9–10 seats, still above the model's. 2024 is roughly a tenfold break.

**Impact.**

- The published seat ranges, P(majority), P(largest) and P(hung) carry essentially no conversion uncertainty: Labour 199–359, majority 18.9%, largest 93.9%, hung 81.0%.
- The current five-party configuration (26/23/20/11/11) is the regime where conversion is most sensitive to tactical patterns, as 2024 showed. The published seat and odds outputs are therefore likely overconfident.
- The independent note raised this as a hypothesis. The backtest above demonstrates it.

**Suggested fix.**

1. Add a correlated conversion term, for example per-simulation party×seat-cluster or party "efficiency" shocks loading on seats where the party is competitive. Calibrate it so the seat-total residuals given actual regional votes match the four pairs.
2. Keep this perfect-regional-swing backtest as a regression and acceptance test.
3. Until then, label the seat ranges and odds as excluding tactical/efficiency uncertainty, or widen them.

### P2

#### W-2 [Westminster] Seat-level win probabilities have thin tails and are overconfident in fragmented elections

**Where.** Gaussian seat noise (`monte_carlo.py:954`) uses a MAD-based σ (`calibration.py:947`), so the Gaussian core fits the middle of the residuals but ignores their tails.

**Evidence.**

- Raw seat residuals: plain SD 6.83 vs robust SD 4.90, excess kurtosis 22.6.
- Share of residuals outside each Gaussian band:

| Band | Observed | Nominal |
|-|-|-|
| 90% | 15.0% | 10% |
| 99% | 5.1% | 1% |
| 99.9% | 2.6% | 0.1% |

- Win-probability reliability with perfect regional swing (`checks/wm_seat_reliability.py`):
  - 2010–2019: favourites ≥95% lost 0–0.65% of the time, which is acceptable;
  - 2019n→2024 with production parameters: 28 of 371 lost (7.5%);
  - 2019n→2024 leave-one-pair-out: 34 of 396 lost (8.6%);
  - the "0–5%" bin (mean 0.7%) actually won 6.9–9.1% of the time;
  - mean multiclass Brier 0.32 in 2024 vs 0.08–0.12 earlier.
- Variants (`checks/wm_seat_reliability_variants.py`):
  - t₄ tails cut 2024 favourite losses to 7.3%;
  - λ centred at the historical mean plus t₄ tails cut them to 5.9%;
  - neither reaches nominal, so the 2024 seat variance itself was larger (σ_raw 6.30 vs pooled 4.90).
- The current export shows 24 seats at "win 100%", 103 at ≥99% and 178 at ≥95%.

**Impact.**

- Popup win percentages for safe-looking seats overstate certainty in the current fragmented regime.
- The tail choice also moves national odds. In `checks/wm_param_uncertainty.py`, t₄ seat tails at the same MAD move P(Lab majority) 18.6% → 14.9% and hung 81% → 85%, about 10× the MC error.

**Suggested fix.**

1. Use heavier-tailed seat noise (a fitted t, or a bootstrap of empirical residuals).
2. Make σ_seat depend on the regime or fragmentation, for example weighting 2024, or scaling σ_seat with the effective number of parties.
3. Display probabilities above 99% as ">99%".

#### W-3 [Westminster, also US] Error scales are treated as known (plug-in Gaussian), dropping their estimation uncertainty

**Where.** `monte_carlo.py:939–941`: national raw draws are `σ_nat·s(P̂)·N(0,1)`, with σ_nat a point estimate from 9 elections (`calibration.py:304–336`, `:401+`). The same pattern holds for US G: House `national_margin_sigma` from 6 cycles, President proxy RMS from 3 cycles, applied through `models/us/_common.py:2861–2868`.

**Evidence.**

- LOO national cell coverage of the nominal 90% interval: 85.7% (30/35).
- Leave-one-election-out z of the top-two lead: RMS 1.18, where 1.0 means calibrated (`checks/wm_national_loo.py`).
- Replacing Gaussian national draws with the posterior-predictive t₉ at the same scale (`checks/wm_param_uncertainty.py`):

| Quantity | Gaussian (as built) | t₉ predictive |
|-|-|-|
| Lab P(largest) | 94.0% | 91.8% |
| Ref P(majority) | 0.02% | 0.29% (×14) |
| Ref P(largest) | 5.6% | 6.8% |
| Lab seat 90% range | 201–359 | 191–365 |

- For the US House, 6 cycles imply t₆ (predictive variance ×1.5); for the President, 3 cycles imply t₃.

**Impact.** Underdog and tail probabilities (majorities, control flips) are understated, by up to an order of magnitude near 0 or 1.

**Suggested fix.**

1. Draw σ per simulation from its scaled-inverse-χ² posterior (equivalently, t-distributed shared errors), or use a hierarchical estimate.
2. Report the sensitivity alongside the headline odds.

#### W-4 [Westminster] The headline seat count is not the centre of the published distribution

**Where.** The design decision is the plan's assumption e (the deterministic count is displayed). Intervals and odds come from the Monte Carlo (`monte_carlo.py:1399–1405`, `:1433–1473`). Seat noise is in `:761`.

**Evidence.**

- Latest run: Labour central 300 vs MC mean 281 / median 282 (90% range 199–359); Reform central 109 vs 127 / 122.
- Seat noise alone moves Labour 300 → 286 (mean) and puts 300 at the 95th percentile of that noise-only distribution (`checks/wm_bias.py`).
- Across all 840 stored runs, Labour central minus MC median ranges −21 to +42 seats.
- 37 runs (2024-07-05 to 2024-10-02) have Labour's central outside its own 90% range (`results/wm_central_outside_band.txt`). Most of these are excluded or compressed out of the trend file.

**Impact.**

- Readers see "300 seats" next to "majority 19%". The odds are coherent with the 282 median, not with 300.
- The discrepancy is a genuine property of the noise model, not a coding bias. It also depends on W-1/W-2: with correctly correlated conversion error the shift would differ.

**Suggested fix.**

- Show the MC median or expected seats as the probabilistic point estimate, and label the deterministic count as the uniform-swing projection; or
- explicitly note when the central lies outside, or near the edge of, its range.

#### US-1 [US-Senate] (gated) The Senate state/race allocation is unidentified, and it decides whether polls reduce uncertainty

**Where.** `data/model_support/us_simulation.py:473–474`: national and state sources load the broad mask at full weight for every race, independent of α. Race residuals load (1−α) and the local floor and sampling load α (`:380–395`, `:404–432`). The v2 comparisons fit "state-only" and "race-only" scales at α = 0, for example `reported-including-nota-raw-centered-state/race` with scale ≈15.5.

**Evidence.**

- Every 2026 state has exactly one Senate race, so H and R cannot be separated by Senate data.
- The v2 target rests on one cycle: 2022, 31 singleton races, centred MS 197.4 pt², i.e. a margin SD of about 14 pt.
- The legacy 2024 centred moment for the same field family was 82.4, 2.4× smaller. Cycle-to-cycle variation dominates.
- Consequence of the allocation at runtime:
  - state-only: about 15 pt of margin SD on every polled race (a 10-pt race is close to a coin flip, whatever its polls say);
  - race-only: polls suppress the fallback term by (1−α).

**Impact (if ungated).** Senate control odds would be determined by an arbitrary allocation choice.

**Suggested fix.**

1. Treat the Senate fallback residual as race-level, i.e. let polls inform it.
2. Calibrate polled-race error (the local floor plus sampling) directly from the 24 retrieved final Senate polling averages against results.
3. Import H from the House state component or from presidential state errors, not from Senate fallback residuals.
4. Require at least two conditional cycles before acceptance.

#### US-2 [US-President, US-Senate] (gated) No inter-state correlation beyond the national factor

**Where.** `us_simulation.py:456–476` creates only `national` and per-state `state:{XX}` sources. For President and Senate units, H and R are both per state, so states are independent given G.

**Evidence.** Final state-average margin errors, poll minus result, from `president-comparison-inputs.json` (`checks/us_president_state_errors.py`):

| Cycle | States | Mean state error | SD about mean | Positive cluster | Negative cluster |
|-|-|-|-|-|-|
| 2016 | 25 | +1.1 | 4.3 | IN +8.5, WI +7.3, IA +6.4, OH +5.9, MI +3.8, PA +2.8 | CA −7.8, IL −5.6, NV −3.2, NM −3.2, OR −3.0 |
| 2020 | 15 | +1.9 | 3.3 | OH +7.0, IA +6.2, WI +6.1, FL/TX +4.3 | MN −2.8, MD −3.9 |
| 2024 | 16 | +1.8 | 1.5 | | |

Errors are clustered regionally and demographically, not independent.

**Impact (if ungated).** A correlated swing-state miss becomes improbable, so EV distributions and Senate control odds come out overconfident. This is the 2016 failure mode at state level.

**Suggested fix.**

1. Add regional and demographic factors, for example a low-rank correlation from state demographics or past swings, in the style of the Economist and 538.
2. Calibrate them on these state errors.
3. Validate the EV distribution by whole-cycle holdout.

### P3

#### W-5 [Westminster] Sparse early windows publish volatile, overconfident odds

The national error scale is constant whatever the window's pollster count (`monte_carlo.py:736`), so a window of one to three polls inherits the precision calibrated on 5–19 final polls. Of 440 published trend entries:

- 11 have ≤3 polls in the 30-day window;
- 12 have ≤2 distinct pollsters, including single-poll windows;
- one-day hung-probability jumps reach 0.53 (2024-11-18), 0.47 (2024-11-07) and 0.33 (2024-11-04) (`results/wm_window_density.txt`).

**Fix.** Add a house-effect term σ_h²·Σ_p W_p², or suppress or widen odds below about 3 effective pollsters.

#### W-6 [Westminster] House effects are absent from the sampling variance

V treats polls as independent samples, which is correct for sampling (`data/model_support/uncertainty.py:408–452`; `westminster_uncertainty.py:104–130`). In the 2026-10-22 window there are 15 polls from 6 pollsters, 4.8 of them effective. Find Out Now holds 28% of the weight.

Pollster-mean dispersion implies a house-effect SD for the deployed average of 0.57 (Lab), 0.92 (Ref), 0.70 (Con), 0.45 (LD) and 1.00 (Grn) pt, against national sampling of 0.18 (`results/wm_house_effects.txt`). σ_nat covers house effects only as averaged over 5–19 equally weighted final pollsters. The excess is roughly 5–9% of national variance.

**Fix.** Same term as W-5.

#### W-7 [Westminster] Nation-only parties get a national error scaled by GB share

`monte_carlo.py:736` scales the SNP's national error by s(2.1%), giving about 0.9 pt raw. In Scotland, where the SNP polls about 30%, its total polling SD is about 1.6 pt, against about 2.75 for a GB party of the same Scottish share. The trade-off is documented in the docstring at `:54–57`. Historically SNP Westminster polling errors were 2–5 pt (2017, 2024).

**Fix.** For nation-only parties, scale the national term by the regional share, or add a nation-level error.

#### W-8 [Westminster] The rival-lead variance is not calibrated

Calibration matches per-party marginal errors. The lead, which drives P(largest), inherits whatever correlation centring induces:

- now: ρ(Lab,Ref) = −0.35 and lead SD 3.85 pt;
- at historical two-party shares: ρ ≈ −0.5, close to history's −0.53 (centred) and −0.63 (zero-origin).

In 2024 the realised lead error was 7.2 pt, z = 1.75 under the model. The LOO lead z has RMS 1.18.

**Fix.** Add a contrast (lead) moment to the calibration, or estimate the error covariance from history.

#### S-1 [Shared] Displayed probability precision exceeds Monte Carlo precision

`electionmapslogic/uncertainty.js:86–87` and `:98` print 2 decimals of a percent; `public_uncertainty.py:28` keeps 4 dp.

- At 10k draws the SE of 18.9% is 0.39 pp, and the seed-to-seed SD is 0.33 pp.
- "win 100%" means 0 of 10,000 Gaussian draws lost, which W-2's tail evidence does not support.
- Chances lines carry about 0.3–0.5 pp of independent date-to-date MC jitter.

**Fix.** Show integer percentages, or 1 dp at most. Show "<1%" and ">99%". Consider common random numbers across dates.

#### S-2 [Shared tracker] Band smoothing looks ahead and lags

`electionmapslogic/features/polltracker-uncertainty.js:5`, `:90–110`: the centred 5-update window uses up to two later runs at past dates, and the latest band is the mean of the last 3 runs. The line itself is unsmoothed, so the band can detach from it (the fade anchors to the edge). Tooltips stay exact.

**Fix.** Use a trailing window, or remove MC jitter at source with common random numbers and drop smoothing.

#### S-3 [Westminster tracker] No segment boundary on config change

US trend `segment_id` includes kernel, config and calibration (`data/model_support/trends.py:548–553`). The Westminster trend compresses only on equal central results (`:329–335`).

All 840 runs currently share config hash `730a3349d3836bc6`. A future recalibration without a full rerun would join incompatible bands and odds.

**Fix.** Break Westminster segments on `config_hash` or kernel change.

#### US-3 [US] The national error is independent across chambers

Seeds are `sha256("us-house:…")` and `sha256("us-senate:…")` (`us_simulation.py:176–180`), so G differs between the House and Senate simulations for the same date and generic-ballot input. Marginal odds are unaffected, and the plan disclaims joint outputs.

**Fix.** Seed the shared G stream by date only, or build a joint simulation. Same-date odds then move coherently, and a later joint-control probability becomes possible.

#### US-4 [US-President] (gated) ME/NE districts are not nested in the statewide residual

`us_simulation.py:370–376`: a district inherits its parent's residual graph only when the parent is blended (polled). Otherwise district and statewide race residuals are independent, sharing only H and G. With the House candidate scales, the correlation is about H²/(H²+R²) ≈ 0.17. That permits incoherent combinations, such as both districts D while the state goes R.

**Fix.** Always load the parent residual on its children, with an additional district-specific term.

#### US-5 [US-House] (gated) G is calibrated on input error, not output error

`national_margin_sigma` (2.44) is the raw generic-ballot input margin error. The engine's G-only national output margin SD is 2.29 (house-report probes), about 6% lower.

**Fix.** Calibrate on the error of the deployed estimator's projected national vote, or document the shrinkage.

#### A-1 [Anchor] (operational) The fixed Senate estimator is not yet applied to any live or published run

The live DB has 113 legacy `us_senate_model` runs (latest 2026-10-19) and no `model_context` table. `uselectionmaps/data/results/us-senate-forecast.json` comes from those runs.

For the 2026 Class-2 field, the legacy subtractor is the pooled 2020 Senate field, D−R −2.26, against the accepted 2020 House anchor of +3.09. The legacy fallback therefore shifted unpolled races about +5.4 pt toward D on the margin before renormalisation, scaled by (1−α) for polled races (`results/us_senate_2026_legacy_shift.txt`).

This is a central-projection issue rather than an uncertainty defect. The first anchored rerun will move central Senate results. Its trend correctly starts a new segment, because `central_options_id` includes `senate_house_anchor`.

A minor residue: the "regional own-poll" branch (`_common.py:2431–2439`) still subtracts the Senate-field regional baseline. No US regional polls exist today.

#### Light correctness check of 087afcfc, 134d1fc4 and the uncommitted piece

No defects found. The anchor review covered line level. The methodological checks reproduce:

- `senate_fallback_swings` gives zero swing on the 70/30-with-unchanged-50/50 recipe, and distinct deltas for mixed-year races in one region;
- `build_reference` gives a corrected 2022 mean of +0.6433 (n = 31), legacy +12.4969, corrected raw-poll mean +0.8322, centred MS 197.37 vs 196.33 (`results/us_senate_anchor_check.txt`);
- excluding the whole 2024 field (2018 anchor unavailable) is the conservative choice, and its cost is US-1's single-cycle identification;
- 134d1fc4 is presentation only.

## 5. Known and documented limitations

These are already acknowledged in the plans and reports. They are not raised as findings.

- **λ centred at 0.** All four λ̂ are positive (0.24, 0.44, 0.12, 0.57; mean 0.34), and the mean is reported but not applied (user decision). Magnitude now: central seats at λ = 0.34 would be Lab 284, Ref 137, Con 98, against 300 / 109 / 113 at λ = 0 (`results/wm_lambda_centre.txt`).
- **σ_reg = 1.5 is a guess.** Only four pairs inform σ_λ.
- **Nowcast framing.** There is no time-to-election drift. Calibration uses final-poll averages, whereas the runtime uses a 30-day decayed average.
- **Westminster seat-level.** Sinn Féin abstention is not modelled (majority 326). NI is held at baseline plus seat noise.
- **No full-pipeline backtest.** Historical polls before July 2024 are not stored.
- **US House.**
  - The 2026 target geography is unresolved.
  - The local floor equals the race scale by assumption; design effect 2, cap 2000 and population fractions of 1 are assumptions.
  - Gaussian tails, including NJ-08 at 6.5σ.
  - The +1.67 signed national bias is treated as zero-mean RMS, and the central is not bias-corrected.
  - The RCP final average is a proxy for the deployed multi-provider blend.
  - H and R come from one 2022→2024 transition (315 districts).
- **US Senate.**
  - The generic-ballot exposure of Senate races (loading 1) is an assumption.
  - The 2018 anchor is unavailable, which removes 2024.
  - Transport of vote stage and population (final-RCV vs published-general) is unaccepted.
  - Structural candidate zeros remain.
- **US President.** The national total-error proxy comes from 3 cycles with no extra national SRS. Allocation and baseline provenance are unaccepted. District transfer and geometry are open.
- **Cross-model.** No joint House/Senate/President probability is offered.
- **Westminster review-era items, already fixed.** The 2026-10-14 and 2026-10-15 sampling-variance, exactness-flag and backfill items.

## 6. Numerical checks run

All checks are in `/tmp/claude-1000/-home-philiph-workspace-budget/dc112de6-75df-446b-9fff-9cee7609ee47/scratchpad/checks/`, with outputs in `.../checks/results/`.

They imported a lean copy of HEAD plus the uncommitted `data/` sources at `.../scratchpad/audit_src` (`REPO=` environment variable), with no bytecode writes. The DB was opened only as `file:/home/philiph/dbs/elections.db?mode=ro`. Westminster engine checks replay the exact saved inputs and config of the 2026-10-22 run from `westminster-latest.npz`.

| Script | What it checks | Key result |
|-|-|-|
| `wm_bias.py` | MC mean/median vs deterministic central, by component | Vote bias ≤0.15 pt; Lab seats 300 vs 282 median; seat noise alone 300 → 286 |
| `wm_components.py` | National-vote SD by error component; rival correlation | National 2.28, regional systematic 0.42, sampling 0.18, seat 0.23, λ 0.20 pt; ρ(Lab,Ref) −0.35; lead SD 3.85 |
| `wm_national_loo.py` | Leave-one-election-out σ_nat coverage; lead z; historical correlation | 30/35 = 85.7%; lead-z RMS 1.18; historical corr −0.63 / −0.53 |
| `wm_seat_coverage.py` | Seat residual tails; leave-one-pair-out seat+λ coverage | Kurtosis 22.6; 2.6% beyond Gaussian 99.9%; LOO 88.6/87.9/91.0/72.4% |
| `wm_seat_reliability.py` | Perfect-regional-swing win-probability reliability (LOO and production) | 2024: ≥95% favourites lost 7.5–8.6% |
| `wm_seat_reliability_variants.py` | λ mean / t₄-tail variants for 2015→17 and 2019n→24 | Tails help but do not fix 2024 |
| `wm_seat_total_backtest.py` | Perfect-regional-swing national seat totals vs actual | 12/20 within 90%; 2024 Lab/Con 57/76 seats outside the range |
| `wm_2024_swing_type.py` | Seat totals from actual regional shares at λ = 0 / λ̂ / 1 | Lab 333 / 346 / 354 vs actual 411 |
| `wm_param_uncertainty.py` | t₉ predictive national error, t₄ seat tails, MC SE, seed spread | Ref P(maj) 0.02% → 0.29%; P(Lab maj) 18.6% → 14.9% with t₄; seed SD 0.33 pp |
| `wm_house_effects.py` | Pollster concentration and house-effect SD in the 30-day window | 4.8 effective pollsters; 0.45–1.00 pt house-effect SD |
| `wm_window_density.txt` (inline query) | Window density of published trend entries; odds jumps | 12 entries ≤2 pollsters; jumps up to 0.53 |
| `wm_central_outside_band.txt` (inline query) | Stored runs with central outside the 90% seat range | 37 runs; Labour central − median −21…+42 |
| `wm_lambda_centre.py` | Central seats at alternative λ centres | 0.34 → Lab 284 / Ref 137 / Con 98 |
| `us_house_residual_structure.py` | US House 2022→24 residual structure beyond state | Margin-structure R² 0.07; between 4.8 / within 13.6 |
| `us_president_state_errors.py` | Regional clustering of state polling errors | 2016 / 2020 clustered by region; SD 4.3 / 3.3 |
| `us_senate_anchor_check.py` | Anchor recipe; mixed-year deltas; v2 signed means without MC | +0.6433 vs +12.4969; MS 197.37 vs 196.33 |
| `us_senate_2026_legacy_shift.py` | Size of the legacy cohort shift for the 2026 field | ≈ +5.4 pt D−R |
| `load_latest.py` | Loads the saved replay snapshot | n/a |

Not run, by design: the repo test suite, builds, and any DB write or live model run. Full Monte Carlo regeneration of the v2 Senate report was not repeated: the deterministic references were reproduced, and the byte-identical regeneration is documented by the implementer.

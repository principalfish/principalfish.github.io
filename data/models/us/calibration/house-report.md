# House uncertainty calibration candidate

**Not accepted. No stored or public odds are enabled by this report.**

National raw input-margin RMS: 2.4381 points; signed bias: +1.6711; demeaned sample SD: 1.9447.
The zero-mean source matches RMS about unchanged central predictions. It does not assume that measured bias is zero or alter the central result.

The full 433-district measured 2022 baseline defines source shapes. 315 eligible districts in 43 states supply one-transition moments.
Actual raw national D/R level changes are passed through the existing masks, Others policy, clamping and normalization. G is zero during H/R fitting.

|Coefficient|Candidate|Status / method|
|-|-|-|
|national_margin_sigma|2.4381|fitted proxy: Equal-election RMS about unchanged central; 6 RCP/official joins|
|national_reference_source_scale|2.4587|derived reference only: target / sqrt(q(full measured 2022 baseline)); recompute for each frozen run|
|state_scale|1.8653|jointly fitted conditional candidate: 315 stable districts, one transition, zero-origin B0 and within W through kernel|
|race_scale|4.1824|jointly fitted conditional candidate: Joint H/R inversion conditional on observed raw national D/R changes; no G double-counting|
|local_scale|4.1824|unidentified assumption proposed for approval: 1× fitted fallback race scale; sensitivity 0/0.5/1/2×; not independent House poll-error fit|
|design_effect|2|unidentified assumption proposed for approval: Sensitivity 1/2/3; reported N is not automatically effective LV N|
|n_eff_cap|2000|unidentified assumption proposed for approval: Sensitivity 1000/2000/100000; no invented N for missing records|
|population_fractions|1|unidentified assumption proposed for approval: LV/RV/A/unknown all 1; sensitivity 1/.75/.5 in addition to observed named-candidate fraction|
|tail_family|Gaussian|unidentified structural assumption: Existing versioned kernel; untrimmed extreme residuals retained; no fitted t degrees of freedom|

H/R recovery uses a separate seed and the actual nonlinear kernel. Its tolerance combines four Monte Carlo standard errors from fitting and recovery, plus 1% numerical allowance. It is a simulation-unit check, not an independent election validation.
Recovery compatible: **True**. Targets W/B: {'within_W': 15.738584020965213, 'between_B': 5.391212176950565}; recovered: [15.728891814988767, 5.409210527971297].

The largest retained residual, NJ-08, is -25.58 points against a fitted conditional output SD of 3.93: 6.51 SDs. This extreme is poorly represented by the Gaussian family. The 92.06% in-sample 90% interval coverage and successful moment recovery do not validate rare-event tails; no outlier was trimmed from the primary fit.

The national holdout omits entire elections; six primary observations provide limited evidence. District sensitivities retain full baseline shapes while changing the scored subset. NJ-08 remains in the primary fit. The largest-outlier exclusion is influence analysis only.

Local polling floor, DEFF, population adjustment and cap are labelled assumptions. Synthetic N 500/1000/2000, alpha 0/.5/1 and separate assumption axes appear in the JSON and console. They never repair actual missing N. Poll-rich scenarios can be wider when their individual samples are noisy.

All source URLs/hashes, candidate classifications and row/page references are preserved in house-inputs.json, the committed 2024 audit, and the full house-report.json. No scratch-directory dependency or live database is required.

## Limits requiring explicit acceptance

- RCP final aggregate is a proxy; the deployed multi-provider blend/lookback is not reconstructed.
- The local House poll error floor, design effects, population fractions, cap and tail family are not fitted by these observations.
- 315 districts share one transition; they are not 315 independent elections.
- Final-RCV counts are a model counting convention, not first-choice national preference.
- 2018 includes uncertified NC09 returns and is sensitivity-only.
- Alaska 2024 primary final count is indexed official-PDF extraction, not a downloaded/hash-verified PDF.
- Production 2026 House odds remain gated until current 2024 map/baseline alignment with 2026 contests is resolved; this fit changes neither geography nor central results.
- Independent fallback/local residuals and the transfer of fallback scale to a local polling floor are assumptions, not jointly identified from matched House polls.
- Repeated signed national bias is represented approximately by symmetric zero-mean RMS; central predictions are not bias-corrected.
- B0 allocates selected-panel offset to state uncertainty; centered between-state sensitivity is shown separately.
- Matching two second moments and in-sample coverage does not validate 1% tails or election-control odds.
- National source-shape transfer determines unfitted third-party contrasts; raw D+R mass errors are not identified by D-R matching.
- Senate and President calibration remain pending; House coefficients are not transferable defaults.

See the full JSON for raw versus decided, uncertified 2018, RCV, centering, untrimmed outlier and independent-recovery diagnostics.

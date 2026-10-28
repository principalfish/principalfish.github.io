# Senate matching-year anchor comparison: review proposal

Status: **unaccepted**. This proposal changes no calibration values, release
settings or saved forecasts. Source-policy acceptance is separate from accepting
any numerical calibration. No state/race allocation is nominated for production.

The estimator is `senate-house-anchor-v1`; the comparison version is
`us-senate-house-anchor-comparisons-v2`. Both official-input and raw-poll references
call the same `senate_fallback_swings` helper as production. Each race keeps its
own Senate candidate-party baseline and uses the matching-year House baseline
for its fallback delta. Baseline years come from source metadata: 2022 regulars
use 2016, and the Oklahoma special uses 2020. The two Oklahoma slots share a
state but have distinct anchors. Senate field aggregation remains solely the
stochastic composition reference; it no longer subtracts from House polling in
the corrected deterministic reference.

## Findings

These are observed-minus-projected D−R margin points for 2022's 31 eligible
singleton targets. All 35 contested slots remain in the stochastic field.
Reported-total and candidate-vote denominator alternatives remain separate; no
default is selected. The table uses reported totals, including NOTA.

|Contrast|Legacy official-input signed mean|Corrected official-input signed mean|Corrected raw-poll signed mean|Corrected centered conditional second moment|
|-|-|-|-|-|
|Raw|12.496944|0.643275|0.832158|197.367184|
|Decided D/R|13.786474|1.208175|1.528081|144.023048|

The corrected reference substantially reduces the signed mean. Large centered
residual variation remains, so a smaller bias does not establish a calibrated
uncertainty model or remove candidate-specific error. Centered fits subtract
each cycle's residual mean only when forming the target moment; central point
predictions receive no fitted bias correction.

All 16 conditional state-only/race-only, centered/zero-origin, raw/decided and
NOTA sensitivity fits recover their target moments within the stated Monte Carlo
tolerances at 2,048 fitting draws and 8,192 independent recovery draws. These
kernel scales are not empirical margin standard deviations. State-only and
race-only boundaries allocate the same combined residual error differently and
remain unidentified. Full scales, target moments and recovery errors are in the
versioned JSON/Markdown reports.

## Availability and remaining decisions

- The accepted 2018 policy leaves its House anchor unavailable because NC-09
  was uncertified. No 2019 substitute or qualified proxy is admitted. All 34
  races in the 2024 conditional field are excluded together; the Nebraska special
  has a separately available 2020 anchor but is not used to manufacture a partial
  field. Every slot, baseline, structural issue and reason remains in the JSON
  availability ledger. 2024 anchor evidence also remains unavailable for future
  baseline use and is not needed for these historical target races.
- Only the complete 2022 conditional cycle is available. Its leave-one-cycle-out
  fits have no training election and report unavailable scales, target moments
  and conditional intervals. No scale-transfer or deployable coverage validation
  is claimed. There is no fabricated zero variance.
- National House error facts remain the original qualified final-RCV/no-nominal
  proxy series. National fitting still includes 2012, 2014, 2016, 2020, 2022 and
  2024, independently of conditional Senate availability; 2018 remains excluded.
  Independent national holdout diagnostics retain all six admitted cycles. Raw
  signed mean is +1.671108 points and RMS is 2.438083 points. These are total
  average-error proxies, not identified pure systematic coefficients.
- Official current-national counterfactual levels retain those same original
  final-RCV proxy facts. Baseline anchors use accepted published-general measured
  counts, retaining Other in the denominator, fusion treatment, unmeasured
  uncontested races and the NY-14 +30 Republican/+30 denominator correction.
  Matching election years does not reconcile vote stages or measured populations.
  Transport remains an unaccepted assumption, including the 2022 Alaska
  general/tabulator discrepancy and the original 2024 verification limits.
- Baseline party masks, structural candidate zeros, observed winning parties
  unreachable without own polls, same-state singleton exclusions, minor-party
  aliases and RCV limitations are unchanged and visible. The anchor does not fix
  them. Local averages retain their original candidate joins and total-error
  diagnostics; no historical sampling N, blend alpha, local floor, design effect
  or district transfer is invented. Post-blend G mapping, state/race allocation,
  tails, turnout and early held-today performance require separate review.

## Identities and reproduction

The original Senate input and legacy report files remain byte-identical. President
JSON and Markdown regenerated at default draws are also byte-identical to their
committed originals. House generation is unchanged.

|Identity|SHA256|
|-|-|
|Original Senate input bytes|5708f8de432c1325c765a38aedc6d76f91cd6d6ba9d5c1811cc839c7391f7cb1|
|House anchor candidate catalogue|e20765425615bdc43a2e063f0ab260ca6f00e2ebd27bff6e1be9101dabe50757|
|Accepted anchor source policy|6568de70b4317be92b58a7450de2d4d262e397989e5364cb8a8447bfbd4e71ee|
|Corrected reference identity|0216619d980848443cf282d3168d4ff32d92c831c1c9cb5221038d240aef767e|
|senate-comparison-report-house-anchor-v2.json|ccffea86c2d3c40d464d5a3000cdff9aa4eac40107e2cf47fa65ccc59f6c0a02|
|senate-comparison-report-house-anchor-v2.md|42e4ba461a974b36a46eb0ed7ff598f1cdc0158407596a508d250ebf9a60026f|

From an isolated source tree without `.env`, use the repository's data virtualenv
and run from `data/`:

```bash
./election_data/bin/python scripts/calibrate_us_uncertainty.py --model senate --output-dir .pytest_cache/senate-anchor-first
./election_data/bin/python scripts/calibrate_us_uncertainty.py --model senate --output-dir .pytest_cache/senate-anchor-second
```

Both JSON and Markdown were generated twice at default draws and compared
byte-for-byte before these versioned artifacts were copied into the repository.
The command is offline and does not open a database. Omitting `--output-dir`
prints Markdown and writes nothing. Senate output always uses the versioned
filenames; the original `senate-comparison-report.json/.md` files are preserved.
The console defaults to the corrected report and offers an explicitly labelled
legacy comparison via `?reference=legacy`.

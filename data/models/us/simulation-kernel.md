# US simulation mechanics

The House adapter freezes the same selected central estimator used by the
ordinary deterministic runner. Central values are that estimator's result;
they are never Monte Carlo means or medians. `freeze_house_central_inputs`
checks every zero-error recorded vote count and winner flag, in the existing
party and district order. Central-only verification does not require usable
polling N. Building stochastic inputs separately validates every actual
contributor and stops with repair details when provenance or required N is
unresolved.

`model_support.us_simulation` is currently a scratch-only numerical API. It
saves no model elections, draw files, trends, static exports or public odds.
`UsErrorConfig` requires a label for provisional assumptions. These settings
are not accepted calibration, and the read-only console diagnostic at
`/us/house/uncertainty/budget?as_of=2026-10-19` uses sampling-only assumptions,
with zero systematic scales. The ordinary model runner remains deterministic.

For a covariance source with party proportions `t`, the Gaussian error draw is
`scale * (sqrt(t) * z - t * sum(sqrt(t) * z))`. Its covariance is
`scale² * (diag(t) - t tᵀ)`. At 50/50 each party's SD is half the scale; the
SD of their margin is the full scale. Calibration must use these engine units
and verify the realized spread after masks, clamping and normalization.

Each admitted individual local poll gets a sampling source with scale
`100/sqrt(n_eff)`. The effective base is the reported N multiplied by the
named-candidate fraction (capped at one for rounded columns), an explicit
population adjustment, divided by an explicit design effect, and subject to
an explicit cap. Reported N is not automatically the effective likely-voter
base. LV, RV, adults and unknown population adjustments are separate settings.
Defaults of one are labelled scratch assumptions, not empirically validated
precision. The selected raw shares and their decided normalization are frozen.
Polls retain their identity, dates, matchup, reported population and source.

Local average errors use each party's actual poll-weight denominator, while
alpha uses the total seat weight. A party absent from one admitted poll is not
silently assigned zero in that poll's average. The local floor and sampling
enter with alpha; baseline-extrapolation residuals enter with `1-alpha`.
The existing Others branch keeps its fallback when Others is absent from
local polls. Parent polling errors are reused through the existing inheritance
graph and child coefficient products, with no duplicate national/state term.

The national source is drawn once per simulation; state sources follow actual
states, rather than the database's Census-division regions. National and state
factors enter once. Baseline support and own-poll entrant rules remain the
central model's rules. A single-party baseline is labelled as a data diagnostic;
it does not establish that the race is uncontested, and a genuine local entrant
can still receive sampling/local errors. Generic-ballot aggregates are checked
for provenance but have no invented N, independent snapshot sampling error or
shrinking variance from the number of snapshots. Their total estimator error
belongs in the national factor.

Each source has its own stream derived from the model/date seed and source ID.
Changing chunk size leaves draws and outcomes unchanged. Projection uses the
existing clamp-to-zero, all-zero baseline fallback, Python-compatible ordered
sum, normalization, count rounding, and first-maximum winner selection.
National budgets preserve shared-source covariance rather than treating
seats as independent. Budget SDs are pre-clamp references, not outcome ranges.

Persistence, accepted calibration, posterior/result summaries and public
publication are separate subsequent stages. The existing Westminster engine
and schema are unchanged.

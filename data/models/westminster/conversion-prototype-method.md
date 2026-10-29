# Offline conversion experiment

This experiment tests the existing nation × three baseline-share-band cluster
proposal together with Student-t seat errors and predictive lambda uncertainty.
It does not change the runtime model, its configuration, saved simulations or
release status. These elections have already informed model design: results are
retrospective diagnostics, not untouched external validation.

Execution checkpoints preserve completed recovery, fitting and evaluation
stages through atomic writes. Reuse requires matching source, configuration,
NumPy version, experiment settings and stage input identities. Checkpoints are
scratch state, separate from the complete report. An exact cache of latent
random draws within each profile avoids repeating random-number generation;
the draw order, arithmetic and statistical contract are unchanged.

## Comparison and inputs

Report all eight combinations of cluster off/on, Gaussian/Student-t independent
seat errors and Gaussian/predictive lambda. Keep the four existing same-boundary
transitions, party alignment, Speaker exclusions, equal transition weights and
zero-centred lambda. Derive each party's bands from its own earlier-election
shares within each nation, keeping ties together. Map nations explicitly.

Actual regional swings are central inputs. Simulated regional vote totals are
not constrained to match those inputs. Report aggregate drift and noise-induced
bias separately. Regional balancing, regime scaling, a cluster-scale prior and
new historical transitions are outside this experiment.

All fitted quantities in a held-out fold use only its three training
transitions. Preserve source/input identities and test holdout exclusion.

## Training fit

Use a joint, simulation-based minimum-distance fit. This is not a latent-variable
maximum-likelihood fit. Marginal historical tails contain several components;
they cannot be treated as independent Student-t seat errors after adding shared
noise.

For each real or synthetic training election, fit lambda with the existing
least-squares lever and fixed eligible cells. For synthetic elections, apply
clamping, normalization and fallback first, then refit lambda. Scale the resulting
residuals by the lambda-zero share-scale function. Project the full cluster
loadings through the same nuisance fit and residual scaling. Party centring must
include the different cluster memberships of all parties, not just marginal
centring corrections.

Fit five summaries: total residual second moment, the full-loading cluster
quadratic moment, and the 50th, 90th and 99th percentiles of absolute residuals.
For each transition, compare observed summaries to forward-simulated expected
summaries. The objective is the equal-transition mean of the sum of five squared,
standardized differences.

Use common random numbers and 128 training simulations per candidate, then
repeat fits with 256 to check Monte Carlo stability. Hold the reference weights
fixed across variants within a fold: 256 simulations with the training-pooled
Gaussian seat MAD, zero cluster scale and Gaussian lambda RMS. Standardize each
summary by the maximum of its reference draw SD, 0.001, and 5% of its reference
mean's absolute value. These are estimator weights, not standard errors or a
claim that summaries are independent.

Profile seat degrees of freedom over 2.25, 2.5, 3, 4, 6, 10, 30 and the Gaussian
endpoint. Fit seat and cluster scales using bounded finite-difference
Gauss–Newton, from zero to 16 percentage points, with at most 96 unique objective
evaluations per profile. Start from the training MAD with cluster scale equal to
the smaller of four points and half that MAD; also test zero clusters. Use
finite-difference steps `max(0.005, 0.01 * max(1, abs(scale)))`, central differences
inside bounds and one-sided differences at bounds. Solve with least-squares
`rcond=1e-8`, limit proposed steps to two points in maximum norm, and backtrack
by factors 1, 1/2, 1/4, 1/8, 1/16 and 1/32.

Convergence requires projected-gradient maximum norm at most 0.001, or an
accepted parameter step at most 0.005 with relative objective improvement at
most 1e-6. If backtracking finds no improving step, explicitly probe bounded axis and
diagonal neighbours at distance 0.005, regardless of the rejected Gauss–Newton
proposal size. Only if none
improves beyond the same objective tolerance can this count as resolution
stationarity; it is not a claim of gradient convergence. Record the reason, gradients, steps, active limits and flat profiles.
These are numerical search limits, not a probability prior. Do not treat an
unresolved numerical limit as a valid fit or increase model breadth after
scoring. The initial coarse coordinate search missed a better known synthetic
point; the recovery regression below must detect that error before historical
scoring.

The initial stability guards compare 128- and 256-draw fits: component variance
fractions must differ by at most 0.10; scale differences must be at most the larger
of 0.25 percentage points and 20% of the larger scale. Report degrees-of-freedom
profiles and predictively equivalent fits rather than demanding exact recovery
of a discrete tail parameter. Near-optimal discrete profiles (within one objective unit) are also checked for
materially different variance allocation or forward summaries. One objective
unit is a diagnostic cutoff, not a likelihood confidence interval. These guards
are diagnostics, not proof that the variance components are identified.

## Lambda overlap

Use the approved zero-mean construction: the scale is the RMS of training lambda
estimates; predictive degrees of freedom equal their count (three inside each
four-transition holdout). Do not variance-normalize away the predictive widening.

Seat and cluster errors also perturb fitted lambda. Report their analytic
contribution to its estimation variance and its fraction of the observed mean
squared training estimates. A fraction of 25% is a declared warning guard for
unresolved attribution, not evidence that smaller fractions are harmless. Do not
silently subtract this variance or introduce a hierarchical prior. A material
overlap requires a separate statistical decision before production integration.

## Evidence gates

Test synthetic known-component recovery and off-component agreement with the
existing projection before historical scoring. Check centring, clamp/fallback,
zero-scale identity, deterministic parity, seed/chunk invariance and covariance
allocation. Distinguish genuine zero components or a Gaussian endpoint from an
unidentified fit.

The fixed recovery seed is 821744. Use two three-transition synthetic scenarios,
each with 384 seats per transition: seat core 1.5, cluster zero, Gaussian seats
and Gaussian lambda scale 0.3; and seat core 1.5, cluster 1.0, seat degrees of
freedom four and predictive lambda scale 0.3 with three degrees of freedom.
Compare 128- and 256-draw fits. The declared scale-recovery guards are the larger
of 0.25 points or 40% of the known seat core, and the larger of 0.35 points or 40%
of the known cluster scale. The fitted objective must be no more than five units worse than the known
feasible point: a worse result signals optimizer regret. A fitted point may
legitimately improve on the generating point for a finite sample. Report both
scores without interpreting their difference as a confidence interval. Use the
known point with its lambda scale replaced by the training RMS to avoid
claiming population-scale recovery from three draws. Optimizer convergence and
Monte Carlo stability are also required. These are heuristic diagnostics on two
fixed realizations, not confidence coverage or proof of identification. Kernel
parity additionally uses the frozen aggregate fixture and records its identity.

Use at least 6,000 independent evaluation simulations per fold, separate from
fitting/reference streams, and report every variant and fold. Preserve the
17/20 requirement for 90% seat-total coverage. Compare leader/runner-up
standardized RMS errors with a predictive reference that retains their
within-election dependence, and report favourite reliability, residual tails,
aggregate drift and the existing vote-mean tolerance. Fix the reference rule
before scoring. Retain Monte Carlo precision diagnostics rather than selecting
a favourable seed.

No numerical coverage result overrides failed recovery, unstable fits,
unresolved variance allocation, excessive drift or disagreement with the actual
projection. A staged approximation can be useful evidence but cannot clear the
production gate. If the joint candidate fails, leave conversion uncertainty
unresolved and stop before engine/archive changes. Do not lower acceptance or
postpone this failure to final recalibration.

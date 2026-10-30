# Poll-error uncertainty for Westminster and US forecasts

Add honest uncertainty to the election projections. Poll sample sizes and bases feed an effective-n and margin of error. A Monte Carlo engine, calibrated against historical poll errors, turns them into seat ranges and outcome probabilities, shown in the console, on the maps and as bands on the poll trackers.

Westminster is built. The US House, Senate and presidential models have the same machinery, but their release is gated on calibration being accepted.

The remaining work comes from a review of the branch. Fix how vote shares convert to seats under correlated error, add heavier seat-level tails and parameter uncertainty, and correct several US model details. Then rerun and publish the backfilled history, only with explicit approval.

The work lives on branch `poll-errors-simulation`.

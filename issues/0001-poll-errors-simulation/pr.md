# feat: add Monte Carlo poll-error uncertainty to Westminster forecasts

Adds honest uncertainty to the Westminster projection. Poll sample sizes and bases feed an effective n and a margin of error. A Monte Carlo engine, calibrated against historical poll errors, turns them into seat ranges and outcome probabilities. These show in the console, on the map (the simulated median seat count as the headline, with the uniform-swing count kept and flagged when it falls outside its own 90% range), and as bands on the poll tracker. A reproducible validation harness checks the shipped model against frozen inputs.

Westminster only. The US uncertainty work moved to its own branch, and the model refinements the branch review called for (correlated conversion error, heavier seat tails, parameter uncertainty) are left for a follow-up.

## Progress

- [x] Westminster Monte Carlo engine, calibration, storage, console, export, map and tracker bands
- [x] Remove the US work from this branch (`5a96e52c`)
- [x] Remove the offline conversion experiment from this branch (`39610fb9`)
- [x] Regenerate the Westminster validation report (`2618c0dd`)
- [x] Westminster display: simulated median headline, whole-percent odds (`860b0d85`)
- [x] Style cleanup (`000fbd9f`)
- [x] Branch self-review and fixes (`eeee25d0`, `a832137d`)
- [x] Merge master in (`af4405fb`)
- [x] Westminster poll weighting: 15-day half-life over a 60-day window (`a5cdf8e0`)
- [x] Westminster backfill and export: 848 nowcasts rerun with uncertainty (`51c31966`)
- [x] Main headline leads with the simulated outcome (`c7a85eab`)
- [x] Second self-review fixes; tracker starts on the 2024 election-day result (`ae61f984`, `b2c33428`)
- [ ] Final PR description

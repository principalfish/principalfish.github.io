# Poll-error uncertainty for Westminster and US forecasts

Add honest uncertainty to the election projections. Poll sample sizes and bases feed an effective-n and margin of error. A Monte Carlo engine, calibrated against historical poll errors, turns them into seat ranges and outcome probabilities, shown in the console, on the maps and as bands on the poll trackers.

Westminster is built. The US House, Senate and presidential models have the same machinery, but their release is gated on calibration being accepted.

The remaining work comes from a review of the branch. Fix how vote shares convert to seats under correlated error, add heavier seat-level tails and parameter uncertainty, and correct several US model details. Then rerun and publish the backfilled history, only with explicit approval.

The work lives on branch `poll-errors-simulation`.

## Plan

_2026-11-08 · 15 of 17 pieces done · now: waiting on review of piece 7_

- [x] **Threads 1–4** · 40 checkpoints, commits in each thread plan
  The calibrated Monte Carlo engine, poll bases and effective n, console diagnostics, map and tracker uncertainty, and the frozen validation replay.
- [x] **1/8 Remove the US work** · `5a96e52c`
  The US uncertainty work moved to issue 0004's branch, `us-poll-errors-uncertainty`.
- [x] **1b/8 Remove the offline conversion experiment** · `39610fb9`
  The experiment moved to issue 0003's branch.
- [x] **1c/8 Regenerate the validation report** · `2618c0dd`
  Rebuilt from the frozen replay inputs.
- [x] **2/8 Westminster display changes** · `860b0d85`
  Simulated seat headline, odds as whole percentages.
- [x] **3/8 Style cleanup** · `000fbd9f`
  Ruff formatting, tidied imports and console templates.
- [x] **4/8 Self-review fixes (round 1)** · `eeee25d0`, `a832137d`
  Tracker segments, polls config errors, test gaps, a pinned jsdom dependency and console config guards.
- [x] **5/8 Merge master** · `af4405fb`
  Checks green afterwards.
- [x] **6a/8 Weighting defaults** · `a5cdf8e0`
  15-day half-life over a 60-day window, in the console and the CLI.
- [x] **6/8 Backfill and export** · `51c31966`
  848 daily runs rerun with uncertainty, exported with Phil's go-ahead.
- [x] **6b/8 Headline leads with the simulation** · `c7a85eab`
  Hung, majority and largest-party odds first, then the seat range.
- [x] **6c/8 Self-review fixes (round 2)** · `ae61f984`, `b2c33428`
  Documentation fixes, and the tracker starts again on the election-day result.
- [x] **6d/8 Nowcast note** · `950b22a2`
  Smaller note below the latest poll, saying it's a nowcast.
- [x] **6e/8 Vote-totals table** · `b788ae28`
  One Seats column; the uncertainty toggle only adds ranges.
- [x] **6f/8 Tracker gutter** · `8d7c9d42`
  No blank left space in the Chances and vote-only views.
- [x] **6g/8 Seats as the simulated mean** · `be4222f5`, `789e36ae`
  Mean seats, rounded by largest remainder to add up to 650, in the table, headline and tracker.
- [ ] **7/8 Split the map page: UNS by default, opt-in modelled uncertainty** · waiting on you
  Added 2026-11-08: a "Show with modelled uncertainty" checkbox replaces Show uncertainty; the latest poll and nowcast note move below the map; the tracker line follows its checkbox.
- [ ] **8/8 PR text** · not started
  The first final description went on PR #74 on 2026-11-08; `/publish` again after piece 7.

# Poll-error uncertainty for Westminster forecasts

Add honest uncertainty to the Westminster nowcast. Poll sample sizes and bases feed an effective n and a margin of error per poll. A Monte Carlo engine (10,000 simulations per run), calibrated against historical final-poll errors, turns the poll average into seat ranges and outcome probabilities. A reproducible validation harness checks the shipped model against frozen inputs.

**What readers get.** The map page still opens on the uniform-swing view. A "Show with modelled uncertainty" checkbox below the header switches to the simulated headline, mean seats with 90% ranges (rounded by largest remainder so the table adds up to 650), and each seat's vote range and win chance in its popup. The poll tracker starts on the uniform-swing line; its Uncertainty box switches to the simulated mean with 90% bands. "Latest poll used" moves to a smaller line below the map, joined by the nowcast note when the box is ticked. The data console shows every stage's intermediate numbers.

**Model settings and data.** Westminster poll weighting is a 15-day half-life over a 60-day window (it was 30 days for both). The full history (848 daily runs, 2024-07-05 to 2026-10-30) was rerun with these settings. On 2026-10-30 Labour is 354 on uniform swing and 317 as the simulated mean; the gap is the cost of error falling on Labour's many narrow seats.

**Out of scope.** Westminster only. US uncertainty moved to issue 0004 (branch `us-poll-errors-uncertainty`). The offline conversion-error experiment and the model refinements (correlated conversion error, heavier seat tails, parameter uncertainty) moved to #78. The seat ranges are known to be on the narrow side; that work is where they get revisited.

**Where it stands.** The code is on branch `poll-errors-simulation`, PR #74. Still to do: a beta note below the map, merging master (#80 landed, and the branch now conflicts in 14 files), and the final PR text.

## Plan

_2026-11-08 · 19 of 20 pieces done · now: adding a beta note below the map, then merge master and PR text_

- [x] **Threads 1–4** · 40 checkpoints, commits in each thread plan
  The calibrated Monte Carlo engine, poll bases and effective n, console diagnostics, map and tracker uncertainty, and the frozen validation replay.
- [x] **Offline conversion experiment** · `eaa73607`, `88bfc2a0`, `44dd8336`
  Six-transition check of how vote shares convert to seats; it failed its gate. Removed from this branch by piece 1b and carried on in #78.
- [ ] ~~**Conversion, tail and parameter refinements (2a–2f, 4b–4d)**~~ · moved 2026-11-07 to #78
- [ ] ~~**US House, Senate and presidential uncertainty**~~ · moved 2026-11-07 to issue 0004
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
- [x] **7/8 Split the map page: UNS by default, opt-in modelled uncertainty** · `5902f98f`
  Added 2026-11-08: a "Show with modelled uncertainty" checkbox replaces Show uncertainty; the latest poll and nowcast note move below the map; the tracker line follows its checkbox.
- [x] **Review fixes (round 3)** · 1f89732b
  Poll tracker back to master on initial load, with new features behind its checkbox; docs, console error tests and validator fixes.
- [ ] **8/8 PR text** · not started
  The first final description went on PR #74 on 2026-11-08; `/publish` again after piece 7.

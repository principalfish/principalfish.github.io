# Poll-error uncertainty for US forecasts

Add Monte Carlo poll-error uncertainty to the US House, Senate and President forecasts: seat and electoral-vote ranges, win and control probabilities, and tracker bands. The work also carries three fixes to the deterministic US models and the US poll trackers, which ship with it.

## Why this is its own issue

On 2026-11-07 Phil split the US work out of issue 0001 (`poll-errors-simulation`), so that issue ships the Westminster uncertainty only. The US work waits until two things are in place:
- the full US data pipeline: results imports such as 0002's 2026 midterm results, and the 2020 House results by district that A-1 needs;
- a more refined UK model (issue 0003).

## Where the code is

Branch `us-poll-errors-uncertainty` was created at `44dd8336`, the tip of `poll-errors-simulation` on 2026-11-07, and pushed to origin. It holds every US commit as built, on top of the UK work. `poll-errors-simulation` removed the US parts in one commit, `5a96e52c` (Piece 1 of 0001) and ships. **Merging master into this branch later brings that removal commit with it,** so revert it on this branch right after the merge (see `plan.md`).

`us-file-commit-map.txt` in this folder maps each changed file to the commits that touched it, as classified on 2026-11-07.

## What's in it

US-only commits (20): `6c4de57d e2a8e6fa 9d858a83 c0d62535 15611eed 0dcd8bb3 e770a063 fecbbd0c 6ff412a8 a48ecdf6 f70496e6 dcd2b883 d71845aa 795d46b9 aafc7be2 c7c994af 03bbf46d 087afcfc 134d1fc4 282dd916`. The US parts of the mixed commits are also here: `76be5ea8`, `f1e7d0cb`, `73fadb68` and `f417a14e`.

- **US uncertainty.** The House, Senate and President simulations (`model_support/us_*.py`), calibration (`models/us/calibration/*`), release gates (`models/us/*-release.json`, all `enabled: false`), validation, the console explorer, the export adapters and the front-end views.
- **Overall poll trackers.** House, Senate and President trackers on the US maps page (`a48ecdf6`, `dcd2b883`, `795d46b9`).
- **Deterministic US fixes, ungated.** On a live run, each of these changes the US output:
  - the Senate House-anchor fix (`087afcfc`, `134d1fc4`, `282dd916`), which removes the legacy +5.4 pt D shift on unpolled 2026 races;
  - the phantom-party fix for all three US models (`c0d62535`);
  - the Senate roster pinned per cycle (`0dcd8bb3`).
- **Poll-evidence columns** on polls (`e2a8e6fa`): `evidence_kind`, `population_type`, `evidence_source`. The live DB may already have them; master's code ignores them.

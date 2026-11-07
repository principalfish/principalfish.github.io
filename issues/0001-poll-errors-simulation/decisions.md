# Decisions: Poll-error uncertainty for Westminster forecasts

Each thread's decisions are recorded in full in its plan's Context and Notes under `threads/`. This ledger holds what's decided about the consolidated issue, and what's open now.

## Settled
- The four `~/.project-ai` plans for this branch are one issue, `poll-errors-simulation`, with the originals kept unchanged in `threads/` (2026-10-30).
- Thread 4 (review findings) controls the order of the remaining work. Overlapping checkpoints from threads 1–3 are cross-referenced, not merged (2026-10-30).
- 2a: investigate a more stable calibration before building any infrastructure, rather than build it switched off and defer 17/20 to 2f (2026-10-28).
- 2a: revise into a bounded offline experiment of the already-approved components (clusters + heavy seat tails + lambda predictive uncertainty), with nothing new added (2026-10-28).
- Piece 2a's offline joint model failed the unchanged 17/20 coverage gate (15/20; `stop_before_engine`). It isn't integrated, the gate isn't lowered, and 2b/2c don't start on this evidence (2026-10-29).
- Next step for 2a: investigate more comparable historical evidence, rather than replan on the existing four transitions with new assumptions (2026-10-29).
- Commit the active work and pause the model investigation until Phil returns (2026-10-29; commits `c067f150` → `ab3fc62e`).
- 2a historical scope: **A**, adding 1997→2001 and 2005→2010 now; the 1980s/early 1990s are not included (2026-10-30, after the full-flow explanation). The risk is accepted: both are low/moderate-change elections and may not contain a 2024-sized miss.
- 2a entry: **both**. The new transitions join every fold's training set, and each is also held out and reported as a separate diagnostic. The gate stays 17/20 on the original 20 cells (2026-10-30).
- 2a storage: a **frozen, hashed in-repo fixture** that only the experiment reads. No DB write, no export, nothing on the site (2026-10-30).
- Ship the branch as built and refine afterwards. 2a–2f and 4b–4d move to issue 0003 (#78), based on this branch. (2026-11-07)
- 4b–4d go into the same refinement issue as 2a–2f, not a separate US issue. (2026-11-07)
- Westminster ships with no caveat label about the missing conversion error and fat tails. (2026-11-07)
- 3a (display) ships with this branch. (2026-11-07)
- ~~Merge order: 0002 → master, then master → this branch, then ship this branch.~~ Superseded the same day: the UK ship doesn't wait for 0002. (2026-11-07)
- **Ship UK only.** All US work (uncertainty, US poll trackers, and the ungated Senate anchor, phantom-party and Senate roster fixes) moves to issue 0004 on branch `us-poll-errors-uncertainty` (`44dd8336`, pushed); this branch removes it in one commit. US uncertainty waits for the full US data pipeline and a more refined UK model. (2026-11-07)
- 0003 is left as is, still holding 4b–4d. (2026-11-07)
- Carried to 0004: US-5 = document the shrinkage; US-4 district term = new `district_scale`, default 0; A-1 waits for 2020 House results by district. (2026-11-07)
- 2a-H2 contract: equal weights across six transitions, new folds report-only (decided 2026-10-30; built in `88bfc2a0`).

- Move the offline conversion experiment (2a, H1–H3: `ab3fc62e`, `eaa73607`, `88bfc2a0`, `44dd8336`) off this branch to issue 0003. The boundary covers everything those commits added or changed, including the `historical_pair` and `content_hash` refactors. The shipped-model validation harness stays. Removed with one commit, not a history rewrite (Phil, 2026-11-07; Piece 1b).
- 0003's branch `refine-westminster-and-us-poll-error-models` was cut locally at `44dd8336`, so it keeps the experiment (not pushed). 0003's and 0004's plans now say what to do after merging this branch or master in: 0003 reverts 1b's removal commit, and 0004 reverts `5a96e52c` but leaves 1b applied (Phil, 2026-11-07).
- Regenerate the stale `uncertainty_validation_report.json` in its own commit straight after 1b, as Piece 1c. Reads the live DB only and writes that one repo file (Phil, 2026-11-07).
- W-4 (3a): the MC median seat count is the headline. The deterministic count stays, labelled "uniform-swing projection" and flagged when it falls outside its own 90% range. Confirmed 2026-11-07, as first chosen 2026-10-28 (Phil).
- Westminster poll weighting becomes a 15-day half-life with a 60-day window, for the console's daily run and the CLI defaults alike (Piece 6a), followed by a full rerun of 2024-07-05 → 2026-10-30. Holyrood and US unchanged (Phil, 2026-11-07).
- Main subtitle line leads with the simulation when uncertainty is shown: most likely outcome and its odds plus the largest party's odds, then the leader's median, range and majority odds. Uniform swing stays in the table (Phil, 2026-11-07; Piece 6b). Supersedes Piece 2's leave-the-main-line-unchanged choice.
- 6b wording: keep the prefix; majority case uses the same shape and adds hung to the second line; ties name both; it shows whenever uncertainty exists; the cut-off uses the rounded %; the second line follows the largest-party favourite (Phil, 2026-11-07).
- Self-review 2: fix all three verified doc items, and keep the 2024-07-05 election-day point at the start of the poll tracker, as master has it (Phil, 2026-11-07; Piece 6c).
- 6c: the election-day point is kept without bands and separate from the nowcast line (break at 2024-08-07); keep rule = leading unpolled runs; live step = trends rebuild only (Phil, 2026-11-07).
- 6d: the header note goes below "Latest poll used", smaller and not bold, and leads with "Nowcast: if an election were held today."; line 2 drops its "(if an election were held today)" (Phil, 2026-11-07). Front end only.

## Open
- Whether to run the branch self-review (Piece 4) before the master merge. Recommended.
- The go-ahead for each live step of the Westminster backfill and export (Piece 6).

## Assumed
- Older historical outcomes stay outside the original 20-outcome gate's denominator (thread 4 Notes; confirmed by the 2026-10-30 entry decision).

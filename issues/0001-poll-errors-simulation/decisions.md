# Decisions: Poll-error uncertainty for Westminster and US forecasts

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

## Open
- **2a-H2 contract details** (ask when H2 starts): the weight of the two new transitions (equal across all six is the natural extension), and whether their held-out folds count toward any guard (RMS, stability, drift) or are report-only.
- Thread 4's per-piece decisions, to raise when each piece starts: W-1 cluster definition and 2024 weighting (2a), W-2 regime scaling (2b), W-4 headline median (3a), US-2 correlation source (4d), and the live-run go-aheads (4e, Phase 5).

## Assumed
- Older historical outcomes stay outside the original 20-outcome gate's denominator (thread 4 Notes; confirmed by the 2026-10-30 entry decision).

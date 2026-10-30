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

## Open
- **Historical scope for 2a:** (A) 1997→2001 and 2005→2010 now (recommended), or (B) also the 1980s/early 1990s now? **Phil asked for an explanation of the full flow before choosing, so give that first** (outline in `resume.md`). This blocks all remaining model work.
- Thread 4's per-piece decisions, to raise when each piece starts: W-1 cluster definition and 2024 weighting (2a), W-2 regime scaling (2b), W-4 headline median (3a), US-2 correlation source (4d), and the live-run go-aheads (4e, Phase 5).

## Assumed
- Older historical outcomes, if added, stay outside the original 20-outcome gate's denominator (thread 4 Notes).

# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `5a96e52c…` until Piece 1b is committed.
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Settled 2026-11-07: regenerate the stale validation report as Piece 1c, straight after 1b (`plan.md`).

Current: **Piece 1b** staged in the builder worktree and paused for Phil's review (see `plan.md` Notes, 2026-11-07 builder Piece 1b).

Pending decisions:
- **W-4 for Piece 2 (3a).** Thread 4's Notes (2026-10-28) record Phil choosing the MC median as the **headline**, with the deterministic count labelled "uniform-swing projection". Phil hasn't confirmed it yet. Recommendation: keep it. The export already carries `s: [lo, median, hi]` (`data/model_support/public_uncertainty.py:114`).

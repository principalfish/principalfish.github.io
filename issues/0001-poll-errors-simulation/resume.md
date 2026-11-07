# Resume point: read this first

Updated 2026-11-07 (planner chat). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `5a96e52c…` (later is fine once Piece 1b is committed).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git worktree list` → nothing has `poll-errors-simulation` checked out, so a builder can start fresh.

Next step: **Piece 1b**, removing the offline conversion experiment (`plan.md` has the file list and acceptance). The boundary and the removal commit are settled.

Settled 2026-11-07: 0003's branch `refine-westminster-and-us-poll-error-models` was cut locally at `44dd8336` (check: `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…`), and 0003's and 0004's plans carry the merge-back notes.

Pending decision:
- **W-4 for Piece 2 (3a).** Thread 4's Notes (2026-10-28) record Phil choosing the MC median as the **headline**, with the deterministic count labelled "uniform-swing projection"; the ledger carried it as open. Phil hasn't confirmed it yet. Recommendation: keep it. The export already carries `s: [lo, median, hi]` (`data/model_support/public_uncertainty.py:114`). It doesn't block 1b.

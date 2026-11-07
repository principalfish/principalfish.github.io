# Resume point: read this first

Updated 2026-11-07 (planner chat). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `5a96e52c…` (later is fine once Piece 1b is committed).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git worktree list` → nothing has `poll-errors-simulation` checked out, so a builder can start fresh.

Next step: **Piece 1b**, removing the offline conversion experiment (`plan.md` has the file list and acceptance). The boundary and the removal commit are settled.

Pending decisions:
1. **How 0003's branch holds the experiment.** Phil asked to add it to 0003's existing branch. 0003's issue branch `refine-westminster-and-us-poll-error-models` doesn't exist; the branch he means is its planner chat's worktree branch, `claude/planner-refine-poll-error-models-f5f524`, at `44dd8336`, which already contains the whole experiment (and the US code). Proposed: cut 0003's issue branch at `44dd8336` (its plan allows "44dd8336 or later"), so nothing needs adding. Then note in 0003's and 0004's plans that a later merge of this branch or master would apply the removal commits (`5a96e52c` and 1b) and delete their US work or experiment, so that merge must neutralise them. Needs Phil's OK to write those notes into 0003's and 0004's records (each has its own planner chat; 0003 is paused).
2. **W-4 for Piece 2 (3a).** Thread 4's Notes (2026-10-28) record Phil choosing the MC median as the **headline**, with the deterministic count labelled "uniform-swing projection"; the ledger carried it as open. Phil hasn't confirmed it yet. Recommendation: keep it. The export already carries `s: [lo, median, hi]` (`data/model_support/public_uncertainty.py:114`).

# Resume point: read this first

Updated 2026-11-07 (planner chat, resumed after Piece 1). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `5a96e52c…` (later is fine if Piece 2 has been committed since).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git worktree list` → nothing has `poll-errors-simulation` checked out, so a builder can start fresh.

Pending decision: **W-4 for Piece 2 (3a).** Thread 4's Notes (2026-10-28) record Phil choosing the MC median as the **headline**, with the deterministic count kept and labelled "uniform-swing projection"; the consolidated ledger carried it as still open. Asked Phil on 2026-11-07 to confirm or change it, given the ship-as-built decision (ranges known to be too narrow). Recommendation: keep the headline choice. The export already carries `s: [lo, median, hi]` per party (`data/model_support/public_uncertainty.py:114`), so no new export field is needed for the median.

Once confirmed: record it in `decisions.md` and thread 4's Notes, then start the builder (`builder: poll-errors-simulation`, branch `poll-errors-simulation`, base `master`) for Piece 2.

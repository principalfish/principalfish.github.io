# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `5a96e52c…` until Piece 1b is committed.
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Current: **Piece 1b** staged in the builder worktree and paused for Phil's review (see `plan.md` Notes, 2026-11-07 builder Piece 1b).

Pending decisions:
- **Stale committed validation report** (found in 1b's checks, pre-existing since `92743751`): regenerate `data/models/westminster/uncertainty_validation_report.json` in its own commit after 1b, or leave it for Piece 4 (self-review). Recommendation: regenerate it as a separate small commit right after 1b. Its `historical`/`national` numbers already match, so only `replay` and the source hashes change. It reads the live DB read-only and writes only the repo file.
- **W-4 for Piece 2 (3a).** Thread 4's Notes (2026-10-28) record Phil choosing the MC median as the **headline**, with the deterministic count labelled "uniform-swing projection". Phil hasn't confirmed it yet. Recommendation: keep it. The export already carries `s: [lo, median, hi]` (`data/model_support/public_uncertainty.py:114`).

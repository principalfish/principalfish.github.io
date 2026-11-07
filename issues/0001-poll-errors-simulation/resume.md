# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `860b0d85…` (Piece 2).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Settled 2026-11-07: regenerate the stale validation report as Piece 1c, straight after 1b (`plan.md`).

Done: **Piece 1b** (`39610fb9`), **1c** (`2618c0dd`) and **Piece 2 (3a)** (`860b0d85`), all pushed. Next: **Piece 3** (style cleanup), when Phil says go.

Settled 2026-11-07: **W-4**, Phil confirmed the MC median seat count as the headline for Piece 2 (3a). The deterministic count stays, labelled "uniform-swing projection", and is flagged when it falls outside its own 90% range.

No pending decision. Next step: Piece 2 (3a); see `plan.md`.

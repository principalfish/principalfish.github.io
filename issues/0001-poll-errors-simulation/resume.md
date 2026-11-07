# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `eeee25d0…` (Piece 4 review fixes).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Settled 2026-11-07: regenerate the stale validation report as Piece 1c, straight after 1b (`plan.md`).

Done: **Piece 1b** (`39610fb9`), **1c** (`2618c0dd`), **2 (3a)** (`860b0d85`), **3** (style, `000fbd9f`) and **4** (review fixes, `eeee25d0`), all pushed. Next: **Piece 5**, merge master in; it needs Phil's go-ahead to push.

Settled 2026-11-07: **W-4**, Phil confirmed the MC median seat count as the headline for Piece 2 (3a). The deterministic count stays, labelled "uniform-swing projection", and is flagged when it falls outside its own 90% range.

No pending decision; the round-2 picks are made (`review.md`).

In progress 2026-11-07: **Piece 5, merging master in**, staged in the builder against origin/master `dc2fda3d`. Phil chose to finish the merge before round 2. Master's net change since `9ac8b04d` is empty (a setup script added in `3b1adc2f` and removed in `dc2fda3d`), so the validation report's hashed sources are unaffected. After Phil approves and it's pushed: **self-review round 2 fixes** (R2-1, R2-2, R2-4; `review.md`) on top. For each, ingest, confirm the commit and record its SHA (round 2's in `review.md`). Then Piece 6 (backfill and export; each live step needs Phil's go-ahead), then regenerate `uncertainty_validation_report.json` if its hashed sources changed.

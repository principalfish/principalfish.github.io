# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `eeee25d0…` (Piece 4 review fixes).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Settled 2026-11-07: regenerate the stale validation report as Piece 1c, straight after 1b (`plan.md`).

Done: **Piece 1b** (`39610fb9`), **1c** (`2618c0dd`), **2 (3a)** (`860b0d85`), **3** (style, `000fbd9f`) and **4** (review fixes, `eeee25d0`), all pushed. Next: **Piece 5**, merge master in; it needs Phil's go-ahead to push.

Settled 2026-11-07: **W-4**, Phil confirmed the MC median seat count as the headline for Piece 2 (3a). The deterministic count stays, labelled "uniform-swing projection", and is flagged when it falls outside its own 90% range.

No pending decision beyond the review choice below.

In progress 2026-11-07: **Piece 4 review fixes.** Phil chose all seven findings (`review.md`). They went to the builder for its Review fixes step. When it reports back: ingest, confirm the fix commits with `git log master..poll-errors-simulation`, re-run correctness and coverage on the new diff, update `review.md` with the SHAs, then tick checkpoint 4. Next after that: Piece 5 (merge master in).

Pending 2026-11-07 (planner chat): **self-review round 2.** All seven findings are resolved in `eeee25d0`. The re-check raised R2-1 to R2-5 (`review.md`), and Phil is choosing which to fix before Piece 5.

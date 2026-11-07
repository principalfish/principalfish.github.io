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

In progress 2026-11-07: **Piece 5 merge first** (origin/master `dc2fda3d`, staged in the builder; Phil chose this order). Then **self-review round 2 fixes** (R2-1, R2-2, R2-4; `review.md`) on top. When it reports: ingest, confirm the commit, record the SHA in `review.md`. Then Piece 5 (merge master in), which needs Phil's go-ahead to push. After Piece 5 and Piece 6, regenerate `uncertainty_validation_report.json` if its hashed sources changed.

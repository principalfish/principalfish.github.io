# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `000fbd9f…` (Piece 3).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Settled 2026-11-07: regenerate the stale validation report as Piece 1c, straight after 1b (`plan.md`).

Done: **Piece 1b** (`39610fb9`), **1c** (`2618c0dd`), **2 (3a)** (`860b0d85`) and **3** (style, `000fbd9f`), all pushed. Next: **Piece 4**, `/review self poll-errors-simulation` in the planner chat; the builder applies the fixes Phil picks.

Settled 2026-11-07: **W-4**, Phil confirmed the MC median seat count as the headline for Piece 2 (3a). The deterministic count stays, labelled "uniform-swing projection", and is flagged when it falls outside its own 90% range.

No pending decision beyond the review choice below.

Pending 2026-11-07 (planner chat): **Piece 4 self-review findings, Phil choosing which to fix.** Diff `master...000fbd9f`. Models: correctness and security opus, coverage, style and common-mistakes sonnet. Security clean. Verified findings:
1. medium · `electionmapslogic/features/polltracker.js:144-146` (also `:180`, `:208`; `data/model_support/trends.py:~295`): a run with no `segment_id` is treated as a segment boundary. All 440 rows in the committed `model_output_trends.json` have `hung` but no `segment_id` (written before `f417a14e`), so the tracker breaks its lines and bands at every run. That ships broken unless Piece 6 regenerates every row. Fix: only break when a known segment changes or uncertainty appears or disappears; treat unknown→unknown as continuous. Add a legacy-rows test.
2. medium · tests: the `dom.js:1465-1468` seat-popup band (wiring, predict view hidden, escaping); `app.js:153-192` (MC headline after load, and a stale fetch after a mid-request switch doesn't render).
3. low-med · tests: frozen-input error paths in `westminster_validation_inputs.py:88-200`.
4. low · `data/console/blueprints/polls.py:83`: `/polls` loads the uncertainty config on every request, so a bad config breaks every parliament's list. Fix: load it only for Westminster or catch `UncertaintyConfigError`, as `poll_detail` does at `:188-190`.
5. low · `data/console/blueprints/uncertainty_explorer.py:161-162`: unrounded quantile tail and an unvalidated `interval`. Fix: match `services/uncertainty_explorer.py:92-95`.
6. low · docs: `README.md:81-86` and `westminster_validation.py:3-5`, `:460-463`. Say "calibration", not "conversion", and state the "audit" (4.9029/0.3818) inline.
Dropped: the "piece 16" comment (already on master), "stored at 4 dp" (correct), `SIMULATIONS_PATH` default (same pattern as `DATABASE_PATH`).
Next: write `review.md` with Phil's picks, then send them to the builder (Review fixes step).

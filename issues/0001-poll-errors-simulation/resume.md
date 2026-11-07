# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `39610fb9…` (Piece 1b; later is fine once 1c is committed).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Settled 2026-11-07: regenerate the stale validation report as Piece 1c, straight after 1b (`plan.md`).

Done: **Piece 1b** committed as `39610fb9` and pushed. Next: **Piece 1c** (regenerate the validation report; `plan.md`).

Pending decisions:

Settled 2026-11-07: W-4 = MC median as the headline (Phil confirmed).

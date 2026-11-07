# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `a832137d…` (round-2 review fixes).
- `git rev-parse origin/us-poll-errors-uncertainty` → `44dd8336514cb20953b76f0c8d24b87df814aa95` (0004's branch; don't touch it).
- `git rev-parse refine-westminster-and-us-poll-error-models` → `44dd8336…` (0003's branch; holds the experiment).

Settled 2026-11-07: regenerate the stale validation report as Piece 1c, straight after 1b (`plan.md`).

Done: **1b** (`39610fb9`), **1c** (`2618c0dd`), **2** (`860b0d85`), **3** (`000fbd9f`), **4** (`eeee25d0`) and **5** (merge, `af4405fb`), all pushed. Round-2 review fixes `a832137d`. Next: **Piece 6**, the Westminster backfill and export, with a live go-ahead for each step.

Settled 2026-11-07: **W-4**, Phil confirmed the MC median seat count as the headline for Piece 2 (3a). The deterministic count stays, labelled "uniform-swing projection", and is flagged when it falls outside its own 90% range.

No pending decision; the round-2 picks are made (`review.md`).

Self-review closed 2026-11-07 (`review.md`). Rounds 1 and 2 are fixed in `eeee25d0` and `a832137d`. The validation report's source hashes match the branch at `a832137d` (check: hash each file listed in `identity.source_sha256` with `git show poll-errors-simulation:<file> | sha256sum`). Regenerate it if Piece 6 changes any of them.

After Piece 6: Piece 7, `/publish poll-errors-simulation` from the planner chat. The plan's `/builder … finish` wording is outdated.

2026-11-07 (planner chat): **switched to relaying.** The builder now sends every question, review request and live-step go-ahead to this chat. Put each one to Phil and relay his answer, quoting his words. PR #74, opened by Phil on 2026-10-13, is this issue's PR: not a draft, title "Poll errors simulation". Its body now carries `pr.md`'s progress checklist. The builder is running the Piece 6 rerun (~1.5 h, background); its next request is the go-ahead for the second backup.

# Review

# Branch review 2026-10-26 (parked)

`/review self` of `poll-errors-simulation` at `134d1fc4` plus the uncommitted Senate anchor piece 4. Six read-only passes ran: an independent methodology audit, code-review, coverage, common-mistakes, security and style. **The review is parked: no fixes were applied and nothing was committed.** The user will assess it later.

Full consolidated review: ../../reviews/poll-errors-simulation-20261026-134d1fc4-full/review.md
Methodology report: ../../reviews/poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/review.md

Headline: the machinery is correct, but Westminster seat odds are overconfident. Seat errors are independent (W-1, P1), seat-level tails are thin (W-2), and the error scales are treated as known (W-3). The US is sound but its scales are unidentified, so it should stay gated (US-1, US-2). No code bugs were found at P1 or P2. Security is clean.

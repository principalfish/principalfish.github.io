# Refine the poll-error models for Westminster and the US

The poll-error uncertainty is shipping as built. A review found it too confident in places, so this issue improves the error models afterwards, as separate, evidence-gated experiments.

For Westminster, seat totals barely vary once vote shares are known. That's because each seat's error is drawn independently, while in real elections tactical voting and vote efficiency shift many seats together. Even given the actual regional results, the 90% seat ranges covered only 12 of 20 party-by-election checks. Three attempts at a correlated conversion term reached 15, 15 and 14 against a target of 17. Every miss under-called a landslide, as in 2024, when Labour won 411 seats against a projected range of about 322–358.

The rest of the Westminster work:
- heavier seat-level tails, so no seat shows "win 100%";
- uncertainty in the fitted error sizes themselves;
- pollster house effects, and a flag for sparse polling windows;
- the right error scale for nation-only parties such as the SNP and Plaid;
- a calibrated lead between the top two parties;
- a joint recalibration of all of it.

For the US:
- a t-tailed national error;
- Senate errors handled per race, so polls inform them;
- correlated errors between states.

Each change is off by default until it passes its own test. Nothing is backfilled or published without explicit approval.

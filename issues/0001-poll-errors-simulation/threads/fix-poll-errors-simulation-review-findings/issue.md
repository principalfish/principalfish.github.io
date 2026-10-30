# Fix the poll-errors-simulation branch review findings

The 26 October review found overconfident Westminster seat odds, unidentified US error scales, and calibration, display, robustness, test and style gaps on `poll-errors-simulation`. Fix all findings on this branch, using the repository validation harness to retain the audit baseline and compare each model change. Westminster remains unpublished pending recalibration and explicitly authorized backfill/export; US release flags remain disabled.

To reproduce the unresolved conversion failure, fit the approved nation × three baseline-share-band cluster model on three of the four historical transitions and score the held-out transition with actual regional swing as its central input. Direct seat-total calibration, full-loading residual covariance and an approximate regional-balancing diagnostic each cover 15/20 party/election outcomes, below the required 17/20, and miss all five 2024 outcomes. Before adding cluster engine or archive infrastructure, run the user-approved offline joint experiment with heavy seat tails and lambda-scale uncertainty, retaining training-only fitting, equal transition weights, the original acceptance gates and explicit component-attribution checks. If it still fails, leave W-1 unresolved and obtain a concrete new design/evidence decision rather than weakening acceptance or deferring the failure to final recalibration.

Sources:
- [Full review](../../reviews/poll-errors-simulation-20261026-134d1fc4-full/review.md)
- [Methodology audit and numerical checks](../../reviews/poll-errors-simulation-20261026-134d1fc4-uncertainty-methodology/review.md)
- [Conversion-model assessment](assessment.md)

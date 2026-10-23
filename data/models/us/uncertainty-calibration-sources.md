# Proposed US uncertainty calibration sources

For source sign-off, 22 October 2026. This inventory selects sources and records
access/coverage limits; it does not fit parameters or enable odds. The proposed
archive table and its builder belong to the calibration stage after approval.

## Recommendation

Use the **RealClearPolitics / RealClearPolling historical archive** as the one
curated polling-average source for each of these series:

- final generic-congressional aggregate levels;
- final national presidential averages;
- final state presidential averages;
- final Senate race averages.

Use the [FEC's official result compilation](https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/)
for result verification, alongside the existing database results after matching
scope. FEC compiles results reported by state election offices. This is a result
source, not an additional polling archive blended into the calibration series.
Where FEC coverage is unavailable or ambiguous, record the exclusion and propose
an explicit source exception instead of silently substituting another source.

Why this archive: its [historical index](https://www.realclearpolling.com/historical-polling-archive)
links election-specific pages, and the checked pages expose raw candidate/party
levels and a final average date range rather than only a margin. That preserves
the undecided-inclusive generic-ballot levels used by the existing central model.
The current feed already contains RCP snapshots, but it also averages other
providers. Historical RCP error is therefore a **proxy for**, not a direct
validation of, the deployed multi-provider, recency-weighted estimator.

## Availability checked

The following final generic-average rows were visible in the original
publisher's archive on the audit date. D/R values are percentages, copied as
reported, not renormalized and not fitted parameters.

| Cycle | Democratic | Republican | Final average window | Source |
|-|-|-|-|-|
| 2012 | 47.3 | 47.5 | 10/29–11/5 | [RCP 2012](https://www.realclearpolling.com/polls/state-of-the-union/2012/generic-congressional-vote) |
| 2014 | 43.2 | 45.6 | 10/15–11/2 | [RCP 2014](https://www.realclearpolling.com/polls/state-of-the-union/2014/generic-congressional-vote) |
| 2016 | 46.0 | 45.4 | 11/1–11/7 | [RCP 2016](https://www.realclearpolling.com/polls/state-of-the-union/2016/generic-congressional-vote) |
| 2018 | 49.7 | 42.4 | 10/13–11/3 | [RCP 2018](https://www.realclearpolling.com/polls/state-of-the-union/2018/generic-congressional-vote) |
| 2020 | 49.3 | 42.5 | 10/25–11/2 | [RCP 2020](https://www.realclearpolling.com/polls/state-of-the-union/2020/generic-congressional-vote) |
| 2022 | 45.5 | 48.0 | 10/18–11/6 | [RCP 2022](https://www.realclearpolling.com/polls/state-of-the-union/2022/generic-congressional-vote) |
| 2024 | 46.7 | 47.0 | 10/14–11/2 | [RCP 2024](https://www.realclearpolling.com/polls/state-of-the-union/2024/generic-congressional-vote) |

This gives seven accessible final generic-ballot observations, not seven
independent series of daily surveys. Older cycles are linked in the archive
but are not yet validated for inclusion. Start with these seven rather than
claiming a longer verified sample. Retain missing/undecided mass as unallocated;
it is not an observed vote for the model's Others party.

State-average availability was spot-checked at:

- [Pennsylvania presidential 2020](https://www.realclearpolling.com/polls/president/general/2020/pennsylvania/trump-vs-biden): Biden 48.7, Trump 47.5, final window 10/29–11/2.
- [Pennsylvania presidential 2024](https://www.realclearpolling.com/polls/president/general/2024/pennsylvania/trump-vs-harris): Trump 48.5, Harris 48.1, final window 10/18–11/4.
- [Pennsylvania Senate 2024](https://www.realclearpolling.com/polls/senate/general/2024/pennsylvania/mccormick-vs-casey): Casey 48.1, McCormick 46.3, final window 10/21–11/4.
- [2024 Senate battleground index](https://www.realclearpolling.com/polls/battlegrounds/2024/senate) exposes final race averages, including Wisconsin and Pennsylvania.
- [National presidential 2024](https://www.realclearpolling.com/polls/president/general/2024/trump-vs-harris) is the same publisher's final national matchup series.

These establish the proposed source's format and feasibility. They are **not**
a complete census of available races or sufficient data for fitting state/race
variance. The calibration builder must enumerate every included and excluded
race in the chosen cycles; do not cherry-pick only the most notable misses or
pretend an unpolled state has an average. Prioritize 2016/2020/2024 presidential
and 2020/2022/2024 Senate archives, verifying each race URL before inclusion.
Coverage beyond the spot checks remains unverified.

## Committed calibration table and builder design

After source sign-off, build `data/models/us/us_final_poll_errors.json` from
reviewed records with a deterministic `data/scripts/build_us_final_poll_errors.py`.
The script should consume pinned local extracted records and explicit source
metadata, validate them and produce the table; it should not depend on live
page layout during every calibration/model run. Do not redistribute full
articles or entire poll tables unnecessarily. Retain the small final-average
facts and hashes of the curated input artifact, plus source URLs/retrieval dates.

Each observation needs:

- schema/version, series, election date/cycle, geography and contest kind;
- candidate/party identities, including independent candidates and special or
  runoff identifiers; preserve primary/withdrawn matchup exclusions;
- the final pre-election average's fieldwork window and any separately available
  publication date; never manufacture a publication date from its end date;
- raw poll percentages and denominator convention, with unallocated mass;
- official result counts/percentages, result scope and URL; include both source
  rounded values and computed precision only where counts justify it;
- source URL, retrieval date, curation revision and input hash;
- include/exclude status and a concrete reason for each excluded observation.

Validation must reject duplicates, non-finite/out-of-range percentages,
post-election polling averages, inconsistent candidate mappings and mismatched
race/cycle/geography. Multiple snapshots of a final average do not create extra
observations. Different Senate elections in one state must not collapse into one
row. A runoff is a separate contest, not the next snapshot of the general race.

Preserve raw party levels and signed errors as well as margins; do not quietly
convert a raw generic ballot into a two-party average. For state/race comparisons,
retain original levels and report any decided normalization as an explicit
transformation. Match the central pipeline's denominator and party treatment
before converting residuals into the engine's error scales.

## What can be fitted, and what remains an assumption

| Component | Available evidence | Honest first-stage conclusion |
|-|-|-|
| House national generic error | Seven verified final RCP cycle averages; official results need joining | A small cycle-level proxy estimate is feasible. Transfer to the current provider blend/window needs sensitivity and explicit acceptance. |
| Presidential national error | RCP final national matchup archive | Curate supported cycles before fitting; do not reuse the House generic-ballot scale. |
| Presidential state polling error | RCP state averages; 2020/2024 examples verified | Fit only after a full inclusion/exclusion inventory; separate common national error from incremental state residuals. |
| Presidential baseline-swing residual | Sixteen DB election results, 1964–2024 | Up to fifteen transitions, after restricting to comparable state units. This measures swing-model error, not polling error. |
| House district residual | 2022 and 2024 DB results | One transition, with changed boundaries and single-party baselines. Unmatched districts must be excluded, not treated as same-geography observations. |
| Senate polling/state/race residual | Final race averages plus 2020/2022/2024 DB results | Cross-sectional poll-error fitting may be possible. Separate state versus race variance and same-seat transition error are not identified by the current DB. |
| Local polling floor, geographic covariance and tails | Limited historical individual-poll/contributor records | Label constrained assumptions and show sensitivity unless additional curated evidence identifies them. |
| LV/RV/decided-base adjustment | No persisted US population/base history yet | Do not fit from absent labels or borrow UK estimates silently; recover evidence first. |

Final-average errors are total estimator errors: do not subtract imaginary
sampling variance or add it again as a separate generic-ballot component.
Weight/group at election level for validation; fifty correlated states in one
cycle do not constitute fifty independent tests of national error. Fit shared
and incremental components without double-counting the same residual.

The archive does not reconstruct the exact prior US pipeline, and the DB has
almost no pre-2024 polls. Consequently, leave-election-out checks on the curated
archive test a proxy component, **not the complete deployed blend**. State this
in calibration reports. Where component fitting is underidentified, use labelled
assumptions and obtain explicit acceptance rather than presenting a fitted
coefficient. The 2026 House/Senate outcomes can score the saved pre-election
nowcasts out of sample only if inputs/configurations are frozen before outcomes
are used; do not overwrite those runs during later tuning and call them held out.

No parameter values are approved by this source proposal. The next sign-off is
on using RCP as the curated average archive, FEC/verified DB outcomes for results,
and the documented coverage/transfer limits. Numerical calibration acceptance
remains a later separate gate.

## House candidate evidence, 23 October 2026

The reproducible House slice is now in
[`calibration/house-inputs.json`](calibration/house-inputs.json), with the generated
[candidate report](calibration/house-report.md) and full JSON companion. Source
approval has not accepted its numerical values or assumptions. House production
odds also remain gated until the 2026 map/baseline alignment is resolved.

The primary national fit uses the six clean 2012/2014/2016/2020/2022/2024 RCP
joins. The uncertified 2018 NC09 return is sensitivity-only. Official outcomes
use FEC candidate results, the separately reviewed 2020 NYC correction, and the
approved 2024 House Clerk exception. Raw D/R levels, final-RCV conventions,
nominal-count exclusions, original hashes and row/page provenance are retained.

Conditional state/race fitting uses 315 stable, measured, genuinely contested
districts from the 2022–2024 transition. The complete measured 433-district 2022
baseline supplies the six-party covariance shapes. The fit passes observed raw
national D/R changes through the actual deterministic helper and kernel before
forming its residuals; source scales are jointly inverted and independently
recovered. It does not substitute the earlier ideal-UNS ANOVA proxy.

Run offline from `data/`:

```sh
./election_data/bin/python scripts/calibrate_us_uncertainty.py \
  --output-dir models/us/calibration
```

Without `--output-dir`, the command prints its review document and writes
nothing. Neither mode opens a database, changes central projections, saves a
forecast or enables public odds. Senate and President fitting remain separate.

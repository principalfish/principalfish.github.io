# Independent methodological assessment: Westminster Monte Carlo uncertainty

Date: 14 October 2026

Issue: `add-monte-carlo-uncertainty-to-westminster-projection`

This note assesses the overall modelling approach against published election-modelling methods. It draws on the current issue plan, the implementation review and online research. It records independent recommendations; it does not amend the approved plan or authorise implementation changes.

## Overall assessment

The approach is defensible for a transparent Westminster nowcast. Retain its basic architecture, but do not yet treat its majority probabilities as empirically validated. The highest-priority methodological work is historical validation and a more coherent treatment of correlated errors.

Monte Carlo is an appropriate way to translate uncertain vote shares into seats and parliamentary outcomes. Its reliability depends on whether the simulated elections represent the ways real elections differ from the central projection. Running more simulations reduces numerical noise; it does not resolve incorrect distributional assumptions.

The present model is best described as an empirically tuned probabilistic regional-swing nowcast.

## Comparison with established approaches

| Approach | What it contributes | Comparison with this model |
|-|-|-|
| Swing-based projection | Translates changes in aggregate support into constituency results | Regional swings provide a reasonable, interpretable baseline |
| Hierarchical poll aggregation | Estimates underlying support while accounting for pollster effects, time and geographic relationships | Some uncertainty components are approximated, but the polling average remains fixed rather than jointly estimated |
| Multilevel regression and poststratification (MRP) | Models voting by demographic and political characteristics, then estimates each constituency's electorate | The present model has less information about where support changes within regions |
| Simulation of election outcomes | Converts joint vote distributions into seat ranges and outcome probabilities | Shared national/regional errors and complete-election simulations are structurally sensible |

These approaches overlap: MRP and hierarchical polling models can also use Monte Carlo. YouGov's methodology and the Economist's research model illustrate the additional data and structure involved. [YouGov methodology](https://yougov.com/en-gb/articles/49537-faqs-about-yougovs-2024-general-election-mrp-model), [Economist model paper](https://stat.columbia.edu/~gelman/research/published/Harvard_Data_Science_Review.pdf).

MRP would be a substantial change in data requirements and infrastructure. It is not a prerequisite for making the current model useful. The relevant test is whether the simpler model's estimates and uncertainty perform adequately on elections excluded from fitting.

## Decisions that are well supported

**Include systematic polling error.** Election polls can share errors that do not disappear when more surveys are averaged. The national error term addresses this directly. Separating national, regional and constituency uncertainty also provides an inspectable structure. Research on polling error explicitly distinguishes shared election-level bias from other variation. [Shirani-Mehr and colleagues, Disentangling Bias and Variance in Election Polls](https://sites.stat.columbia.edu/gelman/research/unpublished/pollposition_v2.pdf).

**Use a nowcast target.** “If an election were held today” is an appropriate interpretation. YouGov also describes its MRP estimates this way. Omitting future campaign movement is consistent with that target; it would become a problem if the outputs were described as probabilities for the next actual election. [YouGov's nowcast explanation](https://yougov.com/en-gb/articles/49537-faqs-about-yougovs-2024-general-election-mrp-model).

**Calibrate against history and expose diagnostics.** Sourced historical inputs, reproducible simulations, explicit error components and inspectable intermediate results are useful foundations. However, parameter fitting and software correctness do not by themselves demonstrate predictive calibration.

## Main methodological concerns

### 1. Historical fitting has not yet established reliable probabilities

Estimating error scales from history establishes plausible parameter values. It does not establish that a displayed 90% interval contains the result about 90% of the time, or that seats assigned 70% win probabilities are won about 70% of the time.

The recommended validation unit is an entire election:

- Hold an election out of calibration, including all fitted parameters that use its outcome.
- Reconstruct the inputs available before that election and run the complete pipeline.
- Assess vote-share interval coverage, split by party, region and competitiveness.
- Assess seat-win probabilities using Brier scores and reliability plots.
- Assess national seat-total interval coverage and interval widths.

Holding out individual seats would leave the same election's shared shocks in the training data. The thousands of constituency observations are not thousands of independent national elections. There are too few general elections to validate extreme majority probabilities precisely.

Where practical, supplement leave-one-election-out checks with chronological tests that train only on earlier elections. If historical input availability prevents a faithful reconstruction, state that limitation and distinguish component tests from full pipeline validation.

Published election research explicitly distinguishes in-sample fit from performance on withheld outcomes. The cited by-election study is an example of validation practice, not evidence that its particular model transfers directly to general elections. [Hanretty, Forecasting multiparty by-elections using Dirichlet regression](https://chrishanretty.co.uk/pubs/10.1016_j.ijforecast.2021.03.007/fulltext.pdf).

**Priority: highest.**

### 2. Correlation matters as much as individual error-bar widths

The national term correctly allows many seats to move together. However, after the shared terms, constituency errors are independent, while the regional error scale is a stated guess.

A plausible missing component is correlated error among similar constituencies across regional boundaries: for example, university seats or places with similar previous voting patterns. Independent constituency noise can produce plausible individual-seat intervals while understating uncertainty in a party's national seat total.

More elaborate forecasting models estimate geographic relationships using historical results and demographic similarities. Their authors identify these correlations as a difficult and consequential modelling choice. [Economist's 2024 methodological discussion](https://sites.stat.columbia.edu/gelman/research/published/2024_Election_Forecasting_Review.pdf).

Recommended assessment:

- Examine historical residual correlations after removing the model's existing shared components.
- Test a small number of interpretable shared factors or shrinkage-based alternatives.
- Assess national seat-total uncertainty as well as individual-seat coverage.
- Check Scotland and Wales separately, particularly because SNP polling misses are absent from the national calibration table.

Four historical election transitions cannot support a freely estimated constituency-by-constituency covariance matrix. Additional complexity should earn its place through validation.

This concern is an inference about the present model's structure, not a demonstrated measurement of how much its national intervals are too narrow.

### 3. The additive/proportional swing term is useful, but its distribution needs testing

Allowing uncertainty about the pattern of swing is preferable to assuming one pattern is always correct. Historical Electoral Calculus models also explored additive and proportional behaviour, particularly when a party's support declines sharply. [Electoral Calculus transition-model discussion](https://www.electoralcalculus.co.uk/blogs/Analysis_shapedstm.html).

The current implementation draws one common lambda for every party and region, centred on zero, although all four fitted historical values are positive.

Keeping the distribution centred on zero preserves additive swing as its reference. It does not establish that a symmetric distribution around additive swing is the best predictive distribution. Widening uncertainty around a biased centre does not necessarily correct the bias. Nor do four positive fitted values establish that the next election must follow the same pattern.

Compare additive, fitted blended and explicitly mixed-model alternatives on held-out elections. Avoid introducing numerous party-specific parameters without enough data to estimate them reliably.

The existing deterministic figure can remain available for continuity, but should be identified separately from the simulation mean or median. Counts of central-projection seat winners, expected seats and median simulated seats are different summaries and can legitimately disagree. [Electoral Calculus on seat winners versus expected seats](https://www.electoralcalculus.co.uk/blogs/ec_expseats_20240620.html).

### 4. Vote shares need a coherent joint distribution

The review identified a concrete sampling-variance inconsistency. The engine uses the analytic sampling variance for independent raw draws, then centres them. In a two-party 50/50 example with effective sample size 1,000 and all other error terms disabled:

- Intended variance per party: 2.5 percentage-points squared.
- Variance after centring: 1.25 percentage-points squared.
- One million draws through the implementation produced approximately 1.24924.

This is a consequence of the planned transformation, not an undocumented implementation deviation. It needs a modelling correction.

More generally, adding Gaussian errors, clipping negative shares and renormalising can shift expected shares and distort distributions near zero. Zero-mean input noise does not guarantee that the final simulated shares remain centred on the deterministic projection. The per-cell calibration correction addresses the linear centring step; it does not establish calibration after every nonlinear transformation.

Multiparty models commonly treat shares jointly through approaches such as Dirichlet or log-ratio models. Those approaches bring their own assumptions and should be evaluated rather than adopted solely because they are more elaborate. [Hanretty's discussion of compositional modelling](https://chrishanretty.co.uk/pubs/10.1016_j.ijforecast.2021.03.007/fulltext.pdf).

First separate and correct the sampling covariance. Then measure final simulated means, variances and boundary behaviour, including small parties, before deciding whether a broader share-model redesign is warranted.

### 5. Better sample sizes do not establish total polling precision

Capturing the actual voting-intention base is worthwhile. Nevertheless, a fixed design effect, a sample-size cap and an estimated decided fraction remain approximations. A published weighted base does not reveal the effective sample size implied by the respondent weights.

For nonprobability polling, conventional sampling margins do not capture all uncertainty introduced by selection and modelling assumptions. These quantities should be described as model-based approximations to sampling uncertainty rather than complete measures of poll accuracy. [AAPOR guidance](https://aapor.org/statements/understanding-a-credibility-interval-and-how-it-differs-from-the-margin-of-sampling-error-in-a-public-opinion-poll/).

There is also a distinction between industry-wide error and persistent pollster-specific error. The national term addresses the former, but does not stop a frequently publishing pollster from disproportionately influencing the average. Hierarchical aggregators explicitly model pollster effects. [Economist model specification](https://stat.columbia.edu/~gelman/research/published/Harvard_Data_Science_Review.pdf).

Useful checks include sensitivity to pollster mix, repeated releases from the same organisation, design-effect assumptions and the treatment of sparse regional crossbreaks. A full Bayesian aggregator is one possible later response; it is not required before measuring these sensitivities.

### 6. Calibration should match the deployed pipeline, including its tails

The national calibration uses averages of final polls, whereas the deployed projection uses a decayed 30-day window. Those estimators can have different errors when opinion moves. A nowcast can lag current opinion even though it deliberately excludes uncertainty about future opinion changes.

Final-poll misses also contain some combination of sampling error, turnout error and late movement. Treating their whole RMS as systematic error and then adding other components creates a potential overlap. This should be checked; it is not proof that the complete model is overdispersed. YouGov's 2024 review found evidence of both late movement and turnout-related problems. [YouGov's post-election review](https://yougov.com/en-gb/articles/51186-a-summary-of-yougovs-review-into-our-2024-general-election-performance).

MAD is a reasonable robust estimate of typical constituency error. However, fitting normal noise to that scale does not establish that rare local upsets are adequately represented. Such events matter when reporting very low win probabilities.

Recommended comparisons:

- Calibrate or validate the actual deployed averaging and projection procedure wherever historical data permits.
- Compare normal errors with heavier-tailed or empirical-residual alternatives.
- Test the sensitivity of outcomes to uncertain error scales and to including or excluding individual historical elections.
- Report the limited number of historical election transitions clearly; a large number of seat-party cells does not remove uncertainty about election-level behaviour.

## Recommended next methodological stage

Retain the current model and add a validation stage before publishing precise outcome probabilities:

1. Correct the sampling covariance and test its behaviour after the full share transformation.
2. Backtest the complete polling-average → projection → simulation pipeline on held-out elections.
3. Test sensitivity to geographic correlation, swing assumptions, pollster mix and heavier tails.
4. Revisit the point-projection model where validation demonstrates systematic weaknesses.
5. Explain the outputs as conditional model-based nowcast probabilities, with the assumptions and validation results available alongside them.

A full MRP replacement would require substantially different data and infrastructure. The current project can become a useful probabilistic regional-swing model without that replacement. Its next methodological milestone should be demonstrated performance on elections excluded from fitting.

## Scope and limitations of this assessment

This is a qualitative methodological assessment supported by primary research papers and practitioners' own methodology publications. Recommendations about this codebase are the assessor's inferences, not endorsements of the implementation by the cited authors.

The preceding code review verified the sampling-variance example and software checks. This research assessment did not run a historical backtest, estimate new parameters, compare predictive scores or establish that a replacement model would outperform the current one.

The cited US models are useful comparisons for error decomposition and dependence, but their two-party assumptions cannot be transferred directly to Westminster. The historical Electoral Calculus transition-model article illustrates alternative swing assumptions; it is not presented as its current production methodology.

# Add poll error bars and Monte Carlo seat/outcome probabilities to Westminster

The Westminster projection currently publishes a single deterministic answer: one
vote share and one winner per seat, one seat total per party. It says nothing about
how confident that answer is, even though every Westminster poll carries a sample
size that the model never reads.

This adds an uncertainty layer on top of the existing poll average and regional
swing model, without changing their point estimates. Sampling error from each
poll's (effective) sample size is combined with a historically calibrated
systematic polling error, correlated nationally, regionally and per seat, plus
uncertainty over whether swing is uniform or proportional. It is propagated
through 10,000 simulated elections. The outputs are 90% ranges on national,
regional and seat vote shares; seat win probabilities; seat-count ranges; and the
chances of each party winning a majority, being the largest party, or a hung
parliament. They are framed as a nowcast: "if an election were held today".

On the maps page these appear in the seat popup, the vote totals table (national,
or per region when filtered to one region), a one-line odds headline, and the poll
tracker, which gains vote-share bands and a "Chances" view backfilled across the
whole tracker history. The local data console gets a diagnostic view for every
stage: per-poll margins of error, the calibration report, the per-region error
budget, simulation results and odds history. Each stage can then be debugged
before the next is built. The latest run's individual simulated elections are also kept,
including every seat's winner and margin. A console simulation explorer can filter
them by outcome, open a single simulation, and drill into why a seat flips.

As a prerequisite, the corrupt stored sample sizes (BMG ~150k, Techne ~16k) are
repaired and the live Techne parser bug fixed. The importers also start capturing
each voting-intention table's own base: the number of people who named a party,
nationally and per region. That is the true n behind the published shares. Existing
polls are backfilled by re-fetching their sources. Holyrood, US, predict mode and a
Bayesian aggregator are out of scope.

## Error model

Shares are proportions (0–1) inside formulas and are displayed as percentage points.
Every σ is quoted in points for a party on 30%; §4 scales it for other shares. Each
formula is followed by its terms. A term reused from an earlier step points back to it.

### 0. Existing model (unchanged; listed because later steps build on it)

```
wᵢ      = 2^(−dᵢ / h) · ωᵢ
P̂ᵣ,ₖ    = Σᵢ wᵢ · pᵢ,ᵣ,ₖ  /  Σᵢ wᵢ
Δᵣ,ₖ    = P̂ᵣ,ₖ − P⁰ᵣ,ₖ
p̂ₛ,ₖ    = max(0, p⁰ₛ,ₖ + Δ_R(s),k)  /  Σ_k′ max(0, p⁰ₛ,k′ + Δ_R(s),k′)
```

Where:
- **i**: a poll admitted to the run, i.e. fieldwork ending within the 30-day window
  before the run date (`--since-days-back`).
- **k**: a party ("Other" is merged into "Others", as today).
- **r**: one of the 12 Westminster regions (`regions`, map 2).
- **s**: a seat (one of 650 constituencies).
- **R(s)**: the region containing seat s.
- **T**: the run's `as_of_date`.
- **dᵢ**: days between poll i's fieldwork end and T.
- **h**: decay half-life, 30 days (`--half-life-days`).
- **ωᵢ**: pollster weight (`pollsters.weight`; default 1, and all are 1 today).
- **wᵢ**: weight of poll i in the average. A poll loses half its weight every h days.
- **pᵢ,ᵣ,ₖ**: party k's share in poll i for region r (`poll_rows.percentage` ÷ 100). Its
  national rows are written pᵢ,ₖ.
- **Σᵢ**: sum over polls that report region r. A region with no crossbreak rows in the
  window uses the national average instead.
- **P̂ᵣ,ₖ**: the weighted poll average for party k in region r (national: P̂ₖ). The hat
  marks an estimate.
- **P⁰ᵣ,ₖ**: party k's regional share at the baseline election (2024), aggregated from
  `votes`. Superscript ⁰ = baseline.
- **Δᵣ,ₖ**: the regional swing, i.e. poll average minus baseline share.
- **p⁰ₛ,ₖ**: party k's share in seat s at the baseline election.
- **max(0, ·)**: clamps a negative share to zero.
- **Σ_k′**: sum over all parties, so the shares renormalise to 1.
- **p̂ₛ,ₖ**: the deterministic seat projection, which stays the published point
  estimate. The winner is the party with the largest p̂ₛ,ₖ.

### 1. The n behind each share

```
nᵢ,ᵣ  =  B_i,r                                  if the poll published the base
         nᵢ^total                               otherwise, national (r = national)
         nᵢ^total · popᵣ / Σ_{r′ ∈ Rᵢ} pop_r′    otherwise, regional (estimate)
```

Where:
- **nᵢ,ᵣ**: the number of respondents behind poll i's shares for region r. Written nᵢ for
  the national figure.
- **B_i,r**: the unweighted base of the voting-intention table's column for region r (or
  the "All"/"Total" column nationally), as printed by the pollster and captured at import
  (`poll_bases.unweighted_n`). It counts respondents who named a party, i.e. the sum of
  the party counts, because published shares exclude don't-knows and won't-votes. This is
  the preferred source.
- **nᵢ^total**: the poll's total sample (`polls.sample_size`, after the repair). It
  includes don't-knows, so it overstates n. Fallback only.
- **popᵣ**: region r's population (`regions.population`; e.g. London 9.1M).
- **Rᵢ**: the set of regions poll i reported. GB-only polls exclude Northern Ireland.
- **Σ_{r′ ∈ Rᵢ} pop_r′**: total population of those regions. The ratio popᵣ / Σ pop is
  region r's expected slice of the sample.

Example: in More in Common's 21 Sep 2026 poll, `sample_size` is 2113 but only 1491
respondents named a party. Its table prints that 1491, plus a base per region.
Estimating London from population instead (13.8% of GB) would give ≈206 of those 1491,
but the published London base is the real figure.

Each n is labelled in the console with its source: base, sample_size or estimate.

### 2. Single-poll sampling error

```
n_eff,i,r  = min(nᵢ,ᵣ / deff, n_cap)
SE_i,r,k   = √( pᵢ,ᵣ,ₖ (1 − pᵢ,ᵣ,ₖ) / n_eff,i,r )
MoE_i,r,k  = z · SE_i,r,k
```

Where:
- **nᵢ,ᵣ**: the n behind the share (§1).
- **deff**: design effect, default **1.5**. Weighting a sample inflates its variance, so it
  behaves like a smaller random sample. The literature puts UK online panels at 1.5–2;
  Kish's approximation is deff ≈ 1 + CV(weights)². It is applied to the *unweighted*
  base. Weighted bases are stored for the console but not used.
- **n_cap**: maximum effective sample size per poll, default **3000**. It guards against a
  mis-parsed n giving near-zero error.
- **n_eff,i,r**: effective sample size, i.e. the random-sample size with the same
  precision.
- **pᵢ,ᵣ,ₖ**: party k's share in poll i for region r (§0).
- **SE_i,r,k**: standard error of that share (binomial).
- **z**: **1.645**, the normal quantile for a two-sided 90% interval. 90% (5th–95th
  percentile) is used everywhere.
- **MoE_i,r,k**: the ± 90% margin of error, shown per poll and per crossbreak in the
  console.

Examples:
- National, n = 1500 decided, p = 0.30: n_eff 1000, SE 1.45 pts, MoE ±2.4 pts.
- A London crossbreak with base 200: n_eff 133, MoE ±6.5 pts. Crossbreaks are noisy.

### 3. Sampling variance of the poll average

The average is not re-weighted. This is the variance of the estimator the model already
uses, assuming the polls are independent samples:

```
Vᵣ,ₖ = Σᵢ wᵢ² · P̂ᵣ,ₖ (1 − P̂ᵣ,ₖ) / n_eff,i,r   /   (Σᵢ wᵢ)²
N_polls,r = (Σᵢ wᵢ)² / Σᵢ wᵢ²
```

Where:
- **wᵢ**: poll weight (§0).
- **P̂ᵣ,ₖ**: the poll average (§0). It is used instead of each pᵢ so a party on 0 in one
  poll doesn't get zero variance.
- **n_eff,i,r**: effective size of poll i's figure for region r (§2). For the national
  average (written Vₖ), national rows and their n_eff are used.
- **Vᵣ,ₖ**: sampling variance of the regional average. A region with no crossbreaks in
  the window uses Vₖ.
- **N_polls,r**: Kish effective number of polls, i.e. how many equally weighted polls the
  average is worth (console diagnostic only).

### 4. Share scaling

```
s(p) = √( p (1 − p) )  /  √( p_ref (1 − p_ref) )
```

Where:
- **p**: the share the error applies to, given in each later step.
- **p_ref**: **0.30**, the share at which every σ is quoted.
- **s(p)**: multiplier on an error SD, so small parties get proportionally smaller errors:
  s(0.30) = 1, s(0.10) = 0.65, s(0.03) = 0.37.

### 5. Random draws for each simulated election j

```
e_nat,k     ~ N(0, (σ_nat · s(P̂ₖ))²)
e_reg,r,k   ~ N(0, (σ_reg · s(P̂ᵣ,ₖ))²)          (systematic; centred below)
e_samp,r    ~ N(0, cᵣ · (diag(tᵣ) − tᵣ tᵣᵀ))     (sampling; zero-sum as drawn)
e_seat,s,k  ~ N(0, (σ_seat · s(p̂ₛ,ₖ))²)
λ           ~ N(0, σ_λ²)
```

Where:
- **j**: one simulated election, j = 1…S. **S** = **10,000** (`--simulations`), processed
  in chunks of 1,000 to bound memory. The RNG seed is T's ordinal, so a given date always
  gives the same result.
- **N(0, σ²)**: a normal draw with mean 0 and variance σ².
- **e_nat,k**: the national polling error for party k, drawn once per simulation and
  shared by every region. It's the industry-wide miss (e.g. 2015, 2024).
- **σ_nat**: SD of the national polling error, **calibrated** from UK final polls
  1992–2024 (§8). Fallback 2.5 pts if no config. Like σ_seat, it is a *raw* SD: the
  centring below shrinks it, and §8 calibrates it so the centred errors of the leading
  parties match history.
- **P̂ₖ**: the national poll average (§0).
- **e_reg,r,k**: the region-specific systematic error in region r, independent per
  region. A raw SD, centred like the national and seat terms.
- **σ_reg**: SD of region-specific systematic error, default **1.5 pts**. This is an
  informed guess; there's no data to calibrate it.
- **e_samp,r**: the sampling error of region r's poll average (one vector over the
  parties polled there), drawn so that each party's variance is exactly **Vᵣ,ₖ** (§3)
  *after* the zero-sum step. Drawing it as a raw N(0, V) and then centring would shrink
  it (two parties on 50/50 with n_eff 1,000: V = 2.5 pt², but (e₁ − e₂)/2 has 1.25), and
  unlike σ_nat and σ_seat there is no calibration to undo that. Construction, with ε a
  vector of independent standard normals:

  ```
  e_samp,r,k = √cᵣ · ( √tₖ · εₖ − tₖ · Σⱼ √tⱼ · εⱼ )
  ```

  which sums to zero and has covariance cᵣ(diag(t) − ttᵀ), the multinomial covariance of
  a share average. (cᵣ, t) are solved for per region so that cᵣ·tₖ(1 − tₖ) = Vᵣ,ₖ for
  every polled party, with Σt = 1. When every party's average comes from the same polls
  and the polled shares sum to 100, Vᵣ,ₖ = 100²·C·qₖ(1 − qₖ) with one factor
  C = Σwᵢ²/n_eff,i ÷ (Σwᵢ)² and q the averaged shares, so the fit is exactly t = q and
  cᵣ = 100²·C. Real polls list parties summing to ~96, a party can be missing from some
  polls, and a region can mix its own crossbreaks with national-fallback cells; then t
  moves a little off q (by ~0.002 at a 96% total) and every Vᵣ,ₖ is still reproduced
  exactly. Only where no such multinomial exists (two polled parties with different V,
  which a zero sum forces equal, or one party's V above the others' total) does it fall
  back to t = q with cᵣ = Σₖ Vᵣ,ₖ / Σₖ qₖ(1 − qₖ), keeping the region's total sampling
  variance; such regions are flagged. A region using the national fallback uses the
  national averages and variances Vₖ in the same way.
- **Vᵣ,ₖ**: sampling variance of the regional average (§3).
- **P̂ᵣ,ₖ**: the regional poll average (§0).
- **e_seat,s,k**: the seat-specific deviation from its region's swing (local candidates,
  tactical voting, incumbency), independent per seat.
- **σ_seat**: SD of seat-level deviation, **calibrated** from 2010–2024 results (§8),
  in the same raw (pre-centring) units. Fallback 3.0 pts.
- **p̂ₛ,ₖ**: the deterministic seat projection (§0).
- **λ**: the swing-type factor, one per simulation and shared by every seat. 0 means
  uniform (additive) swing, 1 means proportional swing. Centred on 0 so the simulations
  stay centred on the published projection.
- **σ_λ**: SD of λ, **calibrated** from historical seat swings (§8). Fallback 0.3.
- **s(·)**: share scaling (§4).

Each raw error vector (national, regional systematic, seat) is then centred so it sums
to zero across parties. If one party is overstated, the others are understated in
proportion to their size:

```
ẽₖ = eₖ − qₖ · Σ_k′ e_k′
```

Where:
- **eₖ**: one raw draw from above (applied separately to e_nat, e_reg for each region,
  and e_seat for each seat). e_samp is already zero-sum and is not centred; the regional
  error a seat receives is ẽ_reg + e_samp.
- **qₖ**: the share that term applies to (P̂ₖ, P̂ᵣ,ₖ or p̂ₛ,ₖ), normalised to sum to 1.
- **Σ_k′ e_k′**: the total of the raw draws across parties.
- **ẽₖ**: the centred error. The tilde marks centred.

Parties with no poll average in a region get no national or regional error there, only
seat noise. "No poll average" covers the NI parties (absent from GB polls), and also a
regional average below **0.5 pts** (`min_polled_share`). Pollsters print SNP and Plaid as
0, or a stray 0.1, in English crossbreaks, and the NI placeholder rows give GB parties 0
in Northern Ireland; none of those count as a poll average. In each region the national
error is then re-centred over the parties it reaches (qₖ = P̂ₖ normalised over them).

### 6. Seat projection in each simulation

```
swingₛ,ₖ  = (1 − λ) · Δ_R(s),k  +  λ · Δ_R(s),k · p⁰ₛ,ₖ / P⁰_R(s),k
xₛ,ₖ      = p⁰ₛ,ₖ + swingₛ,ₖ + ẽ_nat,k + ẽ_reg,R(s),k + e_samp,R(s),k + ẽ_seat,s,k
shareₛ,ₖ  = max(0, xₛ,ₖ)  /  Σ_k′ max(0, xₛ,k′)
winnerₛ   = argmaxₖ shareₛ,ₖ
```

Where:
- **λ**: the swing-type factor drawn in §5.
- **Δ_R(s),k**: the swing in seat s's region (§0).
- **p⁰ₛ,ₖ, P⁰_R(s),k**: baseline seat and regional shares (§0). Their ratio scales the
  swing up where a party was strong and down where it was weak, which is the proportional
  part. λ applies only to named parties whose regional baseline P⁰ is at least
  **1 pt** (`lambda_min_share`). The "Others" aggregate is always additive: it is the
  target of the model's Other → Others alias and mixes unrelated local candidates.
  Everywhere else, only the additive term is used.
- **swingₛ,ₖ**: the seat's swing, a blend of additive and proportional.
- **ẽ_nat,k, ẽ_reg,R(s),k, ẽ_seat,s,k**: centred errors (§5); **e_samp,R(s),k**: the
  region's zero-sum sampling error (§5).
- **xₛ,ₖ**: the simulated seat share before clamping. It can be negative.
- **max(0, ·), Σ_k′**: clamp and renormalise, exactly as the existing projection does.
- **shareₛ,ₖ**: simulated vote share in seat s.
- **argmaxₖ**: the party with the highest value.
- **winnerₛ**: the simulated winner of seat s.

With every error 0 and λ = 0 this reproduces the published projection p̂ₛ,ₖ exactly; a
test asserts that. The historical mean λ is reported in calibration but not applied,
because applying it would move point estimates.

### 7. Outputs over the S simulations

```
seatsₖ⁽ʲ⁾     = #{ s : winnerₛ⁽ʲ⁾ = k }
voteₖ⁽ʲ⁾      = Σₛ shareₛ,ₖ⁽ʲ⁾ · Tₛ  /  Σₛ Tₛ
seat range    = 5th / 50th / 95th percentile of seatsₖ⁽ʲ⁾ over j
vote band     = 5th / 95th percentile of voteₖ⁽ʲ⁾ over j
P(majorityₖ)  = mean_j[ 1{ seatsₖ⁽ʲ⁾ ≥ M } ]
P(largestₖ)   = mean_j[ 1{ seatsₖ⁽ʲ⁾ = max_k′ seats_k′⁽ʲ⁾ } / #tied⁽ʲ⁾ ]
P(hung)       = 1 − Σₖ P(majorityₖ)
seat win %    = mean_j[ 1{ winnerₛ⁽ʲ⁾ = k } ]
seat band     = 5th / 95th percentile of shareₛ,ₖ⁽ʲ⁾ over j
```

Where:
- **⁽ʲ⁾**: the value in simulation j.
- **#{ s : … }**: the count of seats meeting the condition.
- **winnerₛ, shareₛ,ₖ**: from §6.
- **seatsₖ⁽ʲ⁾**: party k's seat total in simulation j.
- **Tₛ**: seat s's turnout, i.e. baseline total votes. It's used as a weight, as the
  existing model does.
- **voteₖ⁽ʲ⁾**: party k's national vote share in simulation j.
- **percentile over j**: taken across the S simulated values. 5th–95th is the 90% range;
  50th is the median.
- **mean_j[·]**: the average over simulations. The mean of a 0/1 indicator is the share
  of simulations where the condition holds.
- **1{·}**: 1 if the condition holds, else 0.
- **M**: majority threshold, **326** of 650 seats. Sinn Féin abstention is not modelled.
- **#tied⁽ʲ⁾**: the number of parties sharing the top seat count in simulation j, so a tie
  is split evenly.
- **P(hung)**: no party reaches M. Majorities are mutually exclusive, so this is 1 minus
  their sum.
- **seat win %, seat band**: per seat s and party k.

Regional outputs apply the same formulas over the seats in a region. The headline
"leader" is the party with the highest P(largest).

### 8. Calibration

The calibration script writes a committed config and a full report, both shown in the
console. History shows *realised* errors, which already sum to zero across parties.
The simulation draws *raw* errors and then centres them (§5), which shrinks a big
party's error. So each historical residual is divided by its own cell's centring ratio
(the engine's own transform at that cell's shares) before the spread is taken: a
moment match per cell, giving the raw σ the config holds (user decision).

**σ_nat, from curated UK history:**

```
zₑ,ₖ          = (Aₑ,ₖ − Rₑ,ₖ) / s(Rₑ,ₖ)
σ_nat,real    = √( mean_e [ mean_k zₑ,ₖ² ] )
cₖ(shares)    = √( sₖ² − 2qₖ·sₖ² + qₖ²·Σⱼ sⱼ² ) / sₖ,   sⱼ = s(shareⱼ), q = shares / Σ shares
σ_nat         = √( mean_e [ mean_k (zₑ,ₖ / cₖ(Rₑ))² ] )
```

Where:
- **e**: a UK general election, 1992–2024, each weighted equally.
- **k**: a party in the table at e: Con, Lab, LD always; Green, UKIP, Brexit Party or
  Reform where PollBase's result row and at least one final poll report them. PollBase
  has no SNP column, so the SNP is absent.
- **Aₑ,ₖ**: the equal-weight average of each pollster's final poll, from Mark Pack's
  PollBase (which includes non-BPC pollsters), in a committed, sourced table
  (`uk_final_poll_errors.json`). Final-poll rule: fieldwork ending in the 3 days
  before polling day or on it, started before polling day, published by polling day;
  each pollster's last such poll.
- **Rₑ,ₖ**: the actual GB vote share (PollBase's result row).
- **s(·)**: share scaling (§4), so errors on parties of different sizes are comparable.
- **zₑ,ₖ**: the polling miss, scaled to a 30% party.
- **σ_nat,real**: the root mean square (RMS) of the misses: the mean z² within each
  election, averaged over elections. RMS rather than SD, because a consistent
  industry-wide bias is still error. All parties count, UKIP 2017 included (user
  decision).
- **cₖ**: the SD a party's error keeps after the engine centres it (ẽ = e − q·Σe, §5),
  as a fraction of its raw SD (`monte_carlo.centring_sd_ratio`). Here it is taken at
  each election's result shares, with one remainder party standing in for everyone
  PollBase does not name. About 0.82 for the two largest parties and 0.93 for the
  rest.
- **σ_nat**: the raw value written to the config. Each miss is divided by its own
  party's ratio, so the engine's centred draw for that party matches history.

**λ and σ_seat, from election pairs in the DB:**

```
y        = p¹ₛ,ₖ − p⁰ₛ,ₖ
a        = P¹_R(s),k − P⁰_R(s),k
b        = a · p⁰ₛ,ₖ / P⁰_R(s),k
λ̂        = Σ (y − a)(b − a)  /  Σ (b − a)²
σ_λ      = √( mean_pairs λ̂² )
p̂ₛ,ₖ     = max(0, p⁰ + a + λ̂ (b − a)) renormalised over the seat's parties
u        = ( y − a − λ̂ (b − a) ) / s(p̂ₛ,ₖ)
σ_seat,real = 1.4826 · median| u − median(u) |     (pooled over the pairs)
σ_seat   = 1.4826 · MAD( u / cₖ(p̂ₛ) )                (pooled over the pairs)
```

Where:
- **pair**: two consecutive general elections on the same boundaries: 2010→15, 15→17
  and 17→19 (old boundaries), and 2019 notional→2024 (new).
- **⁰ / ¹**: the earlier / later election of the pair.
- **p⁰ₛ,ₖ, p¹ₛ,ₖ**: party k's share in seat s at each election.
- **P⁰_R(s),k, P¹_R(s),k**: party k's share across seat s's region at each election
  (actual results, not polls, so seat-level error is isolated from polling error).
- **y**: the actual seat change.
- **a**: the additive prediction, i.e. the seat moves exactly like its region.
- **b**: the proportional prediction, i.e. the regional change scaled by the seat's
  relative strength (a itself where the engine keeps the swing additive: Others, and
  parties under `lambda_min_share` regionally).
- **Σ**: sum over every seat × party cell in the pair.
- **λ̂**: the least-squares swing-type factor for the pair (0 = additive, 1 =
  proportional). It is also reported per party.
- **σ_λ**: RMS of λ̂ across the pairs. No centring correction: λ is not centred.
- **p̂ₛ,ₖ**: the seat's predicted later share, exactly as the engine projects with
  every error 0 and λ = λ̂: the share the engine scales seat noise by.
- **u**: what's left of the seat change after the fitted swing type, scaled by s(p̂).
  A cell with p̂ = 0 (no seat noise in the engine) is left out of u. So is a cell with
  s(p̂) = 0 or no finite centring ratio (p̂ = 100); the two are counted separately.
- **median, |·|**: middle value; absolute value. median|u − median(u)| is the median
  absolute deviation (MAD).
- **1.4826**: converts a MAD into a normal SD. A MAD is used so one-off outliers
  (by-election-style upsets, independents) don't inflate σ_seat.
- **σ_seat,real**: the robust SD of u, pooled across all pairs (user decision).
- **cₖ(p̂ₛ)**: the centring ratio (as above) at the seat's predicted shares, for that
  cell's party. About 0.82 for a seat's two largest parties and 0.96 for the rest.
- **σ_seat**: the raw value written to the config: the same robust spread with each u
  first divided by its own cell's ratio, so the engine's centred seat noise matches
  history cell by cell.

Excluded cells: the Speaker's seat (at either election); the Others aggregate (always
additive in the engine, and it mixes unrelated local candidates); parties absent
nationally at either election; cells with P⁰_R(s),k < `lambda_min_share` (1%); and
cells where the party did not stand in the seat at the earlier election (p⁰ = 0: a new
candidacy, not a swing). The report counts each, and every table above is shown in the
console Calibration view.

### Known limits

- Only four election pairs inform σ_λ.
- σ_reg has no calibration data and is a guess.
- The sampling term treats polls as independent. Correlated house effects are covered
  only through σ_nat and σ_reg.
- There is no time-to-election drift: the odds are a nowcast ("if an election were held
  today").

# Stage briefs: add-monte-carlo-uncertainty-to-westminster-projection

Read the brief for a piece before it is built. Each one explains why the stage exists,
what it does, the maths involved (with § references to `issue.md`), a worked example,
what you'll be able to check afterwards, and the judgment calls worth questioning.
The builder re-presents the relevant brief at the start of each piece, with worked
examples refreshed from the live data.

---

## The whole pipeline in one picture

```
 polls (shares + n)
   │  piece 1: fix wrong n        piece 2: use the n the shares are actually based on
   ▼
 per-poll error  ── "this one poll could be ±2.4pt out by chance"            (§1–2)
   │
   ▼
 error of the poll AVERAGE  ── averaging many polls shrinks chance error      (§3)
   │
   ▼
 + errors averaging can't fix:
     national  ── the whole industry misses together (2015, 2024)            (§5)
     regional  ── a region behaves differently from what polls say           (§5)
     seat      ── local candidates, tactical voting, incumbency              (§5)
     swing type ── do strong seats swing more (proportional) or the same?    (§5–6)
   │  piece 4 calibrates how big each of these is, from history
   ▼
 10,000 simulated elections ── each draws one set of errors, projects 650 seats  (§6)
   │  pieces 3a, 3b + 6
   ▼
 summaries: odds, seat ranges, win % per seat                                 (§7)
   │  piece 5 stores them; piece 7 lets you inspect everything in the console
   ▼
 export (piece 8) → maps page (piece 9) + poll tracker (piece 10)
```

The key idea is that **the published projection doesn't change.** Everything here
describes how far reality could plausibly land from it.

---

## Piece 1: Repair poll sample sizes

**Why it exists.** Every error bar starts from a sample size. If a poll says it asked
152,118 people, the maths concludes it is accurate to ±0.2pt and the model becomes
falsely certain. Several stored polls have exactly that problem.

**What went wrong.**
- **BMG:** the methodology line reads "Sample: 1,521 GB adults 18+". An older parser
  glued the digits together into 152118. The parser has since been fixed, but 13 bad
  rows stayed in the database.
- **Techne (all 27 polls, still live).** Two bugs stack:
  1. **Wrong wave.** The "Unweighted Sample" row prints the *previous* wave's n, then
     the current wave's, then the Male/Female columns: `1636 1644 808 836`. The parser
     took the first number. The real n is the second, and Male + Female = 1644 proves
     it.
  2. **Glued digits.** pypdf 6.6.0–6.16.1 dropped the spaces, so the parser read five
     digits (`16361`).
  - The stored values can't be decoded: each holds the previous wave plus a stray digit,
    and the series has gaps.

**What this piece does.**
- **Fix the Techne parser** so it takes the current-wave figure. It checks that figure
  against Male + Female and refuses to guess when digits are glued. pypdf is pinned to
  ≥6.16.2.
- **Write a repair script** that lists every wrong value next to its corrected value.
  You review that list before anything is written.
  - BMG rows: strip the trailing "18".
  - Techne rows: re-fetch each poll's PDF and parse it with the fixed parser. The PDF's
    fieldwork dates must match the stored poll, and its previous-wave figure must match
    the stored glued value; otherwise the row is skipped.
- **Corrected values match the parser exactly.** The importers use sample size to spot
  duplicate polls, so a mismatch would let a re-import create duplicates.
- **Out-of-range values are flagged.** Anything outside 500–10,000 that can't be
  repaired is reported as a skip, so nothing implausible goes unnoticed.

**After it, you can check:** the dry-run list. Every corrected n should land between
roughly 500 and 10,000.

**Worth questioning:** whether any *other* pollster has implausible n. Two YouGov MRP
merges (n ≈ 17,230) are genuine and left alone; piece 3's n_eff cap handles them.

**Note:** after piece 2, `sample_size` becomes only a fallback, but it still needs to be
right.

---

## Piece 2: Capture decided bases (the n behind the shares)

**Why it exists.** A poll's headline shares are not percentages of everyone asked.
Don't-knows and won't-votes are removed first. More in Common's 21 Sep poll asked 2,113
people, but only 1,491 named a party, and the 23% Conservative figure is 23% *of those
1,491*. Using 2,113 pretends there is more information than there is.

**Key concepts.**
- **Total sample:** everyone interviewed (`polls.sample_size`).
- **Decided base:** those who named a party. This is the right n for vote shares.
- **Crossbreak:** the same poll's figures for a subgroup, here a region. A London
  crossbreak might be 200 people out of 1,491, so it is much noisier than the national
  figure.
- **Unweighted vs weighted base.**
  - Pollsters reweight respondents to match the population (age, past vote and so on).
  - The *unweighted* base is how many people actually answered.
  - The *weighted* base is what they "count as" after reweighting.
  - We use the unweighted base and account for weighting separately through the design
    effect (piece 3).

**What this piece does.**
- Each importer records the base printed in the voting-intention table, for the "All"
  column and for every region column it reads shares from, in a new `poll_bases`
  table: the **unweighted** count, plus the weighted base when printed.
- Every base is labelled with what it counts (`kind`):
  - `decided`: respondents who named a party (don't-knows, refusals and won't-votes
    removed), so it is the shares' own denominator;
  - `likely_voters`: a turnout-filtered base that still includes the undecided, so it
    overstates n slightly;
  - `all_adults`: everyone interviewed in the column.
- A source column covering several of our regions (YouGov's "North" is North East +
  North West + Yorkshire) is split across them in proportion to `regions.population`,
  as whole numbers that add up to the printed base (largest remainder). Those rows are
  flagged `split_from_macro`.
- Old polls get the same treatment by re-downloading their source files
  (`scripts/backfill_poll_bases.py`), but only when the re-parsed file is clearly the
  same poll: same fieldwork dates and sample size, and shares within 0.5pt (national,
  or for YouGov the regional shares it stores; with neither, the poll is skipped).
- Where no base was captured, a resolver falls back to `sample_size` nationally, or a
  population-share estimate regionally. Each n is labelled with where it came from:
  `base:decided`, `base:likely_voters`, `base:all_adults`, `sample_size`, or
  `estimate:<kind>` / `estimate:sample_size` (naming what the estimate was scaled
  from). An estimate below 1 is left unresolved.

**What each pollster prints (established from fixtures and real source files).**

| Pollster | Base row used | Kind | National | Regions | Weighted? |
|---|---|---|---|---|---|
| More in Common | headline sheet "Unweighted N" (older files also "Weighted N") | decided | yes (1491 vs sample 2113) | 11 direct | older files only |
| Survation | VI table "Unweighted Total" / "Weighted Total" | decided ("likely to vote ... undecided and refused removed") | yes | London, Scotland, Wales, NI direct; North/Midlands/South split | yes |
| Ipsos | national: Table 1 "Total voting (sub-base)", unweighted from its UnWgt Total column; regions: LTV table "Unweighted Total" / "Weighted Total" | national decided; regions likely_voters | yes (867 vs sample 1031) | 6 macro columns, 3 split | yes |
| Opinium | HeadlineVI "Base: ... (Unweighted)" / "(Weighted)" | decided (also when 2024 files label it "All UK respondents": party counts sum to the weighted base) | yes (equals stored sample_size: the importer already reads this row) | 7 macro columns, 3 split | yes |
| BMG | each used table's "Unweighted row" / "Base: Total" | decided for the final "not sure and prefer not to say removed" table; likely_voters for older tables | yes | 11 direct | yes |
| Focaldata | combined-VI table "Column n" / "Column Population" | decided | yes (1397 vs sample 1585) | 6 macro columns, 3 split | yes |
| YouGov | "Unweighted Sample" / "Weighted Sample" (page 1, or the region table's two unlabelled rows) | all_adults | yes | 6 macro columns, 3 split | yes |
| Deltapoll | "Unweighted Sample" / "All GB Adults" (region block's two unlabelled rows) | all_adults | yes | 6 macro columns when the regional block parses | yes |
| Lord Ashcroft | "Unweighted Sample" / "Weighted Sample" of the percentage sheet | all_adults (the stored shares keep don't-know in the denominator) | yes | 11 direct | yes |
| Techne | current-wave "Unweighted Sample" / "Weighted Sample" | all_adults (the decided table prints no base) | yes | none (national-only) | yes |
| Find Out Now | Headline VI "Filtered N", stored weighted-only (no unweighted count is printed; console shows `base:decided(weighted)`) | decided | yes (≈1,450 vs sample ≈2,600) | 11 direct | weighted only |

**YouGov decisions.** YouGov prints only all-adult bases, so they are stored as
`all_adults` (the console shows `base:all_adults`, not a decided base). Its macro
regions (North, Midlands, Rest of South) are split by population; the counts are only
kept when they add up (England + Wales + Scotland = total, the four England regions =
England).

**Find Out Now decision.** Its "Filtered N" (the weighted, turnout-adjusted count behind
the decided shares) is stored as a weighted-only base: `unweighted_n` NULL,
`weighted_n` set, kind `decided`. The resolver uses it as n and labels the source
`base:decided(weighted)` (regional estimates from it: `estimate:decided(weighted)`).

**Two importer bugs fixed, and their stored rows repaired.**
- **YouGov, Jun 2025–Feb 2026 layouts:** page 1 ends in the country columns and the
  region table follows the second question, outside the section the parser read. The
  old parser took page 1's last six columns (65+, ABC1, C2DE, England, Wales, Scotland)
  as Wales, Scotland, North, Midlands, London, Rest of South (poll 26: SNP 39% in
  East/South East/South West England, Plaid 27% in London). The parser now finds that
  table, and a sanity guard rejects any regional table with the SNP or Plaid Cymru
  above 3% outside their nation or below 10% / 5% inside it.
- **Lord Ashcroft:** regional shares were a count (or, in single-sheet workbooks, a
  percentage) divided by the column's all-adult "Weighted Sample", so regions added up
  to 3–45% (and 54–61% in Sept 2026, whose counts are turnout-weighted likely voters)
  against national totals of 61–72%. The percentage sheet prints each region's
  percentages on the same basis as the national ones; the parser now reads them
  directly, and rejects any parse (either path) whose regional totals average more
  than 5 points from the national total (correct polls: within about 1.5).
- `scripts/repair_regional_poll_rows.py` replaces the stored regional rows (and bases)
  only for polls whose stored rows fail the test (YouGov: misaligned, or a region
  totalling under 85; Ashcroft: the importer's basis guard), after checking fieldwork,
  sample size and national shares, with a stale guard over each poll's rows and bases
  and `--dry-run`. A re-parse without bases keeps the stored bases. It also deletes
  the regional rows of YouGov's two MRP merge releases (polls 56 and 241, user
  decision): their sources print no regional table, so those rows were demographic
  columns. The deletion is a recorded list (poll, pollster, digest of the rows); a
  poll whose rows have changed since is skipped. Run it **before** the backfill: the backfill's YouGov share check
  compares the fixed parse with the stored rows and skips broken polls.

**Expected results** (scratch copy of the DB, every source re-fetched on 2026-10-12).

Repair: **30 changes**: 21 YouGov and 7 Lord Ashcroft polls repaired (230–234, 260,
1795), and the 88 regional rows of each MRP release (56, 241) deleted; 69 unchanged,
0 skipped. A second run changes nothing. YouGov stores no national rows, so 56 and
241 are left rowless (their n falls back to `sample_size`, ≈17,230, which piece 3's
n_eff cap handles); the backfill skips them because the parser rejects their
page-1-only layout.

Backfill after the repair:

| Pollster | Polls with national base | Crossbreaks with base | Main reason for gaps |
|---|---|---|---|
| YouGov | 88/90 | 968/968 (100%) | the two MRP releases (now without regional rows) |
| Find Out Now | 87/90 | 957/957 | 3 workbooks without a Headline VI sheet |
| Opinium | 37/37 | 407/407 | — |
| Survation | 13/13 | 109/109 | — |
| Lord Ashcroft | 9/9 | 99/99 | — |
| Techne | 27/27 | (national-only) | — |
| More in Common | 15/73 | 165/792 (21%) | 56 old `/media/` xlsx URLs 404 |
| Focaldata | 4/8 | 20/23 | Google Sheets layouts print no decided base |
| BMG | 1/14 | 11/154 | re-parsed national shares differ from stored (older parser) |
| Ipsos | 1/7 | 11/77 | older PDFs no longer parse; one share mismatch |
| Deltapoll | 1/6 | 0/55 | site returns 403/HTML for most PDFs |

**Effect.** Error bars widen by √(2113/1491) ≈ 1.19, about 19%, for polls like the More
in Common example.

**After it, you can check:**
- In the console poll list and poll view, each column's n, its source and its weighted
  n (the poll view's matrix footer marks split bases; `(weighted)` marks a
  weighted-only base).
- `/polls/coverage`: per pollster, the share of polls with a national base and of
  crossbreaks with a regional base.

**Worth questioning (important):**
- **Several printed bases are not decided bases.** YouGov, Deltapoll and Techne print
  all-adult bases above decided shares; Ipsos's regional bases include the undecided.
  They are labelled, so piece 3/6 can treat them differently (e.g. scale by
  1 − don't-know share where printed). Nothing is derived this round.
- **Lord Ashcroft shares include "don't know / would not vote"**, by design of its
  tables: national rows sum to 61–72%, regional rows (after the repair) likewise. The
  model assumes shares sum to about 100. Recommendation: renormalise Lord Ashcroft
  shares over the parties (divide by their sum) in the model, and treat its all-adult
  base × that sum as the decided n. Not changed this round.

---

## Piece 3a: From n to error (config, effective n, poll and average error)

**Why it exists.** Piece 2 says how many people stand behind each share. This piece
turns that count into "how far could this share be out by chance", for one poll and
for the model's weighted average, and puts the first error numbers in the console.

**The chain, step by step (§1–§4).**
1. **Start from the resolved n** (piece 2): a captured base, the sample size, or a
   population estimate, each labelled with what it counts.
2. **Adjust for what the count counts.** Shares exclude don't-knows, so a count that
   includes them overstates n:
   - `decided` base (including `base:decided(weighted)` and `estimate:decided…`): ×1;
   - `likely_voters` (base or estimate): × `likely_voters_fraction` (0.9);
   - `all_adults` (base or estimate): × `decided_fraction` (0.80);
   - `sample_size` and `estimate:sample_size`: also × `decided_fraction`, because a
     total sample includes don't-knows. A missing base never looks more precise than a
     captured one.
3. **Divide by the design effect** (1.5): weighting costs about a third of the sample.
4. **Cap** at 3,000 effective respondents, so a mis-parsed n (or YouGov's 17k MRP
   merges) cannot claim near-zero error. The result is **n_eff**.
5. **One poll's margin:** `MoE = 1.645 · √(p(1−p)/n_eff)` (90%).
6. **The average's error:** `V = Σwᵢ²·p(1−p)/n_eff,ᵢ ÷ (Σwᵢ)²` with the model's existing
   weights and the averaged share p, plus Kish's effective poll count `(Σw)²/Σw²`.
7. **Share scaling** for later pieces: `s(p) = √(p(1−p))/√(0.3·0.7)`: 1 at 30%, 0.65 at
   10%, 0.37 at 3%.

All settings live in `UncertaintyConfig` (`model_support/uncertainty.py`), loaded from
`data/models/westminster/uncertainty_config.json` when piece 4 has written it, else the
defaults (deff 1.5, cap 3000, 90%, p_ref 0.30, decided 0.80, likely voters 0.9, σ_nat
2.5, σ_reg 1.5, σ_seat 3.0, σ_λ 0.3, 10,000 sims in chunks of 1,000, majority 326).
Unknown keys in the file are an error; missing keys keep their defaults.

**Where 0.80 comes from (user decision).** Pollsters that print both a decided and a
total base give decided ÷ total of: More in Common 0.75, Survation 0.81, Ipsos 0.84,
BMG 0.86, Focaldata 0.89. Their poll-weighted mean is 0.80.

**Worked examples (real London crossbreaks).**

| Poll | Base | Adjustment | n_eff | Share | 90% MoE |
|---|---|---|---|---|---|
| Opinium | 204 decided | ×1 → 204 | 204 ÷ 1.5 = **136** | Reform 18% | **±5.4pt** |
| YouGov | 218 all adults | ×0.80 → 174.4 | 174.4 ÷ 1.5 = **116.3** | Reform 11% | **±4.8pt** |

**The London average** over 23 crossbreaks in the model window: Kish effective count
22.2 (recent polls weigh a little more, so 23 polls count as about 22), sampling SD of
the averaged share **≈0.67pt**, i.e. **±1.1pt** at 90%. (First worked at the earlier
0.75 decided fraction, giving 0.68pt. At 0.80 only the all-adult and `sample_size`
crossbreaks gain n, by about 7%, so the SD moves by less than 1%. The Kish count
depends only on the weights, so it is unchanged.) Averaging shrinks a ±5pt
crossbreak to about ±1pt; the errors averaging can't fix (piece 3b) then dominate.

**After it, you can check** (console, Westminster polls only):
- `/polls`: each poll's national n_eff (marked "(capped)" when the cap applied) and the
  90% margin for a party on 30%, a like-for-like figure across polls.
- `/polls/<id>`: under the matrix, each column's n, source, weighted n, **Adjustment**
  (×1 / ×0.9 / ×0.8) and **n_eff**; beside every share its own ±margin at that
  column's n_eff. "-" marks an unresolved n (no margin is shown).

**Worth questioning:**
- `decided_fraction` 0.80 is one number for every all-adult pollster and region. Real
  don't-know rates vary (by pollster, over time, and higher in some regions).
- The design effect of 1.5 is applied to every pollster alike.

---

## Piece 3b: The simulation engine (the heart of it)

**Why it exists.** We want statements like "Reform majority 31%". A majority depends on
650 seat contests whose errors are linked: if the polls overstate Reform, they overstate
it almost everywhere at once. No neat formula handles that, so we simulate. We generate
10,000 plausible "what if the truth were a bit different" elections and count outcomes.

### The ingredients, in plain words

**1. Chance error in one poll (§2).** Interview a different random 1,500 people and
you'd get slightly different numbers. For a party on 30% with 1,500 decided
respondents:
- The design effect of 1.5 counts weighting as costing a third of the sample, so the
  effective n is 1,000.
- SE = √(0.3×0.7/1000) = 1.45pt.
- The 90% margin is ±2.4pt: 9 times in 10, chance alone keeps the poll within 2.4pt of
  the truth.

**2. Chance error in the average (§3).** Averaging polls cancels much of the chance
error. 15 polls of that size, equally weighted, take the national SE down to about
0.4pt. Regional averages stay noisy because crossbreaks are small. The formula respects
the model's existing weights: recent polls count more, so the average behaves like
*fewer* polls than it contains.

**3. Errors averaging can't fix (§5).** This is where most of the real uncertainty lives.
Each simulated election draws:

| Error | Real-world meaning | Shared by | Size |
|---|---|---|---|
| national `e_nat` | the whole polling industry is off together (shy voters, turnout models), like 2015 or Labour in 2024 | every seat | σ_nat ≈ 2–3pt (piece 4) |
| regional `e_reg` | a region's polling is off on its own (e.g. Scotland), plus that region's chance error from step 2 | seats in that region | σ_reg 1.5pt + sampling |
| seat `e_seat` | a seat departs from its region's swing (popular MP, tactical squeeze, local issue) | just that seat | σ_seat ≈ 3–5pt (piece 4) |
| swing type `λ` | do parties lose most where they were strongest (proportional) or equally everywhere (uniform)? | every seat | σ_λ (piece 4) |

**Why the layering matters.** If every seat had only its own independent error, lucky
and unlucky seats would cancel out, and national seat totals would barely move. Odds
would be absurdly confident. The shared national error and λ make whole blocks of seats
move together, which is what happens in real elections. This was FiveThirtyEight's main
lesson from 2016.

**4. Share scaling (§4).** A party on 3% can't plausibly be 5 points out. So every error
is scaled by s(p): 1.0 at 30%, 0.65 at 10%, 0.37 at 3%.

**5. Zero-sum (§5).** Shares add to 100, so if one party is overstated, others must be
understated. Each set of party errors is adjusted to sum to zero, with the adjustment
shared out by party size.

### One simulated seat, worked through (§6)

Seat X. Baseline (2024): Lab 40, Con 30, Ref 15, LD 10, Grn 5.
Its region swings (from polls): Lab −8, Con −5, Ref +12, LD 0, Grn +1.
Regional baseline shares: Lab 35, Con 28, Ref 14, LD 10, Grn 6.

**The published projection (no errors, uniform swing):** Lab 32, Ref 27, Con 25, LD 10,
Grn 6. **Labour holds by 5pt.**

**Simulation j** draws λ = 0.3 (30% of the way towards proportional swing):

| | Lab | Con | Ref | LD | Grn |
|---|---|---|---|---|---|
| uniform swing | −8 | −5 | +12 | 0 | +1 |
| proportional swing = swing × seat/region | −8×40/35 = −9.14 | −5×30/28 = −5.36 | +12×15/14 = +12.86 | 0 | +1×5/6 = +0.83 |
| blended: 0.7×uniform + 0.3×proportional | −8.34 | −5.11 | +12.26 | 0 | +0.95 |
| errors drawn (national + regional + seat, centred, sum 0) | −2.0 | −0.6 | +2.9 | −0.2 | −0.1 |
| x = baseline + swing + errors | 29.66 | 24.29 | 30.16 | 9.80 | 5.85 |
| renormalised to 100 | 29.73 | 24.35 | **30.23** | 9.82 | 5.86 |

In this simulation **Reform gains the seat by 0.5pt**. If that happens in 2,300 of the
10,000 simulations, the seat's Reform win chance is 23% and Labour's is roughly the rest.

### Turning 10,000 simulations into numbers (§7)

- **Seat range:** sort a party's 10,000 seat totals; the 500th and 9,500th are the 90%
  range, the 5,000th is the median.
- **P(majority):** the share of simulations where the party has ≥326 seats.
- **P(largest):** the share where it has the most seats, with ties split.
- **P(hung):** the share where nobody reaches 326.

### Determinism and the key safety test

- The random seed is derived from the run date, so re-running a date gives identical
  numbers.
- The engine must reproduce the published projection **exactly** when all errors and λ
  are zero. That test proves the simulations are centred on what's published, not on a
  subtly different model.

### As built (piece 3b)

- **Where.** `data/model_support/monte_carlo.py`, a sibling of `uncertainty.py`
  (which stays the numpy-free settings and sampling-error half). The model hands it a
  `SimulationInputs` built by `build_simulation_inputs` straight from its own
  dictionaries, and calls `run_simulations(inputs, config, seed, keep_raw=...)`. That
  returns the summary, the error scales it drew with (for the error budget), and
  optionally the raw simulations.
- **Who gets which error (user decision).** A party gets the national and the regional
  error in a region only where it has a real poll average there. That means its own
  crossbreaks, or the national fallback for a region with none, and an average of at
  least 0.5 points (`min_polled_share`). Pollsters print SNP and Plaid as 0, or a stray
  0.1, in English crossbreaks, and Northern Ireland placeholder rows give GB parties 0
  there. Before this rule, SNP picked up a national error in every English seat. In
  each region the national error is re-centred over the parties it reaches. Parties in
  no poll (the NI parties, the Speaker) get seat noise only.
- **Which swings can be proportional (user decision).** λ applies only to a named party
  whose regional baseline is at least 1 point (`lambda_min_share`). The "Others"
  aggregate is always additive: it pools unrelated local candidates, so an
  independent's 47% in Islington North over Others' 3% in London says nothing about
  how that seat will swing. Seat noise still applies to Others.
- **The regional sampling error keeps its full size (review fix, 2026-10-14).** At
  first the sampling variance V of a region's poll average was folded into the regional
  error's raw SD and then centred along with the systematic part. Centring shrinks a
  raw error, and nothing corrected for it: for two parties on 50/50 with an effective
  n of 1,000, V is 2.5 pt² but only 1.25 reached the simulation. Now the sampling part
  is its own term, drawn already summing to zero with the covariance of a share average
  (multinomial), cᵣ(diag(t) − ttᵀ). Each region's (cᵣ, t) is solved so that every polled
  party's sampling variance is exactly its V. When all parties' averages come from the
  same polls and their shares add to 100, t is just the averaged shares; real polls add
  to about 96, and t then shifts by a fraction of a point to keep each V exact. If no
  exact fit exists (two parties with different V, or one V larger than all the others
  combined) it falls back to t = the shares, keeping the region's total sampling
  variance, and flags the region. National-fallback regions use the national averages
  and variances the same way. The σ_reg part is unchanged (drawn raw, then centred). The
  regional error a seat gets, and the `e_reg` stored for the explorer, is the sum of the
  two; the draws also keep the sampling part on its own, for the error budget.
  **Effect:** a big party's sampling SD rises by about 1/0.85 (a 3% party's barely
  moves), so with σ_reg 1.5 a region's total error SD for a 35% party goes from about
  2.7 to 3.05 pt at a summed effective n of 300 (1.8 to 2.0 at 1,000). Regional vote
  bands widen by roughly that much. National ranges move less, because regional errors
  partly cancel across regions and the national error and λ dominate.
- **Reproducibility.** Each error term has its own random stream, all derived from the
  seed (the run date's ordinal), drawn simulation by simulation (the regional stream
  holds each simulation's systematic normals, then its sampling normals). So the result
  is the same whatever the chunk size, and re-running a date gives identical numbers.
- **The exactness test, and why it was fiddly.** With every error and λ at zero the
  engine matches `project_seat_votes` to the last bit on every seat and party. Three
  details made that work. Parties must be taken in the same order the model loops over
  them, which decides who wins an exact tie. Python's `sum()` of floats (3.12+) is
  *compensated*, so the engine reimplements that summation rather than using numpy's.
  And the λ blend is written so that λ = 0 is literally "baseline + swing".
- **Per-seat ranges** come from a 0.1-point histogram of each seat's simulated shares,
  because keeping all 10,000 × 650 × 13 shares would take 680 MB. They are accurate to
  about a tenth of a point. The stored seat block leaves out a party that never wins
  and whose range tops out below 0.5 points.
- **Speed.** 10,000 simulations of 650 seats × 13 parties take about 5 s (about 5.5 s
  when the raw simulations are kept for the explorer).

**Real numbers (read-only demo, as of 5 Oct 2026, fallback σs: σ_nat 2.5, σ_reg 1.5,
σ_seat 3, σ_λ 0.3; regional sampling variances from the captured poll bases).** The
live polls (after the 12 Oct repairs) give a deterministic Lab 307, Ref 109, Con 105,
LD 72. The stored 5 Oct row (Lab 307, Ref 112, Con 102) predates those repairs, and it
reproduces exactly on a pre-repair backup.

| | Seats 5th / median / 95th | Vote 90% | P(majority) | P(largest) |
|---|---|---|---|---|
| Labour | 222 / 299 / 372 | 22.8–29.6 | 28% | 97% |
| Reform | 47 / 114 / 203 | 19.6–26.5 | 0% | 3% |
| Conservative | 57 / 102 / 158 | 16.6–23.2 | 0% | 0% |
| Lib Dem | 60 / 70 / 78 | 8.5–13.3 | 0% | 0% |

P(hung) is 72%. Some seats:
- Dartford: Lab 47% / Ref 47% / Con 6%, a three-way marginal at 31.5 / 31.4 / 25.5.
- Witham: Con 50% / Ref 50%.
- Pontypridd: Ref 50% / Lab 49%.
- South West Devon: projected Con, but Ref 41% / Con 41% / Lab 18%.

The independents' seats, before → after the additive-Others rule:

| Seat | Win %, before → after | Others 90% range, before → after |
|---|---|---|
| Islington North | 98.3% → 100% | 38.2–53.9 → 42.0–52.0 |
| Birmingham Perry Barr | 98.8% → 99.2% | 34.3–45.4 → 34.8–45.1 |
| Leicester South | 98.4% → 99.5% | 32.1–45.1 → 33.7–44.1 |
| Chorley (the Speaker) | 100% → 100% | 73.0–83.7 → 74.0–83.8 |

λ now applies to 63 of the 156 region × party cells, and 69 cells count as polled. In
an average simulation about 18% of seats change hands relative to the projection.
Piece 4's calibration will show whether σ_seat = 3 is too wide.

**After it, you can check:** the engine's tests (zero-error reproduction, seeds,
centring). Nothing new is visible in the console until pieces 6–7; the per-poll n_eff
and ± margins arrive in piece 3a.

**Worth questioning:**
- Whether a design effect of 1.5 suits every pollster.
- Whether a normal distribution is too thin-tailed. Real polling misses occasionally
  look bigger than a normal curve suggests.

---

## Piece 4: Calibration (where the error sizes come from)

**Why it exists.** The simulations are only as honest as the error sizes fed in. Too
small and the odds are overconfident; too big and every outcome looks equally likely.
This piece measures them from history wherever data exists, and says so plainly where it
doesn't.

**σ_nat: how wrong final polls have been (§8).**
- A committed table lists, for each general election from 1992 to 2024, the average of
  the final polls and the actual GB result per party.
- Each miss is scaled to a "30% party" so large and small parties are comparable.
- σ_nat is the root mean square (RMS) of all misses.
- RMS is used rather than standard deviation because SD ignores a consistent miss: if
  every pollster overstated Labour by 4 points, that's a 4-point error, not zero spread.
- Expect a value around 2–3pt. 1992, 2015 and 2024 will stand out in the report.

**λ and σ_seat: how seats really swing relative to their region (§8).**
- For each pair of consecutive elections on the same boundaries (2010→15, 15→17, 17→19,
  2019 notional→2024), every seat × party gives three numbers:
  - **y:** what actually happened to the seat;
  - **a:** what it would have done if it swung exactly like its region (uniform);
  - **b:** what it would have done under proportional swing.
- **λ̂** is the best-fit mix between a and b for that pair. 0 means uniform held; 1 means
  fully proportional. The 2019→2024 Conservative collapse will probably show a large λ̂.
- **σ_λ** measures how much λ̂ varies between elections. That's the uncertainty we
  simulate.
- **σ_seat** is the spread of whatever is left after the best mix, i.e. the genuinely
  local part.
  - It uses the median absolute deviation (MAD) × 1.4826, a "robust" SD.
  - One freak result (a Galloway-type upset) doesn't inflate it the way a normal SD
    would.

**What isn't calibrated:**
- σ_reg (1.5pt) is a guess, because no historical regional poll data exists in the
  database.
- σ_λ rests on only four election pairs.
- The report and the console Calibration view label every parameter as calibrated,
  literature or guess.

**After it, you can check:** the Calibration view in the console, with every parameter,
its source, and every intermediate table. Read the per-election and per-pair tables
before accepting the config.

**Worth questioning:**
- Should 1992 be included (very different polling methods)?
- Should recent elections count more?
- Is the 2019 notional result trustworthy enough to calibrate against?
- Is σ_λ from four points meaningful, or should it be fixed by judgment?


### As built (piece 4)

- **Where.** `scripts/build_final_poll_errors.py` turns Mark Pack's PollBase workbook
  into `models/westminster/uk_final_poll_errors.json` (committed).
  `scripts/calibrate_uncertainty.py` fits the four calibrated settings and writes
  `uncertainty_config.json` and `uncertainty_calibration_report.json` (both committed;
  re-running reproduces them byte for byte). The maths lives in
  `model_support/calibration.py`. It reuses the engine's own centring helpers
  (`monte_carlo.centring_weights` and `centring_sd_ratio`). The console view is
  `/westminster/uncertainty/calibration` (hub at `/westminster/uncertainty`, linked from
  the home page). Final-poll data: Mark Pack, PollBase —
  https://www.markpack.org.uk/opinion-polls/.
- **Which polls count as "final".** For each election, each pollster's last poll whose
  fieldwork ended in the 3 days before polling day (or on it), which started before
  polling day and was published by polling day. Polls PollBase shows as unpublished or
  released later are left out, e.g. Survation's withheld 2015 phone poll, and Focaldata
  and Find Out Now in 2024. Every pollster counts equally. PollBase includes pollsters
  outside the British Polling Council. It has no SNP column, so the SNP isn't in σ_nat.
  Two PollBase rows whose dates contradict themselves are skipped by name and listed in
  the table: YouGov 16 Apr 2010 and YouGov's 11–18 Jun 2024 MRP. Both are well outside
  the window.

**Engine units (user decisions).** History shows the *realised* size of errors, which
already add up to zero across parties. The simulation draws a *raw* error for each party
and then centres it (an overstated party means others are understated). That centring
also shrinks a party's own error, by an amount that depends on its share. A seat's two
leading parties keep about 82% of the raw SD; smaller parties keep about 96%.
- So each historical residual is divided by its own cell's share kept, worked out with
  the engine's own formula, before the spread is taken.
- This is a moment match per cell, and it gives the raw σ the config holds.
- Each simulated party then wobbles as much as history says it should.
- σ_λ needs no correction, because λ isn't centred.

**σ_nat: realised 2.55 → raw 2.94.** Realised is the RMS of the scaled misses, with each
election weighted equally and every party included. For the raw value, each z is first
divided by its party's centring ratio at that election's result shares. A remainder
party stands in for those PollBase omits. The ratios run 0.80–0.84 for the two largest
parties (p10–p90, median 0.82) and 0.88–0.99 for the rest (median 0.93). A is the
final-poll average, R the result, z = (A − R)/s(R):

| Election | Polls | Con A / R | Lab A / R | LD A / R | Others (A / R) | RMS z |
|---|---|---|---|---|---|---|
| 1992 | 5 | 38.3 / 42.8 | 39.4 / 35.2 | 19.0 / 18.3 | – | 3.38 |
| 1997 | 5 | 30.8 / 31 | 46.6 / 44 | 16.4 / 17 | – | 1.45 |
| 2001 | 3 | 31.3 / 32.7 | 45.3 / 42.0 | 18.3 / 18.8 | – | 1.97 |
| 2005 | 6 | 32.5 / 33.2 | 37.7 / 36.2 | 22.5 / 22.6 | – | 0.90 |
| 2010 | 9 | 35.6 / 36.9 | 27.6 / 29.7 | 27.4 / 23.6 | – | 2.80 |
| 2015 | 12 | 33.6 / 37.7 | 33.0 / 31.2 | 8.9 / 8.1 | Grn 5.2 / 3.8, UKIP 12.8 / 12.9 | 2.49 |
| 2017 | 11 | 43.1 / 43.4 | 36.5 / 41.0 | 7.7 / 7.6 | Grn 2.0 / 1.7, UKIP 4.4 / 1.9 | 4.19 |
| 2019 | 12 | 43.1 / 44.7 | 33.8 / 32.9 | 11.9 / 11.8 | Grn 3.0 / 2.8, BXP 2.9 / 2.1 | 1.43 |
| 2024 | 19 | 21.3 / 24.4 | 38.8 / 34.7 | 11.3 / 12.6 | Grn 6.7 / 6.9, Ref 16.2 / 14.7 | 2.59 |

For comparison, realised: 2.62 if every election × party cell counts equally, and 2.35
using Con, Lab and LD only. The biggest single miss is UKIP in 2017 (z = +8.3); it stays
in (user decision). 1997's results are whole numbers in PollBase. PollBase's results
match the database's GB shares to within 0.05 pt for 2010–2024.

**λ and σ_seat, per election pair.** u is what's left of a seat's change after the
fitted swing mix, scaled to a 30% party by the seat's *predicted* share. That is the
share the engine scales seat noise by, as the engine would project it with λ̂. For the
raw value, each u is divided by its own cell's centring ratio at the seat's predicted
shares before the same robust spread.

| Pair | u used | λ̂ | σ_seat realised | σ_seat raw | Median ratio: top two / others | Note |
|---|---|---|---|---|---|---|
| 2010→2015 | 2,719 | +0.24 | 4.32 | 4.86 | 0.82 / 0.94 | LD collapse; SNP surge near-uniform (λ̂ −0.36) |
| 2015→2017 | 3,104 | +0.44 | 4.18 | 4.64 | 0.80 / 0.98 | 179 UKIP-type cells predicted to 0, left out of u |
| 2017→2019 | 2,870 | +0.12 | 3.65 | 4.15 | 0.81 / 0.97 | Speaker moved: Buckingham and Chorley both out |
| 2019 notional→2024 | 2,611 | +0.57 | **5.71** | **6.30** | 0.84 / 0.95 | Reform and Brexit Party left out (no match in the other election) |
| **Pooled** (user decision) | 11,304 | σ_λ **0.38** (mean +0.34, not applied) | **4.41** | **4.90** | 0.82 / 0.96 | |

No cell was dropped for lack of seat noise (p̂ = 100). Per party in 2019→2024: Con λ̂
+0.82 (the Conservative collapse was strongly proportional), LD +0.82, Lab +0.12,
Green +0.18. 2019→2024 has the widest seat spread by far, with tactical voting and the
uneven Reform surge both landing in the residual.

The tests check this against the engine itself. Synthetic residuals are built on real
`SimulationInputs`; the engine's own `error_scales` and draws are then run at the
calibrated raw σ. The centred seat noise has the residuals' realised spread to within
3%, and each party's national error matches its ratio to within 1%.

**decided_fraction = 0.80** (0.7987): decided base ÷ sample size averaged over 34 polls
(More in Common 0.75 × 15, Survation 0.81 × 13, Focaldata 0.89 × 4, BMG 0.86, Ipsos
0.84). Opinium (base = sample size) and Find Out Now (weighted-only bases) are left out.

**Config written:** σ_nat 2.94, σ_seat 4.90, σ_λ 0.38, decided_fraction 0.80. The rest
is unchanged.

**Still not calibrated:** σ_reg 1.5 (a guess; it is also centred by the engine, so its
effective size on a leading party is about 1.2), design effect 1.5 (literature),
likely_voters_fraction 0.9 (a guess), and the settings (cap, interval, p_ref,
thresholds). The config keeps whatever values the existing file has for these, so a
hand-set value survives a re-run.

**For your review:** 2019 notional→2024 alone would give a raw σ_seat of 6.30. Pooled,
it is one of four.

---

## Piece 5: Storage

**Why it exists.** The daily runs need somewhere to keep their results, both for the
tracker's history and for the console.

**What's stored**, in a new table with one row per model run:
- **National block, every run:** odds, vote bands, seat ranges and seat histograms. This
  is what the Chances history and the vote bands are drawn from.
- **Regional and seat blocks, latest run only:** older runs' copies are cleared when a
  new run is saved. Keeping 650 seats × every day would bloat the database for little
  use.
- **The error budget** (piece 6 fills it) sits inside these blocks.

**After it, you can check:** tests show run N+1 clears run N's seat data but keeps its
national block, and deleting a run deletes its row.

**Worth questioning:** whether you'd ever want regional history, e.g. "how has Scotland's
seat range moved?". Today only national history is kept.

**As built.**
- **The table** `model_uncertainty` has one row per model run: `election_id` (the run),
  `schema` ("pf-model-uncertainty-v1"), and three JSON columns. `national_json` is always
  there; `regions_json` and `seats_json` can be empty. The JSON is stored compactly with
  sorted keys, so the same summary always gives the same text. The format tag is a column,
  not a key in the JSON, so the blocks are exactly what the engine produced. If the format
  ever changes, old rows can be told apart and the reader refuses them.
- **"Latest" means latest by run date, not the run saved last** (your decision). Every
  time a run is saved, with or without a summary, every other UNS run on that map loses its
  region and seat blocks, and the latest-dated run keeps them. So the order runs are saved
  in never changes the result:
  - Re-running an older date (a backfill) leaves today's detail alone. The older run keeps
    only its national block.
  - Saving a newer run without a summary (e.g. with uncertainty switched off) clears the
    older runs' detail, because the newest run is the one that counts.

  National blocks are never cleared. Holyrood and US runs are separate scopes, so they are
  never touched.
- **Runs without a summary store nothing**, and on a database that hasn't been migrated
  they don't touch the table at all. Once it exists, they still apply the pruning rule
  above. One more thing happens to the table on such runs. When a date is re-run or deleted,
  that date's old summary is deleted with it. Without this, SQLite can hand the deleted
  election's id to the next run, and the stale summary would then be attached to that run.
- **Saving a summary before the migration has run** stops with "run
  scripts/migrate_add_model_uncertainty.py first", and nothing is written.
- **Deleting a run from the console** also removes its summary, and still works before the
  migration.
- **Console:** the hub now has a status line, e.g. "12 runs have stored uncertainty; full
  detail on: UNS 2026-10-14". It says "no run holds full detail" when none does (e.g. after
  the detailed run was deleted), and "table not migrated yet" before the migration.
- **To apply it:** back up, then run `scripts/migrate_add_model_uncertainty.py`. Try
  `--dry-run` first: it opens the database read-only and prints the SQL.

---

## Piece 6: Wiring the engine into the daily model

**What a run does:**
1. Build the central projection using the existing additive seat calculation, with
   three explicit user-approved input/policy corrections:
   - Normalize each Lord Ashcroft national/regional party column to 100. Its actual
     decided n is the all-adult base times the original party total divided by 100;
     do not apply the generic decided fraction again. Stored source data is unchanged.
   - Exclude entirely empty regional columns before adding any weights. These are
     placeholders, not observations of zero support. Genuine zero party shares in
     reported regions still contribute.
   - Hold Northern Ireland at baseline vote shares. GB-poll NI columns collapse its
     parties into Other and cannot support party-level swings. Apply no GB polling
     swing, national fallback or national/regional polling errors there; retain
     existing seat-level simulation noise and label this limitation in diagnostics.
2. Retain each contributing poll's weight, sample-size source, adjustment and n_eff.
   Calculate per-party sampling variance, summed n_eff and Kish effective poll count.
   Missing respondent counts stop uncertainty with a repair message; they do not
   silently remove polls or cause national-variance substitution.
3. Run 10,000 simulations in batches of 1,000 by default, using the date-derived seed
   and calibrated settings. `--simulations N` changes the count.
4. Save national/regional/seat summaries and diagnostic inputs with the central
   projection. Save raw simulations atomically after the database commit, tagged
   with that election id. A file failure reports the already-committed election and
   leaves the previous raw file intact; database and file are separate transactions.
5. Retrospectives and automatic gap backfills save summaries for every date, but
   only the final date writes raw simulations.

**The error budget.** Store both the raw systematic settings and the uncertainty
actually delivered after masking and centring. Regional sampling is drawn separately
as a zero-sum vector, preserving its requested marginal variances where feasible;
record whether the covariance fit was exact. National diagnostics combine regional
errors by turnout while preserving the correlation of the shared national draw.

The diagnostic combined SD and normal swing band describe polling errors before
seat noise, lambda, clamping and renormalisation. Simulated vote/seat intervals
include those effects. This distinction keeps the later console explanation honest.

**Controls and compatibility:**
- Daily CLI/console runs enable uncertainty by default. Existing direct Python
  configurations retain deterministic behavior unless passed uncertainty settings.
- `--no-uncertainty` skips uncertainty; `--no-raw-sims` keeps summaries without the
  raw file. `SIMULATIONS_PATH` controls that file's location.
- `--dry-run` prints odds and seat ranges without database/raw/trend writes. An
  explicitly requested `--output-csv` retains the existing preview behavior.
- The tests preserve existing deterministic expectations and separately cover each
  approved input/policy correction, flags, diagnostics and write-failure behavior.

**As built / verification:** see the piece 6 entry in `plan.md` Notes. The console
views and public exports remain later pieces. Stop after verification for user review;
review workflows and committing require the user's next instruction.

---

## Piece 7: Console diagnostics: how to debug the model

**Why it exists.** So you can see *why* the model says what it says, before anything is
published.

**The views, and how to use them together:**
1. **Error budget.** Start here when a range looks wrong. It shows which error component
   dominates. Wide Scottish ranges driven by sampling would mean small crossbreaks;
   driven by σ_reg would mean the guess is doing the work.
2. **Simulation results.** The headline odds, seat ranges and histograms, alongside the
   published projection. The per-seat table is sorted most competitive first: these are
   the tossups.
3. **Odds history.** How the odds moved day to day. A sudden jump should line up with a
   poll or a config change.
4. **Simulation explorer:**
   - **Filter:** e.g. "Labour majority". It tells you how many simulations match (that
     is the probability) and how those simulations differ from the rest. If matching
     simulations have mean national Labour error +2.8pt, that is the average error
     among those simulations. It is an association, not a necessary threshold for
     winning or proof of causation.
   - **Inspect one simulation:** its exact errors and λ, its national result, and the
     list of seats that flipped compared with the published projection.
   - **Seat drill-down:** for a seat, how often it flips and which errors drive the
     flips. These comparisons describe associations within the simulations.
   - **One seat in one simulation:** trace baseline, swing, lambda, national, regional
     and seat errors through clamping and normalization to the winner. Exact replay
     uses a saved original-input/config snapshot and verifies its result. Older raw
     files without that snapshot remain browsable with replay clearly unavailable.

**Interpretation:** polling-error normal bands and full simulated outcome intervals
are separate quantities. Show raw versus applied centred SDs, geographic exclusions,
and NI's unchanged baseline with seat-level noise. Regional majority means more than
half that region's seats; largest-party ties split credit. Older summaries that lack
regional odds show that they were not stored.

**Worth questioning:** which views you actually use. Prune the rest later; each is
self-contained.

---

## Piece 8: Export

**Why it exists.** The website can't read the database, so the results are written to
JSON files.

**What's published:**
- **Trend file:** gains a vote band and odds per day, for the tracker.
- **Per-seat file:** gains each party's share range and win % per seat, for the popup.
- **New uncertainty file:** national and regional odds and ranges, for the headline and
  vote totals table.

Raw simulations and the error budget are **not** published; they stay in the console.

**After it, you can check:** with no uncertainty data, exports are byte-identical to
today's. That proves nothing changes for runs without it.

**Worth questioning:** file sizes (the trend file stays well under 300 KB), and whether
anything in the public files could be misread without the "if an election were held
today" label.

**As built** (numbers from the live UNS 2026-10-12 run, exported into a scratch copy of
`electionmaps/data`; nothing in the repository's data was written):
- **One whitelist.** `model_support/public_uncertainty.py` copies named fields only, so
  the error budget, seed, config and input hashes never leave the console. Published
  precision: vote shares 1 dp (as the tracker's `v`), probabilities 0–1 to 4 dp (as
  stored), seats as integers. A malformed stored field stops the export or trend rebuild
  with a message instead of publishing a guess. The histogram counts must add up to the
  number of simulations.
- **Trend file.** Each party gains `vl`/`vh`, plus `pm` and `pl` when they are not
  zero; the entry gains `hung`. A missing `pm`/`pl` in an entry with `hung` means 0.
  The 2026-10-12 entry now reads, for Labour, `"s":323,"v":26.8,"vl":22.8,"vh":30.6,
  "pm":0.306,"pl":0.9697`, with `"hung":0.6938`. It grows by 315 bytes (119,720 →
  120,035 bytes today, since only one run has a summary).
- **Days that look the same still collapse.** The seed changes every day, so two days
  with the same seats and shares always have slightly different odds. Such days still
  collapse to the first one, as now. The one exception: a day with odds is kept after an
  equal day without. So a full backfill keeps today's 323 points of 824 runs, and the
  file comes to about 221 KB (a synthetic 830-run test gives 228 KB). If every day were
  different it would be about 565 KB, but without uncertainty that case would already be
  about 305 KB.
- **Per-seat file.** Each seat gains `u: [[party, lo, hi, win], ...]` in the same order
  as `p`. All 650 seats get rows: 3,793, one for every `p` party. The file grows from
  112,539 to 188,273 bytes. Hexham, for example: Labour 29.1–46.7 win 0.5009,
  Conservative 29.2–46.4 win 0.4991. A `p` party can lack a row: that happens when its
  range stays below 0.5 points and it never wins (none do today). Going the other way,
  154 stored seat ranges belong to parties with no projected votes in that seat; none of
  them ever wins, and they are not published. Piece 9 should show a `p` party without a
  row with no range.
- **`results/prediction-uncertainty.json`** (`pf-uncertainty-v1`, 8,996 bytes) holds:
  - the run's name and date, its 10,000 simulations and the 0.9 interval;
  - a `basis` line: "Nowcast: if an election were held today. 90% ranges and
    probabilities from 10,000 simulations, conditional on current polls and the model's
    error assumptions.";
  - the national figures. Labour's vote is 22.8–30.6, its seats 220/301/376, P(majority)
    0.306, P(largest) 0.9697, plus a 328-bin seat histogram. Reform's are 18.9–26.8,
    44/109/201, 0.0002 and 0.0279. P(hung) is 0.6938;
  - all 12 regions with vote and seat ranges, and the regional odds the run stored. In the
    South East, for example, Labour is largest at 0.6112 and hung is 0.9877. Regional
    "majority" means more than half that region's seats.
- **Which run is "current" (review fix, your decision).** Everything now uses one rule:
  the latest-dated run. That is the date in the run's name, with the database id only
  breaking a tie between runs of the same date.
  - It decides which run keeps the detailed summary, which run the console calls
    detailed, which run the export and the console panel ship as the current prediction,
    and whether a model run writes the raw-simulation file and the "latest poll used"
    metadata.
  - So re-running an older date never replaces today's prediction, raw file or metadata.
    The model prints "Raw simulations not written: … is not the latest dated run" and
    "Trend metadata not written: …" instead.
  - A model run whose name has no date can no longer be the current prediction.
  - The US forecasts still use their old rule, the highest id.
- **Free checks.** Majority odds plus hung must add to 1, and so must the largest-party
  odds. The allowance is the rounding: half a unit in the fourth decimal place per term.
  Each party's seat range must also lie inside its histogram, whose first and last bins
  must be non-empty. The same checks apply to regions where the odds are stored. The
  live run passes them.
- **Trend dedup, both directions.** A day with odds is kept after an equal day without
  them. An equal day without odds after a day with them is dropped, and the earlier odds
  carry forward.
- **Old or missing uncertainty.** The export writes the file only when the exported run
  has its regional detail (a real write now prints "Wrote uncertainty: … (N regions)"). Otherwise it removes an old file, so stale odds can never sit
  beside a newer projection. The front end must treat a missing file as "no uncertainty".
- **Manifest.** The Westminster shell gains
  `"uncertaintyDataPath": "results/prediction-uncertainty.json"`. Regenerating
  `map-modes.json` (in a scratch copy) changes exactly that one line. The real
  regeneration waits for the publish step.
- **No uncertainty, no change.** Exports and the trend file are byte-identical to today
  when there is no stored uncertainty:
  - The pinned goldens were made by the old code (b10516b9) on a fixed test scenario.
  - On the live data, the old code and the new code (with uncertainty switched off)
    wrote identical trees and printed identical output for the full export, and built
    identical trend files.
- **Console.** The uncertainty hub has a "Published export" panel. It shows the run the
  export would ship, the basis line, each file's exact size and what it holds, and
  which stored fields are never published. That list is built from the same field
  lists the export uses, block by block: national, national parties, regions, region
  parties and seat parties. Live, it shows national: budget, config_hash, input_hash,
  seed; regions: budget; and nothing at the party level. It uses the export's own functions in memory
  and takes 0.26 s on the live data. Its sizes match a real export byte for byte.

---

## Piece 9: Maps page: popup, vote totals, headline

**What readers will see:**
- **Seat popup:** "Lab 32% (27–37) · win 71%". The range is the 90% interval for the
  share; win % is the share of simulations Labour won.
- **Vote totals table:** a "90% seats" column (e.g. 248–331) and a vote-share range.
  Shown nationally or for a single selected region; hidden for other filters, because
  ranges for arbitrary seat selections weren't computed.
- **Headline:** "Reform largest party 78% · Reform majority 31% · Hung 64% (if an
  election were held today)".

**Worth questioning:** wording. Does "win 71%" read clearly to a casual visitor? Should
the ranges show as "27–37" or "±5"? Ranges are not always symmetric, so "27–37" is more
honest.


**As built (2026-10-21, awaiting manual review):** display values use at most
two decimal places. The headline leader is selected by P(largest), independently
of central seats. To prevent mixed cached runs, both exported files now carry
a matching fingerprint of public contents; a missing or mismatched summary
hides aggregate ranges and odds while leaving central results usable. Seat
bands remain self-contained in the seat payload. The totals table scrolls
horizontally on narrow panels.

A fresh 1000-draw scratch run for 2026-10-19 showed Labour largest 98.2%, Labour
majority 32.7%, Hung 67.3%. These are test-preview values from a copied database;
the live database and published data were not updated.


**UI refinement agreed 2026-10-21:** explanatory text appears once beside the
headline odds, not inside seat popups or above vote totals. Show uncertainty
beside Show all defaults off; enabling it reveals the interval columns, with
90% seats after the seat-change column. Popup bands remain visible. National
and region-only ranges remain supported; party/majority filters hide ranges
and retain ordinary filtered central totals.


Approved and committed locally as `dc5426f5` on 2026-10-21. The explanation
uses the same font, size and bold weight as the other subtitle lines.

---

## Piece 10: Poll tracker: bands and Chances

**What readers will see:**
- A shaded band around each party's vote-share line: the 90% range on that day.
- A "Chances" view: lines for each party's majority chance (or largest-party chance)
  over time, plus a hung-parliament line.

**How to read it:** the band is uncertainty about where opinion *is now*, not a forecast
of where it's heading. Chances lines move when polls move, and also when the
deterministic lead crosses a seat threshold. Small poll shifts near 326 seats can move
odds a lot; that's real, not a bug.


**As built (2026-10-21, awaiting manual review):** Seats & votes is the
default view. Chances reuses the party/date controls with a Majority/Largest
party switch and a separate Hung line. Calendar gaps carry forward the last
reading; a saved run without uncertainty clears optional values. Earlier
dates remain without bands/odds until the operational backfill. Isolated
observations get visible points/range whiskers. Percentages use at most 2 dp.

The scratch preview's 2026-10-19 Labour tooltip shows majority 32.7% and
largest party 98.2%, with Hung 67.3%; these match the saved scratch run.
No live data was changed.


**Seat-band extension requested 2026-10-21:** add the stored 90% seat-count
range around each solid seat line, alongside the existing vote-share bands.
Each band follows its metric checkbox and uses the appropriate axis. Hover
details show seat-count endpoints (e.g. Labour 220–380 seats in the scratch
2026-10-19 run), with no change to the model itself. Older exports without
seat bounds remain supported; the preview cache is rebuilt from copied data.

---

## Piece 11: Backfill and publish

**What happens**, in order, each step with your go-ahead:
1. Back up the database.
2. Run the migrations.
3. Repair sample sizes (dry run first).
4. Backfill decided bases (coverage report first).
5. Run calibration and **read its report**.
6. Re-run the model for every day since Jul 2024.
7. Rebuild trends and export.
8. Sanity-check in the console before anything goes live.

**Sanity checks before publishing:**
- A rock-solid safe seat should show >95% win.
- A true marginal should be roughly 40–60%.
- A 30% party's national 90% vote band should be about ±3–4pt.
- The published seat count should sit inside its range.
- Odds history should move smoothly, without unexplained jumps.

**If something's wrong:** the backup restores the database, and the old export files are
in git.

Piece 10 default refinement (2026-10-21): tracker opens in Seats & votes view with Seats checked and Vote % unchecked. Users can enable Vote % independently. Fresh-load DOM checks passed with uncertainty and legacy data; assets rebuilt. Still uncommitted.

Piece 10 band styling (2026-10-21): strengthened seat/vote-band fills and added thin party-colour borders; isolated range whiskers also made clearer. Central lines remain thicker and bands do not intercept pointer events. Assets rebuilt and uncertainty/legacy DOM smoke checks passed. Still uncommitted.

Piece 10 band fade (2026-10-22): user requested shading to weaken with distance from the central line. Both seat and vote fills now fade towards their interval boundaries while retaining party-colour outlines. The fade follows the central curve at each date; interval bounds and missing-data gaps remain unchanged. If the central estimate lies outside its interval, the fill anchors at the nearest boundary. This is a display treatment, not a reconstructed probability density. Assets rebuilt and JS/DOM checks passed; still uncommitted.

Piece 10 final band treatment (2026-10-22): removed party-colour interval borders at user request. Full interval base fill is 20% opacity; 16 evenly spaced nested ribbons at 1.2% opacity each produce approximately 21% at the outer edge and 34% centrally. This is a gently nonlinear, stepped alpha-compositing fade, not an exact linear or quadratic scale. Central lines and isolated range markers remain. Focused renderer tests and actual/legacy tracker DOM checks passed; assets and scratch preview refreshed. Still uncommitted.

Piece 10 opacity refinement (2026-10-22): user increased central shading target to approximately 75%, superseding the earlier 34%. Full interval base remains20%; sixteen inset ribbons at7% opacity combine to74.95% centrally. Ribbon widths16/17 through1/17 leave the outer edge at20%. Borders remain absent. Focused tests and uncertainty/legacy DOM checks pass; assets/preview refreshed. Still uncommitted.

### Piece 10 approved commit — 2026-10-22

Committed locally as `423a8419`. Final approved rendering uses borderless bands with 20% opacity at the edges and approximately 75% at the central line; seats are enabled and votes disabled by default. This supersedes earlier intermediate styling notes.

### Piece 11 handover clarification — 2026-10-22

Begin with a read-only readiness audit of the live schema, repaired sample sizes, decided bases, configuration and historical coverage; reconcile work already completed before proposing any repeated migration or repair. Use a backed-up scratch database and representative historical runs to establish failures and runtime before the full backfill. Historical runs apply the current model assumptions to historical polls; they are not archived forecasts or independent calibration evidence. Unresolved contributing sample sizes must stop with a repair message.

Each live-database operational step requires its own go-ahead. Rebuild trends and stage exports locally for inspection. Investigate unexpected central-versus-interval discrepancies and unexplained odds discontinuities; do not require the deterministic central value to lie inside every simulated interval or force odds to be smooth. Remote publication remains a separate explicitly authorized action. No piece 11 operations were started by this commit handover.

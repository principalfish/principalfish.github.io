# Plan: add-monte-carlo-uncertainty-to-westminster-projection

## Context

**Goal.** The Westminster projection is fully deterministic: one share and winner per
seat, one seat total per party. Add an uncertainty layer that shows how sure the
projection is: 90% ranges on vote shares (national, regional, seat), seat win
probabilities, seat-count ranges, and P(majority) / P(largest party) / P(hung). The
existing point estimates stay exactly as they are.

**How it works today (verified).**
- **Polls.** `Poll.sample_size` (`data/models.py:412`) is present on all 374 Westminster
  polls, and 346 of them carry 12-region crossbreaks (`PollRow.region_id`, regions 13–24
  on map 2). Crossbreaks have no n of their own. `regions.population` is populated for
  all 12 regions. No model reads `sample_size`.
- **Model.** `data/models/westminster/run_uns_model.py`:
  - `collect_poll_shares` (~L799) computes an exp-decay × pollster-weight average
    (`model_support/polling.py` `PollAggregation`, L120).
  - `compute_region_diffs` (~L956) takes regional poll share − regional baseline, falling
    back to the national average.
  - `project_seat_votes` (~L1057) applies an additive swing, clamps at 0, renormalises
    and takes the argmax.
  - `persist_projection` (~L1250) writes through `model_support/persistence.py:replace_output`
    to one `elections` row (`model_uns`, "UNS YYYY-MM-DD") plus `votes` rows.
  - `publish_trends` / `trend_batch` (`model_support/trends.py`) rebuild
    `electionmaps/data/results/model_output_trends.json` from the DB.
  - `run_retrospective` (~L378) re-runs per date.
- **No randomness anywhere in `data/`.** numpy 2.5.3 is in the venv only via shapely and
  is not in `requirements.txt`. scipy is absent.
- **Export.** `scripts/export_elections.py` (~L335–390, 422–430) writes
  `results/prediction-simulation.json` (`pf-results-v4`, `scripts/export/payload.py:72-183`),
  which already has optional per-seat keys (the `ev` precedent). `parliamentFeatures` in
  `electionmaps/data/map-modes-shell.json` is copied through verbatim.
- **Front end.** The model view renders like any election:
  - `Seat.fromRaw` / clone constructor (`electionmapslogic/state.js:328-359`) **drop
    unknown fields**.
  - Seat popup: `renderSeatPopup` / `buildPopupRow` (`dom.js:1371`, `1200-1233`).
  - Vote totals: `renderVoteTotals` (`dom.js:605-668`, markup `shell.html:149-161`, fed by
    `state.filteredSeatsSummary`; it re-summarises the filtered seats).
  - Subtitle: `ElectionSummary.#subtitleText` (`state.js:841`) → `renderSubtitleText`
    (`dom.js:80`).
  - Poll tracker: `features/polltracker.js:34 parsePollTrackerData` plus
    `polltracker-view.js` (D3; `pollTrackerDrawLines` :472, tooltips :405, fragment
    `fragments/polltracker.html`). It plots model output lines, not individual polls.
- **Data bugs.**
  - Techne's live regex `\d{3,5}` (`polls/importers/westminster/techne_import.py:245`)
    stores n≈16,4xx (true ≈1,64x).
  - BMG's parser is fixed (`bmg_research_import.py:555-600`), but older stored rows hold
    n≈150k (e.g. `152118` = "1,521" + "18+").
  - Sample size is part of importer duplicate detection, so repaired rows must match what
    the fixed parsers produce.
- **History for calibration.** Map 1 has the 2010, 2015, 2017 and 2019 generals (same
  boundaries). Map 2 has the 2019 notional and 2024 generals.

**Settled decisions (from discussion; do not re-litigate).**
1. **Scope.** Westminster only. Generic core lives in `data/model_support/` so
   Holyrood/US can adopt it later.
2. **Point estimates unchanged.** Keep the decay-weighted average and additive regional
   swing. Sample size is **not** added to the average's weighting. Uncertainty is a layer
   on top.
3. **Error model.**
   - Sampling error of the weighted average comes from per-poll effective n
     (n ÷ design effect).
   - Plus a systematic error, correlated national → regional → seat.
   - Run as a Monte Carlo in Python inside the Westminster model run. Nothing runs in the
     browser.
4. **Calibration.** Seat-level noise is empirical, from historical seat-vs-region swing
   residuals in the DB. National and regional systematic error come from the literature
   (≈2pt). All parameters live in config.
5. **Data fix.** Fix the Techne parser, repair stored BMG/Techne rows, and add a
   defensive per-poll n_eff cap.
6. **Intervals.** 90% (5th–95th percentile) everywhere.
7. **Outcomes.** P(majority ≥326) per party; P(largest party) per party; P(hung); seat
   median, 90% range and full distribution per party. The distribution is exported as
   data but not rendered this round.
8. **Storage.**
   - National outcomes, share intervals and seat ranges are kept for **every** daily run.
   - Per-region share intervals and seat ranges, plus per-seat win probabilities and share
     intervals, are kept for the **latest run only**.
9. **Backfill.** Re-run the retrospective model over the full tracker range (since Jul
   2024).
10. **Seat popup.** Each party row shows its 90% share range and win %. Model elections
    only.
11. **Vote totals table.**
    - Adds a seat 90% range column and a vote-share 90% range.
    - Shows national figures when unfiltered, and that region's figures when filtered to
      exactly one region.
    - Hides the columns under any other filter.
12. **Headline.** Keep today's subtitle and append one compact line, e.g.
    "Reform largest party 78% · Reform majority 31% · Hung 64%".
13. **Poll tracker.**
    - A shaded 90% band around each party's vote-% line.
    - A view toggle "Seats & votes | Chances". Chances has a 0–100% axis, a line per
      party, a Majority/Largest switch and a Hung line.
14. **Per-poll CIs.** ±MoE per poll is shown in the local data console only. No public
    per-poll export.
15. **Nowcast framing.** The probabilities mean "if an election were held today". There's
    no time-to-election drift term. The headline line and the Chances view carry that
    label, e.g. "(if an election were held today)".
16. **σ_nat comes from curated UK history.**
    - A committed data file, `data/models/westminster/uk_final_poll_errors.json`, holds
      the final-poll average vs result per party for each GE from 1992 to 2024.
    - Parties: Con, Lab, LD, plus UKIP/Brexit/Reform, Green and SNP (GB share) where
      available.
    - Each election entry carries its source URL (BPC or Wikipedia "Opinion polling for
      the YYYY UK general election"). As built (user decision, piece 4): Mark Pack's
      PollBase is the single source; it has no SNP column.
    - The calibration script derives σ_nat from it. It replaces the literature constant.
17. **Swing-type uncertainty.**
    - Calibration fits how far historical seat swings were proportional rather than
      additive.
    - Each simulation draws a proportionality factor λ shared by all seats, so seats move
      together.
    - σ_seat is the residual left after the λ fit.
18. **Zero-sum party errors.** Each error vector (national, regional, seat) is centred
    before use as e_k − p_k·Σ_j e_j. An overstated party implies others are understated,
    and small parties stay small.
19. **Console diagnostics for every stage, built piece by piece.**
    - Four views, all built initially: Calibration report, Error budget, Simulation
      results, Odds history. Plus per-poll ±MoE in the existing poll views.
    - Each view ships in the same piece as the stage it diagnoses (or the piece right
      after), so every stage can be inspected in the console before the next is built.
    - Each view is a self-contained route and template in one new blueprint, so it can be
      removed later without touching the others.
20. **n behind the shares = the unweighted decided base.**
    - The voting-intention table's own base (respondents who named a party, i.e. the sum
      of party counts) is used nationally and per region. Headline shares exclude
      don't-knows, so `polls.sample_size` (total respondents) overstates n. For example,
      More in Common reports 1491 decided against a sample of 2113.
    - Captured at import into a new `poll_bases` table, unweighted plus weighted where
      printed. deff still covers weighting.
    - Existing polls are backfilled by re-fetching `source_url`.
    - Fallback: national → `polls.sample_size`, regional → population estimate.
21. **Raw simulations are stored and explorable.**
    - Each simulated election of the **latest run** is kept: seat totals, national vote
      shares, λ, the drawn national and regional errors, and every seat's winner and
      winning margin.
    - It is replaced on every run. Older runs keep only their summaries.
    - The console gets a **Simulation explorer** with three views:
      - filter sims by outcome;
      - inspect one sim;
      - seat drill-down.
    - No CSV export.
22. **Console transparency is a standing requirement (user, 2026-10-13).** Every stage
    shows its intermediate numbers in the data console, so the user can follow exactly
    how a figure was produced (e.g. "Labour NE −2 = national draw −1.5 + regional
    draw −0.5, each = SD × z, SD built from σ_nat·s(p), √V and σ_reg·s(p)"). Concretely,
    the run payload stores, and the console shows:
    - the full error budget per region × party;
    - per simulation, the decomposition of each region's effective swing;
    - per simulation and seat, the full decomposition from 2024 share to winner.

    Each new piece adds its own view of its intermediate values; anything a view needs
    is persisted or reproducibly derivable.

**Out of scope.** Holyrood and US; predict-mode uncertainty; a win-probability
choropleth; standalone regional seat-range views; individual poll dots in the tracker; a
Bayesian/Kalman aggregator and house effects; per-seat probability history charts;
rendering the seat histogram.

**Assumptions (confirmed as part of the final ledger).**
- **a. n per poll and region** comes from `resolve_poll_n` (piece 2): captured unweighted
  decided base, else national `polls.sample_size`, else regional estimate = poll n ×
  region population ÷ total population of the regions that poll reported
  (`regions.population`). Excluding NI when the poll lacks NI rows is part of this.
- **b. Error structure, per simulation.** Let `s(p) = sqrt(p(1−p)) / sqrt(0.3·0.7)`.
  - **Single poll:** `n_eff = min(n/deff, cap)`, `SE = sqrt(p(1−p)/n_eff)`,
    `90% MoE = 1.645·SE`.
  - **Weighted average:** `Var(avg) = Σwᵢ²·p(1−p)/n_eff,ᵢ ÷ (Σwᵢ)²`, using the model's
    existing weights wᵢ = decay × pollster weight. p is the averaged share. Regions that
    fall back to the national average use the national variance.
  - **National error:** `e_nat ~ N(0, (σ_nat·s(p))²)`, one per party, shared by all
    regions.
  - **Regional error:** sum of a centred systematic draw with raw SD `σ_reg·s(p)`
    and a separate zero-sum sampling draw. The sampling covariance is fitted to
    preserve each party's `Var(avg_r)` where feasible; otherwise it preserves total
    variance and records the fallback. See the sampling correction in Notes.
    Regions are independent; party errors within a region are correlated.
  - **Seat noise:** `e_seat ~ N(0, (σ_seat·s(p_seat))²)`, independent per seat and
    party.
  - **Centring:** systematic national/regional and seat vectors are centred as in
    Settled 18. The sampling vector is already zero-sum and is not centred again.
  - **Swing-type factor:** `λ ~ N(0, σ_λ²)`, one per simulation. A seat's swing for party
    k is `(1−λ)·Δ_R,k + λ·Δ_R,k·p_s,k/P_R,k`, i.e. additive at λ=0 and proportional at
    λ=1.
    - λ is centred on 0 so the MC stays centred on the published additive projection
      (Settled 2, assumption e).
    - The calibration report prints the historical mean λ for information. Moving the
      centre would change point estimates and is out of scope.
  - **Seat share:** `baseline + swing + e_nat + e_reg + e_seat`, then clamp ≥0 and
    renormalise exactly as `project_seat_votes` does.
- **c. Config defaults.** Design effect 1.5; per-poll n_eff cap 3000; σ_reg 1.5pt at a
  30% share (an informed guess; there's no data to calibrate it). σ_nat, σ_λ and σ_seat
  come from calibration, with fallback defaults of σ_nat 2.5pt, σ_λ 0.3 and σ_seat 3pt if
  the config file is missing. All are tunable.
- **d. Simulation count and seed.** 10,000 sims per run, with the seed derived from
  `as_of_date` (reproducible, deterministic tests, stable backfill). Sims run in chunks
  (e.g. 1,000) to bound memory: 650 seats × ~10 parties × 10k floats ≈ 520 MB unchunked.
- **e. Displayed seat count** stays the deterministic projection. The sims supply ranges
  and odds around it.
- **f. Storage.**
  - One new table keyed by model election id (FK `elections.id`) with a JSON payload:
    national block always; region and seat blocks nullable.
  - The national block holds outcomes and intervals plus the national error-budget
    inputs.
  - The region block holds per-region intervals and seat ranges plus the per region ×
    party error budget, all of it (Settled 22):
    - the poll average P̂, the central swing Δ and the 2024 share P⁰;
    - number of polls, Kish effective count and summed n_eff;
    - intended sampling SD √V, actual delivered sampling SD, covariance-fit
      exactness, and source (crossbreaks or national fallback);
    - s(P̂) and s(P̂ₖ national);
    - raw national SD σ_nat·s(P̂ₖ) and raw regional SD σ_reg·s(P̂), alongside the
      applied SDs after masking and centring;
    - regional SD combining delivered sampling and centred systematic variance,
      and combined SD including the applied national term;
    - seat SD σ_seat·s at the region's mean seat share;
    - the centring weight q;
    - flags: polled, fallback, λ-eligible;
    - the share of variance from sampling;
    - a 90% band on the region's effective swing (Δ ± z·combined SD).

    The national block holds national poll evidence and turnout-weighted applied
    error components. Shared national errors retain their cross-region correlation;
    independent regional components combine with squared turnout weights. The
    diagnostic swing band excludes seat noise, lambda, clamping and renormalisation;
    the simulated outcome intervals include them.
  - This feeds the console Error budget view.
  - When a newer run is persisted, older runs' region and seat blocks are nulled (pruned).
  - Rows are removed together with their election in `replace_output` / `delete_outputs`.
  - Needs a `migrate_add_model_uncertainty.py` in the `migrate_*.py` style: idempotent,
    `--dry-run`, one transaction.
- **g. Exports.**
  - `prediction-simulation.json` gains an optional per-seat key, staying `pf-results-v4`.
  - A new `prediction-uncertainty.json` carries national and regional data, wired via a
    `parliamentFeatures` path flag.
  - Trend entries gain vote-band and odds keys.
- **h. Headline "leader"** = the party largest in the most sims. Show the leader's
  P(largest), the leader's P(majority) and P(hung).
- **j. Raw-simulation store.**
  - Location: a compressed `.npz` at a new `SIMULATIONS_PATH` setting (`data/config.py`,
    `.env_example`), default `/home/philiph/dbs/simulations/westminster-latest.npz`. It
    sits beside the DB and off the Drive mount, not inside SQLite.
  - Rationale: it's about 10–20 MB and fully reproducible from the seed, so it needs no
    backup, and it keeps the DB and the daily Drive copy (Drive ~91% full) from growing.
  - Written atomically (temp file + `os.replace`, like `model_support/io.publish_json`).
  - Arrays (S sims, K parties, R regions, N seats):
    - `seats[S,K]` int16
    - `vote[S,K]` float32
    - `lam[S]` float32
    - `e_nat[S,K]` float32
    - `e_reg[S,R,K]` float32
    - `winner[S,N]` uint8 (party index)
    - `margin[S,N]` float16 (winner − runner-up share, pts)
  - Metadata: party, region and seat id lists; `election_id`; `as_of`; seed; config hash;
    and the deterministic winners, for "flipped vs projection".
  - The explorer warns if the file's `election_id` isn't the latest `model_uns` election.
- **i. Front-end fallback.** Everything degrades to today's display when the uncertainty
  data is absent, so old files and other parliaments are unaffected.

## Approach

Add a pure, typed numpy module, `model_support/uncertainty.py`, that knows nothing about
the DB:
- poll-average variance;
- a correlated error sampler;
- a vectorised version of the existing additive-swing projection;
- summarisers (percentiles, probabilities, histograms).

The Westminster model computes its usual deterministic projection, then hands the
baseline state, regional swings and per-key sampling variances to the module. It
persists the summary next to the existing outputs. Export and front end read the
summary.

The vectorised projection must reproduce `project_seat_votes` exactly when all errors
are zero. A test asserts this, so the MC is centred on the published projection.

**Alternatives rejected.**
- **Sampling error only.** Badly overconfident: real multiparty poll variance is about
  2× nominal (Chen et al., POQ 2023), and final UK polls miss by about 2pt on average
  (Jennings & Wlezien 2018).
- **Kalman filter aggregator.** It would move point estimates. Deferred to a possible
  later issue.
- **Full Bayesian (Stan/PyMC).** Heavy toolchain, untyped, hundreds of MCMC fits for
  backfill.
- **Browser-side MC via `predict.js`.** Duplicates the error model and is slow on mobile.
- **Independent seat errors only.** This is 538's 2016 lesson: it makes national odds
  far too confident. Hence the correlated national → regional → seat structure.

Prior art consulted: Curtice–Firth probabilistic seat prediction (BBC exit poll),
Hanretty/Lauderdale/Vivyan electionforecast.co.uk (2015), FiveThirtyEight correlated-error
simulation, the Economist's open-source `us-potus-model`, and Electoral Calculus seat odds.
## Pieces

1. **Repair poll sample sizes**
   - Files:
     - `data/polls/importers/westminster/techne_import.py`
     - new `data/scripts/repair_poll_records.py` (was `repair_poll_sample_sizes.py`)
     - `data/tests/test_techne_import.py`
     - new `data/tests/test_repair_poll_records.py` (was `test_repair_poll_sample_sizes.py`)
     - `data/requirements.txt` (pypdf pin)
     - add-on: `data/polls/importers/westminster/ipsos_import.py`,
       `data/polls/importers/westminster/lord_ashcroft_import.py`, `data/models.py`,
       `data/tests/test_ipsos_import.py`, `data/tests/test_lord_ashcroft_import.py`,
       `data/tests/test_polls.py`, `data/tests/test_us_poll_queue.py`
   - What:
     - Confirm the Techne PDF text layout from its test fixture or a sample, then fix
       `_parse_sample_size` so it reads only the unweighted count (≈1.6k).
     - Write a one-off, idempotent repair script with `--dry-run` that prints
       old → new for every changed row. It applies pollster-specific rules derived from
       the confirmed bug causes:
       - BMG: ≥6 digits ending "18" → strip the "18".
       - Techne: the rule matching the parser fix.
     - The repaired value must equal what the fixed parser yields, so dedupe stays
       consistent.
     - Use the `migrate_*.py` style: sqlite3, a single transaction, `DatabaseConfig`.
   - Functions: `_parse_sample_size` / `parse_unweighted_sample`, `plan_repairs(conn, fetch_pdf_text)`, `apply_repairs(conn, repairs)`, `main`.
   - Depends on: none.
   - Acceptance:
     - The Techne fixture parses to the true n.
     - The repair script's dry run lists only BMG/Techne rows, all landing in a plausible
       range (500–10,000).
     - Re-running is a no-op.
     - The user runs it against the live DB after a `data/scripts/backup_db.py backup` backup. The builder
       does not run it unprompted.

2. **Capture decided bases (n behind the shares)**
   - Files:
     - `data/models.py` (new `PollBase` ORM, `PollBaseKind`; `Poll.bases`)
     - new `data/scripts/migrate_add_poll_bases.py`
     - new `data/polls/importers/bases.py` (shared `ParsedBase`, `PlannedPollBase`,
       apportioning, `replace_poll_bases`); `data/polls/importers/types.py`
       (`PollImportResult.inserted_bases`)
     - the Westminster importers under `data/polls/importers/westminster/` (each gains
       `ParsedPoll.bases`, `ParsedPoll.national_shares()`, `ImportPlan.bases` and a
       public `fetch_parsed_poll(source_url)`)
     - new `data/model_support/poll_bases.py` (resolver + coverage)
     - new `data/scripts/backfill_poll_bases.py`
     - add-on: new `data/polls/importers/sanity.py` (SNP/Plaid alignment guard), new
       `data/scripts/repair_regional_poll_rows.py` (YouGov + Lord Ashcroft regional-row
       repair), fixtures `data/tests/fixtures/yougov/headline-20250804.txt` and
       `data/tests/fixtures/lord_ashcroft/tables-2025-06-30.json` /
       `tables-2026-09-28.json`, new `data/tests/test_importer_sanity.py`,
       `data/tests/test_repair_regional_poll_rows.py`; `data/console/services/wikipedia_queue.py`
       and `data/console/blueprints/poll_import.py` (bases in the result messages)
     - `data/console/blueprints/polls.py`, `poll_list.html`, `poll_detail.html`, new
       `poll_coverage.html`, `_poll_rows_table.html`, `import_preview.html`,
       `wikipedia_queue.html`
     - (`data/db.py` needed no change)
     - tests: each importer's test file (with fixtures),
       `data/tests/test_westminster_importers_commit.py`, `data/tests/uk_fixtures.py`
       (region populations), `data/tests/test_console_polls.py`,
       `data/tests/test_console_poll_import.py`, new `data/tests/test_poll_bases.py`,
       new `data/tests/test_backfill_poll_bases.py`, new
       `data/tests/test_migrate_add_poll_bases.py`
   - What:
     - **Schema.** Table `poll_bases`:
       - `poll_id` FK;
       - `region_id` FK nullable (NULL = national);
       - `unweighted_n` NOT NULL;
       - `weighted_n` NULL;
       - unique on (poll_id, IFNULL(region_id, 0)).
       - Migration: idempotent, `--dry-run`, one transaction.
     - **Importers.** Each one records the voting-intention table's own column base, i.e.
       the respondents who named a party (equivalent to summing party counts), for the
       national "All"/"Total" column and every region column it already reads shares
       from:
       - unweighted, plus weighted when printed;
       - known locations include the More in Common "Unweighted N" row
         (`more_in_common_import.py:432`), YouGov "Unweighted Sample"/"Weighted Sample",
         Ipsos "Unweighted Total"/"Weighted Total", and the Opinium, Focaldata and
         Survation base rows;
       - the builder confirms per importer from its fixture which bases are the decided
         VI base, and records any that publish none (e.g. if Techne gives national only);
       - extend each importer's fixture test to assert the captured bases.
     - **Resolver.** `resolve_poll_n(poll, region_id)` returns `(n, source)`, trying in
       order:
       1. captured unweighted base (`"base"`);
       2. national only: `polls.sample_size` (`"sample_size"`);
       3. regional only: the population estimate (`"estimate"`).
     - **Backfill.** Re-fetch each existing poll's `source_url`, parse with the importer's
       existing parser, and insert only `poll_bases` rows. Poll rows and shares are never
       touched. Failures (404, layout change) are logged and fall back to the resolver.
       Idempotent, `--dry-run`, with a per-pollster coverage report.
     - **Console.** The poll views show per row the n used, its source
       (base / sample_size / estimate) and the weighted n when present. There's also a
       coverage summary (% of polls and crossbreaks with captured bases, per pollster).
   - Functions: `resolve_poll_n(inputs, region_id, populations) -> ResolvedN`,
     `PollNIndex.from_records/load/resolve`, `poll_base_coverage`, `plan_poll_bases`,
     `apportion`, the importers' `_parse_bases`/`parse_bases`, `fetch_parsed_poll`,
     `plan_backfill(conn, fetch, pollsters)`, `apply_backfill(conn, items)`.
   - Depends on: 1 (so sample-size fallbacks are clean). Importers are independent, so the
     builder may split this piece per importer.
   - Acceptance:
     - Each importer's fixture yields the expected national and regional unweighted bases.
       For the More in Common fixture the national base is 1491, not `sample_size` 2113.
     - The resolver's fallback order is covered by tests.
     - The backfill dry run reports coverage and is a no-op on re-run.
     - The console shows the n source per row.
     - The live backfill run waits for the user's go-ahead after a DB backup.

3a. **From n to error: config, effective n, poll and average error, console ±**
   - Files:
     - new `data/model_support/uncertainty.py` (the config and error-input half)
     - `data/model_support/poll_bases.py` (`ResolvedN` gains structured `origin` /
       `basis` / `weighted_only`; `source` becomes a derived property)
     - `data/requirements.txt` (add `numpy>=2.0`)
     - new `data/tests/test_uncertainty.py`; `data/tests/test_poll_bases.py`,
       `data/tests/test_console_polls.py`
     - `data/console/blueprints/polls.py` and the poll templates (`poll_list.html`,
       `poll_detail.html`; `_poll_rows_table.html` needed no change)
   - What:
     - `UncertaintyConfig` frozen dataclass holding the defaults from assumption c, plus
       `simulations`, `interval=0.90` and `chunk_size` (used by 3b). It loads from
       `data/models/westminster/uncertainty_config.json` when present, else uses the
       defaults; piece 4 writes the calibrated values into that file.
     - **All-adult bases (user decision, piece 2 review).** `decided_fraction`
       (default 0.80, user decision: the poll-weighted mean of decided ÷ total base
       across pollsters that print both) multiplies any n whose source kind is
       `all_adults` (YouGov, Deltapoll, Techne, Lord Ashcroft, and
       `estimate:all_adults`) before the design effect.
       **Fallbacks count as all-adult too (user decision):** `sample_size` and
       `estimate:sample_size` are total samples including don't-knows, so they also get
       × `decided_fraction`. A missing base therefore never looks more precise than a
       captured one.
     - **Likely-voter bases (user decision).** `likely_voters_fraction` (default 0.9)
       multiplies any n whose kind is `likely_voters` (Ipsos regions, older BMG layouts,
       and `estimate:likely_voters`), because those bases still include undecideds.
       `decided` bases are used as-is. `split_from_macro` is provenance only (console
       "(split)" label); nothing downstream reads it.
     - `effective_n(resolved, cfg)`: kind adjustment, then ÷ deff, then the cap. It
       returns the adjusted n, n_eff and the factors applied, so the console can show
       each step.
     - `poll_moe(share, n_eff, cfg)`: 90% half-width.
     - `weighted_average_variance(entries)`: Σwᵢ²·p(1−p)/n_effᵢ ÷ (Σwᵢ)², and the Kish
       effective poll count (Σw)²/Σw².
     - `share_scale(p)`.
     - **Console: per-poll ±MoE.** Next to each party share in the poll views, show n
       (with its source), the kind adjustment, n_eff and ±90% MoE. Regional rows use the
       resolved n (assumption a). Show "–" when n is missing.
   - Functions: as listed.
   - Depends on: 2.
   - Acceptance:
     - A known-n MoE matches the analytic value (n=1000, p=0.5, deff=1 → ±2.6pt at 90%).
     - The kind adjustments apply only to their kinds, in order, and the cap holds.
     - Weighted-average variance matches a hand calculation, including unequal weights
       and the Kish count.
     - `share_scale(0.3) == 1`.
     - Config round-trips from JSON, and defaults apply when the file is absent.
     - A console test asserts that n, its source, the adjustment, n_eff and MoE render for
       national and crossbreak rows.

3b. **The simulation engine**
   - Files:
     - new `data/model_support/monte_carlo.py` (the engine; a sibling of
       `uncertainty.py` rather than its second half, see Notes)
     - new `data/tests/test_monte_carlo.py`
   - What:
     - `draw_errors(rng, ...)`: shared national, per-region and per-seat terms, each
       centred (zero-sum), plus the per-sim λ.
     - `project_seats_vectorised(...)`: swing blended by λ (additive ↔ proportional),
       errors added, clamp, renormalise, argmax over a sims axis.
     - `summarise(...)`:
       - per-party national share [lo, hi];
       - seat [lo, median, hi];
       - histogram;
       - P(majority ≥ majority_threshold);
       - P(largest), with ties split evenly;
       - P(hung);
       - the same per region;
       - per-seat share [lo, hi] and win probability per party.
     - Seed from `as_of_date` (e.g. its ordinal). Typed with `numpy.typing.NDArray` under
       strict mypy.
     - `run_simulations(..., keep_raw: bool)` returns the summary plus, when asked, a
       `SimulationDraws` dataclass of the raw arrays in assumption j's compact dtypes.
       They are filled chunk by chunk into preallocated arrays.
     - `save_draws(path, draws, meta)` / `load_draws(path)` write and read the `.npz`
       atomically.
   - Functions: as listed.
   - Depends on: 3a.
   - Acceptance:
     - The projection with zero errors and λ=0 is bit-identical to
       `run_uns_model.project_seat_votes` on a fixture.
     - Centred errors sum to ~0 across parties.
     - λ=1 reproduces a hand-computed proportional swing.
     - Probabilities sum to 1 across parties for "largest".
     - The same seed gives identical output.
     - Widths grow monotonically with σ.
     - Raw draws round-trip through save/load.
     - Seat totals recomputed from `winner` equal `seats`.
     - The raw arrays and the summary agree (e.g. P(majority) recomputed from `seats`).

4. **Error calibration (national, swing type, seat)**
   - Files:
     - new `data/scripts/calibrate_uncertainty.py`
     - new `data/scripts/build_final_poll_errors.py` (PollBase → curated table)
     - new `data/model_support/calibration.py` (pure fits, loaders, parameter notes)
     - new committed `data/models/westminster/uk_final_poll_errors.json` (curated)
     - new committed `data/models/westminster/uncertainty_config.json`
     - new committed `data/models/westminster/uncertainty_calibration_report.json`
     - new `data/console/blueprints/uncertainty.py`, templates `uncertainty_hub.html`,
       `uncertainty_calibration.html`; `data/console/__init__.py`, `home.html`,
       `static/style.css`
     - new `data/tests/test_calibrate_uncertainty.py`, `test_calibration.py`,
       `test_build_final_poll_errors.py`, `test_console_uncertainty.py`
   - What:
     - **σ_nat.**
       - Build `uk_final_poll_errors.json` from Mark Pack's PollBase (which includes
         non-BPC pollsters; no SNP column): for each GE 1992–2024, the equal-weight
         average of each pollster's final poll and PollBase's GB result per party, with
         the source per election.
       - Compute `z = (poll − result) / s(result)` for every party and election.
       - Realised σ_nat = √(mean over elections of each election's mean z²), not SD: a
         consistent industry bias is still error.
       - Convert to engine units: σ_nat = realised ÷ the engine's centring SD ratio
         (median over each election's two largest parties at the result shares).
       - Print a per-election table so outlier years (1992, 2015, 2024) are visible.
     - **λ and σ_seat.**
       - Use consecutive same-boundary general pairs: map 1 2010→15, 15→17, 17→19;
         map 2 2019 notional→2024.
       - Per seat s and party k: Δ_add = Δ_R,k (regional change) and
         Δ_prop = Δ_R,k·p⁰_s,k/P⁰_R,k.
       - Fit λ per pair (and per major party for the report) by least squares of the
         actual seat change on `(1−λ)·Δ_add + λ·Δ_prop`.
       - σ_λ = RMS of the fitted per-pair λ.
       - Realised σ_seat = robust SD (1.4826·MAD) of `(actual − fitted) / s(p̂)`,
         pooled across pairs, p̂ the seat's predicted share as the engine projects it at
         λ̂ (cells with p̂ = 0 left out).
       - Convert to engine units: σ_seat = realised ÷ the engine's centring SD ratio
         (median over each seat's two largest predicted shares). σ_λ needs none.
       - Exclude the Speaker's seat, the Others aggregate, parties absent in either
         election, cells with P⁰_R,k < `lambda_min_share` (1%), and cells where the party
         did not stand in the seat at the earlier election (p⁰ = 0).
     - Write σ_nat, σ_λ and σ_seat to `uncertainty_config.json` along with the
       literature/guess σ_reg, deff and cap. The model loads this file into
       `UncertaintyConfig`.
     - Also write a committed `data/models/westminster/uncertainty_calibration_report.json`
       holding every intermediate table:
       - per election × party: A, R, s(R), z;
       - per pair: λ̂ overall and per major party, n cells, residual median, MAD and
         σ_seat contribution;
       - pooled results;
       - exclusions with reasons.
     - **Console: Calibration view.** New blueprint `data/console/blueprints/uncertainty.py`
       (registered with the others) with a hub page and
       `/westminster/uncertainty/calibration` (template `uncertainty_calibration.html`).
       It shows:
       - every parameter in use, with its value, unit, source ("calibrated" /
         "literature" / "guess") and meaning;
       - all report tables.
   - Depends on: 3a.
   - Acceptance:
     - The script prints the per-election and per-pair tables (including mean λ) and
       writes the config; `--dry-run` writes nothing.
     - Synthetic DB and table fixtures with a known injected λ, residual SD and poll
       error recover each within tolerance.
     - Every entry in the curated table carries a source.
     - A console test renders the calibration view from fixture config and report files.

5. **Uncertainty storage**
   - Files:
     - `data/models.py` (new `ModelUncertainty` ORM)
     - new `data/scripts/migrate_add_model_uncertainty.py`
     - `data/model_support/persistence.py`
     - `data/tests/conftest.py` if needed
     - new `data/tests/test_model_uncertainty_persistence.py`
   - What:
     - Table `model_uncertainty`: `election_id` PK/FK, `national_json` NOT NULL,
       `regions_json` NULL, `seats_json` NULL.
     - `replace_output` gains an optional uncertainty payload, written in the same
       transaction, and prunes region and seat blocks on older elections in the scope.
     - `_delete_range` / `delete_outputs` remove matching rows.
     - Migration: idempotent, `--dry-run`.
   - Depends on: 3b (payload shape).
   - Acceptance:
     - Persisting run N+1 nulls run N's region and seat blocks but keeps its national
       block.
     - Deleting an output deletes its uncertainty row.
     - The migration is a no-op on a second run.

6. **Westminster model integration**
   - Files:
     - `data/models/westminster/run_uns_model.py`
     - `data/model_support/westminster_uncertainty.py`
     - `data/config.py`, `.env_example`
     - `data/tests/test_westminster_model.py`
     - `data/tests/test_westminster_uncertainty_integration.py`, `data/tests/conftest.py`
   - What:
     - **Lord Ashcroft renormalisation (user decision, piece 2 review).** Ashcroft's
       published shares include don't know / won't vote (national sums 61–72%). Before
       averaging, rescale each Ashcroft poll's shares (national and per region) over the
       parties so they sum to 100. Its n for those shares = all-adult base × the parties'
       total share (the actual decided count), and that n is not then also multiplied by
       `decided_fraction`. Stored data stays as published. Show this in the console error
       budget.
     - Exclude entirely empty regional columns before calculating central or
       uncertainty weights. Keep genuine zero party shares within reported regions.
       This additional input correction was approved on 2026-10-16.
     - Hold Northern Ireland at its baseline vote shares: the available GB-poll NI
       columns collapse its parties into Other. Apply no GB polling swing, national
       fallback, or national/regional polling errors there; retain existing seat
       noise. Record the exclusion explicitly in diagnostics (approved 2026-10-16).
     - An unresolved respondent count for a contributing nonzero average stops
       uncertainty with the poll/region to repair. `--no-uncertainty` remains usable;
       do not silently omit a poll's weight or substitute national V for missing n.
     - While aggregating, also accumulate the per (region, party) Σw² and per-poll n_eff
       needed for `weighted_average_variance`. n per poll and region comes from the resolver in
       piece 2 (assumption a).
     - After the deterministic projection, call the core MC with the baseline seat shares,
       regional swings, variances and config.
     - Pass the summary to `persist_projection` → `replace_output`.
     - CLI: `--simulations N` (default from config), `--no-uncertainty`, `--no-raw-sims`.
     - Write the raw-simulation file (assumption j) for the latest date only: a single run,
       or the final date of a retrospective. Earlier retrospective dates skip it.
     - Add the `SIMULATIONS_PATH` setting.
     - `run_retrospective` produces uncertainty per date.
     - `--dry-run` prints a short odds summary.
   - Functions: `collect_poll_shares`, `compute_region_diffs`, `run_simulation`,
     `persist_projection`, `run_retrospective`, `main`.
   - Depends on: 2, 3a, 3b, 4, 5.
   - Acceptance:
     - Deterministic projection mechanics and existing deterministic expectations
       remain unchanged. Ashcroft normalization, empty regional column exclusion,
       and the NI baseline rule are explicit user-approved input/policy changes.
       CLI assertions may change for the newly enabled uncertainty output.
     - A run on the test DB stores a national block with probabilities summing correctly.
     - The point-projection seat count lies inside the 90% seat range in a fixture
       scenario.
     - Runtime for one date is a few seconds or less.
     - After a run, the raw file exists with the run's `election_id`.
     - A retrospective writes it once, for its last date.
     - `--no-raw-sims` writes nothing.

7. **Console: run diagnostics (error budget, simulation results, odds history, simulation explorer)**
   - Files:
     - `data/console/blueprints/uncertainty_runs.py` and `uncertainty_explorer.py`;
       corresponding `console/services/` modules keep routes separate from calculations.
     - shared run/comparison templates and budget, results, history, simulations,
       simulation, simulation-seat and seat templates under `data/console/templates/`.
     - hub/model-output links, console blueprint registration and scoped histogram CSS.
     - `data/model_support/simulation_replay.py` plus optional snapshot persistence,
       input fingerprint and regional odds in `monte_carlo.py`.
     - `data/tests/test_console_uncertainty_runs.py` and core engine contract tests.
   - What: built before export so model output can be inspected before anything is
     published. All views read `model_uncertainty` rows.
     - **Error budget** (`/westminster/uncertainty/budget/<election_id>`, latest run): a
       region × party table of every field in the region block (assumption f), plus a
       national row. Each SD links to how it was built: σ value × s(p), with s(p) shown,
       and √V linked to the polls and n_eff behind it. Show raw versus applied
       (centred) SDs and explain geographic ineligibility and NI's baseline-only
       polling policy. The combined normal band describes polling-error swing only;
       it is not the simulated outcome interval, which also reflects seat noise,
       lambda, clamping and normalization. Worked explanations use stored numbers.
     - **Simulation results** (`/westminster/uncertainty/results/<election_id>`):
       - run metadata (as_of, S, seed, config hash);
       - national and per-region P(majority), P(largest), P(hung), vote 5/95 and seat
         5/50/95 per party;
       - a seat histogram per party as native SVG bars with an accessible count table;
       - the deterministic projection's seats beside them;
       - a sortable per-seat table (winner, win %, runner-up, runner-up %, share
         intervals), defaulting to most competitive first.
     - **Odds history** (`/westminster/uncertainty/history`): one row per daily run with
       P(majority), P(largest), P(hung) and vote bands per party, newest first,
       filterable by date range.
     - Older runs (region/seat blocks pruned) show only the national parts, with a note.
     - **Simulation explorer.** Reads the raw `.npz` via `load_draws`, cached by file
       mtime, and shows a mismatch warning per assumption j.
       - **Filter** (`/westminster/uncertainty/sims`):
         - Outcome filters, combinable: party majority, party largest, hung, and a seat
           range per party.
         - Shows the matching count and % of S.
         - Shows, for matching sims vs all sims: mean λ, mean `e_nat` per party, mean
           `e_reg` per region × party, and median seats and vote per party. These are
           conditional associations, not necessary thresholds or causal claims.
         - Lists the first 100 matching sims with a link to each.
       - **Inspect** (`/westminster/uncertainty/sims/<j>`):
         - that sim's λ, `e_nat` per party, and `e_reg` per region × party;
         - **swing decomposition per region × party:** central swing Δ + national draw
           (after per-region masking and centring) + regional draw = effective swing,
           with each draw also shown in SDs (z-score) and as "1 in N";
         - its national seats and vote vs the deterministic projection;
         - the table of seats whose winner differs from the deterministic projection
           (seat, region, projected winner → simulated winner, margin).
       - **Seat in a simulation** (`/westminster/uncertainty/sims/<j>/seat/<seat_id>`):
         the full decomposition for that seat, per party, from 2024 share, through
         uniform swing, the λ term, the national draw, the regional draw and the seat
         draw, to the pre-clamp x, clamped, renormalised share and winner. The raw
         `.npz` does not store all seat errors (≈340 MB). New raw files retain an
         immutable replay snapshot of the original inputs, config and engine version.
         Replay all original RNG streams from the seed to simulation j, including the
         region-masked national draw; the globally centred stored national vector alone
         cannot recover that draw. Verify replayed terms and results against stored
         draws before displaying a decomposition. Old files without snapshots remain
         browsable but show exact replay unavailable; incompatible versions or mismatches
         show an explicit message. Reachable from Inspect and the seat drill-down.
       - **Seat drill-down** (`/westminster/uncertainty/sims/seat/<seat_id>`):
         - the seat's central shares, margin, seat SD per party and the 90% band, with
           the region's error budget row linked;
         - winner distribution and margin percentiles;
         - the number of sims where it flips vs the projection;
         - mean λ, `e_nat` and its region's `e_reg` in flip sims vs all sims (what drives
           the flip);
         - links to sample flip sims.
         - Reachable from the per-seat table in Simulation results.
   - Depends on: 5, 6.
   - Acceptance:
     - Console tests render each view from a fixture DB with uncertainty rows.
     - The budget table's total SD equals √(sum of components²).
     - The seat-in-simulation view replays seat errors that reproduce the stored winner
       for every seat of a fixture run, and its decomposition sums to the pre-clamp x.
     - The history view lists every run that has a national block.
     - Explorer tests use a small fixture `.npz` (e.g. S=50, 6 seats). Filter counts match
       a hand count; the inspect view's flipped seats match the fixture; the seat
       drill-down flip count matches.
     - A missing or mismatched file shows a clear message rather than an error.
     - The smoke check `create_app()` passes.

8. **Export**
   - Files:
     - `data/model_support/trends.py`
     - `data/scripts/export_elections.py`
     - `data/scripts/export/payload.py`
     - `electionmaps/data/map-modes-shell.json`
     - `electionmaps/data/map-modes.md`
     - tests: `test_model_trends.py`, `test_export_payload.py`, `test_export_elections.py`,
       `test_export_manifest.py`
   - What:
     - Trend entries add `vl` / `vh` (vote band, 1dp), `pm` (P majority) and `pl`
       (P largest) to each party, plus an entry-level `hung`, read from `national_json`.
       Keys are omitted when no uncertainty row exists.
     - `prediction-simulation.json` seats gain an optional `u: [[pid, lo, hi, winPct], ...]`
       aligned to `p`.
     - New `results/prediction-uncertainty.json`, schema `pf-uncertainty-v1`:
       `{as_of_date, simulations, interval, national:{parties:{pid:{v:[lo,hi], s:[lo,med,hi], pm, pl, hist}}, hung}, regions:{rid:{parties:{pid:{v:[lo,hi], s:[lo,med,hi]}}}}}`.
     - Add `uncertaintyDataPath` to Westminster `parliamentFeatures` in the shell, then
       re-export.
   - Depends on: 5, 6.
   - Acceptance:
     - Export with no uncertainty rows produces byte-identical output to today.
     - With rows present, the new keys and file appear and validate in tests.
     - The trends file stays well under ~300 KB.

9. **Front end: popup, vote totals, headline**
   - Files:
     - `electionmapslogic/state.js`
     - `electionmapslogic/uncertainty.js`, `uncertainty-controller.js`
     - Export identity helper/calls in `data/scripts/export/uncertainty.py` and
       `data/scripts/export_elections.py`; matching console export-size preview.
     - `electionmapslogic/dom.js`
     - `electionmapslogic/shell.html`
     - `electionmapslogic/maps.css`
     - tests in `electionmapslogic/tests/` (`seat.test.js`, `state.test.js`, new as needed)
   - What:
     - `Seat.fromRaw` and the clone constructor keep `u`.
     - Pair public results and summary with `uncertaintyId`, a SHA-256 digest of
       their canonical public contents, so same-date cached exports cannot mix.
       No stored private input/config hashes or seed are published.
     - Lazily load `uncertaintyDataPath` for model elections with a stale-request guard
       (Senate-forecast `isCurrent()` pattern).
     - `buildPopupRow` appends "(lo–hi)" and "win NN%" when `u` is present.
     - `renderVoteTotals`: new "90% seats" and vote-range cells, controlled by a
       default-off "Show uncertainty" button beside "Show all". The seats range
       follows the seat-change column. Supported for national (unfiltered) or
       exactly one region filter; hidden otherwise or when data is absent.
     - `renderSubtitleText` appends the compact odds line (rule h) with the nowcast label "(if an election were held today)".
     - Rebuild with `npm run minify:electionmaps`.
   - Depends on: 8.
   - Acceptance:
     - Vitest covers field retention, popup formatting, column visibility under no filter,
       one region, a party filter and two regions, and headline text.
     - Real elections and the US page render unchanged.

10. **Front end: poll tracker bands + Chances view**
   - Files:
     - `data/model_support/public_uncertainty.py`, `data/model_support/trends.py`
       and corresponding export/trend tests.
     - `electionmapslogic/features/polltracker.js`
     - `electionmapslogic/features/polltracker-view.js`
     - `electionmapslogic/features/polltracker-uncertainty.js`
     - `electionmapslogic/fragments/polltracker.html`
     - `electionmapslogic/maps.css`
     - `electionmapslogic/tests/polltracker.test.js`
   - What:
     - `parsePollTrackerData` carries `seatLo` / `seatHi` / `voteLo` / `voteHi` / `pMaj` / `pLargest` per party
       and a `hung` timeline. Carry forward across calendar days without a run,
       but clear optional values at an explicit run lacking uncertainty or a
       party payload; never attach stale uncertainty to a newer central run.
     - Publish stored national seat interval bounds as optional compact trend
       fields `sl`/`sh`; accept earlier exports without them.
     - Draw a `d3.area()` band behind each enabled solid seat or dashed vote
       line, using that metric’s axis and including interval bounds in its domain.
       Tooltips show the 90% seat-count range and vote-share range when available.
     - Toggle "Seats & votes | Chances". Chances uses a 0–100% y-axis, a line per party,
       a Majority/Largest switch and a Hung line. Tooltips show %. The view carries the nowcast label.
     - Bands and the toggle are hidden when the data lacks these keys.
   - Depends on: 8.
   - Acceptance:
     - Parser tests cover the new keys with and without data.
     - jsdom harness check (AGENTS.md recipe) on Westminster, plus a US page sanity check.

11. **Backfill + publish (operational)**
    - What:
      - Back up the DB (`cd data && ./election_data/bin/python scripts/backup_db.py backup` — `backup.py` is a library with no CLI).
      - Run the migrations (2, 5), the repair script (1) and the base backfill (2).
      - Run calibration (4) and review its report tables before accepting the config.
      - Run `run_uns_model.py` retrospective from 2024-07-05 to the latest date.
      - Run `scripts/rebuild_model_trends.py` and `scripts/export_elections.py`.
      - Inspect the odds and error budget in the console views for sanity.
      - Each live-DB step needs the user's go-ahead.
    - Depends on: 1, 2, 4, 5, 6, 8.
    - Acceptance:
      - The tracker shows bands and Chances back to Jul 2024.
      - The current-prediction popup, table and headline show ranges and odds.

## Checkpoints
- [x] Sample sizes repaired; the Techne parser is correct. (commit 4eb2646b; live repair + 3 corrections applied by user)
- [x] Decided bases captured by importers and backfilled; console shows n source and coverage. (commit bb499720; migration, regional repair and backfill applied to live DB 2026-10-12)
- [x] 3a: n → n_eff adjustments, poll MoE and average variance; console shows per-poll n, adjustment, n_eff and ±MoE. (commit 8c85b9da)
- [x] 3b: core MC reproduces the deterministic projection at zero error. (commit 9007e57a)
- [x] σ_nat, σ_λ and σ_seat calibrated; curated poll-error table, config and report committed; console Calibration view works. (commit 769602c4)
- [x] Piece 5: model_uncertainty storage, date-based pruning and read API. (commit 46882f96; live migration applied 2026-10-14)
- [x] Piece 6: the model persists uncertainty; deterministic compatibility and approved input/policy corrections verified. (commit 332ca4bc; geography follow-up 309bad61)
- [x] Console Error budget, Simulation results and Odds history views work. (commit 8588845f)
- [x] Raw simulations stored for the latest run; console Simulation explorer (filter, inspect, seat drill-down) works. (commit 8588845f)
- [x] Export emits the new keys and file; no-data export is byte-identical. (commit e4aad160)
- [x] Popup, vote totals and headline show uncertainty. (commit dc5426f5; UI refinements approved 2026-10-21)
- [x] Tracker bands and Chances view work. Approved and committed locally as `423a8419` on 2026-10-22.
- [ ] Full backfill published.

## Verification
- After each Python piece: `cd data && ./run_tests.sh` (strict mypy gates, then pytest).
  Narrow with `./run_tests.sh -k uncertainty -q`.
  - Run checks with isolated database/output paths (a scratch checkout is suitable).
    Verify the working tree afterward; stage only the intended piece's files.
- Smoke checks:
  - `cd data && ./election_data/bin/python models/westminster/run_uns_model.py --dry-run`
    prints an odds summary.
  - `./election_data/bin/python scripts/export_elections.py --dry-run`
  - `./election_data/bin/python -c "from console import create_app; create_app()"`
- After each front-end piece:
  - `npm test` (Vitest).
  - `npm run minify:electionmaps`.
  - jsdom harness per AGENTS.md (esbuild pre-bundle, inject `shell.html`, `jsdom@24`),
    checking Westminster and the US page.
  - Don't temp-edit watched files while `server.sh` runs.
- Statistical sanity: on the live projection, a safe seat should be >95% win, a marginal
  roughly 40–60%. The national 90% vote band for a ~30% party should be about ±3–4pt.

## Stage briefs (required before each piece)

The user wants to understand every stage, especially the error and simulation parts.
`briefs.md` (in this directory) holds a brief per piece. **Before starting any piece, the
builder presents that piece's brief and waits for the user's go-ahead.** Where a brief has
a worked example, refresh it with real numbers from the current data (e.g. a real poll's
base and margin of error, a real seat's simulated flip, the actual calibrated σs). If the
implementation diverges from what the brief says, update `briefs.md` in the same piece.

## Notes
Piece 1: Techne repair re-fetches PDFs (stored values undecodable: previous-wave n + stray digit, series has gaps) — user approved. Parser now takes the current-wave (second) figure, validated against Male+Female; pypdf pinned >=6.16.2. YouGov MRP merges (n≈17.2k) are genuine and left alone. Add-on (user-approved, after the live BMG/Techne repair ran): script renamed to `repair_poll_records.py` and given a sourced, column-whitelisted `CORRECTIONS` table for Ipsos 271 (n 367→1062), Ipsos 225 (start 2025-11-30→2025-10-30) and More in Common 261 (end 2026-02-13→2026-02-23; xlsx 404s, cause unknown). Ipsos and Lord Ashcroft fieldwork parsers now read an omitted start month as the previous month when start day > end day (inferred windows capped at 14 days); `models.py` rejects fieldwork_start > fieldwork_end on ORM insert/update. Same-month "D-D Month" patterns in BMG/Deltapoll/Find Out Now/Focaldata/More in Common/Opinium/Survation can still yield inverted windows from a "30-5 Month" string — not changed; the ORM guard now stops them being stored.
Piece 2: `poll_bases` (unweighted_n NOT NULL >0, weighted_n NULL, `kind` in decided / likely_voters / all_adults, `split_from_macro` boolean, unique on (poll_id, IFNULL(region_id,0)), FK poll ON DELETE CASCADE + ORM cascade). Importers parse bases tolerantly (no usable base → none, shares still import); commit replaces a poll's bases alongside its rows (kept untouched when existing rows are skipped). Macro columns are split by `regions.population` with largest remainder (exact `Fraction` quotas, ties to the earlier region; a region whose share rounds to 0 gets no row; a macro with an unknown population is skipped). Per-pollster findings and the kinds table are in briefs.md piece 2. Deviations from Settled 20 ("unweighted decided base"): YouGov, Deltapoll, Lord Ashcroft and Techne print only all-adult bases → stored as `all_adults` (YouGov per the user's decision; the others by the same rule); Ipsos regional bases are the LTV table's "Unweighted Total" (undecided included) → `likely_voters` (its decided sub-base is printed weighted-only per region); BMG's pre-final-table layouts → `likely_voters`; Find Out Now prints only a weighted "Filtered N" → no base (open question). Opinium's stored `sample_size` already is its decided unweighted base. Resolver (`model_support/poll_bases.py`): captured base → national `sample_size` → regional estimate = poll n (national base if captured else sample_size) × pop ÷ Σ pop of reported regions, where "reported" = ≥1 row with share > 0 (MiC/BMG/FON/Opinium/Focaldata store all-zero NI placeholder rows). Test world (`uk_fixtures`) now seeds live region populations. Backfill reuses `console.importers_registry.IMPORTERS` as the identifier → module table and calls each importer's new `fetch_parsed_poll` with no year hints (as the console import does). Pre-existing issues seen while sampling real sources (not fixed): Ipsos 2025-06/2026-01 PDFs no longer parse with the current importer; Deltapoll 2024 PDFs' regional header now extracts on two lines so regional shares/bases are not parsed, and some Deltapoll URLs return 403/HTML; older BMG workbooks re-parse to different national shares than stored (e.g. Con 24 vs 18); MiC `/media/` xlsx URLs 404; Lord Ashcroft stored regional shares sum to ~31% (poll 260) / ~57% (1795) against national ~65/72, which looks like a regional-denominator bug. **Most important:** ~21 YouGov polls (Jun 2025–Feb 2026, e.g. poll 26) have page-1 columns ending "England Wales Scotland" with the region table after "Now, thinking specifically"; the share parser falls into its old-format branch and maps the last six page-1 columns (65+, ABC1, C2DE, England, Wales, Scotland) onto Wales, Scotland, North, Midlands, London, Rest of South, so their stored regional shares are wrong (poll 26 London: Plaid 27%). The base parser records national only for these. Expected backfill coverage (scratch-copy dry plan, all sources re-fetched): YouGov 89/90 national, 76% crossbreaks; Opinium, Survation, Ashcroft, Techne 100%; MiC 15/73 (old URLs 404); Focaldata 4/8; BMG 1/14; Ipsos 1/7; Deltapoll 1/6; Find Out Now 0. Live migration + backfill wait for the user's go-ahead after a backup.
Piece 2 review fixes: estimate sources now carry their basis (`estimate:<kind>` / `estimate:sample_size`) and an estimate rounding below 1 is unresolved; the "reported region" rule lives once in `model_support/poll_bases.py` (`REPORTED_ROW_SQL`, `reported_region_id`); backfill checks YouGov via the new `HasRegionalShares` protocol (`ParsedPoll.regional_shares()`), skips with "no share check possible" when neither national nor regional shares can be compared, and skips importers with `CAPTURES_BASES = False` (Find Out Now) without fetching; More in Common's n-row scan stops at the headline table's own n rows; `weighted_value` validates thousands groups; `national_shares_from` shared; `inserted_bases` shown in importer CLIs ("inserted bases: N"), the console import flash and the Wikipedia-queue detail line.
Piece 2 add-on (user-approved): (A) YouGov parser finds the region table when it follows the second question (Jun 2025–Feb 2026 layouts; page 1 ends in the country columns), accepts the "Weighetd Sample" typo, and rejects any regional table failing the new `polls/importers/sanity.py` guard (SNP/Plaid > 3% outside their nation or < 10%/5% inside). (B) Lord Ashcroft: regional shares were count-or-percentage ÷ all-adult "Weighted Sample" (sheet-0 percentages divided again in single-sheet 2025 workbooks; turnout-weighted LTV counts ÷ all-adult sample in Sept 2026) — now read directly from the percentage sheet's region columns (same basis as national); a region total > 105 raises. National Ashcroft shares legitimately include DK/WNV (sum 61–72): not changed; recommend the model renormalise (see briefs). (A3/B) New sibling script `scripts/repair_regional_poll_rows.py` (not an extension of repair_poll_records.py, which rewrites single polls columns under value guards; this replaces child-row sets under a fingerprint guard): only polls failing their pollster's test are fetched (YouGov: guard or a region total < 85 — poll 931 stored the constituency question; Ashcroft: a region > 12pt from the national total); plan requires matching fieldwork, sample size, national shares (±0.5) and a re-parse passing the test; apply replaces regional poll_rows + poll_bases in one BEGIN IMMEDIATE transaction, aborting on any fingerprint change; idempotent; `--dry-run`, `--pollster`. Scratch-copy result: 28 repaired (21 YouGov, 7 Ashcroft), 69 unchanged, 2 skipped (YouGov MRP 56/241: source has no regional table; recommend deleting their regional rows). Run order: migrate → repair → backfill. (C) `poll_bases.unweighted_n` nullable with CHECK (unweighted_n NULL or > 0) and (unweighted_n or weighted_n present); Find Out Now stores Headline VI "Filtered N" weighted-only (`CAPTURES_BASES = True`); resolver uses unweighted else weighted, source `base:decided(weighted)` / `estimate:decided(weighted)`; console shows the flag. Backfill after repair: YouGov 88/90 (98% crossbreaks), Find Out Now 87/90 (100% crossbreaks).
Piece 2 round-2 review fixes: shared `scripts/poll_sources.py` (stored-poll bulk loader with a per-poll fingerprint of every row and base read with the shares the tests judge, `source_mismatch`, worst-party `share_mismatch`, `fetch_with_importer`, `insert_poll_bases`, `MAP_NAME`/`SHARE_TOLERANCE`) used by backfill and repair; the `CAPTURES_BASES` opt-out is removed. Lord Ashcroft's basis test moved to `sanity.regional_basis_problem` (mean regional-minus-national total over shared parties, threshold 5; correct polls within ~1.5, broken 15–53 below) and is applied at import on both parse paths and reused by the repair (replacing the 12-point worst-region rule); Ashcroft regional bases are kept only when they sum to the Total column. YouGov shares and bases share one layout detector (`_detect_layout`). Repair: the stale guard compares the bulk-read fingerprint (national rows and bases included), a re-parse without bases keeps stored bases, and user decision: a recorded `REGIONAL_DELETIONS` list (poll, pollster, SHA-256 of the regional rows; source text) deletes the regional rows of YouGov MRP releases 56 and 241, whose sources print no regional table (they become rowless; n falls back to sample_size). Scratch-copy result: 30 changes (21 YouGov, 7 Ashcroft repaired, 2 deletions), 69 unchanged, 0 skipped; re-run is a no-op; backfill after it: YouGov 88/90 polls, 968/968 crossbreaks.
Piece 3a: `model_support/uncertainty.py` holds `UncertaintyConfig` (frozen; validated in `__post_init__`: deff/cap/sims/chunk/majority > 0, interval and p_ref in (0,1), fractions in (0,1], σs ≥ 0 — 0 allowed to switch a term off; `z` via `NormalDist`), `load_uncertainty_config(path=None)` (reads `UNCERTAINTY_CONFIG_PATH` at call time; absent → defaults; unknown keys, wrong JSON types incl. bools/float-for-int, bad JSON → `UncertaintyConfigError`), `kind_factor(basis, cfg)`, `effective_n(resolved, cfg) -> EffectiveN | None`, `poll_moe(share_pct, n_eff, cfg)`, `kish_effective_count(weights) -> float | None`, `weighted_average_variance(entries, share_pct) -> AverageVariance | None` (variance pt², `kish_count`, `total_n_eff`, `.sd`, `.moe(cfg)`; None for no/zero weights so callers fall back to the national variance) and `share_scale(share_pct, cfg)`. Shares are percent in and out; errors in points. The kind is no longer string-parsed: `ResolvedN` carries `origin` (base / sample_size / estimate), `basis` (decided / likely_voters / all_adults / sample_size; an estimate's basis is its poll n's) and `weighted_only`, and `source` is a property rendering the unchanged label; `__post_init__` requires n/origin/basis all set or all None, a sample-size origin to have a sample-size basis, and a base not to (`estimate:sample_size` stays valid) (piece-2 tests updated to the new constructor; a label table test added). Console (Westminster map polls only, since the settings are Westminster's; other maps keep just n/source/weighted n): `/polls` adds n_eff ("(capped)") and "MoE at 30%" (reference share = `p_ref`, comparable across polls, needs no share query); `/polls/<id>` adds footer rows Adjustment (×1/×0.9/×0.8) and n_eff, and `<small>±x.x</small>` beside each share (blank cells, unresolved n and shares of 0, 100 or beyond get none; the detail view loads the settings only for Westminster polls, so a malformed file cannot break other parliaments' pages). Missing values show "-" to match the existing console cells rather than "–". `_poll_rows_table.html` unchanged: it renders import plans, which have no stored poll to resolve n against. Console tests patch `UNCERTAINTY_CONFIG_PATH` to a temp file (autouse) so piece 4's committed config cannot move their expected margins; one test writes a distinctive config to prove the views read it. numpy added to requirements (unused until 3b).
Piece 3a follow-up (user decision): `decided_fraction` default 0.75 → 0.80, the poll-weighted mean of decided ÷ total base across pollsters printing both (More in Common 0.75, Survation 0.81, Ipsos 0.84, BMG 0.86, Focaldata 0.89). Tests pinned to the default were recomputed (YouGov London 218 all adults → 174.4 → n_eff 116.3 → Reform 11% ±4.8); the kind-factor mechanism test now uses an explicit config. The /polls page was also widened (`base.html` `container_class` block, `poll_list.html` sets `container-wide`, `.container-wide { max-width: none; }`).
Piece 3b: the engine is a sibling module, `model_support/monte_carlo.py` (numpy; `uncertainty.py` stays numpy-free config + sampling-error inputs, imported by the console). API: `SimulationInputs` (frozen arrays: `party_ids`/`region_ids`/`seat_ids`, `seat_region[N]`, `seat_turnout[N]` = baseline total votes, `seat_baseline[N,K]` p⁰, `region_baseline[R,K]` P⁰ (0 → additive-only swing), `region_swing[R,K]` Δ, `region_average[R,K]` P̂ᵣ,ₖ (`weighted_share`, scales/centres the regional term), `region_variance[R,K]` V pt², `region_polled[R,K]`, `national_average[K]`, `national_polled[K]`; validated in `__post_init__`), built by `build_simulation_inputs(seat_party_vote_totals=, region_by_seat_id=, party_universe=, region_swings=, region_baseline_shares=, region_average_shares=, region_variances=, national_average_shares=)` from run_uns_model's own dicts (a region×party is "polled" — gets national/regional error — exactly when its variance / national average is passed; piece 6 passes the national variance for crossbreak-less regions). Then `error_scales(inputs, cfg, p̂)` → `ErrorScales` (SDs + centring weights q, 0 for non-receiving parties), `ErrorStreams.from_seed(seed)` (four PCG64 streams spawned from one `SeedSequence`, one per term, each drawn in simulation order → chunk-size invariant), `draw_errors(streams, scales, count)` → `ErrorDraws` (centred ẽ_nat[S,K], ẽ_reg[S,R,K], ẽ_seat[S,N,K] float32, λ[S]), `project_seats_vectorised(inputs, errors)` → `SeatProjection(share[S,N,K], winner[S,N])`, `deterministic_projection(inputs)`, `SimulationTallies` (preallocated, `record()` per chunk), `summarise(inputs, tallies, cfg, seed=)` → `SimulationSummary` (+ `to_json_dict()`), `seat_outcome_probabilities(seats, M)` (reusable by the explorer), `config_hash(cfg)`, `run_simulations(inputs, cfg, seed, *, keep_raw)` → `(SimulationSummary, SimulationDraws | None)`, `SimulationDraws` (assumption j arrays + ids, seed, config hash, `projected_winner[N]` uint8; e_nat/e_reg are the centred errors applied), `DrawsMeta(election_id, as_of_date)`, `save_draws(path, draws, meta)` (temp file + `os.replace`, temp removed on failure; header JSON `pf-simulations-v1` in a 0-d string array) / `load_draws(path)` → `(draws, meta)` (any missing/garbled file, array, dtype, shape or header field → `SimulationFileError`). Exactness: with zero errors and λ=0 the projection equals `project_seat_votes` bit for bit (tested on the real function incl. a tie, a clamp, absent parties, the all-clamped baseline fallback, a skipped zero-vote seat, and a ×1e14 vote scale where rounded counts expose every bit). Subtleties: party order must be `tuple(party_universe)` (the set's iteration order decides both the renormalising sum's rounding and the tie-break — first max wins, as `max()`); seat order = dict order, skipping non-positive totals; turnout = Python `sum` of the seat's votes; and Python ≥3.12 `sum()` of floats is Neumaier-compensated, so the renormalising total is a vectorised compensated sum (`_python_sum`, Knuth TwoSum — a plain left-to-right or numpy pairwise sum differs in the last bit); the blend is written (p⁰+Δ) + λ·(Δ·p⁰/P⁰ − Δ) so λ=0 is exactly `baseline_share + swing`. Winners do not depend on the total's rounding (division by a common total is monotone). Deviations: a projected seat with no region or no baseline share among the universe raises (Westminster has neither; `project_seat_votes` would zero-swing / skip them); `run_simulations` raises unless 2·M > N. Summary: national vote band (linear percentiles) and seats [lo, median, hi] (inverted-CDF, i.e. the 500th/5,000th/9,500th sorted sims), dense histogram `{start, counts}` from min to max, P(maj), P(largest) ties split, P(hung) = 1 − ΣP(maj); per region vote band + seat range; per seat share band + win prob. Per-seat bands come from 0.1-pt histograms (10k×650×13 shares would be 680 MB), interpolated in-bin and clamped to the observed min/max (within a bin of the order statistics around the rank; exact when sims agree). Region/seat blocks drop a party whose band tops out below 0.5 pt and which never wins (`COMPACT_SHARE_FLOOR`). `to_json_dict()` uses the export's short keys (`national.hung`, `parties.{pid}.v/s/pm/pl/hist`, `regions.{rid}.parties.{pid}.v/s`, `seats.{sid}.{pid}.v/w`; shares 2 dp, probs 4 dp). Performance (650×13×12, 10k sims, chunk 1000): ≈4.8–5.0 s without raw, ≈5.5–5.7 s with raw, ~760 MB peak RSS (chunk 500 ≈ same speed, less memory); float32 normals and float32 seat errors, cell-major histogram bincount. Tests: 68 in `test_monte_carlo.py` (mutation-checked: tie-break, party order, compensated sum, fallback, centring, polled flags, per-seat region gather, tie split, histogram clamp). Read-only demo (as_of 2026-10-05, 30-day window, fallback σs, V from poll_bases via `PollNIndex` + `effective_n` + `weighted_average_variance` with collect_poll_shares' weights): live DB zero-error engine == `project_seat_votes` on every row, but both give Lab 307 / Ref 109 / Con 105 vs stored 307/112/102 — the stored row predates the 2026-10-12 poll repairs; on a pre-repair backup copy (`elections-2026-10-12-163640`) the 30-day projection and the engine reproduce the stored row exactly. Results: Lab seats 222/300/372, vote 22.8–29.6, P(maj) 28.3%, P(largest) 97.3%; Ref 47/114/203, 19.6–26.5, 0% / 2.7%; Con 57/102/158; LD 60/70/78; P(hung) 71.7%; ~18% of seats differ from the projection in an average sim. Found: the live DB lacks `elections.target_election_year` (US electoral-vote migration not applied), so ORM `Election` queries — incl. `run_uns_model.resolve_simulation_scope` — fail on it today; the demo read the baseline id with raw SQL.
Piece 3b review fixes (two user decisions): (A) λ applies only to a named party whose regional baseline P⁰ ≥ `lambda_min_share` (new `UncertaintyConfig` field, points, default 1.0, validated in (0, 100]); the "Others" aggregate is always additive. `SimulationInputs` gains `region_proportional[R,K]`, which `build_simulation_inputs(config=, …, additive_party_ids=)` sets from the threshold; piece 6 passes `set(PARTY_ID_ALIASES.values())`, i.e. party 15 "Others". `__post_init__` rejects a proportional cell with P⁰ ≤ 0 and any non-finite proportional term (e.g. a subnormal P⁰). (B) The national error is masked per region by `region_polled` and re-centred per region over the parties it reaches (`ErrorScales.national_reach` / `national_region_weight`; `ErrorDraws.national_by_region[S,R,K]` is what's applied; `ErrorDraws.national` / `draws.e_nat` stay the vector centred over every nationally polled party). A region×party counts as polled only when its average is ≥ `min_polled_share` (new field, default 0.5 pt, validated in [0, 100)), because SNP/Plaid read 0–0.13 in English crossbreaks and the NI placeholder rows give GB parties 0; issue.md §5/§6 wording updated. Fixes: `load_draws` calls `np.load` outside `with`, rejects a non-`NpzFile` (.npy), closes in `finally`, and maps OSError/ValueError/EOFError/TypeError/BadZipFile/zlib.error to `SimulationFileError`; `SimulationDraws.__post_init__` checks `lam.ndim` and winner/projected_winner < K (`empty()` now zero-fills). P(hung) = mean(max seats < M), so never negative. Interval tails are rounded to 12 dp, so the inverted-CDF ranks are exact at 0.95/0.99. `config_hash` excludes `chunk_size`. `run_simulations` returns `SimulationRun(summary, scales, draws)`. `SimulationSummary` has `national_json()` (with simulations/interval/seed/config_hash/hung), `regions_json()` and `seats_json()` for piece 5's three columns; `to_json_dict()` = `{"national", "regions", "seats"}`. The seats block omits below-floor parties, so piece 8 must handle a missing party. `save_draws` uses `Path.replace`. Tests: 85 in `test_monte_carlo.py`, plus 3a config tests for the two fields; mutation-checked (zlib catch, per-region mask, per-region re-centring, polled floor, additive Others, tail rounding, hash, winner range). Demo after the fixes: Lab 222/299/372, P(maj) 28.3%, P(largest) 97.3%, P(hung) 71.7%; SNP is listed in 0/593 seats outside Scotland and Plaid in 0/618 outside Wales. Others seats, before → after: Islington North 98.3 → 100% (Others range 38.2–53.9 → 42.0–52.0), Perry Barr 98.8 → 99.2%, Leicester South 98.4 → 99.5%, Chorley 100%. λ applies to 63/156 cells; 69 cells are polled. 10k sims: 5.2 s without raw, 5.5 s with. The US electoral-vote migration has since been applied to the live DB, so the demo uses the ORM again.
Piece 3b final fixes: (1) `load_draws` also catches RuntimeError (which covers NotImplementedError) and rejects any member that is not an ndarray (a non-.npy member comes back as raw bytes). (2) A cell counts as polled only if its average is ≥ `min_polled_share` AND the party has a regional baseline > 0, except a party with no baseline in any region (a new party), which is judged on the average alone. Spurious SNP/Plaid crossbreak averages reach 0.65 in the 30-day history (0.16 today); any that still slip through are harmless, because their errors scale with s(≈0). (3) Inclusive boundary tests at exactly 0.25 / 2.0. (4) Explicit national fallback: `build_simulation_inputs` takes a required `national_variances`. A region × party with no crossbreak value (absent from `region_average_shares` / `region_variances`, which piece 6 fills only from region-keyed poll averages, not `weighted_share`) is polled with average = national average and variance = national variance, flagged in a new `SimulationInputs.region_fallback[R,K]` (validated ⊆ polled), subject to the same baseline rule (so the fallback never reaches SNP in England). Docstring drift fixed (ẽ_nat[R(s)], region_average, national_polled). (5) Module docstring notes as a simplification that a nation-only party's national error still scales by its small national share. Tests: 93 in `test_monte_carlo.py`; suite 5207 passed. Demo re-run: headline table unchanged.
Piece 4 (user decisions: PollBase as the single final-poll source, 1992–2024 equal weight; pooled σ_seat/σ_λ with per-pair values shown; decided_fraction recomputed; Settled 22 transparency added mid-piece): **Source.** Mark Pack's PollBase, edition `PollBase-Q3-2026.xlsx` (sha256 c8e4863d…f10, in the curated file), credited "Final-poll data: Mark Pack, PollBase — https://www.markpack.org.uk/opinion-polls/" in the table, report and console. Results R are PollBase's own `Result` rows (GB shares; single source). They match the DB's GB shares (NI excluded) to ≤0.05 pt for every 2010–2024 party (cross-check table in the report). 1997's Result row is printed to whole numbers (31/44/17), so 1997 carries up to ±0.5 pt rounding — kept, flagged. **Final-poll rule** (`build_final_poll_errors.py`, uniform): a row with a fieldwork start date and numeric Con/Lab/LD; fieldwork end (last day number in the `Fieldwork` text read forward from the start date, or a day/month end like `10-1/7`) in [polling day − 3, polling day]; fieldwork started before polling day (no on-the-day polls); `Published` on or before polling day — a blank Published cell does not count (PollBase leaves it blank for withheld polls, e.g. Survation's 6 May 2015 phone poll; later releases carry full dates, e.g. Focaldata 2024 published 2025-04-14, Find Out Now 3 Jul 2024 published 2024-11-15); per pollster the last poll (latest end, then latest publication, then later row); a party's A = equal-weight mean over the pollsters reporting it (blank ≠ 0). Fieldwork ending on polling day is allowed because PollBase prints Populus's final 2015 poll as "5-7" (published 7th); 1992's "c.6-9" NOP/ITC row is excluded by the blank-Published rule. Parties: Con/Lab/LD always; Green, UKIP, Brexit Party, Reform where both the Result row and ≥1 final poll have them (2015/2017 Green+UKIP, 2019 Green+Brexit, 2024 Green+Reform). **PollBase has no SNP column**, so no SNP (gap). Party columns are read only before the `Leader:` ratings block (later blocks reuse party names). UK-basis polls (Savanta, Survation, Techne in 2024) are used as published and flagged `geography: UK` (≈2.5% relative understatement of GB parties; < 0.1 pt on the averages). Only rows within 45 days before an election are parsed. Final polls per election: 1992 5, 1997 5, 2001 3 (YouGov's 4 Jun 2001 row has no Published date — "press released" — so it drops; Rasmussen/NOP end outside the window), 2005 6, 2010 9, 2015 12, 2017 11, 2019 12, 2024 19. **σ_nat** = √(mean over elections of mean z²) = 2.551 (each election equal, the user's "equal weight"); every-cell RMS 2.622; Con/Lab/LD-only 2.350 (reported for comparison; UKIP 2017's z = +8.3 — polled 4.4 vs 1.9, standing in only 378 seats — is the largest single cell). **λ / σ_seat** per pair: cells = a party with votes in the seat at either election, seats in both; exclusions tested in order: Speaker's seat at either election (Buckingham 2010–17, Chorley 2019–24, hard-coded `SPEAKER_SEATS`), the Others aggregate (after `PARTY_ID_ALIASES` 7→15; always additive in 3b's engine), party absent nationally at either election (in 2019n→24 this drops Reform (2024 only) and the Brexit Party (stored as UKIP id 14, 2019 only): 946 cells), regional P⁰ < `lambda_min_share` (1%), and p⁰ = 0 (s(p⁰) = 0 makes u undefined; also b = 0) — that last one is an addition to issue §8, forced by the formula. Cells whose party stood down at the later election (p¹ = 0) are kept and counted. NI seats are included (the engine applies σ_seat there too). λ̂ (Σ(y−a)(b−a)/Σ(b−a)²): 2010→15 +0.238, 15→17 +0.437, 17→19 +0.118, 19n→24 +0.567; σ_λ = RMS = 0.382, mean +0.340 (not applied). σ_seat (1.4826·MAD): 4.602 / 3.352 / 3.986 / 6.205, pooled over 11,603 cells 4.267. **decided_fraction** = 0.7987 → 0.80 (34 polls: BMG 1, Focaldata 4, Ipsos 1, MiC 15, Survation 13; Opinium 37 excluded as base = sample size, Find Out Now 87 weighted-only; Westminster-parliament national polls only, constituency polls excluded). **Config written:** σ_nat 2.55, σ_seat 4.27, σ_λ 0.38, decided_fraction 0.80, all others unchanged (σ_reg 1.5 guess, deff 1.5 literature, cap 3000 etc.); non-calibrated values are taken from the existing config file (else defaults) so a hand-set value survives a re-run; rounded to 2 dp. The script opens the DB read-only (`open_database(read_only=True)`), writes both outputs via temp files renamed only after both serialise, and `--dry-run` prints every table and writes nothing. **Console (with Settled 22):** `/westminster/uncertainty` hub (linked from home) and `/westminster/uncertainty/calibration`: settings in use (live config) with unit, source, the value the calibration wrote (flagged when they differ) and a one-line "what it is and how it was built" (from the report; fallback meaning+source); σ_nat per election × party A, R, A−R, s(R), z, z², election mean z², and the √(mean) line; by-party RMS; results cross-check; per pair cells, stood-down, Σ(y−a)(b−a), Σ(b−a)², λ̂, λ̂² and the σ_λ RMS line; per-party λ̂ pivot; σ_seat median/MAD/×1.4826 per pair + pooled; u percentiles (p5…p95) and a 1-pt histogram (±10, tails counted) per pair + pooled with CSS bars; exclusions pivot; decided_fraction per pollster count/mean/polls×mean and Σ÷n line. Missing/malformed config or report → message (report loader validates schema and every table the template iterates; numbers go through a `fmt` that shows "-" for non-numbers). Tests: synthetic final-poll misses → σ_nat; synthetic cells and a synthetic DB pair (λ 0.4, σ 2.5 injected, recovered within 0.05 / 10%); exclusion counts by reason; decided-fraction derivation; builder on a synthetic workbook (tie-break, on-the-day, unpublished, post-election release, ratings-block columns, month wrap; mutation-checked); dry runs write nothing; committed table has a source per election and the credit; committed config equals the report's config. 3a/3b tests unaffected (they pass explicit or patched config paths). Not changed: 3a/3b behaviour.
Piece 4 review fixes (user decisions: σ_seat pooled across pairs; σ_nat keeps all parties incl. UKIP 2017, elections weighted equally; **calibrate in engine units**): History measures realised, already-zero-sum errors; the engine draws raw N(0,(σ·s)²) and centres them (ẽ = e − q·Σe), which shrinks a big party's error. (a) u is now scaled by s(p̂), p̂ the seat's predicted later share exactly as the engine projects with λ̂ (`calibration.predicted_shares`: max(0, p⁰ + a + λ̂(b − a)) renormalised over every party with votes anywhere at either election; b = a for Others and for parties under `lambda_min_share` regionally, as in the engine); cells with p̂ = 0 are left out of u and counted (91/179/14/15). (b) Centring factor from the engine's own transform: `monte_carlo.centring_weights` (renamed from the private `_centring_weights`, no behaviour change) and new `monte_carlo.centring_sd_ratio` (√(sd² − 2q·sd² + q²·Σsd²)/sd). σ_seat: median ratio over each seat's two largest predicted shares (receivers = every party, as the engine's seat term), per pair and pooled; σ_nat: median over each election's two largest named parties at the result shares, receivers = the named parties plus one remainder party (100 − Σ named) standing in for the parties PollBase omits. Raw = realised ÷ factor, written to the config. σ_λ unchanged. Results: σ_nat realised 2.551 ÷ 0.821 = **3.11**; σ_seat realised 4.414 (pooled, now s(p̂)-scaled; per pair 4.32/4.18/3.65/5.71) ÷ 0.823 = **5.36** (per pair raw 5.24/5.22/4.52/6.75); σ_λ 0.38; decided 0.80. (c) Report and console show realised, factor and raw for both (national per-election top-two ratios; per pair centring with contender count and p̂ = 0 count). (d) Tests draw with the engine (`draw_errors` on seat-only / national-only `ErrorScales`) at the raw σ and recover the realised σ for the top-two parties within 3% / 2%. (e) issue.md §5/§8 rewritten (engine units, PollBase incl. non-BPC pollsters, σ_nat as the election-weighted RMS, all exclusions incl. Others and p⁰ = 0, SNP absent). Other fixes: test for a party standing only at the earlier election (kills the `parties1` mutant); a later-ending/earlier-published/earlier-row tie-break test; P⁰ exactly at `lambda_min_share` kept; the builder raises when a resolved publication date precedes the fieldwork end. Two real PollBase rows trip that, so a named `SKIPPED_ROWS` list (sheet, row, pollster, fieldwork text → reason) skips them and stops the build if an entry goes stale; they are recorded in the table's `skipped_rows` (YouGov "16" with start 17 Apr 2010; YouGov MRP 11-18 Jun 2024 printed as published on the 16th). Both are outside every window, so the averages are unchanged. Vote queries are `ORDER BY`ed. Both scripts write through the new shared `model_support.io.write_text_files` (unique temporaries, all written before any replace, cleaned up on failure). A damaged workbook (BadZipFile, InvalidFileException, zlib.error, EOFError, KeyError, ValueError, ElementTree ParseError — at load or while iterating rows in read-only mode) is a clean `error:` exit. The report loader rejects a non-https election `source_url`, and the template links only https. CSS `.visually-hidden` → `.u-visually-hidden`, with rationale comments for the bar's min-width/colour and the data-driven inline width. defusedxml not added: it is not installed in the venv, so adding it to requirements would leave the env out of sync (skipped, as allowed). Suite: 5357 passed.
Piece 4 round-2 fixes (user decision: **centring correction per cell**, an exact moment match, replacing round 1's median-of-top-two factor): σ_seat — each scaled residual u is divided by its own cell's ratio (`calibration.cell_centring_ratios` = `monte_carlo.centring_sd_ratio` on s(p̂) with q = `centring_weights(p̂, all parties)`), then 1.4826·MAD per pair and pooled; σ_nat — each z is divided by its own election × party ratio (result shares + remainder party), then the election-weighted RMS. Report and console show the realised (uncorrected) spread, the corrected raw value, per-cell ratio and z ÷ ratio, median u ÷ ratio and MAD, and ratio summaries (count/p10/median/p90) split contenders (top two) vs others, for national and pooled seats and the medians per pair. Results: σ_nat realised 2.551 → raw **2.944** (ratios: top two 0.795/0.821/0.843, others 0.877/0.927/0.987); σ_seat realised 4.414 → raw **4.903** (per pair raw 4.857/4.642/4.152/6.302; ratios pooled: top two 0.774/0.822/0.871 over 5,072 cells, others 0.904/0.962/0.991 over 6,232). Config: σ_nat 2.94, σ_seat 4.90, σ_λ 0.38, decided 0.80. Other fixes: `swing.method` now says s(p̂). Cells with s(p̂) = 0 or a non-finite/zero ratio (p̂ = 100, a one-party seat) are left out and counted as `no_seat_noise`, separately from `predicted_zero` (0 in the real data); tested. The verification test now runs through real `build_simulation_inputs` + `deterministic_projection` + `error_scales` + `draw_errors`: synthetic residuals with per-cell centred sizes, calibrated raw σ, and the engine's centred seat noise reproduces the residuals' realised spread within 3%. The national check compares the engine's per-party SD with raw × ratio within 1%, and the tautological assert is gone. `calibrate_uncertainty.py` turns a write OSError into `error:` + exit 1 (tested). `model_support/io.py`: a shared `_write_temporary` (unique temp beside the destination, partial file removed on failure) is used by `publish_json` and `write_text_files`. `write_text_files` sets `REPO_FILE_MODE` 0644 before `os.replace` (tested); `publish_json` keeps its existing 0600 (unchanged behaviour, commented). The docstring notes that a failure between replaces can leave outputs mixed. The three committed JSON files were regenerated (all now 0644) and are byte-reproducible. Suite: 5366 passed.
Piece 5: **Table** `model_uncertainty` (ORM `models.ModelUncertainty`): `election_id` INTEGER PK + FK `elections.id` ON DELETE CASCADE, `schema` TEXT NOT NULL, `national_json` TEXT NOT NULL, `regions_json` / `seats_json` TEXT NULL. **Schema marker (decision):** a `schema` column, not a key inside the JSON, so the blocks stay exactly the engine's `national_json()` / `regions_json()` / `seats_json()` (piece 8 can copy them verbatim) and a reader can check the format without parsing; value `persistence.UNCERTAINTY_SCHEMA = "pf-model-uncertainty-v1"`; `load_uncertainty` raises on any other value. JSON is `json.dumps(sort_keys=True, separators=(",", ":"), allow_nan=False)` (deterministic; NaN/inf or non-JSON → ValueError before anything is written). `Election.uncertainty` relationship (uselist=False, cascade all/delete-orphan, **passive_deletes=True**, so the console's `session.delete(election)` never SELECTs the table and keeps working on an un-migrated DB; the DB cascade removes the row where foreign keys are on). Migration `scripts/migrate_add_model_uncertainty.py` (one step, migrate_add_poll_bases style: idempotent, one transaction, `--dry-run` opens read-only); `create_tables()` builds the same table (columns/FK/cascade compared in tests). `ensure_elections_sqlite_schema` deliberately does **not** create it (that would auto-migrate the live DB on the next model run). **Persistence API** (numpy-free; takes plain dicts): `UncertaintyPayload(national, regions=None, seats=None)`; `replace_output(..., uncertainty: UncertaintyPayload | None = None)` stores the row in the run's own `BEGIN IMMEDIATE` transaction and then prunes; a payload with no table → `UncertaintyStoreError` (rolled back, nothing written). **Prune by run date (user decision):** after a payload write, within the scope (`scoped_elections`: type + map + name prefix, date from `OutputScope.date_from_name`), only the run with the latest as-of date (ties → highest id) keeps regions/seats; all others are set NULL; national never pruned. Re-saving an older date leaves the latest run's detail and is itself pruned at once. The latest-dated run wins even if it has no summary (so an older payload is then national-only). **No-payload path:** no insert and no prune; with the table absent no statement touches it (trace-tested, the catalog probe aside). One necessary exception: `_delete_range` (used by `replace_output` for the same date and by `delete_outputs`) deletes the replaced/deleted runs' rows whenever the table exists, because these raw `sqlite3` connections don't enforce foreign keys and SQLite reuses the deleted top rowid, so a stale row would attach itself to the next election (tested). Read API: `load_uncertainty(sqlite_path, election_id) -> StoredUncertainty | None` (None when file/table/row absent), `uncertainty_status(sqlite_path, scope) -> UncertaintyStatus(stored_runs, detail_run: UncertaintyRun(election_id, name, as_of) | None) | None` (None = not migrated). **Console:** the hub shows "N runs have stored uncertainty; full detail on: UNS YYYY-MM-DD" / "none yet" / "table not migrated yet (run scripts/migrate_add_model_uncertainty.py)" / "No … map" for the `model_uns` scope on `WESTMINSTER_MAP_NAME`. Tests: `test_model_uncertainty_persistence.py` (31), `test_migrate_add_model_uncertainty.py` (13), console hub (+7); mutation-checked (passive_deletes, prune keeper by id instead of date, unscoped prune, no row delete in `_delete_range`, sort_keys, allow_nan, status scope/max/undated filter, dry-run read-only). Verified on a scratch copy of the live DB: hub says "not migrated" → migration → "0 runs … none yet". Live DB untouched (migration only `--dry-run`). Suite: 5417 passed.
Piece 5 review fixes: (1) Pruning no longer depends on write order. `replace_output` runs `_prune_detail` on **every** save while the table exists, payload or not, so the end state is always "only the latest as-of run in the scope (model type + map + name prefix) keeps regions/seats". A newer run saved without a summary therefore clears older runs' detail. When the table is absent and there is no payload, only the catalog probe runs (trace test unchanged). New tests: a payload-less later run prunes; a payload-less older run leaves the latest detail; both write orders end in the same state (parametrized). Module, `replace_output`, `OutputScope` and `ModelUncertainty` docstrings updated. (2) The status test's expected run has the later date but the lower id. (3) Tie-break on equal dates is tested: a hand-inserted "UNS 2026-10-05 rerun" with a higher id keeps detail over "UNS 2026-10-05" on the next save, and the status picks it too. (4) Hub wording: "…; no run holds full detail" replaces "full detail on: none yet" (also the state after deleting the detailed run; tested in status). (5) New `persistence.westminster_uns_scope(map_id)` → `OutputScope("model_uns", map_id, "UNS")`, used by the hub, and by all 10 literals in `run_uns_model.py` (mechanical, behaviour-neutral; `OutputScope` import dropped there). Mutation-checked: prune only with a payload (3 fail), keeper by date only (tie test fails), status by id only / date only (fail). Suite: 5423 passed.
Review fixes (review.md 2026-10-14): **(1) Backfill revalidation.** `backfill_poll_bases.apply_backfill` now re-reads every planned poll under its `BEGIN IMMEDIATE` and compares it with what planning checked the source against: `BackfillItem` carries `head` (new `poll_sources.PollHead` = pollster identifier, fieldwork start/end, sample size, source_url; `StoredPoll.head`) and the `fingerprint` captured by `load_stored_polls`; new shared `poll_sources.poll_head` (None unless the poll is on `MAP_NAME`) and `poll_sources.stale_reason` (deleted/moved off the map → own columns changed → rows changed → bases changed). Comparing the poll's own columns matters because SQLite can hand a deleted poll's id to a new poll whose rows happen to match. **Decision: skip and continue**, unlike `repair_regional_poll_rows.py`, which aborts on the same guard: the backfill only adds rows and each poll's insert is independent, while the plan costs a full re-fetch, so one console edit should not discard it; this also keeps the backfill's existing documented rule (a poll that gained bases since planning is left alone), which is itself a fingerprint change. `apply_backfill` returns `BackfillResult(polls_written, rows_written, stale)` (stale = skip items whose note names the reason) and `main` prints the stale polls with a "re-run to plan them again" line. Tests: deleted poll gets no bases; id reused by a new poll with identical rows gets none (only the head comparison catches it); per-column metadata changes (start, end, sample size, source_url, pollster); moved to another map; national/regional share change and removed rows; gained bases; the last-processed poll going stale leaves earlier inserts written; `main` with a poll deleted during the fetch phase. Mutation-checked (head, rows, bases, map filter). The repair script is unchanged (its rows fingerprint already aborts on a deletion; an id reused with identical rows and bases would slip past it, not addressed). **(2) Exact regional sampling covariance.** `monte_carlo`: the regional sampling term is separated from σ_reg. `ErrorScales.regional_sd` → `regional_systematic_sd` (σ_reg·s(P̂) only, still drawn raw then centred with `regional_weight`); new `regional_sampling_scale` cᵣ (R,), `regional_sampling_shape` t (R,K), `regional_sampling_exact` (R,) and property `regional_sampling_sd` = √(cᵣ·t(1−t)). The sampling error is drawn zero-sum as `√cᵣ(√t⊙ε − t·Σ√tⱼεⱼ)` (Cov cᵣ(diag t − ttᵀ)) and added to the centred systematic term without further centring. **c/V reconciliation:** 3a's V = Σwᵢ²/n_eff,ᵢ ÷ (Σwᵢ)² · P(1−P)·100², with the poll set per (region, party) (`collect_poll_shares` accumulates weight only for parties a poll lists) and P the unnormalised average. So V is exactly multinomial-shaped, cᵣ·q(1−q) with q the shares normalised over the polled parties, only when every party is on the same polls and the polled shares sum to 100. Read-only check of the live DB (polls with fieldwork ending since 2026-09-05): every region lists every party in every poll (one C per region), but polls' listed shares sum to 66–102 (mean 95.7) and no fallback cells occur. Rather than one cᵣ fitted to all parties (which misses each V by 1–4% at a 96% total, and by up to 40% when a party is on fewer polls), `regional_sampling_multinomial` solves (cᵣ, t) per region so that cᵣ·tₖ(1−tₖ) = Vₖ for every polled party with Σt = 1 (`_fit_multinomial`: bisection on cᵣ; all parties on the smaller root when Σt ≥ 1 at cᵣ = 4·max V, else the largest-V party on the larger root, feasible only if its V is below the others' total). In the multinomial-shaped case this returns t = q, cᵣ = 100²·C exactly (checked on 20,000 random 3–8-party share vectors incl. majority parties: every V reproduced to 2e-13, t − q ≤ 2e-7, worst near a 50% party where t is ill-conditioned); at a 96% total (30/28/20/10/8) t moves 0.002 from q. No exact fit (two polled parties with unequal V, which zero-sum forces equal; one V above the rest combined) → fallback t = q, cᵣ = ΣV / Σq(1−q) (keeps the total), `exact` False. V = 0 cells get no sampling error; fewer than two V > 0 parties → none. Fallback regions/cells use the national average and variance (already in `region_average`/`region_variance`), so they get the same treatment. **Stream layout:** the regional stream now draws (count, 2, R, K) float32 normals per chunk: per simulation R×K systematic then R×K sampling normals, so chunk-size invariance holds (tested 1+99+200 vs 300; run-level chunk tests unchanged) and seeds reproduce; seeded results differ from before the fix (the systematic draws moved in the stream). `ErrorDraws.regional` = total applied (systematic + sampling), new `ErrorDraws.regional_sampling`; `SimulationDraws.e_reg` stores the total (documented). Zero-error exact reproduction unchanged (V = 0 there). Piece 4 calibration unaffected: σ_nat and σ_seat are calibrated against the national and seat terms' raw-then-centre transform (`centring_sd_ratio`/`centring_weights`), which did not change, and nothing in `calibration.py` / `calibrate_uncertainty.py` used the regional construction; only `test_calibration`'s national-only `ErrorScales` helper gained the new fields. Tests (`TestRegionalSampling`, sampling only, 200k draws, 2% tolerance on variances): 50/50 n_eff 1000 → 2.5 pt² (was 1.25); unequal 5-party shares on three polls → each V and the full covariance; shares summing to 96 → each V exact, t ≠ q but within 0.005; a party on fewer polls → each V exact; majority party → larger root, t = q; national-fallback region → national V; zero-sum per draw; σ_reg-only still matches raw × centring ratio; small/infeasible regions (lone, none, two equal, two unequal, dominant V); chunking + seed. `test_each_sd_follows_its_formula` now expects the systematic SD σ_reg·s alone (it encoded √(V + (σ_reg·s)²), the old folded construction) and checks the delivered sampling variance equals V. Mutation-checked (separate per-chunk draws, shape → weights, larger root dropped, always-fallback). **Effect:** sampling SD of a party with normalised share q was √(1 − 2q + q²ΣV/V) of intended (≈0.85 for a 35% party, 0.98 for 3%); a 35% party's total regional SD with σ_reg 1.5 rises ≈2.7 → 3.05 pt at summed n_eff 300 (1.8 → 2.0 at 1,000), so regional vote bands and seat ranges widen modestly; national ranges move less (regional errors partly cancel; σ_nat and λ dominate). Not re-run on the live DB. **(3) Piece 5 rollback tests (optional, done):** `TestLateFailureRollsBack` injects `RAISE(ABORT)` triggers on the `model_uncertainty` INSERT (replacing a same-date detailed run) and on the pruning UPDATE (with and without a payload) and asserts elections, votes and every uncertainty row (incl. another scope's) are unchanged; mutation-checked with a premature `conn.commit()` before each statement. Suite: 5449 passed.

2026-10-15: Reviewed fixes committed locally as d2a80a7e (`fix: preserve sampling variance and revalidate poll base backfills`), on the user's explicit instruction. The seven files matched the reviewed snapshot; strict mypy and 5,449 tests had passed. The minor exactness-flag tolerance finding remains as reviewed and accepted for this commit. Paused at the user's request before piece 6; its brief has been presented, but implementation has not begun.

2026-10-16 — Piece 6 implementation (uncommitted, pending user review). `run_uns_model.py` now collects uncertainty evidence in a Westminster-specific `WestminsterPollAggregation` from `model_support/westminster_uncertainty.py`; the shared polling API is unchanged. Each observation retains poll id, model weight, resolved n/basis/source, actual adjustment, n_eff/cap/design effect, weighted-only and macro-split provenance, and Ashcroft's original party total. Variance uses every contributing poll's weight; Kish count and summed n_eff are stored. Ashcroft shares are normalized before averaging and its n uses the observed decided fraction exactly once. **User decisions during implementation:** unresolved contributing n stops uncertainty with the poll/region and a repair message; `--no-uncertainty` remains available. Entirely empty regional columns are excluded before adding weights, retaining genuine zero parties in reported columns. The full-data check then exposed Survation's NI columns as 99–100% Other with no NI-party breakdown; the user explicitly approved holding Northern Ireland at baseline vote shares, excluding GB polling swing/error and national fallback there, while retaining seat noise. NI is identified by name, and its diagnostics report `poll_average: null`, `sampling_source: excluded` and the reason; excluded NI observations do not select a contributing poll endpoint.

Piece 6 integration: CLI/console defaults load calibrated settings, with `--simulations N`, `--no-uncertainty` and `--no-raw-sims`; these survive endpoint capping and automatic backfill. Existing direct Python `SimulationConfig` construction retains deterministic behavior unless uncertainty settings are supplied. The seed is the date's ordinal; configured batching/count are passed to the existing engine. National/region/seat JSON plus full error budgets are passed through the atomic output replacement. Raw and post-centering SDs are distinct; the national applied budget keeps cross-region correlation of the shared draw and combines independent regional sampling/systematic terms with squared turnout weights. The sampling covariance-fit flag and requested-vs-delivered sampling variance remain visible. Combined diagnostic swing bands cover polling errors only, before seat noise/lambda/clamping/normalisation. The raw path is configured by `SIMULATIONS_PATH` (default `/home/philiph/dbs/simulations/westminster-latest.npz`), validated against protected output/database targets, and written atomically after DB persistence, only for the final date. A DB failure cannot publish raw draws; a raw-write failure reports the committed election id and preserves the previous file. Dry runs print odds/ranges without database/raw/trend writes and retain explicit CSV-preview semantics. Tests isolate SIMULATIONS_PATH alongside existing database/backup guards.

Piece 6 verification: strict mypy passed all 227 source files; the complete isolated `./run_tests.sh -q` run passed 5,468 tests in 113.57s. On 2026-10-19, the working tree was verified byte-for-byte against the tested source manifest. Focused Westminster/integration suite: 166 passed. Console app construction, export `--dry-run`, and the model's actual CLI `--dry-run --simulations 100` succeeded against isolated paths. Full-size test used a read-only SQLite backup of the live DB, never the live writer: 650 seats, 10,000 simulations for 2026-10-12, 12 regional and 650 seat summaries, raw metadata matched the persisted election id, raw size 17,113,093 bytes. Dry run 5.69s; saved run 15.60s including MC 6.08s, DB replacement 0.08s, existing trend-cache reconstruction 6.87s, and raw compression/publication 1.18s. This is about six seconds for computation; a complete single-date save is slower than the original “few seconds” target because of trend reconstruction. Final point seats differ from HEAD only by Conservative 116→115 and SNP 13→14 (Ashcroft correction); NI seat winners remain 5 DUP, 7 Sinn Féin, 2 SDLP, 1 Alliance, 1 UUP and 2 independents/Others. No live model runs, public export writes, commits, or automatic review workflows were performed. The piece 6 checkpoint remains unchecked until user approval and commit.

2026-10-19 — On the user's explicit instruction, committed the tested piece 6 files as `332ca4bc` (`feat: integrate Westminster projection uncertainty`), then fixed the pre-existing regional-baseline fallback in a separate commit `309bad61` (`fix: exclude ineligible parties from regional swings`). The fallback originated in 9b9556ec on 2026-02-20 and is present at this branch's merge-base with master. It treated SNP's absent Yorkshire result as missing regional data, substituting its 2.519% UK baseline and producing the spurious −2.373pt swing against a 0.146% crossbreak average. The central runner now uses the simulation engine's existing eligibility rule: positive baseline support anywhere makes a party ineligible in regions where its baseline share is absent/zero; a party with no positive regional baseline anywhere remains eligible as a new entrant. Ineligible cells have zero applied swing and an `applicable=false` diagnostic; the CLI and regional CSV show N/A. Both direct regional observations and national-swing fallback are blocked for these cells, preventing positive phantom votes as well as misleading negative swings. NI's separately approved baseline-only policy is preserved. Regression tests cover absent/explicit-zero baselines, negative/positive regional observations, national fallback, new entrants, persisted simulation budgets/seat outcomes and CSV/CLI rendering. Strict mypy and all 5,477 tests pass. A 1,000-simulation CLI dry run on the copied 650-seat dataset displays SNP/Plaid as N/A in Yorkshire and retains the previous central seat totals. No live writes, remote actions, automatic review workflow or piece 7 implementation.


### Piece 7 implementation — 2026-10-19 (awaiting user review and commit)

Implemented the approved console diagnostics piece. Calibration stays separate; two
new blueprints and two read-only services provide budgets, national/regional results,
competitive constituency sorting, date-filtered history, combined outcome/seat-range
filters, per-simulation inspection, constituency drill-down and exact seat decomposition.
Stored central results are used alongside simulations; no fresh calculation is substituted
into a historical page. Older pruned runs retain national results and explain missing
detail. Probability ties split credit in summaries; the explorer's largest-party filter
explicitly includes ties.

Supporting engine changes: regional majority/largest/hung odds are derived from existing
tallies (majority is floor(region seats/2)+1). New raw files add a versioned, validated
snapshot of original inputs/config and an input fingerprint also stored in national
metadata. Seed replay regenerates every stream, including region-masked national errors;
it checks saved winners, seat totals, votes and errors before exposing decomposition.
Old raw files without a supported snapshot still show saved results/errors and filters,
with exact replay explicitly unavailable. Input fingerprints and summary outcome checks
detect stale same-date files. Vote comparisons allow the known float32/rounded-summary
precision boundary; invalid raw seat totals are rejected before histogram calculations.
File/replay caches are bounded and keyed by file metadata.

Interpretation and UI: raw/applied centred SDs, polling-only normal bands, complete poll
provenance, geographic ineligibility and NI exclusions are explicit. Conditional means
are associations, not necessary thresholds. Constituency pages show central margins,
win distributions, interval/margin summaries, flip statistics and replay links. Histograms
use native SVG geometry rather than inline CSS, with expandable numeric count tables.

Verification: strict mypy passed all 233 source files; 5501 tests passed in the full
isolated suite. The focused engine/console run passed 127 tests. A copied full-size database
produced 10,000 draws over 650 seats (17,152,376-byte raw file, 15.55 seconds including
persistence/compression). Independent scalar arithmetic reconstructed all shares/winners
and stored margins at indices 0, 1, 127, 128 and 9999; maximum share difference was
3.55e-14 percentage points, with final-index replay about 1.3 seconds. All nine rendered
page cases passed (latest inspect about 1.6 seconds), without changing the copied DB.
Combined Labour-majority filters matched a hand count of 3060/10000. History/pruned
views, a genuine pre-snapshot raw file, missing/malformed files and election mismatches
passed smoke checks. Export dry-run and console app creation passed.

Artifacts: `/tmp/westminster-piece7-checks-_g0rbver/checks.log` and
`/tmp/westminster-piece7-smoke-qixwt6wf/` (isolated data, generation/render/replay/
compatibility reports). Source hashes match the isolated full-suite checkout. Live data,
exports, Git history and remotes were not changed. No automatic review skills ran;
checkpoints remain unticked pending the user's review/commit direction. Piece 8 has not
started.

Piece 7 follow-up (user requested, 2026-10-19): Results parties are ordered by saved central projected seat count descending, using regional projected counts in regional tables. Odds history uses each date's own projected seat counts. Parties with zero seats sort last; ties retain their existing order. History counts are grouped in one database query rather than loading every vote. Verified rendered order for all 13 national/regional tables and two history dates; strict mypy and all 5,501 tests passed again. Work remains uncommitted.

Piece 7 committed locally with explicit user approval on 2026-10-19: `8588845f feat: add Westminster uncertainty diagnostics`. Includes projected-seat ordering in Results/Odds history and the Back to all simulations link, verified to preserve the run and remove filters (24 console tests passed after that template-only follow-up). Full strict mypy and 5,501-test suite passed before that final link. Three generated data files from the user's console run remain uncommitted. No remote action. Next piece 8 has been briefed only; implementation awaits user direction.

2026-10-19: Finished the console display-fix batch (2dp rounding, round_display filter registered via add_app_template_filter for strict mypy, no "-0.00", seat page parties ordered by central share); committed as b10516b9. The three generated data files from the user's 12 Oct console run remain uncommitted by user choice. Piece 8 approved: include national seat histograms and a basis/interval label in prediction-uncertainty.json.

### Piece 8 implementation — 2026-10-19 (awaiting user review and commit)

User decisions applied: national seat histograms and a basis/interval label are published.
- **Whitelist.** New `model_support/public_uncertainty.py` (numpy-free) is the only place
  public fields are chosen: `trend_uncertainty`, `seat_bands`, `public_document`,
  `basis_label`, `run_size`; `PublicUncertaintyError(ValueError)`. It copies named fields,
  validates type, range and order (incl. histogram counts summing to `simulations`),
  rounds shares to 1 dp (Python `round` of the stored 2 dp value, as the trend `v`) and
  probabilities to 4 dp (fractions, as stored). It never copies budget, seed or hashes.
- **Trends** (`model_support/trends.py`): `reconstruct_trends` reads national blocks in its
  own transaction via new `persistence.iter_national_blocks(conn, scope)`. That function
  decodes one block at a time, shares the schema check with `load_uncertainty` (new
  `_check_schema`) and yields nothing without the table. Parties gain `vl`/`vh`, plus
  `pm`/`pl` only when non-zero; entries gain `hung`. A party missing from the block gets no
  keys. A party in the block without trend votes is ignored. Malformed or foreign-format
  blocks raise `ValueError`, which `trend_batch` and `rebuild_model_trends.py` already
  report.
- **Dedup decision.** Consecutive runs still collapse on equal seats and shares. The
  uncertainty values are not compared, since the seed is the date ordinal so equal days
  differ only by Monte Carlo noise. Whether the run has a summary is compared, so the
  first run with a summary is kept after an equal run without. Without summaries the rule
  is exactly the old one.
- **Trend size.** On the live history, 323 points of 824 runs at about 315 bytes each give
  about 221 KB after a full backfill (today 119,720 → 120,035 bytes, one run with a
  summary). The synthetic 830-run, 650-seat, 13-party test gives 325 points and 228,401
  bytes; it asserts < 250 KB. Worst case, all runs distinct: about 565 KB (the
  no-uncertainty file alone would be about 305 KB).
- **Export.** `scripts/export/uncertainty.py`:
  - `UNCERTAINTY_FILENAME`, `ModelUncertaintyExport`, `model_uncertainty_export`;
  - `latest_model_run` (the export's existing highest-id rule, now shared with the
    console), `model_run_uncertainty`;
  - `sync_uncertainty_file`.

  `build_result_payload(..., seat_uncertainty=)` adds `u: [[pid, lo, hi, win]]` in `p`
  order, skipping `p` parties absent from the seat block. It is omitted when no row
  remains. `serialize.compact_json` is the exact `write_json` text, so the console can
  measure sizes. `export_elections.main` loads the exported `model_uns` run's row
  (`load_uncertainty`) for the full, `--election-name` and `--current-simulation` paths.
  `_export_page(..., uncertainty=)` uses it only when the election id matches.
  `--output-file` carries `u` and writes no uncertainty file. The document is built
  before any file is written, so a malformed row writes nothing.
- **Stale-file decision.** When the exported run has no region block, an existing
  `results/prediction-uncertainty.json` is removed, so stale odds never sit beside a newer
  projection. Dry run prints "Would remove stale uncertainty file"; nothing is printed when
  there is neither a document nor a file.
- **Manifest.** `map-modes-shell.json` Westminster `parliamentFeatures` gains
  `"uncertaintyDataPath": "results/prediction-uncertainty.json"`. It is copied through
  verbatim (tested with a non-default value), and the committed shell's value is tested
  against `UNCERTAINTY_FILENAME`. A scratch regeneration of `map-modes.json` differs from
  the committed one by exactly that line; the real file is left for the publish step.
  `map-modes.md` documents the flag, the `pf-uncertainty-v1` schema and `u`; the README
  results-schema section mentions both.
- **Byte identity.** `tests/export_golden.py` is a fixed scenario (Westminster world plus
  three model runs, one a duplicate). It records the trend file and every file and printed
  line of a full export, a dry run and `--current-simulation`. The goldens
  (`tests/fixtures/export_golden/no_uncertainty.json`) were produced by HEAD b10516b9 from
  a `git archive` copy, and reproduced again from HEAD after the helper's final edit.
  `test_export_golden.py` requires identical text with no rows, with the table dropped,
  and with a national-only (pruned) row; in the last case only the trend file changes.
  On live data, the HEAD export and the new export (with `load_uncertainty` → None), each
  run read-only into a scratch copy of `electionmaps/data`, wrote identical trees and
  printed identical output. HEAD and new `reconstruct_trends` (with blocks suppressed)
  matched byte for byte, and equalled the user's uncommitted trend file.
- **Real output (UNS 2026-10-12, scratch dir).**
  - `prediction-simulation.json` grows 112,539 → 188,273 bytes. All 650 seats get `u`:
    3,793 rows, none missing. 154 stored seat ranges belong to parties with no `p` row,
    and none of those ever wins.
  - `prediction-uncertainty.json` is 8,996 bytes. Labour: v 22.8–30.6, s 220/301/376, pm
    0.306, pl 0.9697. P(hung) is 0.6938. 12 regions, all with regional odds.
  - The live `export_elections.py --dry-run` prints "Would write uncertainty: …
    (12 regions)".
- **Console (Settled 22).** The hub has a "Published export" panel: new
  `console/services/uncertainty_export.export_preview`, plus `published_export` in the
  hub blueprint. It shows:
  - the run the export would ship and the basis label;
  - each file's exact size and contents;
  - the trend-entry additions and the count of runs with stored national blocks;
  - the stored fields that are never published.

  A malformed summary shows the export's error, and an empty database shows "No model
  run to export." On the live DB it renders in 0.26 s, and its sizes equal a real
  export's.
- **Tests.**
  - New files: `test_public_uncertainty.py` (25), `test_export_golden.py` (3).
  - Additions: trend keys, rounding, omission, dedup both ways, missing parties, a
    malformed block and the CLI exit, foreign schema, undated-run guard, size;
    `iter_national_blocks` scope, pruned runs, no table, refusal; `u` order and
    skipping; real 50-sim runs through `main()` (document equals the whitelist output,
    no private keys, flag copied, dry-run line, stale removal in both modes,
    `--current-simulation`, `--output-file`, another run's row never attached, malformed
    row writes nothing); the committed shell flag; the hub panel (sizes equal a real
    export, no detail, bad summary, no run).
  - Mutation-checked: dedup flag and values, the undated-run guard, zero omission, the
    histogram sum, share rounding, region hung, `p`-order alignment, stale unlink, the
    no-file no-op, the election-id match, the scope SQL. All were killed.
  - Strict mypy passes (235 source files), and the full suite has 5,561 passed. The
    console `create_app()` smoke check passes.
- Finding (fixed in the review fixes below): the export picked the model run with the
  highest id, not the latest date. Before the fix, a later re-run of an older date
  became the exported run. Its detail is pruned, so the export shipped no
  `u` and would remove the uncertainty file until the latest date is re-run.
- Not done: no commit, no live DB writes, no writes to the repository's
  `electionmaps/data` outputs (the three uncommitted user files are unchanged). The
  checkpoint stays unticked pending review.

Piece 8 review fixes (user approved all, 2026-10-19; uncommitted):
1. **One "latest run" rule.**
   - New `persistence.latest_run(runs)` (max by (as-of date, id)), `latest_scoped_run(conn,
     scope)` and `latest_saved_run(sqlite_path, scope)` (never creates the file).
     `_prune_detail` and `uncertainty_status` use it.
   - `scripts/export/uncertainty.latest_model_run` now picks all `model_uns` runs with a
     dated name (via `westminster_uns_scope(map_id).date_from_name`), takes
     `latest_run`, then loads that `Election` with its map. This covers the full export,
     `--current-simulation` and the console panel.
   - Decision: a `model_uns` run with an undated name can no longer be the current
     prediction. Documented in the function and in `map-modes.md`.
   - US model selection in `export_elections.py` (highest id per US model type) is
     deliberately unchanged.
   - On live data the date rule and the old id rule pick the same run: the HEAD and new
     exports with uncertainty off produced identical trees.
2. **Raw/meta gating.** `run_simulation` writes the raw `.npz` only when the saved run is
   `latest_saved_run`'s id; otherwise it prints "Raw simulations not written: UNS <date>
   is not the latest dated run (latest: <date>); the raw file is left unchanged." `main`
   writes the trend metadata only when `cfg.as_of_date` is the latest dated run's date,
   otherwise "Trend metadata not written: …". Tested both ways (an older re-run leaves
   raw bytes and meta bytes unchanged; saving the latest date writes both).
3. **Older-rerun tests.** A run one day before DAY is saved after DAY (higher id). The full
   export (real and dry run, with a pre-existing file), `--current-simulation` and the hub
   panel all ship `UNS DAY` with `u` and the uncertainty file, and print no stale line.
4. **Reverse dedup.** An equal run is skipped unless it has a summary and the previous
   kept entry does not. So an equal no-summary run after a summary run is dropped, and
   the earlier odds carry forward. Tested both directions; byte-identity is unchanged
   (goldens).
5. **Field-set constants.** `PUBLIC_NATIONAL_FIELDS`, `PUBLIC_NATIONAL_PARTY_FIELDS`,
   `PUBLIC_REGION_FIELDS`, `PUBLIC_REGION_PARTY_FIELDS` and `PUBLIC_SEAT_PARTY_FIELDS`
   are exported.
   - `_public_fields` builds party blocks from them in that order.
   - The panel's "never published" list uses them for national, national parties,
     regions, region parties and seat parties. Live: national budget, config_hash,
     input_hash, seed; regions budget; the party levels none.
6. **Consistency checks.** Σpm + hung and Σpl must be within (terms) × 5e-5 of 1 (checked
   on the published, rounded values). Each check is skipped where a regional term is not
   stored. Each national seat range must sit inside its histogram, whose first and last
   bins must be non-empty. Both apply in `public_document` and `trend_uncertainty`. The
   live run passes.
7. Real writes print "Wrote uncertainty: <path> (N regions)".
8. `main` builds `ModelUncertaintyExport` once for the exported model run and passes it to
   `_export_page(model_uncertainty=)`, so the id-match branch is gone.
   `model_run_uncertainty` raises `ValueError` if handed another election's row (tested).
9. Style:
   - `payload.py` imports `Mapping`/`Sequence` from `collections.abc`.
   - New test lines are wrapped, `import json` and `UNCERTAINTY_FILENAME` are hoisted in
     `test_export_manifest.py`, and `import export_elections` is hoisted in
     `test_export_elections.py`. `export_golden.py` keeps its inline import, with a
     comment saying why.
   - The panel's `<th>` cells have `scope="col"`.

   Optional `json_extract` was skipped: the Python decode is one block at a time and fine.

Re-verified:
- The goldens were regenerated from a fresh HEAD archive and are identical.
- On live data, the HEAD export and the new export (with uncertainty off) wrote identical
  trees and printed identical output. The real export is unchanged in size: 188,273 and
  8,996 bytes, now with a "Wrote uncertainty" line. Live trends are unchanged.
- The live `--dry-run` prints "Would write uncertainty … (12 regions)".
- Mutation checks killed every mutant (date rule, raw and meta gates, both dedup
  directions, odds checks and their tolerance, histogram ends, region and national odds
  calls, latest_run tie-break).
- Strict mypy passes and the full suite has 5,578 passed; the console smoke check
  passes. The three user files are unchanged.

Piece 8 addition (user request, 2026-10-20; uncommitted): **scratch exports.**
- **Cause.** `--output-root` was honoured only by `--current-simulation`. The full export,
  `--election-name` and `--metadata-only` always wrote to `PAGE_OUTPUT_ROOTS`, so the
  user's `--output-root /tmp/export-check` export wrote into the repository. The files it
  wrote there are now user files and are left as they are.
- **`--output-base DIR`** (new) moves every page root to `DIR/<page>/data`
  (`resolve_page_roots`). This applies to the full export, `--election-name`,
  `--metadata-only` and `--current-simulation`.
  - A missing root is seeded by `shutil.copytree` from the repository's page data
    directory, printing "Seeded scratch page: <src> -> <dst>". The export reads the
    shell, existing manifest, maps and parent results there. An existing root is reused
    unchanged.
  - Decision: `--dry-run` still seeds, since that writes only inside DIR.
- **`--output-root`** defaults to None and is accepted only with `--current-simulation`.
  Otherwise the parser exits with "--output-root applies only to --current-simulation;
  use --output-base DIR …". The two flags are mutually exclusive.
  - `--current-simulation` writes under `--output-root`, else the (scratch) UK page root.
  - `OUTPUT_ROOT_DEFAULT` was removed: it was unused, and `PAGE_OUTPUT_ROOTS` holds the
    same path.
- **Docs.** Module docstring, `parse_args` docstring and `--help`, the README export
  section and the AGENTS.md smoke-check note are updated.
- **Tests** (`TestOutputBase`; plus `output_base=None` in `test_export_ordering`'s fake
  Namespace):
  - The full export writes only under the base, which ends up with exactly the golden
    full-export files and output, while the "repository" root stays at its shell.
  - An existing base is reused (no reseed; a marker file survives).
  - A dry run seeds and writes no outputs.
  - `--metadata-only` and `--current-simulation` honour the base.
  - The three misuse cases are refused before anything is written.
  - Goldens still pass. The suite has 5,586 passed and strict mypy passes.
- **Demo.** `scripts/export_elections.py --output-base <scratchpad>/export-check` against
  the live DB (read only) exited 0. It seeded both pages and wrote "Wrote uncertainty …
  (12 regions)" and both manifests (15 and 25 elections).
  - The protected files' sha256 and a hash of every file under `electionmaps/` and
    `uselectionmaps/` are unchanged, and `git status` is unchanged.
  - The scratch trees equal the repository's page directories file for file (`diff -rq`),
    as expected after the user's own export from the same DB.


2026-10-20: Piece 8 committed as e4aad160 (code only, incl. date-rule latest run, raw/meta gating, --output-base). Generated data left uncommitted for piece 11: prediction-uncertainty.json (new), prediction-simulation.json, electionmaps map-modes.json (+uncertaintyDataPath), US forecast refresh, and the user's console-run trend files. Paused before piece 9.


Piece 9 implementation (2026-10-21; uncommitted, awaiting manual review): seat
popups retain vote-share bands and win probabilities; totals show ranges only
for national or exactly-one-region selections; headline chooses the party with
highest P(largest), with the nowcast qualifier. Display values use at most two
decimal places. Optional loading checks election, parliament, view and result
object identity. Export payloads now share a content-derived uncertaintyId;
missing or mismatched summaries leave central results usable. The console
export-size preview uses the same helper. No-uncertainty payloads remain unchanged.

Verification: all 537 Vitest tests passed; strict mypy passed 240 files; all 5589
Python tests passed in an isolated source copy without the live environment.
Both page bundles and shared CSS rebuilt. jsdom smoke checks covered a fresh
1000-draw 2026-10-19 projection (650 seats), popup formatting, national/one-region
ranges, hiding ranges under a party filter, Westminster 2024, US presidential
2024 and the current US Senate projection. These check rendered DOM behaviour,
not browser layout geometry. Simulation, trends and export writes were confined
to /tmp/westminster-piece9-aiwnqz4b and a copied SQLite database. Scratch preview
served on localhost:8019. No automatic review passes, commits or remote writes.
Piece 9 checkpoint stays unchecked pending manual approval and local commit;
pieces 10 and 11 have not started.


Piece 9 UI refinement (2026-10-21, still uncommitted): user requested one
explanation in the headline area, removing duplicate prose from seat popups and
vote totals. A Show uncertainty/Hide uncertainty button beside Show all starts
off and controls only the totals interval columns; popup ranges remain visible.
Its selection persists across filters, but unavailable intervals and the button
hide under unsupported filters. The 90% seats column follows the seats delta.
After discussion the user retained national and region-only interval support;
party/majority filters still use filtered central totals and hide intervals.
The proposed full-table override for map filters was withdrawn and not retained.


Piece 9 approved and committed locally 2026-10-21: dc5426f5
(`feat: show Westminster uncertainty on election maps`). Includes the default-off
totals toggle, explanation moved to the headline and matching subtitle typography.
Final frontend verification: 542 tests passed, both bundles/shared CSS rebuilt,
DOM smoke checks passed for toggle, national/region filters, party/majority
hiding, Westminster 2024 and US presidential 2024. Backend verification remains
5589 tests and strict mypy across 240 files; no backend changes during UI refinement.
Working tree clean after commit; nothing pushed. Piece 10 brief supplied next;
implementation awaits the user's go-ahead.


Piece 10 implemented (2026-10-21; uncommitted, awaiting manual review): vote
bands are drawn behind dashed vote lines; the Chances view has Majority/Largest
party modes, a 0–100% axis and a Hung line. Tooltips show percentages and vote
intervals with at most two decimal places; the view explains its nowcast basis.
Ordinary data hides the optional controls/bands. Omitted compact pm/pl values
mean zero only when the run and party carry valid uncertainty; malformed values
remain unavailable. Chart bounds include interval endpoints, isolated readings
remain visible, and explicit missing runs break uncertainty segments.

Implementation separates parser, uncertainty rendering helpers and view wiring.
The backend/export formats are unchanged. Verification: full Vitest suite
passed (561 tests), asset build passed, and real-module jsdom checks covered
actual scratch history, legacy payloads, bands/tooltips, Majority/Largest
switches, Hung, the 100% axis and metric visibility. Westminster prediction,
Westminster 2024 and US presidential 2024 smoke checks also passed. No browser
layout geometry was verified automatically.

Preview uses the existing copied database plus six scratch-only retrospective
runs for 2026-10-13 through 2026-10-18 (1000 simulations each), alongside the
previous Oct12/Oct19 data; the compact trend cache has six uncertainty-bearing
entries over Oct12–19. Its latest-poll metadata was refreshed from the same
scratch run. No live database or tracked published data changed. Preview URL:
http://localhost:8020/electionmaps/?view=polltracker&parliament=westminster
Use Last30 to inspect the recent bands/odds; older uncertainty awaits piece11.
No review skills, commit, remote writes or piece11 work were started.


Piece 10 scope extension approved 2026-10-21: user requested seat bands after
reviewing the vote-band preview, then asked to resume the proposed extension.
Seat-count intervals already stored in national summaries are now carried
through the trend payload as sl/sh and plotted around solid seat lines.
These are counts, not seat percentages. No new simulation or model assumptions
are introduced. Band visibility follows the Seats/Vote% checkboxes; Chances
continues to show probability lines. Existing caches without seat bounds
remain readable. Scratch trends are regenerated from the copied database;
no live operational backfill is authorized or performed.

Seat-band extension verification (2026-10-21): 568 JavaScript tests passed;
strict mypy passed across 240 files and all 5594 Python tests passed in
the isolated source copy. Built assets and preview refreshed. DOM checks
covered both metric bands, seat-only/vote-only visibility, tooltip range
220–380 matching the saved Labour result, Chances without bands, and legacy
payload fallback. The compact 830-run/13-party synthetic history is 287,842
bytes; its regression guard was updated from 250 KB to the plan's 300 KB
budget to include seat bounds. Piece 10 remains uncommitted pending review.

Piece 10 default refinement (2026-10-21): tracker opens in Seats & votes view with Seats checked and Vote % unchecked. Users can enable Vote % independently. Fresh-load DOM checks passed with uncertainty and legacy data; assets rebuilt. Still uncommitted.

Piece 10 band styling (2026-10-21): strengthened seat/vote-band fills and added thin party-colour borders; isolated range whiskers also made clearer. Central lines remain thicker and bands do not intercept pointer events. Assets rebuilt and uncertainty/legacy DOM smoke checks passed. Still uncommitted.

Piece 10 band fade (2026-10-22): user requested shading to weaken with distance from the central line. Both seat and vote fills now fade towards their interval boundaries while retaining party-colour outlines. The fade follows the central curve at each date; interval bounds and missing-data gaps remain unchanged. If the central estimate lies outside its interval, the fill anchors at the nearest boundary. This is a display treatment, not a reconstructed probability density. Assets rebuilt and JS/DOM checks passed; still uncommitted.

Piece 10 final band treatment (2026-10-22): removed party-colour interval borders at user request. Full interval base fill is 20% opacity; 16 evenly spaced nested ribbons at 1.2% opacity each produce approximately 21% at the outer edge and 34% centrally. This is a gently nonlinear, stepped alpha-compositing fade, not an exact linear or quadratic scale. Central lines and isolated range markers remain. Focused renderer tests and actual/legacy tracker DOM checks passed; assets and scratch preview refreshed. Still uncommitted.

Piece 10 opacity refinement (2026-10-22): user increased central shading target to approximately 75%, superseding the earlier 34%. Full interval base remains20%; sixteen inset ribbons at7% opacity combine to74.95% centrally. Ribbon widths16/17 through1/17 leave the outer edge at20%. Borders remain absent. Focused tests and uncertainty/legacy DOM checks pass; assets/preview refreshed. Still uncommitted.

### Piece 10 completion — 2026-10-22

User approved the final tracker treatment; committed locally as `423a8419` (`feat: add poll tracker uncertainty bands and chances`). Seat and vote ranges, Chances views, and missing-data handling are complete. Seats default on; votes default off. Final bands have no borders and fade from approximately 75% combined opacity at the central line to a 20% outer floor, using 16 nested ribbons. This is display shading, not a density estimate.

Validation: full JavaScript suite 570 passed; full isolated Python suite 5594 passed with strict mypy (240 files); subsequent styling refinements passed the 7 focused helper tests, asset builds, and actual/legacy tracker DOM smoke checks. No live database or published data changed. Piece 11 remains unstarted; its brief is the next handover.

### Piece 11 progress — 2026-10-22

Readiness audit (read-only): schema, sample sizes, config (hash 730a3349d3836bc6) all ready; all 840 model_uns runs (2024-07-05 → 2026-10-22) already carried uncertainty under the current config. Gap found: 8 of 10 polls imported after 2026-10-12 had no poll_bases (likely imported through a console process started before base capture). With user go-ahead: backed up; backfilled 97 bases for those 8 polls (dry run first); re-ran the model 2026-10-05 → 2026-10-22 (18/18 succeeded; detail and raw file on 2026-10-22); backed up after each write.

Sanity checks across 840 runs: safe seats ≥95% except Basingstoke (15.7pt, 94.5%) and North Herefordshire (21.7pt, Green, 92.5%); 50 marginals <1pt at 33–53% (median 50%); leading-party national 90% band median 7.9pt. Central Labour seats sit above the 90% range in 37 runs Jul–early Oct 2024 (asymmetric seat noise around the post-landslide projection) — accepted per handover. 21 odds jumps >15pt, all Aug–Dec 2024: extreme ones are zero-poll windows (excluded from the trend file), the rest 2–3-poll windows; none from 2025 onward.

Staged export via --output-base reviewed (UNS 2026-10-22: Labour central 300, seats 199/282/359, P(maj) 0.189, P(largest) 0.939, hung 0.810; trend 440 entries all with uncertainty), then real export into the repo with user approval — repo output identical to the staged copy. Data files remain uncommitted for the user's review/commit; remote publication is a separate user action.

2026-10-22: Poll tracker follow-up (user requested), committed as 9bb8c2c8: an "Uncertainty" checkbox right of Vote % (default on; off hides bands and tooltip 90% ranges, hidden when data has no bands); view (Seats & votes | Chances) and chance measure (Majority | Largest party) converted to radio groups in the Seats/Vote % pill style; mode-specific controls moved to a second row, with the view switch and date range on the first. 573 JS tests pass; bundles rebuilt.

2026-10-22: Exported data committed as 40d3933f (prediction-uncertainty.json, prediction-simulation.json, trends, map-modes.json uncertaintyDataPath, US forecast refresh). Branch ready to merge; remote publication left to the user.

2026-10-22: Merged origin/master (5 commits: Oct 13/15/20 poll exports, US matchup-rename fix, Senate controller test fix) into the branch as 35e527a1. The only conflicts were model_output_trends.json and prediction-simulation.json (master held older uncertainty-free exports); resolved by regenerating from the DB (rebuild_model_trends.py + export_elections.py), which reproduced the branch versions byte-for-byte. 5608 Python + 573 JS tests pass. Branch now fast-forwards onto master; push/merge to master is the user's action.

# Plan: add-us-election-uncertainty-and-overall-poll-trackers

## Context

### Approval and checkout

The user approved the brief on 2026-10-22 with `$architect-build go`, after settling the scope below. This is an architecture artifact, not authorization to implement, migrate the live database, backfill live results, or publish. Implementation proceeds one piece at a time, with a full brief before each piece and the user's go-ahead. Follow the project's separate approval/commit workflow; do not automatically launch review skills or advance pieces.

Comprehension was refreshed at clean branch `poll-errors-simulation`, HEAD `35e527a1`. This includes the Westminster implementation, published exports, and later tracker refinements. Earlier working-tree changes to US polling imports have since been committed/merged; inspect the actual checkout again before implementing. Do not revert intervening changes. The predecessor issue is `add-monte-carlo-uncertainty-to-westminster-projection` in the same project-data directory; verify its final interfaces rather than using an earlier snapshot.

### Settled product decisions

- These are **held-today nowcasts**, conditional on the selected candidate matchups and the model's assumptions. Do not introduce future campaign movement or describe the probabilities as election-day predictions.
- House: all 435 districts, party seat distributions, projected national vote-share distributions and majority chances.
- Senate: the full 100-seat chamber, with non-contested seats fixed and contested races simulated. Keep raw party seats separate from caucus/control outcomes. Explicit independent alignments and a configured vice-presidential party determine control in tied caucus outcomes. Report the 50–50 caucus split probability separately, including when a configured VP resolves control.
- President: electoral votes and national popular vote. Report probability of an electoral majority and probability of no electoral majority. Do not simulate congressional resolution of a contingent election, faithless electors or litigation.
- Full Westminster-style experience for each model: public ranges/headlines/tables/popups, overall tracker and Chances, console calibration/error budget/results/history, simulation list/detail and race-level replay.
- Keep the existing House generic-ballot aggregate feed (also used by Senate). Calibrate its aggregate error directly; do not invent respondent counts or treat aggregator snapshots as independent surveys.
- Preserve existing central projections, polling windows, decay and local-poll blending. Changing a central estimator, candidate eligibility rule or election-allocation rule is a separate explicit decision if investigation shows it is needed.
- Reuse the latest shared UI: "Seats & votes | Chances" radios, secondary metric controls, All/30/90 windows, party selection, totals on/vote share off, tracker Uncertainty on, borderless fading bands with approximately 75% centre/20% edge opacity. Preserve current five-update band smoothing and actual unsmoothed date-specific tooltip bounds, with gaps respected. Current-map uncertainty toggles follow their own existing default-off behavior.

### Timing and deliverable (user decision, 2026-10-22)

House and Senate polling day is 2026-11-03 (`uselectionmaps/data/map-modes-shell.json:26,61`).

**The plan aims to publish House odds, then Senate odds, before polling day.** The user judges readiness at each gate. That judgement includes whether clearly labelled assumptions are acceptable for publication if calibration is not complete in time. The builder must not assume it is too late, and must not cut corners to make the date. The sequencing (House slice first, Senate next, President last) serves that aim.

Whatever ships before 3 Nov, the work also delivers:
- the 2026 House/Senate nowcast history from 2026-06-29 (the first generic-ballot snapshot and first model run);
- 2028-ready machinery (2026 results as baselines, the Class-3 field, new specials);
- piece 13's scoring against the 2026 result, the only genuine out-of-sample test this pipeline will get.

### Resolved decisions (user, 2026-10-22, after review)

1. **Calibration source policy: one curated source per series**, following the Westminster/PollBase precedent. That means one archive for final generic-ballot aggregates and one for final state averages, each with a source URL per cycle, committed as `us_final_poll_errors.json` with a builder script. Piece 1 names the specific sources for sign-off.
2. **Trend compression: the Westminster rule.** Range and odds values are ignored when collapsing equal runs, since same-day reruns differ only by seed noise. An equal run is kept only when it is the first with a summary. Context changes (cycle, matchup, roster or control policy) always break segments, whatever the totals.
3. **Absent-party behaviour: fix phantoms only.** A party with no baseline vote in a seat gets no national or regional swing there. It still gets a share when that seat's own polls name it, so a new 2026 candidate is still modelled through the blend. Before this change, the live House run gave positive votes to parties with no 2024 baseline: 20 Democratic seats (up to 18.7%), 2 Independent (up to 32.7%), 4 Libertarian and 1 Republican.

   This is a separate, explicitly approved central-model commit, the analogue of Westminster's `309bad61`. It lands before piece 5 pins zero-error parity. It must:
   - preserve "Others" behaviour;
   - list every seat whose result changes;
   - add regression tests for absent baseline with a seat poll, absent baseline without one, and a new entrant.
4. **Pre-election target:** see "Timing and deliverable" above.

### Existing implementation and integration seams

Paths below are repository-relative; newly proposed modules are labelled as such.

- `data/models/us/_common.py`: shared `UsModelSpec`, `UsSimulationConfig`, `PollReading`, `collect_poll_readings`, `aggregate_national`, `aggregate_seat_polls`, `blend_seat_swings`, `project_seat_votes`, `persist_projection` and orchestration. Local blending is alpha = W/(W+k); Maine/Nebraska presidential districts inherit their parent state's blended fallback. Selected matchup, partial-poll admission and absent-party rules already affect the central result.
- `data/models/us/run_us_house_model.py`, `run_us_senate_model.py`, `run_us_presidential_model.py`: model-specific specifications. Senate uses House generic ballot plus race polls, a contested field and special-election baselines. President requires a tracked national matchup and records target election year.
- `data/polls/importers/us/us_wikipedia_polls.py`: generic-ballot tables contain aggregate estimates; other accepted tables contain individual polls. Parsers capture sample size and LV/RV/A population labels, but the persisted poll model currently lacks that population/evidence-kind distinction. `data/models.py`, `data/db.py` and the import queue/CLI are affected by provenance changes.
- `data/model_support/monte_carlo.py`, `uncertainty.py`, `westminster_uncertainty.py`, `simulation_replay.py`: existing simulation and replay facilities. The Westminster projection inputs assume regional swings and the tally counts each unit as one seat. Its calibration, NI handling and proportional-swing uncertainty are not US model defaults.
- `data/model_support/persistence.py`: `UncertaintyPayload` and `replace_output` already provide scoped atomic result/summary replacement, latest-detail retention and compact historical summaries. `ModelUncertainty` stores versioned JSON blocks. US `persist_projection` does not yet supply uncertainty.
- `data/model_support/trends.py`: already reconstructs US histories and reads scoped stored uncertainty. Presidential rows contain electoral-vote `e` and popular-vote `v`; existing `s` is allocation-unit wins. Senate `s` counts contested wins, not a full chamber. Never silently change those field meanings.
- `data/electoral_votes.py` and presidential export consumers: database allocations for the target cycle are authoritative. National popular totals exclude district duplicates while district electoral votes remain included.
- `electionmapslogic/features/senate-chamber.js` and `senate-forecast-controller.js`: frontend full-chamber merge exists, but backend history does not yet persist corresponding composition. `ElectionData.fromSeats` clears uncertainty identity. A full-chamber view needs a matching saved aggregate, not borrowed contested-seat uncertainty.
- `data/scripts/export/uncertainty.py` and `export_elections.py`: Westminster-only latest-scope/filename/attachment paths. Public whitelist in `public_uncertainty.py` and result-summary identity hashing should be extended, not bypassed.
- `electionmapslogic/uncertainty*.js`, `features/polltracker*.js`, `fragments/polltracker.html`, `state.js`: reusable UI with Westminster-specific measure labels and `pm/pl/hung` assumptions.
- `uselectionmaps/uselectionmaps.js`, `index.html`, `data/map-modes-shell.json`: US does not yet load/register the tracker fragment/module; all three features currently enable prediction only. Generated `map-modes.json` must come from source-shell export.
- `data/console/blueprints/uncertainty*.py`, `console/services/uncertainty*.py`, and `templates/uncertainty*.html`: existing console implementation is scoped to Westminster. `console/services/us_models.py` already serializes console US model jobs and their export step.

Verified detail added by the 2026-10-22 review (checked at `35e527a1` and against the live DB, read-only):

**Senate central field**
- `run_us_senate_model.py` builds the contested field **at import time**.
- Specials come from `senate_special_elections()`. It uses `date.today().year` (L133) and drops specials once `nextElectionYear < year_now` (L143).
- The Class-2 allowlist is read from the live `senate-current.json` (L83–101).
- `"2020 US Senate Election"` is hard-coded as the baseline.
- So a retrospective run after 2026, or after a roster edit, projects a different field. This is an existing **central** reproducibility gap.

**Trend publishing**
- `trends.reconstruct_trends` (L284–309) validates every stored national block in a scope with `public_uncertainty.trend_uncertainty`. That requires `pm`/`pl`/`hung` (L214–221, `_check_odds` L307–327).
- `persistence._check_schema` hard-matches `pf-model-uncertainty-v1`.
- So storing a US block before readers dispatch on schema would break US trend publishing.

**Engine (`monte_carlo.py`)**
- It has no per-seat swing input; `SimulationInputs` takes region × party swings only.
- Tallies are unweighted.
- It requires `2·threshold > seat_count` (L1937).
- With all-fallback regions it draws national sampling variance independently per region. Every US region falls back today, so this would decorrelate the national error.
- `WestminsterPollAggregation.sampling()` raises on any missing n.

**Live evidence**

| Model | National evidence | Seat-level polls |
|-|-|-|
| House | 47 generic-ballot aggregator snapshots, all N null | 265 district polls, 11 without N |
| Senate | none of its own (uses the House generic ballot) | 495 state polls, 3 without N |
| President | 50 national polls, only 17 on the tracked matchup | **1** state poll |

- No US `poll_bases` rows exist.
- All 230 US pollsters have weight 1.0.
- Only one US poll predates the 2024 election.

**Central-model quirks that affect calibration**
- The national swing subtracts the raw, undecided-inclusive generic ballot from vote-share baselines (`_common.py:860-864`).
- Seat polls, by contrast, are decided-normalised.
- Senate subtracts its own 2020/2022 field baseline from the House generic-ballot level.

**Results available for calibration**
- House: 2022 and 2024 only, both on the 2024 map (2022 on different lines in several states).
- Senate: 2020, 2022 and 2024, different classes each time.
- President: 16 elections, 1964–2024, 56 units each.

**Importer.** Where a pollster publishes LV and RV variants, the importer keeps the first and drops the rest (`us_polls_common.py:1166-1216`). LV/RV cannot be recovered without re-fetching.

**Export and runs**
- US export selects the highest id per model type (`export_elections.py:527`), not `persistence.latest_run`.
- The Westminster seed is `as_of_date.toordinal()`.
- `SIMULATIONS_PATH` defaults to `westminster-latest.npz`.
- Console rebuilds time out at 3,600 s, over roughly 600 presidential dates.

**Frontend**
- US trend files already ship: House 42 runs, Senate 63, President 27, with fields `s`, `v` and `e`.
- The tracker parses `pm`/`pl` only when a run carries `hung` (`polltracker.js:19-20,89`).
- `app.js:185-188` captures `state.electionData` for the uncertainty fetch, so switching to Full Senate mid-fetch loses a legitimate attachment.
- There is no `polltracker-view.test.js`.
- The shared radios are labelled "Seats & votes | Chances".

### Boundaries and known gaps

No new individual generic-ballot importer is in scope. No joint probability of unified House/Senate/presidential control is promised: three standalone simulations cannot be combined by multiplying marginal probabilities. House/Senate 2026 and President 2028 also represent different cycles. Existing FPTP/selected-matchup simplifications remain explicit; this work does not add ranked-choice, runoffs, primaries or redistricting mechanics.

Live US evidence coverage was initially unverified because the first comprehension attempt could not open the database. The review and piece-1 read-only audit on 2026-10-22 subsequently verified the inventory recorded above and in `data/models/us/uncertainty-readiness-2026-10-22.json`. Historical calibration archives still need complete curation, and chamber contexts must be pinned. Missing inputs must not result in fabricated precision or silently borrowed UK coefficients.

## Approach

### 1. Separate model context, uncertainty mechanics and outcome counting

Introduce small typed model descriptors/adapters instead of three copies of the engine or conditionals throughout every view. An immutable run context identifies model, map, as-of date, target cycle, selected matchup fingerprint, baseline(s), central-model options, aggregation rules and uncertainty configuration/version.

The US adapter supplies the existing deterministic pre-clamp projections plus explicit error loadings. Shared machinery handles seeded/chunked sampling, projection normalization, distribution summaries, compact storage and replay where semantics match. A US-specific projection kernel is appropriate for the blend graph; do not force district/state blends into Westminster's regional-swing arrays. Preserve Westminster's public v1 schema and numerical behavior through compatibility paths and regression fixtures.

The first version retains existing same-date replacement semantics within each model/map/name-prefix scope. It does not introduce parallel scenario storage. Record all context changes and break history segments where meaning changes. Latest detailed draws remain per existing model scope; older runs keep compact summaries, not a promise of full replay forever.

### 2. Evidence and US-specific errors

Represent `individual_poll`, `aggregate_estimate` and legacy `unknown` explicitly. Preserve source URL/identity, dates, population code, sample basis, matchup and actual selected contributors. Unknown historical evidence is audited and repaired from provenance, never classified solely because its sample size is missing.

For individual polls, compute sampling uncertainty from real usable respondent bases and the same weights/admission as the central model. Preserve RV separately from LV and all-adult samples; electorate population and decided-voter denominator are distinct concepts. Apply any design-effect/denominator assumptions explicitly in US config and diagnostics. A contributing individual poll with unresolved required N stops uncertainty with poll/scope repair details; `--no-uncertainty` remains available. Excluded/zero-weight/unmatched polls do not block a run. Absence of any local poll means a model-based fallback error, not zero error.

For generic-ballot aggregates, uncertainty describes the error of the complete aggregate estimator actually deployed. Repeated snapshots and overlapping aggregators do not add independent respondents or reduce error as 1/sqrt(snapshot count). Retain central averaging behavior, but use a shared aggregate-estimation component and a calibrated non-vanishing error floor. If underlying polls become available later, that is a separate evidence/estimator change.

Use shared national, state and race error factors, with each source drawn once per simulation and reused along its paths. A first-version ordinary-race specification is `x = baseline + central_swing + G + H_state + (1-alpha)*R_race + alpha*L_local`, where G is shared national error, H is shared state residual, R is baseline-extrapolation residual, and L contains genuine local sampling variance plus a local polling-error floor. Calibrate the components jointly/conditionally to avoid counting the same error twice. This formula specifies candidate loadings; apply the existing masks and “Others” branch rather than mechanically perturbing every party.

For generic-ballot models, start with G calibrated as total aggregate error and add no separate generic-ballot sampling term. Validate its exposure in House versus Senate; sharing a central input does not imply identical residual behavior. A later split into common systematic and aggregate-specific estimation error needs evidence and an explicit contract revision, not additional variance bolted onto the total. State/race residuals are conditional on that national component.

Preserve the same alpha and parent graph as the central model: local estimation errors enter with alpha, fallback residuals with (1-alpha), and inherited parent polling errors remain shared with child districts. Common national/state factors apply once; children inherit only the appropriate parent residual graph rather than adding those common factors again. Individual sampling variance therefore scales with squared loadings, including parent/child products. Specify covariance/loadings and whether every calibrated component is total or incremental in the report. The proposed factors are a model specification to test, not a claim the current data identifies all of them.

Implement a centred, compositional party-error representation consistent with current clamp/normalization rules; do not force independent Democrats/Republicans errors or assume all races are two-party. Preserve current absent-party/eligibility behavior. Do not transplant Westminster's proportional-swing lambda. Zero error must reproduce the existing US projected shares, rounded votes, winners and aggregate totals with the existing tie-break rules.

Calibration must use archived US estimates/polls and official results, grouped by election/cycle and source dependence. Separate fitted parameters, externally sourced assumptions and sensitivity settings. Hold out whole elections where data permit, rather than treating hundreds of correlated districts as independent validation samples. Evaluate interval coverage, probability reliability/Brier scores, aggregate outcome coverage and sensitivity to national/state correlation and tail assumptions. Poll-rich and unpolled races need separate checks. Senate state versus race residuals may not be separately identifiable from one race per state; pool/regularize or use a combined component and say so.

A held-today nowcast far from election day has no directly observable same-day election outcome. Near-election validation and historical final-poll residuals inform the model but do not validate every earlier nowcast. Do not fit campaign movement into the nowcast error merely by comparing early polls with later results. Resolve and document usable calibration sources before claiming production odds. If evidence cannot support a component, provide labelled assumptions and sensitivity results for review; public enablement waits on an explicit calibration acceptance decision.

### 3. Correct aggregation and control

- House: sample all district winners; one seat per district. Counts sum to 435, majority threshold 218. National vote uses the current baseline-turnout weighting and is labelled projected vote share, not generic-ballot share.
- Senate: pin the roster, contested `(state, class, cycle)` identities, special-election substitutions and unchanged holdovers. Replace the correct member in each simulation, with 100 members total and no duplicated specials. Preserve the existing unsupported same-state multiple-contest limitation unless the builder obtains a separate scope decision; reject ambiguous fields rather than silently dropping a race.
- Senate party distributions and caucus-control distributions are separate. Configure independent alignment explicitly, including candidate/race identity where a generic Independent party would be insufficient. Never assume all independents caucus together. Exactly 50/50 between the two configured governing blocs is the tied-chamber event. Apply the configured VP bloc only to a resolvable tie. Unaligned/unknown outcomes remain explicitly unresolved, not forced into either bloc. Retain both outright majority and control metrics; control probabilities plus unresolved probability sum to one.
- Pin Senate policy per target cycle/as-of context. Configuration needs provenance/effective dates; no automatic lookup of today's VP at replay time. A future cycle with an unknown VP requires an explicit scenario assumption, displayed as conditional.
- Senate national vote share means **votes in contested races**, never votes for all 100 seats. The overall full-chamber tracker defaults to seats and omits vote share; the seats-up view can expose clearly labelled contested-vote share and intervals. State aggregates distinguish contested versus full membership counts.
- President: count allocated EVs for the target cycle, using statewide/district allocations already in the database. Standard covered cycles total 538 and require 270 for a majority; derive/validate thresholds from the resolved allocation, not geographic-unit count. Keep a separate national popular tally that excludes duplicated Maine/Nebraska district votes. Draw correlated state/district outcomes; do not independently randomize their inherited polls. Record no-EV-majority explicitly (including 269–269), without inventing a contingent-election winner.

### 4. Storage, replay and public contracts

Keep the generic `model_uncertainty` storage; use a versioned US stored payload with model/metric discriminators rather than overloading Westminster `hung`. A small migration adds poll evidence-kind/population provenance as needed; versioned JSON carries run context and outcome data without duplicating model-specific uncertainty tables.

The persisted replay snapshot includes resolved deterministic inputs, selected poll IDs and effective sampling evidence, aggregate provenance, alpha/loadings/parent graph, party/race order, baseline/special IDs, electorate weights, EV allocation, Senate roster/caucus/VP policy, config, seed and algorithm versions. Replaying must not consult current poll/matchup/manifest/config values to reconstruct the calculation. Validate hashes, supported schema, array shapes and resource bounds; load numeric arrays without pickle. Store enough full-precision input to reproduce saved outcomes; display rounding remains separate.

Publish a versioned US uncertainty document with explicit `model`, `context`, `metrics` and named `outcomes` contracts, decoded to common chart/view structures. Keep raw party and caucus series in separate domains. Proposed filenames are `us-house-uncertainty.json`, `us-senate-uncertainty.json`, `us-president-uncertainty.json`. The public whitelist excludes raw evidence, internal budgets and large simulation arrays.

Do not reuse legacy `s` as full-Senate seats or presidential EVs. Presidential trend `e` remains EVs; add explicit EV bounds. Senate adds full-chamber central counts/bounds alongside retained contested `s`. Introduce a versioned US trend envelope or explicitly versioned additions with per-point context and named outcome series; settle exact keys and golden fixtures in piece 1, then use that single contract everywhere. No renderer may guess metric type from a number or parliament name alone. Ordinary Westminster trend arrays remain compatible.

Pair current results and summary using the existing public identity hash, extended to include model/aggregation context. Derived Senate full-chamber views retain a valid identity only when their saved roster/context matches. Missing, stale, unsupported or mixed-cache documents hide optional ranges/chances and keep central results usable. Export each model independently and remove its stale sidecar when a newer central-only result is exported.

Record context boundaries in history. Cycle, selected matchup or chamber-composition/control-policy changes must stop carry-forward, compression, band smoothing and chart lines across the boundary even when totals happen to match. Mark legacy unknown contexts honestly. Config revisions retain their provenance; apply an explicit segment boundary when interpretation/comparability changes. History produced by rerunning today's assumptions is reconstructed history, not an archive of what was forecast then.

### 5. Public and console presentation

Use explicit display descriptors for seats, full-chamber seats, EVs, popular votes, control and no-majority outcomes. Do not relabel US events as hung parliament. President defaults to EVs on/popular vote off; House to seats on/votes off; Senate to full seats with no fictitious full-chamber popular-vote control. Senate Chances uses control by caucus plus tied/unresolved outcomes; House uses majority, President electoral majority/no-majority. Additional largest-party views are optional only where their meaning is clear and the stored contract supports them; they are not substitutes for control.

Reuse headline, totals and popup components and existing formatting (maximum 2dp). Regional-only ranges are shown only for an explicitly stored matching aggregate. Party, majority, gain and arbitrary field filters show filtered central totals without borrowing national bands. The Senate Seats up / Full Senate switch selects distinct stored aggregates. Race probabilities attach to the contested race, never to a holdover sharing its state. Historical actual elections and user-created hypothetical swing predictions remain central-only unless a real matching uncertainty run exists.

Console parity means model-specific hub, calibration/provenance, error budget, results, odds history, simulation list/detail, race-across-simulations and race-in-one-simulation decomposition, with navigation back to the full set. Show aggregate sample size as not applicable, not zero; explain blend weights and inherited/shared errors. Use frozen EV and Senate counting context in detail views. A holdover has fixed status, not a falsely simulated win probability. Historical summaries with pruned draws show the retention limitation clearly.

Console transparency is a standing requirement (Westminster Settled 19/22): each stage's intermediate numbers appear in the console in the same piece that builds the stage, or the next one. They are not batched at the end. The pieces below place each view with its stage. Before each piece the builder presents that piece's brief from `briefs.md` (in this directory), refreshed with real numbers, and waits for the user's go-ahead.

### 6. Westminster lessons carried over (added by review, 2026-10-22)

These were learned the hard way in `add-monte-carlo-uncertainty-to-westminster-projection`. Each is a requirement here, not an option.

- **Zero-sum sampling covariance.** Drawing independent sampling errors and then centring them halved the sampling variance: 2.5 → 1.25 pt² in the 50/50, n_eff 1000 case (review 2026-10-14). Westminster fixed this with `monte_carlo._fit_multinomial` / `regional_sampling_multinomial`, drawing `√c(√t⊙ε − t·Σ√tⱼεⱼ)`. The US compositional errors must reuse that construction, or pass the same sampling-only test: post-transform empirical variance equals the analytic value, for unequal shares and for fallback cells.
- **Calibrate in engine units.** Westminster divides each realised residual by its own cell's `centring_sd_ratio` before fitting, because the raw σ fed to the engine is about 10–20% larger than the realised σ. US calibration must invert whatever its kernel applies (centring, clamp, renormalisation), and must verify this by drawing through the real kernel and recovering the realised spread.
- **One shared national draw.** National (G) error is drawn once per simulation and applied everywhere. It is never drawn per region, even when every region is a fallback.
- **Latest-run rule.** Export, raw-file writes and trend-meta writes all use `persistence.latest_run` (as-of date, then id), never the highest id. An older re-run must not become the exported run or overwrite the raw file.
- **Seed and raw path per model.** The seed derives from (model, as-of date) and is recorded; the three models must not share streams. Raw draws go to per-model files under `SIMULATIONS_PATH`'s directory (e.g. `us-house-latest.npz`), with the same protected-target validation and atomic write.
- **Schema dispatch before first write.** Every reader of `model_uncertainty` (persistence `_check_schema`, `trends.reconstruct_trends`, `public_uncertainty`, export, console) dispatches on the schema. A US block must never reach Westminster validators.
- **Aggregates never enter `WestminsterPollAggregation`.** It raises on missing n.
- **No-poll runs.** Westminster leaves no-poll runs out of the trend file (e2a47fbe). For the US, "no poll" must be defined per model, because generic-ballot snapshots are roughly fortnightly and many runs have seat polls but no national snapshot. Budgets show which evidence was present.
- **Central bugs exposed by parity.** Fix them as separate, explicitly approved commits (as with `309bad61`), never silently inside an uncertainty piece.
- **Uncontested baselines.** σ scales with s(p), so a single-party baseline gets no error and can never flip; 15 House districts have one in 2024. Label these in the console ("fixed: uncontested baseline") rather than presenting 100% as a modelled probability.
- **Runtime.** Westminster's full save took about 15 s per date, dominated by trend reconstruction, not the Monte Carlo. Batch trend rebuilds during backfill, and budget against the console's 3,600 s rebuild timeout.

## Pieces

Restructured by the 2026-10-22 review.
- **Pieces 1–4** are shared foundations.
- **Pieces 5–8** build the House end to end (closest to Westminster: unit seats, majority 218, no holdovers).
- **Pieces 9–10** add the Senate.
- **Pieces 11–12** add the President. It goes last: with one state poll and a hypothetical 2028 matchup, its odds are mostly national G plus fallback residuals.
- **Pieces 13–14** validate against the 2026 result and run operations.

Each slice reuses the machinery the earlier slice built rather than adding parallel code. Every piece presents its `briefs.md` brief first and ships its own console view (§5).

### 1. Contracts, readiness audit and calibration sources (shared; second planning round)

- **Files:**
  - proposed `data/model_support/us_uncertainty.py` (typed contracts) and `data/model_support/us_outcomes.py` (outcome and context types);
  - fixtures in `data/tests/`;
  - documentation under `data/models/us/` for the evidence and calibration contracts;
  - integration readers: `_common.py`, `persistence.py`, `public_uncertainty.py`, `trends.py`.
- **What:**
  - Settle exact versioned stored, public and trend contracts, plus the schema-dispatch design for every `model_uncertainty` reader.
  - Record the trend compression rule (resolved decision 2) in the trend contract and its fixtures.
  - Audit, without live writes: sample and provenance coverage, candidate mappings, Senate roster/specials/VP config availability, and EV coverage.
  - **Fix the calibration sources.** Proposed: a committed `data/models/us/us_final_poll_errors.json` plus a builder script, in the PollBase pattern. Per cycle it records:
    - the final generic-ballot aggregate **at the raw, undecided-inclusive levels the deployed estimator uses** against the House popular vote;
    - final state presidential and Senate averages against results;
    - a source URL for each.

    Source policy: one curated source per series (resolved decision 1). Piece 1 names the specific sources for sign-off.
  - List which components will be fitted from DB results and which are labelled assumptions:
    - President state residuals from 1964–2024;
    - House district residuals from one noisy 2022→2024 transition;
    - Senate state/race residuals, which have no same-seat transitions.
  - Identify supported backfill intervals per model and cycle.
- **Functions:** typed `UsRunContext`, `IndividualPollEvidence`, `AggregateEstimateEvidence`, `UsOutcomeSpec` and schema validators. Names can follow local conventions, but must preserve the boundaries above.
- **Depends on:** none.
- **Acceptance:**
  - contract fixtures distinguish all three metrics;
  - legacy Westminster remains readable;
  - the audit lists actionable gaps;
  - the source inventory states exactly what calibration is possible;
  - **separate user sign-off on contracts and sources** before piece 2.
  - No live repair or invented historical context.

### 2. Persist US evidence provenance and validate actual contributors (shared)

- **Files:**
  - `data/models.py`, `data/db.py`;
  - `data/polls/importers/us/us_wikipedia_polls.py` and `us_polls_common.py`;
  - import CLI/queue services as necessary;
  - `_common.py`;
  - proposed migration, re-fetch backfill script and US evidence tests;
  - console US poll views.
- **What:**
  - Persist population, evidence kind and source provenance, and carry them through the selected `PollReading` and seat contributors.
  - The importer currently keeps the first of a pollster's LV/RV variants and drops the rest (`us_polls_common.py:1166-1216`). Decide whether variants are stored or which one is selected, and re-fetch existing polls to recover the label.
  - Write the idempotent migration and a dry-run-first backfill using the Westminster `backfill_poll_bases` pattern, **including its revalidate-under-`BEGIN IMMEDIATE` guard**: poll head plus fingerprint; skip a poll that was deleted, changed or reused under the same id.
  - Preserve existing import review and deduplication behaviour.
  - Never globally relabel every null-N poll as an aggregate.
  - Console: evidence kind, population, n source and MoE in the US poll views. Aggregate n shows as "not applicable".
- **Functions:**
  - importer write path, `Database.add_poll` / upsert;
  - `collect_poll_readings`, `usable_seat_readings`, `aggregate_national`, `aggregate_seat_polls`;
  - evidence resolver and validation helpers.
- **Depends on:** 1.
- **Acceptance:**
  - LV/RV/A round-trip, including re-fetched legacy polls;
  - generic aggregates have no synthetic N;
  - selected contributors match central weights exactly;
  - missing N on a used individual poll fails with a repair message;
  - ignored or malformed unmatched polls do not block;
  - central results unchanged;
  - the backfill skips stale polls (delete, id reuse, metadata or share change);
  - temporary-database migration and backfill tests pass;
  - the console shows the new fields.

### Additional prerequisite: audit and prepare House baseline corrections

Approved explicitly by the user on 2026-10-22 after the calibration-source audit found incomplete NY-20 totals and invalid ballots classified as Others. Complete this as its own reviewed local commit before calibrating House errors.

- Source: official House Clerk 2024 district/candidate statistics, with the exact candidate/fusion/RCV/valid-vote counting rules recorded. The national recapitulation is not directly comparable and must not be used as a denominator without reconstruction.
- Audit all 435 2024 districts against the saved baseline; identify every changed party/candidate total and winner. Prioritize 2024; record any 2022 comparability gaps separately.
- Prepare reproducible source input, correction tooling and a discrepancy report. Ambiguous fusion affiliation, ranked-choice rounds, blanks/void and uncontested nominal totals must be resolved explicitly, not guessed. Preserve the original snapshot/provenance.
- Test correction planning, counts and stale-input protection on scratch databases. No live database changes before the user reviews the report and a backup is made.
- Keep this correction separate from the already approved phantom-vote code fix; both must precede the zero-error parity reference used for the House kernel.

### 3. US calibration data, configurations, report and console view (shared; go/no-go gate)

- **Files:**
  - proposed `data/model_support/us_calibration.py` and `data/scripts/calibrate_us_uncertainty.py`;
  - the piece-1 source table and its builder;
  - per-model configuration and report files under `data/models/us/`;
  - shared calibration utilities only where model-neutral;
  - a console calibration view at `/us/<model>/uncertainty/calibration`.
- **What:**
  - Reproducible calibration and reporting for the aggregate estimator **as deployed**:
    - House: the raw generic ballot minus vote-share baselines;
    - Senate: the House generic-ballot level minus its own 2020/2022 field baseline;
    - also individual, state and race residuals.
  - Calibrate in engine units (§6).
  - Resolve dependence, denominators, correlation, residual identifiability, and floor/tail assumptions.
  - Hold out whole elections where data permits.
- **Functions:** calibration input validation, grouped fit and holdout evaluation, configuration generation, report serialisation, the console presenter.
- **Depends on:** 1–2.
- **Staging:** shared calibration machinery and the House candidate report land first, preserving the approved House-first vertical slice. Senate/presidential inventories retain explicit pending status until their model-specific calibration is completed; each model requires accepted parameters before its stored/public odds are enabled. The overall checkpoint remains open until all required model calibrations are accepted.
- **Acceptance:**
  - every coefficient has provenance and units;
  - the report reproduces its inputs and separates fitted values from assumptions;
  - aggregate overlap is not treated as extra N;
  - leakage-free election-level evaluation and sensitivity results exist where data permits;
  - the console shows each calibrated σ with source and method (Westminster calibration-view parity).
  - **Gate (updated by user on 2026-10-22):** build/test kernel mechanics on synthetic/scratch inputs first if needed; the user accepts calibrated values and labelled assumptions before enabling stored or public odds. If they are not accepted, stop and re-scope rather than publishing placeholders.

### 4. Pin the Senate field, specials, baselines and roster per cycle (central prerequisite)

- **Files:**
  - `data/models/us/run_us_senate_model.py`;
  - a proposed versioned per-cycle Senate config under `data/models/us/` (field class, specials with baseline election, roster snapshot, independent alignment, VP bloc, effective dates and provenance);
  - `scripts/export/manifest.py` readers as needed;
  - tests.
- **What:**
  - Replace the import-time, wall-clock and mutable-file resolution (`date.today()`, live `senate-current.json`, hard-coded `"2020 US Senate Election"`) with resolution keyed by as-of date and target cycle.
  - The frontend's `senate-current.json` and the shell keep working.
  - The model and export must not disagree.
- **Functions:** `resolve_senate_cycle(as_of)` (proposed), the field allowlist, special-baseline overrides.
- **Effective-date decision (user, 2026-10-23):** switch the target from 2026 to 2028 on 1 January 2027. Every 2026-dated run retains the 2026 field, regardless of when it is rerun. The 2028 Class-3 field/baseline may resolve, but full-chamber odds require a verified post-2026 roster; do not invent future holdovers.
- **Depends on:** 1.
- **Acceptance:**
  - 2026 dates project exactly today's field and central results (byte-identical stored votes on a scratch copy);
  - a simulated "today = 2027" or an edited roster file does not change a 2026 as-of run;
  - the 2028 Class-3 field resolves from config;
  - the console or run log shows the resolved field and its config version.

### 5. US simulation kernel with House inputs, plus parity/budget console view (House slice)

- **Files:**
  - proposed `data/model_support/us_simulation.py`;
  - `us_uncertainty.py`;
  - `_common.py`, which exposes immutable central inputs;
  - shared `monte_carlo.py` utilities (streams, chunking, centring, multinomial sampling) only where model-neutral;
  - a console error-budget / zero-error-parity view.
- **What:**
  - Build a pre-clamp central projection with sparse, source-indexed error loadings from the existing blend graph, with per-seat swing input (the Westminster `SimulationInputs` is region-only).
  - National G is drawn once per simulation. Sampling construction and calibration units follow §6.
  - Preserve party masks, poll selection, the "Others" branch, current integer rounding and the first-max tie-break over `party_universe` order.
  - Seed from (model, date).
- **Functions:**
  - `build_us_simulation_inputs`, error-scale and loading construction;
  - the US draw/projection kernel and chunk driver (proposed names);
  - an adapter around `blend_seat_swings` / `project_seat_votes`.
- **Depends on:** 2 and the separately committed phantom-vote fix for zero-error parity. User-approved synthetic/scratch kernel tests may precede final piece-3 calibration acceptance; stored/public odds remain gated.
- **Acceptance:**
  - zero-error equality with the House central projection, including alpha 0, 1 and intermediate, unpolled seats, independents, and single-party baselines;
  - the sampling-only variance test (§6);
  - seeded and chunk-invariant reproduction;
  - source-sharing and covariance tests;
  - unsupported evidence fails before persistence;
  - Westminster results unchanged;
  - the console shows per-seat and national budget and parity.

### 6. House tallies, runner wiring, atomic persistence, replay and schema dispatch (House slice)

- **Files:**
  - `us_outcomes.py` (House: unit seats, 435, majority 218, projected-vote national share);
  - `_common.py`, `run_us_house_model.py`;
  - `model_support/persistence.py`, `trends.py`, `public_uncertainty.py` (schema dispatch);
  - `simulation_replay.py`, `monte_carlo.py` file helpers;
  - `console/services/us_models.py`;
  - console hub at `/us/house/uncertainty`;
  - storage and replay integration tests.
- **What:**
  - Add `--simulations`, `--no-uncertainty` and `--no-raw-sims` parity.
  - Add model-specific config and complete immutable snapshots.
  - **Schema dispatch lands here, before the first US write**, so a stored US block is skipped by the Westminster validators and does not break US trend publishing.
  - Per-model raw file, gated to `latest_run`.
  - Integrate ordinary, retrospective and `--rebuild-history` runs.
- **Functions:** `UsSimulationConfig`, CLI parsing, `persist_projection(uncertainty=)`, `replace_output`, save/load/replay dispatch by schema and model, the hub status presenter.
- **Depends on:** 5. Runtime production enablement also requires accepted model calibration and resolved target-cycle House geography/baseline. Software may be completed behind an explicit release gate.
- **Acceptance:**
  - deterministic and uncertainty outputs replace together;
  - a failure preserves the previous complete date;
  - a dry run writes nothing;
  - raw staging failures cannot mismatch;
  - retention is isolated per model;
  - an older re-run leaves the raw file and trend meta unchanged;
  - **US trend output stays byte-identical** with a US block stored, until piece 8 enables the US contract;
  - changing the current matchup, config or roster after saving does not change replay;
  - console serialisation is intact;
  - the hub shows run and detail status.

### 7. House console results, odds history and simulation explorer (House slice)

- **Files:**
  - `console/blueprints/uncertainty.py`, `uncertainty_runs.py`, `uncertainty_explorer.py`;
  - their services and templates, plus navigation;
  - a proposed shared model-descriptor / route registry.
- **What:**
  - Keep the Westminster routes.
  - Add allowlisted `/us/<model>/uncertainty/...` routes, driven by descriptors, with saved scope and context validation on every election id.
  - Pages: results, odds history, simulation list/detail, race distribution, and per-simulation race decomposition (baseline, central, inherited fallback, local blend, each error component, clamp/normalisation, winner).
  - No λ, NI or hung text on US pages.
- **Functions:** model resolution, run lookup, results/history aggregation, draw loader and replay dispatch, pagination and index validation.
- **Depends on:** 6.
- **Acceptance:**
  - cross-model ids are rejected cleanly;
  - selected draws verify against saved shares, winners and totals;
  - navigation returns from a race to its simulation and to the full set;
  - latest-only retention explains absent files;
  - wrong scope, hash or version and malformed arrays fail safely;
  - display is capped at 2 dp;
  - pages render without loading every simulation row.

### 8. House export, trends, map and overall tracker (House slice)

- **Files:**
  - `public_uncertainty.py`, `trends.py`;
  - `data/scripts/export/uncertainty.py`, `export_elections.py`;
  - `uselectionmaps/uselectionmaps.js`, `index.html`, `data/map-modes-shell.json`;
  - `electionmapslogic/uncertainty*.js`, `features/polltracker*.js`, the fragment, `state.js`;
  - JS tests, including a new `polltracker-view.test.js`.
- **What:**
  - Export the versioned `us-house-uncertainty.json`, with `uncertaintyId` stamped into `us-house-forecast.json`.
  - US export selection switches to `latest_run`.
  - Add the US trend envelope, with context segmentation and stale-sidecar removal.
  - Frontend: a descriptor (tally noun, outcome label, no-majority label) replaces the hard-coded majority/hung labels, and the "chances only when `hung`" gate.
  - Register the tracker fragment and module for the US page.
  - Add `polltrackerDataPath`, `uncertaintyDataPath` and default parties `[20, 21]` to the House config only.
- **Functions:** the public US serialiser, model-scoped latest selection, `reconstruct_trends`, the uncertainty decoder, the tracker parser and descriptors, page registration.
- **Depends on:** 6–7.
- **Acceptance:**
  - golden payloads have correct metrics and identity;
  - no private evidence or draws leak;
  - legacy Westminster stays byte- and semantically compatible;
  - an older re-run of a date does not become the exported run;
  - the House tracker plots seats, with Chances = majority / no-majority;
  - the five-update smoothing, the raw tooltip bounds and the default toggles match the shared UI;
  - Westminster and one other non-US parliament still work in jsdom;
  - a manual preview check is done.

### 9. Senate: full-chamber/control tallies, runner, console and export (Senate slice)

- **Files:**
  - `us_outcomes.py` (Senate);
  - the Senate runner on the piece-4 config;
  - the existing persistence, console and export paths, extended through descriptors;
  - tests.
- **What:**
  - Fixed holdovers plus contested substitutions, from the pinned roster.
  - Raw party seats are kept separate from caucus/control outcomes.
  - Explicit independent alignment, with real fixtures: Nebraska 2026 Osborn (I, not caucusing); King and Sanders (I, caucus D, Class 1 holdovers).
  - The configured VP resolves a 50–50 tie. The 50–50 probability is reported separately, and unaligned outcomes stay unresolved.
  - Contested-vote share is labelled as such.
  - Two stored aggregates: Seats up and Full Senate.
  - The Senate's generic-ballot exposure is labelled an assumption: there is no state generic ballot.
  - Console: holdover/control arithmetic in results and the explorer.
- **Depends on:** 4, 8.
- **Acceptance:**
  - every draw sums to 100;
  - the 51 / 50+VP thresholds are correct;
  - party and caucus totals differ correctly;
  - an unknown alignment or VP gives unresolved;
  - regular and special substitutions replace the correct member;
  - holdovers show fixed status, not a win probability;
  - `us-senate-uncertainty.json` golden payloads pass.

### 10. Senate map and tracker (Senate slice)

- **Files:**
  - `features/senate-forecast-controller.js`, `features/senate-chamber.js`;
  - `uncertainty-controller.js`, `app.js` activation;
  - the Senate shell config;
  - JS tests.
- **What:**
  - The Seats up and Full Senate tabs bind their own stored aggregates.
  - `ElectionData.fromSeats` no longer loses a valid full-chamber identity.
  - Fix the activation race: the tab switch currently loses a legitimate attachment (`app.js:185-188`).
  - The tracker shows full-100 seats, with Chances = control by caucus plus tied and unresolved.
- **Depends on:** 9.
- **Acceptance:**
  - stale results never attach;
  - **a legitimate attachment survives a tab switch mid-fetch**;
  - the full Senate has seat and control ranges and no full-chamber vote share;
  - race probabilities never attach to a holdover;
  - the tracker never plots contested `s` as chamber seats;
  - Westminster and House still work.

### 11. President: EV/popular tallies, runner, console and export (President slice)

- **Files:**
  - `us_outcomes.py` (President);
  - `data/electoral_votes.py` allocation helpers;
  - the presidential runner, console and export through descriptors;
  - tests.
- **What:**
  - EVs weighted from the database allocation for the target cycle, with the threshold derived from it.
  - Popular vote excludes Maine/Nebraska district duplicates.
  - Correlated state and district outcomes. Parent inheritance applies only on the presidential map, and only when the parent was polled.
  - No-EV-majority, including 269–269, is recorded explicitly.
  - Cycle and matchup labels are visible.
- **Depends on:** 8.
- **Acceptance:**
  - every draw sums to 538;
  - the 270 threshold is correct;
  - 269–269 and the split EV/popular winner cases pass;
  - no double-counted popular vote;
  - `us-president-uncertainty.json` golden payloads pass.

### 12. President map and tracker (President slice)

- **Files:** the presidential page config, tracker and headline descriptors, JS tests.
- **What:**
  - The tracker plots EVs (`e` and its bounds), never units won (`s`).
  - Chances = electoral majority / no majority.
  - Popular vote is off by default.
- **Depends on:** 11.
- **Acceptance:**
  - correct units and labels;
  - historic central-only data renders with the uncertainty controls hidden;
  - all four tracker pages work.

### 13. Validate against the 2026 result (House and Senate)

- **Files:**
  - proposed `data/scripts/validate_us_uncertainty.py`;
  - a validation report under `data/models/us/`;
  - a console validation view.
- **What:**
  - Once 2026 official results are imported (a separate, approved import), score the stored final nowcasts and the nowcast history against them:
    - district and state interval coverage;
    - Brier score and reliability for race probabilities;
    - chamber seat-total coverage;
    - generic-ballot G realised against assumed.
  - Use pre-registered metrics, fixed before results are known where possible.
  - Feed the findings into 2028 calibration as a recorded config revision, not a silent refit.
- **Depends on:** 9–10, 2026 results.
- **Acceptance:** the report is reproducible, limitations are stated (one election), and any recalibration is a separate approved decision.

### 14. Scratch pilots and operational backfill

- **Files:** an operational runbook in the repository docs; issue checkpoint and report notes; generated exports only after specific approval.
- **What:**
  - Read-only readiness audit, then migration dry runs, a fresh backup, and a scratch pilot per model.
  - Measure runtime and memory against the console rebuild timeout. Batch trend rebuilds.
  - Request each live-database step and each publication separately.
- **Depends on:** 2–12 and accepted calibration.
- **Acceptance:**
  - a reviewed sample of safe, marginal, unpolled, independent, special, uncontested and district cases;
  - no unexplained aggregation or covariance artefacts;
  - same-cycle historical contexts are reproducible;
  - live backfill and publication happen only when separately authorised.

## Checkpoints

- [x] Review edits (2026-10-22) accepted; decisions 1–4 resolved by the user.
- [x] Phantom absent-party votes fixed in `c0d62535` (separate approved central commit, before piece 5).
- [x] Piece 1: contracts, schema-dispatch design, audit and calibration sources signed off. Committed `6c4de57d` (2026-10-22).
- [x] Piece 2: evidence provenance implementation, guarded re-fetch tool and console complete; committed `e2a8e6fa`. Live schema migrated; evidence re-fetch/application remains an operational step.
- [x] House baseline audit/corrections committed `9d858a83`; exact reviewed live application separately approved and completed after backup.
- [ ] Piece 3: calibration accepted (go/no-go gate); console calibration view.
- [x] Piece 4: Senate field/roster pinned per cycle; 2026 central results unchanged. Committed `0dcd8bb3` (2026-10-23).
- [x] Piece 5: House kernel zero-error parity and sampling-variance test; budget view. Committed `15611eed` (2026-10-23); scratch-only until calibration acceptance.
- [x] Piece 6: House persistence/replay; schema dispatch; US trends byte-identical; hub. Committed `fecbbd0c` (2026-10-23); production release remains gated.
- [x] Piece 7: House console results/history/explorer. Committed `6ff412a8` (2026-10-23).
- [x] Piece 8: House export, map ranges and tracker software committed `a48ecdf6` (2026-10-24); synthetic preview inspected and colour feedback applied. Corrected preview visual confirmation remains pending; production release stays gated.
- [x] Piece 9: Senate 100-seat/control simulation, console and export software committed `f70496e6` (2026-10-24); production calibration remains gated.
- [x] Piece 10: Senate tabs, activation race and tracker committed `dcd2b883` (2026-10-24), including approved isolated central-roster history.
- [x] Piece 11: President EV/popular simulation, console and export committed `d71845aa` (2026-10-24); production calibration remains gated.
- [x] Piece 12: President map and electoral-vote tracker committed `795d46b9` (2026-10-24); production calibration remains gated.
- [x] Piece 13 software preparation: preregistration, frozen snapshots, strict offline scorer, CLI and console readiness. Committed `c7c994af` (2026-10-25).
- [ ] Piece 13: actual certified2026 result validation report, reviewed evaluation basis and compatible frozen inputs.
- [x] Piece 14 runbook preparation: safe isolation, readiness, parity/resource/sample review, history/retention/export and live handover. Committed `03bbf46d` (2026-10-25).
- [ ] Piece 14: actual accepted-calibration scratch pilots reviewed; separately authorised backfills done.
- [ ] Publication completed only with explicit authorisation, or handed over as pending.

## Verification

For Python changes, use `cd data && ./run_tests.sh`: strict mypy gates pytest. Focus runs while building, then the relevant full suite for a completed Python piece. Existing groups include `test_us_model.py`, `test_us_electoral_votes.py`, `test_model_trends.py`, `test_monte_carlo.py`, `test_model_uncertainty_persistence.py`, `test_public_uncertainty.py`, export identity/partition tests and console uncertainty tests. Add meaningful US-specific integration fixtures for the new math/contracts and migrations; don't mirror implementation details in tests.

For frontend changes, use `npm test` with focused Vitest files during implementation, then the applicable suite. Extend `polltracker*.test.js`, `uncertainty*.test.js`, `senate-chamber.test.js` and `senate-forecast-controller.test.js`, and add `polltracker-view.test.js` (none exists) for descriptor labels. After changes under `electionmapslogic/`, run `npm run minify:electionmaps` to rebuild both page bundles and shared assets. Never modify generated bundles or manifests as the source of truth.

DB tests use fresh temporary databases. The full pipeline has historically dirtied tracked trend files; inspect status and use an isolated checkout/copy with no `.env` for broad checks when necessary. `.env` overrides shell `DATABASE_PATH` in runners: select/verify a scratch database using supported configuration, and do not assume an environment assignment bypasses it. Never run the live model as a test. Full scratch site exports use `--output-base`; `--output-root` is restricted to current-simulation export.

High-value cases:

1. Evidence: unknown legacy type, aggregate without N, individual missing N, RV versus LV, zero-weight and unselected matchups, duplicate/repeated source snapshots, no local evidence and missing national evidence.
2. Math: zero-error parity; alpha 0/1/intermediate; parent-state inherited covariance; shared national factor across districts; clamp/renormalization/rounding/tie behavior; third-party/independent and absent-party handling; seed/chunk reproducibility. Use fixed-seed sampling tolerances justified by draw count, not brittle exact empirical variance assertions.
3. Aggregation: correct fixed Senate seats and special class, caucus versus party totals, unknown/unaligned classification, configured VP tie resolution, no-EV-majority, presidential popular/EV winner disagreement, target allocation changes, no double-counted district vote totals.
4. Storage: one model cannot prune another; failed replacement keeps prior data; raw snapshot integrity/retention; replay after source/config/roster mutation; dry-run makes no writes; supported old schemas remain readable and unknown versions fail clearly.
5. History/export: same totals with different context cannot compress together; missing explicit uncertainty breaks ranges; no-data calendar days carry only within a context; mixed old/new cached exports suppress ranges; maximum public precision/whitelist and export-size measurements.
6. UI: three US models plus Westminster and another parliament, actual-election and user-input views, metric defaults, switch/reset/stale-request behavior, national versus matching regional filters, Senate full/seats-up switches, mobile layout and keyboard radio/toggle controls.
7. Review additions (2026-10-22):
   - uncontested single-party baselines (15 House districts in 2024) and a same-party top-two (CA/WA);
   - national-snapshot-free runs that carry seat polls;
   - an older-date re-run after the latest date (the export, raw file and trend meta stay on the latest);
   - a stored US block leaving Westminster and US trend output unchanged before the US contract is enabled;
   - Senate field resolution with a faked later "today" and an edited roster;
   - LV/RV backfill skipping deleted, changed or reused-id polls;
   - a legitimate Senate attachment surviving a tab switch mid-fetch;
   - the sampling-only post-transform variance against the analytic value;
   - calibration recovering the realised spread through the real kernel.

Use a browser if available. Otherwise prebundle real modules with esbuild and drive jsdom@24 with the shared shell and tracker fragment injected before module import, stubbing observers/navigator as documented in AGENTS.md. A DOM smoke test does not prove chart geometry or visual quality; obtain a manual preview check for each model, full Senate and narrow layout.

Calibration acceptance is distinct from software tests. A passing suite verifies implementation, not reliable electoral probabilities. Do not force the deterministic point estimate inside every simulated interval or require history to be smooth; investigate discrepancies and jumps against their inputs and uncertainty transformations.

## Notes

### Methodology references consulted during architecture

These sources inform the approach and source audit; none supplies ready-to-use coefficients for this pipeline.

- AAPOR, [2020 pre-election polling report](https://aapor.org/wp-content/uploads/2022/11/AAPOR-Task-Force-on-2020-Pre-Election-Polling_Report-FNL.pdf): evidence for substantial shared polling error across national/state/race estimates.
- AAPOR, [2024 pre-election polling report](https://aapor.org/wp-content/uploads/2025/10/AAPOR-Task-Force-on-2024-Pre-Election-Polling_Report.pdf): additional cycle evidence for the calibration inventory; avoid fitting solely to one unusually good or bad election.
- Gelman and colleagues, [Grappling with uncertainty in forecasting the 2024 U.S. presidential election](https://sites.stat.columbia.edu/gelman/research/published/2024_Election_Forecasting_Review.pdf): correlated errors, model uncertainty and validation considerations. Its election-day forecast is not the requested nowcast specification.
- National Archives, [Electoral College FAQ](https://www.archives.gov/electoral-college/faq), and Library of Congress, [Electoral College introduction](https://guides.loc.gov/law-electoral-college): majority/no-majority and Maine/Nebraska allocation semantics; implemented allocations still come from the project's versioned database.
- US Senate, [About the Vice President](https://www.senate.gov/about/officers-staff/vice-president.htm): tie-breaking role. The model's governing-bloc/VP assumption is explicit configuration, not an assertion about every future parliamentary action.

### Planning record — 2026-10-22

The user explicitly chose nowcasts, full overall totals and full console parity, configured Senate caucus/VP control with a separate 50–50 probability, and retaining generic-ballot aggregates with calibrated aggregate error. The approved issue brief is recorded alongside this plan. No source changes, live data operations, commits, network publication or model runs were performed to create these artifacts.

Implementation should append resolved calibration choices, accepted source coverage, schema details and deviations here and tick checkpoints as work is verified. Main chat owns this issue's checkpoint files; parallel reasoning may be delegated, but implementation writers require disjoint ownership and approved batch scope.

### Review — 2026-10-22

The plan was reviewed against the completed Westminster issue, its 2026-10-14 review and the independent methodology note, plus read-only code and live-DB checks. The review is recorded at `/home/philiph/.claude/plans/given-work-done-in-twinkling-nygaard.md`. On the user's approval it led to these edits:

- the timing and deliverable section, and decisions 1–4 (the user answered them the same day: one source per series; the Westminster compression rule; fix phantom votes only; aim for pre-election delivery with the user judging readiness);
- verified seams;
- §5's per-stage console requirement and `briefs.md`;
- §6, "Westminster lessons carried over";
- pieces restructured into shared foundations (1–4), then House (5–8), Senate (9–10) and President (11–12) slices, with new pieces for Senate field pinning (4) and 2026-result validation (13);
- schema dispatch moved before the first US write (6);
- calibration made a go/no-go gate (3);
- LV/RV re-fetch with a revalidating backfill (2);
- extra verification cases.

No source, data or repository changes were made.

### Piece 1 implementation — 2026-10-22 (awaiting review, uncommitted)

Reconciled the refined 14-piece plan with clean `poll-errors-simulation` at `35e527a1`; no earlier issue implementation was present. The user approved the piece-1 brief. Added isolated US evidence/context/outcome contracts, schema identifiers and model-specific public/trend golden fixtures. Production readers/writers remain unmodified: the dispatch matrix explicitly precedes the first US persistence in piece 6. Senate outright-majority, VP-resolved control and tied-caucus events are distinct. Compression follows resolved decision 2, including context changes, first-summary retention and explicit loss-of-summary gaps.

Completed a fresh live read-only audit (SQLite URI mode=ro, query_only, one read transaction), with exact SQL inventory and roster hash committed as a reviewable proposed file. Confirmed 47 national aggregate snapshots, 265 House seat polls (11 missing N), 495 Senate seat polls (3 missing N), 17 national polls on the selected presidential matchup and one matching state poll; 35 Senate races and 65 holdovers; seven EV eras with 538 total. A window/matchup SQL screen identifies five House and three Senate missing-N records at 2026-10-19, but full contributor admission still decides whether each blocks uncertainty. No model, migration, repair or live export was run.

Source proposal: RealClearPolitics/RealClearPolling archive as the one curated polling-average source per series, FEC/verified DB results for outcome cross-checking. Seven final raw generic averages (2012–2024) and sample state/Senate archive pages were verified online. Complete race coverage and fitted coefficients remain piece-3 work. Historical archive residuals are a proxy for the deployed provider blend, not a validated reconstruction of it. The future curated table/builder follows source sign-off; this piece contains the inventory/proposal rather than claiming accepted calibration data.

The single-party baseline inventory must not automatically be labelled uncontested: same-party top-two candidates can collapse into one party. Record this diagnostic distinction when building the budget view. Central phantom-party fixes remain their separately approved commit before piece 5.

Verification: strict mypy passed across 243 source files; the isolated full Python suite passed 5644 tests. All 36 focused contract tests and focused strict mypy also pass after final formatting. No JS or deployed runtime imports changed. Automatic review skills were not invoked, honoring the project preference. Ruff is not installed in the existing venv; formatting was kept within the applicable guide without installing dependencies.

Piece-1 checkpoint remains unchecked until the user's contract/source sign-off and local commit approval. No piece 2 work has begun. Review artifacts: `data/models/us/uncertainty-contracts.md`, `uncertainty-readiness.md`, `uncertainty-readiness-2026-10-22.json`, and `uncertainty-calibration-sources.md`.

### User authorization and piece 1 commit — 2026-10-22

The user approved committing piece 1 and explicitly requested continuing through all remaining pieces, with permission to pause on questions. Treat this as sequential one-shot implementation/local commits, keeping briefs and checks per piece, without remote publication or removing the explicit calibration and live-operation gates. Piece 1 committed as `6c4de57d`. The user selected preserving the existing first-row LV/RV variant rule and recording its population for piece 2. Piece 2 is in progress; live migration/repair has not been authorized or performed.

### Additional baseline and calibration findings — 2026-10-22

The user authorized auditing and preparing House baseline corrections as a separate commit before calibration; live data application still waits for a reviewed correction report and backup. Confirmed example: stored NY-20 has Tonko 41,963 and Waltz 27,819; official Clerk candidate-line sums are 221,997 and 141,151. Some stored Others values include blank ballots. Full district/candidate source reconciliation is in progress, not yet an approved corrected dataset. RCP archive source remains chosen; FEC has no currently linked 2024 House/Senate compilation, so the official Clerk district statistics are proposed as the 2024 result counterpart, with scope/normalization audited.

The calibration/kernel dependency also needs explicit staging: piece 3 can report candidate values and assumptions, but engine-unit acceptance must use shared projection transformations and realised-spread tests rather than claim validation through a kernel not yet built. Do not bypass the user's calibration acceptance gate. Piece 13 scoring against November 2026 outcomes remains date-dependent and cannot be completed before those results exist.

### Piece 2 completed — 2026-10-22

Committed locally as `e2a8e6fa` under sequential continuation authorization. Preserved first-row variant selection, added nullable evidence kind/population/source, actual-contributor collection and future uncertainty-only validation, idempotent migration, guarded dry-run-first re-fetch repair, and console list/detail/queue displays. Aggregate N/MoE is not applicable; individual MoE is labelled a 90% sample-only approximation. Backfill dry-run ORM reads use a temporary backup rather than touching the source WAL configuration.

Verification: strict mypy (247 files), 636 focused tests, then isolated full suite 5664 passed. Two legacy scope-migration tests were updated to apply the subsequent evidence migration before current-ORM/parity checks; the old migration itself is unchanged. Scratch migration/repeat preserved poll count; scratch poll list/detail returned HTTP200. No JS changes. No automatic review skills.

The user separately authorized backing up and applying the schema-only live migration. Verified backup: `/home/philiph/dbs/elections/pre-us-poll-evidence-20261022T194403Z.db`; three nullable columns applied; all 1534 poll rows preserved and no evidence metadata populated. No live re-fetch/application or baseline correction has occurred.

Additional House baseline-correction prerequisite is now in progress, before piece 3. Full official-candidate extraction proposes 51 unit changes with no winning-party changes; exception/counting-convention review remains required before live application.

### Kernel/calibration staging decision — 2026-10-22

The user explicitly approved building/testing the US kernel on synthetic/scratch inputs before final calibration acceptance, resolving the stage-3/stage-5 dependency. The calibration gate now precedes enabling stored/public odds, rather than preventing kernel mechanics and realised-spread tests. No provisional values may be described as accepted/calibrated. Preserve the phantom-vote fix as its separate prerequisite before House zero-error parity. No live baseline application or operational model run is authorized by this staging change.

### House baseline correction completed — 2026-10-22

Committed curated official facts, the complete 51-district report, guarded dry-run/apply tool and 28 focused tests as `9d858a83`. Strict mypy250 files and isolated full Python suite5692 passed; scratch apply and repeatverified. User reviewed the report and explicitly approved freshbackup/liveapplication. Backup `/home/philiph/dbs/elections/pre-house-2024-corrections-20261022T200149Z.db` passed quick_check; the exact `/tmp/us-house-live-baseline-review.json` was applied atomically, and idempotent repeat made no changes. No winning parties changed. No model runs or static exports were executed. Original538 seed snapshot remains preserved; standalone correction tooling/documents make the repair reproducible.

The read-only polling metadata re-fetch proposed552 individual-survey repairs with12 failed source pages, no aggregate matches; nothing was applied. Historical generic snapshots need explicit provenance curation rather than null-N inference.

Next: separatelyapproved phantom-vote code fix. Its before-reference projection is a dry run against the corrected scratchDB at2026-10-19/60-day window (House D242/R193), recorded in `/tmp/us-house-before-phantom-fix.json`. The user already approved this change in resolveddecision3; preserve Others and allow own-seat-poll newentrants.

### Phantom-party prerequisite completed — 2026-10-23

Committed `c0d62535`. Broad fallback effects require positive local baseline support (Others retains its policy); genuine own-seat polling can introduce candidates. Scratch corrected House baseline comparison changes 56 party rows in 21 districts; projected winners remain Democratic 242 / Republican 193. Full isolated checks: strict mypy, 5700 tests passed. The user reaffirmed central parity: uncertainty-enabled and central-only runs with identical inputs/options must have identical central projections; simulation means/medians are distinct summaries. Piece 5 synthetic/scratch mechanics now proceeding under the staging authorization, with no stored/public odds before calibration acceptance.

### Contributing-sample research — 2026-10-23

Read-only primary-source research is at `/tmp/us-poll-sample-source-research.md` and `.json`. Five actual selected House contributors still have no verified matching primary-source N (polls 1894, 1677, 1117, 1149, 1676). Keep the approved stop-and-repair behavior; no guessed denominators or silent exclusion. Three Marist Senate packets establish full interview counts (OH1176/TX1196/IA1100), with LV weighting of RV respondents, published LV/RV MoEs and survey design effects; they do not publish a separate LV effective N. These are not an approved repair manifest. FL22 poll1149 also has a primary-source pollster-identity mismatch (Impact versus stored Tulchin), requiring a separate guarded identity correction. No live metadata/sample repairs applied. This evidence gap does not prevent synthetic mechanics or deterministic runs, but production uncertainty cannot bypass it.

### Senate effective-date decision — 2026-10-23

User chose calendar-year rollover: 2026→2028 on 1 January 2027. Preserve date-resolved fields across ordinary, retrospective and history rebuild/gap-fill config construction. Full-chamber uncertainty for 2028 must fail clearly until a verified post-2026 roster exists. Piece 4 read-only preparation found the shared runner/history seams also need date resolution; changing only module-level SPEC would be insufficient.

### Calibration source joins prepared — 2026-10-23

Read-only candidate evidence is at `/tmp/us-official-calibration-joins.md` and `.json`, with source workbooks/row joins under `/tmp/us-calibration/`. Seven RCP raw D/R final averages are joined to 435-slot House outcome views: official FEC candidate rows for 2012–2022, approved Clerk-based 2024 correction facts excluding 200 nominal unopposed votes. Fusion, delegates, duplicate unexpired terms, final RCV and unmeasured unopposed counts have explicit conventions. A 2020 NY14 FEC atomic-line typo is resolved by the official NYC signed certification (+30 Republican and denominator). Retained 2024 Alaska final counts now independently match indexed official PDF p3 text; direct PDF fetch still fails, so no downloaded-source hash is claimed. 2018 NC09 remains uncertified: exclude that cycle from a clean certified fit or explicitly approve a labelled sensitivity/proxy. These are candidate curation inputs, not accepted coefficients or complete multi-model calibration; reproducible fitting, engine-spread checks and the user acceptance gate remain.

### Piece 5 completed — 2026-10-23

Committed `15611eed`: frozen central/evidence adapter, US source-graph kernel, shared national/state factors, party-specific weighted multinomial sampling with explicit decided/design/population assumptions, parent-source reuse, deterministic model/date seeds and chunk invariance. Added read-only `/us/house/uncertainty/budget` linked from console home. Exact real scratch parity: 435 districts, all2610 recorded rows, Democratic242/Republican193; artifact `/tmp/us-kernel-real-parity.json`. Stochastic real input correctly blocks unknown provenance (poll1884), independently of central parity. No persistence/public flags or accepted coefficients enabled. Checks: strict mypy252 files,473 focused tests including27 new, isolated fullsuite5727 passed. Formatting-only follow-up preserved exact Python ASTs against the fully tested tree. No automatic review skills. Next unblocked prerequisite is piece4 Senate cycle pinning before completing calibration/persistence.

### Piece 4 completed — 2026-10-23

Committed `0dcd8bb3`: versioned strict Senate fields for 2026 and 2028; calendar rollover on 1 January 2027; pinned 2026 roster/control policy; complete checksummed context saved atomically with deterministic outputs. Optional model_context table is created inside a context-bearing replacement transaction; no live application/model run occurred. Ordinary/direct/history/rebuild/gap-fill paths resolve dates and preflight fields; unsupported periods and cross-cycle ordinary caps fail clearly. Export uses saved context, canonical map/model/prefix latest-run scope, a hidden pinned forecast roster, matching baseline/special metadata and no 2028 Full Senate without verified roster. Current actual-membership file remains separate. README/runbook updated. Exact scratch parity at 2026-06-29 and 2026-10-19: 35 races and210 recorded rows each, canonical payload SHA25635df0a3f7f4e94d1610d34fc2c5eed92805dc7815d73d6fce01c8c7ea98d43c8. Strict mypy255files;573 initial focused plus117 scope-fix tests; final isolated fullsuite5755 passed. No JS changes or automatic review skills.

Remaining calibration evidence artifacts now include `/tmp/us-house-residual-panel.md`/`.json` (315 stable-major-contested districts,43states, one2022→24transition), `/tmp/us-house-calibration-method.md` (inputG versus outputH/R units and recovery procedure), `/tmp/us-state-poll-calibration-research.md`/`.json` (153state/DC plus3national presidential source inventory;56 verified RCP averages and official FEC joins), and `/tmp/us-senate-poll-calibration-research.md`/`.json` (72distinct2022/24events;24 verified final RCP averages with official joins, other exclusions explicit; 2024 Clerk Senate exception awaits acceptance). No fitted production configurations accepted. AAPOR PDF inspected at `/tmp/us-aapor-2024-inspection.md`/`.json`; no quantitative coefficient adopted.

Next brief presented: piece3 reproducible calibration fits, explicit measured-versus-assumed report, actual-kernel unit/spread checks and console view, with acceptance before stored/public odds. Pending user question asks whether to propose labelled individual-House-local-error assumptions plus sensitivity (recommended under approved plan), or expand historical individual House-poll evidence first. Do not silently select a local floor or bypass missing-N/provenance blockers while awaiting that answer.

### Local-error proposal decision and piece 3 resumed — 2026-10-23

User chose clearly labelled individual-House-local-error assumptions plus sensitivity results, rather than expanding individual-poll archive work first. This authorizes proposing candidate assumptions, not accepting coefficients or enabling odds. Shared machinery plus House fit/report/console are proceeding first under the approved House-first sequence; other models remain explicitly pending. Proposed local-floor reference is linked to the fitted fallback race source scale, with zero-reference and half/double sensitivities. Design effect, effective-N cap and population-base adjustments will be explicit candidate assumptions with broad sensitivity, never presented as observed poll-specific facts. Gaussian tails remain an unvalidated limitation; no silent t-tail implementation. National aggregate error targets input D-minus-R contrast moments; district H/R proxies target output margins through the real kernel, with zero-origin versus centred moment choices visible. Required actual contributor validation remains unchanged.

### Historical generic evidence provenance recovered — 2026-10-23

Read-only source work produced `/tmp/us-generic-evidence-curation.md` and `.json`. All47 historical generic-ballot snapshots match provider label, updated-date and both raw party shares in immutable Wikipedia revisions explicitly identifying the aggregation table. Full poll-head/ordered-row fingerprints and pollster identity/weight snapshots are retained. Proposed changes are only evidence_kind and evidence_source; existing N, population, shares, dates, source_url and weights are preserved. Some revision timestamps follow the labelled update date: these prove kind/provenance, not original publication availability. No live records were changed. A later curated apply tool must verify pollster identity/weight as well as poll head/share fingerprints under an immediate transaction; review/apply authorization remains operational. Individual House missing-N contributors remain unresolved.


### House target-geography release gate — 2026-10-23

The existing House map/baseline uses 2024 (119th Congress) districts while 2026 district polls are attached by district code. Census identifies 2026 boundary changes in Alabama, California, Florida, Louisiana, North Carolina, Ohio, Tennessee, Texas and Utah; a matching district number does not establish comparable geography. Official reference: https://www.census.gov/programs-surveys/decennial-census/about/rdo/congressional-districts.120th_Congress.html . This is separate from the stable 2022–2024 district panel used for conditional calibration.

User explicitly chose to finish the current software and keep House production odds gated until the map work is resolved. This authorizes gated software completion, not new boundaries, rebased central estimates, live poll reassignment or production odds. Calibration acceptance and contributor-evidence validation remain independent requirements. Future House runtime configuration must fail closed for unresolved target geography; synthetic/scratch development and the unaccepted candidate calibration report may continue. A separate map/baseline prerequisite remains pending rather than silently changing this issue's central model.


### House candidate calibration implemented and verified — 2026-10-23

House candidate source facts, offline fitting/report generation and read-only console calibration view are implemented. Primary national input-margin RMS is 2.43808348 points (six clean cycles), with signed poll-minus-outcome bias +1.67110839 explicitly retained as a limitation rather than silently corrected. Joint conditional engine source scales are state 1.8653 and race 4.1824; independent kernel recovery matches W/B0 targets within stated Monte Carlo tolerances. The full 433 measured 2022 districts define source shapes; 315 eligible districts in 43 states supply one transition. All four conditional sensitivity fits recover successfully.

Candidate local scale equals race scale by an unfitted transfer assumption; DEFF 2, effective decided-N cap 2000, population fractions 1 and Gaussian tails are proposals, not accepted settings. Synthetic sensitivity probes never repair missing actual samples. NJ-08's -25.58-point residual is 6.51 fitted conditional output SDs; 92.06% training coverage and matched second moments do not establish reliable rare-event tails. Every candidate acceptance flag remains false; Senate/President calibration remains pending. House geography remains a separate production release gate.

Verification: strict mypy 258 source files and isolated full Python suite 5776 passed (136.22s), with no .env/live DB and no repo trend mutations. The implemented 21 new tests cover election holdout leakage, exact full-reference central parity/source shapes, stale/malformed provenance, joint covariance/recovery, synthetic local sensitivity, deterministic reporting, console and no-write CLI behavior. User's sequential local-commit authorization applies to this software; numerical calibration acceptance is still pending and piece-3 overall checkpoint remains open. No automatic review skills or live operations were run.


### House calibration candidate committed; gated piece 6 started — 2026-10-23

House candidate software committed as `e770a063`. Production numerical acceptance and the overall piece-3 checkpoint remain pending; no House production odds are enabled. User's latest instruction explicitly permits completing software while the map/baseline prerequisite remains unresolved. Piece6 now proceeds with synthetic/temporary-database acceptance fixtures and a fail-closed runtime release gate, retaining deterministic central-only behavior until approved release conditions are met. No bypass flag, invented evidence, live run or public output is authorized.

Piece6 brief: House seat/popular tallies, atomic central-plus-summary persistence, complete immutable snapshots and exact replay, model-isolated raw retention, CLI/history integration, schema dispatch before any US block is stored, and a console status hub. On/off central equality, rollback/dry-run, stale raw identity, historical rerun isolation and unchanged legacy trend bytes are required. Root remains the only issue-artifact writer; a scoped sole implementer owns source until handback.


### House target rollover decision — 2026-10-23

User explicitly selected two-year House target windows: 2025/2026 dates use 2026, then 2027/2028 dates use 2028 from 1 January 2027. Each target requires separately approved geography and calibration; a 2026 release must reject a 2027 run. The initial calendar-year-only guard was corrected before committing piece6, with boundary integration coverage and invalid accepted national-error configuration checks.

Verification command clarification: `run_tests.sh` always supplies `tests/` to pytest, so adding a positional test-file argument does not narrow collection. For focused verification use strict mypy followed by the venv interpreter's `-m pytest <file>`, or a `-k` expression with run_tests.sh. A workspace full run caused by the positional-path form was interrupted; git status confirmed no unrelated/trend changes. Final full suites remain isolated with .env excluded.


### Piece 6 completed; piece 7 started — 2026-10-23

Committed `fecbbd0c`: House tallies, exact frozen replay, staged per-model raw archives, explicit release configuration, atomic central/summary replacement, schema dispatch, compact race share ranges/histograms independent of raw retention, CLI/history integration and read-only status hub. Production remains disabled pending accepted calibration, target geography/baseline and actual contributor repairs. Ordinary commands retain central-only behavior; explicit simulations fail with blockers.

Independent content fingerprint and election ID bind raw archives; stale files are rejected even if SQLite reuses an ID. Per-date failed replacement preserves old output, older reruns preserve newer raw/meta, detail retention stays scoped and US trend bytes remain unchanged. Final strict mypy262 and isolated fullsuite5805 passed (146.79s), including29 new House integration cases. No live DB/model/export operation or automatic review skills were run.

Piece7 brief presented: read-only House results, odds history, simulation list/detail, race across simulations and per-simulation race decomposition, with frozen labels/context, scope/hash/version validation, complete return navigation, two-decimal presentation and explicit retention limitations. A compact/selected-row archive reader must avoid loading every votes/errors cube for a page. Freeze any additional deterministic decomposition at production time rather than reconstructing it from current database records. One scoped source implementer; root owns issue artifacts/checks/commits.


### Piece 7 completed; piece 8 brief — 2026-10-23

Committed `6ff412a8`: seven read-only House explorer views, explicit model/run/race scoping, frozen labels, two-decimal display, return navigation and retention messages. Selected ZIP-row reads avoid loading full cubes; replay regenerates the source RNG prefix with bounded memory. New central provenance captures and checks actual fallback/local blend contributions; older snapshots show missing diagnostics explicitly. Metadata sizes/layouts are checked before allocation, numeric queries are bounded, compact totals conserve the field, malformed compressed streams render safe errors. Strict mypy265; focused41 explorer tests; isolated finalfullsuite5846 passed (165.48s). No live operations or automatic review skills.

Piece8 scope is House-only under the approved vertical slice: Senate/President trend arrays and features remain legacy until their slices. Preserve internal legacy trend APIs or adapt callers explicitly. Add strict public whitelisting with identity matching, context-aware House trend envelope/gaps, current-model map/popups/totals, matching regional summaries and overall House tracker. Cache identity must distinguish same-date/same-central reruns with different uncertainty settings; no stale sidecar may attach. Current release and saved acceptance/geography must both authorize public odds; flags remain closed. Pure serializers/synthetic previews may be tested without publishing production odds.

Regional aggregates must come from correlated simulation totals with frozen membership, never by summing marginal interval bounds. Any additional group metadata/summaries may be frozen as additive snapshot data with legacy handling. Tracker defaults and fading bands follow the approved shared UI; current-map toggle remains default-off. Required checks include Python, JS, both rebuilt page bundles, actual modules in cached jsdom24 plus Westminster and another UK parliament. Browser inspection is unavailable on this host, so prepare a concrete preview before requesting the remaining human visual check. Cached harness: `/tmp/westminster-piece9-aiwnqz4b/harness`.


### Senate and President calibration prerequisites researched — 2026-10-23

Read-only reports prepared at `/tmp/us-senate-calibration-prerequisites.md`/`.json` and `/tmp/us-president-calibration-prerequisites.md`; no additional coefficients, assumptions or source exceptions accepted. Senate fitting requires correct 2016→2022 ClassIII and 2018→2024 ClassI fields/specials, conditioning generic effects through the actual Senate field-baseline/blend response. Final RCP averages do not recover historical individual weights/alpha; state/race/local error splits are not jointly identified. The 2024 Clerk Senate source exception is still pending.

Current2026 baseline D/R zero scan finds Arkansas Democratic only. Root verified central preparation on the corrected scratchDB and committed piece7 tree: June29 last60days has no Arkansas own-poll admission; October19 admits1913/1830 (weight1.5776573444826716, decidedR55.49845719390035/D44.50154280609966), resolving D eligibility in that prepared October run. This is not a stochastic validation or candidate-standing audit. Variance cannot repair an applied zero mask; historical missing-admission limitations need explicit treatment. No central model change is authorized by this finding.

President uses weighted individual national polls, whereas the generic House kernel currently only creates individual sampling sources for local polls. A total final-RCP national error proxy cannot be relabelled pure systematicG and combined with extra national SRS without a justified decomposition; policy acceptance is pending. President counting, covariance pooling and budget weights must exclude ME/NE child duplication for national/reference quantities while retaining56 correlated outcome units weighted to538EV. Historical ME/NE district transitions require geography checks. Repository2012/16/20/24 state-only presidential JSON totals differ from retained official FEC versions; this is not a liveDB audit or proof of bad-ballot/import bugs. Pin/disclose actual central baselines versus official targets before fitting; no automatic baseline correction/live operation is approved. Gated software may continue.


### Piece 8 software completed; preview feedback — 2026-10-24

Committed `a48ecdf6`: House-only public serializer/export, exact recorded-vote/context/artifact matching, current and saved release acceptance, stale-sidecar removal, opaque run-sensitive cache identity, correlated regional summaries with frozen exact membership, House trend envelope and context gaps, map/popups/totals, and overall House tracker. Senate/President trend arrays remain legacy. The internal list API is preserved; console/CLI consumers explicitly handle the House envelope. Generated manifest changes came from metadata export against a scratch database, not a live export. Both shared page bundles rebuilt.

Verification: strict mypy267; final isolated full Python suite5859 passed (179.29s); JavaScript589 passed with cached jsdom24 enabled; actual-module smoke passed House map435/tracker40, Westminster650 and Holyrood129. Eight old test assertions expecting a House array were adapted to the envelope and explicit unknown-context semantics; their failed-run/rebuild/consumer checks remain. Source matched the final isolated verification copy, and git diff checks passed. No automatic review skills, live data operations or remote writes.

A scratch-only synthetic preview runs at http://127.0.0.1:8008/uselectionmaps/ (artificial435 districts/test history, not forecast data). The user inspected it, questioned the invented figures and requested red/blue with no focus on Libertarian. The distinction was clarified; scratch colours now match the existing production Democratic blue #1375E1 / Republican red #E81B23; default tracker selects20/21 only, preserving third-party votes. A prominent synthetic warning was added only to the scratch preview. Corrected visual confirmation is still pending; do not claim the synthetic output is an accurate election projection. Backend Senate work can proceed under the existing sequential all-pieces authorization while this UI feedback remains open.

### Piece 9 brief — 2026-10-24

Next is the gated Senate backend: freeze the configured cycle/roster/control policy with each run; replace only the35 contested2026 slots and retain65 holdovers; preserve raw party totals separately from caucus/control; report full100 and seats-up aggregates, outright caucus majority,50–50, VP-resolved control and unresolved outcomes. King/Sanders/Osborn alignments remain explicit; unknown alignment or VP must never be guessed. Extend parity/evidence adapters, atomic persistence/replay, console explorer/error-budget arithmetic and public serialization/export through model dispatch. No full-chamber national vote share; contested votes must be labelled explicitly. Preserve exact central on/off parity and existing House/Westminster behavior.

Calibration is not accepted for Senate and the2028 roster remains unverified. Build/test with labelled synthetic/scratch fixtures; operational odds remain disabled and no House coefficients are silently transferred. No live metadata repair, central change, backfill or publication is included. Piece10 subsequently owns Senate map tabs, mid-fetch attachment retention and overall tracker.


### Senate source exception and President proxy proposal authorised — 2026-10-24

User accepted the official House Clerk2024 presidential/congressional compilation for Senate outcome calibration alongside prior-cycle FEC compilations. Verified primary source: https://clerk.house.gov/member_info/electionInfo/2024/statistics2024.pdf (published2025-03-10; includes named Senate results). The FEC results index checked2026-10-24 still lists2022 as its latest congressional compilation: https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/ . This is a source exception only, not a live import or acceptance of calibration values.

User authorised proposing the initial presidential national error as a labelled historical polling-average total-error proxy, with sensitivity checks and **no additional national sampling-error term**. This is an explicit estimator-transfer assumption, not a reconstruction of the deployed individual-poll blend or approval of numerical values. Actual contributor/evidence validation still applies; state/local sampling and error decomposition need separate justification so proxy error is not counted twice. Production remains gated until the report and labelled assumptions are accepted.


### Piece 9 completed; piece 10 brief — 2026-10-24

Committed `f70496e6`: Senate full/contested raw-party counting, separately frozen caucus/control outcomes, exact regular/special slot substitution,65holdovers/35contested2026, snapshot/replay dispatch, disabled Senate release/candidate config, console/error-budget/explorer and strict public export. Raw archived arrays remain contested35; full100 is derived from the saved roster, including holdover-only Independent IDs/labels. King/Sanders count by explicit candidate alignment; Maine's contested Independent cannot inherit King's holdover alignment. Nebraska Osborn stays raw Independent/unaligned; unknown VP is explicitly null and yields unresolved50–50, while51known caucus seats remain secure. Future2028 full chamber fails without a verified roster/policy. No Senate numerical coefficients were proposed or borrowed from House, and Senate trends remain legacy arrays for piece10.

Verification: strict mypy269; isolated full suite5874 passed (183.49s); final template label correction tested with56House/Senate console/explorer tests passed (27.69s). All Python source matched the full-suite snapshot; only three Senate-specific display labels changed afterward and were covered by the focused rerun. Git diff checks passed. No automatic review skills, live operations or remote writes.

Piece10 will activate Senate public decoding, own-metric seats-up/full100 attachment, preserved identity through derived chamber views, and robust mid-fetch tab switching. The full view has no full-chamber vote share; fixed holdovers receive no race win probabilities. Activate the Senate trend envelope/context segmentation and overall tracker for full100 raw-party seats. Chances show explicit caucus control plus tied and unresolved series: a VP-resolved50–50 event overlaps control and must not be presented as another exclusive partition. Keep legacy central-only data usable without invented odds; preserve House/Westminster, defaultSeatsON/VotesOFF, and existing band appearance. Build both bundles, generate manifest through scratch export, use real-module jsdom regressions for tab races/filters/stale identities and other parliaments. Production stays gated; no live export/backfill/publication.


### Piece 10 implementation and central-history choice pending — 2026-10-24

Uncommitted source implements Senate own-metric tabs, retained canonical run identity during delayed requests, stale-run rejection, no full vote share/holdover race odds, full100 tracker with separate caucus control/majority and tied/unresolved outcomes, Senate trend-envelope dispatch, context gaps and existing UI defaults. Both bundles rebuilt and manifest generated via scratch metadata export. Checks: mypy269, JavaScript608, focused Python79, final DOM78; actual-module smoke passed Senate35→100→35, tracker, Westminster650 and Holyrood129. Initial full suite5874 passed/3legacy assertions failed; assertions updated for Senate envelope/unknown-context semantics and focused11passed. Final full suite still required.

A concrete narrow product choice was presented before further implementation: derive central full100 totals only from validated saved model_context rosters/field winners, expose an explicit frozen-roster-only marker with null full input context, and show isolated central points without carry/bands/chances; or keep these dates as gaps until rerun. User reply is pending. Do not fabricate UsRunContext baseline/options fingerprints or use today's roster for old history. Piece10 remains unchecked/uncommitted until this choice and final checks resolve.

Root separately prepared /tmp/us-saved-central-preview/site from the corrected scratch database using committed piece9 code and SQL query_only. Port8009 serves actual435district geometry and existing stored House central run2026-10-19, blue/red colours, no uncertainty sidecar. Clear banner says it is not a newly validated2026forecast; no live data or repository export was changed. This replaces the synthetic map for assessing actual existing central display.

A descriptive presidential national proxy evidence packet is prepared at /tmp/us-president-national-proxy-proposal.json and .md using the existing verified2016/20/24RCP/FEC inventory and its hash. It contains raw margin errors, descriptive moments and whole-election leave-one-out diagnostics, accepted=false, no operational coefficients. Engine-unit conversion/recovery and state/local/district calibration remain pending. The user's approved proxy policy forbids additional national SRS; numerical acceptance is still required.


### Senate partial-context history decision — 2026-10-24

User selected **show isolated central points** for Senate history with a valid saved roster but incomplete input context. Implement the narrow frozen-roster-only adapter: validate the saved cycle, exact elected contested field and holdover slots, derive full100 central raw-party counts, and explicitly mark the partial context. Render visible isolated central points with no carry, connecting line, ranges or chances; context/segment remain null rather than invented. Missing or invalid saved rosters stay gaps; never retrofit today's roster. Numerical/release gates remain unchanged. Resume piece10 before final full checks and local commit.


### Piece 10 completed; piece 11 brief — 2026-10-24

Committed `dcd2b883`: Senate own-metric tabs and canonical attachment identity, delayed-fetch tab safety, fixed holdovers without race odds, full100 tracker/caucus-domain control and separate tied/unresolved series, explicit context gaps, and the user-approved visible central-only roster dots. The latter validate saved roster/field/winners/party IDs, work without an uncertainty table, carry no data across incomplete contexts, and never infer ranges or odds. An invalid uncertainty snapshot cannot fall back to this weaker evidence. Labels explain missing history without exposing implementation details. Both bundles rebuilt; generated manifest came from scratch metadata export.

Verification: strict mypy269; focused Python82 and JavaScript610; real-module jsdom Senate35→100→35/tracker plus Westminster650 and Holyrood129 passed; final isolated full Python5883 passed (182.99s). Source matched the verification copy; git diff checks passed. The worker hit a usage limit after starting the partial-history adapter, and root completed it inline, added regressions and final checks. No live operations, automatic review skills or remote writes.

Piece11 next: freeze President target/matchup/allocation, tally538EV/270/no-EV-majority, and tally national popular vote from51nonoverlapping states/DC while retaining56state/district simulation units. Reference populations for national/state covariance shapes and national budget weights must also exclude duplicated district votes; correcting only the public tally is insufficient. Preserve actual parent residual sharing, applied masks and exact on/off central parity. Extend replay/archive consistency, console and public export with model dispatch (database election type is us_presidential_model, model slug president). Production calibration remains unaccepted; candidate config stays disabled with no invented coefficients. Explicit proxy policy is total national polling-average error with no extra national SRS, retained in provenance. Proposal inputs exist in /tmp/us-president-national-proxy-proposal.json; engine recovery and state/local/district decomposition still pending. Piece12 owns frontend map/tracker activation.

### Piece 11 completed; piece 12 brief — 2026-10-24

Committed `d71845aa`: President adapter freezes target, matchup, allocation and counting policies; all56 state/district winners tally538EV with270 majority/no-EV-majority, while exactly51 states/DC supply popular votes, national/state covariance reference shapes and national budget weights. Frozen selected/eager replay survives mutable sources/configuration/allocation changes; public export requires exact saved-result identity and current/saved release acceptance. Console exposes seven explorer paths with correct EV/popular labels. A documented operational display extension carries only saved target_cycle and tracked_matchup after strict public-core validation. Production candidate and release remain disabled; no House calibration coefficients transferred.

Verification: strict mypy271; focused105 Python tests; isolated full5895 passed in186.23s, with all20 changed/new source files matching the check copy. Existing corrected scratch-data zero-error parity passed June29 and October19:336 recorded rows,56 units,51 references,538EV. No live database or static exports changed, no automatic review skills or remote writes.

Piece12: activate President map/tracker descriptors and gated public decoding; plot electoral_votes/legacy e out of538 rather than s=56units, electoral-majority/no-majority chances, popular vote off by default. Show coherent saved target/matchup labels, retain historical central-only EV points without invented context or uncertainty controls, break incompatible/unknown matchup/allocation segments. Extend President trend dispatch/envelope and current/saved release gates; add console home links for Senate/President diagnostics. Verify allthreeUS plus Westminster/anotherUK with focused Python, full JS, both bundles, scratch-generated manifest and real-module jsdom.

### Calibration comparison research after kernel completion — 2026-10-24

Read-only scoped research confirms no further human decision is needed to prepare unaccepted comparison reports, but a complete runtime candidate still needs explicit nomination of unidentified assumptions. President: three national proxy errors have raw-margin RMS1.933302868 points (not engine input sigma),53 final-state averages, and complete official51-state outcome counts for2016/2020/2024 whose D/R/total sums exactly match their national source records. Prior-cycle repo baseline versions must be hash-pinned and labelled as deployed artifacts, not silently promoted to official FEC counts. National total-error proxy has no extra national SRS. Fit only identifiable combined fallback error, compare H/R allocations through the real graph, and treat local-average residuals as total error rather than an incremental floor plus invented sampling. District transfer/geometry and tail validity remain explicit limits. Research notes: /tmp/us-president-calibration-prerequisites.md; descriptive source data /tmp/us-president-national-proxy-proposal.json and /tmp/us-president-state-proxy-diagnostics.json.

Senate: /tmp/us-senate-calibration-comparison-proposal.md records exact available manifests: /tmp/us-senate-poll-calibration-research.json has72 event records with24 retrieved averages and complete2022/2024 official outcomes; /tmp/us-senate-calibration-prerequisites.json has71 raw baseline sources; /tmp/us-official-calibration-joins.json has historical House generic levels/count views. Full counts cover35 distinct2022 slots and34 in2024. Ordinary SenateCycle rejects simultaneous same-state contests (OK2022/NE2024), so historical baseline-only race diagnostics may use unique slot IDs through the kernel but must not claim ordinary-runner or full-chamber replay; historical rosters/VP/caucus policies are absent. Distinguish CA duplicate ballots, GA stages, LA baseline runoff, special baselines, AK first preferences and structural candidate-eligibility zeros. Existing scratch convenience contrasts mix raw poll and decided outcome margins; derive consistently from raw facts. NV NOTA appears inside the raw candidate arrays; denominator alternatives must stay explicit/unaccepted. Compare observed-generic and official-House-input central transforms to expose generic effects, retain deployed post-blend G semantics, and do not count G twice in total residual fits. Two election folds only. No House state/race parameters transfer.

Source files above are scratch research, not durable repo documentation or accepted operational inputs. Subsequent comparison work must commit self-contained pinned manifests/provenance, deterministic generators, labelled sensitivities and console display, leave all acceptance flags false, and present concrete remaining candidate choices before enabling odds. No live repair/backfill/publication is authorised by this research.

### Piece 12 completed; calibration comparison brief — 2026-10-24

Committed `795d46b9`: President public decoder/descriptor, 538-EV map totals/ranges, electoral-majority/no-majority tracker, popular vote off by default, frozen target/matchup labels, explicit allocation/matchup/context boundaries, isolated historical central EV dots and hidden uncertainty controls when unavailable. President trends now use the US envelope through the actual us_presidential_model election type. Console home exposes all three diagnostic hubs. Both bundles rebuilt; generated manifest came from scratch metadata export. Release/calibration files remain disabled and unchanged.

Verification: strict mypy 271; focused Python 158 plus 344 compatibility tests; JavaScript 630 including jsdom DOM tests. Eight real-module smokes pass: President map 56 units/538 EV and Alabama popup, President tracker and 629 legacy points, House 435, Senate 35→100→35 plus tracker, Westminster 650 and Holyrood 129. Initial full run found twelve obsolete array/compression expectations; these were adapted to the new contract. A subsequent run hit full /tmp (SIGBUS while staging fixtures), resolved by deleting obsolete generated check copies and placing TMPDIR/pytest basetemp under the ignored workspace data/.pytest_cache. Final isolated full Python: 5901 passed in 376.06s; source matched the check copy (23 changed/new files). Saved central-only preview at http://127.0.0.1:8010/uselectionmaps/?view=polltracker&parliament=us_presidential&preview=president-tracker; no synthetic public odds, no live data changes.

Returning to piece 3 for Senate/President comparison software: commit self-contained pinned offline manifests, deterministic JSON/Markdown reports, exact shared-transform/kernel references, measurable combined fallback moments and state/race allocation boundaries, raw/decided polling-average diagnostics, independent recovery and whole-election holdouts, and read-only per-model console dispatch. Reports/scenarios stay accepted:false; no runtime candidate is nominated, no House state/race coefficients transfer, no aggregate N/W/alpha is fabricated, no district/chamber validation is claimed without its inputs. Unidentified assumptions and denominator alternatives remain explicit in the report ledger for concrete user review. House machinery/configurations and disabled release files remain unchanged. Current post-blend G semantics remain fixed; raw-input counterfactuals are diagnostic, not a silent exposure change.

### Validation preregistration preparation — 2026-10-24

Read-only scoped preparation for piece13 found that actual certified2026 inputs remain unavailable; the actual result report/checkpoint cannot complete now. Software/definitions can be prepared under the plan's preregistration requirement. Future official manifest must pin model/cycle/date/stage/revision/source hashes, exact House boundary/field identity or Senate state/class/special slot, candidate identities and raw parties, explicit candidate-to-model mapping, integer votes/authoritative winner/denominator/exclusions, and separate runoff/first-preference/final-RCV/fusion conventions. Reject incompatible or missing identities rather than infer from labels. Complete chamber/national scoring requires complete comparable fields; Senate full totals substitute observed contested winners into saved holdovers, not a later chamber.

Metrics: inclusive saved share-interval coverage/width with scored/missing denominators; full-vector unnormalised multiclass winner Brier (0–2); fixed-bin reliability as descriptive counts/mean forecast/observed frequency without pretending districts or party rows are independent election replications; per-party chamber intervals (not simultaneous coverage). Frozen national selected-poll party numerators/denominators reconstruct the generic estimator; compare its raw D−R margin to explicit official HOUSE valid-vote margin. The assumed contrast SD derives from frozen source scale times sqrt(tD+tR−(tD−tR)^2). This is an estimator-error diagnostic, not observation of a latent G draw; Senate contested popular shares are not generic truth. Keep raw and decided diagnostics separate.

Persistence replaces same-date runs and retains race detail only for latest, while national/private inputs/config/seed remain per run. Missing historical detail must be unavailable_detail, never zero. Any later regeneration needs an explicit separate mode, exact frozen inputs/kernel/seed, aggregate verification and a reconstruction label; never console GET simulation or overwritten stored runs. As-of dates have no creation timestamp and cannot prove pre-result existence. Freeze exact run IDs/result/artifact hashes plus kernel/config/release provenance in a pre-result manifest; unmatched later backfills are retrospective. History versus eventual election results includes campaign movement, so cannot establish same-day held-today coverage.

Pending user question (asked asynchronously2026-10-24, no answer at time of this note): use latest saved run dated before polling day; ten fixed10% bins for primary winner-confidence reliability; top two central parties per race for primary share coverage, with all-party coverage also reported; exact hashes distinguish pre-result snapshots from retrospective runs. No choice is assumed from elapsed time. Main piece3 comparison implementation proceeds independently. Preregistration/scorer source work has not started.

### Resume and validation definitions approved — 2026-10-25

The user approved the preregistered validation definitions: select the latest saved run dated strictly before polling day; use winner-confidence reliability in ten fixed 10% bins; use the two largest central parties per race for primary share-range coverage and also report all-party coverage. Freeze exact run/result/artifact hashes to distinguish pre-result snapshots from retrospective backfills. This resolves the pending question in the previous note; it does not approve calibration values, live imports, backfills or publication.

Checkout reconciled at poll-errors-simulation, HEAD795d46b9. Piece3 comparison work remains uncommitted: four comparison modules, two pinned input manifests, CLI/console dispatch and template. The prior worker hit a usage limit before tests/artifacts; a fresh scoped builder resumes this same approved report scope with one source writer. Root continues to own commits and checkpoints.

### Senate anchor finding and separate correction decision — 2026-10-25

The completed comparison reports show large signed baseline-only residuals even with official House national inputs: selected singleton Senate raw D−R observed-minus-projected mean +12.4973 points in2022 (31units, RMS18.7754) and +12.5325 in2024 (30units, RMS15.4735). A read-only diagnostic used the same frozen baselines, masks and projection helper but subtracted each race baseline year's official national HOUSE levels instead of the selected Senate field's shares. The means became +0.6436 and +1.6955 (RMS14.0635 and9.2551). This is an illustrative comparison, not an accepted central change; historical stages/denominators and special baseline years still require a separate correction plan. The2018 generic source remains excluded from the national-error fit; its official outcome counts were used only in this diagnostic.

User explicitly chose: finish gated validation/runbook software first and plan the Senate anchor correction separately. Do not change the central projection in the current issue. Prepare a separate local correction plan after the software work; no remote issue creation. This finding is a calibration-release blocker, not a reason to absorb the shared level mismatch silently into zero-mean variance. Comparison scenarios remain unaccepted; actual accepted calibration must be revisited after the anchor issue.

### Senate/President comparisons committed; validation preparation brief — 2026-10-25

Committed `aafc7be2`: self-contained pinned President/Senate source manifests, shared-transform zero-error references, raw/decided and denominator comparisons, state-only/race-only and centered/zero-origin boundary recovery, whole-election conditional holdouts, national input/output probes, explicit contrast groups, deterministic JSON/Markdown reports, CLI dispatch and read-only console views. House default behavior/configuration is unchanged. All scenarios remain accepted:false with no runtime coefficient nomination. President has eight recovered boundaries; Senate has twelve recovered boundaries and four explicitly unavailable decided-contrast bracket probes. Undefined D/R draws are not omitted or assigned invented denominators; bracket failure does not prove target unreachability.

Verification: strict mypy 276; focused comparison/House 53; all four report files reproduced byte for byte in independent default CLI runs; both console views returned200. Final isolated full Python5933 passed in373.71s. Fourteen implementation/artifact files matched the full-check copy; the final README-only paragraph documents the separately gated Senate anchor finding. Ruff unavailable, including scratch installation; manual formatting and diff checks applied. No live data, release flags, automatic review skills or remote writes. Piece3 checkpoint remains open for actual calibration acceptance and unresolved model assumptions/anchor correction.

Piece13 preparation now: add repo preregistration definitions and strict offline scoring/snapshot software using the approved pre-polling-day latest-run cutoff, ten fixed winner-confidence bins, top-two central share coverage plus all-party coverage. Freeze self-contained national/private documents, retained race blocks and actual central rows with exact hashes so same-date replacement/pruning cannot erase evidence; local capture timestamps are operator attestations rather than independent publication proof. Score only explicit compatible official result manifests; unavailable detail stays unavailable, no implicit Monte Carlo regeneration. Include party Brier, reliability, chamber ranges using saved Senate holdovers, and correctly labelled generic-estimator diagnostics. Console readiness pages do not simulate or invent scores. Synthetic fixtures verify software only. Actual certified2026 input, result report/checkpoint, imports/backfills/publication remain pending and separately authorised.

### Validation software integration and separate Senate correction plan — 2026-10-25

The separate correction artifacts are now at issues/anchor-us-senate-swings-to-matching-house-national-baselines/{assessment.md,issue.md,plan.md}. They require an anchor evidence/policy checkpoint before a central estimator change; the 2018 NC09 uncertified proxy is explicitly qualified. No central fix, live forecast rerun or release change was made in this issue.

Piece13 source is frozen for integration: preregistration, explicit official-result contracts and artifact hashes, self-contained read-only capture, pure scoring, CLI and console readiness. Integration caught an unsettled result-stage assumption: software now freezes an optional separately reviewed evaluation policy, requires exact manifest policy agreement, and reports unavailable_evaluation_basis without scores when absent. No first-preference/final-RCV/runoff policy was silently selected. Root confirmed this implementation boundary; actual outcome-stage policy approval remains pending. Focused strict mypy clean and204 synthetic/compatibility tests passed. Full isolated Python checks are running before local commit. Actual2026 report/checkpoint remains open.

### Validation preparation committed; operational runbook brief — 2026-10-25

Committed c7c994af under the user's sequential local-commit authorization. All10 changed files matched the isolated full-check source. Strict mypy281 source files;204 focused tests; final isolated full Python5955 passed in394.19s. CLI capture/score tests use synthetic production-format payloads, verify unchanged database bytes, survive removal of mutable DB summaries, reject source/field/stage/policy mismatches, and prove no config/.env/writer/simulation imports in the capture/scoring CLI. Actual evaluation policy, certified2026 input/report and House geometry remain pending. Senate generic-estimator scoring explicitly remains unavailable without separate House national truth rather than treating contested Senate votes as national truth. No live operations, automatic review skills or release changes.

Piece14 preparation now: repository runbook with current go/no-go blockers; committed-source scratch copy without .env; explicit isolated DB/raw/archive/temp/trend/export paths; read-only inventory and migration dry runs; evidence proposal review; exact central-row parity; actual simulation runtime/RSS measurement against300s ordinary/3600s rebuild console limits; reviewed race-case worksheet; bounded context-aware backfill and batched trends; retention/snapshot timing; scratch exports; partial-failure recovery; separate live/publication approvals. Documentation only now: actual calibrated pilots/backfills remain dependency-gated and the piece14 completion checkpoint stays open.

### Gated software handover — 2026-10-25

Committed03bbf46d: data/models/us/uncertainty-operations.md plus README link and explicit central-only/uncertainty gate behavior. The292-line runbook records the known blockers, .env-free committed-source copy, SQLite read-only backup, isolated paths, dry-run migrations, reviewed contributor evidence, exact central parity, real simulation timing/RSS (dry runs skip draws),300s ordinary/3600s rebuild console limits, case review worksheet, context-aware bounded backfill, batched trend regeneration, capture-before-pruning, full-site scratch export, scoped partial-failure recovery and separate live/publication approvals. All8 shell blocks and embedded Python parsed. Executed the setup against a new tiny scratch-only DB: copied committed source without .env, verified SQLite backup contents and resolved DB/raw/archive/temp paths under the scratch root. CLI options had been checked via --help in the isolated copy. Whitespace/local links checked. Documentation-only changes after the passing5955-test/281-file type-checked validation commit did not require repeating the Python suite. No source runtime or release flags changed.

Software preparation for13/14 is complete; their actual outcome/operational checkpoints remain open. Remaining work must not be labelled completed: model-specific calibration acceptance (including unidentified assumptions), House target geography/baseline, separately planned Senate anchor correction and recalibration, President allocation/provenance acceptance, actual contributing evidence repairs, accepted-input scratch pilots/resource/sample review, separately authorised live backfills/publication, and certified2026 scoring with reviewed evaluation basis and compatible retained pre-result snapshots. No actual2026 score, calibrated operational pilot benchmark, live model write, backfill, publication, remote issue or PR was created. Local branch remains poll-errors-simulation.

### Independent review fixes selected — 2026-10-25

The independent read-only review of03bbf46d against master found five correctness defects. User selected all five for implementation and explicitly authorised returning the clean shared checkout from master to poll-errors-simulation. Scope: dated Senate CLI poll selection; actual legacy Senate array handling; full identity guard on older Westminster poll repairs; saved regular Senate class propagation; open-popup uncertainty-toggle refresh. Separate Senate anchor correction/calibration/live operations remain gated. Follow-up tracking is in review.md. One source/test implementer; root owns checkpoints/integration checks. The additional optional-jsdom CI gap is recorded but dependency changes are not selected.

### Five independent review findings fixed — 2026-10-25

Committed76be5ea8 on poll-errors-simulation under the existing sequential local-commit authorization. Fixed dated Senate CLI endpoint scope (including explicit historical independence from today's cycle), actual legacy Senate array handling, complete poll-repair identity/share revalidation and atomic batch application, saved regular Senate class propagation through export/forecast/interactive chamber builders, and open-popup uncertainty-toggle refresh.20 changed files include a frozen legacy-array fixture, meaningful regressions and both rebuilt shared bundles. Every changed file matched the passing isolated check copy before commit.

Strict mypy281 files clean; focusedPython141 and focusedJS135 passed; final isolated Python5968 passed422.32s; fullJS with existing jsdom24 harness635 passed across34files with no skips. Build and diff whitespace checks pass. Detailed fix/check/remaining-gap record in review.md. No automatic review skills, dependency/CI changes, new calibration/release settings, live repair/model/backfill/export publication, or remote changes. The selected five findings are complete; the original feature's calibration, geography, separate Senate anchor, actual pilots/backfill/publication and actual2026-result checkpoints stay open.

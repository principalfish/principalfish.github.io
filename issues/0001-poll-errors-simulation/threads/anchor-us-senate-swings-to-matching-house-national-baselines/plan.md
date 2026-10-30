# Plan: anchor-us-senate-swings-to-matching-house-national-baselines

## Context

The user explicitly requested a separate correction plan while the US uncertainty issue finishes its gated validation/runbook software. The user resumed the audit on2026-10-26, authorising piece1 candidate evidence/loader/tests/report preparation. The user has now explicitly approved source and activation policies; piece 2 is authorised (2026-10-26). Branch at assessment: `poll-errors-simulation`, HEAD `aafc7be2`, 2026-10-25. See assessment.md for the recipe and evidence.

Senate national polling is the House generic ballot. Current baseline aggregation filters to the contested Senate field and incorporates race-specific baseline overrides, then subtracts that field's shares from House levels. The correct reference boundary must compare like populations while retaining each race's Senate baseline and its candidate-support limitations.

Current 2026 regular races use 2020 baselines, with FL/OH specials using 2022. Historical 2022 regulars use 2016 and the OK special 2020; historical 2024 regulars use 2018 and the NE special 2020. The configured 2028 field uses 2022 but lacks a verified full-chamber roster/control policy. An available anchor must never bypass that gate.

The current field resolver is unchanged by this work. Historical paired same-state slots remain calibration-only inputs until separately supported. Region is not a sufficient key: tests place regular and special races together in one region, and distinct slots can share a state.

## Approach

Audit source policies first. Pin immutable House reference counts and population definitions by baseline election year; do not query a mutable current House forecast. Use raw percentage levels consistent with the national polling estimator. Resolve a per-race fallback:

`race Senate baseline + current House generic share − pinned House baseline share for that race's baseline year`.

Keep Senate baseline aggregation for its existing descriptive and error-shape roles; do not globally replace a shared national baseline argument. Introduce an explicit per-seat fallback consumed by unpolled projection, local blending, frozen central inputs and diagnostics. Preserve all existing eligibility/Other rules, alpha weights, clamping, normalization, turnout, rounding and winner order. No national evidence retains existing zero-national-swing semantics.

Freeze complete anchor identity and versions with both central-only and uncertainty runs. Old snapshots retain old semantics, with no lookup against current anchors during replay. Anchor revisions split context/series identity and invalidate incompatible calibration acceptance. Correcting the deterministic reference does not itself change national/state error covariance or validate a variance decomposition.

## Pieces

### 1. Audit and pin House anchor evidence

- Files: new versioned anchor artifact and loader under `data/models/us/` and `data/model_support/`; source audit/report; focused input tests.
- What: pin election year, official counts, D/R/Other denominator, geographic/ballot population, fusion/party rules, stage/RCV policy, missing/unmeasured contests, corrections, source URLs and hashes. Resolve required years from race baseline metadata.
- Depends on: none.
- Acceptance: hashes/identity/sums validated; operational 2020/2022 references audited; no nominal vote invention; explicit user acceptance of source and activation policies before the central change.
- Decision gate: 2016 Peter Welch dual endorsement needs review. The 2018 FEC November proxy includes uncertified NC-09 returns; either explicitly approve a labelled historical proxy population or leave that branch unavailable. Never substitute the 2019 election or silently add 2018 to G fitting. The 2020 NY-14 correction and uncounted-write-in policy require explicit preservation; 2020/2022 unmeasured uncontested races limit the measured-vote population. The 2024 final-RCV view has Alaska verification limits and is not a complete first-choice series. Evidence acceptance and calibration acceptance are different decisions.

### 2. Apply Senate fallback per race

- Files: `data/models/us/_common.py`, accepted anchor policy/loader, Senate cycle/context readers, `trends.py`, `us_release.py`, central/blending and compatibility tests.
- Functions: baseline-year resolution, a pure fallback helper, `prepare_us_projection`, `blend_seat_swings`, unpolled projection adapter, `freeze_house_central_inputs`, `house_central_diagnostics`.
- What: construct one explicit race fallback from pinned House reference shares and the actual national estimator. Feed it consistently into all deterministic and uncertainty adapters. Keep House/President paths unchanged and preserve regional-own-poll behavior. Include the minimum piece 3 provenance dependency required for safe activation: compatible versioned central context, estimator/anchor/per-race identity in uncertainty options, separate trend identity and rejection of calibration accepted for a different estimator/anchor policy.
- Depends on: piece 1 and its source/activation acceptance.
- Acceptance: the 70/30 plus unchanged 50/50 recipe yields zero fallback; mixed-year races sharing a region use distinct correct deltas; unrelated field membership/turnout cannot alter a remaining race's deterministic House reference; alpha 0/intermediate/1 and no-evidence behavior are correct; uncertainty-on/off recorded rows are exactly equal.

### 3. Complete provenance diagnostics and legacy presentation

- Files: `data/model_support/us_run.py`, `us_outcomes.py`, `us_replay.py`, `trends.py`; central `model_context` persistence/readers; console budget/explorer decomposition and tests.
- What: the storage, estimator/anchor identity, central-context wrapper and legacy reader dispatch landed with piece 2 as an inseparable activation dependency. Complete console budget/decomposition presentation of each race’s baseline year, anchor shares, frozen current generic shares and resulting fallback, using saved data only. Distinguish deterministic anchor correction from stochastic variance; older records show legacy/unavailable provenance.
- Depends on: piece 2.
- Acceptance: old documents/archives replay byte-exactly after current anchors change; missing anchor metadata means legacy semantics; new anchor/version splits segments even when central counts coincide; current mutable DB/rosters/anchors never relabel saved runs; budget and decomposition identify deterministic fallback separately from stochastic variance. Older console records clearly show unavailable/legacy anchor provenance.

### 4. Rebuild comparisons and prepare recalibration for review

- Files: `data/model_support/us_calibration_comparison.py`, `us_comparison_senate.py`, new versioned source/report artifacts, CLI/console consumers and documentation.
- What: use the same corrected fallback helper for production and historical references, including special baseline years. Preserve the original comparison artifacts or provide explicit versioned identities. Reassess signed means as well as centered moments, while retaining structural eligibility and source omissions in the ledger.
- Depends on: pieces 1–3 and each historical source-policy gate.
- Acceptance: source/anchor/report fingerprints are reproducible; no selected-field subtractor remains in corrected reference construction; all calibrated quantities and unresolved assumptions are stated; the 2018 branch is qualified or unavailable according to its approved policy. No runtime acceptance, live rerun/backfill or publication occurs automatically.

## Checkpoints

- [x] Read-only assessment confirms the bug and concrete reproduction.
- [x] User requested this separate plan; assessment was read-only.
- [x] Piece 1 candidate audit software and source report prepared and verified (2026-10-26).
- [x] Anchor source and activation policy audit reviewed and accepted (2026-10-26).
- [x] Per-race fallback implemented; deterministic recipe and mixed-year tests pass (2026-10-26).
- [x] Exact central parity, legacy replay and context separation verified (2026-10-26).
- [x] Complete saved per-race anchor/current-generic diagnostics in console (2026-10-26).
- [x] Corrected comparison software and versioned artifacts regenerated reproducibly (2026-10-26).
- [ ] Corrected comparison proposal manually reviewed.
- [ ] Calibration assumptions/values separately accepted, or release remains gated.
- [ ] Any live reruns/backfills/publication separately authorised.

## Verification

Run strict mypy before focused pytest from `data/`, using the `election_data` interpreter (fallback `.venv`):

```bash
./election_data/bin/python -m mypy
./election_data/bin/python -m pytest tests/test_us_senate_uncertainty.py tests/test_us_calibration_comparison.py tests/test_model_trends.py tests/test_us_uncertainty_explorer.py tests/test_us_model.py -q
```

Run the complete `cd data && ./run_tests.sh` only in an isolated source copy without `.env`; the full suite can dirty tracked trend files. Its script prepends `tests/`, so positional files do not narrow it. Use explicit pytest for focused checks. `.env` overrides shell DATABASE_PATH; verify scratch configuration and never test against live data. Put large fixture/temp files on the ignored workspace cache if `/tmp` is constrained.

Meaningful cases include missing/mismatched anchors failing before persistence; same-region mixed baseline years; deterministic independence from unrelated cohort changes; no national polls; alpha endpoints/intermediate; new entrants/absent parties/Other rules; ties/rounding; unchanged House/President rows; current-anchor mutation leaving old replay untouched; anchor/context changes breaking history; correct special baseline-year selection; and 2018 policy enforcement. The anchor does not fix California's structural candidate zero or supply a missing 2028 roster.

Generate corrected reports through `scripts/calibrate_us_uncertainty.py --model senate --output-dir <scratch>` only after the historical anchor policy is accepted. Compare repeated outputs and provenance. If frontend handling changes, run `npm test` and rebuild both bundles with `npm run minify:electionmaps`; otherwise do not broaden unrelated UI scope.

## Notes

Prepared 2026-10-25 while validation software for `add-us-election-uncertainty-and-overall-poll-trackers` proceeds separately. No source, live database, remote issue or public output was changed. The diagnostic improvement is evidence for the reference problem, not evidence that candidate/local residuals vanish or that a zero-mean error scale is operationally accepted. Implementation must stop at the explicit audit gate if source population or activation policy remains unresolved.

### Piece1 audit resumed — 2026-10-26

Checkout reconciled: clean poll-errors-simulation at76be5ea8, with the five prior review fixes committed. User's OK RESUME follows the recommendation to tackle the Senate anchor audit. First-section brief delivered: versioned candidate House national anchors, offline immutable validation/inspection, focused tests and source report; operational2020/2022 plus explicit historical2016/2018 qualifications, with2024 retained as an unavailable future audit note. No published-general versus final-RCV counting policy,2018proxy admission or activation date is silently selected. All candidates remain unaccepted; no central/runtime wiring, live DB run/repair, release change, report overwrite or publication. Source and activation approval remain a concrete checkpoint after the tested audit is ready. One scoped implementer owns source/artifacts/tests; independent read-only evidence research supports the source audit; root owns this checkpoint.


### Piece 1 audit prepared — 2026-10-26

Five uncommitted files are ready on poll-errors-simulation at 76be5ea8: immutable
candidate inspector, pinned catalogue, offline workbook reproduction command,
source audit and 28 focused tests. Independent extraction and documented CLI
reproduction agree for both views in 2016, 2018, 2020 and 2022. Exact catalogue
SHA256: e20765425615bdc43a2e063f0ab260ca6f00e2ebd27bff6e1be9101dabe50757.

Strict mypy passed 284 source files; the full isolated Python suite passed
5,996 tests in 399.40 seconds. The isolated tree excluded .env, and all executable,
test and catalogue bytes match the passing copy. Log:
data/.pytest_cache/senate-anchor-piece1-full.log. No frontend changes or automatic
review-skill passes. No runtime integration, old-report overwrite, live database,
release, backfill or public export changes.

The concrete recommendation in data/models/us/house_anchors/source-audit.md is
published-general measured candidate-party raw shares for 2020/2022, keeping
Other in the denominator, explicit candidate fusion, no invented unopposed votes,
and the certified NY-14 +30 Republican/+30 denominator correction. Final-RCV
remains a sensitivity view. Historical 2016 Welch Democratic classification is
supported by the official party table but still needs acceptance; 2018 remains
blocked pending unavailable-versus-qualified-proxy policy. Proposed activation
is senate-house-anchor-v1 for newly computed runs, preserving old saved runs and
replay and requiring an explicit request for historical recomputation. All source
and activation choices remain pending. Stop before piece 2 until accepted.


### Policies accepted; piece 2 started — 2026-10-26

The human approved all three questions separately: (1) published-general 2020/2022
raw measured candidate shares with Other retained, same-candidate fusion,
no unmeasured-vote invention and certified NY-14 +30 R/+30 denominator;
(2) classify Welch as Democratic in 2016 using the official party table and leave
2018 unavailable because NC-09 was uncertified; (3) senate-house-anchor-v1 for
newly computed runs, preserving saved runs and replay, with live reruns,
backfills and exports outside this authorisation. Final-RCV remains sensitivity
evidence; calibration remains a separate unaccepted decision.

Checkout reconciled: poll-errors-simulation at 76be5ea8, with only the five
untracked audit files from piece 1. User-facing piece 2 brief delivered:
per-race House reference from each race's baseline year, mixed regular/special
handling, unchanged House/President and regional-own-poll semantics, exact
uncertainty-on/off central parity and legacy replay. One scoped source writer
implements piece 2; root owns checkpoints and isolated full checks. No automatic
review workflow advancement or remote/live operations.


### Piece 2 safe activation boundary refined — 2026-10-26

Source inspection confirms three inseparable activation seams: central-only
model_context currently saves only SenateCycle, uncertainty options omit the
estimator identity, and Senate release checks only the stochastic kernel.
Activating the corrected arithmetic alone could mix estimator meanings or use
calibration accepted against the old reference. Therefore piece 2 includes the
minimum originally-piece-3 provenance needed to honour the approved activation:
versioned central-context wrapper with legacy reader dispatch; estimator and
accepted policy digest plus per-race baseline years in frozen uncertainty
options/provenance; compatible central trend handling; and estimator/policy
matching in Senate release/calibration gates. Synthetic fixtures may explicitly
name the new identity, but no production calibration is accepted. Existing
central_raw replay remains frozen and needs no lookup against current anchors.
User was briefed on this cohesive boundary. Detailed console presentation and
historical comparison regeneration remain later pieces. One source writer owns
all necessary files, so there is no parallel implementation overlap.


### Piece 2 implemented and verified — 2026-10-26

Per-race House fallback, blend/unpolled/frozen adapters, actual race diagnostics,
accepted policy resolver and preflight checks are implemented. Accepted policy
SHA256: 6568de70b4317be92b58a7450de2d4d262e397989e5364cb8a8447bfbd4e71ee.
Candidate evidence bytes remain unchanged. All approved source decisions are
represented in accepted-v1.json, separately from immutable candidates-v1.json.

Minimum piece 3 safety work is also complete: new central-context wrapper,
self-contained legacy/new reader validation, frozen estimator/anchor/per-race
identity in uncertainty options, compact public central-basis fingerprints,
and release/calibration/publication rejection of incompatible estimator identity.
Saved replay never resolves current anchors. Error-budget options render a compact
estimator/policy/race-count summary rather than raw extraction arrays. Full
per-race anchor/current-generic presentation remains piece 3; corrected historical
comparison generation remains piece 4. No production calibration was accepted.

Verification: strict mypy passed 286 source files. Focused suites passed 507
central/model cases, 20 anchor/guard cases, 170 context/trend/explorer/comparison
cases and 67 final Senate/explorer cases. Isolated full suite: 6,015 passed and
one stale persistence fixture failed because its shared Senate baseline was 2024,
which is correctly unavailable under the approved policy. The test alone now
uses approved 2020 for Senate; all other model years and rollback assertions are
unchanged. All 30 persistence tests then passed, and the full-check script reran
its sole cached failure successfully after strict mypy. Thus all 6,016 cases are
verified across the full run and targeted rerun, without rerunning 6,015 already
passing cases after a test-fixture-only change. The final compact-budget console
assertion additionally passed in the isolated tree. All final changed files match
that tree; .env was absent.

Logs under data/.pytest_cache/: senate-anchor-piece2-full.log (full run),
senate-anchor-piece2-full-rerun.log (strict mypy + sole failed case rerun),
senate-anchor-piece2-final-console.log, senate-anchor-piece2-persistence.log,
senate-anchor-piece2-context.log and senate-anchor-piece2-budget.log.
No automatic review skills, commit, live operation, report regeneration, backfill,
public export or remote write occurred. Current changes are ready for human
review; stop before committing or advancing to the remaining section.


### Selected review fixes started — 2026-10-26

The human selected all three findings in the findings-only uncommitted review:
independent missing/wrong anchor-policy digest regression coverage, first-party
import ordering, and the multiline tuple trailing comma. Scope is these fixes,
relevant verification, then a pause before commit. No further planned section or
automatic review workflow is authorised by this selection. One scoped source
writer owns test/style changes; root owns external checkpoint records and
isolated full verification. Existing estimator arithmetic, accepted policy and
evidence bytes remain unchanged. Review report:
reviews/anchor-us-senate-house-baselines-uncommitted-20261026-76be5ea8/review.md.


### Selected review fixes complete — 2026-10-26

All three human-selected findings are addressed: independent missing/wrong policy
hash regression cases with valid estimator controls, new anchor import tidying,
and the multiline tuple trailing comma. Ten new cases catch separate removal of
the current-settings (four cases), saved-provenance (four), and frozen-options
(two) digest guards in in-memory mutation probes. Runtime guards and accepted
source artifacts remain unchanged. Only the new test file and three style files
differ from the reviewed implementation; no unrelated legacy formatting expansion.

Strict mypy passed 286 files; 84 focused tests passed; the full isolated suite
passed 6,026 tests in 439.15 seconds. All 16 current issue files match the passing
copy without .env; git diff --check is clean. Logs:
data/.pytest_cache/senate-anchor-review-fixes-full.log and senate-review-*.log.
The stored review report now records the resolved findings and verification.
No automatic review passes or later piece advancement. No commit/live operation,
backfill/export/report regeneration/remote write. Stop before commit as explicitly
requested by the user.


### Reviewed audit/correction committed — 2026-10-26

Human explicitly requested the local commit. Committed all 16 issue files as
087afcfc (fix: anchor Senate swings to matching House baselines) on
poll-errors-simulation, preserving earlier history. All staged source bytes
matched the isolated 6,026-test passing copy and staged whitespace checks passed.
No push or remote mutation. Pieces 3 (remaining per-race console presentation)
and 4 (corrected comparison/calibration proposal) remain pending; calibration,
live reruns/backfills and publication are still separate decisions. No automatic
workflow advancement after this commit.


### Piece 3 console provenance started — 2026-10-26

Human requested the next stage after local commit 087afcfc. Checkout verified
clean on poll-errors-simulation at that commit. Brief delivered: show race
baseline year, matching House anchor, frozen national generic polling level and
actual fallback in budget/decomposition views, separated from stochastic error.
Use saved inputs only and label older records with unavailable/legacy metadata.
One scoped source writer owns console service/blueprint/template/tests; root owns
checkpoints and final isolated checks. Estimator arithmetic, source policies,
context identities, calibration acceptance and comparison regeneration are outside
this section. No current poll/config lookup may relabel saved values; no live
operations or automatic review/commit advancement.


### Piece 3 console provenance complete — 2026-10-26

Four uncommitted files implement the budget/decomposition presentation: console
service, explorer blueprint, shared template and new provenance regression tests.
The shared table displays each race’s frozen Senate baseline year/identity,
matching published-general House year/share, raw party-specific weighted current
generic level, House delta, route, eligible fallback and weighted fallback.
National evidence uses only admitted frozen national snapshots and saved weights;
regional precedence uses frozen selected_polls.region_shares, including an own
regional poll implying exactly zero swing. Structural eligibility and actual
retained fallback/local contributions are preserved. Legacy anchor absence,
missing diagnostic detail and missing regional evidence receive explicit labels.
The neutral explanatory wording does not relabel legacy estimators as V1.

The implementation reads no current polling/anchor/calibration settings to
reconstruct saved values. Integration tests change current poll rows, party/seat
names, release settings and anchor resolution and confirm saved budget and
single-simulation detail HTML remain identical. Other meaningful cases cover
2020 regular versus 2022 special baselines, party-specific weighting/explicit zero,
no national evidence, regional override, structural zero, partial/full local blend,
and legacy/missing provenance. No estimator arithmetic, stochastic logic,
fingerprints, replay/storage schemas, source policies or release acceptance changed.

Strict mypy passed 287 source files. All 11 new provenance tests passed; the
preceding focused integration run also passed all 67 existing Senate/explorer
cases. Final .env-free isolated full suite passed 6,037 tests in 423.39 seconds.
The final template wording was synchronized before the US console tests; all four
final files match the passing tree. git diff --check is clean. Logs under
data/.pytest_cache/: senate-provenance-mypy.log, senate-provenance-pytest.log,
and senate-console-provenance-full.log. No frontend engine edits; no JS build or
unrelated JS checks were required.

Source is ready for manual review/testing, with no automatic review-skill pass,
commit or advancement to piece 4. Corrected historical comparisons and the
recalibration proposal remain the final implementation section, preserving the
approved 2018-unavailable policy. No live model operation, backfill, export,
calibration approval, publication or remote write occurred.


### Piece 3 committed; piece 4 started — 2026-10-26

The human requested commit and continuation. Console provenance committed locally
as 134d1fc4 (feat: explain frozen Senate anchor inputs in console), four files,
with a clean checkout afterwards. All pending bytes matched the passing isolated
6,037-test copy before staging. No remote operation.

Final implementation brief delivered: regenerate historical conditional
comparisons using the same matching-year fallback as production, preserving
original report/input bytes and keeping the approved 2018 branch unavailable.
Prepare versioned unaccepted calibration/report artifacts with signed-mean and
spread diagnostics, source/version identities and clear unavailable-history
qualification. No production calibration acceptance, live rerun/backfill or export.
One source/artifact/test writer owns this section; root owns issue checkpoints
and isolated full verification. Numerical or source ambiguity must be raised;
no additional source population is silently accepted.


### Piece 4 implementation and proposal complete — 2026-10-26

Twelve uncommitted files implement the final comparison section. Senate units now
carry their source baseline year; raw-poll and official-input counterfactuals use
the production senate_fallback_swings helper per slot. The two Oklahoma slots
resolve 2016 and 2020 separately. Full-field composition remains solely stochastic;
party masks, structural exclusions, denominator alternatives and source candidate
qualifications are unchanged. Approved unavailable 2018 makes all 34 slots of the
2024 conditional reference unavailable together; its NE special and every original
slot/baseline/reason stay in the availability ledger rather than creating a partial
cohort. Corrupt/missing/hash-mismatched accepted references hard-fail, separately
from this explicit policy exclusion. National and local source facts are retained.

New comparison version us-senate-house-anchor-comparisons-v2 identifies central
estimator senate-house-anchor-v1, accepted policy/catalogue digests and full
reference identity. Versioned JSON/Markdown/report-review proposal preserve all
legacy artifact bytes. CLI outputs the new Senate filename; console distinguishes
current and explicit legacy comparisons. Original national/current-official facts
remain the qualified final-RCV/no-nominal proxy, distinct from published-general
baseline anchors; vote-stage/population transport stays unaccepted. President
and House generation semantics remain unchanged.

For reported-total raw 2022 singleton conditional targets, observed-minus-projected
mean changes from legacy +12.496944 pp to +0.643275 pp; corrected raw-poll mean is
+0.832158 pp. Centered conditional second moment remains 197.367184 pp squared.
All 35 slots remain in the stochastic field and 31 singleton targets enter the
moment. All 16 state/race, centered/zero-origin, raw/decided and denominator
sensitivity fits numerically recover at 2,048 fit / 8,192 independent recovery
draws. This is conditional diagnostic evidence, not deployable forecasting or
tail/coverage validation. Only one conditional cycle is usable: empty-training
holdout scales/targets/intervals are explicitly unavailable, not zero. Independent
national holdouts retain all six admitted generic-error cycles. No coefficient
allocation or numerical calibration is accepted or nominated for production.

Default Senate JSON and Markdown generated twice byte-identically in an .env-free
copy. SHA256 JSON ccffea86c2d3c40d464d5a3000cdff9aa4eac40107e2cf47fa65ccc59f6c0a02;
Markdown 42e4ba461a974b36a46eb0ed7ff598f1cdc0158407596a508d250ebf9a60026f.
Default regenerated President JSON/Markdown also match committed originals exactly.
Original Senate input/legacy reports remain unchanged. Review proposal:
data/models/us/calibration/senate-comparison-house-anchor-v2-review.md.

Strict mypy passed 288 files; 109 focused comparison/anchor/Senate/explorer tests
passed. Final isolated full suite passed 6,047 tests in 438.86 seconds. All 12
final files match the passing copy without .env, source artifact hashes match the
reproduced identities, every report/scenario remains accepted false and git diff
--check is clean. Logs under data/.pytest_cache/: senate-comparison-{mypy,focused,
reproduction}.log and senate-anchor-comparison-full.log. No frontend engine edits
or unrelated JS checks. No automatic review workflow, commit, runtime calibration
change, live forecast/backfill/export/publication or remote write.

All four implementation sections are now prepared and verified. Stop for manual
review of this final uncommitted section/proposal. Numerical model acceptance and
separately authorised operational runs remain open; the original broader US
issue's geography, evidence, actual-pilot and certified-2026 checkpoints are not
claimed complete by this correction.


### Piece 4 committed — 2026-10-28

User explicitly requested committing the 12 pending comparison/report files. Committed locally as `282dd916` (`feat: rebuild Senate comparisons with matching House anchors`) on `poll-errors-simulation`. All 12 files matched the isolated source copy that passed strict mypy (288 files) and the full Python suite (6,047 tests); `git diff --check` passed. No additional review was run. Comparison review and numerical calibration acceptance remain pending, release stays gated, and no live reruns, backfills, publication or remote changes were performed.

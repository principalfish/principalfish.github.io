# 2026 House and Senate uncertainty validation preregistration

Definitions fixed on 25 October 2026, before polling day (3 November 2026).
Actual certified 2026 result inputs and the actual validation report remain
pending. This document and scorer software do not accept calibration, import
results, backfill runs, enable releases or publish forecasts.

## Frozen forecasts and final-run selection

The final nowcast is the latest saved run **dated strictly before polling day**,
with election ID breaking same-date ties. Capture defaults to that single run;
`--include-history` additionally captures earlier eligible saved dates. Never
substitute an older run because the latest lacks uncertainty. Each date keeps its
own context, party vector, configuration and intervals. No carry across dates or
contexts, and no automatic historical regeneration.

Persistence replaces same-date output and prunes historical race detail. Election
rows have no creation timestamp. Therefore a dated database row is not evidence
that its forecast existed before results. Before results, capture self-contained
national/private documents, retained race blocks and actual stored central vote
rows. The file records election IDs, result/artifact identities, component and
whole-run hashes, saved config/release hashes, kernel version and capture-code
hashes. It retains private selected polls and source graphs. Scoring consumes
these exact embedded payloads, never replacement database rows. Preserve the
snapshot and its checksum outside mutable working files, with independently dated
evidence if a prospective out-of-sample claim is to be made. A local clock value
and hash alone are an operator attestation, not independent publication proof.
Do not commit large snapshots or private polling inputs to this repository.

Capture after polling day is labelled **retrospective**, even when the embedded
as-of date precedes polling day. Missing historical race detail is
`unavailable_detail`, with null race scores; national/chamber summaries can still
be scored. Missing uncertainty is `unavailable_uncertainty`. No Monte Carlo
execution occurs during capture, scoring, or console GET requests. Existing runs
pin a kernel version but lack production-time source-file hashes: captured source
hashes identify code at capture and must not be described as proof of historical
production source bytes. Exact frozen outputs suffice for the defined scoring;
reproduction of historical simulations is outside this software scope.

From `data/`, with explicit local paths and IDs:

```sh
./election_data/bin/python scripts/validate_us_uncertainty.py readiness --model house
./election_data/bin/python scripts/validate_us_uncertainty.py capture \
  --model house --database /path/to/offline-copy.db --map-id 1 \
  --name-prefix 'US House UNS' --output /path/to/new-house-snapshot.json
./election_data/bin/python scripts/validate_us_uncertainty.py score \
  --snapshot /path/to/house-snapshot.json --results /path/to/official-manifest.json \
  --output /path/to/new-validation-report.json
```

Capture opens SQLite in read-only URI mode, sets `query_only`, and reads in one
transaction. It never opens `.env`, creates a database or writes saved outputs.
An output must be a new explicit path. JSON inputs/outputs are bounded to 128 MiB,
history to 256 runs and central rows to 10,000 per run. The console view at
`/us/{house|senate}/uncertainty/validation` displays definitions/readiness only;
it cannot discover official results or claim a score from current data.

## Explicit future official-result manifest

Actual result-stage, denominator and aggregation policy review remains pending.
Capture may additionally take `--evaluation-policy /path/to/reviewed-policy.json`.
There is **no default scoring basis**, and no inference from the baseline's stage.
Without a frozen reviewed policy, scoring returns `unavailable_evaluation_basis`
and produces no race/chamber/diagnostic scores. A reviewed policy contains schema
`pf-us-validation-policy-v1`, `model`, `cycle: 2026`, nonempty `approval` and
`provenance` labels, `winner_domain: "raw_model_party"`, and
`candidate_aggregation_policy`. Its separate `share_stage` and `winner_stage`
objects each specify `vote_basis`, `denominator_policy` and `exclusions_policy`.
The official manifest must agree exactly with these frozen choices. The scorer
does not choose first preferences, final RCV or runoff conventions for the user;
any incompatible/unsettled basis must be reviewed explicitly before scoring.

Use schema `pf-us-validation-results-v1`, `model` (`house`/`senate`), integer
`cycle: 2026`, `polling_day: "2026-11-03"`, and a nonempty `revision`. This is a
contract description, not an invented 2026 official input. Required fields:

- `sources`: nonempty array of `{id, url, artifact, sha256, authority,
  publication_date, retrieved_at, extraction_provenance}`. Retrieval timestamps
  require an explicit UTC offset. `artifact` is a local file, relative to the manifest
  or absolute. The CLI verifies its SHA256; sources and extraction need review
  before any actual-results scoring. Hashes pin revisions, not authenticity.
- Separate `share_stage` and `winner_stage`, each containing `identity`, `date`,
  `revision`, `authority`, `certification_status: "certified"`,
  `denominator_policy`, `exclusions_policy` and `source_ids`. Stage
  dates cannot precede polling day. State explicitly first preferences versus
  final RCV, general election versus runoff and any same-party/fusion policy.
  Never substitute a final-round share denominator for first preferences or
  infer the authoritative winner from the scored share counts. Both stages
  declare `vote_basis` matching the explicitly reviewed frozen evaluation policy,
  with each race explicitly declaring its winner selection method. The top-level
  `winner_domain` must be `raw_model_party`.
- `candidate_aggregation_policy`: explicit treatment of fusion ballot lines,
  same-party candidates and write-ins. Each candidate occurs once per race;
  consolidate fusion lines under one candidate identity before input.
- `field`: `model_field_id` (the exact frozen context fingerprint) and
  `source_ids`. A House field additionally requires `boundary_sha256`,
  `boundary_approval` and `districts` mapping every frozen seat ID to an exact
  official district ID. The same separately approved field must have been
  included at capture with `--house-field`. Identical names or a count of 435
  alone are insufficient. A changed boundary fails compatibility. No current
  House boundary approval is implied by this software.
- `races`: the complete comparable field. Each race has integer `seat_id`,
  `identity`, `share_stage_id`, `winner_stage_id`, positive integer `denominator`, `candidates`, `excluded_counts`,
  and `winner`. House identity is `{district_id, boundary_sha256}`; Senate is
  `{state, seat_class, special}` and must exactly match saved slots. No missing
  or duplicated race is imputed. Partial certified fields cannot produce an
  apparent complete chamber/national score.
- Each candidate has `{id, name, raw_party, model_party_id, votes}`, with explicit
  integer model party ID and nonnegative integer counts. Candidate counts sum
  exactly to the authoritative valid-vote denominator. `excluded_counts` is an
  explicit array of `{reason, count}`, including an empty array when none.
  Blank/invalid ballots never become Others. Every raw-party mapping is explicit;
  absent labels never determine party identity. Same-party candidates aggregate
  to party votes for this model's scoring.
- `winner` is a separate authoritative `{candidate_id, name, raw_party,
  model_party_id, method}` assertion under `winner_stage`. `method` must explicitly
  be `plurality`, `runoff`, `rcv_final`, `unopposed` or `official_tiebreak`.
  Shared candidate identities
  must agree between stages. A runoff or RCV winner can differ from the plurality
  of the share stage. This evaluates party winner probabilities, not candidate
  probabilities.
- House additionally supplies `generic_contrast` with explicit
  `democratic_party_id` and `republican_party_id`. This contrast uses all complete
  comparable House valid votes. Senate contested votes are not national generic
  ballot truth; Senate receives an explicit unavailable status for this diagnostic.

All score inputs are read-only. A real official import or an actual-results
policy decision remains a separately approved operation. Incompatible facts fail
closed; the scorer does not silently select different stages or older contexts.

## Metrics, units and missingness

For each eligible saved date/context, score the recorded interval level (no
resampling or interpolation) against explicit observed party shares. Coverage is
inclusive at both endpoints; width is upper minus lower in percentage points.
Primary coverage/mean width uses the two largest **central share** parties per
race, ties resolved by the frozen party order. Secondary coverage/mean width uses
the full frozen party vector, including zero-share parties. Report counts and
race/party rows so zero-heavy secondary coverage cannot obscure the primary.

For each retained race, multiclass Brier is `sum((p_j - y_j)^2)` over the complete
party vector, where `y` is one-hot for the authoritative winner's mapped party.
Its range is 0–2; report the mean across races. Favourite confidence is the largest
party win probability; a tie uses frozen party order. Reliability uses ten fixed
bins `[0,.1), [.1,.2), ... [.9,1]`; exact confidence 1 belongs to the last bin.
Each bin records count, mean confidence and empirical favourite accuracy. Empty
bins retain count 0 and null means. Do not pool dates or treat repeated forecasts
of the same race as independent observations.

Chamber range coverage is inclusive per raw party. House counts authoritative
winners over the complete compatible 435-seat field. Senate separately scores
contested seats and the full 100: frozen holdover counts equal saved full central
party totals minus saved contested central party totals, checked nonnegative and
against saved roster/field sizes. Add observed contested winner totals to those
saved holdovers. Never use a later roster, infer caucus/control, or claim a
full-chamber popular vote. Ranges and means remain attached to each date/context;
this software does not pool across incompatible contexts.

The generic diagnostic reconstructs the actual saved selected national polls and
weights with the production `aggregate_national` semantics: filter national,
generic-matchup readings and use a separate weighted denominator for each party.
Do not renormalize raw shares or invent an aggregate sample size. Compare raw D−R
margin to the official complete House national valid-vote D−R margin. For the
saved national source with proportions `t` and scale `s`, the implied D−R contrast
SD is `s * sqrt(t_D + t_R - (t_D - t_R)^2)`. Report signed
`(estimator - outcome) / SD` only when SD is positive; zero SD returns a null
standardized residual and explicit `zero_sigma` status. This is a diagnostic
estimator residual, **not an observed latent G factor**. Kernel G acts after the
local blend and cannot be recovered from one final national margin.

Only one correlated election is observed. Early held-today forecasts versus an
eventual election result include campaign/opinion movement and do not establish
same-day nowcast coverage. No coefficient is refitted, and any 2028 calibration
revision needs a separate recorded approval. Synthetic tests demonstrate software
arithmetic and rejection behavior; they are never a public forecast or an actual
2026 validation report.

# US uncertainty boundary contracts

These contracts describe held-today simulations. House storage, replay and schema
dispatch are implemented behind the explicit production release gate. US public
sidecars and trends remain pending activation. Golden files under
`data/tests/fixtures/us_uncertainty/` use **synthetic party/map identifiers and
results**, not calibrated forecasts. Fingerprints in fixtures are illustrative;
production producers must hash the canonical frozen input, not assign a label.

## Versioned formats

| Boundary | Schema |
|-|-|
| Stored national block / database schema discriminator | `pf-us-model-uncertainty-v1` |
| Public sidecar | `pf-us-public-uncertainty-v1` |
| US trend envelope | `pf-us-model-trends-v1` |
| Existing Westminster stored blocks | `pf-model-uncertainty-v1` (unchanged) |

Both stored and public summary documents have exactly `schema`, `model`,
`context`, `result_identity`, `summary`. Stored documents additionally have
`private`, an object reserved for diagnostics, configuration and replay inputs.
No private field is copied to a public document. This piece fixes the public and
national-summary boundary; the private replay/input schema is finalized alongside
its engine before the first write. The current validator checks that `private` is
an object, **not** that a run can be replayed. A successful contract check is not
permission to persist or publish an uncalibrated forecast.

`model` is `house`, `senate` or `president`. `result_identity` pairs the central
results and their summary, extending the existing result-identity inputs with
model/counting context. An exporter must recompute and compare it; a nonempty
string check alone does not establish a match.

`summary` contains exactly `simulations` (positive integer), `interval` (strictly
between zero and one), `metrics`, and `outcomes`. No missing probability means
zero: all participating series include explicit zero values. Percentages are
0–100; probabilities are 0–1. Counts are integers, never booleans. All numerical
values are finite. Public share rounding is at most 2dp; keep sufficient
probability precision to preserve mutually exclusive outcome conservation.

## Metrics and outcomes

Each metric has `unit`, `domain: "party"`, `total`, `central` (party id to value),
and `ranges` (same party ids to `[low, median, high]`). Metric names carry meaning;
consumers cannot infer a metric from a numeric value or the parliament name.
Party ids are canonical positive decimal database ids. Outcome caucus ids are
stable names in the saved control policy, a distinct namespace.

| Model | Required metrics | Required outcomes |
|-|-|-|
| House | `house_seats` (435 seats), `national_vote_share` (100 percent) | `majority`, `no_majority` |
| Senate | `senate_contested_seats` (saved field size), `senate_full_seats` (100 seats) | `control`, `unresolved_control`, `majority`, `no_caucus_majority`, `tied_caucuses` |
| President | `electoral_votes` (538 EVs), `national_vote_share` (100 percent) | `electoral_majority`, `no_electoral_majority` |

`majority` and `electoral_majority` are objects with `domain: "party"` and
`probabilities: {party_id: probability}`. `control` has `domain: "caucus"` and
`probabilities: {caucus_id: probability}`. The residual and tied events are scalar
probabilities. Majority thresholds are 218 seats and 270 EVs. Senate control uses
saved caucus alignments and VP policy: 51 is an outright majority, not the only
route to control. Each success-probability sum plus its residual equals one.
Senate `majority` uses the caucus domain and records outright 51+ membership;
its sum plus `no_caucus_majority` equals one. This is distinct from VP-resolved
control. `tied_caucuses` is a subset of `no_caucus_majority`.
`tied_caucuses` overlaps resolved control and is not added to that sum. A tied
chamber without a configured tie-breaking assumption remains unresolved. Do not
call any of these events Westminster's `hung` or infer control from raw party
seat totals. A Senate whole-chamber popular-vote series is deliberately absent.

Central counts sum to the declared metric total. Vote percentages allow only
per-party 2dp rounding error in their sum. Bounds are ordered and lie within the
metric domain; their sums need not equal the total. The deterministic central
estimate need not lie inside its simulated interval. Full joint consistency,
Senate holdover arithmetic, EV allocation, histograms and draw replay need engine
integration tests; this boundary validator does not fabricate those guarantees.
Regional and race summaries are not national metrics: their separately scoped
contracts must carry matching race/aggregate identity before UI attachment.

## Context and frozen counting inputs

`UsRunContext` defines every required context field, including explicit nulls for
inapplicable references. All models pin model, map, as-of date, target cycle,
matchup, field, baseline, central options and comparability id. Senate additionally
pins `roster_id` and `control_policy_id`; President pins
`electoral_allocation_id`. Unknown historic context is null at the **trend point**
level; do not manufacture a current roster or matchup for it.

References must resolve to immutable inputs saved with the run, independent of
later database edits. The retention design must preserve the compact counting
context needed for historical totals even when detailed draws are pruned:

- Senate roster: all 100 unique seat slots, contested race-to-slot mapping,
  fixed holdovers and vacancies, raw party ids and stable candidate identities.
  A special and regular race in one state remain distinct; no holdover receives
  a simulated win probability.
- Senate control policy: explicit independent/candidate caucus alignments,
  configured VP caucus, effective dates, provenance and conditional assumption
  label. Never infer an independent's alignment from a party colour or query
  today's VP during replay.
- Presidential allocation: target-cycle EV allocation by state/district unit,
  exact winner/allocation rule and authoritative provenance. Popular-vote
  aggregation includes each state once and excludes district duplicates.
- Matchup/field/baseline/options snapshots: the actual selected candidate field
  and estimator inputs, including any sanctioned absent-party behaviour.

`segment_id` is SHA-256 over canonical context fields excluding `as_of_date`.
Seed, changing poll input hashes and run date do not break a comparable series.
Cycle, matchup, field, roster, control policy, allocation and central-option
changes do. Config hashes are retained privately; `comparability_id` changes
explicitly when interpretation changes, rather than on every numerical refit.

## Trend envelope and gaps

The exact new envelope is `{schema, model, points}`. Each point contains
`election_id`, `election_name`, `as_of_date`, `context`, `parties`, `metrics`,
`summary`. `context` is the complete context above, or null for legacy unknown
meaning. `metrics` has the same named metric records as the summary but omits
`ranges`; it is available independently of simulations when counting context is
known. `summary` is the complete summary object above or explicit null. With
unknown context, `metrics` is empty and `summary` must be null; retain legacy
central `parties` honestly. Matching point/summary/context dates and central
metrics must be enforced when the trend writer is integrated.

Legacy `parties` remains `{party_id: {s, v, e?}}`:

- House `s` is district wins.
- Senate `s` remains **contested** wins; full-chamber totals are only in the
  named `senate_full_seats` metric.
- President `s` remains allocation-unit wins and `e` remains electoral votes.

Compression compares legacy central values and named central metrics within the
same context. Ignore ranges/odds differences between otherwise equal summarized
runs. Preserve the first summary after central-only history, and preserve an
explicit loss of summary as a gap. Unknown or changed contexts always retain a
point, even when central totals are identical. Calendar days without a run may
carry within a known segment only; an explicit central-only run clears ranges
and chances. Lines, carry-forward and five-update band smoothing stop at context
boundaries and gaps. Tooltip bounds remain unsmoothed. An ordinary Westminster
array remains readable without this envelope.

## Reader dispatch before the first US write

House storage dispatch now recognizes the US schema before invoking any legacy
reader. The following boundaries also describe the remaining model and public
activation work:

| Reader/writer | Required dispatch |
|-|-|
| `model_support/persistence.py` `_encode_payload`, `replace_output` | Carry an explicit payload schema, validate by schema/model, store that schema rather than always writing the Westminster constant. Preserve atomic scoped replacement and detail retention. |
| Same module `_check_schema`, `load_uncertainty`, `iter_national_blocks`, `uncertainty_status` | Recognize the two stored versions, expose the discriminator to callers, reject unknown versions; never pass a US block to a Westminster validator. |
| `model_support/trends.py` `_trend_uncertainty`, `_unpolled`, `reconstruct_trends` | Dispatch before any Westminster budget/odds interpretation. Before US trend activation, skip US optional additions and retain byte-identical legacy output. Then emit the new US envelope explicitly, with context boundaries. |
| `model_support/public_uncertainty.py` and `scripts/export/uncertainty.py` | Keep Westminster functions scoped; add US model dispatch, strict public whitelist and context-aware identity matching. Export each sidecar independently and remove its stale sidecar after a newer central-only result. |
| `models/us/_common.py` persistence entry | Pass validated model/context explicitly; never infer model from optional metric keys. |
| `console/services/uncertainty*.py`, blueprints and replay loaders | Resolve allowlisted model and saved scope before selecting the validator/replay adapter; reject wrong-model ids and explain pruned detail. |
| Frontend `uncertainty*.js` and `features/polltracker*.js` | Decode declared schema/model/metric domain; unsupported or mismatched optional documents hide uncertainty while central data remains usable. |

The summary-validator tests leave legacy Westminster validators unchanged and
exercise their existing format directly. Full old/new storage and stale-cache
integration tests are mandatory when dispatch is enabled.

## House private replay and retained race detail

The stored national document's `private.schema` is `pf-us-source-replay-v1`.
It contains the complete frozen source graph, polling evidence and candidate
readings, baseline shares/turnout, ordered party and race labels, central rows,
configuration, release decision, central options, seed and budget. The
`artifact_id` hashes context/result identity, summary and all private values
except that hash itself. Every date retains this snapshot after race detail is
pruned. Replay never resolves today's configuration, map or matchup.

The retained seat block is `pf-us-race-summary-v1`, with explicit model, context,
result identity, simulation count and interval. Its `seats` mapping contains
party win probabilities, central share, three-value share ranges and 101-bin
share histograms. Histogram index `i` counts `[i,i+1)` percent for 0–99; index
100 counts exactly 100%. Quantiles use temporary float32 share samples and
float64 quantile calculations, rounded to two decimals. These compact bands and
histograms are stored even with `--no-raw-sims`. Deterministic central rows and
raw replay retain their existing exact integer counts and float64 errors.

The raw archive is `pf-us-source-draws-v1` at `us-house-latest.npz` beside
`SIMULATIONS_PATH`. All bulk arrays are staged before SQLite replacement;
after commit a small election-ID member binds the archive before atomic
publication. Reads require matching election ID, model, map, context/date,
kernel and artifact fingerprint. Publication failures preserve the old file,
which is rejected for a changed saved run. Older reruns cannot replace newer
archives or metadata. Detail pruning is isolated by model/map/name-prefix.

Per-race summary sample allocation is capped at 256 MiB, retained numerical
arrays at 512 MiB, and staged raw arrays at 1 GB. These checks precede their
allocations and persistence. Lower the simulation count when a summary bound
is reached, or use `--no-raw-sims` for the raw-only bound. Dry runs validate
inputs but draw no simulations and write no files or DDL.

`house-release.json` must explicitly enable release, attest target geography,
name the baseline/map/cycle and reference accepted, labelled calibration.
Committed production settings remain disabled while geography and calibration
are unresolved. An explicit `--simulations` request reports the blockers;
ordinary runs preserve central-only operation while gated, and
`--no-uncertainty` always selects the central estimator.

House targets roll forward on 1 January of each odd year: dates in 2025–2026
use the 2026 field, and dates in 2027–2028 use the 2028 field. A release approved
for one target cannot be reused for another; each requires approved geography
and calibration.

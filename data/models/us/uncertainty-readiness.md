# US uncertainty readiness audit

Read-only snapshot, 22 October 2026, repository baseline `35e527a1`.
The [machine-readable inventory](uncertainty-readiness-2026-10-22.json) records
SQL queries, counts, missing-base poll IDs, dates and the Senate roster hash.
SQLite was opened with `mode=ro`, `PRAGMA query_only=ON` and a read transaction.
No model, migration, repair or export was run. The inventory is evidence for
implementation decisions, not a permanent assertion about the live database.

## Evidence inventory

| Scope | Stored polls | Missing/non-positive sample size | Fieldwork end dates |
|-|-|-|-|
| House national aggregate snapshots | 47 | 47, expected for aggregates | 2026-06-29–2026-10-18 |
| House district surveys | 265 | 11 | 2025-06-10–2026-10-19 |
| Senate race surveys | 495 | 3 | 2023-04-25–2026-10-19 |
| Presidential national surveys, all matchups | 50 | 0 | 2024-12-20–2026-08-28 |
| Presidential state surveys, all matchups | 1 | 0 | 2026-06-24 |

The selected presidential matchup is `Vance (R) vs Newsom (D)`: 17 national
surveys and the single state survey match it. The 50 national surveys are not
50 independent observations of that matchup. House has 108 stored automatic
race-matchup selections; Senate has 29. Each of the 760 House/Senate seat polls
has a matchup label, but that does not prove it is the selected usable matchup.

All stored US polls have a nonempty source URL. Some URLs point to a pollster's
Wikipedia article rather than a survey report or the race's polling table.
Examples include polls 1159, 1677, 1870, 1965 and 2019. Re-fetching must resolve
race/table provenance, not assume every `source_url` contains the original
sample-size or population cell. The importer may also have discarded LV/RV
variants. Neither population labels nor explicit evidence kinds currently have
persisted columns, and there are no US `poll_bases` rows.

The 47 generic snapshots are from Decision Desk HQ (9), FiftyPlusOne (9),
RealClearPolitics (9), Silver Bulletin (9), VoteHub (3), Race to the WH (3) and
NBC News (5). They describe overlapping national evidence over time. The
existing central aggregation remains unchanged; these counts must never become
an effective respondent count or independent sampling precision.

## Actionable evidence repairs

The missing-N inventory contains these individual-poll IDs:

- House: 1117 (AL-02), 1126 (CA-11), 1127 (CA-14), 1139 (FL-08), 1141 (FL-11),
  1149 (FL-22), 1159 (IA-02), 1230 (OH-07), 1676 (SD-01), 1677 (TX-09),
  1894 (FL-09).
- Senate: 1870 (Iowa), 1965 (Ohio), 2019 (Texas).

At the last saved House/Senate date, 2026-10-19, five House rows
(1117, 1149, 1676, 1677, 1894) and all three Senate rows both fall in the
60-day window and match the stored race selection. This SQL screen does **not**
apply material-candidate completeness, all party-level admission rules or weights.
Only the model's actual contributor list can identify which must block a run.
Earlier dates can use different members of the 14-row inventory.

The evidence stage must re-fetch/inspect these records and the population
labels, retaining source identity and a fingerprint. A later write must
revalidate under the prescribed transaction guard and skip deleted, edited or
reused IDs. Unresolved N on a contributing individual survey stops uncertainty;
aggregate N is not applicable. Do not impute N=1000 or turn every null-N row into
an aggregate. The decision about retaining LV/RV variants remains with that
stage; this audit neither changes the selection rule nor restores discarded rows.

## Senate composition and control

The current file has 50 states and 100 members: 53 Republican, 45 Democratic
and 2 Independent. Class counts are 33 Class 1, 33 Class 2 and 34 Class 3.
The 2026 field is the 33 Class-2 members plus Florida and Ohio Class-3 specials:
35 contested seats and 65 holdovers. Holdovers are 31 Republican, 32 Democratic
and 2 Independent. The independent holdovers are Angus King (Maine) and Bernie
Sanders (Vermont), as named in the roster.

The source manifest names 2022 baselines for both specials. It has no explicit
caucus or VP policy. Current code resolves the field at import time using the
current date and mutable roster. A pinned per-cycle field, roster, special
baselines, candidate-specific alignment and VP policy are required before
reproducible full-chamber uncertainty. The proposed King/Sanders Democratic
alignment, Osborn unaligned and Republican VP bloc are still explicit policy
assumptions to confirm in the Senate-field stage; they are not inferred from
party names by this audit.

The map represents one Senate race per state, and this work cannot silently
expand it to simultaneous regular/special races in the same state. Such a cycle
must fail with a clear unsupported-field message until separately supported.

## Electoral votes and historical results

All seven stored census eras (1960 through 2020) contain 56 allocation units
and total 538 EVs. The 2020 era covers 2024–2028. All 603 stored presidential
model runs have target year 2028. This verifies totals and era availability;
the allocation loader's detailed identity/weight checks remain authoritative.
Maine/Nebraska district votes must be excluded from the national popular tally,
while district EVs remain counted.

Available result sets:

| Model | Election results | What they support |
|-|-|-|
| House | 2022, 2024; 435 districts each | One candidate transition, subject to boundary matching and uncontested/same-party exclusions |
| Senate | 2020 (33), 2022 (34), 2024 (33) | Cross-sectional results; not same-class six-year transitions |
| President | 16 elections, 1964–2024; 56 units each | State-result transitions after geography/allocation validation; not historical polling errors |

There is only one stored US survey ending before the 2024 general election.
The database therefore cannot supply a historical final-poll calibration series
or replay the full deployed polling blend across earlier elections. External
final-average archives are necessary, with the transfer limitations described
in [the source proposal](uncertainty-calibration-sources.md).

The House 2024 baseline has 15 districts with only one party receiving positive
stored votes: CA-12, CA-16, CA-20, CA-34, FL-20, KY-05, LA-04, MS-03, OK-03,
PA-03, TX-01, TX-09, TX-11, TX-13 and TX-20. This means **single-party baseline**,
not necessarily an uncontested candidate election: same-party top-two candidates
can collapse to one party. Diagnostics should not call every such seat
uncontested without candidate evidence. The separately approved phantom-vote
fix must precede zero-error parity; this piece makes no projection changes.

## Backfill scope and release gates

| Model | Stored daily history | Recommended initial reconstruction |
|-|-|-|
| House | 113 runs, 2026-06-29–2026-10-19 | 2026-06-29 onward, current 2026 context, after evidence repairs |
| Senate | 113 runs, 2026-06-29–2026-10-19 | Same dates after field, roster and policy are pinned |
| President | 603 runs, 2024-12-23–2026-08-17 | Existing supported 2028-target history after selected-matchup evidence audit; cap at usable poll evidence |

These are supported stored intervals, not a claim every date already has enough
resolved evidence. Later runs may extend them. Reconstructed history uses the
chosen assumptions; it is not an archive of previously published probabilities.
There are no stored US uncertainty summaries yet.

Before the first US uncertainty write, schema dispatch must cover persistence,
trend reconstruction, public serialization, export and console loaders. Before
public enablement, the calibration report must distinguish fitted versus assumed
components and receive explicit acceptance. That acceptance may include clearly
labelled assumptions; it is not a claim that all coefficients are identifiable.

Review items for the next stages:

1. Approve the contract and curated source choices.
2. Recover evidence labels and repair selected missing-N contributors safely.
3. Accept fitted error sizes and explicit remaining assumptions.
4. Pin Senate context and fix phantom votes in their separate stages/commits.
5. Verify zero-error parity, exact replay, latest-run selection and per-model raw
   paths before any operational backfill.

## Reproducing the inventory

The JSON includes each SQL query under `queries`. Resolve the configured path
through `DatabaseConfig` without printing environment contents, open its
`Path.as_uri() + '?mode=ro'` with `sqlite3.connect(..., uri=True)`, set
`PRAGMA query_only=ON`, and run the queries inside one `BEGIN` read transaction.
Rollback/close afterward. The stored IDs 21/22/23 were verified against `maps`;
re-resolve them if the database is rebuilt. Census totals come from the database;
roster counts come from the hashed `senate-current.json` and source manifest.
Do not use `Database` construction or a model runner to perform this inventory.

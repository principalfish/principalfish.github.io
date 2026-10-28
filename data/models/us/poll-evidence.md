# US poll evidence provenance

US polls retain three nullable fields: `evidence_kind`, `population_type` and
`evidence_source`. New imports label accepted generic-ballot aggregate rows as
`aggregate_estimate`, other survey rows as `individual_poll`, and preserve the
source population label (`LV`, `RV` or `A`) where reported. Existing rows remain
unknown until verified. A missing sample size does not establish the evidence
kind.

The importer continues selecting the first row for a pollster/fieldwork period
within a table, including when later rows are LV/RV variants. Recording its
population does not change that selection or the central model's weights.
Existing deduplicated polls are not silently rewritten by an ordinary import.

The console poll list/detail and US review queue display evidence and population.
A survey's displayed MoE is explicitly a 90% simple-random-sample approximation
at a 50% share. It excludes design effects and systematic/model error. Aggregate
sample size and MoE are not applicable; unresolved survey evidence is labelled
rather than assigned a sample size.

## Existing database upgrade

Back up the configured SQLite database before changing it. From `data/`:

```bash
./election_data/bin/python scripts/migrate_add_us_poll_evidence.py --dry-run
./election_data/bin/python scripts/migrate_add_us_poll_evidence.py
```

The migration adds only the three nullable columns and is idempotent. It must
precede using the updated ORM against an older database. It does not populate
metadata or recalculate model outputs. The repository `.env` controls its database
path; shell `DATABASE_PATH` alone does not override `.env`.

## Re-fetch and review existing evidence

```bash
./election_data/bin/python scripts/backfill_us_poll_evidence.py --database /path/to/elections.db
```

The default is a dry run. It re-fetches supported US source pages and matches
stored identity, dates, scope, matchup and every candidate share. ORM lookups
use an isolated SQLite backup, while the source is read-only. Unmatched or
ambiguous polls remain unresolved. Failed pages are reported. Source pages may
have changed since an earlier import, so inspect the report rather than
assuming every old poll can be repaired automatically.

After reviewing the plan and backing up the intended database, add `--apply` to
write verified metadata and reported sample sizes. Under `BEGIN IMMEDIATE`, the
writer rechecks the complete poll head and share-row fingerprint and skips any
stale, deleted or changed record. It does not change shares, matchups or poll
selection. Review skipped and unresolved polls separately; never fill them with
an invented respondent count.

The uncertainty adapter can collect the deterministic model's actual admitted
contributors and call `validate_contributor` on those records. Unknown evidence
or an individual contributor without a usable N requires repair; aggregate
error has no synthetic sampling N. This validation is not activated for existing
deterministic runs, so recording provenance alone does not change projections.

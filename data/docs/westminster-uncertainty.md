# Westminster uncertainty validation

Run the offline acceptance harness from `data/`:

```bash
./election_data/bin/python models/westminster/validate_westminster_uncertainty.py
```

This reads the configured SQLite database with `mode=ro` and `query_only`,
uses the committed final-poll evidence, current uncertainty config and frozen
22 October 2026 aggregate model inputs, and writes only `models/westminster/uncertainty_validation_report.json`. It does
not rerun or persist a forecast. The console's Westminster Uncertainty →
Validation page displays this saved report and flags a changed config.

For an explicit input set and scratch output:

```bash
./election_data/bin/python models/westminster/validate_westminster_uncertainty.py \
  --database /path/to/elections.db \
  --replay /path/to/westminster-latest.npz \
  --output /tmp/westminster-validation.json
```

`--inputs` selects an alternate frozen aggregate-input fixture; `--replay` instead
uses an explicitly selected, supported raw-draw archive. The default fixture
contains shares, turnout, IDs and names, with the original archive SHA, config,
seed and input fingerprint. It contains no individual polls or simulated draws.
Its schema, content hash, source config hash and original input fingerprint
are checked before simulation, so raw-draw format upgrades do not lose the
baseline comparison.

`--config` and `--final-polls` select alternate local evidence. Default draw
counts are 4,000 for the historical checks and 10,000 for each runtime check;
`--simulations` and `--replay-simulations` override them. `--skip-replay`
explicitly omits runtime diagnostics. Missing
required inputs fail without replacing the report. Changing environment
variables is unnecessary: `--database` names the exact read-only input.

The report records config, historical-input, replay-input and source hashes,
fixed seeds, draw counts and NumPy version. Repeating the command with the
same inputs and source produces the same report. Runtime diagnostics identify
the original date, original archive and both saved and evaluated configs and
kernels; they evaluate current settings on frozen inputs even if polling has
moved on.

Historical seat-total and favourite-reliability checks condition on **actual
regional votes**. They reproduce the Gaussian calibration experiment that fits
the seat and lambda scales
(`data/models/westminster/calibrate_uncertainty.py`), with
leave-one-pair-out fitted scales; they are not an end-to-end forecast
backtest. Only four transitions and nine national final-poll elections are
available. Any new conversion-error component must also be added to this
conditional experiment before its historical coverage can validate that
component. Runtime bias and Monte Carlo precision use the real model kernel.

The initial report covers 12/20 seat-total cells at 90%, loses 34/396 (8.59%)
of 2024 ≥95% favourites under held-out scales and 28/371 (7.55%) under current
scales, and covers 30/35 (85.7%) national cells with lead z RMS 1.177. Current
config reliability uses the deployed scales, rounded to 2 dp (σ_seat 4.90,
σ_λ 0.38); the fitted scales are 4.9029 and 0.3818 (4 dp:
`swing.pooled.sigma_seat_raw` and `swing.pooled.sigma_lambda` in
`data/models/westminster/uncertainty_calibration_report.json`).

## Archived upgrade scripts and the evidence builder

`SIMULATIONS_PATH` (see `.env_example`; default
`/home/philiph/dbs/simulations/westminster-latest.npz`) is where each
Westminster model run saves its raw Monte Carlo draws, unless run with
`--no-raw-sims`. The console's Explore simulations pages read that file. It is
reproducible and not part of the database backups.

An existing database once needed five one-off scripts, run in this order. They now
live in `archive/`, are not maintained, and **may no longer run**; a fresh setup uses
`scripts/rebuild_database.py` instead, which builds the current schema and data.
The record, for reference:

1. `archive/migrate_add_poll_bases.py` created `poll_bases`.
2. `archive/migrate_add_model_uncertainty.py` created `model_uncertainty`.
3. `archive/repair_poll_records.py` fixed wrong sample sizes and dates.
4. `archive/repair_regional_poll_rows.py` fixed wrong regional shares.
5. `archive/backfill_poll_bases.py` filled `poll_bases` from the sources.

Steps 3 to 5 re-fetched poll sources, and step 5's matching relied on the values steps 3
and 4 corrected, which fixed the order.

`models/westminster/build_final_poll_errors.py PATH/TO/PollBase.xlsx` is separate and
needs no database: it rebuilds
`data/models/westminster/uk_final_poll_errors.json` from a new PollBase edition, and
`models/westminster/calibrate_uncertainty.py` then reads that table.

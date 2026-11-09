# Reorganise data/ by pipeline role

`data/` has outgrown its layout. `old_data/` holds live importers and source files that current code still reads and writes. Results importing is spread across four places, and `scripts/` mixes regular tools with finished one-offs. The `models.py` file also shadows the `models/` folder, which is a large part of why 95 files adjust the Python path by hand.

This reorganises `data/` by pipeline role, then by jurisdiction: shared core code, input files, importers (seed, results and polls), models, export, the console, and maintenance scripts. `old_data/` goes away, and finished one-off scripts move to a single archive folder outside the normal checks. Normal imports replace the path adjustments.

Behaviour doesn't change. Each step has to pass the type checks and tests, and a full rebuild of a scratch copy of the database must produce the same data and byte-identical site files as before. The one regenerated file is the Westminster validation report, whose numbers must stay identical apart from its source fingerprints and paths.

The console's pages don't change here, only its imports. Its redesign is a separate issue that follows this one.

## Plan

_2026-11-09 · 10 of 10 pieces done · now: PR #84 ready_

- [x] **1/10 Editable install, archive folder, dead files** · `4b0f4475`
  Make `data/` installable so scripts can import normally, and create the archive folder.
- [x] **2/10 `schema.py` and `models/` package** · `0539b23f`
  Rename `models.py` so the `models/` folder can be imported normally.
- [x] **3/10 `inputs/`** · `9a3a0689`
  Move the source data files out of `old_data/files`.
- [x] **4/10 UK and poll importers** · `2e2111c0`
  Bring UK results, seed and poll importers together under `importers/`.
- [x] **5/10 US importers** · `23263912`
  Move the US importers, and archive the 538 and Wikipedia converters.
- [x] **6/10 `export/`** · `0190c24e`
  Give the export code its own package.
- [x] **7/10 `scripts/` cleared, `old_data/` removed** · `9f7c03c5`
  Archive finished migrations and repairs, leaving only regular tools.
- [x] **8/10 `models/support/`** · `b48be9c0`
  Move the model library, and regenerate the validation report with go-ahead.
- [x] **9/10 `core/`** · `71bb7672`
  Move the shared root modules, with a test pinning `.env` loading.
- [x] **10/10 Path hacks gone, docs updated** · `413f28eb`
  Remove the remaining path adjustments and update the README and docs.
- [x] **Review fixes (round 1)** · `4acadf28`
- [x] **Review fixes (round 2)** · `c34e8c76`

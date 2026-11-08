# Reorganise data/ by pipeline role

`data/` has outgrown its layout. `old_data/` holds live importers and source files that current code still reads and writes. Results importing is spread across four places, and `scripts/` mixes regular tools with finished one-offs. The `models.py` file also shadows the `models/` folder, which is a large part of why 95 files adjust the Python path by hand.

This reorganises `data/` by pipeline role, then by jurisdiction: shared core code, input files, importers (seed, results and polls), models, export, the console, and maintenance scripts. `old_data/` goes away, and finished one-off scripts move to a single archive folder outside the normal checks. Normal imports replace the path adjustments.

Behaviour doesn't change. Each step has to pass the type checks and tests, and a full rebuild of a scratch copy of the database must produce the same data and byte-identical site files as before. The one regenerated file is the Westminster validation report, whose numbers must stay identical apart from its source fingerprints and paths.

The console's pages don't change here, only its imports. Its redesign is a separate issue that follows this one.

## Plan

_2026-11-08 · 0 of 10 pieces done · now: piece 1 in progress_

- [ ] **1/10 Editable install, archive folder, dead files** · in progress
  Make `data/` installable so scripts can import normally, and create the archive folder.
- [ ] **2/10 `schema.py` and `models/` package** · not started
  Rename `models.py` so the `models/` folder can be imported normally.
- [ ] **3/10 `inputs/`** · not started
  Move the source data files out of `old_data/files`.
- [ ] **4/10 UK and poll importers** · not started
  Bring UK results, seed and poll importers together under `importers/`.
- [ ] **5/10 US importers** · not started
  Move the US importers, and archive the 538 and Wikipedia converters.
- [ ] **6/10 `export/`** · not started
  Give the export code its own package.
- [ ] **7/10 `scripts/` cleared, `old_data/` removed** · not started
  Archive finished migrations and repairs, leaving only regular tools.
- [ ] **8/10 `models/support/`** · not started
  Move the model library, and regenerate the validation report with go-ahead.
- [ ] **9/10 `core/`** · not started
  Move the shared root modules, with a test pinning `.env` loading.
- [ ] **10/10 Path hacks gone, docs updated** · not started
  Remove the remaining path adjustments and update the README and docs.

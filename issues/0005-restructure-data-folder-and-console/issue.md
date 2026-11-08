# Reorganise data/ by pipeline role

`data/` has outgrown its layout. `old_data/` holds live importers and source files that current code still reads and writes. Results importing is spread across four places, and `scripts/` mixes regular tools with finished one-offs. The `models.py` file also shadows the `models/` folder, which is a large part of why 95 files adjust the Python path by hand.

This reorganises `data/` by pipeline role, then by jurisdiction: shared core code, input files, importers (seed, results and polls), models, export, the console, and maintenance scripts. `old_data/` goes away, and finished one-off scripts move to a single archive folder outside the normal checks. Normal imports replace the path adjustments.

Behaviour doesn't change. Each step has to pass the type checks and tests, and a full rebuild of a scratch copy of the database must produce the same data and byte-identical site files as before. The one regenerated file is the Westminster validation report, whose numbers must stay identical apart from its source fingerprints and paths.

The console's pages don't change here, only its imports. Its redesign is a separate issue that follows this one.

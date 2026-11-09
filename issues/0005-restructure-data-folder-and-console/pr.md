# chore: reorganise data/ by pipeline role

**Run this straight after merging, once, in the main checkout:**

```bash
cd data && ./election_data/bin/pip install --no-deps -e . --config-settings editable_mode=compat
```

`data/` is now an installable package and the main venv doesn't have it yet. Until this runs, any script run by path fails with `ModuleNotFoundError: core`. That includes the hourly restic job's `data/scripts/backup_db.py` step, which keeps backing up a stale copy of the database, and the scripts the console launches. The command adds one path file to the venv. It copies no code and doesn't touch the database. It's documented in `README.md` and `data/README.md`.

## What changed

`data/` had outgrown its layout. `old_data/` held live importers and source files that current code still read and wrote, results importing was spread across four places, and `scripts/` mixed regular tools with finished one-offs. `models.py` also shadowed the `models/` folder, which was a large part of why 95 files adjusted the Python path by hand.

`data/` is now organised by pipeline role, then by jurisdiction:

| Path | What it holds |
|-|-|
| `core/` | Shared modules: `schema.py` (was `models.py`), `db.py`, `config.py`, `backup.py`, `electoral_votes.py`, US geography, migration helpers |
| `inputs/{westminster,holyrood,us}/` | Source data files (was `old_data/files/`) |
| `importers/` | `seed/`, `results/` (with US `nyt/` and `geometry/`), `polls/` |
| `models/` | `support/` (was `model_support/`), `westminster/`, `holyrood/`, `us/` |
| `export/` | The static export |
| `console/` | Unchanged pages, updated imports |
| `scripts/` | Only `rebuild_database`, `rebuild_model_trends` and `backup_db` |
| `archive/` | Finished one-offs and retired converters, outside mypy and pytest, documented in its README |

`old_data/` is gone. Every `sys.path` adjustment outside `archive/` is gone too, replaced by normal imports, and tests now fail if one returns or if a bare import of a moved module comes back. Commands keep their shape: scripts still run by path.

The console's pages don't change here, only its imports. Its redesign is #83, which follows this.

## Behaviour

Behaviour is meant to be unchanged. After every piece, strict mypy and the tests passed, and a scratch restore of the newest backup was rebuilt from a copy of the branch: 43 of 43 steps, unchanged table counts, identical per-election vote counts and totals, and byte-identical `electionmaps/data` and `uselectionmaps/data`. The console's URL map and the export dry-run match the baselines captured from master. The live database wasn't touched by any of this.

At `c34e8c76`, mypy is clean and 6,387 Python tests pass. The last JavaScript run was 1,656 passing at `413f28eb`, and nothing front-end changed after that. The rebuild proof last ran at `413f28eb`; the two commits since change only docstrings, one test file and the validation report.

The deliberate differences:

- `uncertainty_validation_report.json` is regenerated, as agreed. Its numbers are identical; only source paths, fingerprints, numpy version and the report's own hash changed.
- 363 test IDs from the baseline are gone. 349 covered scripts now in `archive/` and are retired with them, and 14 moved to new test files.
- `build_presidential_topojson.py` and `build_senate_topojson.py` no longer crash on `--help`, from an unescaped `%` in their help text.
- Messages that named a now-archived migration say the table is missing, to rebuild the database, and name the retired script, because the archived scripts that share helpers no longer run.
- The docs that listed archived scripts as steps now read as history.

## Worth checking by hand

- After the install above, run `data/scripts/backup_db.py` by path, or watch the next hourly restic run.
- The rebuild proof doesn't cover poll importers or model runs. Tests, `--help` and `--dry-run` runs and import checks cover those. The console's own Rebuild and Restore buttons were also not exercised; their routes have tests with the work mocked.
- Archived scripts are a record and aren't maintained. Some no longer run, and `archive/README.md` says which depend on what.

## Self review

One self review, with correctness and security on Opus, and coverage, style and common-mistakes on Sonnet, each judged by a fresh verifier. Coverage of changed lines was 97% (372 of 383); the 11 missed lines are scripts run by path with `--help`, which coverage tracing doesn't follow.

- The first pass found the install step above, stale hashes in the validation report, and docstrings still naming the old top-level modules. The last two are fixed in `4acadf28`, with a test for the docstrings.
- A re-check of that commit found gaps in the new test's scan, fixed in `c34e8c76`.
- A re-check of that commit raised two small points, both left as they are. The `~` pattern in that scan has no left anchor, so real code like `~config.flag` would fail the test loudly; none exists. A docstring there says "class" where constants match too.

Helpers now used only by archived scripts (`ParsedPollWithBases`, `HasRegionalShares`, YouGov's `regional_shares()`) are left for a later cleanup.

Closes #82

# Analysis: consolidate

_Commits reviewed: 1 | Issue: .agents/issues/consolidate.md | 2026-03-21 18:13_

Now I have everything I need. Here is the full review.

---

## Engineering Review: commit `33a51dea` (consolidate)

---

### 1. Issue Coverage

The issue file (`.agents/issues/consolidate.md`) is a descriptive inventory of the `data/` folder, not a spec with acceptance criteria. It describes what the directory structure should look like post-consolidation. Mapping the diff against it:

| Requirement | Status |
|---|---|
| `old_data/import_all.sh` entrypoint | Delivered |
| Scripts moved to `old_data/scripts/` | Delivered |
| `build_wikipedia_poll_mappings.py` + `sync_pollsters_from_mapping.py` → `polls/importers/refresh_poll_mappings.py` | Delivered |
| `PollImportResult` moved to `polls/importers/types.py` | Delivered |
| `by_election_import.py` moved to `scripts/` | Delivered |
| `export_manifest_metadata.py` merged into `export_non_simulation_elections.py --metadata-only` | Delivered |
| `run_retrospective_uns.py` merged into `run_uns_model.py` | Delivered |
| `update_polls.sh` moved to `polls/update_polls.sh` | Delivered in filesystem; **reference in `server.py` not updated** — see §3 |
| `migrate_to_supabase.sh` moved to `old_data/` | Delivered |

The inventory does not mention `run_export_targets.py`, `migrate_results_to_v4.py`, `normalize_uns_trend_dates.py`, `split_ukip_reform_parties.py`, `add_poll_region_columns.py`, or `set_pollster_regions_mapping.py`. All are deleted, which is consistent with the description (they don't appear in the target state). No omissions.

---

### 2. Handler Path Audit

**`server.py`** — three meaningful changes:

**`from scripts import by_election_import`** (line 37):  
`data/scripts/` has no `__init__.py`. Python 3.3+ namespace packages allow this to work at runtime — but mypy has no `[mypy-scripts.by_election_import]` ignore entry, so mypy strict will flag it. There is also no `__init__.py` making the package boundary explicit, which is inconsistent with `polls/importers/` (which has one).

**`poll_detail_csv` inlining** (line 1394+):  
The inlined query uses `pr, party, region` correctly. One minor issue: the query is built inside the `with db.session()` block but the `rows` list comprehension runs `session.execute(query).all()` — correct, session is still open. No functional regression.

**`import_poll_confirm` UNS trigger** (line 1281):  
```python
if result.created_poll or result.inserted_rows or result.replaced_rows:
    subprocess.run([sys.executable, str(UNS_MODEL_SCRIPT)], check=True)
flash("UNS model updated.")
```
`flash("UNS model updated.")` fires even when the inner condition is False and the model wasn't run. This preserves the pre-existing behaviour of `maybe_run_uns_model` (the old caller also flashed unconditionally). Not a regression, but the flash message is misleading when nothing ran.

**`UPDATE_POLLS_SCRIPT` path** (line 99):
```python
UPDATE_POLLS_SCRIPT = DATA_DIR / "update_polls.sh"
```
`update_polls.sh` was moved to `data/polls/update_polls.sh`. The constant still points to the old location. `POST /update-polls` will always hit the `not UPDATE_POLLS_SCRIPT.exists()` branch and flash "Update script not found." **Critical — the poll update flow is broken.**

**`SQLITE_ARCHIVE_PATH` antipattern** (line 96): Still uses `__import__("os")` inline. Not changed in this commit; carried forward from prior commit.

---

### 3. Regression Check

**[CRITICAL] `UPDATE_POLLS_SCRIPT` stale path**  
`data/update_polls.sh` → `data/polls/update_polls.sh`. `server.py:99` still references `DATA_DIR / "update_polls.sh"`. The file does not exist at that path. `POST /update-polls` is permanently broken until this is corrected to `DATA_DIR / "polls" / "update_polls.sh"`.

**`.agents/23-data-uns.md` references deleted script**  
The area doc still says:
> `run_retrospective_uns.py`: range runner; resets existing outputs by default unless `--no-reset-existing`.

The script is deleted. Any developer reading this will look for a non-existent file. The correct invocation is now `run_uns_model.py --start-date ... --end-date ...`.

**`.agents/learnings.md` has three stale entries for `run_retrospective_uns.py`**  
Lines covering its `--no-reset-existing` default, the 4-value unpack bug, and the lexicographic name range behaviour are now misleading. The behaviour is preserved inside `run_uns_model.py` but the script name is wrong.

**`mypy.ini`: `[mypy-export_non_simulation_elections]` removed, `mypy_path = scripts` removed**  
`export_non_simulation_elections` is still a first-class script that is imported by `server.py` indirectly (via subprocess, not import — safe). But `by_election_import` is now imported directly (`from scripts import by_election_import`) and has no mypy ignore entry. The `mypy_path = scripts` removal means `by_election_import.py`'s local imports (`from db import Database`) may fail mypy analysis under `explicit_package_bases = True`.

**`run_step` path for `refresh_poll_mappings.py`**  
`update_mapping_and_import_new.py` calls:
```python
run_step([python_bin, "polls/importers/refresh_poll_mappings.py", "--apply"], ...)
```
This is a relative path from `data/`. `update_polls.sh` does `cd "$ROOT_DIR"` (= `data/`) before invoking the orchestrator. Path resolves correctly. ✓

**`from scripts import by_election_import` at runtime**  
Python 3.3+ namespace packages allow import from a directory without `__init__.py`. Runtime import will succeed. Not a runtime regression.

**Deleted `last_unimportable_urls.csv`, `pollster_parser_profiles.json`, `strict_fieldwork_url_year_mismatches.csv`, `poll_1_rows.csv`**  
These were generated artifacts committed to the repo. Nothing reads them at runtime. Cleanup is correct.

---

### 4. Test Coverage Gaps

No new tests accompany this commit. Changed/new code without coverage:

| Component | Tests |
|---|---|
| `import_all.sh` | None (shell script) |
| `refresh_poll_mappings.py`: `dedupe_by_identifier()`, `sync_pollsters()` | None |
| `run_uns_model.py`: `run_retrospective()`, `reset_existing_model_outputs()`, `_build_config_from_args()` | None |
| `export_non_simulation_elections.py`: `--metadata-only` branch | None |
| `server.py`: inlined `poll_detail_csv` query | None |

`conftest.py` is fixed (`DatabaseConfig.local()` instead of `from_env()`). This is the correct fix from the prior review. Tests that previously could silently run against Supabase are now isolated. ✓

---

### 5. Coding Standards Compliance

**`import_all.sh` — step label numbering error**  
Steps are labelled `1/3`, `2/3`, `3/3`, `4/4`. Should be `1/4`, `2/4`, `3/4`, `4/4`. The count changes mid-script (line 15 vs line 24).

**`_build_config_from_args()` — incomplete docstring**  
`run_uns_model.py:235`:
```python
def _build_config_from_args(args: argparse.Namespace) -> SimulationConfig:
    """Construct a SimulationConfig from parsed single-date CLI arguments."""
```
One-liner with no `Args:` or `Returns:` section. The function is non-trivial — it parses dates, applies defaults, and validates ordering. Google-style requires `Args` and `Returns` for functions with parameters/return values.

**`update_mapping_and_import_new.py` — double blank line**  
After removing `write_unimportable_report` and its trailing print, a stray double blank line is left between the end of the removed block and `classify_unimportable_url`. Minor.

**`mypy.ini` — missing ignore entry for `scripts.by_election_import`**  
`by_election_import` is directly imported by `server.py` as a namespace package module. No `[mypy-scripts.by_election_import]` or `[mypy-scripts.*]` ignore entry exists. Mypy strict may fail to resolve the `from db import Database` import inside it when analysed as `scripts.by_election_import` under `explicit_package_bases = True`.

**`data/scripts/` — no `__init__.py`**  
`data/polls/importers/` has `__init__.py` making it an explicit regular package. `data/scripts/` has none, making it an implicit namespace package. This inconsistency is confusing and will cause mypy to treat the two directories differently.

**`.agents/23-data-uns.md` — stale content**  
References `run_retrospective_uns.py` as a separate key script. Should be updated to document `run_uns_model.py --start-date/--end-date` instead.

**`.agents/learnings.md` — three stale entries**  
Entries on lines covering `run_retrospective_uns.py` behaviour should either be removed or rewritten to reference `run_uns_model.py`'s `run_retrospective()` function.

---

### 6. Security and Data Integrity

No new user-supplied values are interpolated into SQL without parameterisation. The SQLite LIMIT f-string (`f" LIMIT {int(limit)}"`) is carried forward from the prior commit and remains an antipattern, not a live vulnerability.

`refresh_poll_mappings.py` makes a network request to Wikipedia in `fetch_html()` — unchanged from `build_wikipedia_poll_mappings.py`. The URL is a constant, not user-supplied.

`import_all.sh` uses `set -euo pipefail` and constructs paths from `BASH_SOURCE[0]`, not from user input. Safe.

No new shell-exec calls with unsanitised input.

---

### 7. Commit Hygiene

**Commit message `"consolidate"`** — inadequate. The commit:
- Moves 4 scripts between directories
- Merges 3 separate scripts (`build_wikipedia_poll_mappings.py`, `sync_pollsters_from_mapping.py`, `run_retrospective_uns.py`) into existing files
- Deletes 9 scripts
- Adds `import_all.sh`
- Fixes `conftest.py` test isolation bug
- Updates `mypy.ini`, `.gitignore`, agent docs
- Removes generated artifacts (`.coverage`, CSV/JSON mapping files)

The message conveys none of this. At minimum it should indicate what was consolidated and what was fixed.

The commit mixes structural reorganisation with the `conftest.py` bug fix (which is a correctness change, not consolidation). These would ideally be separate commits.

No commented-out code left behind. No debug print statements introduced.

---

### 8. Overall Verdict

**Critical (must fix before relying on these behaviours):**

1. **`UPDATE_POLLS_SCRIPT` path is broken.** `server.py:99` still points to `DATA_DIR / "update_polls.sh"` which no longer exists. The file was moved to `data/polls/update_polls.sh`. `POST /update-polls` always fails. Fix: `DATA_DIR / "polls" / "update_polls.sh"`.

**Minor (should fix):**

2. `import_all.sh` step labels are `1/3`, `2/3`, `3/3`, `4/4` — should be `1/4` through `4/4`.
3. `.agents/23-data-uns.md` still documents `run_retrospective_uns.py` as a live script. Update to document `run_uns_model.py --start-date/--end-date`.
4. Three entries in `.agents/learnings.md` reference `run_retrospective_uns.py` by name and are now stale.
5. `_build_config_from_args()` docstring is a one-liner; needs `Args:` and `Returns:` per Google-style standards.
6. `data/scripts/` needs `__init__.py` for consistency with `data/polls/importers/` and correct mypy analysis of `from scripts import by_election_import`.
7. Add `[mypy-scripts.*]` or `[mypy-scripts.by_election_import]` ignore entry to `mypy.ini`.
8. `flash("UNS model updated.")` at `server.py:1284` fires even when the model was skipped (condition was False). Should be inside the inner `if` block.
9. Double blank line in `update_mapping_and_import_new.py` after removed function.

**Suggestions:**

10. Split the `conftest.py` fix into its own commit ("fix(tests): use DatabaseConfig.local() to prevent Supabase connection in test suite") — it resolves a critical correctness bug from the prior review and deserves its own attribution.
11. Add `[mypy-export_non_simulation_elections]` back to `mypy.ini` (was removed but the module is still present and complex enough to benefit from type checking).

# Analysis: supabase-migration

_Commits reviewed: 1 | Issue: .agents/issues/supabase-migration.md | 2026-03-21 16:13_

Now I have everything I need. Here is the full eight-section review.

---

## Engineering Review: commit `c404ae95` (supabase)

---

### 1. Issue Coverage

The issue spec (`supabase-migration.md`) describes six phases. Implementation status vs. spec:

**Phase 1 — `DATABASE_URL` env var support**: Spec says *"Add support for `DATABASE_URL` env var (Supabase connection string) that overrides the default localhost connection."* The actual implementation uses three separate `SUPABASE_REGION`, `SUPABASE_DB_USERNAME`, `SUPABASE_DB_PASSWORD` vars instead. No code reads `DATABASE_URL`. Yet migration step 4 still says *"Set `DATABASE_URL` env var and verify Flask server connects"*, and the migration script's `Next steps` says `export DATABASE_URL="$SUPABASE_DB_URL"`. This documentation still points to a dead code path — setting `DATABASE_URL` does nothing in the current implementation. **The spec and the code diverge on the primary connection string mechanism.**

**Phase 2 — `archive_old_model_runs.py`**: Delivered. Core archival logic is sound.

**Phase 3 — Run archive pre-migration**: Manual step, no code issue.

**Phase 4 — `migrate_to_supabase.sh`**: Delivered.

**Phase 5 — backfill with `--include-sqlite`**: Delivered.

**Phase 6 — Model run scripts**: Spec says *"Optionally trigger `archive_old_model_runs.py` after a run — can defer to manual initially."* The implementation made auto-archiving on by default (opt-out via `--no-archive`), and archives **all** model runs, not just those older than 30 days. The 30-day rolling window intent from the issue is not what the code does. `run_retrospective_uns.py` is also out of scope per Phase 6, which is fine, but it missed the `DatabaseConfig.local()` update — see §3.

**`.env_example`**: Missing `DATABASE_URL`, which the issue and migration notes reference throughout.

---

### 2. Handler Path Audit

**New routes added:**

`GET /models/outputs` (modified): `_sqlite_model_elections(limit)` uses f-string SQL:

```python
query += f" LIMIT {int(limit)}"  # server.py:508
```

The `int()` cast prevents injection. However, `limit` comes from `max(0, default_limit - len(rows))` — a server-computed integer, never user-supplied — so the risk is theoretical. Still an antipattern; prefer a parameterized approach or `sqlite3`'s native limit support.

`_sqlite_model_elections` opens a new SQLite connection on every page load. If `SQLITE_ARCHIVE_PATH` is missing and the archive table has never been created, the code checks `SQLITE_ARCHIVE_PATH.exists()` first and returns early — safe.

`DELETE /models/outputs/sqlite/<int:election_id>/delete` (new):

```python
if not SQLITE_ARCHIVE_PATH.exists():
    flash(f"SQLite archive not found.")  # server.py:1099 — useless f-string
    return redirect(url_for("model_outputs"))
```

The two `conn.execute("DELETE ...")` calls inside `with sqlite3.connect(...) as conn` are in the same implicit transaction — safe atomicity. The `int` route converter prevents injection on `election_id`.

`POST /models/outputs/delete-selected` (modified): SQLite bulk delete correctly uses `?` placeholders:

```python
placeholders = ",".join("?" * len(sqlite_ids))  # server.py:1164
f"DELETE FROM votes WHERE election_id IN ({placeholders})", sqlite_ids
```

This is safe.

**Issues found:**

- `SQLITE_ARCHIVE_PATH` module-level initialization at `server.py:95–97` uses `__import__("os")` inline instead of a top-level `import os`. Non-idiomatic and unusual.
- `flash(f"SQLite archive not found.")` (line 1099) — f-string with no interpolation. Should be a plain string literal. May trigger linting warnings.
- `model_outputs()` docstring says *"the 10 most recent"* but `default_limit` is now 30 (line 546).

---

### 3. Regression Risk

**[CRITICAL] `archive_all=True` immediately archives the just-created run — breaks detail view**

`run_uns_model.py:1423`:
```python
archive_old_runs(db, archive_all=True)
```

This archives every `model_uns` election from PostgreSQL — including the run that was just persisted moments earlier. After archiving, that run no longer exists in PostgreSQL. The detail route at `/models/outputs/<int:election_id>` queries PostgreSQL only:

```python
election_row = session.execute(
    select(Election, Map)...
    .where(Election.id == election_id, Election.type == ElectionType.model_uns)
).first()
if election_row is None:
    flash(f"Model output #{election_id} not found.")
    return redirect(url_for("model_outputs"))
```

**Result:** every run from `run_uns_model.py` produces a detail page that 404s. The listing page masks this because it reads SQLite, but any attempt to view details of a new run redirects back to the list with a "not found" flash. The template hides "View Model" for SQLite-source rows, so the link isn't offered, but this still means the detail view is permanently broken for all runs from this script.

**[CRITICAL] `parse_args()` type annotation is wrong**

`run_uns_model.py:178`:
```python
def parse_args() -> SimulationConfig:
```

Actually returns `tuple[SimulationConfig, bool]` (line 250: `return SimulationConfig(...), args.no_archive`). This is a mypy strict mode violation. The function docstring also says *"Returns: A SimulationConfig"*.

**`run_retrospective_uns.py` not updated to `DatabaseConfig.local()`**

`run_retrospective_uns.py:149`:
```python
db = Database()
```

`run_uns_model.py` was updated to `Database(DatabaseConfig.local())` to prevent Supabase writes. `run_retrospective_uns.py` was missed. When Supabase env vars are active, retrospective runs write to Supabase — inconsistent with the stated design intent (model simulation data stays local).

**`load_dotenv(override=True)` interacts badly with tests when `.env` has Supabase creds**

`config.py:11`:
```python
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
```

The comment on line 10 says *"already-exported shell variables take precedence"* — this is factually backward. `override=True` means `.env` values override already-exported shell variables, not the other way around.

`conftest.py:27–28`:
```python
config = DatabaseConfig.from_env()
config.database = TEST_DB_NAME
```

When a developer has a `.env` with Supabase credentials, `DatabaseConfig.from_env()` returns a config with `supabase_region`, `supabase_db_username`, `supabase_db_password` all set. The `url` computed field then builds a Supabase URL ignoring `self.database` entirely. `config.database = TEST_DB_NAME` is accepted but silently irrelevant — the URL still targets Supabase. Tests silently run against the live remote database.

**`archive_all=True` print message is misleading**

`archive_old_model_runs.py:115–118`:
```python
print(
    f"Found {len(elections_to_archive)} elections to archive "
    f"(election_date < {cutoff})"
)
```

When `archive_all=True`, the cutoff is computed but the `election_date < cutoff` filter is skipped. The message still prints `(election_date < {cutoff})` which is incorrect. This makes dry-run output misleading when called with `archive_all=True`.

---

### 4. Test Coverage Gaps

The test suite (`data/tests/`) contains only integration tests covering polls, parties, seats, elections, regions, votes, and maps. **None of the new or substantially changed code has any test coverage:**

| Component | Tests |
|-----------|-------|
| `DatabaseConfig.local()` — bypasses env var reading | None |
| `DatabaseConfig` Supabase URL construction | None |
| `archive_old_runs()` — core archival function | None |
| `archive_old_runs()` with `archive_all=True` | None |
| `_sqlite_model_elections()` in `server.py` | None |
| `delete_sqlite_model_output` Flask route | None |
| `delete_selected_model_outputs` with SQLite IDs | None |
| `_load_sqlite_elections()` | None |
| `_as_of_date_from_name()` | None |
| `_process_election_recs()` | None |

`_as_of_date_from_name` and `_process_election_recs` are pure functions with no DB dependencies — both are straightforward to unit test. `archive_old_runs` has an explicit `dry_run` flag that could enable integration testing without writes.

---

### 5. Coding Standards Compliance

**Incomplete docstrings:**

`archive_old_model_runs.py:92–99` — `archive_old_runs()` Args section only documents `archive_all`:

```python
Args:
    archive_all: If True, archive every model_uns run regardless of age.
```

`db`, `archive_days`, `sqlite_path`, and `dry_run` are all missing from Args.

**Stale docstrings:**

- `config.py:44–46` — `url` property docstring says *"If SUPABASE_HOST and SUPABASE_USER are set"*. The actual var names are `SUPABASE_REGION` and `SUPABASE_DB_USERNAME`.
- `run_uns_model.py:178–200` — `parse_args()` docstring says *"Returns: A SimulationConfig"* — now returns `tuple[SimulationConfig, bool]`.
- `run_uns_model.py:220` — `--no-archive` help text says *"Skip auto-archiving model runs older than 30 days"* — the behavior archives all runs, not just those older than 30 days.
- `server.py:540` — `model_outputs()` docstring says *"the 10 most recent"* — `default_limit` is 30.

**Help text inconsistency:**

`backfill_model_output_trends.py:127`:
```python
help="... Pass --no-sqlite to read from PostgreSQL only."
```
The actual flag generated by `BooleanOptionalAction` is `--no-include-sqlite`. The issue notes (`supabase-migration.md:136`) were updated to say `--no-include-sqlite`, but the help text still says `--no-sqlite`.

**`_load_sqlite_elections` docstring misrepresents the `map_name` filter:**

`backfill_model_output_trends.py:46`:
```
this filter is best-effort and ignored when not possible to apply.
```
The filter is never applied at all — there is no conditional code attempting it. The docstring implies a best-effort attempt was made. Should say the filter is not implemented.

**`__import__` antipattern:**

`server.py:96`:
```python
SQLITE_ARCHIVE_PATH = Path(
    __import__("os").environ.get("SQLITE_DATABASE_PATH", str(DATA_DIR / "model_uns.db"))
)
```
`import os` should be at the top of the file with the other stdlib imports.

**Empty f-string:**

`server.py:1099`:
```python
flash(f"SQLite archive not found.")
```
No interpolation. Should be `flash("SQLite archive not found.")`.

---

### 6. Security and Data Integrity

**Password in URL string (low risk, by convention):**  
The Supabase URL constructed in `config.py:56–57` embeds the password in a connection string. This is standard PostgreSQL URI practice. No logging of the URL string was found.

**`migrate_to_supabase.sh` password in process table:**  
`_sb_password` is extracted via `sed` and stored as a shell variable (line 42), then placed into `SUPABASE_CONN` (line 56). The full connection string with password is visible in `/proc/<pid>/environ` and potentially `ps` output for the duration of the `psql` invocations. This is inherent to shell-based migration scripts with no mitigation option short of `PGPASSFILE`. Acceptable for a one-time operator tool.

**SQLite `LIMIT` via f-string (antipattern, not a live vulnerability):**  
`server.py:508` — `limit` originates from server-computed arithmetic, not user input. The `int()` cast adds belt-and-suspenders. Still an antipattern; standard practice is to pass it as a query parameter even for SQLite (which doesn't support `?` for `LIMIT` in all drivers, so `int()` cast + f-string is acceptable, but worth a comment).

**No atomicity between SQLite write and PostgreSQL delete in `archive_old_runs`:**  
The function writes to SQLite, commits, then deletes from PostgreSQL, commits. Kill between the two commits results in duplicates (data in both stores, caught on next run by `existing_ids` check). Kill during SQLite write raises an exception before the PostgreSQL delete runs — data safe in PostgreSQL. The failure mode is duplicates, not data loss. This is acceptable given the operational context.

---

### 7. Commit Hygiene

**Commit message:** `"supabase"` — inadequate. The commit introduces two new scripts, modifies four Python files and one Jinja template, adds `.gitignore` entries and `.env_example`, and includes regenerated data files. The message conveys nothing about what changed or why.

**Bundled changes:** The commit mixes:
- Infrastructure (config, migration script)
- New maintenance tooling (`archive_old_model_runs.py`)
- Feature additions to the Flask server
- Model runner behavioral changes
- Regenerated data artifacts (`model_output_trends.csv`, `model_output_trends_meta.json`)

The CSV/JSON data files in particular should be a separate commit. They are output artifacts from a model run, not source code changes, and obscure the diff. Bundling them makes it harder to identify what code changed vs. what was generated.

---

### 8. Overall Verdict

**Critical (fix before relying on these behaviors):**

1. **Archive immediately destroys the just-created run.** `archive_old_runs(db, archive_all=True)` at the end of `run_uns_model.py` removes the run that was just written — including from PostgreSQL — so the detail view 404s for every run produced by this script. Either change to `archive_all=False` (archives only runs older than 30 days, preserving the recent window) or accept that detail views only work for SQLite-sourced rows shown in the listing.

2. **`parse_args()` return type annotation is wrong.** `-> SimulationConfig` must be `-> tuple[SimulationConfig, bool]`. mypy strict mode will flag this.

3. **`run_retrospective_uns.py` not updated.** Uses `Database()` instead of `Database(DatabaseConfig.local())`. Writes model runs to Supabase when Supabase env vars are active — unintended per the design intent in `run_uns_model.py`.

4. **`load_dotenv(override=True)` comment is backward.** The comment says shell vars take precedence; the code says the opposite. When a Supabase `.env` is present, `conftest.py`'s `config.database = TEST_DB_NAME` is silently ignored (the Supabase URL overrides the test DB name), causing tests to target the live remote DB.

**Minor:**

5. `archive_old_runs()` docstring Args section missing `db`, `archive_days`, `sqlite_path`, `dry_run`.
6. `config.py` url docstring: "SUPABASE_HOST/SUPABASE_USER" should be "SUPABASE_REGION/SUPABASE_DB_USERNAME".
7. `parse_args()` return docstring still says "Returns: A SimulationConfig".
8. `--no-archive` help text says "older than 30 days" but behavior is archive-all.
9. `archive_old_runs` print message says `(election_date < {cutoff})` when `archive_all=True` — misleading.
10. `backfill_model_output_trends.py` help text says `--no-sqlite`; actual flag is `--no-include-sqlite`.
11. `_load_sqlite_elections` docstring says "best-effort, ignored when not possible" — filter is simply unimplemented.
12. `model_outputs()` docstring says "10 most recent"; limit is 30.
13. `server.py` uses `__import__("os")` inline; should be top-level `import os`.
14. `flash(f"SQLite archive not found.")` — empty f-string.
15. Issue spec's `DATABASE_URL` references (migration steps 4, migration script Next steps) are dead code — no code reads `DATABASE_URL`. Either implement it or remove the references.

**Suggestions:**

16. Add unit tests for `DatabaseConfig.local()` URL output, `_as_of_date_from_name()`, `_process_election_recs()`, and at least a smoke test for `archive_old_runs(dry_run=True)`.
17. Split the regenerated CSV/JSON data artifacts into a separate commit from the code changes.
18. Consider `PGPASSFILE` or `PGPASSWORD` env var for `migrate_to_supabase.sh` to keep the password out of the process table.

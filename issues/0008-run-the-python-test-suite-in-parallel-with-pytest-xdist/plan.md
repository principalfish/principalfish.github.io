# Plan: run-the-python-test-suite-in-parallel-with-pytest-xdist

**Build model:** claude-opus-5-5. The pieces are small, but they touch test infrastructure every later run depends on (pytest hook ordering, controller-vs-worker behaviour, shell quoting under shellcheck), where a subtle mistake hides failures.

## Context

The Python suite lives in `data/tests/` (7,424 tests) and runs through `data/run_tests.sh`: strict mypy first, which gates the run, then `"$PYTHON" -m pytest tests/ "$@"`. The root `./check.sh` (from 0007, merged in PR #87) calls `./data/run_tests.sh "${PYTEST_ARGS[@]}"`. CI's "Python pipeline (mypy + pytest)" job in `.github/workflows/ci.yml` runs `./run_tests.sh` from `data/` on `ubuntu-latest` (4 vCPU), using `data/.venv`. There is no pytest config file: `tests/test_project_layout.py:62` (`test_pytest_run_is_limited_to_tests_folder`) asserts that `data/pytest.ini` doesn't exist and that `run_tests.sh` matches `pytest\s+tests/`. Keep both true.

The measurements are in `measurements.md` in this folder. In short: serial 8m39s at 73% CPU; `-n 4` 5m02s; `-n 6` 3m29s; `-n auto` (12) 2m37s; `-n auto` with coverage 2m29s. Coverage XML was identical serial vs xdist on a 292-test subset. Serial runs leave the CPU 27–62% idle, waiting on SQLite fsyncs in pytest's temp folder, which here is on the ext4 root disk under WSL2. Two console test files took 46.6s on disk and 19.7s with the temp folder on `/dev/shm`. A full run writes about 350 MB of temp files.

Why it's parallel-safe (all checked 2026-11-15):
- Every test gets its own temp SQLite file (`tests/conftest.py`, the `db` fixture and the autouse `_never_open_the_live_database`). The one session fixture, `_westminster_template`, uses `tmp_path_factory`, so it's per worker, and its connections are read-only.
- No fixed ports, no `chdir`, no raw `os.environ` writes in tests. Real subprocesses are only `--help`/import checks, the backup lock holder and job-timeout tests. `lru_cache`s are per process.
- The tracked trend files are written only by the model `main()`s via `models/support/trends.py:publish_trends`. The console outputs route that once rewrote `model_output_trends.json` is redirected to `tmp_path` by an autouse fixture (`tests/test_console_westminster.py:45`).
- Four full runs (serial, `-n 4`, `-n auto`, `-n auto` + coverage) failed the same 5 tests, nothing new, and left `git status` clean.

Those 5 failures are local only: `data/.pytest_cache` in the main checkout holds 6.5 GB of old scratch repo copies (3,016 `.py` files against 318 real ones). These scans walk `DATA_DIR.rglob("*.py")` and skip only a fixed set of folder names:
- `tests/test_core_package.py:44` (`SKIPPED_DIRS` at :26);
- `tests/test_project_layout.py:135` (`SKIPPED_DIRS` at :18);
- `tests/test_model_package_paths.py:150`;
- `tests/test_us_importer_packages.py:36`.

The scans are also the slowest tests (up to 45s each under load). A fresh worktree has no such folder, so to reproduce the failure in a test, create an ignored folder holding a `.py` file.

Settled decisions (from `decisions.md`):
- `-n` goes in `data/run_tests.sh`, as `pytest tests/ -n auto … "$@"`, so CI and `check.sh` get it. A later `-n 0` in the args turns it off, because pytest's last value wins.
- Default workers: CPU count / 2. Implement it as a `pytest_xdist_auto_num_workers` hook in `tests/conftest.py`, so `-n auto` means half. The hookspec is `firstresult`, and a conftest impl is called before xdist's builtin. Mark it `@pytest.hookimpl(optionalhook=True)` so a venv without xdist still loads the conftest. When `PYTEST_XDIST_AUTO_NUM_WORKERS` is set, return `None` so xdist's builtin applies the env var. Otherwise return `max(1, len(os.sched_getaffinity(0)) // 2)`, falling back to `os.cpu_count()` where `sched_getaffinity` is missing.
- CI uses all 4 workers: `ci.yml`'s Python job sets `PYTEST_XDIST_AUTO_NUM_WORKERS: 4`.
- Pytest's temp files go in RAM. When `/dev/shm` is a writable directory, `run_tests.sh` creates a fresh per-run folder under it (`mktemp -d`), passes `--basetemp=<folder>/t`, and removes the folder on exit (`trap … EXIT`). Otherwise it leaves pytest's default. An env var, `TESTS_TMP_ON_DISK=1`, skips the RAM folder so a failing test's files stay on disk. A user's own `--basetemp` in the args still wins, since it comes later.
- The repo-scanning tests list data/'s `.py` files through one shared helper using `git ls-files --cached --others --exclude-standard`. Gitignored folders are skipped, new untracked files still count, and tracked files deleted from the working tree are filtered out.
- A session guard: the run fails if tracked or untracked-not-ignored files under `electionmaps/data` or `uselectionmaps/data` changed during it, and it names them.
- Coverage works under xdist. This planner chat, not the builder, updates the planner branch's `coverage-changed.sh` after the PR merges.
- The Pages-deploy gate was considered and dropped. It's not part of this issue.

Assumed: `pytest-xdist>=3.8` in `data/requirements.txt`, matching pytest's `>=` style, with no `psutil`. `--pdb` keeps working, because xdist turns itself off for `-n auto` when `--pdb` is passed. `-s` needs `-n 0`, which gets documented.

## Approach

Three self-contained pieces: the parallel run itself, the scan helper, then the guard. Each is proven by its own tests plus a full `./run_tests.sh`.

Rejected alternatives:
- `addopts = "-n auto"` in `data/pyproject.toml`. Every direct `python -m pytest` call would go parallel, including one-file runs (worker start-up cost) and `coverage-changed.sh`.
- `-n` in `check.sh` only. CI and `cd data && ./run_tests.sh` would stay serial.
- `TMPDIR=/dev/shm`. pytest keeps its last 3 numbered runs, so about 1 GB of RAM would stay used between runs.
- `PRAGMA synchronous=OFF` in the `db` fixture. It only covers that one engine, and it changes how the code under test behaves.
- Extending the `SKIPPED_DIRS` sets. The next stray folder would need another entry.

## Pieces

1. Run the suite in parallel
   - Files: `data/requirements.txt`, `data/run_tests.sh`, `data/tests/conftest.py`, `.github/workflows/ci.yml`, `AGENTS.md`, a new test module (e.g. `data/tests/test_parallel_run.py`)
   - What:
     - Add `pytest-xdist>=3.8` to `data/requirements.txt`.
     - In `run_tests.sh`, run `"$PYTHON" -m pytest tests/ -n auto <basetemp args> "$@"`, with the `/dev/shm` per-run folder and cleanup described in Context. It must stay shellcheck-clean and keep matching `pytest\s+tests/`.
     - Add the half-the-CPUs hook to `tests/conftest.py`.
     - In `ci.yml`, set `PYTEST_XDIST_AUTO_NUM_WORKERS: 4` on the Python job's `mypy + pytest` step.
     - In `AGENTS.md`'s Checks section, add one or two lines: the suite runs in parallel on half the CPUs, `-n 0` runs it serially (needed for `-s`), `PYTEST_XDIST_AUTO_NUM_WORKERS=<n>` sets the count, and `TESTS_TMP_ON_DISK=1` keeps temp files on disk.
   - Functions: `pytest_xdist_auto_num_workers` (new, in conftest)
   - Depends on: none
   - Acceptance:
     - `cd data && ./run_tests.sh` reports half the CPUs as workers (6 here), passes, and leaves no `data-tests` folder under `/dev/shm` afterwards.
     - `./run_tests.sh -n 0 -k <kw>` runs serially.
     - `git status` is clean after the run.
     - The draft PR's CI "mypy + pytest" step reports 4 workers and finishes faster than today's ~5m04s. Report its time.
   - Tests:
     - The hook returns half the available CPUs, at least 1, given a patched CPU count.
     - The hook returns `None` when `PYTEST_XDIST_AUTO_NUM_WORKERS` is set.
     - `run_tests.sh` still matches `pytest\s+tests/` (the existing `test_pytest_run_is_limited_to_tests_folder`) and passes `-n auto` before `"$@"`.
   - Manual review: from the builder's worktree, run `cd data && time ./run_tests.sh`. Check the "created: N workers" line says half your CPUs and note the time. Then run `ls /dev/shm` to confirm no leftover per-run folder, and `TESTS_TMP_ON_DISK=1 ./run_tests.sh -k model_outputs -q` to confirm it runs with temp files on disk.

2. List the scanned source files through git
   - Files: a new helper module in `data/tests/` (e.g. `data/tests/source_files.py`), `data/tests/test_core_package.py`, `data/tests/test_project_layout.py`, `data/tests/test_model_package_paths.py`, `data/tests/test_us_importer_packages.py`, plus tests for the helper
   - What:
     - Add one helper that returns data/'s `.py` files, optionally under a sub-folder, from `git -C <data dir> ls-files -z --cached --others --exclude-standard -- '*.py'`. Keep only files that exist, as absolute `Path`s sorted.
     - Replace the four `DATA_DIR.rglob("*.py")` scans with it, keeping each test's own extra skips (e.g. `archive`) and its "found more than 100 files" sanity check.
     - The sub-tree scans (`test_importer_packages.py:27/32`, `test_console_templates.py:339`) may switch too, for consistency, but don't have to.
   - Functions: the new helper, and the four scans' file-listing code
   - Depends on: none
   - Acceptance: the four scan tests pass with a gitignored folder of stray `.py` copies under `data/`; the scans cover the same real files as before.
   - Tests:
     - The helper skips a `.py` file inside a gitignored folder.
     - It includes a new untracked, not-ignored `.py` file.
     - It leaves out a tracked file deleted from the working tree.
     - The existing scan tests still pass and still find more than 100 files.
     - Write the helper tests against a throwaway git repo in `tmp_path`, so they don't depend on this checkout's state.

3. Fail the run if it changes tracked exported data
   - Files: `data/tests/conftest.py` (or a small plugin module it registers through `pytest_plugins`), plus tests for the guard
   - What:
     - At session start, snapshot the files that `git -C <repo root> ls-files -z -m -o -d --exclude-standard -- electionmaps/data uselectionmaps/data` lists, each with a sha256, or "deleted".
     - At session end, snapshot again. If the two differ, print the changed paths and set the session's exit status to failed.
     - A file already dirty before the run fails only if it changes again.
     - Run it on the xdist controller and in serial runs, never on workers (`hasattr(config, "workerinput")`).
     - If git or the repo isn't available, warn and skip.
   - Functions: `pytest_sessionstart` / `pytest_sessionfinish` (or plugin equivalents), and pure snapshot/compare helpers
   - Depends on: none
   - Acceptance:
     - A normal `./run_tests.sh` passes with the guard active.
     - A run in which some test writes a tracked file under `electionmaps/data` fails and names it.
   - Tests:
     - The snapshot helper, against a `tmp_path` git repo: a clean tree gives an empty snapshot; modifying a tracked file shows up; an already-dirty file that's unchanged isn't reported; a new untracked file shows up.
     - The compare helper names exactly the changed paths.
     - The guard is skipped on xdist workers.

## Checkpoints
- [ ] 1. Suite runs in parallel on half the CPUs (4 on CI), temp files in RAM, docs updated
- [ ] 2. Repo scans list files through git; stray ignored folders no longer fail them
- [ ] 3. Run fails if it changes tracked exported data

## Verification

After each piece:
- `cd data && ./run_tests.sh` (strict mypy gates, then pytest, now parallel)
- `./check.sh --lint-only` (ruff, Biome, shellcheck: `run_tests.sh` is shellchecked)
- `git diff --check`
- `git status`, which must stay clean apart from the piece's own files

`npm test` and `npm run minify:electionmaps` aren't needed: no front-end files change.

Coverage: run `coverage-changed.sh` as usual. Until this chat updates it, it runs pytest serially, which still works.

Piece 1 also reports the draft PR's CI "mypy + pytest" step time against today's ~5m04s.

## Notes
- Don't touch the live database (`/home/philiph/dbs/elections.db`); nothing here needs it.
- Stage files explicitly; never `git add -A` (the tracked trend JSON must never ride along).
- After merge (planner chat): add `-n auto` and the RAM `--basetemp` folder to the planner branch's `tools/coverage-changed.sh`, only when xdist is importable.

# Findings: run-the-python-test-suite-in-parallel-with-pytest-xdist

## piece 1 · 2026-11-15 · Run the suite in parallel

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | correctness | low | defect | verified | fixed | `data/tests/conftest.py:47` | Hook deferred to xdist for any non-empty `PYTEST_XDIST_AUTO_NUM_WORKERS`, but xdist ignores a non-integer and uses every CPU |
| round | coverage | low | test | verified | fixed | `data/tests/test_parallel_run.py` | Signal test didn't assert the RAM folder existed before checking it was removed |
| round | correctness | low | defect | dropped | - | `data/run_tests.sh:21` | No size check on `/dev/shm` (Docker's 64 MB default) — dropped: no container setup in the repo; WSL2 and ubuntu-latest only |
| round | correctness | low | test | dropped | - | `data/tests/test_parallel_run.py:107` | Stub-script subprocess has no timeout — dropped: verifier removed the traps and neither signal test hung |
| round | coverage | low | test | dropped | - | `data/run_tests.sh` | No test of the missing/unwritable `/dev/shm` condition — dropped: the check itself rated it not worth a test; empty-array path is covered |
| round | security | - | - | noticed | accepted | `data/run_tests.sh` | A SIGKILLed run leaves its `/dev/shm/data-tests.*` folder (~350 MB) until reboot; no trap can catch SIGKILL |
| round | correctness | low | cleanup | noticed | accepted | `data/run_tests.sh` | `trap 'exit 143' TERM` may be redundant: bash already runs the EXIT trap on an untrapped SIGTERM (the INT trap is needed) |

## piece 2 · 2026-11-15 · List the scanned source files through git

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | correctness | low | defect | verified | fixed | `data/tests/test_source_files.py:17` | The `repo` fixture didn't clear GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY or GIT_COMMON_DIR, so a hook-set variable would send the throwaway repo's add/commit to the real repo |
| round | correctness | - | cleanup | verified | fixed | `data/tests/test_importer_packages.py:26` | MODULES and RUNNABLE each called `python_files(IMPORTERS_DIR)` at import (two git calls per worker) |
| round | coverage | - | test | verified | fixed | `data/tests/source_files.py:46` | No test for a conflicted file that git lists once per stage |
| round | coverage | low | test | dropped | - | `test_core_package.py`, `test_model_package_paths.py`, `test_us_importer_packages.py` | No own "more than 100 files" check — dropped: only `test_project_layout.py` had one, and it is kept; the helper's checkout test covers the empty-scan risk |
| re-check | correctness | low | test | verified | fixed | `data/tests/test_source_files.py:105` | `test_the_repo_fixture_clears_the_variables_that_point_git_elsewhere` passes whether or not the fixture's delenv loop exists, unless the suite runs under a git hook |
| manual | user | - | test | - | fixed | `data/tests/test_source_files.py` | User chose option A: a `_git_pointed_elsewhere` fixture sets the five git env vars and `repo` depends on it, so the env-var test fails (every `repo` test errors) if the clearing is removed |

## piece 3 · 2026-11-15 · Fail the run if it changes tracked exported data

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | correctness | low | defect | verified | fixed | `data/tests/data_guard.py:63` | An unreadable dirty file raised OSError and ended the run in INTERNAL_ERROR; `_file_state` now returns an `unreadable` marker |
| round | correctness | - | defect | verified | fixed | `data/tests/data_guard.py:59` | A non-UTF-8 file name made the `-z` decode raise; names now go through `os.fsdecode` |
| round | correctness | - | defect | verified | fixed | `data/tests/data_guard.py:116` | The finish hook ran before pytest's session-fixture teardown on `-x`/Ctrl-C runs; now `trylast=True` |
| round | correctness | - | cleanup | verified | fixed | `data/tests/data_guard.py:98` | Every CalledProcessError was reported as "not a git checkout"; now "git ls-files failed in <root>" |
| round | coverage | - | test | verified | fixed | `data/tests/test_data_guard.py` | No test that an already-failed exit status is left alone |
| round | docs | - | - | - | - | - | clean |
| round | security | - | - | - | - | - | clean |
| re-check | coverage | low | test | verified | fixed | `data/tests/test_data_guard.py:225` | In the "already-failed status is left alone" test the TESTS_FAILED case can't catch a regression; only INTERRUPTED discriminates |
| re-check | coverage | low | test | verified | fixed | `data/tests/test_data_guard.py:212` | Nothing covers NO_TESTS_COLLECTED flipping to TESTS_FAILED; only OK is tested |
| re-check | coverage | - | cleanup | verified | fixed | `data/tests/test_data_guard.py:255` | `assert state.skipped_because is not None` is redundant with the equality on the next line |
| manual | user | - | test | - | fixed | `data/tests/test_data_guard.py` | User chose option A: the already-failed status test uses USAGE_ERROR and INTERRUPTED, the failing-run test is parametrized over OK and NO_TESTS_COLLECTED, and the redundant assert is gone (mutation proofs: both regressions are now caught) |

## review 01 · 2026-11-15

| Phase | Check | Severity | Kind | Verdict | Outcome | Where | Finding |
|-|-|-|-|-|-|-|-|
| round | user | high | defect | verified | fixed | `data/tests/test_backup.py:231` | Parametrize ID includes gzip's mtime (current time on 3.12), so xdist workers collecting in different seconds disagree and CI fails intermittently |
| round | correctness | low | defect | verified | fixed | `data/tests/source_files.py:48` | `decode("utf-8")` raises on a non-UTF-8 file name and breaks collection; use `os.fsdecode` |
| round | common-mistakes | low | defect | verified | fixed | `data/tests/source_files.py:48` | Same as correctness: decode vs `os.fsdecode` |
| round | coverage | low | defect | verified | fixed | `data/tests/source_files.py:48` | Same as correctness: decode vs `os.fsdecode`, with a `bad-\xff.py` test |
| round | coverage | low | test | verified | fixed | `data/tests/conftest.py:39` | No end-to-end test that `-n auto` uses the hook and starts half the CPUs |
| round | correctness | low | cleanup | verified | skipped | `data/tests/test_data_guard.py:21` | Repo fixture, env-var list and helpers duplicated with `test_source_files.py` |
| round | common-mistakes | low | cleanup | verified | skipped | `data/tests/test_source_files.py:13` | Same duplication, raised separately |
| round | correctness | low | cleanup | verified | skipped | `data/tests/test_parallel_run.py:11` | Imports the hook and helper from `tests.conftest` |
| round | correctness | - | docs | verified | skipped | `data/run_tests.sh:21` | A small or noexec `/dev/shm` isn't detected; a docs line was proposed |
| round | security | - | - | dropped | - | `data/requirements.txt:17` | `pytest-xdist>=3.8` unpinned — dropped: the settled choice, matching pytest |
| round | security | low | - | dropped | - | `data/run_tests.sh` | SIGKILL leaves the /dev/shm folder — dropped: accepted by the user at piece 1 |
| round | docs | - | docs | dropped | - | `AGENTS.md` | "Half the CPUs" against CI's 4 — dropped: `ci.yml` explains it |
| re-check | correctness | medium | test | verified | fixed | `data/tests/test_parallel_run.py:111` | The new `-n auto` test asserts `created: N/N workers`, but xdist prints the singular `1/1 worker` when N is 1 (a machine with 2 or 3 CPUs, or a 1-CPU taskset); reproduced with `taskset -c 0,1`. CI (4 vCPU, 2 workers) and this machine (6) pass |
| re-check | common-mistakes | - | - | - | - | - | clean |
| re-check | coverage | - | - | - | - | - | clean |
| manual | user | - | test | - | fixed | `data/tests/test_parallel_run.py:111` | User chose option A: the `-n auto` test matches `created: N/N workers?` with a regex so xdist's singular `1/1 worker` passes; checked with `taskset -c 0`, `0,1` and `0-2`, and a correctness re-check was clean |

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
| re-check | correctness | low | test | verified | open | `data/tests/test_source_files.py:105` | `test_the_repo_fixture_clears_the_variables_that_point_git_elsewhere` passes whether or not the fixture's delenv loop exists, unless the suite runs under a git hook |


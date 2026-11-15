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

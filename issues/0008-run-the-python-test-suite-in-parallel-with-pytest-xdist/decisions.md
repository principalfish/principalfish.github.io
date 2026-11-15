# Decisions: Run the Python test suite in parallel with pytest-xdist

## Settled

## Open
- Is the suite safe to run in parallel (temp DBs, tracked-file writes, shared files, ports, module caches, order dependence)?
- What speedup do `-n auto` and `-n 4` give over serial?
- Does coverage (`coverage-changed.sh`) still work under xdist?
- Where does `-n` live: `data/run_tests.sh`, root `check.sh`, or `pytest.ini`; and does CI use it?

## Assumed
- Issue 0007 (PR #87, root `check.sh`) lands first; the builder branches from `master` after it merges.

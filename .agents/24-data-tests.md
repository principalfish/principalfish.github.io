# Area: Tests (`data/tests/`)

## Purpose

Validates DB-layer behavior for core entities and relationships.

## Coverage examples

- maps
- regions
- seats
- elections
- votes
- polls

## Characteristics

- Tests are DB-backed and rely on configured test DB (`election_maps_test`).
- Failures can come from environment/auth setup, not just logic changes.

## Execution

From `data/`:

```bash
../election_data/bin/python -m pytest tests/
```

or wrapper:

```bash
./run_tests.sh
```

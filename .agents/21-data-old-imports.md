# Area: Base imports (`data/old_data/`)

## Purpose

- Bootstrap foundational map, party, and historical election data.

## Canonical order

1. `import_topojson.py`
2. `import_parties.py`
3. `import_general_elections.py`
4. `import_region_populations.py` (optional)

## Durable notes

- Scripts target the current `data/models.py` schema.
- Treat older helper scripts in this folder as legacy unless explicitly needed.

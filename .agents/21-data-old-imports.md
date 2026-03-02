# Area: Base data imports (`data/old_data/`)

## Purpose

Bootstraps foundational election datasets into the current `models.py` schema.

## Main scripts

1. `import_topojson.py`
   - Loads constituency maps/regions/seats from TopoJSON files.
2. `import_parties.py`
   - Creates/updates party records, short names, colours.
3. `import_general_elections.py`
   - Loads 2010–2024 election votes and updates `seats.electorate`.
4. `import_region_populations.py` (optional)
   - Updates region population values from external CSV/JSON.

## Typical order

- `import_topojson.py`
- `import_parties.py`
- `import_general_elections.py`
- optional `import_region_populations.py`

## Notes

- Some files in `old_data/` are legacy/one-off style utilities.
- Import scripts are designed around current table names in `data/models.py`.
- Turnout is not stored as a standalone table row; it is derived from summed vote totals.

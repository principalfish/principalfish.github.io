# Area: Base imports (`data/old_data/`)

## Purpose

- Bootstrap foundational map, party, and historical election data.

## Running

Run `./old_data/import_all.sh` from the `data/` directory. This runs all four scripts in dependency order with `--skip-existing` flags.

## Canonical order

1. `scripts/import_topojson.py` — maps, regions, seats from TopoJSON boundary files
2. `scripts/import_parties.py` — parties with colours and short names
3. `scripts/import_general_elections.py` — historical GE results (2010–2024)
4. `scripts/import_region_populations.py` — region population figures (from `files/region_populations.csv`)

## Data files (`old_data/files/`)

- `650map.json` — pre-2019 constituency boundaries
- `650map_new.json` — post-2022 constituency boundaries
- `2010election.json` … `2024election.json` — per-seat GE results
- `2019election_new.json` — 2019 results remapped to post-2022 boundaries
- `region_populations.csv` — population figures for each region

## Durable notes

- Scripts live in `old_data/scripts/` and use `parents[2]` to resolve the `data/` path.
- All scripts are idempotent with `--skip-existing`; `import_all.sh` uses this by default.
- `import_region_populations.py` requires `--map-name` and `--input`; `import_all.sh` targets "UK Constituencies post 2022" and `files/region_populations.csv`.

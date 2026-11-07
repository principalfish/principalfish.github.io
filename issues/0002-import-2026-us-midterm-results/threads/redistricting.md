# Which states' House districts changed for 2026 (2026-11-07)

Method (scratch script, not in repo): for each 2024 district in `uselectionmaps/data/maps/map-21.topo.json`, find counties overlapping it (>2% of county area, us-atlas@3.0.1 counties-10m). For 2026, take the counties NYT reports per district (`results-house.json` county units). Score = share of a state's counties whose district set differs. Unchanged states score 0.00–0.05.

## Clearly redrawn (whole-district moves visible)
| State | Districts | Mismatch share | Note |
|-|-|-|-|
| UT | 4 | 0.66 | |
| CA | 52 | 0.52 | |
| TN | 9 | 0.46 | |
| LA | 6 | 0.36 | |
| OH | 15 | 0.31 | |
| FL | 28 | 0.30 | |
| TX | 38 | 0.26 | |
| AL | 7 | 0.19 | AL-02 moves wholesale (e.g. 01031, 01045, 01061 go AL-01 → AL-02) |
| NC | 14 | 0.12 | NC-01 / NC-03 swap eastern counties (01013, 01049, 01137 …) |

## Probably noise; confirm during the build
CO, NV, OR (≈0.11): extra districts appear on counties already split; no whole-county moves. MD, VA, AZ, NJ and below: slivers and independent cities.

## Not checkable this way
CT and MA (NYT reports towns, not counties; CT's FIPS changed to planning regions in 2022). AK, ME, RI and single-district states are trivially fine.

## Notable
MO scores 0.00, so 2026 ran on the 2024 map there, despite the 2025 redraw.

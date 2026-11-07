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

## 2012-cycle geometry for House 2018/2020 (Census probe, 2026-11-07)
- Census cartographic files: `GENZ2016/shp/cb_2016_us_cd115_*` (115th, 2016 elections) and `cb_{2018,2019,2020,2021}_us_cd116_*` (116th). There is no cd117 file in any vintage; TIGER2020/2021 also carry only cd116.
- PA: the 2018 court map is in cd116 (all 18 PA district bounding boxes differ from cd115), so cd116 is right for 2018.
- NC: the 2019 remedial map used in 2020 is not in any Census file. The cb_2020 cd116 NC districts are byte-identical in bounds to cb_2018. So 2020 needs NC's enacted 2019 plan from the NC General Assembly.

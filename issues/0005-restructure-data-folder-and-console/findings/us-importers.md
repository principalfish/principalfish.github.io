# Sweep findings: us-importers

Read-only sweep of master at 35e5326c, 2026-11-08. Model output; the planner spot-checked the design-critical claims.

**The findings file was NOT written.** Write was refused ("Subagents should return findings as text, not write report files"), so I did not route around it. The full detail is below, which is why this exceeds your 2000-character limit. Summary first. Paths are relative to `data/`. Read-only; mypy and imports were not run, so failure chains come from reading the code.

## Summary
Wrong or partly right:
- **Claim 3:** `convert_538_house` IS still needed. `convert.py:75` imports it at module level, and `create_app` (`console/__init__.py:41`) reaches that, so removing it stops the console starting. `convert_538_senate` is test-only.
- **Claim 4:** provenance only. Both presidential converters are manual one-shots; only tests call them. The JSON has no `meta`.
- **Claim 2:** `cli.py:57-60` inserts at 0; only `convert.py` and `current_senate.py` append.
- Claims 1 and 5 confirmed (1 is incomplete: also `regions`, `convert_senate_current`, `house_2026_sources`, `check_redistricting`).

Surprises:
- `us_electoral_votes` is live via `migrate_us_electoral_votes.py:37`, rebuild step 2, run by the console (`site.py:66`).
- The wiki converter chains through 538 presidential to `convert_538_house.aggregate_unit`.
- `import_all.sh` has no US steps, and `:54` calls a nonexistent `import_holyrood_boundaries.py`.
- The 21 "538" totals (8 in 2022, 13 in 2024) exist only in committed JSON.
- `sources.py:34` has no presidential chamber.
- `build_senate_topojson` and `build_presidential_topojson` have no callers, tests or docs, but are the only way to rebuild maps 23 and 22.

## convert_538_house symbols
| Symbol | Line | Reached by | Status |
|-|-|-|-|
| `UNOPPOSED_NOMINAL_VOTES` | 83 | `convert.py:75-76,471,523`; tests `test_usa_house.py:180`, `test_us_results_convert.py:158,252,681` | Live |
| `aggregate_unit` | 122 | `convert_538_presidential.py:39,103`; `convert_538_senate.py:42,109` | Presidential |
| `resolve_candidate_party`, `BALLOT_PARTY_TO_KEY`, `PARTY_PRECEDENCE` | 103, 65, 75 | `aggregate_unit` (`:139`) | Presidential |
| `district_code`, `keep_final_rcv_round`, `NON_VOTING_STATES`, `convert` | 86, 161, 60, 192 | own `convert`, tests | Test-only |
| `main` | 247 | none | Manual |

Backfill of the 21: `convert.py:444-462,507-516`, fed by `cli.py:568-585`, `:1089`, console `us_results.py:344`. The `is_538` branch (`convert.py:453,460`) and `_has_real_totals` (`:467`) cannot trigger on committed data: all 14 House/Senate files have `meta`. `test_us_results_convert.py:249-252` pins the constant to the 538 one.

## old_data/scripts/usa/ (import_all.sh: no for every file)
| File | Kind | Console | Rebuild | Callers |
|-|-|-|-|-|
| `us_import` | Live library | Yes | Via importers | `import_house:34`, `import_senate:38`, `import_presidential:33`, `cli.py:95`, `current_senate.py:62`, `us_results.py:105`, `test_us_results_convert.py:37`, `test_console_us_results.py:49` |
| `import_house_elections` | Live | In-process `cli.py:93,782`; subprocess | `rebuild_database.py:51,161` | `test_usa_house.py:31`, `test_rebuild_database.py:127,132`, README:608 |
| `import_senate_elections` | Live | In-process `cli.py:94,785`; subprocess | `:52,161` | `test_usa_senate.py:30`, `test_console_us_results.py:48`, `test_rebuild_database.py:133,165` |
| `import_presidential_elections` | Live | Subprocess only | `:53,161` | `test_usa_presidential.py:34`, `test_presidential_ev_consumers.py:35`, `test_rebuild_database.py:134` |
| `regions` | Live table | Via `us_import` | Yes | `us_import:20`, `convert_538_presidential:40`, `convert_wiki:51`, `convert_senate_current:35` |
| `us_electoral_votes` | Live seed | Via rebuild | Via migrate | `migrate_us_electoral_votes.py:37`, `convert_538_presidential:41`, `convert_wiki:52`, `test_us_electoral_votes.py:25`, README:709 |
| `convert_538_house` | Converter + live constant | Import-time | No | `convert.py:75`, `test_usa_house.py:30` |
| `convert_538_senate` | Dead one-shot | No | No | `test_usa_senate.py:29` |
| `convert_538_presidential` | One-shot | No | No | `convert_wiki:50`, `test_usa_presidential.py:33` |
| `convert_wiki_presidential` | One-shot (bs4) | No | No | `test_convert_wiki_presidential.py:30` |
| `convert_senate_current` | Retired one-shot | No | No | No code/tests; README:1191; `current_senate.py:6,211`; `export/legacy.py:29`; warning at own `:14-16` |
| `build_house_topojson` | Geometry | No | No | `test_usa_house_topojson.py:36`, README:1543, `export_elections.py:1087,1099` |
| `build_presidential_topojson`, `build_senate_topojson` | Geometry | No | No | Nothing |
| `house_2026_sources` | Builder table | No | No | `build_house:61`, `test_usa_house_topojson.py:35`, README:1536 |
| `check_redistricting` | Analysis CLI | No | No | `test_usa_house_topojson.py:37,442`, README:1547,1552 |

## scripts/us_results/
All live in-process from the console; none reachable from rebuild or `import_all.sh`. `cli.py` is the only `__main__` (`:1122`). Console imports (`console/blueprints/us_results.py`): `adapters:62`, `cli:63`, `convert:82`, `current_senate:88`, `fetch:93`, `races:94`, `sources:95`. `__init__.py` is a marker. Internal: `cli.py:64,65,76,82-84`; `convert.py:67-69`; `adapters.py:36,44`; `fetch.py:36`; `races.py:21`. External: `races.py:20` imports `polls.importers.us.us_geography`. Tests: `test_us_results_{fetch,cli,adapters,convert,current_senate}.py`, `test_console_us_results.py:28-38`. UI: `home.html:36`.

## Path couplings
- **sys.path:** `cli.py:57-60`; `convert.py:71-73`; `current_senate.py:58-60`; `migrate_us_electoral_votes.py:19-22`; importers `house:29-30`, `senate:33-34`, `presidential:27-28`; own-dir at `convert_538_senate:40`, `convert_538_presidential:37`, `convert_wiki:48`, `convert_senate_current:33`, `build_house:60`.
- **Depth-sensitive:** `parents[3]` at those three importers (`:29`, `:33`, `:27`); `parents[2]` at `cli.py:54`, `convert.py:71`, `current_senate.py:56`.
- **String paths:** `rebuild_database.py:34,35,51-53,161`; `migrate_us_electoral_votes.py:19`; `cli.py:57,103`; `current_senate.py:58,70`; `test_us_results_current_senate.py:766` pins the folder name "usa". `importers_registry.py` and `runner.py` have nothing US; `paths.py:19` is indirect only.
- **Tests by spec_from_file_location:** `test_usa_house:17,30-31`, `test_usa_senate:16,28-30`, `test_usa_presidential:18,32-34`, `test_us_electoral_votes:12,25`, `test_convert_wiki_presidential:17,30`, `test_usa_house_topojson:19,35-37`. `test_presidential_ev_consumers:21-25,35` uses sys.path + bare import. Implicit path: `test_us_results_convert:37`, `test_console_us_results:48-49`. Cross-test imports: `test_us_results_convert:35-36,250`.
- **mypy.ini:** `:5-6` comment, `:7` exclude, `:8` mypy_path, `:71` `scripts.*` ignore.
- **Docs:** README:31,594,608-609,709,1191-1196,1498,1536,1543,1547,1552; 538 prose at 1480,1501,1578. No README mention of `convert_538_*`, the wiki converter, or the senate/presidential builders. No references in AGENTS.md, `server.sh`, `.vscode`, `.gitignore`, TODO. CI hits these only through `run_tests.sh`.
- **Data paths:** no usa script uses `__file__` for data; everything is CLI args. `__file__`-relative: `cli.py:103`, `current_senate.py:67-70`, `rebuild_database.py:35` (glob `:140`).

## Import and run order
- `us_results.py:63` before `:105`; `cli.py:58-60` before `:62-101`; `test_us_results_convert:17` before `:37`; `test_console_us_results:29` before `:48-49`.
- `us_import.py:18-20` has no path setup. `current_senate.py` adds only usa and assumes `data/`. `convert.py:67` also assumes `data/`.
- If `convert` is imported first, usa stays last on the path. Harmless: no usa module name collides anywhere in `data/`.
- `_load` re-executes and overwrites `sys.modules`, so identity depends on collection order.
- Rebuild: `migrate_us_electoral_votes` (`:245-248`) must precede presidential (`import_presidential:31,76`); specials follow regular (`:192-200`).
- Scripts run as `python <file>` with cwd `data/` (`rebuild_database.py:293-299`, `runner.py:53`), so `-m` needs `Step` changes or a kept bootstrap.

## electoral_votes and models/us
`electoral_votes.py` reads DB allocations at runtime (`:36-158`). `us_electoral_votes.py` is static seed data (`EV_BY_ERA:22`, `ELECTION_YEAR_INTERVALS:128`). Neither imports the other; `migrate_us_electoral_votes.py:25,37` uses both. Converters stamp descriptive EVs only.

`models/us/` has `__init__.py` but `models/` does not, and `data/models.py` wins, so `models.us` is not importable. Runners add their folder (`house:19`, `presidential:26`, `senate:45`); `_common.py:57-58` adds `data/`; `paths.py:24-26` runs them by path; mypy silences the names (`:56-66`). No new package can live under `data/models/`; `data/us/` is free.

## Suggested first step
Move `UNOPPOSED_NOMINAL_VOTES` into `scripts/us_results/convert.py` and drop `convert.py:71-75`, cutting the console's dependency on a 538 file.

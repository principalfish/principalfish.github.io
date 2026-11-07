# feat: import 2018–2026 US House and Senate results from NYT data

The 2026 midterms were held on 3 November and the US maps stopped at 2024. FiveThirtyEight, the old source of the House and Senate results, closed in 2025. This branch builds a re-runnable toolchain on the New York Times' static results JSON (AP calls) for the House and Senate, 2018–2026. It imports and publishes the data, including the provisional 2026 count.

## What changed

**Results pipeline** (`data/scripts/us_results/`):
- an NYT fetcher with a local snapshot cache (`NYT_CACHE_DIR`), restricted to the static CDN over https;
- schema adapters for the four NYT JSON formats used from 2018 to 2026, and a converter into the existing `data/old_data/files/usa/` format;
- importer extensions, and a `cli.py` with `fetch`, `convert`, `import`, `all` and `current-senate`, each with a dry run and a diff report.

**Per-seat status:** a new `election_seat_status` table (called, % counted, same-party, deciding round, Senate class) and `elections.source_note`, added by `scripts/migrate_add_us_result_status.py`. Uncalled races keep the votes counted so far and aren't given to the leader. Runoffs and ranked-choice races keep the deciding round.

**Specials:** a standalone special takes the state's slot. A special that clashes with a regular race is folded into its parent election's `specials` list.

**Console:** an "Import US Results (NYT)" page with fetch, preview and confirm, plus a separate "Rebuild Current Senate" action.

**Maps:** map 24 (2026 House) is stitched from the Census cd119 file plus the Census 120th-Congress files for the nine redrawn states (AL, CA, FL, LA, NC, OH, TN, TX, UT). Map 25 (the 2012 cycle) serves 2018 and 2020. `check_redistricting.py` scores a map against NYT county results, and it flags exactly those nine states.

**Front end:**
- **Uncalled seats:** drawn hatched in the leader's colour, or neutral when nobody leads. They count on their own "N uncalled" line and are left out of the seat ±.
- **Source line:** shown under the map.
- **Popups:** show the counted share, the same-party note and the deciding round.
- **Senate specials:** appear in popups and the chamber tally.

**Comparisons:**
- **House:** each election compares with the previous House election across district maps. The totals cover the whole comparison chamber, so reapportionment years add up.
- **Senate:** each election compares with the same class six years earlier (2026 → 2020, 2024 → 2018). The totals cover matching states only. Off-cycle specials carry an embedded previous same-class result (`pv`) for their popups.

**Predict:** rolled over to 2028. The House and Senate baselines are the 2026 results, and uncalled baseline seats are projected from their leader. The Senate baseline overrides (FL, OH Class 3) are keyed by class.

**Current Senate:** derived from the 2026 results plus 65 holdovers. A state with one uncalled senator takes its called senator's colour.

**Election lists:** sorted newest first in code. The House reads 2026, Predict 2028, 2024 and older. The Senate reads 2026, Current Senate, Predict 2028, 2024 and older. The page now opens on the 2026 Senate election.

**Forecasts switched off:** the House and Senate "2026 Prediction" forecasts are switched off by `EXPORT_US_CHAMBER_FORECASTS` (off) and their data files are deleted. The presidential forecast is unchanged. Old `current-us-*` links fall back to the 2026 result.

## Data

The live database was migrated and the 2018–2026 House and Senate results imported (2026 is provisional: 22 House and 2 Senate seats uncalled). The Current Senate was rebuilt and the US page exported. The commits `bb86f47e`, `cfc7f3c5`, `c32e7ef2`, `f54c40bd` and `3efe8c83` carry that data:
- **2020–2024 files:** the 538-sourced files are rewritten from NYT. No winner changes, and the known differences (Alaska 2022 ranked-choice final round, Osborn in Nebraska 2024) are as expected.
- **Restore point:** `/home/philiph/dbs/elections.pre-us-nyt-import-20261107.db`.
- **UK:** the export also rewrote the UK `prediction-simulation.json`. That change came from a newer UK model run in the shared database, so it was discarded.

## Check by hand

- **The map in a browser:** Phil checked the served page. Worth a second look: the hatched uncalled seats, the source line, the Senate ± against 2020, and the election list order.
- **Alaska 2026 Senate:** shown through a one-seat override (`data/scripts/export/seat_overrides.json`) with NYT's candidate rows. Otherwise the same-party merge would show "Dan Sullivan 107,171". It switches itself off with a warning if the live counts move. Delete it once the ranked-choice final round is imported.
- **2026 is provisional:** re-run the import with `--refresh` as counts finish (README section 12).

## Review

- **Self review:** one `/review self` pass covered the branch at `b51ab61c` (pieces 1–11). It verified 7 findings; 4 were fixed in `98269358` (Senate class years after the 2026 cycle, and three missing tests), and 3 were skipped by choice (a deferred archived-forecast gap and two style conventions that master also breaks).
- **Not covered:** pieces 12–14 (list order and forecasts off, Senate comparisons, the Alaska override) came after that review. They only had the builder's per-piece correctness, style and coverage checks.
- **Merging with #74:** this branch conflicts with `poll-errors-simulation` (#74) in `README.md`, `data/models.py`, the export payload and export files and their tests, `shell.html` and the bundles. Rebuild the bundles rather than hand-merging them.

## Out of scope

Governors, presidential sources, 2028 forecast models, and scoring the 2026 forecasts against the result.

Closes #79

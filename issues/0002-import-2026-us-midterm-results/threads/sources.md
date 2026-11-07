# Source probe: NYT and BBC (2026-11-07, curl from the dev machine)

## NYT
- The HTML pages (`www.nytimes.com/interactive/2026/11/03/us/elections/results-{house,senate}.html`) return 403 behind a DataDome captcha. Not bypassed.
- The data files load openly from the CDN, with no challenge:
  - `https://static01.nyt.com/elections-assets/pages/data/2026-11-03/results-house.json` (10.2 MB)
  - `https://static01.nyt.com/elections-assets/pages/data/2026-11-03/results-senate.json` (1.4 MB)
  - The 2024 equivalent also exists (`.../2024-11-05/results-house.json`), so this is a stable pattern and could back-test a converter against the known 2024 file.
- Shape: `races[]`. House has 436 entries: 435 districts plus a `US-national` popular-vote race (D 63.6M of 114.2M counted, eevp 94%). Senate has 35: 33 regular plus `FL-S_W-S` and `OH-S_W-S` ("Special General").
  - Race: `nyt_id` (e.g. `TX-G-H-28-2026-11-03`), `seat_number`, `type`, `race_call_status` (`called` / `too_early` / `runoff`), `outcome.won` (candidate ids), `outcome.called_by` (`ap`), `certified`, `uncontested`, `tabulation_method`, `rounds` (RCV), `candidate_metadata` (name, party code DEM/REP/..., incumbent).
  - `reporting_units[]`: the state-level unit carries district totals, `eevp` (estimated expected vote %) and `total_expected_vote`. County, city and other sub-units are there too (3,737 county units across the House).
  - Candidates per unit: `{nyt_id, votes.total, leader}`.
- Status as of `resultsUpdatedAt` 2026-11-07T07:26Z: House has 413 called, 21 `too_early`, 1 `runoff` (LA-5; Louisiana ran a jungle "Nonpartisan Election" in 6 districts). Senate has 33 called; AK and MS are `too_early`.
- Uncontested: 5 House races.
- Credit: calls are by AP. The data is NYT/AP copyright; reuse terms are unchecked.

## BBC
- `https://www.bbc.co.uk/news/election/2026/us/results` is an article (Optimo asset) with no results data of its own. `/house` and `/senate` return 404.
- Its results are external embeds of AP's customer interactives (`interactives.apelections.org/.../organization-layouts/published/108620/...`), i.e. AP data under the BBC's licence. Not a usable source.

## NYT per-state files (follow-up probe)
- The national `results-senate.json` is state-level only (35 state units, no counties).
- Per-state files exist with county units, precincts, timeseries and vote-type breakdowns:
  - `.../2026-11-03/results-<state>-us-senate.json` (e.g. `results-texas-us-senate.json`, 7.2 MB, 254 counties)
  - `.../2026-11-03/results-<state>-us-senate-special.json` (Ohio, Florida)
  - `.../2026-11-03/results-<state>.json` (state overview, 3.2 MB for Texas)
- Not needed for the state-level Senate map; useful only for county-level work later.

## Past years on NYT (probe 2026-11-07)
| Year | URL pattern | Schema |
|-|-|-|
| 2026 | `static01.nyt.com/elections-assets/pages/data/2026-11-03/results-{house,senate}.json` | A (races[], reporting_units, candidate_metadata) |
| 2024 | `static01.nyt.com/elections-assets/pages/data/2024-11-05/results-{house,senate,president}.json` | A (national file has district totals only, no counties) |
| 2022 | `static01.nyt.com/elections-assets/2022/data/2022-11-08/results-{house,senate}.json` | A (older variant of the same family) |
| 2020 | `static01.nyt.com/elections-assets/2020/data/api/2020-11-03/national-map-page/national/{house,senate,president}.json` | B (data.races[], candidates, counties, eevp) |
| 2018 | `int.nyt.com/applications/elections/2018/api/1/races/2018-11-06.json` | C (one file, 555 races incl. governors/measures) |
| 2016 | not found (`int.nyt.com/.../2016/api/1/races/2016-11-08.json` 404s) | |

The repo already holds House 2022 and 2024, and Senate 2020, 2022 and 2024 (538 sourced, `old_data/files/usa/`). Missing: House 2018 and 2020, Senate 2018. House 2018 and 2020 used the 2012-cycle districts (plus PA 2018 and NC 2020 court redraws), which map 21 doesn't have.

# Election-map manifest reference

Both maps pages use this contract: the [UK manifest](map-modes.json) and the
[US manifest](../../uselectionmaps/data/map-modes.json). Edit their hand-authored
[UK shell](map-modes-shell.json) or
[US shell](../../uselectionmaps/data/map-modes-shell.json), then regenerate the
manifests with the [exporter](../../data/scripts/export_elections.py). See the
[export runbook](../../README.md#10-static-election-map-export-manifest--files).

The shells supply page branding, parliament features, map options, party aliases,
and the preferred default election. The exporter assembles election entries,
file paths, party metadata, and region metadata, and transforms the shell inputs
described below. Generated manifests have eight top-level keys:
`defaultElection`, `elections`, `misc`, `parliamentFeatures`, `mapModes`,
`partyKeyAliases`, `files`, and `parties`.

The shared [engine](../../electionmapslogic/app.js) loads the page's
`data/map-modes.json` once during `initPage()`. `initState()` calls
`manifest.init()`, whose private hydration builds runtime lookups and fills
missing optional configuration. These lookups are not serialized JSON fields.

## Files and metadata

Paths in `files` are relative to the page's data directory: `maps/...` on the UK
page resolves under `/electionmaps/data/`, and on the US page under
`/uselectionmaps/data/`.

| Field | Description |
|-|-|
| `files.elections.mapsById` | Map ID (a JSON object key) → TopoJSON path, e.g. `"2": "maps/map-2.topo.json"`. Each map identifies a boundary set. |
| `files.elections.electionsById` | Election ID → results path, e.g. `"current-prediction": "results/prediction-simulation.json"`. |
| `files.meta` | Parliament key → prediction metadata path. The engine reads `latest_poll_snippet` for model-election and poll-tracker subtitles. |
| `parties` | Array of party records: numeric `id`, canonical string `key`, display `name`, and hex `colour`. Numeric IDs in result files resolve through this list. |
| `partyKeyAliases` | Alias → canonical party key. String party references are lowercased and stripped of non-alphanumerics for alias lookup; for example, `"reformuk": "reform"`. |
| `mapModes[mapId].regions` | Array of region records with numeric `id` and display `name`, normally generated from the database. Used to resolve region IDs and label filters. |

Hydration derives `manifest.partiesById` (a Map from numeric ID to party record),
`manifest.partiesByKey` (an object from key to record), `manifest.regionsById`
(a Map from numeric ID to normalized region key), and
`manifest.regionsByMapId` (an object from map ID to region records).
`manifest.resolveElectionFiles()` looks up an election's `mapId`, `id`, and
optional `comparisonElectionId` in `files.elections`.

## Page branding: `misc`

| Field | Description |
|-|-|
| `title` | Page brand, currently `"UK Election Maps"` or `"US Elections"`; defaults to `"Election Maps"`. The H1 shows `<brand> · <parliament label>`, and the browser title shows `<election or view> \| <parliament label> \| <brand> \| Principal Fish`. |
| `parliamentTabs` | Ordered array of `{ "parliament": "westminster", "label": "Westminster" }` records. Keys match `elections[].parliament`; labels also supply parliament display names. Without a matching tab label, the engine capitalizes the key's first letter. |

Feature modules are activated from the union of the `features` arrays for
parliaments listed in `parliamentTabs`, limited to modules registered by the page.
Individual parliament configuration then controls the navigation offered for that
parliament. Keep each page's static `<title>` and `<h1>` in `index.html` aligned
with its brand: they are placeholders shown before the manifest loads.

## Election entries: `elections`

The ordered election array supplies the left-hand navigation, with hidden entries
omitted. Core fields are `id`, `name`, `type`, `mapId`, and `parliament`.

| Field | Description |
|-|-|
| `id` | Unique string ID, also used by file mappings, defaults, comparisons, and prediction anchors/baselines. |
| `name` | Election display name. |
| `type` | Election/model category recorded by the exporter; see current values below. Display behavior uses explicit flags and map/parliament configuration. |
| `mapId` | Numeric ID matching `files.elections.mapsById` and `mapModes`. |
| `parliament` | Parliament key. Current UK keys are `westminster` and `holyrood`; current US keys are `us_house`, `us_presidential`, and `us_senate`. These are configured examples, not a fixed schema enum. |
| `comparisonElectionId` | Optional ID of the comparison results. Enables aggregate deltas and, where supported, per-seat comparisons. |
| `byElectionSeats` | Optional array of seat names; marks by-election seats and changes the Gains filter's label to “By-elections”. |
| `model` | Optional boolean. Marks a prediction election for presentation, prediction metadata, and countdown eligibility. Raw vote counts are suppressed, while vote shares can still be displayed. |
| `referendum` | Optional truthy flag or configuration object. Enables referendum presentation, hides per-seat comparison controls, and shows the data-info button. The object configures the referendum choropleth (below). |
| `multiMember` | Optional boolean for chamber-composition snapshots such as Current Senate. Tallies individual members; hides vote count/share columns and vote/comparison-based map controls. |
| `upcomingElections` | Optional boolean enabling the next-election-year filter, populated from member data. Current Senate uses it alongside `multiMember`. |

Current category values are:

| Page | `type` values |
|-|-|
| UK | `uk_general`, `holyrood_general`, `model_uns`, `holyrood_uns`, `eu_referendum` |
| US | `us_house`, `us_house_model`, `us_presidential`, `us_presidential_model`, `us_senate`, `us_senate_model` |

A type label alone does not enable a prediction engine or referendum UI. For
example, the UK forecast carries `"type": "model_uns"` and `"model": true`;
the EU referendum carries this configuration:

```json
{
  "referendum": {
    "highParty": "leave",
    "lowParty": "remain",
    "threshold": 50,
    "metricLabel": "Leave vote share"
  }
}
```

`highParty` and `lowParty` select party shares/colours at the choropleth's ends;
`threshold` anchors its midpoint and `metricLabel` labels the measure. The values
above are also the runtime defaults when the object omits them. Vote-share
visibility additionally depends on the vote-total view: a combined AMS `all`
view has no single share column. Predict mode suppresses raw counts as well.

### Default selection: `defaultElection`

`defaultElection` is an election ID. Its parliament supplies the fallback when
`?parliament=` is absent or names a parliament with no elections on this page.
Within the selected parliament, the engine chooses:

1. An explicitly requested matching `?election=` ID, including a hidden entry.
2. The prediction anchor, if it exists and is visible.
3. The visible `defaultElection`, if it belongs to that parliament.
4. The first visible election in manifest order.
5. The first election, if every entry is hidden.

Thus `defaultElection` is a fallback, rather than an unconditional landing
election. `hiddenElectionIds` removes entries from navigation and automatic
selection while keeping them available to explicit URLs and prediction loading.

## Parliament configuration: `parliamentFeatures`

This object is keyed by parliament. Current enabled features are:

| Parliament | `features` |
|-|-|
| `westminster` | `predict`, `pollTracker`, `postcode` |
| `holyrood` | `postcode` |
| `us_house`, `us_presidential`, `us_senate` | `predict` |

Holyrood retains prediction configuration and hides
`current-holyrood-prediction`, but currently has no Predict navigation link.
Configuration retained under `predict` does not itself enable that feature.

| Field | Description |
|-|-|
| `features` | Array of module names. `predict` and `pollTracker` enable their navigation links; `postcode` activates map-specific postcode lookup. |
| `hiddenElectionIds` | Optional array of election IDs to omit from navigation and automatic selection. |
| `predictAnchorElectionId` | Current election/forecast anchor, also preferred during automatic selection. “Use current forecast” is available when this entry has `model: true`. |
| `predictBaselineElectionId` | Election whose result files supply the prediction baseline. |
| `nextElectionYear` | Target year used by Predict labels and model construction. For Senate it also selects the export's live special elections. |
| `nextElectionDate` | Optional countdown start-of-election date or timestamp; omitted or `null` disables the countdown. |
| `nextElectionLabel` | Election name displayed in the countdown. |
| `polltrackerDataPath` | Poll-tracker data path relative to the page's data directory. Currently configured for Westminster. |
| `polltrackerDefaultParties` | Array of numeric party IDs checked initially in the poll tracker. |
| `uncertaintyDataPath` | Optional path, relative to the page's data directory, of the current model run's uncertainty summary (`pf-uncertainty-v1`, below). Currently configured for Westminster as `results/prediction-uncertainty.json`. The current model run is the latest-dated one (by the date in its name, e.g. `UNS 2026-10-12`, then by database id; runs whose name has no date are never chosen), so re-running an older date does not replace it. The export writes that file only when the current run has full regional detail, and removes a stale one otherwise, so a missing file means "no uncertainty data": display falls back to the point projection. |
| `seatTrendsAvailable` | Optional flag enabling a seat-popup trends chart; currently configured for US presidential maps. |
| `seatTrendsDataPath` | Trends JSON path used by that chart, relative to the page's data directory. |
| `predict` | Nested prediction-model and input-grid configuration, described below. |

The countdown appears in Predict mode or on an election with `model: true`, never
in the poll tracker, and hides when its date passes. A bare ISO date such as
`"2031-05-01"` means UTC midnight. A timestamp such as
`"2026-11-03T00:00:00-05:00"` selects a precise instant. The displayed date is
formatted in UTC: midnight at a positive offset, such as `+01:00`, displays the
previous day, so use a bare date when that is unsuitable. The text is
`12d 3h 4m 5s · <label> · 3 November 2026`.

### Uncertainty summary: `pf-uncertainty-v1`

The file at `uncertaintyDataPath` describes the run exported as the model
election (for Westminster, `current-prediction`): the latest-dated model run. Vote shares are percentage
points to 1 dp; probabilities are fractions from 0 to 1 to 4 dp; seats are
integers. Parties and regions are keyed by numeric ID strings, as in `parties`
and `mapModes[mapId].regions`.

| Field | Description |
|-|-|
| `schema` | `"pf-uncertainty-v1"`. |
| `as_of_date`, `election_name` | The model run, e.g. `"2026-10-12"`, `"UNS 2026-10-12"`. |
| `simulations`, `interval` | Simulation count and interval level (`0.9`: ranges run from the 5th to the 95th percentile). |
| `basis` | One-line reading guide to show with the figures, e.g. "Nowcast: if an election were held today. 90% ranges and probabilities from 10,000 simulations, conditional on current polls and the model's error assumptions." |
| `national.parties[id]` | `v: [lo, hi]` vote-share range; `s: [lo, median, hi]` seats; `pm` P(majority); `pl` P(largest party, ties split); `hist: {start, counts}` seat histogram, `counts[i]` simulations with `start + i` seats (counts sum to `simulations`). |
| `national.hung` | P(no party reaches a majority). |
| `regions[id].parties[id]` | `v` and `s` as nationally, plus `pm` (more than half the region's seats) and `pl` when the run stored them. Parties whose range stays below 0.5 points and that never win a seat there are omitted. |
| `regions[id].hung` | P(no party holds more than half the region's seats), when stored. |

The model election's result file (`pf-results-v4`) carries the matching
per-seat ranges: a seat may have `u: [[partyId, lo, hi, win], ...]`, one row
per `p` party in `p` order, with the share range in points and the win
probability as a fraction. A `p` party with no row never won the seat in any
simulation and its range stays below 0.5 points, so none was stored; a seat
without `u` has no stored ranges at all.

### Nested `predict` options

| Field | Description |
|-|-|
| `model` | Engine selector: `fptp` for uniform swing, `ams` for constituency swing plus D'Hondt list allocation, or `senate` for seats-up projections and full-chamber composition. |
| `title` | Input-grid heading; defaults to `"User Input"`. |
| `modelledPartyKeys` | Canonical party keys whose baseline shares and swings the engine models. |
| `aggregate` | Optional synthetic aggregate row: `key`, display `label`, and `excludeRegions` (normalized region keys). Westminster's England row excludes Scotland, Wales, and Northern Ireland; US models use a national row. Omitted or `null` means no aggregate row. |
| `gridSections` | Ordered grid layout. Each section has `id` and ordered `columnKeys`. `containsAggregate` includes the aggregate and its expandable member rows; `extraRegionKeys` and `regionKeys` append explicit region rows in order. `blankRegionHeader` hides the “Region” heading. |
| `virtualColumns` | Virtual column key → region-to-party mapping. Westminster's `nat` maps `scotland` to `snp` and `wales` to `plaidcymru`; regions without a mapping have a blank cell. |
| `virtualColumnMeta` | Virtual column key → optional `title` tooltip and `swatchClass` CSS class. |
| `regionLabelOverrides` | Normalized region key → shorter label for the prediction grid only. |
| `tabs` | Ordered `{ "key": "constituency", "label": "Constituency" }` records. AMS uses `constituency`/`list` input tabs; Senate uses `seatsup`/`chamber` output tabs. The first tab is initially active. |
| `chamberElectionId` | Senate-only election ID for the full-chamber snapshot, currently `current-senate`. Carried-over members are merged with projected seats-up winners. |

## Map configuration: `mapModes`

Keyed by map ID strings, such as `"2"` or `"23"`. Most options are optional;
the current shells show which each map uses.

| Field | Description |
|-|-|
| `name` | Descriptive map name, e.g. `westminster-2024` or `us-senate-2024`. |
| `regions` | Region records described above, normally generated during export. |
| `voteTotalsViews` | Ordered `{ "id": "all", "label": "Overall" }` records defining vote-total tabs. Hydration supplies `[{ "id": "all", "label": "All" }]` when missing or empty. Holyrood configures `all`, `constituency`, and `list`. |
| `hiddenVoteTotalsParties` | Optional party-key array to omit from the vote totals table, e.g. `alba` on Holyrood's 2026 map. |
| `listSeatPattern` | Optional case-insensitive regex string identifying AMS list seats. Holyrood uses `"\\bList\\s+\\d+$"` in JSON; this is also the runtime fallback, including for invalid patterns. |
| `postcode` | Optional postcode lookup configuration, described below. A map without it has no postcode search. |
| `projection` | `albersUsa` selects D3's US projection; otherwise the renderer uses Mercator. |
| `tally` | `electoralVotes` weights presidential winner totals by electoral votes and labels the tally column “EV”; otherwise winners are tallied as seats (or members for a composition snapshot). |
| `neutralFill` | Fill colour for topology areas with no active result, used for uncontested Senate states. Falls back to the `others` party colour. |
| `hideMajority` | Suppresses the subtitle's majority summary for single-winner elections; multi-member chamber summaries are handled separately. |
| `senateClassNextElection` | Generated class → next-election-year object. Sets each chamber member's next-up year from their class for cycle filtering and popups. |
| `senateSpecialElections` | Array of `{ seat, class, year, baselineElectionId }` records for live off-cycle races. Overrides the named member's cycle and provides the baseline for Senate prediction. |

`postcode` has `endpoint` (the URL prefix to which the encoded postcode is
appended) and `resultProperty` (the constituency field under the response's
`result`). Optional `seatRenames` maps API names to the map's seat names;
`boundaryWarning: true` enables the page's boundary-warning control. Lookup also
requires the page's `postcode` feature module. The US maps retain
`postcodeSupported: false`, but the current engine does not read this field;
availability is determined by the feature and `postcode` configuration.

An additional supported option, absent from current map configurations, is
`seatComparison: false`. It hides per-seat vote-share-change and Gains controls
outside Predict mode; aggregate vote-total deltas still depend on comparison
data. Referendums suppress per-seat comparisons regardless of this option.

### Shell inputs transformed during export

These are configuration inputs, rather than keys to add to a generated manifest:

| Shell field | Exported result |
|-|-|
| `mapModes[mapId].regionNameOverride` | Database region name → display name overrides for generated `regions`. The override object is removed. Existing explicit shell `regions` are retained for compatibility. |
| `mapModes[mapId].senateClassCycle` | Durable `{ "base": { "1": 2018, "2": 2020, "3": 2022 }, "period": 6 }` cycle becomes `senateClassNextElection`, resolved against the export year. The cycle input is removed. |
| `mapModes[mapId].senateSpecialElections` | Only valid records for `parliamentFeatures.us_senate.nextElectionYear` survive. A target year earlier than the export year produces no live specials. Each record needs integer `class`/`year` and non-blank `seat`/`baselineElectionId`. |

The generated Senate manifest currently has next-up years `1: 2030`, `2: 2026`,
and `3: 2028`, with Florida and Ohio Class 3 specials in 2026 using
`2022-us-senate` as their baseline. Regeneration resolves this time-dependent
configuration afresh; edit the shell's durable inputs rather than the generated
years.

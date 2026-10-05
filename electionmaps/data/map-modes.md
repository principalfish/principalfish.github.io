# map-modes.json

The manifest file for the election maps application. Loaded once on boot by `initElectionData()`.

---

## `defaultElection`

String ID of the election to load when the URL has no `?election=` param. With no `?parliament=` param either, its parliament is the tab the page opens on. A `?parliament=` with no elections in this manifest (e.g. another page's parliament) also falls back to its parliament.

---

## `misc`

Page-level settings, copied from the page's `map-modes-shell.json`. Each maps page (`/electionmaps`, `/uselectionmaps`) has its own manifest, so these are the only place the page's brand and tabs are set; the page HTML holds no config.

| Field | Description |
|---|---|
| `title` | Page brand, e.g. `"UK Election Maps"` (default `"Election Maps"`). Shown in the H1 (`<title> · <parliament label>`) and the browser-tab title (`<election or view> \| <parliament label> \| <title> \| Principal Fish`). Keep the page's static `<title>` and `<h1>` in `index.html` in step, since they show before the manifest loads. |
| `parliamentTabs` | The page's parliament tabs, in display order. Each has `parliament` (key matching `elections[].parliament`) and `label` (tab text, also used as the parliament's display name; without a tab the key is capitalised). Also decides which features the page switches on: only parliaments listed here contribute their `parliamentFeatures[].features`. |

---

## `settings`

Static lookup tables used during data loading and display.

### `settings.mapFilesById`

Maps a numeric map ID to its TopoJSON file path under `data/`. Each map represents a distinct set of constituency boundaries (e.g. Westminster pre-/post-2024 boundary changes, Holyrood 2021/2026 boundaries).

### `settings.dataFilesByElectionId`

Maps an election ID to its results data file path under `data/`. Used by `resolveElectionFiles()` to find the right JSON to load for a given election.

### `settings.parties`

Master list of all parties that can appear in results data. Each entry has:
- `id` — numeric ID used in raw results files
- `key` — string key used throughout the UI (`"labour"`, `"snp"`, etc.)
- `name` — display name
- `colour` — hex colour for map fills, chart lines, etc.

Loaded into `state.manifestPartiesById` (Map keyed by `id`) and `state.manifestPartiesByKey` (Map keyed by `key`) during `hydrateManifestSettings()`.

### `settings.regionsByMapId`

Maps a map ID to its list of regions. Each region has a numeric `id` and a display `name`. Loaded into `state.manifestRegionsById` and `state.manifestRegionsByMapId` during `hydrateManifestSettings()`.

---

## `elections`

Ordered list of election entries shown in the left-hand election list. Each entry has:

| Field | Description |
|---|---|
| `id` | Unique string ID. Referenced by `defaultElection`, `comparisonElectionId`, `predictAnchorElectionId`, `predictBaselineElectionId`, and `dataFilesByElectionId`. |
| `name` | Display name shown in the election list. |
| `type` | Controls UI behaviour — see types below. |
| `mapId` | Numeric ID into `settings.mapFilesById` and `settings.regionsByMapId`. |
| `parliament` | `"westminster"` or `"holyrood"` — determines which parliament tab the election appears under. |
| `comparisonElectionId` | Optional. ID of the election to load as the comparison (delta columns in vote totals, swing arrows on map). |
| `byElectionSeats` | Optional array of seat names that are by-elections; changes the Gains filter button label to "By-elections". |

### Election types

| Type | Behaviour |
|---|---|
| `uk_general` | Standard Westminster general election. Shows vote totals. |
| `holyrood_general` | Standard Holyrood general election. Shows constituency and list vote total tabs. |
| `model_uns` | Westminster UNS model prediction. Vote totals are hidden. |
| `holyrood_uns` | Holyrood UNS model prediction. Vote totals are hidden. |
| `eu_referendum` | EU referendum result. Hides vote share change choropleth option; shows data info button. |

---

## `parliamentFeatures`

Per-parliament feature flags and predict mode configuration.

```json
"westminster": {
  "features": ["predict", "pollTracker"]
},
"holyrood": {
  "features": ["predict"],
  "predictAnchorElectionId": "current-holyrood-prediction",
  "predictBaselineElectionId": "2021-holyrood-2026"
}
```

| Field | Description |
|---|---|
| `features` | Array of feature strings. `"predict"` shows the Predict button; `"pollTracker"` shows the Poll tracker button. |
| `predictAnchorElectionId` | The election that acts as the "current simulation" for the Apply prediction button. Also used as the default election when no URL param is present. |
| `predictBaselineElectionId` | The election whose vote shares are used as the predict grid baseline. |
| `nextElectionYear` | Year of the next election. Used in the "Predict YYYY" labels; for `us_senate` the export also uses it to pick which Senate special elections to include. |
| `nextElectionDate` | Optional. When the next election starts, which turns on the countdown. Either a bare ISO date (`"2031-05-01"`, read as UTC midnight) or a timestamp with an offset for a precise moment (`"2026-11-03T00:00:00-05:00"` for midnight US Eastern). The date in the countdown text is shown in UTC, so a timestamp must fall on the intended day in UTC: midnight at a negative offset does, but midnight at a positive offset (e.g. UK summer time, `+01:00`) shows the day before. `null` or omitted = no countdown. |
| `nextElectionLabel` | Name of the election shown in the countdown text, e.g. `"US Senate election"`. |

The countdown (`"12d 3h 4m 5s · <label> · 3 November 2026"`) shows only when `nextElectionDate` is set, and only in Predict mode or on a prediction election (`model: true`). It never shows in the poll tracker, and hides itself once the date passes.

---

## `mapModes`

Per-map-ID display configuration. Keyed by the numeric map ID.

| Field | Description |
|---|---|
| `seatViews` | Tabs shown in the seat list panel. Each has `id` and `label`. For Westminster this is just `seats`; for Holyrood it is `constituency`. |
| `voteTotalsViews` | Tabs shown in the vote totals panel. Westminster has a single `all` tab; Holyrood has `all`, `constituency`, and `list`. |
| `hiddenVoteTotalsParties` | Optional array of party keys to hide from the vote totals table for this map (e.g. Alba is hidden on the 2026-boundary Holyrood map where it has no list presence). |
| `listSeatPattern` | Optional regex string (compiled case-insensitively) identifying regional-list (AMS top-up) seats by name. Omit for first-past-the-post maps. Holyrood sets `\bList\s+\d+$` to match the `<region> List <n>` naming convention; an AMS chamber labelling list seats differently overrides it here rather than in code. |

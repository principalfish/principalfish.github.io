# Shared election-maps engine

Shared election-maps engine (D3 + TopoJSON): core modules (`state`/`dom`/`utils`/`files`/`app`), `features/` (predict + poll tracker, opt-in per page), shared app markup (`shell.html` + opt-in `fragments/`, injected per page by `shell-loader.js`), `maps.css`, `mobile-sidebar`, `tests/` = Vitest specs.

## Manifest contract used by webpage

Each page loads its own generated manifest:
[UK `map-modes.json`](../electionmaps/data/map-modes.json) or
[US `map-modes.json`](../uselectionmaps/data/map-modes.json). Both use the same contract:

- `defaultElection` and ordered `elections` entries with `id`, `name`, `type`,
  `mapId`, `parliament`, and optional behavior/comparison fields.
- `files.elections.mapsById`, `files.elections.electionsById`, and `files.meta`,
  with paths relative to that page's data directory.
- `parties`, `partyKeyAliases`, and `mapModes[mapId].regions` for party and region metadata.
- `misc`, `parliamentFeatures`, and `mapModes` for branding, features, prediction,
  and map options.

The [shared engine](app.js) initializes the manifest, derives
party/region lookups, and resolves results and topology through `files.elections`.
Edit each page's `map-modes-shell.json` for hand-authored configuration and
regenerate its manifest. See the [full manifest reference](../electionmaps/data/map-modes.md)
for supported fields, selection rules, and shell transformations.

## Results schema

- Exported result payloads use compact schema `pf-results-v4`:
	- top-level: `{"schema":"pf-results-v4","seats":[...]}`
	- seat keys: `n` (seat name), `r` (region), `w` (winner), `e` (electorate), `m` (majority), `t` (turnout), `p` (party rows)
	- party row: `[partyKey, total, name]`
	- model run only, optional: `u` (per-party share range and win probability, aligned to `p`)
- The current Westminster model run also writes `results/prediction-uncertainty.json`
  (`pf-uncertainty-v1`: national and regional ranges, odds and seat histograms) when it
  has full detail; see the [manifest reference](../electionmaps/data/map-modes.md).
	- optional seat keys, emitted only when they apply (older payloads have none of them):
		- `ev`: Electoral College votes (presidential seats only)
		- `c`: `false` for an uncalled seat (absent when called)
		- `pc`: percent of the expected vote counted, to one decimal place; uncalled seats only, when known
		- `sp`: `true` when the top candidates share a party (top-two / jungle races, merged into one row)
		- `rd`: the decisive round, `"rcv-final"` or `"runoff"`, when the first round did not decide the seat
		- `pv` (US Senate seats and `specials` entries): `{w, p}`, the previous result for the same state and Senate class, so
		  an off-cycle seat can be compared with its own last race. `w` is the previous winner's party ref (omitted when
		  that result had none) and `p` its party rows `[[partyRef, votes], ...]`, without candidate names. It is the most
		  recent earlier `us_senate` election, regular or special, with a seat for that state and class (classes come from
		  `election_seat_status.senate_class`; a seat or earlier result with no class is skipped). Only seats whose class
		  differs from the election's regular class (the class most of its seats have) get it: the standalone specials
		  (Florida and Ohio 2026) and every `specials` entry. Regular seats are compared through the entry's comparison
		  election instead. Absent when no earlier election holds that state and class.
	- an uncalled seat's `w` is the party leading on votes; with no positive votes or a tie for the lead, `w` is omitted
	- optional top-level `specials` (US Senate): same-day special elections held alongside a state's regular race, one
	  `{n, cl, w, p, c, pc, sp, rd, pv}` per state, sorted by name. `cl` is the seat's Senate class; the other keys follow
	  the seat rules above, and there is no `r`. The special has no result file or manifest entry of its own.
- a party may appear in more than one `p` row of a seat. That happens only when a committed seat override splits a
  merged same-party result into its individual candidates (normally the export merges them into one row per party). The
  front end sums the rows for party totals, tallies and the ±, and, when it sees a repeated party, lists the individual
  rows (no per-row deltas) in the seat popup instead of the per-party rows, and the popup's majority (and the majority
  filters) and the second-place filter use the top two candidates rather than the top two party totals.
- Seat candidate overrides: `data/export/seat_overrides.json`, applied on every export by
  `data/export/seat_overrides.py`, replaces one uncalled seat's `p` with the individual candidates and sets `w` to the
  individual vote leader. It is keyed by election name (exact, e.g. `"2026 US Senate Election"`), then seat name
  (`"Alaska"`), each `{"w": partyKey, "p": [[partyKey, votes, name], ...]}` with rows in descending vote order (no tie for the top count) and `w` the party of the first row, the individual leader; party keys
  are resolved to manifest ids at export. It exists for the 2026 Alaska Senate seat, where Sullivan and Heikes (both
  Republican) merge into one row that outvotes Peltola although Peltola leads individually. Stale guard: the override holds
  static numbers, so it is applied only while the seat is still uncalled and each party's override rows sum to the seat's
  current merged party totals; otherwise the export prints a `WARNING` and keeps the merged rows (update or delete the
  entry; the export never fails over it). A malformed file stops the export with a clear error. Delete the Alaska entry once
  the ranked-choice final round is imported.
- Frontend loader supports both compact and legacy result formats.

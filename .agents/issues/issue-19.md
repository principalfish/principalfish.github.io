# master branch — misc fixes and test suite

## Changes

### 1. CLAUDE.md
- Migrated `AGENTS.md` to `CLAUDE.md` so Claude Code auto-loads project instructions at session start.

### 2. NI UUP slider bug fix
- **Bug:** UUP slider in Predict 2029 had no effect and started at 0% instead of ~15%.
- **Root cause:** `PREDICT_NI_PARTY_KEYS` used `'uup'` but the canonical normalized key (per `normalizePartyKey` and the DB export) is `'uu'`. So `buildPredictBaselineShares` looked up `seat.votes.uup` (always 0) and slider state was stored under a different key than the baseline.
- **Fix:** Changed `'uup'` → `'uu'` in `PREDICT_NI_PARTY_KEYS` in both `electionmaps.js` and `electionmaps.min.js`.

### 3. Predict mode: preserve zoom and update selected seat on submit
- **Bug:** Clicking Submit in predict mode reset the map zoom to default and lost the active seat highlight/popup.
- **Fix:**
  - Added `currentOpenSeatName` to track which seat popup is open.
  - Added `highlightSeat()` to `mapInteractionController` (highlights seat path without re-zooming).
  - Added `preserveTransform` option to `renderTopoMap` — reads current D3 zoom transform before re-render and restores it.
  - Added `preserveZoom` option to `renderMapWithViewState`, passed through to `renderTopoMap`.
  - `applyPredictModeProjection` now calls `renderMapWithViewState({ preserveZoom: true })` then re-renders the seat popup and re-highlights the seat path with updated projected data.

### 4. Reverted by-election UI
- Removed all by-election toggle UI from `electionmaps.js` and `index.html`:
  - `byElectionToggleButton`, state variables, `loadByElectionOverlayForElectionIfNeeded`, `normalizeByElectionOverlay`, `applyByElectionOverlay`, `syncByElectionToggleButton`.
  - BY-ELECTION badge in seat list and meta in seat popup removed.
  - `refreshElectionSeatStateAndRender` simplified back to no overlay logic.
- The data pipeline support (DB model fields, export script, `elections.json` manifest pointer) is retained.

### 5. Buy me a coffee styling
- Added `.pf-topbar-coffee-link` CSS rules to `site/topbar.css` (and rebuilt `topbar.min.css`) so the coffee link matches the contact link: same colour, font, weight, opacity, and underline-on-hover.

### 6. JS test suite
- Added `vitest` as a dev dependency; added `npm test` and `npm run test:watch` scripts.
- Created `electionmaps/core.js` — ES module exporting pure functions extracted from `electionmaps.js` (party/region normalization, seat utilities, election summary, seat normalization, predict region predicates, baseline share calculation). No DOM dependencies.
- Created `tests/core.test.js` (moved from `electionmaps/`) — 122 tests covering all exported functions, including regression tests for the `uu`/`uup` NI key bug.
- All 122 tests passing.

### 7. core.js / electionmaps.js unification
- `electionmaps.js` now imports all pure functions from `core.js` rather than duplicating them (~250 lines removed).
- `normalizeSeats` in `core.js` extended to handle legacy `seatInfo`/`partyInfo` format (used by `uk-general-2019-changed-boundaries.json`).
- `projectedSeatForPredictMode` call site updated to pass `predictRegionalSwingsByParty` explicitly; local definition and `resolvedPredictSwingValue` wrapper removed.
- Build switched from `terser` to `esbuild --bundle` for `electionmaps.min.js`, bundling `core.js` in and marking d3/topojson as external. Bundle is ~50.6kb.
- `tests/core.test.js` moved from `electionmaps/` to `tests/`; import path updated.

### 8. v1 predict URL encoding removed
- `encodePredictPayload` / `decodePredictPayload` (JSON+base64, v1) deleted from `core.js`; v1 was live for ~30 minutes before v2 was deployed, so no URLs in the wild.
- `buildPredictShareUrl` and `readPredictShareStateFromUrl` simplified to use v2 only.

### 9. UUP alias consolidation
- `'uup'` removed from `PARTY_LABELS` (was a duplicate label key alongside `'uu'`).
- `uup: 'uu'` added to `PARTY_KEY_ALIASES` so `normalizePartyKey('uup')` → `'uu'`.
- `uup` entry removed from `PARTY_COLOURS` in `electionmaps.js`.
- Canonical key remains `'uu'` (matches DB export).

### 10. Party ID migration (pf-results-v3)
- **Goal:** Replace hardcoded party label/colour constants with manifest-only lookups; switch results JSONs to use integer `party_id` from the DB.
- **core.js:**
  - Removed `PARTY_LABELS` export entirely.
  - Simplified `normalizePartyKey()` — removed `PARTY_LABELS` guard checks (they were pass-throughs; `return lower` fallback is equivalent).
  - Added `resolvePartyRef(ref, partiesById)` — resolves integer party_id or string key to canonical string key.
  - Updated `normalizeSeats(resultsData, partiesById)` — optional second param; integer refs in `w`/`p` are resolved via `partiesById` map.
- **electionmaps.js:**
  - Removed `PARTY_COLOURS` constant and `PARTY_LABELS` import.
  - `labelParty()` now returns manifest `name` or raw key (no JS fallback).
  - `colourParty()` now returns manifest `colour` or `'#9CA3AF'` (no JS fallback).
  - All `normalizeSeats()` call sites pass `manifestPartiesById` as second arg.
- **export script:**
  - Added `OTHERS_PARTY_ID = 7` constant.
  - Added `party_id_for_vote(vote)` — returns `vote.party.id` or `OTHERS_PARTY_ID` for independents.
  - `build_result_payload()` now groups by integer party_id; `w` and `p[0]` are integers.
  - Schema bumped `"pf-results-v2"` → `"pf-results-v3"`.
- **Results JSONs:** All 6 results files regenerated in pf-results-v3 format.
- **Tests:** Added `resolvePartyRef` tests and `normalizeSeats` integer-ref tests; 131 tests passing.
- **DB note:** Applied missing `elections.parent_election_id` and `elections.election_date` columns (via `ALTER TABLE IF NOT EXISTS`) to unblock export — these columns are part of the by-election schema and `migrate_add_election_parent_fields.py` exists for this purpose.

### 12. Fix run_retrospective_uns crash deleting all simulations

- **Bug:** Running `run_retrospective_uns.py` wiped all `model_uns` elections then crashed, leaving nothing.
- **Root cause:** `run_simulation()` returns 5 values but the call site at line 104 only unpacked 4 (`election_name, projected_votes, _, _`), causing `ValueError: too many values to unpack` on day 1 of backfill. Combined with `--reset-existing` defaulting to `True`, the DB was cleared before the crash.
- **Fix:** Changed unpack to `election_name, projected_votes, _, _, _` in `run_retrospective_uns.py`.
- **Fix 2:** `reset_existing_model_outputs` now takes `start_date`/`end_date` and scopes deletes to `Election.name >= "UNS {start_date}"` and `< "UNS {end_date+1day}"` (lexicographic range works because names are `UNS YYYY-MM-DD`). CSV rows outside the range are preserved; only matching rows are stripped.

### 11. Remove parent_election_id from codebase
- Removed `parent_election_id` and `election_date` columns from `Election` model in `data/models.py`, and the `parent_election` self-referential relationship.
- Removed `parent_election_id` and `election_date` params from `db.add_election()`.
- Removed `db.get_child_elections()` method entirely.
- Removed `parentDbId`, `date` from by-election export rows in `export_non_simulation_elections.py`; removed `parentElectionDbId`/`date` from manifest entries.
- Removed unused `iso_date_or_none()` function and `from datetime import date` import from export script.
- Deleted `data/scripts/migrate_add_election_parent_fields.py`.
- Removed `test_by_election_parent_and_date` and `TestGetChildElections` from `data/tests/test_elections.py`.
- 103 data tests pass.

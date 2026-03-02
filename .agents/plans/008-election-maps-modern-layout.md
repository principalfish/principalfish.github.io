# 008 Election Maps modern layout scaffold

## Status
- [x] Define target layout from screenshot and requirements
- [x] Build three-pane page structure (left elections, center map, right insights)
- [x] Add dedicated maps top bar and visual background shell
- [x] Add zoom/pan interaction scaffold for map viewport
- [x] Vendor referenced CDN scripts/CSS into local static assets
- [x] Remove emoji-style iconography from Guess The Year branding
- [x] Simplify homepage hero/title treatment
- [x] Restyle root 404 page to match current site visual system
- [x] Apply shared logo favicon and homepage card icon treatment
- [x] Add URL-param election switching with placeholder data contract
- [x] Validate interaction behavior and finalize documentation notes

## Scope
1. Retain core interaction model of the existing maps product.
2. Update styling and layout to a cleaner modern shell.
3. Keep the page static-hosting compatible while preparing for real map data wiring.

## Decisions
- Keep a left navigation rail for election/year selection.
- Keep a center zoomable map surface and basic map controls (reset, search, zoom).
- Keep a right insights rail with vote totals, seat list, and filter controls.
- Use a lightweight custom JS scaffold for pan/zoom now; this can later be swapped for `d3-zoom` while keeping the same DOM structure.
- Store external frontend dependencies under `site/vendor/` and reference them locally from pages.
- Use `election-maps/data/elections.json` manifest as the routing contract (`?election=` -> map/data file pair).
- Keep data files as placeholders for now; user-provided DB exports will replace them.

## Validation plan
- Verify three-pane layout classes exist and render in `election-maps/index.html`.
- Verify `site/election-maps.js` is loaded and updates zoom value.
- Verify responsive behavior at narrower widths.

## Validation notes
- Added exporter CLI targeting modes in `data/export_non_simulation_elections.py`:
  - `--election-name "..."` exports a specific election by exact name
  - `--current-simulation` exports the latest `model_uns` election
  - `--output-file /path/file.json` writes a single election payload directly to a custom file (requires one of the two targeting flags)
- Single-election mode now skips manifest rewrites by design to avoid clobbering `election-maps/data/elections.json` during ad-hoc exports.
- Focused CLI validation run:
  - `--help` shows new options
  - `--election-name "2019 General Election" --output-file ... --dry-run` succeeds
  - `--current-simulation --output-file ... --dry-run` succeeds
  - `--output-file ... --dry-run` without a selector correctly errors
- `election-maps/index.html` now includes `maps-app` with:
  - `maps-panel-left`
  - `maps-stage` (`mapsViewport`, `mapContent`)
  - `maps-panel-right`
- `site/election-maps.js` provides:
  - wheel zoom
  - drag pan
  - reset/zoom button handlers
  - seat hover preview text updates
- `site/styles.css` now includes full maps-shell styling for top bar, panes, map stage, cards, and responsive breakpoints.
- Vendored assets in `site/vendor/`:
  - `tailwindcdn.js`
  - `bootstrap.min.css`
  - `bootstrap.bundle.min.js`
  - `confetti.browser.min.js`
- Rewired local references in:
  - `index.html`
  - `bio/index.html`
  - `election-maps/index.html`
  - `guesstheyear/index.html`
- Guess The Year branding cleanup:
  - `guesstheyear/index.html` title heading now shows `Chronos` (no leading emoji)
  - favicon now uses shared `/imgs/logo.png`
  - top-bar back link text now matches other pages (`← Back home`)
- Homepage simplification:
  - `index.html` title now renders as `principalfish` only
  - removed subtitle lead copy and footer block
  - `site/styles.css` adds `homepage`, `home-title`, and `homepage-top` tweaks for spacing/visual balance
- `404.html` now uses full HTML markup and shared site classes from `/site/styles.css` for consistent look-and-feel with a clear homepage return link.
- Added shared logo/favicon and homepage icon treatment:
  - downloaded `imgs/logo.png` from the referenced GitHub path
  - `index.html`, `bio/index.html`, `election-maps/index.html`, `guesstheyear/index.html`, and `404.html` now reference `/imgs/logo.png` as favicon
  - homepage cards now include compact icon badges, with `logo.png` used for the Election Maps card icon
- Homepage title icon treatment:
  - root `index.html` title now includes a local silly `principal fish` SVG icon beside `principalfish`
  - `site/styles.css` adds `.home-title-icon` sizing plus a subtle bob animation with reduced-motion fallback
- Election data-mode routing now uses URL parameter `election` with default fallback from manifest:
  - `election-maps/data/elections.json`
  - placeholder map files in `election-maps/data/maps/`
  - placeholder results files in `election-maps/data/results/`
- `site/election-maps.js` now:
  - builds left-nav election links from manifest
  - loads selected map/data files by `?election=...`
  - supports both placeholder `seats[]` schema and legacy seat-keyed schema
  - computes subtitle, turnout metadata, vote totals, and seat list from loaded data

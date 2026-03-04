# Learnings Log

Top-level durable insights only.

## Workflow

- Use `.agents/01-file-map.md` first to locate affected files quickly.
- Treat `.agents/issues/` files as issue-level tracking: keep one active file per branch and name it from the branch (for example `issue-13.md`).
- When starting work on an existing branch, resolve and update the matching `.agents/issues/<branch>.md` file first (for example `issue-18` -> `.agents/issues/issue-18.md`).
- Keep user-facing docs in `README.md`; keep operational/internal detail in `.agents/`.
- When documentation references one-off audit directories, prefer reproducible SQL checks against current tables unless that directory is guaranteed to exist.

## Data + DB

- The base import sequence is: topojson -> parties -> general elections.
- Fresh poll imports should include `--include-unimported-parsers` to avoid skipped rows.
- Current schema expectations: electorate on `seats.electorate`; turnout derived from summed `votes.vote_total`.
- `data/start_db.sh` assumes local Docker Postgres on port `5432`; local host Postgres conflicts can block startup.

## UNS + Trends

- `run_retrospective_uns.py` resets existing model outputs by default in non-dry-run mode; use `--no-reset-existing` to preserve prior output.
- `run_uns_model.py` now enforces same-date overwrite and can auto-backfill missing dates, preventing duplicate same-day model outputs.
- `run_uns_model.py` trend-cache writes now skip appending a new date when the full seat snapshot is unchanged from the latest prior date (`TREND_CACHE_SKIP`).
- `backfill_model_output_trends.py` now also skips consecutive model elections with unchanged full seat snapshots, so rebuilt trend CSVs stay compact by default.
- Trend timelines should use canonical UNS date semantics (`UNS YYYY-MM-DD` / normalized `as_of_date`) to avoid chart drift.
- `electionmaps/data/results/model_output_trends.csv` can be safely reduced by dropping consecutive dates whose full seat snapshot is unchanged; these often represent no-new-poll periods and add chart noise/size.

## Electionmaps

- Maps subtitle now uses stacked span layout by default, so the latest poll snippet line (`maps-subtitle-latest`) appears on a separate line on desktop and mobile.
- Zoom percentage display is baseline-relative to `INITIAL_MAP_SCALE`, allowing a slightly zoomed-in initial map view while showing `100%` at load.
- To place map action buttons directly on the map, a page-local override in `electionmaps/index.html` can position `.maps-toolbar` absolutely at top-right (desktop) and remove the viewport top margin to avoid leaving a gap.
- Within that top-right overlay toolbar, the seat search block can be made to sit above other controls by giving it a dedicated class (`maps-toolbar-group-search`) and applying desktop-only `order: -1; width: 100%`.
- If that overlay feels cramped, restore the desktop toolbar to the default above-map strip (`position: static`) and use `maps-toolbar-group-search { order: -1; margin-right: auto; }` so seat search sits left while filter/zoom groups stay right.
- For a mixed layout, keep `Reset/Filters/Choropleths` and `Seat search` in the above-map toolbar (`search` right via `margin-left: auto`), and move zoom controls into a `.maps-zoom-overlay` positioned absolutely in the viewport top-right.
- To make map-embedded zoom controls visually blend with the map, keep `.maps-zoom-overlay` positioned in the viewport but set its container chrome to transparent (`padding: 0; border: 0; background: transparent`).
- If zoom overlay placement should match on mobile and desktop, define `.maps-viewport { position: relative; }` and `.maps-zoom-overlay { position: absolute; top/right... }` outside desktop-only media queries; otherwise mobile falls back to normal flow placement.
- To move map-embedded zoom controls from top-right to top-left globally, switch the overlay anchor from `right` to `left` in the shared `.maps-zoom-overlay` rule.
- Keep layout rules in `site/styles.css` (not page-local `<style>` in `electionmaps/index.html`) for maintainability, then run `npm run minify:electionmaps` so `site/styles.min.css` picks up the change.
## Electionmaps

- Runtime architecture is manifest-driven (`electionmaps/data/elections.json`) with data resolved by configured map/result file mappings.
- Exported results use compact schema `pf-results-v2`; frontend supports compact and legacy normalization.
- Keep map interaction state centralized and re-render from canonical seat datasets to avoid stale filters/search/highlights.
- Route/path naming is now `electionmaps` (not `election-maps`); keep links/scripts/exports/docs aligned when making path changes.
- Mobile UX now uses an off-canvas elections drawer on `max-width: 980px`, implemented with page-local assets (`electionmaps/mobile-sidebar.css` + `electionmaps/mobile-sidebar.js`) so desktop layout stays untouched.
- Electionmaps mobile UX now works as a unified shell: full-screen election picker, `Map/Seats/Totals` switch, and a bottom-sheet right panel with collapsed/half/full states driven by `maps-mobile-*` classes on `.maps-page`.
- Predict 2029 input now includes Northern Ireland in rendered rows and share-state validation, and NI seats now consume configured party swings instead of being hard-excluded from swing resolution.
- For very small screens, keep `maps-predict-grid` horizontally scrollable (`overflow-x: auto`) with `maps-predict-grid-table { width: max-content; min-width: 100%; }` so all user-input columns remain reachable.
- Predict 2029 now renders a separate Northern Ireland matrix section with dedicated NI party headers (`Sinn Fein`, `DUP`, `Alliance`, `UUP`, `SDLP`) so NI inputs are distinct from GB party columns.
- NI section headers should follow the same color-swatch-only style as GB; party names are exposed via header hover (`title`) tooltips rather than inline text labels.
- Predict NI section no longer needs a dedicated heading; use row label alias `Northern Ireland -> N Ireland` and keep first-column width fixed to match adjacent predict matrix sections.
- For better small-screen predict UX, make input cell widths responsive (`clamp(...)`) and tighten table/input padding so columns shrink first, with horizontal scrolling only as fallback.
- To align NI and GB predict matrices, set shared CSS vars for first-column and value-column widths on `.maps-predict-grid` and apply them to all table sections; this also lets you reclaim space from an oversized Region column without shrinking numbers.
- If NI still looks offset against GB, render NI with one blank spacer value column and a blank second header first-cell (no repeated `Region`) so visual column tracks line up while keeping NI-specific party inputs.

## Data Server

- Local UNS runs from `data/server.py` now auto-export the latest simulation payload to `electionmaps/data/results/prediction-simulation.json` after successful non-dry-run execution, using `data/scripts/export_non_simulation_elections.py --current-simulation`.
- Data console home now includes a manual export-only action (`/exports/current-simulation`) so `prediction-simulation.json` can be refreshed independently of running the model.

## Frontend + Assets

- Prefer local vendored assets for static reliability; bundle D3 rather than relying on jsDelivr ESM entrypoints.
- In `guesstheyear`, entering `?mode=infinite` now checks whether today's Daily challenge is completed; if not, it redirects to Daily so users finish the current game before starting Infinite.
- Static HTML entrypoints should not include `site/vendor/tailwindcdn.js` in served output; keep styling build/static to avoid Tailwind’s production runtime warning.
- After removing runtime includes, `site/tailwind-config.js` and `site/vendor/tailwindcdn.js` can be deleted entirely; the current build uses static CSS (`site/styles.css` / minified outputs) and does not depend on Tailwind runtime scripts.
- `server.sh` is the canonical local preview entrypoint and runs required frontend build steps before serving.
- Shared styles should remain centralized in `site/styles.css` to keep root, bio, and maps pages visually consistent.
- Topbar is centralized via `site/topbar.js` + `site/topbar.css`; configure per-page behavior with body data attributes instead of duplicating page-specific header markup.
- `npm run minify:electionmaps` now also builds `site/topbar.min.js` and `site/topbar.min.css`; pages should reference these minified topbar assets for served output.
- GA4 should be wired with a `G-...` Measurement ID (not legacy `UA-...`), and the global site tag should be included in each static HTML entrypoint head.
- For pages served on multiple hostnames (custom domain, GitHub Pages preview, localhost), set GA4 `gtag('config', ..., { cookie_domain: 'auto' })` to avoid invalid-domain cookie warnings.
- GA initialization is now runtime-gated in static HTML heads: skip analytics on local/dev hosts (`localhost`, `127.0.0.1`, `0.0.0.0`, `[::1]`, `.local`, and `file:`).
- For `electionmaps` history-driven route updates (`history.replaceState`), emit explicit GA4 `page_view` events to capture virtual navigation beyond initial page load.
- Keep `document.title` synchronized with the active `electionmaps` mode/election before calling route-state helpers so virtual GA4 `page_view` events capture the correct `page_title`.
- For pages with async title updates (`electionmaps`), disable GA4 auto `page_view` (`send_page_view: false`) in `site/ga-setup.js` via pathname detection and emit manual `page_view` after route/title settle to avoid all events collapsing under the default HTML title.
- NI seat polygons are not stable between `map-1.topo.json` and `map-2.topo.json`: all 17 shared NI seat names have geometry deltas, and one seat is renamed (`Belfast South` -> `Belfast South and Mid Down`).
- Bio/profile photos added from phone originals can be multi-megabyte JPEGs; keep layout lazy-loaded and plan a web-optimized pass (downscale/WebP) to reduce static page payload.
- For profile pages, concise copy with fewer dense paragraphs improves scanability and preserves the existing card-style visual rhythm.
- Contextual inline links (rather than separate CTA blocks) work well for profile prose when pointing to flagship internal pages like `electionmaps/`.

## Quality + Risks

- Current baseline test status in `data/` is green (`data/run_tests.sh`).
- Main maintainability risks are large monolithic files (`electionmaps/electionmaps.js`, `data/server.py`, `guesstheyear/script.js`) and should be modularized incrementally.
- In `data/server.py`, avoid per-request DB engine creation and watch unbounded preview cache growth patterns.

- Seat search in `electionmaps` now uses a custom JS suggestion menu (`.maps-seat-search-menu`) instead of native `datalist`, because mobile browsers can fail to show datalist options reliably; this keeps tap/focus suggestions and Enter selection consistent across desktop and mobile.

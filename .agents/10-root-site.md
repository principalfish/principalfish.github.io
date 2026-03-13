# Area: Root static site

## Purpose

- Hosts static pages and client-side election maps UI.
- Separate from the `data/` Flask backend runtime.

## Key files

- `index.html`, `bio/index.html`, `electionmaps/index.html`
- `site/styles.css`, `site/main.js`, `site/vendor/`
- `electionmaps/electionmaps.js`
- `electionmaps/data/elections.json` and exported `maps/` + `results/`
- `server.sh` for local build + static serve

## JS build

- `electionmaps/electionmaps.js` is the source file — **always edit this, never edit the minified file directly**.
- After any JS change, regenerate the minified file from the repo root: `npm run minify:electionmaps`
- This also minifies `site/topbar.js` and CSS — safe to run any time.

## Durable rules

- Keep shared visual primitives centralized in `site/styles.css`.
- Keep electionmaps file loading manifest-driven.
- Keep path naming consistent with `electionmaps` across links/scripts/docs.
- By-election seat names for the current-parliament view are stored as `byElectionSeats` in the manifest entry and populated by `export_non_simulation_elections.py`.
- Gate GA initialization in static HTML entrypoints so localhost/development hosts do not emit analytics events.
- Electionmaps mobile-specific UX adjustments can live in page-local assets when they must override minified shared styles without changing desktop behavior.
- Electionmaps mobile interaction model should remain coherent across panels: elections picker as full-screen sheet, right insights as bottom sheet, and `Map/Seats/Totals` view switching coordinated in `electionmaps/mobile-sidebar.js` via `.maps-page` state classes.

# Area: Root static site

## What it does

- Provides static hosting artifacts and basic local static serving.
- Not the same as the `data/` Flask app.

## Key files

- `index.html`: root homepage entrypoint for static hosting.
- `bio/index.html`: biography page (text + photos).
- `electionmaps/index.html`: election maps page shell.
- `electionmaps/data/elections.json`: manifest for election URL routing plus settings metadata (map/data file mapping, party and region metadata, comparison election ids).
- `electionmaps/data/maps/`, `electionmaps/data/results/`: generated static data targets (maps per `map_id`, compact `pf-results-v2` results).
- `site/`: homepage assets (Tailwind config, custom CSS, and JS behavior).
- `site/styles.css`: shared live editorial style system for root, bio, and electionmaps pages.
- `electionmaps/electionmaps.js`: interaction scaffold for maps viewport (zoom/pan/control wiring).
- `site/vendor/`: local copies of third-party frontend dependencies referenced by static pages.
- `designs/`: visual concept preview pages for homepage direction.
- `server.sh`: runs local frontend asset automation (`npm run vendor:d3`, `npm run minify:electionmaps`) and then serves via `python3 -m http.server` (default `PORT=8000`, override with `PORT=...`).
- `404.html`, `CNAME`: static hosting/domain behavior.

## Working assumptions

- This area is lightweight and mostly deployment/static-content oriented.
- Most product/data logic is under `data/` and `guesstheyear/`.

## Typical tasks in this area

- Root homepage scaffolding and app-link curation
- Add/update static subpages linked from homepage
- Maintain maps-page shell styling (top bar + background + three-pane app scaffold)
- Maintain election-map data contract (`?election=` URL param + manifest-driven file loading)
- Keep manifest/settings schema aligned with exporter outputs (`mapFilesById`, `dataFilesByElectionId`, `parties`, `partiesByKey`, `regionsByMapId`, `comparisonElectionId`)
- Keep the “bluey” gradient background consistent across root static pages by reusing `maps-page` + `maps-background` from `site/styles.css`
- Build style concept previews before committing to one visual direction
- Static page changes
- Domain/hosting adjustments
- Local static preview

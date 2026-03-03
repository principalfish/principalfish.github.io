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

## Durable rules

- Keep shared visual primitives centralized in `site/styles.css`.
- Keep electionmaps file loading manifest-driven.
- Keep path naming consistent with `electionmaps` across links/scripts/docs.

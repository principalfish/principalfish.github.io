# Shared site assets

Shared frontend assets — styles, top bar, Google Analytics, vendored `d3`/`topojson`/`leaflet` bundles.

The rest of the site's shared pieces sit at the repo root: `index.html`, `404.html` and
`CNAME` are the landing page, error page and custom domain; `bio/` is the static bio page;
`imgs/` holds shared images; and `package.json` holds the build tooling (esbuild, terser,
clean-css) and Vitest.

## Static site local preview (`/`, `/bio`, `/electionmaps`)

Prerequisites:
- Node.js + npm (for frontend asset build steps)
- Python 3 (for local static server)

From repo root:

```bash
./server.sh
```

`server.sh` now runs frontend asset build steps before serving:
- `npm run vendor:d3`
- `npm run minify:electionmaps`

Then it starts `python3 -m http.server` (default port `8000`).
To use a different port:

```bash
PORT=8001 ./server.sh
```

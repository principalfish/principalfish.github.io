# 002 Root site Tailwind scaffold

## Status
- [x] Review root-site documentation and current static files
- [x] Scaffold root `index.html` as homepage shell
- [x] Add `site/` assets (styles + behavior) and wire from `index.html`
- [x] Validate static serving and append learnings

## Scope
1. Create a root homepage suitable for GitHub Pages static hosting.
2. Keep page-specific logic/assets under `site/`.
3. Use Tailwind for styling with a zero-build static approach.

## Decisions
- Tailwind integration method: CDN (`https://cdn.tailwindcss.com`) for static/no-build startup.
- Root entrypoint: `index.html` in repository root.
- Local logic/assets folder: `site/`.

## Validation plan
- Confirm `index.html` references `site/` assets and renders basic sections.
- Run local static server smoke-check command.

## Validation notes
- Verified references with `rg`:
  - `site/tailwind-config.js`
  - `site/styles.css`
  - `site/main.js`
  - `https://cdn.tailwindcss.com`
- Ran `timeout 2s python3 -m http.server` (process exited via timeout as expected after successful startup window).

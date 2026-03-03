# 011 Choropleths reset and popup padding

## Status
- [x] Add Choropleths reset button in popup UI
- [x] Wire reset behavior in election maps script
- [x] Add extra map-edge padding for popup placement
- [x] Validate diagnostics and record learnings

## Scope
- Add a `Reset` action to the Choropleths popup.
- Ensure reset returns choropleth controls to defaults (`none`, `all`) and re-renders map.
- Add visible spacing between map edges and the Filters/Choropleths popups.

## Validation notes
- Diagnostics: no editor errors in `election-maps/index.html`, `election-maps/election-maps.js`, and `site/styles.css`.

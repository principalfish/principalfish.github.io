# issue-13

## Status
- [x] Add URL-sharing support to Predict 2029 screen
- [x] Add explicit share action for prediction scenarios
- [x] Build and validate frontend assets
- [x] Improve mobile wrapping for prediction subtitle + latest poll snippet
- [x] Show latest poll snippet on separate line on desktop
- [x] Set default map zoom readout to 100%

## Notes
- Track this issue in a branch-named file under `.agents/issues/`.
- Branch: `issue-13`
- Added `Share` action in predict mode.
- Predict state is serialized into `predict` URL query param (base64url JSON payload), restored when opening a shared link.
- Non-predict routes now clear `predict` state from URL.
- `mapsSubtitle` now renders as structured segments (main summary + latest poll snippet) so mobile layouts stack cleanly instead of awkward inline wraps.
- Validation: no editor errors in `electionmaps/electionmaps.js` and `electionmaps/index.html`; ran `npm run minify:electionmaps`.
- Updated shared subtitle styling to stack `maps-subtitle-main` and `maps-subtitle-latest` at all breakpoints, ensuring “Latest poll used …” is on a separate line on desktop and mobile.
- Validation: no editor errors in `site/styles.css` and rebuilt `site/styles.min.css`; ran `npm run minify:electionmaps`.
- Updated zoom label calculation to be baseline-relative to `INITIAL_MAP_SCALE`, so initial map view remains slightly zoomed in but displays `100%` by default.
- Validation: no editor errors in `electionmaps/electionmaps.js` and rebuilt `electionmaps/electionmaps.min.js`; ran `npm run minify:electionmaps`.

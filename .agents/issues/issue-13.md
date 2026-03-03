# issue-13

## Status
- [x] Add URL-sharing support to Predict 2029 screen
- [x] Add explicit share action for prediction scenarios
- [x] Build and validate frontend assets
- [x] Improve mobile wrapping for prediction subtitle + latest poll snippet

## Notes
- Track this issue in a branch-named file under `.agents/issues/`.
- Branch: `issue-13`
- Added `Share` action in predict mode.
- Predict state is serialized into `predict` URL query param (base64url JSON payload), restored when opening a shared link.
- Non-predict routes now clear `predict` state from URL.
- `mapsSubtitle` now renders as structured segments (main summary + latest poll snippet) so mobile layouts stack cleanly instead of awkward inline wraps.
- Validation: no editor errors in `electionmaps/electionmaps.js` and `electionmaps/index.html`; ran `npm run minify:electionmaps`.

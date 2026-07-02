// ─── US election maps — page entry ──────────────────────────────────────────
//
// Thin shell for the US page (President, Senate, House). The engine lives in
// /electionmapslogic; this page enables the `predict` feature (interactive uniform-swing
// forecasting) and imports only that feature module — it ships none of the poll-tracker or
// postcode code. window.MAPS_PAGE is set inline in index.html before this module loads and
// is read via `page` in state.js. index.html injects the shared app markup
// (electionmapslogic/shell.html, no fragments — postcode / referendum-info / polltracker
// are UK-only) via shell-loader before importing this bundle.
//
// Because only this entry imports the predict modules, esbuild bundles them into
// uselectionmaps.min.js alone. Predict switches on per parliament via the manifest's
// parliamentFeatures (only parliaments listing `predict` in `features` get the model).

import { startApp } from '../electionmapslogic/app.js';
import {
  activatePredictView,
  getPredictBaseElection,
} from '../electionmapslogic/features/predict-controller.js';
import { wirePredictControls } from '../electionmapslogic/features/predict-view.js';

startApp({
  predict: {
    wire: wirePredictControls,
    activate: activatePredictView,
    getBaseElection: getPredictBaseElection,
  },
});

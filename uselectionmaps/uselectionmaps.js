// ─── US election maps — page entry ──────────────────────────────────────────
//
// Thin shell for the US page (President, Senate, House). Shared engine modules handle
// the manifest-driven maps, saved uncertainty and poll trackers. The page also enables
// interactive uniform-swing prediction and the Senate seats-up/full-chamber switch.
// index.html loads the shared shell and poll-tracker fragment before this bundle.
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
import { activateSenateForecastView } from '../electionmapslogic/features/senate-forecast-controller.js';
import {
  activatePollTrackerMode, wirePollTrackerControls,
} from "../electionmapslogic/features/polltracker-view.js";

startApp({
  predict: {
    wire: wirePredictControls,
    activate: activatePredictView,
    getBaseElection: getPredictBaseElection,
    activateElection: activateSenateForecastView,
  },
  pollTracker: { wire: wirePollTrackerControls, activate: activatePollTrackerMode },
});

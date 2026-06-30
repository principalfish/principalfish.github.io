// ─── UK election maps — page entry ──────────────────────────────────────────
//
// Thin shell for the UK page (Westminster + Holyrood). The engine lives in
// /electionmapslogic; this entry only declares the feature modules this page bundles —
// predict, the poll tracker, and postcode search — keyed by feature name. startApp switches
// each on only when the manifest's parliamentFeatures enables it, so behaviour is
// manifest-driven. window.MAPS_PAGE is set inline in index.html before this module loads.
//
// Because only this entry imports these feature modules, esbuild bundles them into
// electionmaps.min.js alone; the US bundle (which omits these imports) ships without them.

import { startApp } from '../electionmapslogic/app.js';
import {
  activatePredictView,
  getPredictBaseElection,
} from '../electionmapslogic/features/predict-controller.js';
import { wirePredictControls } from '../electionmapslogic/features/predict-view.js';
import {
  activatePollTrackerMode,
  wirePollTrackerControls,
} from '../electionmapslogic/features/polltracker-view.js';
import {
  wirePostcodeSearch,
  initPostcodeSearch,
} from '../electionmapslogic/features/postcode.js';

startApp({
  predict: {
    wire: wirePredictControls,
    activate: activatePredictView,
    getBaseElection: getPredictBaseElection,
  },
  pollTracker: {
    wire: wirePollTrackerControls,
    activate: activatePollTrackerMode,
  },
  postcode: {
    wire: wirePostcodeSearch,
    mapInit: initPostcodeSearch,
  },
});

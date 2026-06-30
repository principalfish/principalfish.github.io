// ─── UK election maps — page entry ──────────────────────────────────────────
//
// Thin shell for the UK page (Westminster + Holyrood). The engine lives in
// /electionmapslogic; this entry only selects the features this page enables — predict and
// the poll tracker — and hands their hooks to the shared bootstrap. window.MAPS_PAGE is set
// inline in index.html before this module loads and is read via `page` in state.js.
//
// Because only this entry imports the predict / poll-tracker modules, esbuild bundles them
// into electionmaps.min.js alone; the US bundle (which omits these imports) ships without them.

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

startApp({
  wire: () => {
    wirePredictControls();
    wirePollTrackerControls();
  },
  activatePredictView,
  activatePollTrackerMode,
  getPredictBaseElection,
});

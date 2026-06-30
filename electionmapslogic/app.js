// ─── App bootstrap (shared) ─────────────────────────────────────────────────
//
// The shared orchestrator: fetches the manifest, resolves the active election from the URL
// or defaults, loads map + result JSON, runs the render pipeline, and routes to the initial
// view. Page-specific features (predict, poll tracker) are injected by the page entry as a
// `features` bag, so this module imports ONLY the core engine — never a feature module. A
// page that ships no features (e.g. the US page today) bundles none of that code.

import {
  state,
  manifest,
  page,
  initState,
  buildRouteSearchParams,
} from './state.js';
import { fetchJson } from './files.js';
import {
  renderHeader,
  renderLeftBar,
  renderMap,
  renderMapControlOptions,
  renderMapInit,
  renderPageTitle,
  syncRightPanelHeight,
  setElectionPreDataFetch,
  wireInit,
} from './dom.js';

// Base path for data fetches. Page-relative by default ('data' → /<page>/data); a page may
// set page.dataBase to read a different directory.
const DATA_BASE = page.dataBase || 'data';

/**
 * @typedef {object} AppFeatures
 * @property {() => void} [wire] - Wires the feature controls once at init (e.g. predict /
 *   poll-tracker buttons). Called from `startApp` after the core `wireInit`.
 * @property {() => Promise<void>} [activatePredictView] - Enters predict mode after the
 *   baseline election has loaded. Presence of this hook is what enables the predict view.
 * @property {() => Promise<void>} [activatePollTrackerMode] - Enters poll-tracker mode.
 * @property {() => (object|null)} [getPredictBaseElection] - Resolves which election's files
 *   to fetch when in predict mode (feature-gated baseline lookup).
 */

// =====================================================================
// INIT
// =====================================================================

/**
 * Bootstraps election data: fetches the manifest, resolves the active election from the URL or
 * defaults, loads map and results JSON in parallel, optionally loads comparison election data,
 * populates controls, and triggers the initial render.
 * @param {AppFeatures} features
 * @returns {Promise<void>}
 */
async function initPage(features) {
  // Fetch 1: manifest — election list, parliament config, file paths, party/region lookup data
  const view = new URLSearchParams(window.location.search).get('view') || 'election';
  await initState(await fetchJson(`${DATA_BASE}/map-modes.json`), view);

  // A page only handles the feature views whose hooks it provides. If the URL requests a
  // feature view this page doesn't ship (e.g. ?view=predict on the US page), degrade to the
  // election view rather than rendering a half-wired feature.
  if (state.view === 'predict' && !features.activatePredictView) state.view = 'election';
  if (state.view === 'polltracker' && !features.activatePollTrackerMode) state.view = 'election';

  renderPageTitle();
  trackVirtualPageView();
  renderLeftBar();
  // Render early with the election name only — subtitle will be overwritten with full summary
  // text (majority / hung parliament) once election results have loaded below.
  renderHeader();
  if (state.view === 'polltracker') {
    await features.activatePollTrackerMode();
  } else {
    await activateElection(features);
    if (state.view === 'predict') await features.activatePredictView();
  }
}

// =====================================================================
// ELECTIONS
// =====================================================================

/**
 * Loads and renders the active view (election or predict): picks which election's
 * files to fetch, populates the relevant state slots, and runs the shared render
 * pipeline. Predict-specific model setup happens afterwards in `activatePredictView`.
 * Must be called after `state.currentElection` is set.
 * @param {AppFeatures} features
 * @returns {Promise<void>}
 */
async function activateElection(features) {
  setElectionPreDataFetch();

  // Pick which election's files to fetch. Predict mode delegates to the injected
  // `getPredictBaseElection`, which handles the feature-gate fallback (demoting view to
  // 'election') and the manifest-driven baseline lookup. A null return there means the
  // baseline isn't configured — render an error and bail.
  let fetchElection;
  if (state.view === 'predict' && features.getPredictBaseElection) {
    fetchElection = features.getPredictBaseElection();
    // getPredictBaseElection demotes the view to 'election' (returning currentElection) when
    // predict isn't a feature; a null return here means predict IS configured but its baseline
    // election is missing/unresolvable. Fail with a clear message rather than letting
    // resolveElectionFiles throw an opaque TypeError on the null.
    if (state.view === 'predict' && !fetchElection) {
      throw new Error(`Predict baseline election is not configured or resolvable for parliament '${state.currentParliament}'`);
    }
  } else {
    fetchElection = state.currentElection;
  }

  // Fetch map topology, election results, and (election view only) comparison results.
  const { mapFile, dataFile, comparisonDataFile } = manifest.resolveElectionFiles(fetchElection);
  const includeComparison = state.view !== 'predict' && comparisonDataFile;
  const [mapData, resultsData, comparisonData] = await Promise.all([
    fetchJson(`${DATA_BASE}/${mapFile}`),
    fetchJson(`${DATA_BASE}/${dataFile}`),
    includeComparison ? fetchJson(`${DATA_BASE}/${comparisonDataFile}`) : Promise.resolve(null),
  ]);

  // Wire results into state. Predict mode points both electionData and
  // comparisonElectionData at the baseline so the initial render shows it without
  // running a projection; activatePredictView overwrites electionData later if a
  // shared `?predict=` scenario is present.
  state.setMapData(mapData);
  state.setElectionData(resultsData, fetchElection?.name ?? null);
  if (state.view === 'predict') {
    state.setComparisonElectionData(resultsData, fetchElection?.name ?? null);
  } else if (comparisonData) {
    state.setComparisonElectionData(comparisonData);
  }

  state.setupMapData();
  renderMapControlOptions();
  renderHeader(state.electionData.summary.text);
  renderMapInit();
  renderMap();
  syncRightPanelHeight();

  const params = buildRouteSearchParams(state.view === 'predict' ? 'predict' : 'election');
  window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
}

// =====================================================================
// MISC
// =====================================================================

let lastTrackedPath = '';

/**
 * Fires a gtag page_view event for the current location, deduplicating against the last tracked path.
 * No-ops on dev hosts because ga-setup.js leaves window.gtag undefined there.
 * @returns {void}
 */
function trackVirtualPageView() {
  if (typeof window.gtag !== 'function') return;

  const pagePath = `${window.location.pathname}${window.location.search}`;
  if (pagePath === lastTrackedPath) return;

  lastTrackedPath = pagePath;
  window.gtag('event', 'page_view', {
    page_location: window.location.href,
    page_path: pagePath,
    page_title: document.title,
  });
}

// =====================================================================
// ENTRY
// =====================================================================

/**
 * Page entry point. Wires core controls, lets the page wire its feature controls, then loads
 * election data and routes to the initial view.
 * @param {AppFeatures} [features] - Page-specific feature hooks. Omit for a feature-free page.
 * @returns {Promise<void>}
 */
export async function startApp(features = {}) {
  wireInit();
  features.wire?.();

  try {
    await initPage(features);
  } catch (error) {
    renderHeader('', true);
    console.error(error);
  }
}

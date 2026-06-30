import {
  state,
  manifest,
  page,
  initState,
  buildRouteSearchParams,
} from './scripts/state.js';
import {
  activatePredictView,
  getPredictBaseElection,
} from './scripts/predict-controller.js';
import {
  fetchJson,
} from './scripts/files.js';
import {
  parsePollTrackerData,
} from './scripts/polltracker.js';
import {
  renderHeader,
  renderLeftBar,
  renderMap,
  renderMapControlOptions,
  renderMapInit,
  renderPageTitle,
  renderPollTracker,
  syncRightPanelHeight,
  setElectionPreDataFetch,
  wireInit,
} from './scripts/dom.js';

// Base path for data fetches. Page-relative by default ('data' → /<page>/data); the US
// page sets page.dataBase to '../electionmaps/data' so it reads the one shared data dir.
const DATA_BASE = page.dataBase || 'data';

// =====================================================================
// INIT
// =====================================================================

/**
 * Bootstraps election data: fetches the manifest, resolves the active election from the URL or defaults,
 * loads map and results JSON in parallel, optionally loads comparison election data,
 * populates controls, and triggers the initial render.
 * @returns {Promise<void>}
 */
async function initPage() {
  // Fetch 1: manifest — election list, parliament config, file paths, party/region lookup data
  const view = new URLSearchParams(window.location.search).get('view') || 'election';
  await initState(await fetchJson(`${DATA_BASE}/map-modes.json`), view);

  renderPageTitle();
  trackVirtualPageView();
  renderLeftBar();
  // Render early with the election name only — subtitle will be overwritten with full summary
  // text (majority / hung parliament) once election results have loaded below.
  renderHeader();
  if (view === 'polltracker') {
    await activatePollTrackerMode();
  } else {
    await activateElection();
    if (state.view === 'predict') await activatePredictView();
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
 * @returns {Promise<void>}
 */
async function activateElection() {
  setElectionPreDataFetch();

  // Pick which election's files to fetch. Predict mode delegates to
  // `getPredictBaseElection`, which handles the feature-gate fallback (demoting view
  // to 'election') and the manifest-driven baseline lookup. A null return from
  // there means baseline isn't configured — render an error and bail.
  let fetchElection;
  if (state.view === 'predict') {
    fetchElection = getPredictBaseElection();
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
// POLLTRACKER
// =====================================================================

/**
 * Switches the app into poll tracker mode, loads data, renders controls and chart, and updates the route.
 * @returns {Promise<void>}
 */
async function activatePollTrackerMode() {
  document.body.classList.add('maps-polltracker-mode');

  const dataPath = manifest.parliamentFeatures[state.currentParliament].polltrackerDataPath;
  const data = await fetchJson(`${DATA_BASE}/${dataPath}`);
  state.pollTrackerData = parsePollTrackerData(data);
  renderPollTracker();
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
// WIRE CONTROLS
// =====================================================================

/**
 * Entry point. Wires controls then loads election data and routes to the initial view.
 * @returns {Promise<void>}
 */
async function init() {
  wireInit();

  try {
    await initPage();
  } catch (error) {
    renderHeader('', true);
    console.error(error);
  }
}

init();

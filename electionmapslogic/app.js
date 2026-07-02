// ─── App bootstrap (shared) ─────────────────────────────────────────────────
//
// The shared orchestrator: fetches the manifest, resolves the active election from the URL
// or defaults, loads map + result JSON, runs the render pipeline, and routes to the initial
// view.
//
// Features (predict, poll tracker, postcode search) are passed by the page entry as a
// registry keyed by feature name. After the manifest loads, startApp switches on only the
// registered features that the page's parliaments actually enable (per
// `parliamentFeatures[...].features`) — wiring their controls, registering their map-init
// hooks, and routing feature views. This module imports ONLY the core engine, never a feature
// module, so a page that registers no features (e.g. the US page today) bundles none of that
// code, and which features run is manifest-driven rather than hard-coded in the entry.

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
  registerMapInitHook,
} from './dom.js';

// Base path for data fetches. Page-relative by default ('data' → /<page>/data); a page may
// set page.dataBase to read a different directory.
const DATA_BASE = page.dataBase || 'data';

/**
 * @typedef {object} FeatureModule
 * @property {() => void} [wire] - One-time control wiring, run once after the manifest loads.
 * @property {() => void} [mapInit] - Per-election map-init hook (registered with the core).
 * @property {() => Promise<void>} [activate] - View activation, for the view features
 *   (`predict` / `pollTracker`) that replace the election view.
 * @property {() => (object|null)} [getBaseElection] - `predict` only: resolves which
 *   election's files to fetch as the projection baseline.
 */

/**
 * Returns the subset of registered feature modules the page's parliaments actually enable,
 * per the manifest's `parliamentFeatures[parliament].features`. Keyed by feature name (the
 * same strings used in `features`), so the entry just declares the modules it bundles and the
 * manifest decides which to switch on. Union across all of the page's parliaments.
 * @param {Record<string, FeatureModule>} featureModules
 * @returns {Record<string, FeatureModule>}
 */
function enabledFeatures(featureModules) {
  const enabledNames = new Set();
  for (const tab of manifest.parliamentTabs()) {
    for (const name of manifest.parliamentFeatures?.[tab.parliament]?.features ?? []) {
      enabledNames.add(name);
    }
  }
  const enabled = {};
  for (const [name, mod] of Object.entries(featureModules)) {
    if (enabledNames.has(name)) enabled[name] = mod;
  }
  return enabled;
}

// =====================================================================
// INIT
// =====================================================================

/**
 * Bootstraps election data: fetches the manifest, switches on the page's enabled features,
 * resolves the active election from the URL or defaults, loads map and results JSON in
 * parallel, optionally loads comparison election data, populates controls, and triggers the
 * initial render.
 * @param {Record<string, FeatureModule>} featureModules
 * @returns {Promise<void>}
 */
async function initPage(featureModules) {
  // Fetch 1: manifest — election list, parliament config, file paths, party/region lookup data
  const view = new URLSearchParams(window.location.search).get('view') || 'election';
  await initState(await fetchJson(`${DATA_BASE}/map-modes.json`), view);

  // Resolve which registered features this page actually enables, then switch them on:
  // wire their controls once, and register any per-map-init hooks (e.g. postcode search).
  const features = enabledFeatures(featureModules);
  for (const feature of Object.values(features)) {
    feature.wire?.();
    if (feature.mapInit) registerMapInitHook(feature.mapInit);
  }

  // If the URL requested a feature view this page doesn't enable (e.g. ?view=predict on the
  // US page), degrade to the election view rather than rendering a half-wired feature.
  if (state.view === 'predict' && !features.predict) state.view = 'election';
  if (state.view === 'polltracker' && !features.pollTracker) state.view = 'election';

  renderPageTitle();
  trackVirtualPageView();
  renderLeftBar();
  // Render early with the election name only — subtitle will be overwritten with full summary
  // text (majority / hung parliament) once election results have loaded below.
  renderHeader();
  if (state.view === 'polltracker') {
    await features.pollTracker.activate();
  } else {
    await activateElection(features);
    if (state.view === 'predict') await features.predict.activate();
  }
}

// =====================================================================
// ELECTIONS
// =====================================================================

/**
 * Loads and renders the active view (election or predict): picks which election's
 * files to fetch, populates the relevant state slots, and runs the shared render
 * pipeline. Predict-specific model setup happens afterwards in the predict feature's
 * `activate`. Must be called after `state.currentElection` is set.
 * @param {Record<string, FeatureModule>} features - The page's enabled features.
 * @returns {Promise<void>}
 */
async function activateElection(features) {
  setElectionPreDataFetch();

  // Pick which election's files to fetch. Predict mode delegates to the predict feature's
  // `getBaseElection`, which handles the feature-gate fallback (demoting view to 'election')
  // and the manifest-driven baseline lookup. A null return means baseline isn't configured.
  let fetchElection;
  if (state.view === 'predict' && features.predict?.getBaseElection) {
    fetchElection = features.predict.getBaseElection();
    // getBaseElection demotes the view to 'election' (returning currentElection) when predict
    // isn't a feature; a null return here means predict IS configured but its baseline election
    // is missing/unresolvable. Fail with a clear message rather than letting resolveElectionFiles
    // throw an opaque TypeError on the null.
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
  // running a projection; the predict feature overwrites electionData later if a
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
 * Page entry point. Wires core controls, then loads election data, switches on the page's
 * manifest-enabled features, and routes to the initial view.
 * @param {Record<string, FeatureModule>} [featureModules] - Feature modules the page bundles,
 *   keyed by feature name. Omit for a feature-free page.
 * @returns {Promise<void>}
 */
export async function startApp(featureModules = {}) {
  wireInit();

  try {
    await initPage(featureModules);
  } catch (error) {
    renderHeader('', true);
    console.error(error);
  }
}

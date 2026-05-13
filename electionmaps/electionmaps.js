import {
  state,
  manifest,
  initState,
  ElectionData,
  predictModelClassFor,
} from './scripts/state.js';
import {
  fetchJson,
} from './scripts/files.js';
import {
  renderHeader,
  renderLeftBar,
  renderMap,
  renderMapControlOptions,
  renderMapInit,
  renderPageTitle,
  renderPollTracker,
  renderPredict,
  syncRightPanelHeight,
  setElectionPreDataFetch,
  setPredictActionHandlers,
  setPredictWindowVisible,
  wireInit,
} from './scripts/dom.js';

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
  await initState(await fetchJson('data/map-modes.json'), view);

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
  } else {
    fetchElection = state.currentElection;
  }

  // Fetch map topology, election results, and (election view only) comparison results.
  const { mapFile, dataFile, comparisonDataFile } = manifest.resolveElectionFiles(fetchElection);
  const includeComparison = state.view !== 'predict' && comparisonDataFile;
  const [mapData, resultsData, comparisonData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`),
    includeComparison ? fetchJson(`data/${comparisonDataFile}`) : Promise.resolve(null),
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
  const data = await fetchJson(`data/${dataPath}`);
  state.pollTrackerData = parsePollTrackerData(data);
  renderPollTracker();
}

/**
 * Parses the poll tracker JSON into a chart-ready timeline and per-party series.
 *
 * Input shape (one entry per model run; the writer guarantees one entry per as_of_date):
 *   [{ election_id, election_name, as_of_date: "YYYY-MM-DD",
 *      parties: { "<partyId>": { s: <seats>, v: <votePct> } } }]
 *
 * Pipeline:
 *   1. Flatten — explode each entry into one row per party: { partyKey, asOfDate, seats, votePct }.
 *   2. Index — bucket rows by date (timeline) and by (party, date) for the carry-forward step.
 *   3. Sort — order timeline ascending by date (lexicographic on ISO YYYY-MM-DD == chronological).
 *   4. Expand — fill in every calendar day between the first and last date, even days with no model run.
 *      Gives the chart a uniform daily x-axis instead of one tick per sparse data point.
 *   5. Carry forward — for each party, walk the dense timeline and emit (seats, votePct) for every day,
 *      reusing the last known value on days with no data so chart lines stay continuous through gaps.
 *      Days before a party's first reading remain null.
 *
 * @param {Array} data - Parsed JSON array from the poll tracker data file.
 * @returns {{
 *   timeline: Array<{dateKey: string, dateValue: Date}>,
 *   seriesByParty: Map<string, {
 *     partyKey: string,
 *     partyName: string,
 *     colour: string,
 *     seats: Array<number|null>,
 *     votePct: Array<number|null>,
 *     latestSeats: number
 *   }>
 * }} Chart-ready timeline and per-party series.
 */
function parsePollTrackerData(data) {
  const partiesById = manifest.partiesById;

  // 1. Flatten: one row per (entry, party).
  const rows = [];
  for (const entry of data) {
    const asOfDate = String(entry.as_of_date || '').trim();
    for (const [partyIdStr, pdata] of Object.entries(entry.parties || {})) {
      const partyId = Number(partyIdStr);
      const seats = Number(pdata.s);
      const votePct = Number(pdata.v);
      if (!Number.isFinite(partyId) || !Number.isFinite(seats) || !Number.isFinite(votePct)) continue;
      rows.push({
        partyKey: String(partyId),
        asOfDate,
        seats,
        votePct,
      });
    }
  }

  // 2. Index by date and by (party, date).
  const timelineByDateKey = new Map();
  const byParty = new Map();

  rows.forEach((row) => {
    const dateKey = row.asOfDate;
    timelineByDateKey.set(dateKey, { dateKey });
    if (!byParty.has(row.partyKey)) byParty.set(row.partyKey, new Map());
    byParty.get(row.partyKey).set(dateKey, row);
  });

  // 3. Sort: ISO date strings sort chronologically as plain strings.
  const sortedDates = Array.from(timelineByDateKey.values())
    .sort((a, b) => a.dateKey.localeCompare(b.dateKey));

  // 4. Expand: produce one entry per calendar day from first to last date inclusive.
  // UTC throughout to avoid timezone drift bumping a date to the previous/next day.
  const parseIsoDate = (value) => new Date(`${value}T00:00:00Z`);
  const formatIsoDate = (value) => {
    const year = value.getUTCFullYear();
    const month = String(value.getUTCMonth() + 1).padStart(2, '0');
    const day = String(value.getUTCDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  const timeline = (() => {
    // Single point or empty: nothing to expand, just attach dateValue.
    if (sortedDates.length <= 1) {
      return sortedDates.map((entry) => ({ dateKey: entry.dateKey, dateValue: parseIsoDate(entry.dateKey) }));
    }
    // Walk day-by-day from earliest to latest date.
    const start = parseIsoDate(sortedDates[0].dateKey);
    const end = parseIsoDate(sortedDates[sortedDates.length - 1].dateKey);
    const entries = [];
    const current = new Date(start.getTime());
    while (current.getTime() <= end.getTime()) {
      const iso = formatIsoDate(current);
      entries.push({ dateKey: iso, dateValue: new Date(current.getTime()) });
      current.setUTCDate(current.getUTCDate() + 1);
    }
    return entries;
  })();
 
  // 5. Carry forward: for each party, walk the dense timeline producing parallel seats/votePct arrays
  // with the last known value reused on no-data days. Days before the party's first reading stay null.
  const seriesByParty = new Map();
  byParty.forEach((rowsByDateKey, partyKey) => {
    const seats = [];
    const votePct = [];
    let lastSeats = null;
    let lastVotePct = null;
    timeline.forEach((entry) => {
      const row = rowsByDateKey.get(entry.dateKey);
      if (row) {
        lastSeats = Number(row.seats || 0);
        lastVotePct = Number(row.votePct || 0);
      }
      seats.push(lastSeats);
      votePct.push(lastVotePct);
    });

    // Resolve display name + colour from the manifest using the numeric party id.
    const manifestParty = partiesById?.get(Number(partyKey));
    seriesByParty.set(partyKey, {
      partyKey,
      partyName: manifestParty?.name || partyKey,
      colour: manifestParty?.colour || '#9CA3AF',
      seats,
      votePct,
      latestSeats: lastSeats ?? 0,
    });
  });

  return { timeline, seriesByParty };
}

// =====================================================================
// PREDICT
// =====================================================================
//
// Predict mode shares the same data-load path as election mode: activateElection fetches
// the manifest's baseline election (e.g. 2024-general) and assigns it to both
// state.electionData and state.comparisonElectionData, so the user lands on the baseline
// without any projection. activatePredictView then instantiates a parliament-specific
// PredictModel and shows the input grid; if the URL carries a shared `?predict=` scenario
// it deserialises and runs the projection so the shared map renders. Subsequent Submit /
// Apply / Reset clicks call runPredictProjection, which re-projects from the model and
// re-renders.

// Cached current-model simulation seats (for the "Use current forecast" button), one entry
// per parliament. Prefetched in the background from activatePredictView so the first Apply
// click resolves instantly; ensurePredictSimulation also falls back to fetching on demand
// if the prefetch is still in flight (or failed silently).
const predictSimulationCache = new Map();

/**
 * Resolves the election that activateElection should fetch when the page is in
 * predict mode. Three outcomes:
 *   - Parliament's manifest doesn't list `predict` in `features`: demotes
 *     `state.view` to 'election' and returns `state.currentElection` so the caller
 *     proceeds as a regular election view.
 *   - Predict configured and `predictBaselineElectionId` resolves: returns the
 *     baseline election (e.g. 2024-general for Westminster).
 *   - Predict configured but baseline missing or unresolvable: returns null;
 *     caller is expected to render an error and bail.
 * @returns {object|null}
 */
function getPredictBaseElection() {
  const parliament = state.currentParliament;
  const features = manifest.parliamentFeatures[parliament]?.features ?? [];
  if (!features.includes('predict')) {
    state.view = 'election';
    return state.currentElection;
  }
  const baselineId = manifest.parliamentFeatures[parliament]?.predictBaselineElectionId;
  return baselineId ? manifest.getElectionFromId(baselineId) : null;
}

/**
 * Predict-only setup that runs after `activateElection` has fetched the baseline and
 * rendered it. Constructs the parliament-specific predict model, wires the action
 * buttons, shows the predict window, and (only if the URL carries a shared scenario)
 * deserialises it and re-projects so the user lands on the shared map.
 * @returns {Promise<void>}
 */
async function activatePredictView() {
  const parliament = state.currentParliament;
  const parliamentConfig = manifest.parliamentConfig(parliament);
  const PredictModelClass = predictModelClassFor(parliament);
  state.predictModel = new PredictModelClass(parliamentConfig.nextElectionYear, parliamentConfig.predict);

  setPredictActionHandlers({
    apply: handlePredictApply,
    submit: handlePredictSubmit,
    share: handlePredictShare,
    reset: handlePredictReset,
  });
  setPredictWindowVisible(true);
  renderPredict();

  // Warm the simulation cache in the background so the "Use current forecast" button
  // doesn't pay the (possibly multi-MB) fetch latency on its first click. Fire-and-forget:
  // ensurePredictSimulation catches its own errors and returns null on failure, and the
  // Apply handler still calls ensurePredictSimulation defensively.
  ensurePredictSimulation(parliament);

  // If a shared scenario URL is present, hydrate the model, repaint the grid so the
  // deserialised inputs show up in the cells, and re-project so the map reflects them.
  // Without a payload, the baseline already shown by activateElection is the correct
  // initial display.
  const sharedPayload = new URLSearchParams(window.location.search).get('predict');
  if (sharedPayload) {
    state.predictModel.deserialize(sharedPayload);
    renderPredict();
    runPredictProjection();
  }
}

/**
 * Projects the predict model and pushes the result through the regular render pipeline.
 * Reads `state.predictModel`; no-op when null.
 * @returns {void}
 */
function runPredictProjection() {
  const model = state.predictModel;
  if (!model) return;

  const nextYear = model.nextElectionYear;
  const predictLabel = `Predict ${nextYear ?? ''}`.trim();

  // Only state.electionData (the projection output) changes between projections — the
  // baseline / map / region-labels were set once in activateElection and the model
  // now reads them straight from state via getters.
  const projectedSeats = model.project();
  state.setElectionDataFromSeats(projectedSeats, predictLabel);

  state.setupMapData();
  renderHeader(state.electionData.summary.text);
  renderMapControlOptions();
  renderMap();
  syncRightPanelHeight();

  const params = buildRouteSearchParams('predict');
  window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
}

/**
 * Submit handler. Wired to the `[data-predict-action="submit"]` button via
 * `setPredictActionHandlers` in `activatePredictView`.
 *
 * Runs `model.validate()` to find any region whose entered party shares sum above 100%
 * (which would imply a negative residual for "other parties" and produce undefined
 * behaviour in `projectSeatUniformSwing`). On invalid input, surfaces a blocking
 * `window.alert` listing up to four offending regions with their totals — the cap keeps
 * the dialog readable when many rows are out of bounds — and bails without projecting.
 *
 * On valid input, delegates to `runPredictProjection` which pulls a fresh `Seat[]` out
 * of the model, swaps it into `state.electionData`, and re-renders the map / right panel
 * / header. URL is also synced via `buildRouteSearchParams('predict')` as a side effect.
 *
 * No-ops silently if `state.predictModel` is null (e.g. the user navigated away from
 * predict mode mid-click).
 *
 * @returns {void}
 */
function handlePredictSubmit() {
  // Validate before projecting — a row whose entered shares exceed 100 produces a
  // negative residual for "other parties", which projectSeatUniformSwing has no defined
  // behaviour for. Cap the alert at the first 4 offenders so the dialog stays readable.
  const model = state.predictModel;
  if (!model) return;
  const invalid = model.validate();
  if (invalid.length) {
    const summary = invalid.slice(0, 4).map((r) => `${r.regionLabel} (${r.total}%)`).join(', ');
    window.alert(`Entered percentages exceed 100% for: ${summary}${invalid.length > 4 ? ', ...' : ''}. Please reduce inputs before submitting.`);
    return;
  }
  runPredictProjection();
}

/**
 * Reset handler. Wired to the `[data-predict-action="reset"]` button.
 *
 * Calls `model.reset()`, which clears every input map, drops the aggregate-expanded
 * flag, and (Holyrood only) restores `activeTab` to the first tab. The render pass then
 * runs *before* re-projection: `renderPredict` rebuilds the grid from the cleared model
 * so the user sees baseline values immediately, even if `runPredictProjection`'s
 * zero-swing short-circuit returns the unaltered baseline seats milliseconds later.
 *
 * No-ops silently if `state.predictModel` is null.
 *
 * @returns {void}
 */
function handlePredictReset() {
  // Reset clears every input map (and aggregateExpanded / activeTab on Holyrood); the
  // re-render is needed before re-projection so the grid reflects the cleared inputs
  // even if projection short-circuits to baseline.
  const model = state.predictModel;
  if (!model) return;
  model.reset();
  renderPredict();
  runPredictProjection();
}

/**
 * Apply handler ("Use current forecast" button). Wired to `[data-predict-action="apply"]`.
 *
 * Reads the per-parliament prediction-model output via `ensurePredictSimulation` — the
 * file is prefetched into `predictSimulationCache` from `activatePredictView`, so this
 * usually resolves synchronously from cache; if the prefetch is still in flight (or it
 * never ran because predict view loaded mid-fetch) the call awaits the same fetch. If
 * the load fails — missing anchor election, network error, or empty seat array —
 * surfaces a blocking `window.alert` and bails.
 *
 * On success, calls `model.loadSimulationShares(simulationSeats)` which writes the
 * forecast's per-region shares into the model's input map(s), honouring the current
 * aggregate-expanded state (`PredictModel.loadSharesFromSeats` skips the synthetic
 * aggregate when expanded, sub-regions when collapsed). Then re-renders the grid so
 * forecast values become editable, and re-projects.
 *
 * No-ops silently if `state.predictModel` is null.
 *
 * @returns {Promise<void>}
 */
async function handlePredictApply() {
  // The simulation file is prefetched from activatePredictView so this normally resolves
  // from cache; on cache miss (slow prefetch / previous fetch failure) ensurePredictSimulation
  // falls back to fetching synchronously. loadSimulationShares writes the forecast into the
  // model's input maps, then re-render makes those values appear in the grid before projection.
  const model = state.predictModel;
  if (!model) return;
  const simulationSeats = await ensurePredictSimulation(state.currentParliament);
  if (!simulationSeats) {
    window.alert('Current prediction data is not available.');
    return;
  }
  model.loadSimulationShares(simulationSeats);
  renderPredict();
  runPredictProjection();
}

/**
 * Loads and caches the current model-output seats for the current parliament. Returns null
 * on failure. The cache stores the in-flight promise (not the resolved seats) so concurrent
 * callers — typically the activatePredictView prefetch and a fast Apply click — share one
 * fetch. On failure the entry is evicted so the next call retries.
 * @param {string} parliament
 * @returns {Promise<Seat[] | null>}
 */
async function ensurePredictSimulation(parliament) {
  if (predictSimulationCache.has(parliament)) return predictSimulationCache.get(parliament);

  const anchorId = manifest.parliamentFeatures[parliament]?.predictAnchorElectionId;
  const anchorElection = anchorId ? manifest.getElectionFromId(anchorId) : null;
  if (!anchorElection) return null;

  const promise = (async () => {
    try {
      const { dataFile } = manifest.resolveElectionFiles(anchorElection);
      const resultsData = await fetchJson(`data/${dataFile}`);
      const electionData = new ElectionData(resultsData);
      if (!electionData.baseSeats.length) return null;
      return electionData.baseSeats;
    } catch (error) {
      console.error('Predict simulation load failed', error);
      return null;
    }
  })();
  predictSimulationCache.set(parliament, promise);
  const seats = await promise;
  if (!seats) predictSimulationCache.delete(parliament);
  return seats;
}

/**
 * Share handler. Wired to `[data-predict-action="share"]`.
 *
 * Composes a fully-qualified URL containing the current predict scenario via
 * `buildRouteSearchParams('predict')` (which embeds `model.serialize()` as the
 * `?predict=` payload alongside `?view=predict` and `?election=...`). Then walks a
 * three-tier delivery fallback so the URL is always accessible to the user:
 *
 * 1. `navigator.share` — opens the OS-native share sheet on mobile / supported desktops.
 *    Both the API call and the user's accept/dismiss can throw; either case falls
 *    through to step 2.
 * 2. `navigator.clipboard.writeText` — silent copy + a confirmation alert. Requires a
 *    secure context and a user gesture (the click satisfies the gesture). On failure
 *    (permissions denied, missing clipboard API), falls through to step 3.
 * 3. `window.prompt` — final fallback. Shows a pre-populated dialog the user can copy
 *    from manually. Always succeeds at delivering the URL.
 *
 * Each layer's errors are deliberately swallowed because the next fallback handles the
 * user-visible delivery; logging would just be noise.
 *
 * @returns {Promise<void>}
 */
async function handlePredictShare() {
  // Try Web Share first (mobile-native sheet), then async clipboard, then fall back to
  // a prompt() so the URL is always copyable. Both Web Share and Clipboard need a user
  // gesture and a secure context; this fn is invoked from a click so the gesture is
  // satisfied. Errors from either path are swallowed because the next fallback handles
  // the user-visible delivery.
  const params = buildRouteSearchParams('predict');
  const query = params.toString();
  const shareUrl = `${window.location.origin}${window.location.pathname}${query ? `?${query}` : ''}`;
  try {
    if (navigator.share) {
      await navigator.share({ title: 'UK Election Maps prediction', url: shareUrl });
      return;
    }
  } catch { /* fall through */ }
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(shareUrl);
      window.alert('Prediction link copied to clipboard.');
      return;
    } catch { /* fall through */ }
  }
  window.prompt('Copy your prediction link:', shareUrl);
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

/**
 * Builds a URLSearchParams for the given view, setting/removing the election and predict
 * params as appropriate. The predict payload is sourced from state.predictModel.serialize()
 * when view==='predict'.
 * @param {string} view - View name to set ('election' | 'predict' | 'polltracker').
 * @returns {URLSearchParams}
 */
function buildRouteSearchParams(view) {
  const params = new URLSearchParams(window.location.search);
  params.set('view', view);
  if (view !== 'predict') params.delete('predict');

  if (view === 'polltracker') {
    params.delete('election');
    return params;
  }

  if (view === 'predict' && state.predictModel) {
    // Reflect the model's current state in the URL. When the model hasn't been built
    // yet (activateElection runs first and calls this before activatePredictView), leave
    // any incoming `?predict=` payload alone so the shared scenario can be deserialised.
    const payload = state.predictModel.serialize();
    if (payload) params.set('predict', payload);
    else params.delete('predict');
  }

  if (state.currentElection.id) {
    params.set('election', state.currentElection.id);
  }
  return params;
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

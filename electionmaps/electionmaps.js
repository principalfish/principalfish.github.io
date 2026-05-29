import {
  state,
  manifest,
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

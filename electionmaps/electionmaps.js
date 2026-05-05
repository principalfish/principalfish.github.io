import {
  state,
  manifest,
  initState,
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
  renderRightPanel,
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
  }
}

// =====================================================================
// ELECTIONS
// =====================================================================

/**
 * Loads and renders the active election: fetches map topology and results, optionally loads
 * comparison data for swing calculations, populates controls, and triggers the initial render.
 * @param {'election'} view - Active view.
 * @returns {Promise<void>}
 */
/**
 * Loads and activates an election: fetches all required data files, wires them into
 * shared state, fully re-renders all panels, and syncs the URL to the new selection.
 * Must be called after state.currentElection is set to the target election.
 * @returns {Promise<void>}
 */
async function activateElection() {
  // Pre-fetch: reset state and configure UI for this election type.
  setElectionPreDataFetch();

  // Fetch: map topology, election results, and (if configured) comparison results in parallel.
  const { mapFile, dataFile, comparisonDataFile } = manifest.resolveElectionFiles(state.currentElection);
  const [mapData, resultsData, comparisonData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`),
    comparisonDataFile ? fetchJson(`data/${comparisonDataFile}`) : Promise.resolve(null),
  ]);

  // Parse: load fetched data into shared state, then populate controls and render all panels.
  state.initMapData(mapData);
  state.initElectionData(resultsData);
  if (comparisonData) {
    state.initComparisonElectionData(comparisonData);
  }

  renderMapControlOptions();
  renderHeader(state.electionData.summary.text);
  state.setupMapData();
  renderMapInit();
  renderMap();
  renderRightPanel();

  // Sync: update the URL so the active election is bookmarkable and shareable.
  const params = buildRouteSearchParams('election');
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

/**
 * Builds a URLSearchParams for the given view, setting/removing the election param as appropriate.
 * @param {string} view - View name to set ('election' or 'polltracker').
 * @returns {URLSearchParams} Updated search params with view and election params adjusted.
 */
function buildRouteSearchParams(view) {
  const params = new URLSearchParams(window.location.search);
  params.set('view', view);

  if (view === 'polltracker') {
    params.delete('election');
    return params;
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

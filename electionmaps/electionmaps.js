import * as d3 from '../site/vendor/d3.v7.esm.js';
import {
  feature as topojsonFeature,
  mesh as topojsonMesh,
  merge as topojsonMerge,
} from '../site/vendor/topojson-client.v3.esm.js';
import {
  _state,
  state,
  manifest,
  initState,
  ElectionData,
  Seat,
  ElectionSummary,
} from './scripts/state.js';
import {
  fetchJson,
} from './scripts/files.js';
import {
  trackVirtualPageView,
} from './scripts/misc.js';
import {
  normalizeRegionKey,
  escapeHtml,
  formatInt,
  formatPct,
  formatSigned,
  deltaClass,
  seatLookupKey,
  getRegionLabel,
  buildWinnerBySeat,
} from './scripts/utils.js';
import {
  setElectionPreDataFetch,
  setHeader,
  setLeftBar,
  setMapControlOptions,
  setPageTitle,
  setPollTracker,
  domWireInit,
  renderMapInit,
  renderVoteTotals,
  wireVoteTotalsToggle,
  renderSeatList,
  setSelectedSeatRowByKey,
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

  setPageTitle();
  trackVirtualPageView();
  setLeftBar();
  // Render early with the election name only — subtitle will be overwritten with full summary
  // text (majority / hung parliament) once election results have loaded below.
  setHeader();
  if (view === 'polltracker') {
    await activatePollTrackerMode();
  } else {
    await activateElection(view);
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
async function activateElection(view) {
  // Pre-fetch: reset state and configure UI for this election type
  setElectionPreDataFetch();

  // Fetch: map topology, election results, and (if configured) comparison election results in parallel
  const { mapFile, dataFile, comparisonDataFile } = manifest.resolveElectionFiles(state.currentElection);
  const [mapData, resultsData, comparisonData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`),
    comparisonDataFile ? fetchJson(`data/${comparisonDataFile}`) : Promise.resolve(null),
  ]);

  // Parse fetched data into shared state, then populate controls and render.
  state.initMapData(mapData);
  state.initElectionData(resultsData);
  if (comparisonData) {
    state.initComparisonElectionData(comparisonData);
  }

  setMapControlOptions();
  setHeader(state.electionData.summary.text);
  state.setupMapData();
  renderMapInit();
  renderMap();
  renderRightPanel();

  const params = buildRouteSearchParams('election');
  window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
}

/**
 * Renders the map, seat list, vote totals, and choropleth legend from the per-render
 * data prepared by state.setupMapData().
 * @param {boolean} [preserveZoom=false] - When true, keep the current d3 pan/zoom transform.
 * @returns {void}
 */
function drawMap(preserveZoom = false) {
  state.setupMapData();
  hideSeatSearchSuggestions();
  renderMap(preserveZoom);
}

function renderMap(preserveZoom = false) {
  renderVoteTotals();
  renderSeatList();

  renderTopoMap(state.mapData, state.electionData.currentSeats, {
    visibleSeatKeys: state.mapSeatsVisible.seatKeys,
    choroplethConfig: state.choroplethConfig,
    preserveZoom,
    regionSummary: state.listRegionSummary,
    mapId: String(state.currentElection.mapId ?? ''),
  });

  renderChoroplethLegend(state.choroplethConfig);
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
  setPollTracker();
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
// WIRE CONTROLS
// =====================================================================

/**
 * Calls every wireX handler exactly once. Invoked from init() during boot.
 * @returns {void}
 */
function wireInit() {
  wireMapInteractions();
  wirePopupPanels();
  wireMapViewControls();
  wireSeatSearch();
  wirePostcodeSearch();
  wireSeatPopup();
  wireVoteTotalsToggle();
  wireWindowResize();
  wireVoteTotalsSorting();
}

/**
 * Attaches click handlers to all [data-map-action] buttons: zoom-in (×1.2), zoom-out (×0.83),
 * reset-zoom (restore default transform), and reset-view (zoom reset + clear all filters/choropleths).
 * @returns {void}
 */
function wireMapInteractions() {
  document.querySelectorAll('[data-map-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.getAttribute('data-map-action');
      if (action === 'zoom-in') _state.mapInteractionController.zoomBy(1.2);
      if (action === 'zoom-out') _state.mapInteractionController.zoomBy(0.83);
      if (action === 'reset-zoom') _state.mapInteractionController.reset();
      if (action === 'reset-view') {
        _state.mapInteractionController.reset();
        resetPrimaryFilters();
        resetChoropleths();
        drawMap();
      }
    });
  });

}

/**
 * Attaches click handlers to all [data-popup-action] buttons. 'toggle' opens the target panel
 * and closes all others (plus backdrop); 'close' closes all panels. On mobile the backdrop
 * overlay is shown/hidden alongside the panel. Guards against double-wiring via dataset flag.
 * @returns {void}
 */
function wirePopupPanels() {
  const popupOverlay = document.getElementById('mapsPopupOverlay');

  /** Closes all popup panels and hides the backdrop overlay. */
  function closeAllPopups() {
    document.querySelectorAll('.maps-control-popup').forEach((p) => { p.hidden = true; });
    if (popupOverlay) popupOverlay.hidden = true;
  }

  if (popupOverlay && popupOverlay.dataset.wired !== 'true') {
    popupOverlay.addEventListener('click', closeAllPopups);
    popupOverlay.dataset.wired = 'true';
  }

  document.querySelectorAll('[data-popup-action]').forEach((button) => {
    if (button.dataset.wired === 'true') return;

    button.addEventListener('click', () => {
      const action = button.getAttribute('data-popup-action');
      const targetId = button.getAttribute('data-popup-target');
      const panel = targetId ? document.getElementById(targetId) : null;
      if (!panel) return;

      if (action === 'close') {
        closeAllPopups();
        return;
      }

      if (action === 'toggle') {
        const willShow = panel.hidden;
        closeAllPopups();
        panel.hidden = !willShow;
        if (popupOverlay) popupOverlay.hidden = !willShow;
      }
    });

    button.dataset.wired = 'true';
  });
}

/**
 * Attaches change handlers to all filter and choropleth selects/inputs so any change reads
 * state and re-renders the map. Also wires the gains toggle, reset-filters, and
 * reset-choropleths buttons. Guards against double-wiring via dataset flag.
 * @returns {void}
 */
function wireMapViewControls() {
  if (filterPartySelect?.dataset.wired === 'true') return;

  /** Reads all filter/choropleth input values into state and re-renders the map. */
  const applyFromInputs = () => {
    syncMapControlStateFromInputs();
    drawMap(true);
  };

  [
    filterPartySelect,
    filterRegionSelect,
    filterSecondPartySelect,
    filterMajorityMinInput,
    filterMajorityMaxInput,
    choroplethTypeSelect,
    choroplethPartySelect,
  ].forEach((input) => {
    if (!input) return;
    input.addEventListener('change', applyFromInputs);
  });

  if (filterGainsButton) {
    filterGainsButton.addEventListener('click', () => {
      state.mapFilters.gainsOnly = !state.mapFilters.gainsOnly;
      syncMapControlInputsFromState();
      drawMap(true);
    });
  }

  if (filtersResetButton) {
    filtersResetButton.addEventListener('click', () => {
      resetPrimaryFilters();
      drawMap(true);
    });
  }

  if (choroplethsResetButton) {
    choroplethsResetButton.addEventListener('click', () => {
      resetChoropleths();
      drawMap(true);
    });
  }

  if (filterPartySelect) filterPartySelect.dataset.wired = 'true';
}

/**
 * Attaches all seat search event listeners: focus/input show the autocomplete dropdown,
 * change/blur submit the query, arrow keys navigate suggestions, Enter selects, Escape closes,
 * and an outside click dismisses the menu. Guards against double-wiring via dataset flag.
 * @returns {void}
 */
function wireSeatSearch() {
  if (!seatSearchInput || seatSearchInput.dataset.wired === 'true') return;
  ensureSeatSearchMenu();

  let lastSubmittedQuery = '';
  /**
   * Reads the current search input value and calls selectSeatBySearchQuery, deduplicating against the last submitted query.
   * @returns {void}
   */
  const submitSearch = () => {
    const query = String(seatSearchInput.value || '').trim();
    if (!query || query === lastSubmittedQuery) return;
    lastSubmittedQuery = query;
    selectSeatBySearchQuery(query);
  };

  seatSearchInput.addEventListener('focus', () => {
    showSeatSearchSuggestions(seatSearchInput.value);
  });
  seatSearchInput.addEventListener('input', () => {
    showSeatSearchSuggestions(seatSearchInput.value);
  });
  seatSearchInput.addEventListener('change', submitSearch);
  seatSearchInput.addEventListener('blur', () => {
    window.setTimeout(() => {
      hideSeatSearchSuggestions();
      submitSearch();
    }, 120);
  });
  seatSearchInput.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') {
      if (!_state.seatSearchSuggestions.length) {
        showSeatSearchSuggestions(seatSearchInput.value);
      }
      if (!_state.seatSearchSuggestions.length) return;
      event.preventDefault();
      _state.seatSearchSuggestionIndex = Math.min(_state.seatSearchSuggestionIndex + 1, _state.seatSearchSuggestions.length - 1);
      updateSeatSearchHighlight();
      return;
    }

    if (event.key === 'ArrowUp') {
      if (!_state.seatSearchSuggestions.length) return;
      event.preventDefault();
      _state.seatSearchSuggestionIndex = Math.max(_state.seatSearchSuggestionIndex - 1, 0);
      updateSeatSearchHighlight();
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      if (_state.seatSearchSuggestionIndex >= 0 && _state.seatSearchSuggestionIndex < _state.seatSearchSuggestions.length) {
        const selectedName = _state.seatSearchSuggestions[_state.seatSearchSuggestionIndex];
        seatSearchInput.value = selectedName;
      }
      hideSeatSearchSuggestions();
      submitSearch();
      return;
    }

    if (event.key === 'Escape') {
      hideSeatSearchSuggestions();
    }
  });
  document.addEventListener('click', (event) => {
    if (!(event.target instanceof Node)) return;
    if (seatSearchInput.contains(event.target)) return;
    if (_state.seatSearchMenuEl?.contains(event.target)) return;
    hideSeatSearchSuggestions();
  });

  seatSearchInput.dataset.wired = 'true';
}

/**
 * Attaches event listeners to the postcode search input. On Enter or blur, looks up the
 * postcode and zooms to the matched constituency. Disables the input during the fetch,
 * shows an inline error on failure, and deduplicates blur-after-Enter submissions.
 * Guards against double-wiring via dataset flag.
 * @returns {void}
 */
function wirePostcodeSearch() {
  if (!postcodeSearchInput || postcodeSearchInput.dataset.wired === 'true') return;

  let lastSubmittedPostcode = '';

  /**
   * Reads the postcode input, runs the lookup, and selects the resolved seat.
   * Deduplicates against the last submitted value to avoid double-fetching on blur after Enter.
   * @returns {void}
   */
  const submitPostcode = async () => {
    const query = postcodeSearchInput.value.trim();
    if (!query || query === lastSubmittedPostcode) return;
    lastSubmittedPostcode = query;
    postcodeSearchInput.disabled = true;
    clearPostcodeError();
    const constituencyName = await lookupPostcode(query);
    postcodeSearchInput.disabled = false;
    if (constituencyName) {
      selectSeatBySearchQuery(constituencyName);
    } else {
      showPostcodeError('Postcode not found');
    }
  };

  postcodeSearchInput.addEventListener('focus', () => {
    // If the error flash is showing, dismiss it and restore the original value
    // so the user can immediately retype without clearing "Postcode not found" manually.
    if (postcodeSearchInput.readOnly) {
      clearPostcodeError();
      postcodeSearchInput.value = '';
      lastSubmittedPostcode = '';
    }
  });
  postcodeSearchInput.addEventListener('input', () => {
    lastSubmittedPostcode = '';
    clearPostcodeError();
  });
  postcodeSearchInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      submitPostcode();
    }
  });
  postcodeSearchInput.addEventListener('blur', () => {
    window.setTimeout(submitPostcode, 120);
  });

  postcodeSearchInput.dataset.wired = 'true';
}

/**
 * Closes the seat detail popup and deselects the active map path when the close button is clicked.
 * @returns {void}
 */
function wireSeatPopup() {
  if (!seatPopupClose) return;
  seatPopupClose.addEventListener('click', () => {
    hideSeatPopup();
    _state.mapInteractionController.clearSelection?.();
  });
}

/**
 * Syncs the right panel height to the map on window resize, and re-renders the poll tracker
 * chart so its SVG dimensions update to the new container size.
 * @returns {void}
 */
function wireWindowResize() {
  window.addEventListener('resize', () => {
    renderRightPanel();
    if (state.view === 'polltracker') setPollTracker();
  });
}

/**
 * Attaches click and keyboard (Enter/Space) handlers to all [data-sort-key] table headers
 * to trigger sort changes and re-render the vote totals and right panel.
 * @returns {void}
 */
function wireVoteTotalsSorting() {
  document.querySelectorAll('th[data-sort-key]').forEach((header) => {
    const sortKey = header.getAttribute('data-sort-key');
    if (!sortKey) return;

    const trigger = () => {
      setSortDirection(sortKey);
      renderVoteTotals();
      renderRightPanel();
    };

    header.addEventListener('click', trigger);
    header.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        trigger();
      }
    });
  });
}

// =====================================================================
// BELOW HERE: UNREFACTORED LEGACY CODE
// Extract to a submodule or lift above the banner. Do not add new code.
// =====================================================================

/**
 * Pure utility functions extracted from electionmaps.js for testability.
 * No DOM dependencies, no module-level _state.
 */

// ── Formatting ───────────────────────────────────────────────────────────────

// ── Region normalization ─────────────────────────────────────────────────────

/**
 * Converts a region key or name to title case, splitting on camelCase boundaries, hyphens, and underscores. Returns 'Unknown' for empty input.
 * @param {string} regionKey - Region key or name to convert.
 * @returns {string} Title-cased display label (e.g. 'North West England'), or 'Unknown' for empty input.
 */
// ── Seat utilities ───────────────────────────────────────────────────────────

/**
 * Returns an array of { party, votes } objects for a seat, sorted descending by vote count, excluding parties with zero votes.
 * @param {object} seat - Seat object with a `votes` map of party key to vote count.
 * @returns {Array<{party: string, votes: number}>} Sorted array of party vote entries, highest first.
 */
// TODO: migrate callers to Seat static methods in state.js
function sortedSeatVoteRows2(seat) {
  return Object.entries(seat?.votes || {})
    .map(([party, votes]) => ({ party, votes: Number(votes || 0) }))
    .filter((row) => row.votes > 0)
    .sort((a, b) => b.votes - a.votes);
}

/**
 * Returns { pct, raw } for the winning majority in a seat: pct as a percentage of total votes, raw as the vote margin between first and second place.
 * @param {object} seat - Seat object with a `votes` map and optional `turnout`.
 * @returns {{pct: number, raw: number}} Majority as a percentage of total votes and as a raw vote count.
 */
// TODO: migrate callers to Seat.majorityStats in state.js
function seatMajorityStats2(seat) {
  const voteRows = sortedSeatVoteRows2(seat);
  if (voteRows.length < 2) return { pct: 0, raw: 0 };
  const marginVotes = voteRows[0].votes - voteRows[1].votes;
  const totalVotes = seat.turnout;
  if (totalVotes <= 0) return { pct: 0, raw: marginVotes };
  return { pct: (marginVotes / totalVotes) * 100, raw: marginVotes };
}

/**
 * Returns the previous winner's party key if the seat changed hands between comparisonSeat and currentSeat, or null if there was no change or no comparison available.
 * @param {object} currentSeat - The seat in its current state, with a `winner` property.
 * @param {object|null} comparisonSeat - The seat in its comparison state, or null if no comparison is available.
 * @returns {string|null} The previous winner's party key if a gain occurred, otherwise null.
 */
// TODO: migrate callers to Seat.gainFromParty in state.js
function seatGainFromPartyKey2(currentSeat, comparisonSeat) {
  const winner = currentSeat?.winner || 'others';
  const previousWinner = comparisonSeat?.winner || null;
  if (!previousWinner || previousWinner === winner) return null;
  return previousWinner;
}

/**
 * Builds a Map from seatLookupKey to seat object for fast seat lookups.
 * @param {Array<object>} seats - Array of seat objects, each with a `seat` name property.
 * @returns {Map<string, object>} Map from lowercase seat name key to seat object.
 */
export function buildSeatIndex(seats) {
  const byKey = new Map();
  (seats || []).forEach((seat) => {
    if (!seat?.seat) return;
    byKey.set(seatLookupKey(seat.seat), seat);
  });
  return byKey;
}

/**
 * Clamps value to [minimum, maximum]. Returns minimum if value is not finite.
 * @param {number} value - Value to clamp.
 * @param {number} minimum - Lower bound (inclusive).
 * @param {number} maximum - Upper bound (inclusive).
 * @returns {number} Clamped numeric value within [minimum, maximum].
 */
function clampNumber(value, minimum, maximum) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return minimum;
  return Math.max(minimum, Math.min(maximum, numeric));
}

// ── Seat / feature utilities ──────────────────────────────────────────────────

/**
 * Extracts the seat name from a TopoJSON feature's properties.
 * Tries `name`, `seat_name`, `seat`, `constituency`, and `Name` in order.
 * Returns null if none of the known properties are present.
 * @param {object} featureDatum - A TopoJSON feature object with a `properties` map.
 * @returns {string|null} Seat name extracted from feature properties, or null if not found.
 */
function seatNameFromFeature(featureDatum) {
  const props = featureDatum?.properties || {};
  return props.name || props.seat_name || props.seat || props.constituency || props.Name || null;
}

// ── Map / region utilities ────────────────────────────────────────────────────

// ── Election file resolution ──────────────────────────────────────────────────

const mapSvg = document.querySelector('.maps-svg');
const mapContent = document.getElementById('mapContent');
const zoomValue = document.getElementById('mapsZoomValue');
const seatCard = document.getElementById('mapsSeatCard');
const seatSearchInput = document.getElementById('maps-seat-search');
const postcodeSearchInput = document.getElementById('maps-postcode-search');
const mapsStage = document.querySelector('.maps-stage');
const mapsPanelRight = document.querySelector('.maps-panel-right');
const seatPopup = document.getElementById('mapsSeatPopup');
const seatPopupTitle = document.getElementById('mapsSeatPopupTitle');
const seatPopupMeta = document.getElementById('mapsSeatPopupMeta');
const seatPopupList = document.getElementById('mapsSeatPopupList');
const seatPopupClose = document.getElementById('mapsSeatPopupClose');
const choroplethLegend = document.getElementById('mapsChoroplethLegend');

const filterPartySelect = document.getElementById('mapsFilterParty');
const filterRegionSelect = document.getElementById('mapsFilterRegion');
const filterSecondPartyGroup = document.getElementById('mapsFilterSecondPartyGroup');
const filterSecondPartySelect = document.getElementById('mapsFilterSecondParty');
const filterMajorityMinInput = document.getElementById('mapsFilterMajorityMin');
const filterMajorityMaxInput = document.getElementById('mapsFilterMajorityMax');
const filterGainsButton = document.getElementById('mapsFilterGainsOnly');
const filtersResetButton = document.getElementById('mapsFiltersReset');
const choroplethTypeSelect = document.getElementById('mapsChoroplethType');
const choroplethPartySelect = document.getElementById('mapsChoroplethParty');
const choroplethsResetButton = document.getElementById('mapsChoroplethsReset');
const choroplethVoteShareChangeOption = document.getElementById('mapsChoroplethVoteShareChangeOption');
const dataInfoButton = document.getElementById('mapsDataInfoBtn');
// Canonical map name strings used to route postcode lookups to the correct
// postcodes.io endpoint and to identify whether postcode search is supported.

// Maps old 2021 Holyrood constituency names (as returned by postcodes.io) to their
// 2026 boundary equivalents. Used as a fallback when a returned name has no match
// in the current seat data. Where two old seats merged into one, both map to the new
// combined name — best-guess only, since the boundary changed at the postcode level.
const HOLYROOD_2021_TO_2026_NAME = {
  'Aberdeen South and North Kincardine': 'Aberdeen Deeside and North Kincardine',
  'Airdrie and Shotts': 'Airdrie',
  'East Lothian': 'East Lothian Coast and Lammermuirs',
  'Edinburgh Eastern': 'Edinburgh Eastern, Musselburgh and Tranent',
  'Edinburgh Northern and Leith': 'Edinburgh North Eastern and Leith',
  'Edinburgh Pentlands': 'Edinburgh South Western',
  'Edinburgh Western': 'Edinburgh North Western',
  'Falkirk East': 'Falkirk East and Linlithgow',
  'Glasgow Cathcart': 'Glasgow Cathcart and Pollok',
  'Glasgow Kelvin': 'Glasgow Kelvin and Maryhill',
  'Glasgow Maryhill and Springburn': 'Glasgow Kelvin and Maryhill',
  'Glasgow Pollok': 'Glasgow Cathcart and Pollok',
  'Glasgow Provan': 'Glasgow Easterhouse and Springburn',
  'Glasgow Shettleston': 'Glasgow Baillieston and Shettleston',
  'Greenock and Inverclyde': 'Inverclyde',
  'Linlithgow': 'Falkirk East and Linlithgow',
  'Midlothian North and Musselburgh': 'Midlothian North',
  'North East Fife': 'Fife North East',
  'Renfrewshire North and West': 'Renfrewshire North and Cardonald',
  'Renfrewshire South': 'Renfrewshire West and Levern Valley',
  'Rutherglen': 'Rutherglen and Cambuslang',
};

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

const INITIAL_MAP_SCALE = 1.0;
const INITIAL_MAP_SCALE_MOBILE = 1.26;
const ZOOM_MIN_SCALE = 1;
const ZOOM_MAX_SCALE = 17.5;
const LEGACY_CLICK_ZOOM_BASE = 0.05;
const CLICK_ZOOM_DURATION_MS = 1500;
const RESET_ZOOM_DURATION_MS = 500;
const MAX_SEAT_SEARCH_SUGGESTIONS = 10;

/**
 * Converts a d3 zoom scale value to a human-readable percentage string relative to the initial map scale.
 * @param {number} scaleValue - Raw d3 zoom transform scale value.
 * @returns {string} Percentage string relative to INITIAL_MAP_SCALE (e.g. '150%').
 */
function formatZoomPct(scaleValue) {
  const baselineScale = Math.max(1, Number(INITIAL_MAP_SCALE) || 1);
  const ratio = Number(scaleValue) / baselineScale;
  if (!Number.isFinite(ratio) || ratio <= 0) return '100%';
  return `${Math.round(ratio * 100)}%`;
}

/**
 * Updates state.voteTotals.sort: toggles direction if the same key is re-selected, otherwise switches to the new key with a default direction.
 * @param {string} sortKey - Column key to sort by (e.g. 'seats', 'votes', 'party').
 * @returns {void}
 */
function setSortDirection(sortKey) {
  if (state.voteTotals.sort.key === sortKey) {
    state.voteTotals.sort.direction = state.voteTotals.sort.direction === 'asc' ? 'desc' : 'asc';
    return;
  }
  state.voteTotals.sort.key = sortKey;
  state.voteTotals.sort.direction = sortKey === 'party' ? 'asc' : 'desc';
}

// ── Holyrood tab helpers ──────────────────────────────────────────────────────

/**
 * Pushes the current state.mapFilters and state.mapChoropleths values into the DOM filter/choropleth inputs and toggles second-party group visibility.
 * @returns {void}
 */
function syncMapControlInputsFromState() {
  filterPartySelect.value = state.mapFilters.party;
  filterRegionSelect.value = state.mapFilters.region;

  const showSecondPlaceFilter = state.mapFilters.party !== 'all';
  filterSecondPartyGroup.hidden = !showSecondPlaceFilter;
  if (!showSecondPlaceFilter) {
    state.mapFilters.secondParty = 'all';
  }
  filterSecondPartySelect.value = state.mapFilters.secondParty;

  filterMajorityMinInput.value = String(state.mapFilters.majorityMin);
  filterMajorityMaxInput.value = String(state.mapFilters.majorityMax);
  filterGainsButton.classList.toggle('is-active', state.mapFilters.gainsOnly);

  choroplethTypeSelect.value = state.mapChoropleths.type;
  choroplethPartySelect.value = state.mapChoropleths.party;
}

/**
 * Reads the DOM filter/choropleth inputs into state.mapFilters and state.mapChoropleths, normalizing and clamping values, then syncs the inputs back.
 * @returns {void}
 */
function syncMapControlStateFromInputs() {
  state.mapFilters.party = filterPartySelect.value || 'all';
  state.mapFilters.region = filterRegionSelect.value || 'all';
  if (state.mapFilters.party === 'all') {
    state.mapFilters.secondParty = 'all';
  } else {
    state.mapFilters.secondParty = filterSecondPartySelect.value || 'all';
  }
  state.mapFilters.majorityMin = clampNumber(filterMajorityMinInput.value, 0, 100);
  state.mapFilters.majorityMax = clampNumber(filterMajorityMaxInput.value, 0, 100);
  if (state.mapFilters.majorityMin > state.mapFilters.majorityMax) {
    const swap = state.mapFilters.majorityMin;
    state.mapFilters.majorityMin = state.mapFilters.majorityMax;
    state.mapFilters.majorityMax = swap;
  }

  state.mapChoropleths.type = choroplethTypeSelect.value || 'none';
  state.mapChoropleths.party = choroplethPartySelect.value || 'all';

  syncMapControlInputsFromState();
}

/**
 * Resets all primary filter state (party, region, majority range, gains toggle) to defaults and syncs the controls.
 * @returns {void}
 */
function resetPrimaryFilters() {
  state.mapFilters.party = 'all';
  state.mapFilters.region = 'all';
  state.mapFilters.secondParty = 'all';
  state.mapFilters.majorityMin = 0;
  state.mapFilters.majorityMax = 100;
  state.mapFilters.gainsOnly = false;
  syncMapControlInputsFromState();
}

/**
 * Resets choropleth type and party to defaults and syncs the controls.
 * @returns {void}
 */
function resetChoropleths() {
  state.mapChoropleths.type = 'none';
  state.mapChoropleths.party = 'all';
  syncMapControlInputsFromState();
}

/**
 * Renders the choropleth colour gradient legend into the legend element, or hides it when choropleth is disabled.
 * @param {{enabled: boolean, legend?: object, legendText?: string}} choroplethConfig - Choropleth config as returned by state.buildChoroplethConfig().
 * @returns {void}
 */
function renderChoroplethLegend(choroplethConfig) {
  if (!choroplethLegend) return;
  if (!choroplethConfig?.enabled) {
    choroplethLegend.hidden = true;
    choroplethLegend.innerHTML = '';
    return;
  }

  const legend = choroplethConfig.legend;
  if (!legend) {
    choroplethLegend.textContent = `Choropleth: ${choroplethConfig.legendText}`;
    choroplethLegend.hidden = false;
    return;
  }

  const gradient = legend.isDelta
    ? `linear-gradient(90deg, ${legend.startColour} 0%, ${legend.midColour} 50%, ${legend.endColour} 100%)`
    : `linear-gradient(90deg, ${legend.startColour} 0%, ${legend.endColour} 100%)`;

  choroplethLegend.innerHTML = `
    <div class="maps-choropleth-legend-title">${legend.title}</div>
    <div class="maps-choropleth-legend-bar" style="background:${gradient}"></div>
    <div class="maps-choropleth-legend-labels">
      <span>${legend.minLabel}</span>
      ${legend.isDelta ? `<span>${legend.midLabel}</span>` : ''}
      <span>${legend.maxLabel}</span>
    </div>
  `;
  choroplethLegend.hidden = false;
}

/**
 * Hides the seat detail popup and clears the tracked open seat name.
 * @returns {void}
 */
function hideSeatPopup() {
  if (!seatPopup) return;
  seatPopup.hidden = true;
  state.map.openSeat = null;
}

/**
 * Renders the seat detail popup for seatName, showing majority, gain indicator, and a ranked vote share bar chart with comparison deltas.
 * @param {string} seatName - Display name of the seat to show; looked up in state.electionData.seatsByKey.
 * @returns {void}
 */
function renderSeatPopup(seatName) {
  if (!seatPopup || !seatPopupTitle || !seatPopupMeta || !seatPopupList) return;

  const seatKey = seatLookupKey(seatName);
  const seat = state.electionData.seatsByKey.get(seatKey);
  if (!seat) {
    hideSeatPopup();
    return;
  }
  state.map.openSeat = seatName;

  const comparisonSeat = _state.comparisonSeatsByKey.get(seatKey) || null;
  const gainFrom = seatGainFromPartyKey2(seat, comparisonSeat);
  const majority = seatMajorityStats2(seat);
  const isReferendum = state.currentElection.id === 'eu-referendum-2016';
  const showTurnout = state.currentElection.type !== 'model_uns' && !isReferendum;
  const showRawMajority = state.currentElection.type !== 'model_uns' && !isReferendum;

  seatPopupTitle.textContent = seat.seat;
  seatPopupMeta.innerHTML = `
    ${gainFrom ? `<span class="maps-popup-meta-item">FROM ${manifest.labelParty(gainFrom)} <span class="maps-seat-icon" style="background:${manifest.colourParty(gainFrom)}"></span></span>` : ''}
    <span class="maps-popup-meta-item">${getRegionLabel(seat.region, state.currentRegionLabelsByKey)}</span>
    <span class="maps-popup-meta-item">Majority: ${formatPct(majority.pct)}%${showRawMajority ? ` = ${formatInt(majority.raw)}` : ''}</span>
    ${showTurnout ? `<span class="maps-popup-meta-item">Turnout: ${formatInt(seat.turnout)}</span>` : ''}
  `;

  const currentTurnout = seat.turnout;
  const comparisonTurnout = comparisonSeat?.turnout ?? 0;
  const comparisonVotes = comparisonSeat?.votes || {};

  const rows = Object.entries(seat.votes || {})
    .map(([party, votes]) => {
      const voteTotal = Number(votes || 0);
      const pct = currentTurnout > 0 ? (voteTotal / currentTurnout) * 100 : 0;
      const prevPct = comparisonTurnout > 0 ? ((Number(comparisonVotes[party] || 0) / comparisonTurnout) * 100) : null;
      const delta = prevPct == null ? null : pct - prevPct;
      return { party, pct, delta };
    })
    .sort((a, b) => b.pct - a.pct)
    .slice(0, 8);

  const maxPct = rows.reduce((max, row) => Math.max(max, row.pct), 0);

  seatPopupList.innerHTML = '';
  rows.forEach((row) => {
    const scaledWidth = maxPct > 0 ? (row.pct / maxPct) * 75 : 0;
    const barWidth = Math.max(0, Math.min(75, scaledWidth));
    const item = document.createElement('div');
    item.className = 'maps-popup-row';
    item.style.setProperty('--maps-popup-bar-width', `${barWidth}%`);
    item.style.setProperty('--maps-popup-bar-colour', manifest.colourParty(row.party));
    item.innerHTML = `
      <div class="maps-popup-party"><span class="maps-seat-icon" style="background:${manifest.colourParty(row.party)}"></span>${escapeHtml(manifest.labelParty(row.party))}</div>
      <div class="maps-popup-values">
        <span>${formatPct(row.pct)}%</span>
        ${row.delta == null ? '' : `<span class="${deltaClass(row.delta)}">${formatSigned(row.delta, 2)}</span>`}
      </div>
    `;
    seatPopupList.appendChild(item);
  });

  seatPopup.hidden = false;
}

/**
 * Creates the autocomplete dropdown menu element adjacent to the seat search input if it doesn't exist yet. Returns the element or null if the input is absent.
 * @returns {HTMLElement|null} The autocomplete menu element, or null if the seat search input is not in the DOM.
 */
function ensureSeatSearchMenu() {
  if (_state.seatSearchMenuEl || !seatSearchInput) return _state.seatSearchMenuEl;
  const searchGroup = seatSearchInput.closest('.maps-toolbar-group-search') || seatSearchInput.parentElement;
  if (!searchGroup) return null;

  const menu = document.createElement('div');
  menu.className = 'maps-seat-search-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'listbox');
  menu.id = 'mapsSeatSearchMenu';
  searchGroup.appendChild(menu);
  _state.seatSearchMenuEl = menu;
  return _state.seatSearchMenuEl;
}

/**
 * Hides the autocomplete dropdown and clears the keyboard suggestion index.
 * @returns {void}
 */
function hideSeatSearchSuggestions() {
  _state.seatSearchSuggestionIndex = -1;
  if (!_state.seatSearchMenuEl) return;
  _state.seatSearchMenuEl.hidden = true;
  _state.seatSearchMenuEl.innerHTML = '';
}

/**
 * Populates the autocomplete dropdown with up to MAX_SEAT_SEARCH_SUGGESTIONS seat names matching query (starts-with first, then contains).
 * @param {string} [query=''] - Search string to match against; empty string shows all names up to the limit.
 * @returns {void}
 */
function showSeatSearchSuggestions(query = '') {
  const menu = ensureSeatSearchMenu();
  if (!menu) return;

  const queryText = String(query || '').trim().toLowerCase();
  const startsWithMatches = [];
  const includesMatches = [];
  state.seatSearchNames.forEach((name) => {
    const lowerName = name.toLowerCase();
    if (!queryText || lowerName.startsWith(queryText)) {
      startsWithMatches.push(name);
      return;
    }
    if (lowerName.includes(queryText)) includesMatches.push(name);
  });

  _state.seatSearchSuggestions = [...startsWithMatches, ...includesMatches].slice(0, MAX_SEAT_SEARCH_SUGGESTIONS);
  _state.seatSearchSuggestionIndex = -1;
  menu.innerHTML = '';

  if (!_state.seatSearchSuggestions.length) {
    menu.hidden = true;
    return;
  }

  _state.seatSearchSuggestions.forEach((name, index) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'maps-seat-search-item';
    item.textContent = name;
    item.setAttribute('role', 'option');
    item.dataset.index = String(index);
    item.addEventListener('mousedown', (event) => {
      event.preventDefault();
    });
    item.addEventListener('click', () => {
      seatSearchInput.value = name;
      hideSeatSearchSuggestions();
      selectSeatBySearchQuery(name);
    });
    menu.appendChild(item);
  });

  menu.hidden = false;
}

/**
 * Updates the keyboard-active (is-active) class on suggestion items to reflect _state.seatSearchSuggestionIndex.
 * @returns {void}
 */
function updateSeatSearchHighlight() {
  if (!_state.seatSearchMenuEl) return;
  const options = _state.seatSearchMenuEl.querySelectorAll('.maps-seat-search-item');
  options.forEach((option) => {
    const index = Number(option.dataset.index);
    option.classList.toggle('is-active', index === _state.seatSearchSuggestionIndex);
  });
}

/**
 * Resolves a search query to a seat name (exact → starts-with → contains), zooms the map, selects the list row, and opens the popup.
 * @param {string} query - Raw search string as entered by the user.
 * @returns {void}
 */
function selectSeatBySearchQuery(query) {
  const rawQuery = String(query || '').trim();
  if (!rawQuery) return;

  const directKey = seatLookupKey(rawQuery);
  let seatName = state.currentSeatNameByKey.get(directKey) || null;

  if (!seatName) {
    const queryLower = rawQuery.toLowerCase();
    seatName = Array.from(state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().startsWith(queryLower))
      || Array.from(state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().includes(queryLower))
      || null;
  }

  if (!seatName) {
    return;
  }

  const seatKey = seatLookupKey(seatName);
  const zoomed = _state.mapInteractionController.zoomToSeat(seatName);
  if (zoomed) {
    setSelectedSeatRowByKey(seatKey);
    renderSeatPopup(seatName);
    if (seatSearchInput) seatSearchInput.value = seatName;
    return;
  }

}

/**
 * Returns a descriptive name for a map ID. Checks the manifest first (in case a name
 * field is added later), then falls back to the known map name constants.
 * @param {number} mapId
 * @returns {string|null}
 */

/**
 * Flashes an error message inside the postcode input for 2 seconds, then clears the
 * input so the placeholder is shown again. The input is made readonly during the flash
 * to prevent accidental edits. Cancels any in-flight error flash before starting a new one.
 * @param {string} msg - The error text to display in the input.
 * @returns {void}
 */
function showPostcodeError(msg) {
  if (!postcodeSearchInput) return;
  clearPostcodeError();
  postcodeSearchInput.value = msg;
  postcodeSearchInput.readOnly = true;
  postcodeSearchInput.classList.add('is-postcode-error');
  _state.postcodeErrorTimeout = window.setTimeout(() => {
    postcodeSearchInput.readOnly = false;
    postcodeSearchInput.value = '';
    postcodeSearchInput.classList.remove('is-postcode-error');
    _state.postcodeErrorTimeout = null;
  }, 2000);
}

/**
 * Cancels any active postcode error flash and removes the error style.
 * Does not restore the input value — caller is responsible for that if needed.
 * @returns {void}
 */
function clearPostcodeError() {
  if (_state.postcodeErrorTimeout) {
    clearTimeout(_state.postcodeErrorTimeout);
    _state.postcodeErrorTimeout = null;
  }
  if (postcodeSearchInput) {
    postcodeSearchInput.readOnly = false;
    postcodeSearchInput.classList.remove('is-postcode-error');
  }
}

/**
 * Looks up a postcode via the postcodes.io API and returns the constituency name,
 * or null if the postcode is not found or the current map does not support lookup.
 * Selects the Westminster or Scottish endpoint based on the current election's mapId.
 * @param {string} postcode - The raw postcode string entered by the user.
 * @returns {Promise<string|null>} The constituency name, or null on failure.
 */
async function lookupPostcode(postcode) {
  if (!state.mapConfig?.postcodeSupported) return null;

  // Strip all whitespace then re-insert the canonical space before the inward code
  // (always the last 3 characters). Both endpoints require this format.
  const stripped = postcode.trim().toUpperCase().replace(/\s+/g, '');
  const normalised = stripped.length >= 5 ? `${stripped.slice(0, -3)} ${stripped.slice(-3)}` : stripped;

  const mapName = state.mapConfig?.name ?? null;
  let url = '';
  let resultProperty = '';

  switch (mapName) {
    case 'holyrood-2026':
      url = `https://api.postcodes.io/scotland/postcodes/${encodeURIComponent(normalised)}`;
      resultProperty = 'scottish_parliamentary_constituency';
      break;
    case 'westminster-2024':
      url = `https://api.postcodes.io/postcodes/${encodeURIComponent(normalised)}`;
      resultProperty = 'parliamentary_constituency_2024';
      break;
    default:
      return null;
  }

  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json();
    const rawName = data?.result?.[resultProperty] ?? null;

    if (!rawName) return null;

    // Normalise accented characters to ASCII so names like "Ynys Môn" match
    // our seat data which stores the unaccented form "Ynys Mon".
    const constituencyName = rawName.normalize('NFD').replace(/[\u0300-\u036f]/g, '');

    // If the returned name has no match in the current seat index, try the
    // Holyrood 2021→2026 boundary mapping as a best-guess fallback.
    // Only applied on Holyrood to avoid false rewrites on Westminster lookups.
    const seatKey = seatLookupKey(constituencyName);
    if (!state.currentSeatNameByKey.has(seatKey) && mapName === 'holyrood-2026') {
      const mapped = HOLYROOD_2021_TO_2026_NAME[constituencyName] ?? null;
      if (mapped) return mapped;
    }

    return constituencyName;
  } catch {
    return null;
  }
}

/**
 * Sets the right panel's height to match the map stage height so the two columns stay aligned. On mobile the panel stacks below the map so no height sync is needed.
 * @returns {void}
 */
function renderRightPanel() {
  if (!mapsStage || !mapsPanelRight) return;

  if (window.innerWidth <= 980) {
    mapsPanelRight.style.height = '';
    mapsPanelRight.style.maxHeight = '';
    return;
  }

  const stageHeight = mapsStage.getBoundingClientRect().height;
  if (!Number.isFinite(stageHeight) || stageHeight <= 0) return;

  mapsPanelRight.style.height = `${Math.round(stageHeight)}px`;
  mapsPanelRight.style.maxHeight = `${Math.round(stageHeight)}px`;
}

// TODO: prefer `state.electionData.summary.text` for new callers
/**
 * Updates the page title and subtitle with the election name and leading-party majority (or hung-parliament message).
 * @param {object} election - Election entry object with a `name` and optionally a `type` property.
 * @param {{parties: Array<{party: string, seats: number}>, totalSeats: number}} summary - Election summary as returned by `ElectionSummary.summarize`.
 * @returns {void}
 */
function updateTopSummary(election, summary) {
  const top = summary.parties[0];
  const leadSeats = Number(top?.seats || 0);
  const totalSeats = Number(summary.totalSeats || 0);
  const majorityThreshold = totalSeats / 2;
  const hasMajority = leadSeats > majorityThreshold;
  const majority = hasMajority ? Math.round(2 * (leadSeats - majorityThreshold)) : 0;

  const subtitleText = hasMajority
    ? `${election.name} · ${manifest.labelParty(top?.party || 'others')} majority: ${majority}`
    : `${election.name} · Hung parliament - largest party ${manifest.labelParty(top?.party || 'others')} with ${formatInt(leadSeats)} seats`;
  setHeader(subtitleText);
}

/**
 * Returns the d3 zoom transform that centres the map at INITIAL_MAP_SCALE (or INITIAL_MAP_SCALE_MOBILE on narrow screens).
 * @param {number} width - SVG viewport width in pixels.
 * @param {number} height - SVG viewport height in pixels.
 * @returns {object} d3 zoom identity transform scaled and translated to centre the map.
 */
function getInitialZoomTransform(width, height) {
  const isMobile = window.innerWidth <= 980;
  const scale = Math.max(1, Number(isMobile ? INITIAL_MAP_SCALE_MOBILE : INITIAL_MAP_SCALE) || 1);
  const tx = width / 2 - scale * (width / 2);
  const ty = height / 2 - scale * (height / 2);
  return d3.zoomIdentity.translate(tx, ty).scale(scale);
}

/**
 * Computes a d3 zoom transform to zoom into a specific map feature, scaling based on the square-root of its bounding box dimensions.
 * @param {object} path - d3 geo path generator used to compute the feature's bounding box.
 * @param {object} featureDatum - GeoJSON feature to zoom to.
 * @param {number} width - SVG viewport width in pixels.
 * @param {number} height - SVG viewport height in pixels.
 * @returns {object} d3 zoom identity transform targeting the feature centre with a scale derived from its size.
 */
function getLegacySeatZoomTransform(path, featureDatum, width, height) {
  const bounds = path.bounds(featureDatum);
  const dx = Math.max(0, bounds[1][0] - bounds[0][0]);
  const dy = Math.max(0, bounds[1][1] - bounds[0][1]);
  const dxAdjusted = Math.sqrt(dx);
  const dyAdjusted = Math.sqrt(dy);
  const cx = (bounds[0][0] + bounds[1][0]) / 2;
  const cy = (bounds[0][1] + bounds[1][1]) / 2;
  const denom = Math.max(dxAdjusted / width, dyAdjusted / height, 1e-9);
  const scale = Math.max(ZOOM_MIN_SCALE, Math.min(ZOOM_MAX_SCALE, LEGACY_CLICK_ZOOM_BASE / denom));
  const translate = [width / 2 - scale * cx, height / 2 - scale * cy];

  return d3.zoomIdentity.translate(translate[0], translate[1]).scale(scale);
}

/**
 * Renders the full TopoJSON map into mapSvg using D3.
 * Creates seat path elements coloured by winner or choropleth metric, wires click-to-zoom and hover handlers,
 * draws region boundary overlays, and sets up _state.mapInteractionController for external zoom/reset/highlight calls.
 * Accepts { visibleSeatKeys, choroplethConfig, preserveZoom } in options.
 * @param {object} mapData - TopoJSON topology object with a single named objects entry.
 * @param {Array<object>} seats - Current seat objects used to determine winner colours.
 * @param {{visibleSeatKeys?: Set<string>, choroplethConfig?: object, preserveZoom?: boolean}} [options={}] - Rendering options including filter visibility, choropleth config, and whether to preserve the current zoom transform.
 * @returns {void}
 */
function renderTopoMap(mapData, seats, options = {}) {
  if (!mapSvg || !mapContent || !zoomValue) return;

  const objectName = Object.keys(mapData?.objects || {})[0];
  if (!objectName) throw new Error('TopoJSON missing objects');

  const object = mapData.objects[objectName];
  const featureCollection = topojsonFeature(mapData, object);
  const features = featureCollection?.features || [];

  if (!features.length) throw new Error('No map features available');

  const vb = mapSvg.viewBox?.baseVal;
  const width = vb?.width || 1200;
  const height = vb?.height || 900;

  const projection = d3.geoMercator().fitSize([width, height], featureCollection);
  const path = d3.geoPath(projection);

  const svg = d3.select(mapSvg);
  const content = d3.select(mapContent);
  content.selectAll('*').remove();

  const winnerBySeat = buildWinnerBySeat(seats);
  const visibleSeatKeys = options.visibleSeatKeys || null;
  const choroplethConfig = options.choroplethConfig || { enabled: false };
  const featureBySeat = new Map();
  const seatPathByKey = new Map();
  _state.activeSeatPathNode = null;

  features.forEach((featureDatum) => {
    const seatName = seatNameFromFeature(featureDatum);
    if (!seatName) return;
    featureBySeat.set(seatLookupKey(seatName), featureDatum);
  });

  const zoomRoot = content.append('g').attr('class', 'maps-geo-root');
  zoomRoot.append('rect').attr('class', 'maps-map-bg').attr('x', 0).attr('y', 0).attr('width', width).attr('height', height);
  const zoomLayer = zoomRoot.append('g').attr('class', 'maps-geo-layer');
  const seatLayer = zoomLayer.append('g').attr('class', 'maps-seat-layer');
  const boundaryLayer = zoomLayer.append('g').attr('class', 'maps-boundary-layer');

  const zoomBehavior = d3
    .zoom()
    .scaleExtent([ZOOM_MIN_SCALE, ZOOM_MAX_SCALE])
    .on('zoom', (event) => {
      zoomLayer.attr('transform', event.transform.toString());
      zoomValue.textContent = formatZoomPct(event.transform.k);
    });

  svg.call(zoomBehavior);
  const initialTransform = getInitialZoomTransform(width, height);

  /**
   * Animates the map zoom to centre on a GeoJSON feature using the legacy seat zoom transform.
   * @param {object} featureDatum - GeoJSON feature to zoom to.
   * @returns {void}
   */
  const zoomToFeature = (featureDatum) => {
    const targetTransform = getLegacySeatZoomTransform(path, featureDatum, width, height);
    svg.transition().duration(CLICK_ZOOM_DURATION_MS).call(zoomBehavior.transform, targetTransform);
  };

  /** Removes the active highlight class from the currently active seat path and clears the reference. */
  const clearActiveSeatPath = () => {
    if (!_state.activeSeatPathNode) return;
    d3.select(_state.activeSeatPathNode).classed('maps-region-path-active', false);
    _state.activeSeatPathNode = null;
  };

  /**
   * Sets pathNode as the active seat path, removing the highlight from any previously active path and raising pathNode to the front.
   * @param {SVGPathElement} pathNode - The SVG path element to activate.
   * @returns {void}
   */
  const setActiveSeatPath = (pathNode) => {
    if (!pathNode) return;
    if (_state.activeSeatPathNode && _state.activeSeatPathNode !== pathNode) {
      d3.select(_state.activeSeatPathNode).classed('maps-region-path-active', false);
    }
    _state.activeSeatPathNode = pathNode;
    d3.select(pathNode).classed('maps-region-path-active', true).raise();
  };

  /** Hides the seat popup, clears the active path highlight, and animates the map back to the initial zoom transform. */
  const resetZoom = () => {
    hideSeatPopup();
    clearActiveSeatPath();
    svg.transition().duration(RESET_ZOOM_DURATION_MS).call(zoomBehavior.transform, initialTransform);
  };

  _state.mapInteractionController = {
    zoomBy: (factor) => svg.transition().duration(180).call(zoomBehavior.scaleBy, factor),
    reset: resetZoom,
    clearSelection: clearActiveSeatPath,
    highlightSeat: (seatName) => {
      const seatKey = seatLookupKey(seatName);
      const seatPathNode = seatPathByKey.get(seatKey);
      if (seatPathNode) setActiveSeatPath(seatPathNode);
    },
    zoomToSeat: (seatName) => {
      const seatKey = seatLookupKey(seatName);
      const featureDatum = featureBySeat.get(seatKey);
      if (!featureDatum) return false;
      const seatPathNode = seatPathByKey.get(seatKey);
      if (seatPathNode) {
        setActiveSeatPath(seatPathNode);
      }
      zoomToFeature(featureDatum);
      return true;
    },
    flashRegion: () => {},
  };

  // Draw cross-region boundary mesh — interior edges only, no coastlines.
  const regionBoundaryMesh = topojsonMesh(
    mapData, object,
    (a, b) => a && b && a !== b && a.properties?.region !== b.properties?.region
  );
  if (regionBoundaryMesh?.coordinates?.length) {
    boundaryLayer.append('path')
      .datum(regionBoundaryMesh)
      .attr('class', 'maps-region-boundary')
      .attr('d', path);
  }

  // ── Draw individual constituency seat paths ───────────────────────────────

  const seatPaths = seatLayer
    .selectAll('path')
    .data(features)
    .join('path')
    .attr('class', 'maps-region-path')
    .attr('d', path)
    .attr('fill', (datum) => {
      const seatName = seatNameFromFeature(datum);
      if (!seatName) return manifest.colourParty('others');
      const seatKey = seatLookupKey(seatName);
      const seat = state.electionData.seatsByKey.get(seatKey);
      if (!seat) return manifest.colourParty('others');

      if (visibleSeatKeys && !visibleSeatKeys.has(seatKey)) {
        return '#cbd5e1';
      }

      if (choroplethConfig.enabled && choroplethConfig.valueBySeatKey?.has(seatKey)) {
        const metricValue = choroplethConfig.valueBySeatKey.get(seatKey);
        return choroplethConfig.toColour(metricValue);
      }

      const winner = winnerBySeat.get(seatName) || winnerBySeat.get(seatLookupKey(seatName)) || 'others';
      return manifest.colourParty(winner);
    })
    .attr('stroke', null)
    .on('mouseenter', null)
    .on('click', (event, datum) => {
      event.stopPropagation();
      setActiveSeatPath(event.currentTarget);
      const seatName = seatNameFromFeature(datum);
      if (seatName) {
        setSelectedSeatRowByKey(seatLookupKey(seatName));
        renderSeatPopup(seatName);
        zoomToFeature(datum);
      }
    });

  seatPaths.each(function assignSeatPath(datum) {
    const seatName = seatNameFromFeature(datum);
    if (!seatName) return;
    seatPathByKey.set(seatLookupKey(seatName), this);
  });

  // ── Region connector lines and flash layer (Holyrood list-seat region summaries) ──

  if (options.regionSummary) {
    // Group topology geometries by normalised region key for flash animation.
    const geometriesByRegion = new Map();
    (object.geometries || []).forEach((geom) => {
      const region = geom.properties?.region;
      if (!region) return;
      const regionKey = normalizeRegionKey(region);
      if (!geometriesByRegion.has(regionKey)) geometriesByRegion.set(regionKey, []);
      geometriesByRegion.get(regionKey).push(geom);
    });

    // Flash layer for region highlight animation (inside zoomLayer, drawn above seat paths).
    const flashLayer = zoomLayer.append('g').attr('class', 'maps-region-flash-layer');

    // Flash animation: draw a temporary merged-region path that pulses and disappears.
    _state.mapInteractionController.flashRegion = (regionKey) => {
      const geoms = geometriesByRegion.get(regionKey);
      if (!geoms) return;
      const merged = topojsonMerge(mapData, geoms);
      if (!merged) return;
      const flashPath = flashLayer.append('path')
        .attr('class', 'maps-region-flash-path')
        .attr('d', path(merged));
      flashPath.node().addEventListener('animationend', () => flashPath.remove(), { once: true });
    };
  }

  svg.on('click', (event) => {
    const target = event.target;
    if (target === mapSvg || target?.classList?.contains('maps-map-bg')) {
      resetZoom();
    }
  });

  svg.call(zoomBehavior.transform, options.preserveZoom ? d3.zoomTransform(mapSvg) : initialTransform);
}

/**
 * Entry point. Wires controls then loads election data and routes to the initial view.
 * @returns {Promise<void>}
 */
async function init() {
  // TODO: remove once renderSeatPopup migrates to dom.js (callback slot no longer needed)
  _state.renderSeatPopup = renderSeatPopup;
  wireInit();
  domWireInit();

  try {
    await initPage();
  } catch (error) {
    setHeader('', true);
    console.error(error);
  }
}

init();

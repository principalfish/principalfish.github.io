import {
  _state,
  state,
  manifest,
  initState,
} from './scripts/state.js';
import {
  fetchJson,
} from './scripts/files.js';
import {
  trackVirtualPageView,
} from './scripts/misc.js';
import {
  seatLookupKey,
} from './scripts/utils.js';
import {
  setElectionPreDataFetch,
  renderHeader,
  renderLeftBar,
  renderMapControlOptions,
  renderPageTitle,
  renderPollTracker,
  domWireInit,
  renderMapInit,
  renderMap,
  renderVoteTotals,
  wireVoteTotalsToggle,
  setSelectedSeatRowByKey,
  hideSeatPopup,
  renderSeatPopup,
  renderRightPanel,
  mapInteraction,
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
 * Calls every wireX handler exactly once. Invoked from init() during boot.
 * @returns {void}
 */
function wireInit() {
  wireSeatSearch();
  wirePostcodeSearch();
  wireSeatPopup();
  wireVoteTotalsToggle();
  wireWindowResize();
  wireVoteTotalsSorting();
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
    mapInteraction.clearSelection?.();
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
    if (state.view === 'polltracker') renderPollTracker();
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


// ── Election file resolution ──────────────────────────────────────────────────
const seatCard = document.getElementById('mapsSeatCard');
const seatSearchInput = document.getElementById('maps-seat-search');
const postcodeSearchInput = document.getElementById('maps-postcode-search');
const seatPopupClose = document.getElementById('mapsSeatPopupClose');

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

const MAX_SEAT_SEARCH_SUGGESTIONS = 10;

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
  const zoomed = mapInteraction.zoomToSeat(seatName);
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
 * Entry point. Wires controls then loads election data and routes to the initial view.
 * @returns {Promise<void>}
 */
async function init() {
  wireInit();
  domWireInit();

  try {
    await initPage();
  } catch (error) {
    renderHeader('', true);
    console.error(error);
  }
}

init();

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
  seatLookupKey,
  getRegionLabel,
} from './scripts/utils.js';
import {
  setElectionPreDataFetch,
  setHeader,
  setLeftBar,
  setMapControlOptions,
  setPageTitle,
  setPollTracker,
  domWireInit,
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
 * @param {'election'|'predict'} view - Active view; 'predict' triggers predict mode activation after render.
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
  // TODO: remove once other handlers read state.electionData.summary.data / state.comparisonElectionData.summary.data directly
  window.__mapsCurrentSummary = state.electionData.summary.data;
  window.__mapsComparisonSummary = state.comparisonElectionData?.summary.data ?? null;

  drawMap();
  syncRightPanelHeightToMap();

  if (view === 'predict') {
    await activatePredictMode();
  } else {
    const params = buildRouteSearchParams('election');
    window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
  }
}

/**
 * Renders the map, seat list, vote totals, and choropleth legend for the current filter/choropleth _state.
 * Accepts { preserveZoom: true } to retain the current pan/zoom transform.
 * @param {{preserveZoom?: boolean}} [options={}] - Rendering options; set preserveZoom to true to keep the current d3 zoom transform.
 * @returns {void}
 */
// TODO: eventually this should become a call to state.setupMap() and a set of
// focused DOM handlers in dom.js — e.g. setVoteTotals(), setMap(), setSeatList() —
// each responsible for one render concern rather than one monolithic function.
function drawMap(preserveZoom = false) {
  state.applyMapFilters();
  state.buildChoroplethConfig();

  const mapConfig = manifest.mapModes[String(state.currentElection.mapId)];
  const hasListSeats = state.electionData.currentSeats.some((s) => Seat.isList(s));
  // Suppress vote columns on the 'all' tab when list seats exist: ElectionSummary.summarize counts
  // only constituency votes in 'all' mode while seat counts include both — the mismatch is
  // misleading.
  const showVotes = !hasListSeats || state.voteTotals.mode !== 'all';
  // Aggregated summary of the currently visible (filter-passing) seats under the active
  // vote-totals tab. Drives the vote-totals panel rows; distinct from
  // state.electionData.summary, which always covers the unfiltered chamber.
  const filteredSeatsSummary = ElectionSummary.summarize(state.mapVisible.seats, { mode: state.voteTotals.mode });
  const filteredSeatsComparisonSummary = state.comparisonSeats.length
    ? ElectionSummary.summarize(state.mapVisible.comparisonSeats, { mode: state.voteTotals.mode })
    : null;
  const regionSummary = hasListSeats
    ? buildRegionSummary(state.electionData.currentSeats.filter((s) => Seat.isList(s)))
    : null;
  // Seat list: for Holyrood elections show constituency seats only (list seats appear in region table).
  const filteredSeats = hasListSeats
    ? state.mapVisible.seats.filter((s) => !Seat.isList(s))
    : state.mapVisible.seats;

  window.__mapsCurrentSummary = filteredSeatsSummary;
  window.__mapsComparisonSummary = filteredSeatsComparisonSummary;

  renderVoteTotalsTabs(mapConfig);
  updateVoteTotalsTabsUI();
  renderSeatViewTabs(mapConfig);
  updateSeatViewTabsUI();
  updatePostcodeSearchVisibility();

  toggleVoteTotalColumns(showVotes);
  toggleVotePctColumns(showVotes);
  renderVoteTotals(filteredSeatsSummary, filteredSeatsComparisonSummary, {
    showVoteTotals: showVotes,
    hiddenParties: new Set(mapConfig?.hiddenVoteTotalsParties ?? []),
  });

  renderTopoMap(state.mapData, state.electionData.currentSeats, {
    visibleSeatKeys: state.mapVisible.seatKeys,
    choroplethConfig: state.choroplethConfig,
    preserveZoom,
    regionSummary,
    mapId: String(state.currentElection.mapId ?? ''),
  });

  renderRegionTable(regionSummary);

  renderSeatList(filteredSeats, state.comparisonSeats, {});

  applySeatSearchSuggestions(buildSeatSearchIndex(filteredSeats));
  renderChoroplethLegend(state.choroplethConfig);

  if (seatPreview) {
    let previewText;
    if (hasListSeats) {
      const totalConst = state.electionData.currentSeats.filter((s) => !Seat.isList(s));
      previewText = `Showing ${formatInt(filteredSeats.length)} of ${formatInt(totalConst.length)} constituency seats.`;
    } else {
      previewText = `Showing ${formatInt(state.mapVisible.seats.length)} of ${formatInt(state.electionData.currentSeats.length)} seats.`;
    }
    seatPreview.textContent = previewText;
  }
}


// =====================================================================
// POLLTRACKER
// =====================================================================

/**
 * Switches the app into poll tracker mode: deactivates predict mode, loads data, renders controls and chart, and updates the route.
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
  wirePredictControls();
  wireSeatSearch();
  wirePostcodeSearch();
  wireSeatPopup();
  wireVoteTotalsToggle();
  wireWindowResize();
  wireVoteTotalsSorting(() => {
    if (!window.__mapsCurrentSummary) return;
    renderVoteTotals(window.__mapsCurrentSummary, window.__mapsComparisonSummary || null);
    syncRightPanelHeightToMap();
  });
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
 * Attaches click handlers to all predict window buttons: apply (load current forecast),
 * submit (run projection from inputs), share (copy URL), reset (restore baseline inputs),
 * close (deactivate predict mode), and collapse (toggle window height). Guards against
 * double-wiring via dataset flag on the predict window element.
 * @returns {void}
 */
function wirePredictControls() {
  if (!predictWindow || predictWindow.dataset.wired === 'true') return;

  const predictApplyButton = document.getElementById('mapsPredictApply');
  if (predictApplyButton) {
    predictApplyButton.addEventListener('click', () => {
      applyCurrentPredictionToInputs().catch((error) => {
        console.error(error);
      });
    });
  }

  if (predictSubmitButton) {
    predictSubmitButton.addEventListener('click', () => {
      const invalidRows = validatePredictRowsNotOver100();
      if (invalidRows.length) {
        const labelText = invalidRows
          .slice(0, 4)
          .map((row) => `${row.regionLabel} (${formatPredictShare(row.total)}%)`)
          .join(', ');
        window.alert(`Entered percentages exceed 100% for: ${labelText}${invalidRows.length > 4 ? ', ...' : ''}. Please reduce inputs before submitting.`);
        return;
      }
      rebuildPredictSwingsFromInputs();
      applyPredictModeProjection();
      replacePredictRouteStateFromInputs();
    });
  }

  if (predictShareButton) {
    predictShareButton.addEventListener('click', () => {
      sharePredictScenario();
    });
  }

  if (predictResetAllButton) {
    predictResetAllButton.addEventListener('click', () => {
      resetPredictInputsToBaseline();
      rebuildPredictSwingsFromInputs();
      applyPredictModeProjection();
      replacePredictRouteStateFromInputs();
    });
  }

  const predictCollapseButton = document.getElementById('mapsPredictCollapse');
  if (predictCollapseButton) {
    predictCollapseButton.addEventListener('click', () => {
      const collapsed = predictWindow.classList.toggle('maps-predict-window--collapsed');
      predictCollapseButton.textContent = collapsed ? '▼' : '▲';
      syncPredictModeRightColumnLayout();
    });
  }

  predictWindow.dataset.wired = 'true';
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
 * Toggles the vote totals panel open/closed on click, re-renders the table at the new height,
 * and recalculates the predict window right-column layout (both share the same panel column).
 * @returns {void}
 */
function wireVoteTotalsToggle() {
  if (!voteTotalsToggle) return;
  voteTotalsToggle.addEventListener('click', () => {
    _state.voteTotalsExpanded = !_state.voteTotalsExpanded;
    if (!window.__mapsCurrentSummary) return;
    renderVoteTotals(window.__mapsCurrentSummary, window.__mapsComparisonSummary || null);
    syncPredictModeRightColumnLayout();
  });
}

/**
 * Syncs the right panel height to the map on window resize, and re-renders the poll tracker
 * chart so its SVG dimensions update to the new container size.
 * @returns {void}
 */
function wireWindowResize() {
  window.addEventListener('resize', () => {
    syncRightPanelHeightToMap();
    if (state.view === 'polltracker') setPollTracker();
  });
}

/**
 * Attaches click and keyboard (Enter/Space) handlers to all [data-sort-key] table headers
 * to trigger sort changes, then invokes onSortChanged so the caller can re-render.
 * @param {function(): void} onSortChanged - Callback invoked after the sort direction is updated.
 * @returns {void}
 */
function wireVoteTotalsSorting(onSortChanged) {
  document.querySelectorAll('th[data-sort-key]').forEach((header) => {
    const sortKey = header.getAttribute('data-sort-key');
    if (!sortKey) return;

    /** Updates sort direction for sortKey and invokes the onSortChanged callback. */
    const trigger = () => {
      setSortDirection(sortKey);
      onSortChanged();
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

// ── Predict constants ────────────────────────────────────────────────────────

const PREDICT_BASE_PARTY_KEYS = ['labour', 'conservative', 'libdems', 'green', 'reform'];
const PREDICT_NI_PARTY_KEYS = ['sinnfein', 'dup', 'alliance', 'uu', 'sdlp'];
const PREDICT_HOLYROOD_PARTY_KEYS = [
  'snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform',
];
const PREDICT_MODELLED_PARTY_KEYS = [
  ...PREDICT_BASE_PARTY_KEYS,
  'snp',
  'plaidcymru',
  'scottishgreens',
  'alba',
  ...PREDICT_NI_PARTY_KEYS,
];
const PREDICT_ENGLAND_KEY = 'england';
const PREDICT_SCOTLAND_KEY = 'scotland';
const PREDICT_WALES_KEY = 'wales';
const PREDICT_NI_KEY = 'northernireland';

// ── Formatting ───────────────────────────────────────────────────────────────

/**
 * Returns a CSS class name reflecting whether value is positive, negative, or neutral.
 * @param {number} value - Numeric delta value.
 * @returns {string} One of 'maps-delta-positive', 'maps-delta-negative', or 'maps-delta-neutral'.
 */
function deltaClass(value) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return 'maps-delta-neutral';
  return num > 0 ? 'maps-delta-positive' : 'maps-delta-negative';
}

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

// ── Predict region predicates ────────────────────────────────────────────────

/**
 * Returns true if regionKey normalizes to the Northern Ireland predict region.
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the normalized key matches the Northern Ireland predict region.
 */
function isPredictNorthernIrelandRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_NI_KEY;
}

/**
 * Returns true if regionKey normalizes to the Scotland predict region.
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the normalized key matches the Scotland predict region.
 */
function isPredictScotlandRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_SCOTLAND_KEY;
}

/**
 * Returns true if regionKey normalizes to the Wales predict region.
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the normalized key matches the Wales predict region.
 */
function isPredictWalesRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_WALES_KEY;
}

/**
 * Returns true if regionKey is a non-empty, non-NI, non-Scotland, non-Wales region (i.e. an English region).
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the region is non-empty and does not match any of the named devolved/NI regions.
 */
function isPredictEnglishRegion(regionKey) {
  const key = normalizeRegionKey(regionKey);
  if (!key) return false;
  if (isPredictNorthernIrelandRegion(key)) return false;
  if (isPredictScotlandRegion(key)) return false;
  if (isPredictWalesRegion(key)) return false;
  return true;
}

// ── Predict baseline shares ──────────────────────────────────────────────────

/**
 * Rounds a predict vote share value to the nearest integer.
 * @param {number} value - Vote share value (typically 0–100).
 * @returns {number} Rounded integer share value.
 */
function roundPredictShareValue(value) {
  return Math.round(Number(value || 0));
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

/**
 * Returns the composite Map key string used to store predict inputs: `${regionKey}::${partyKey}`.
 * @param {string} regionKey - Normalized region key.
 * @param {string} partyKey - Party key.
 * @returns {string} Composite key in the form `regionKey::partyKey`.
 */
function predictInputKey(regionKey, partyKey) {
  return `${regionKey}::${partyKey}`;
}

/**
 * Formats a predict share value as an integer string.
 * @param {number} value - Vote share value to format.
 * @returns {string} Rounded integer share as a string (e.g. '42').
 */
function formatPredictShare(value) {
  return String(roundPredictShareValue(value));
}

/**
 * Returns a new Map with all values rounded and clamped to [0, 100].
 * @param {Map<string, number>} sourceMap - Source Map of predict share values to normalize.
 * @returns {Map<string, number>} New Map with the same keys and values rounded and clamped to [0, 100].
 */
function normalizePredictShareMap(sourceMap) {
  const normalized = new Map();
  (sourceMap || new Map()).forEach((value, key) => {
    normalized.set(key, roundPredictShareValue(clampNumber(value, 0, 100)));
  });
  return normalized;
}

/**
 * Returns the nationalist party key for a region ('snp' for Scotland, 'plaidcymru' for Wales, null otherwise).
 * @param {string} regionKey - Normalized or raw region key.
 * @returns {string|null} Nationalist party key for the region, or null if no nationalist party applies.
 */
function predictNatPartyKeyForRegion(regionKey) {
  if (isPredictScotlandRegion(regionKey)) return 'snp';
  if (isPredictWalesRegion(regionKey)) return 'plaidcymru';
  return null;
}

const PREDICT_NAT_COLUMN_KEY = 'nat';

/**
 * Resolves a grid column party key to the actual party key for a given region. The 'nat' column maps to SNP or Plaid Cymru depending on region, and null if not applicable.
 * @param {string} regionKey - Normalized region key used to resolve the nationalist party.
 * @param {string} columnPartyKey - Column party key, which may be the special 'nat' sentinel or a direct party key.
 * @returns {string|null} Resolved party key, or null when the 'nat' column has no applicable party for the region.
 */
function resolvePredictInputPartyKey(regionKey, columnPartyKey) {
  if (columnPartyKey === PREDICT_NAT_COLUMN_KEY) {
    return predictNatPartyKeyForRegion(regionKey);
  }
  return columnPartyKey;
}

/**
 * Returns the list of party keys for which predict inputs are shown for a given region (NI parties for NI, base + optional nationalist party for GB).
 * @param {string} regionKey - Normalized region key.
 * @returns {string[]} Array of party keys for which predict input cells should be rendered.
 */
function collectPredictInputPartyKeysForRegion(regionKey) {
  if (isPredictNorthernIrelandRegion(regionKey)) {
    return [...PREDICT_NI_PARTY_KEYS];
  }
  const keys = [...PREDICT_BASE_PARTY_KEYS];
  const natPartyKey = predictNatPartyKeyForRegion(regionKey);
  if (natPartyKey) keys.push(natPartyKey);
  return keys;
}

/**
 * Returns a shortened display label for a predict region, applying known abbreviations (e.g. 'Northern Ireland' → 'N Ireland').
 * @param {string} regionLabel - Full region display label.
 * @returns {string} Abbreviated label if a known alias exists, otherwise the original label.
 */
function formatPredictRegionLabel(regionLabel) {
  const text = String(regionLabel || '').trim();
  const aliases = {
    'northern ireland': 'N Ireland',
    'north east england': 'North East',
    'north west england': 'North West',
    'south east england': 'South East',
    'south west england': 'South West',
    'east of england': 'E of England',
    'yorkshire and the humber': 'Yorks',
  };
  const normalized = text.toLowerCase();
  if (aliases[normalized]) return aliases[normalized];
  return text;
}

/**
 * Computes baseline regional vote share percentages from actual seat results.
 * For each modelled party and region, calculates votes / total regional votes × 100.
 * English sub-regions also accumulate into the 'england' aggregate key.
 * Returns a Map keyed by `${regionKey}::${partyKey}` with rounded integer share values.
 * @param {Array<object>} seats - Array of normalized seat objects with `region` and `votes` properties.
 * @returns {Map<string, number>} Map keyed by `regionKey::partyKey` with rounded integer share values (0–100).
 */
function buildPredictBaselineShares(seats) {
  const byRegion = new Map();

  const ensureRegionStats = (regionKey) => {
    if (!byRegion.has(regionKey)) {
      byRegion.set(regionKey, { totalVotes: 0, votesByParty: new Map() });
    }
    return byRegion.get(regionKey);
  };

  (seats || []).forEach((seat) => {
    const regionKey = normalizeRegionKey(seat.region);
    if (!regionKey) return;

    const turnout = seat.turnout;
    if (turnout <= 0) return;

    const regionStats = ensureRegionStats(regionKey);
    regionStats.totalVotes += turnout;

    PREDICT_MODELLED_PARTY_KEYS.forEach((partyKey) => {
      const partyVotes = Number(seat?.votes?.[partyKey] || 0);
      regionStats.votesByParty.set(
        partyKey,
        Number(regionStats.votesByParty.get(partyKey) || 0) + partyVotes,
      );
    });

    if (isPredictEnglishRegion(regionKey)) {
      const englandStats = ensureRegionStats(PREDICT_ENGLAND_KEY);
      englandStats.totalVotes += turnout;
      PREDICT_MODELLED_PARTY_KEYS.forEach((partyKey) => {
        const partyVotes = Number(seat?.votes?.[partyKey] || 0);
        englandStats.votesByParty.set(
          partyKey,
          Number(englandStats.votesByParty.get(partyKey) || 0) + partyVotes,
        );
      });
    }
  });

  const shareMap = new Map();
  byRegion.forEach((stats, regionKey) => {
    PREDICT_MODELLED_PARTY_KEYS.forEach((partyKey) => {
      const votes = Number(stats.votesByParty.get(partyKey) || 0);
      const share = stats.totalVotes > 0 ? (votes / stats.totalVotes) * 100 : 0;
      shareMap.set(`${regionKey}::${partyKey}`, roundPredictShareValue(share));
    });

    // Rounding individual shares can push the total above 100. When that
    // happens, subtract 1 from the smallest non-zero party to keep the
    // baseline row at exactly 100 and avoid a spurious -1 in the "other" column.
    const roundedSum = PREDICT_MODELLED_PARTY_KEYS.reduce(
      (sum, pk) => sum + (shareMap.get(`${regionKey}::${pk}`) || 0), 0,
    );
    if (roundedSum > 100) {
      let minKey = null;
      let minVal = Infinity;
      PREDICT_MODELLED_PARTY_KEYS.forEach((pk) => {
        const k = `${regionKey}::${pk}`;
        const v = shareMap.get(k) || 0;
        if (v > 0 && v < minVal) { minVal = v; minKey = k; }
      });
      if (minKey) shareMap.set(minKey, minVal - (roundedSum - 100));
    }
  });

  return shareMap;
}

// ── Predict projection ───────────────────────────────────────────────────────

/**
 * Looks up the swing value for a party in a region from swingsByParty (Map<partyKey, Map<regionKey, swing>>).
 * Falls back to the 'england' aggregate swing for English sub-regions if no direct entry is found.
 * @param {string} normalizedSeatRegion - Normalized region key for the seat being projected.
 * @param {string} partyKey - Party key to look up swing for.
 * @param {Map<string, Map<string, number>>} swingsByParty - Map from party key to a Map of region key to swing value.
 * @returns {number} Swing value (percentage point delta) for the party in the region, or 0 if not found.
 */
function resolvedSwingValue(normalizedSeatRegion, partyKey, swingsByParty) {
  if (!normalizedSeatRegion) return 0;
  const swingMap = swingsByParty?.get(partyKey);
  if (!swingMap) return 0;
  const direct = Number(swingMap.get(normalizedSeatRegion) || 0);
  if (Math.abs(direct) > 1e-9) return direct;
  if (isPredictEnglishRegion(normalizedSeatRegion)) {
    return Number(swingMap.get(PREDICT_ENGLAND_KEY) || 0);
  }
  return 0;
}

/**
 * Projects a single seat result by applying regional swings to the baseline vote shares.
 * Modelled party shares are adjusted by their region's swing; remaining share is redistributed
 * proportionally to non-modelled parties (or assigned to 'others' if none exist).
 * Returns a new Seat instance with updated votes, turnout, and winner.
 * @param {Seat} baseSeat - Baseline Seat instance with `region`, `votes`, and optional `turnout`.
 * @param {Map<string, Map<string, number>>} swingsByParty - Map from party key to regional swing values.
 * @returns {Seat} New Seat instance with projected `votes`, `turnout`, and `winner`.
 */
function projectedSeatForPredictMode(baseSeat, swingsByParty) {
  const totalVotes = baseSeat.turnout;
  if (totalVotes <= 0) return new Seat(baseSeat);

  const regionKey = normalizeRegionKey(baseSeat.region);
  const baseVotes = baseSeat.votes || {};
  const baseTrackedShareByParty = new Map();
  let trackedShareSum = 0;

  PREDICT_MODELLED_PARTY_KEYS.forEach((partyKey) => {
    const baseShare = (Number(baseVotes[partyKey] || 0) / totalVotes) * 100;
    baseTrackedShareByParty.set(partyKey, baseShare);
    trackedShareSum += baseShare;
  });

  let adjustedTrackedShareSum = 0;
  const adjustedTrackedShareByParty = new Map();
  PREDICT_MODELLED_PARTY_KEYS.forEach((partyKey) => {
    const baseShare = Number(baseTrackedShareByParty.get(partyKey) || 0);
    const swing = resolvedSwingValue(regionKey, partyKey, swingsByParty);
    const adjusted = Math.max(0, baseShare + swing);
    adjustedTrackedShareByParty.set(partyKey, adjusted);
    adjustedTrackedShareSum += adjusted;
  });

  const adjustedOtherShare = Math.max(0, 100 - adjustedTrackedShareSum);
  const projectedVotes = {};
  adjustedTrackedShareByParty.forEach((share, partyKey) => {
    if (share <= 0) return;
    projectedVotes[partyKey] = (share / 100) * totalVotes;
  });

  const nonTrackedEntries = Object.entries(baseVotes)
    .filter(([partyKey]) => !PREDICT_MODELLED_PARTY_KEYS.includes(partyKey));
  const nonTrackedVotes = nonTrackedEntries.reduce((sum, [, votes]) => sum + Number(votes || 0), 0);

  if (adjustedOtherShare > 0) {
    if (nonTrackedVotes > 0) {
      nonTrackedEntries.forEach(([partyKey, votes]) => {
        const weight = Number(votes || 0) / nonTrackedVotes;
        projectedVotes[partyKey] = ((adjustedOtherShare * weight) / 100) * totalVotes;
      });
    } else {
      projectedVotes.others = (adjustedOtherShare / 100) * totalVotes;
    }
  }

  const projectedTotal = Object.values(projectedVotes).reduce((s, v) => s + Number(v || 0), 0);
  if (projectedTotal > 0 && Math.abs(projectedTotal - totalVotes) > 1e-6) {
    const scale = totalVotes / projectedTotal;
    Object.keys(projectedVotes).forEach((k) => {
      projectedVotes[k] = projectedVotes[k] * scale;
    });
  }

  const winner = Object.entries(projectedVotes)
    .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))[0]?.[0] || baseSeat.winner || 'others';

  return new Seat({ ...baseSeat, votes: projectedVotes, turnout: totalVotes, winner });
}

/**
 * Runs D'Hondt seat allocation for one Holyrood region.
 * @param {Map<string, number>} votesByPartyKey - List vote totals per party key.
 * @param {number} nSeats - Number of list seats to allocate.
 * @param {Map<string, number>} constWinsByPartyKey - Constituency wins per party (deducted during allocation).
 * @returns {string[]} Ordered array of winning party keys, one entry per seat allocated.
 */
function dhondt(votesByPartyKey, nSeats, constWinsByPartyKey = new Map()) {
  const listSeatsWon = new Map();
  for (const key of votesByPartyKey.keys()) listSeatsWon.set(key, 0);

  const winners = [];
  for (let i = 0; i < nSeats; i++) {
    let bestParty = null;
    let bestQuotient = -Infinity;
    for (const [party, votes] of votesByPartyKey) {
      if (votes <= 0) continue;
      const totalSeats = (listSeatsWon.get(party) || 0) + (constWinsByPartyKey.get(party) || 0);
      const quotient = votes / (totalSeats + 1);
      if (quotient > bestQuotient) {
        bestQuotient = quotient;
        bestParty = party;
      }
    }
    if (bestParty !== null) {
      listSeatsWon.set(bestParty, (listSeatsWon.get(bestParty) || 0) + 1);
      winners.push(bestParty);
    }
  }
  return winners;
}

/**
 * Computes the unweighted arithmetic mean of baseline shares across all regions for each party.
 *
 * @param {Map<string, number>} baselineShareByRegionParty - Map keyed by predictInputKey(regionKey, partyKey).
 * @param {string[]} partyKeys - Party keys to include.
 * @param {string[]} regionKeys - Region keys to average over.
 * @returns {Map<string, number>} Map of partyKey → unweighted mean share (0–100).
 */
function buildHolyroodNationalBaselines(baselineShareByRegionParty, partyKeys, regionKeys) {
  const result = new Map();
  for (const partyKey of partyKeys) {
    let total = 0;
    let count = 0;
    for (const regionKey of regionKeys) {
      const share = baselineShareByRegionParty.get(predictInputKey(regionKey, partyKey));
      if (share != null) { total += share; count++; }
    }
    result.set(partyKey, count > 0 ? total / count : 0);
  }
  return result;
}

/**
 * Projects Holyrood seats using a two-pass AMS model.
 *
 * Pass 1 uses constSwingsByParty for FPTP constituency seats.
 * Pass 2 uses listSwingsByParty (falls back to constSwingsByParty if null) for D'Hondt list seats.
 * Constituency wins from Pass 1 are seeded into the D'Hondt divisors.
 *
 * @param {object[]} baseSeats - Baseline seat records.
 * @param {Map<string, Map<string, number>>} constSwingsByParty - Constituency swings: partyKey → regionKey → swing pp.
 * @param {Map<string, Map<string, number>>|null} [listSwingsByParty=null] - List swings; falls back to constSwingsByParty if null/empty.
 * @returns {object[]} Projected seat records.
 */
function projectHolyroodSeats(baseSeats, constSwingsByParty, listSwingsByParty = null) {
  const effectiveListSwings = (listSwingsByParty && listSwingsByParty.size > 0) ? listSwingsByParty : constSwingsByParty;

  const constBaseSeats = baseSeats.filter((s) => !Seat.isList(s));
  const listBaseSeats = baseSeats.filter((s) => Seat.isList(s));

  // Pass 1: project constituency seats with FPTP swing
  const projectedConst = constBaseSeats.map((s) => projectedSeatForPredictMode(s, constSwingsByParty));

  // Count constituency wins per region per party
  const constWinsByRegion = new Map(); // normalizedRegionKey → Map<partyKey, count>
  for (const seat of projectedConst) {
    const rk = normalizeRegionKey(seat.region);
    if (!constWinsByRegion.has(rk)) constWinsByRegion.set(rk, new Map());
    const wins = constWinsByRegion.get(rk);
    if (seat.winner) wins.set(seat.winner, (wins.get(seat.winner) || 0) + 1);
  }

  // Group list seats by region
  const listByRegion = new Map();
  for (const s of listBaseSeats) {
    const rk = normalizeRegionKey(s.region);
    if (!listByRegion.has(rk)) listByRegion.set(rk, []);
    listByRegion.get(rk).push(s);
  }

  // Pass 2: D'Hondt allocation per region using list swings
  const projectedList = [];
  for (const [rk, regionListSeats] of listByRegion) {
    const voteSumByParty = new Map();
    const projectedRegionList = regionListSeats.map((s) => projectedSeatForPredictMode(s, effectiveListSwings));
    // All list seats in a region share identical vote totals (each seat stores the full regional
    // vote as a duplicate). Accumulate from the first seat only to avoid 7× inflation.
    const firstProj = projectedRegionList[0];
    for (const [partyKey, voteCount] of Object.entries(firstProj?.votes || {})) {
      voteSumByParty.set(partyKey, Number(voteCount || 0));
    }

    const constWins = constWinsByRegion.get(rk) || new Map();
    const listWinners = dhondt(voteSumByParty, regionListSeats.length, constWins);

    projectedRegionList.forEach((proj, idx) => {
      projectedList.push(new Seat({ ...proj, winner: listWinners[idx] || null }));
    });
  }

  return [...projectedConst, ...projectedList];
}

/**
 * Returns all regions from baseRegionLabelsByKey as predict input rows.
 * Used for Holyrood predict mode where regions are the 8 Holyrood electoral regions
 * rather than the Westminster England/Scotland/Wales/NI groupings.
 * @param {Map<string, string>} baseRegionLabelsByKey - Map from normalized region key to display label.
 * @returns {Array<{regionKey: string, regionLabel: string, isEnglandAggregate: boolean, isEnglandRegion: boolean}>}
 */
function collectHolyroodPredictInputRows(baseRegionLabelsByKey) {
  return Array.from(baseRegionLabelsByKey.entries()).map(([regionKey, regionLabel]) => ({
    regionKey,
    regionLabel,
    isEnglandAggregate: false,
    isEnglandRegion: false,
  }));
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

/**
 * Returns a Map from seat name to winner party key for fast map colour lookups.
 * Each seat is stored under both its original name and a lowercase variant.
 * Seats without a `seat` property are skipped. Winner defaults to `'others'` if missing.
 * @param {Array<object>} seats - Array of seat objects with `seat` and `winner` properties.
 * @returns {Map<string, string>} Map from seat name (original and lowercase) to winner party key.
 */
function buildWinnerBySeat(seats) {
  const bySeat = new Map();
  seats.forEach((seat) => {
    if (!seat?.seat) return;
    bySeat.set(seat.seat, seat.winner || 'others');
    bySeat.set(String(seat.seat).toLowerCase(), seat.winner || 'others');
  });
  return bySeat;
}

/**
 * Builds a per-region summary from a mixed constituency+list seat array.
 * Returns a Map keyed by region label → { dominantParty, seatsByParty, votesByParty, listSeats }.
 * dominantParty = party with the most total seats; ties broken by list vote total.
 * @param {Array<object>} seats - Normalised seat objects with `seat`, `region`, `winner`, `votes` properties.
 * @returns {Map<string, {dominantParty: string, seatsByParty: object, votesByParty: object, listSeats: Array}>}
 */
function buildRegionSummary(seats) {
  const regions = new Map();
  for (const seat of seats) {
    const region = seat.region || 'unknown';
    if (!regions.has(region)) {
      regions.set(region, { seatsByParty: {}, votesByParty: {}, listSeats: [] });
    }
    const r = regions.get(region);
    const winner = seat.winner || 'others';
    r.seatsByParty[winner] = (r.seatsByParty[winner] || 0) + 1;
    // Note: list seats each store the full regional vote total by design, so votesByParty
    // will be multiplied by the number of list seats per region. This is acceptable here
    // as votesByParty is only used for relative tie-breaking (dominantParty), not absolute totals.
    for (const [party, votes] of Object.entries(seat.votes || {})) {
      r.votesByParty[party] = (r.votesByParty[party] || 0) + votes;
    }
    if (Seat.isList(seat)) r.listSeats.push(seat);
  }
  for (const [, r] of regions) {
    const sorted = Object.entries(r.seatsByParty).sort((a, b) => {
      if (b[1] !== a[1]) return b[1] - a[1];
      return (r.votesByParty[b[0]] || 0) - (r.votesByParty[a[0]] || 0);
    });
    r.dominantParty = sorted[0]?.[0] || 'others';
  }
  return regions;
}

// ── Predict payload encode / decode ──────────────────────────────────────────

/**
 * Encodes changed predict share values into a compact URL-safe base-36 string.
 * `slots` is an ordered array of [regionKey, partyKey] pairs defining the slot index space.
 * Returns `''` if nothing has changed and `englandExpanded` is false.
 * @param {Array<[string, string, number]>} serializedRows - Array of [regionKey, partyKey, value] triples for values that differ from baseline.
 * @param {boolean} englandExpanded - Whether the England sub-region rows are expanded in the UI.
 * @param {Array<[string, string]>} slots - Ordered array of [regionKey, partyKey] pairs defining the encoding index space.
 * @returns {string} Encoded payload string (e.g. '2.0.1a-2c,3f-1b'), or '' if no changes and englandExpanded is false.
 */
function encodePredictPayload(serializedRows, englandExpanded, slots) {
  if (!slots.length) return '';

  const slotIndexByKey = new Map(
    slots.map(([regionKey, partyKey], index) => [`${regionKey}::${partyKey}`, index])
  );

  const entries = [];
  serializedRows.forEach((entry) => {
    if (!Array.isArray(entry) || entry.length < 3) return;
    const regionKey = String(entry[0] || '');
    const partyKey = String(entry[1] || '');
    const slotIndex = slotIndexByKey.get(`${regionKey}::${partyKey}`);
    if (!Number.isInteger(slotIndex) || slotIndex < 0) return;

    const value = Math.round(Number(entry[2]));
    if (!Number.isFinite(value) || value < 0 || value > 100) return;

    entries.push(`${slotIndex.toString(36)}-${value.toString(36)}`);
  });

  if (!entries.length && !englandExpanded) return '';
  return `2.${englandExpanded ? 1 : 0}.${entries.join(',')}`;
}

/**
 * Decodes a predict payload string into `{ englandExpanded, rows: [[regionKey, partyKey, value], ...] }`.
 * `slots` is the same ordered [regionKey, partyKey] array used during encoding.
 * Returns null on any parse failure or when slots are unavailable.
 * @param {string} encoded - Encoded payload string as produced by encodePredictPayload.
 * @param {Array<[string, string]>} slots - Ordered array of [regionKey, partyKey] pairs matching those used during encoding.
 * @returns {{
 *   englandExpanded: boolean,
 *   rows: Array<[string, string, number]>
 * } | null} Decoded state object, or null on parse failure.
 */
function decodePredictPayload(encoded, slots) {
  const raw = String(encoded || '').trim();
  if (!raw.startsWith('2.')) return null;

  const parts = raw.split('.');
  if (parts.length < 2 || parts[0] !== '2') return null;

  const englandExpanded = parts[1] === '1';
  const rowsPart = parts.slice(2).join('.').trim();
  if (!rowsPart) {
    return {
      englandExpanded,
      rows: [],
    };
  }

  if (!slots.length) return null;

  const rows = [];
  rowsPart.split(',').forEach((chunk) => {
    const token = String(chunk || '').trim();
    if (!token) return;

    const [indexToken, valueToken] = token.split('-');
    if (!indexToken || !valueToken) return;

    const slotIndex = Number.parseInt(indexToken, 36);
    const value = Number.parseInt(valueToken, 36);
    if (!Number.isInteger(slotIndex) || slotIndex < 0 || slotIndex >= slots.length) return;
    if (!Number.isInteger(value) || value < 0 || value > 100) return;

    const slot = slots[slotIndex];
    if (!Array.isArray(slot) || slot.length < 2) return;

    rows.push([slot[0], slot[1], value]);
  });

  return {
    englandExpanded,
    rows,
  };
}

// ── Map / region utilities ────────────────────────────────────────────────────

// ── Election file resolution ──────────────────────────────────────────────────

// ── Predict share lookups ─────────────────────────────────────────────────────

/**
 * Returns the rounded baseline vote share for a region/party from the historical election data Map.
 * `baselineMap` is keyed by `predictInputKey(regionKey, partyKey)`. Returns 0 when not found.
 * @param {string} regionKey - Normalized region key.
 * @param {string} partyKey - Party key.
 * @param {Map<string, number>} baselineMap - Map of `regionKey::partyKey` to baseline share values.
 * @returns {number} Rounded integer baseline share for the region/party, or 0 if not found.
 */
function getPredictBaselineShare(regionKey, partyKey, baselineMap) {
  return roundPredictShareValue(
    Number(baselineMap.get(predictInputKey(regionKey, partyKey)) || 0)
  );
}

/**
 * Returns the current user-entered predict share for a region/party.
 * Falls back to the baseline share when no input has been entered.
 * `inputMap` is keyed by `predictInputKey`; `baselineMap` is the historical baseline.
 * @param {string} regionKey - Normalized region key.
 * @param {string} partyKey - Party key.
 * @param {Map<string, number>} inputMap - Map of user-entered share values keyed by `regionKey::partyKey`.
 * @param {Map<string, number>} baselineMap - Map of historical baseline share values keyed by `regionKey::partyKey`.
 * @returns {number} Current user-entered share if set, otherwise the rounded baseline share.
 */
function getPredictInputShareValue(regionKey, partyKey, inputMap, baselineMap) {
  const cached = inputMap.get(predictInputKey(regionKey, partyKey));
  if (Number.isFinite(cached)) return Number(cached);
  return roundPredictShareValue(getPredictBaselineShare(regionKey, partyKey, baselineMap));
}

/**
 * Returns the sum of all predict input shares for a region across its modelled parties.
 * Uses `inputMap` for entered values, falling back to `baselineMap`.
 * @param {string} regionKey - Normalized region key.
 * @param {Map<string, number>} inputMap - Map of user-entered share values.
 * @param {Map<string, number>} baselineMap - Map of historical baseline share values.
 * @returns {number} Sum of all entered party shares for the region.
 */
function calculatePredictEnteredShareTotal(regionKey, inputMap, baselineMap) {
  return collectPredictInputPartyKeysForRegion(regionKey).reduce((sum, partyKey) => {
    return sum + Number(getPredictInputShareValue(regionKey, partyKey, inputMap, baselineMap) || 0);
  }, 0);
}

/**
 * Returns the implied 'other' share for a region: `100 - sum of entered party shares`, rounded.
 * Can be negative when inputs exceed 100%.
 * @param {string} regionKey - Normalized region key.
 * @param {Map<string, number>} inputMap - Map of user-entered share values.
 * @param {Map<string, number>} baselineMap - Map of historical baseline share values.
 * @returns {number} Implied 'other' share as a rounded integer; negative when total inputs exceed 100.
 */
function calculatePredictOtherShare(regionKey, inputMap, baselineMap) {
  return roundPredictShareValue(100 - calculatePredictEnteredShareTotal(regionKey, inputMap, baselineMap));
}

// ── Predict region collection ─────────────────────────────────────────────────

/**
 * Returns all predict regions as `{ regionKey, regionLabel }` sorted alphabetically by label.
 * `baseRegionLabelsByKey` is a Map from normalised region key to display label.
 * @param {Map<string, string>} baseRegionLabelsByKey - Map from normalized region key to display label.
 * @returns {Array<{regionKey: string, regionLabel: string}>} All regions sorted alphabetically by display label.
 */
function collectPredictAllRegions(baseRegionLabelsByKey) {
  return Array.from(baseRegionLabelsByKey.entries())
    .map(([regionKey, regionLabel]) => ({ regionKey, regionLabel }))
    .sort((a, b) => a.regionLabel.localeCompare(b.regionLabel));
}

/**
 * Returns all validation rows: the England aggregate row first, then every English, Scottish,
 * Welsh, and NI region from `baseRegionLabelsByKey`. Used to check no region exceeds 100%.
 * @param {Map<string, string>} baseRegionLabelsByKey - Map from normalized region key to display label.
 * @returns {Array<{regionKey: string, regionLabel: string}>} Ordered array of region rows for validation.
 */
function collectPredictValidationRows(baseRegionLabelsByKey) {
  const allRegions = collectPredictAllRegions(baseRegionLabelsByKey);
  const rows = [{ regionKey: PREDICT_ENGLAND_KEY, regionLabel: 'England' }];

  allRegions.forEach((row) => {
    if (
      isPredictEnglishRegion(row.regionKey)
      || isPredictScotlandRegion(row.regionKey)
      || isPredictWalesRegion(row.regionKey)
      || isPredictNorthernIrelandRegion(row.regionKey)
    ) {
      rows.push({ regionKey: row.regionKey, regionLabel: row.regionLabel });
    }
  });

  return rows;
}

/**
 * Returns the rows used for URL state serialization — the same set as validation rows.
 * Alias for `collectPredictValidationRows`.
 * @param {Map<string, string>} baseRegionLabelsByKey - Map from normalized region key to display label.
 * @returns {Array<{regionKey: string, regionLabel: string}>} Ordered array of region rows for URL state serialization.
 */
function collectPredictShareStateRows(baseRegionLabelsByKey) {
  return collectPredictValidationRows(baseRegionLabelsByKey);
}

/**
 * Returns the ordered list of row descriptors for the predict grid.
 * England aggregate is always first. English sub-regions follow when `englandExpanded` is true.
 * Scotland, Wales, and Northern Ireland are appended when present in `baseRegionLabelsByKey`.
 * Each row carries `{ regionKey, regionLabel, isEnglandAggregate, isEnglandRegion }`.
 * @param {Map<string, string>} baseRegionLabelsByKey - Map from normalized region key to display label.
 * @param {boolean} englandExpanded - Whether English sub-regions should be included after the England aggregate row.
 * @returns {Array<{
 *   regionKey: string,
 *   regionLabel: string,
 *   isEnglandAggregate: boolean,
 *   isEnglandRegion: boolean
 * }>} Ordered row descriptors for the predict input grid.
 */
function collectPredictInputRows(baseRegionLabelsByKey, englandExpanded) {
  const allRegions = collectPredictAllRegions(baseRegionLabelsByKey);
  const englishRegions = allRegions.filter((row) => isPredictEnglishRegion(row.regionKey));
  const scotland = allRegions.find((row) => isPredictScotlandRegion(row.regionKey));
  const wales = allRegions.find((row) => isPredictWalesRegion(row.regionKey));
  const northernIreland = allRegions.find((row) => isPredictNorthernIrelandRegion(row.regionKey));

  const rows = [
    {
      regionKey: PREDICT_ENGLAND_KEY,
      regionLabel: 'England',
      isEnglandAggregate: true,
      isEnglandRegion: false,
    },
  ];

  if (englandExpanded) {
    englishRegions.forEach((row) => {
      rows.push({
        regionKey: row.regionKey,
        regionLabel: row.regionLabel,
        isEnglandAggregate: false,
        isEnglandRegion: true,
      });
    });
  }

  if (scotland) {
    rows.push({
      regionKey: scotland.regionKey,
      regionLabel: scotland.regionLabel,
      isEnglandAggregate: false,
      isEnglandRegion: false,
    });
  }
  if (wales) {
    rows.push({
      regionKey: wales.regionKey,
      regionLabel: wales.regionLabel,
      isEnglandAggregate: false,
      isEnglandRegion: false,
    });
  }
  if (northernIreland) {
    rows.push({
      regionKey: northernIreland.regionKey,
      regionLabel: northernIreland.regionLabel,
      isEnglandAggregate: false,
      isEnglandRegion: false,
    });
  }

  return rows;
}


// ── Holyrood predict share resolution ────────────────────────────────────────

const HOLYROOD_NATIONAL_KEY = 'national';

/**
 * @param {'overall'|'constituency'|'list'} pass
 * @param {Map<string,number>} constBaseline
 * @param {Map<string,number>} listBaseline
 * @returns {Map<string,number>}
 */
function holyroodBaselineForPass(pass, constBaseline, listBaseline) {
  return pass === 'list' ? listBaseline : constBaseline;
}

/**
 * @param {'overall'|'constituency'|'list'} pass
 * @param {Map<string,number>} nationalBaseline
 * @param {Map<string,number>} nationalListBaseline
 * @returns {Map<string,number>}
 */
function holyroodNationalBaselineForPass(pass, nationalBaseline, nationalListBaseline) {
  return pass === 'list' ? nationalListBaseline : nationalBaseline;
}

/**
 * Raw (unrounded) resolved share for a region/party from a single tab input map, applying
 * national UNS if no region-level override exists.
 * @param {string} regionKey
 * @param {string} partyKey
 * @param {Map<string,number>} tabMap - Input map for the tab being resolved.
 * @param {'overall'|'constituency'|'list'} pass
 * @param {object} state - { constBaseline, listBaseline, nationalBaseline, nationalListBaseline }
 * @returns {number|null} Resolved share (may be fractional), or null if the tab map has no entry.
 */
function resolvedTabShareRaw(regionKey, partyKey, tabMap, pass, state) {
  const regionKey_ = predictInputKey(regionKey, partyKey);
  if (tabMap.has(regionKey_)) return tabMap.get(regionKey_);
  const natKey = predictInputKey(HOLYROOD_NATIONAL_KEY, partyKey);
  if (tabMap.has(natKey)) {
    const nationalInput = tabMap.get(natKey);
    const nationalBase = holyroodNationalBaselineForPass(pass, state.nationalBaseline, state.nationalListBaseline).get(partyKey) ?? 0;
    const regionalBase = getPredictBaselineShare(regionKey, partyKey, holyroodBaselineForPass(pass, state.constBaseline, state.listBaseline));
    return clampNumber(regionalBase + (nationalInput - nationalBase), 0, 100);
  }
  return null;
}

/**
 * Resolves the share for a region/party from a single tab input map, applying national UNS if
 * no region-level override exists. Returns a rounded integer.
 * @param {string} regionKey
 * @param {string} partyKey
 * @param {Map<string,number>} tabMap - Input map for the tab being resolved.
 * @param {'overall'|'constituency'|'list'} pass
 * @param {object} state - { constBaseline, listBaseline, nationalBaseline, nationalListBaseline }
 * @returns {number|null} Resolved share as a rounded integer, or null if the tab map has no entry.
 */
function resolvedTabShare(regionKey, partyKey, tabMap, pass, state) {
  const raw = resolvedTabShareRaw(regionKey, partyKey, tabMap, pass, state);
  return raw !== null ? roundPredictShareValue(raw) : null;
}

/**
 * Raw (unrounded) resolved share for a region/party for a given pass.
 * @param {string} regionKey
 * @param {string} partyKey
 * @param {'constituency'|'list'} pass
 * @param {object} state
 * @returns {number} Share (0–100), possibly fractional.
 */
function resolvedHolyroodShareRaw(regionKey, partyKey, pass, state) {
  if (pass !== 'constituency' && pass !== 'list') throw new Error(`Unknown pass: ${pass}`);
  const tabMap = pass === 'list' ? state.listInput : state.constInput;
  const tabVal = resolvedTabShareRaw(regionKey, partyKey, tabMap, pass, state);
  if (tabVal !== null) return tabVal;
  return getPredictBaselineShare(regionKey, partyKey, holyroodBaselineForPass(pass, state.constBaseline, state.listBaseline));
}

/**
 * Returns the effective resolved share for a region/party for a given pass, as a rounded integer.
 * Resolution order: tab-specific (region override → national UNS) → regional baseline.
 * @param {string} regionKey
 * @param {string} partyKey
 * @param {'constituency'|'list'} pass
 * @param {object} state - { constBaseline, listBaseline, nationalBaseline, nationalListBaseline, constInput, listInput }
 * @returns {number} Share (0–100).
 */
function coreResolvedHolyroodShare(regionKey, partyKey, pass, state) {
  return roundPredictShareValue(resolvedHolyroodShareRaw(regionKey, partyKey, pass, state));
}

/**
 * Calculates the 'other' share for the national row of a Holyrood predict tab.
 * @param {Map<string,number>} tabMap - Tab input map.
 * @param {'overall'|'constituency'|'list'} pass
 * @param {string[]} partyKeys - Column party keys for the tab.
 * @param {object} state - { nationalBaseline, nationalListBaseline }
 * @returns {number}
 */
function coreHolyroodNationalOtherShare(tabMap, pass, partyKeys, state) {
  const natBaselines = holyroodNationalBaselineForPass(pass, state.nationalBaseline, state.nationalListBaseline);
  const total = partyKeys.reduce((sum, pk) => {
    const natKey = predictInputKey(HOLYROOD_NATIONAL_KEY, pk);
    return sum + (tabMap.has(natKey) ? tabMap.get(natKey) : (natBaselines.get(pk) ?? 0));
  }, 0);
  return roundPredictShareValue(100 - total);
}

/**
 * Calculates the 'other' share for a region row using resolved values for the given pass.
 * Sums raw (unrounded) shares before rounding the result, so that independent per-party
 * rounding cannot push the total above 100 and produce a spurious negative 'other'.
 * @param {string} regionKey
 * @param {'overall'|'constituency'|'list'} pass
 * @param {string[]} partyKeys - Column party keys for the tab.
 * @param {object} state - Full Holyrood predict state (passed to resolvedHolyroodShare).
 * @returns {number}
 */
function coreHolyroodResolvedOtherShare(regionKey, pass, partyKeys, state) {
  const total = partyKeys.reduce((sum, pk) => sum + resolvedHolyroodShareRaw(regionKey, pk, pass, state), 0);
  return roundPredictShareValue(100 - total);
}



const mapSvg = document.querySelector('.maps-svg');
const mapContent = document.getElementById('mapContent');
const zoomValue = document.getElementById('mapsZoomValue');
const seatPreview = document.getElementById('mapsSeatPreview');
const voteTotalsBody = document.getElementById('mapsVoteTotalsBody');
const voteTotalsTable = document.getElementById('mapsVoteTotalsTable');
const voteTotalsToggle = document.getElementById('mapsVoteTotalsToggle');
const voteTotalsTabNav = document.getElementById('mapsVoteTotalsTabNav');
const seatViewTabNav = document.getElementById('mapsSeatViewTabNav');
const seatCard = document.getElementById('mapsSeatCard');
const seatSearchInput = document.getElementById('maps-seat-search');
const postcodeSearchInput = document.getElementById('maps-postcode-search');
const postcodeSearchGroup = postcodeSearchInput?.closest('.maps-toolbar-group-postcode') ?? null;
const postcodeWarningBtn = document.getElementById('mapsPostcodeWarningBtn');
const postcodeWarningPanel = document.getElementById('mapsPostcodeWarningPanel');
const seatList = document.getElementById('mapsSeatList');
const mapsStage = document.querySelector('.maps-stage');
const mapsPanelRight = document.querySelector('.maps-panel-right');
const seatPopup = document.getElementById('mapsSeatPopup');
const seatPopupTitle = document.getElementById('mapsSeatPopupTitle');
const seatPopupMeta = document.getElementById('mapsSeatPopupMeta');
const seatPopupList = document.getElementById('mapsSeatPopupList');
const seatPopupClose = document.getElementById('mapsSeatPopupClose');
const choroplethLegend = document.getElementById('mapsChoroplethLegend');
const regionCard = document.getElementById('mapsRegionCard');
const regionTableBody = document.getElementById('mapsRegionTableBody');

const filterPartySelect = document.getElementById('mapsFilterParty');
const filterRegionSelect = document.getElementById('mapsFilterRegion');
const filterSecondPartyGroup = document.getElementById('mapsFilterSecondPartyGroup');
const filterSecondPartySelect = document.getElementById('mapsFilterSecondParty');
const filterMajorityMinInput = document.getElementById('mapsFilterMajorityMin');
const filterMajorityMaxInput = document.getElementById('mapsFilterMajorityMax');
const filterGainsButton = document.getElementById('mapsFilterGainsOnly');
const filtersResetButton = document.getElementById('mapsFiltersReset');
const predictWindow = document.getElementById('mapsPredictWindow');
const predictGrid = document.getElementById('mapsPredictGrid');
const predictTabNav = document.getElementById('mapsPredictTabNav');
const predictSubmitButton = document.getElementById('mapsPredictSubmit');
const predictShareButton = document.getElementById('mapsPredictShare');
const predictResetAllButton = document.getElementById('mapsPredictResetAll');

const choroplethTypeSelect = document.getElementById('mapsChoroplethType');
const choroplethPartySelect = document.getElementById('mapsChoroplethParty');
const choroplethsResetButton = document.getElementById('mapsChoroplethsReset');
const choroplethVoteShareChangeOption = document.getElementById('mapsChoroplethVoteShareChangeOption');
const dataInfoButton = document.getElementById('mapsDataInfoBtn');
// Canonical map name strings used to route postcode lookups to the correct
// postcodes.io endpoint and to identify whether postcode search is supported.
const WESTMINSTER_OLD_MAP_NAME = 'westminster-2010';
const WESTMINSTER_NEW_MAP_NAME = 'westminster-2024';
const HOLYROOD_OLD_MAP_NAME = 'holyrood-2021';
const HOLYROOD_NEW_MAP_NAME = 'holyrood-2026';

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
 * Builds a URLSearchParams for the given view, setting/removing the election and predict params as appropriate.
 * @param {string} view - View name to set ('election', 'predict', or 'polltracker').
 * @returns {URLSearchParams} Updated search params with view, election, and predict params adjusted.
 */
function buildRouteSearchParams(view) {
  const params = new URLSearchParams(window.location.search);
  params.set('view', view);
  if (view !== 'predict') params.delete('predict');

  if (view === 'polltracker') {
    params.delete('election');
    return params;
  }

  if (state.currentElection.id) {
    params.set('election', state.currentElection.id);
  }
  return params;
}


/**
 * Returns an ordered array of [regionKey, partyKey] slot pairs used as the positional index for predict payload encoding.
 * @returns {Array<[string, string]>} Ordered slot pairs matching the serialization order used by encodePredictPayload/decodePredictPayload.
 */
function buildPredictShareStateSlots() {
  const rows = currentPredictInputRows();
  const slots = [];

  if (state.currentParliament === 'holyrood') {
    ['c', 'l'].forEach((prefix) => {
      rows.forEach((row) => {
        predictPartyKeysForRegion(row.regionKey).forEach((partyKey) => {
          slots.push([`${prefix}:${row.regionKey}`, partyKey]);
        });
      });
      // National row slots added after region rows to preserve existing slot indices.
      _state.predictColumnPartyKeys.forEach((partyKey) => {
        slots.push([`${prefix}:${HOLYROOD_NATIONAL_KEY}`, partyKey]);
      });
    });
    return slots;
  }

  rows.forEach((row) => {
    predictPartyKeysForRegion(row.regionKey).forEach((partyKey) => {
      slots.push([row.regionKey, partyKey]);
    });
  });

  return slots;
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


/** Toggles vote percentage column visibility on the totals table. */
function toggleVotePctColumns(show) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-vote-pct-col', !show);
}

/** Sets the active class on vote-totals tab buttons to match state.voteTotals.mode. */
function updateVoteTotalsTabsUI() {
  voteTotalsTabNav?.querySelectorAll('[data-vote-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.voteTab === state.voteTotals.mode);
  });
}

/** Builds vote-totals tab buttons (Overall / Constituency / List) from mapConfig.voteTotalsViews. */
function renderVoteTotalsTabs(mapConfig) {
  if (!voteTotalsTabNav) return;
  voteTotalsTabNav.innerHTML = '';
  const views = mapConfig.voteTotalsViews;
  voteTotalsTabNav.hidden = views.length <= 1;
  views.forEach((view, i) => {
    const btn = document.createElement('button');
    btn.className = `maps-vote-tab${i === 0 ? ' active' : ''}`;
    btn.dataset.voteTab = view.id;
    btn.textContent = view.label;
    btn.addEventListener('click', () => {
      state.voteTotals.mode = view.id;
      updateVoteTotalsTabsUI();
      const { seats, comparisonSeats } = state.mapVisible;
      const tabAllowsVotes = state.voteTotals.mode !== 'all';
      const summary = ElectionSummary.summarize(seats, { mode: state.voteTotals.mode });
      const compSummary = comparisonSeats.length ? ElectionSummary.summarize(comparisonSeats, { mode: state.voteTotals.mode }) : null;
      window.__mapsCurrentSummary = summary;
      window.__mapsComparisonSummary = compSummary;
      toggleVoteTotalColumns(tabAllowsVotes);
      toggleVotePctColumns(tabAllowsVotes);
      renderVoteTotals(summary, compSummary, { showVoteTotals: tabAllowsVotes });
    });
    voteTotalsTabNav.appendChild(btn);
  });
}

/** Sets the active class on seat-view tab buttons to match state.seatView.mode. */
function updateSeatViewTabsUI() {
  seatViewTabNav?.querySelectorAll('[data-seat-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.seatView === state.seatView.mode);
  });
}

/** Builds seat-view tab buttons (Constituencies / Regions) from mapConfig.seatViews. */
function renderSeatViewTabs(mapConfig) {
  if (!seatViewTabNav) return;
  seatViewTabNav.innerHTML = '';
  const views = mapConfig.seatViews;
  seatViewTabNav.hidden = views.length <= 1;
  views.forEach((view) => {
    const btn = document.createElement('button');
    btn.className = 'maps-seat-view-tab';
    btn.dataset.seatView = view.id;
    btn.textContent = view.label;
    btn.addEventListener('click', () => {
      state.seatView.mode = view.id;
      updateSeatViewTabsUI();
      drawMap(true);
    });
    seatViewTabNav.appendChild(btn);
  });
}

/**
 * Updates _state.currentSort: toggles direction if the same key is re-selected, otherwise switches to the new key with a default direction.
 * @param {string} sortKey - Column key to sort by (e.g. 'seats', 'votes', 'party').
 * @returns {void}
 */
function setSortDirection(sortKey) {
  if (_state.currentSort.key === sortKey) {
    _state.currentSort.direction = _state.currentSort.direction === 'asc' ? 'desc' : 'asc';
    return;
  }
  _state.currentSort.key = sortKey;
  _state.currentSort.direction = sortKey === 'party' ? 'asc' : 'desc';
}

/**
 * Lazily initializes and returns the per-region swing Map for a party in _state.predictRegionalSwingsByParty.
 * @param {string} partyKey - Party key to look up or initialize a swing map for.
 * @returns {Map<string, number>} Map from region key to swing value for the given party.
 */
function ensurePredictPartySwingMap(partyKey) {
  if (!_state.predictRegionalSwingsByParty.has(partyKey)) {
    _state.predictRegionalSwingsByParty.set(partyKey, new Map());
  }
  return _state.predictRegionalSwingsByParty.get(partyKey);
}

/**
 * Syncs predict window and seat card visibility based on predict mode state, vote totals expansion, and England expansion.
 * @returns {void}
 */
function syncPredictModeRightColumnLayout() {
  if (!predictWindow || !seatCard) return;

  const predictVisible = state.view === 'predict';
  predictWindow.hidden = !predictVisible;
  predictWindow.style.display = predictVisible ? '' : 'none';

  const predictCollapsed = predictWindow.classList.contains('maps-predict-window--collapsed');
  const hideSeatCard = predictVisible && !predictCollapsed;
  const forcePredictGridScroll = predictVisible && !predictCollapsed &&
    (_state.predictEnglandExpanded || (state.currentParliament === 'holyrood' && _state.predictHolyroodRegionsExpanded));
  seatCard.hidden = hideSeatCard;
  seatCard.style.display = hideSeatCard ? 'none' : '';

  predictWindow.classList.toggle('maps-predict-window-fill', hideSeatCard);
  predictWindow.classList.toggle('maps-predict-window-compact', predictVisible && !hideSeatCard);
  predictWindow.classList.toggle('maps-predict-window-force-scroll', forcePredictGridScroll);
}

/**
 * Returns the GB predict grid column party keys (base parties + 'nat' column).
 * @returns {string[]} Array of column party keys for the GB section of the predict grid.
 */
function collectPredictPartyKeys() {
  if (state.currentParliament === 'holyrood') return [...PREDICT_HOLYROOD_PARTY_KEYS];
  return [...PREDICT_BASE_PARTY_KEYS, PREDICT_NAT_COLUMN_KEY];
}

function currentPredictInputRows() {
  if (state.currentParliament === 'holyrood') return collectHolyroodPredictInputRows(_state.predictBaseRegionLabelsByKey);
  return collectPredictInputRows(_state.predictBaseRegionLabelsByKey, _state.predictEnglandExpanded);
}

function predictPartyKeysForRegion(regionKey) {
  if (state.currentParliament === 'holyrood') return _state.predictColumnPartyKeys;
  return collectPredictInputPartyKeysForRegion(regionKey);
}

function predictElectionYear() {
  return state.currentParliament === 'holyrood' ? '2026' : '2029';
}

/**
 * Returns the NI predict grid column party keys.
 * @returns {string[]} Array of party keys for the Northern Ireland section of the predict grid.
 */
function collectPredictNorthernIrelandPartyKeys() {
  return [...PREDICT_NI_PARTY_KEYS];
}

/**
 * Clamps inputValue to [0, 100], stores it in _state.predictInputByRegionParty, and returns the clamped integer value.
 * @param {string} regionKey - Normalized region key.
 * @param {string} partyKey - Party key.
 * @param {number|string} inputValue - Raw input value from the user (may be a string from an input element).
 * @returns {number} Clamped and rounded integer share value that was stored.
 */
function setPredictInputShareValue(regionKey, partyKey, inputValue) {
  const shareValue = roundPredictShareValue(clampNumber(inputValue, 0, 100));
  _state.predictInputByRegionParty.set(predictInputKey(regionKey, partyKey), shareValue);
  return shareValue;
}

// ── Holyrood tab helpers ──────────────────────────────────────────────────────

/** Builds the Holyrood predict state object from current module globals. */
function holyroodPredictState() {
  return {
    constBaseline: _state.predictBaselineConstShareByRegionParty,
    listBaseline: _state.predictBaselineListShareByRegionParty,
    nationalBaseline: _state.predictNationalBaselines,
    nationalListBaseline: _state.predictNationalListBaselines,
    constInput: _state.predictConstInputByRegionParty,
    listInput: _state.predictListInputByRegionParty,
  };
}

function resolvedHolyroodShare(regionKey, partyKey, pass) {
  return coreResolvedHolyroodShare(regionKey, partyKey, pass, holyroodPredictState());
}

function holyroodNationalOtherShare(tabMap, pass) {
  return coreHolyroodNationalOtherShare(tabMap, pass, _state.predictColumnPartyKeys, holyroodPredictState());
}


function holyroodResolvedOtherShare(regionKey, pass) {
  return coreHolyroodResolvedOtherShare(regionKey, pass, _state.predictColumnPartyKeys, holyroodPredictState());
}

/**
 * Renders the Holyrood tab switcher (Constituency / List) into predictTabNav.
 */
function renderHolyroodPredictTabs() {
  if (!predictTabNav) return;
  predictTabNav.hidden = false;
  predictTabNav.innerHTML = '';
  const tabs = [
    { key: 'constituency', label: 'Constituency' },
    { key: 'list', label: 'List' },
  ];
  tabs.forEach(({ key, label }) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `maps-predict-tab-btn${_state.predictHolyroodTab === key ? ' active' : ''}`;
    btn.textContent = label;
    btn.addEventListener('click', () => {
      _state.predictHolyroodTab = key;
      renderHolyroodPredictTabs();
      renderPredictGrid();
    });
    predictTabNav.appendChild(btn);
  });

}

/**
 * Renders the grid for any Holyrood tab: national row + 8 region rows.
 * - National row: reads/writes the 'national' key in tabMap; always shown without inherited styling.
 * - Region rows: cells show the resolved value for this pass; cells with a tab-specific region
 *   override are shown normally, inherited cells (falling through to national or overall) are greyed.
 *   Clearing a region cell removes its override and reverts to inherited.
 * @param {Map<string,number>} tabMap - The input map for the active tab.
 * @param {'overall'|'constituency'|'list'} pass
 */
function renderHolyroodTabGrid(tabMap, pass) {
  if (!predictGrid) return;
  predictGrid.innerHTML = '';
  _state.predictOtherCellByRegion = new Map();

  const regions = currentPredictInputRows();
  const table = document.createElement('table');
  table.className = 'maps-predict-grid-table';

  // Header
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const regionTh = document.createElement('th');
  regionTh.textContent = 'Region';
  headRow.appendChild(regionTh);
  _state.predictColumnPartyKeys.forEach((pk) => {
    const th = document.createElement('th');
    th.title = manifest.labelParty(pk);
    th.innerHTML = `<span class="maps-predict-grid-swatch" style="background:${manifest.colourParty(pk)}" aria-hidden="true"></span>`;
    headRow.appendChild(th);
  });
  const totalTh = document.createElement('th');
  totalTh.title = 'Other';
  totalTh.innerHTML = '<span class="maps-predict-grid-swatch maps-predict-grid-swatch-other" aria-hidden="true"></span>';
  headRow.appendChild(totalTh);
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');

  // ── National row ──
  const natTr = document.createElement('tr');
  const natLabelTd = document.createElement('td');
  natLabelTd.className = 'maps-predict-grid-region maps-predict-grid-region-national';
  const natToggle = document.createElement('button');
  natToggle.type = 'button';
  natToggle.className = 'maps-predict-expand-btn';
  natToggle.textContent = _state.predictHolyroodRegionsExpanded ? 'Hide regions' : 'Show regions';
  natToggle.addEventListener('click', () => {
    if (_state.predictHolyroodRegionsExpanded) {
      // Collapsing: national becomes source of truth — clear region entries from both maps.
      for (const map of [_state.predictConstInputByRegionParty, _state.predictListInputByRegionParty]) {
        for (const key of Array.from(map.keys())) {
          if (key.split('::')[0] !== HOLYROOD_NATIONAL_KEY) map.delete(key);
        }
      }
    } else {
      // Expanding: regions become source of truth — clear national entries from both maps.
      for (const map of [_state.predictConstInputByRegionParty, _state.predictListInputByRegionParty]) {
        for (const key of Array.from(map.keys())) {
          if (key.split('::')[0] === HOLYROOD_NATIONAL_KEY) map.delete(key);
        }
      }
    }
    _state.predictHolyroodRegionsExpanded = !_state.predictHolyroodRegionsExpanded;
    renderPredictGrid();
    syncPredictModeRightColumnLayout();
  });
  const natLabelWrap = document.createElement('div');
  natLabelWrap.className = 'maps-predict-region-label-wrap';
  natLabelWrap.innerHTML = '<span>National</span>';
  natLabelWrap.appendChild(natToggle);
  natLabelTd.appendChild(natLabelWrap);
  natTr.appendChild(natLabelTd);

  const natBaselines = pass === 'list' ? _state.predictNationalListBaselines : _state.predictNationalBaselines;
  _state.predictColumnPartyKeys.forEach((pk) => {
    const natKey = predictInputKey(HOLYROOD_NATIONAL_KEY, pk);
    const currentVal = tabMap.has(natKey) ? tabMap.get(natKey) : (natBaselines.get(pk) ?? 0);
    const td = document.createElement('td');
    const input = document.createElement('input');
    input.type = 'number'; input.step = '1'; input.min = '0'; input.max = '100';
    input.className = 'maps-predict-grid-input';
    input.dataset.regionKey = HOLYROOD_NATIONAL_KEY;
    input.dataset.partyKey = pk;
    input.value = formatPredictShare(currentVal);
    if (_state.predictHolyroodRegionsExpanded) {
      input.disabled = true;
    } else {
      input.addEventListener('change', () => {
        const raw = input.value.trim();
        if (raw === '') {
          tabMap.delete(natKey);
          input.value = formatPredictShare(natBaselines.get(pk) ?? 0);
        } else {
          const val = roundPredictShareValue(clampNumber(raw, 0, 100));
          input.value = formatPredictShare(val);
          tabMap.set(natKey, val);
        }
        // Refresh national other
        const natOtherCell = _state.predictOtherCellByRegion.get(HOLYROOD_NATIONAL_KEY);
        if (natOtherCell) {
          const other = holyroodNationalOtherShare(tabMap, pass);
          natOtherCell.textContent = formatPredictShare(other);
          natOtherCell.classList.toggle('maps-predict-grid-total-over', other < 0);
        }
        // Region rows are not updated on national change — they only update when
        // explicitly edited. The national UNS is applied via resolvedHolyroodShare
        // at submit time, not reflected live in region displays.
      });
    }
    td.appendChild(input);
    natTr.appendChild(td);
  });

  const natOtherTd = document.createElement('td');
  natOtherTd.className = 'maps-predict-grid-total';
  const natOther = holyroodNationalOtherShare(tabMap, pass);
  natOtherTd.textContent = formatPredictShare(natOther);
  natOtherTd.classList.toggle('maps-predict-grid-total-over', natOther < 0);
  _state.predictOtherCellByRegion.set(HOLYROOD_NATIONAL_KEY, natOtherTd);
  natTr.appendChild(natOtherTd);
  tbody.appendChild(natTr);

  // ── Region rows ──
  if (!_state.predictHolyroodRegionsExpanded) {
    table.appendChild(tbody);
    const wrap = document.createElement('section');
    wrap.className = 'maps-predict-grid-section maps-predict-grid-section-holyrood';
    wrap.appendChild(table);
    predictGrid.appendChild(wrap);
    return;
  }
  regions.forEach((region) => {
    const tr = document.createElement('tr');
    const labelTd = document.createElement('td');
    labelTd.className = 'maps-predict-grid-region maps-predict-grid-region-child';
    labelTd.textContent = formatPredictRegionLabel(region.regionLabel);
    tr.appendChild(labelTd);

    const passBaselineMap = pass === 'list' ? _state.predictBaselineListShareByRegionParty : _state.predictBaselineConstShareByRegionParty;
    _state.predictColumnPartyKeys.forEach((pk) => {
      const regionInputKey = predictInputKey(region.regionKey, pk);
      const hasRegionOverride = tabMap.has(regionInputKey);
      const baselineVal = getPredictBaselineShare(region.regionKey, pk, passBaselineMap);
      const displayVal = hasRegionOverride ? tabMap.get(regionInputKey) : baselineVal;

      const td = document.createElement('td');
      const input = document.createElement('input');
      input.type = 'number'; input.step = '1'; input.min = '0'; input.max = '100';
      input.className = 'maps-predict-grid-input';
      input.dataset.regionKey = region.regionKey;
      input.dataset.partyKey = pk;
      input.value = formatPredictShare(displayVal);
      input.addEventListener('change', () => {
        const raw = input.value.trim();
        if (raw === '') {
          tabMap.delete(regionInputKey);
          input.value = formatPredictShare(getPredictBaselineShare(region.regionKey, pk, passBaselineMap));
          input.classList.add('maps-predict-grid-input--inherited');
        } else {
          const val = roundPredictShareValue(clampNumber(raw, 0, 100));
          input.value = formatPredictShare(val);
          tabMap.set(regionInputKey, val);
          input.classList.remove('maps-predict-grid-input--inherited');
        }
        const otherCell = _state.predictOtherCellByRegion.get(region.regionKey);
        if (otherCell) {
          const total = _state.predictColumnPartyKeys.reduce((sum, p) => {
            const k = predictInputKey(region.regionKey, p);
            return sum + (tabMap.has(k) ? tabMap.get(k) : getPredictBaselineShare(region.regionKey, p, passBaselineMap));
          }, 0);
          const other = roundPredictShareValue(100 - total);
          otherCell.textContent = formatPredictShare(other);
          otherCell.classList.toggle('maps-predict-grid-total-over', other < 0);
        }
      });
      td.appendChild(input);
      tr.appendChild(td);
    });

    const otherTd = document.createElement('td');
    otherTd.className = 'maps-predict-grid-total';
    const displayedTotal = _state.predictColumnPartyKeys.reduce((sum, p) => {
      const k = predictInputKey(region.regionKey, p);
      return sum + (tabMap.has(k) ? tabMap.get(k) : getPredictBaselineShare(region.regionKey, p, passBaselineMap));
    }, 0);
    const other = roundPredictShareValue(100 - displayedTotal);
    otherTd.textContent = formatPredictShare(other);
    otherTd.classList.toggle('maps-predict-grid-total-over', other < 0);
    _state.predictOtherCellByRegion.set(region.regionKey, otherTd);
    tr.appendChild(otherTd);
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  const wrap = document.createElement('section');
  wrap.className = 'maps-predict-grid-section maps-predict-grid-section-holyrood';
  wrap.appendChild(table);
  predictGrid.appendChild(wrap);
}

/**
 * Updates the 'other' display cell for a region in the predict grid, marking it red when the total exceeds 100%.
 * @param {string} regionKey - Normalized region key whose other-share cell should be refreshed.
 * @returns {void}
 */
function updatePredictOtherCell(regionKey) {
  const cell = _state.predictOtherCellByRegion.get(regionKey);
  if (!cell) return;
  const otherShare = calculatePredictOtherShare(regionKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty);
  cell.textContent = formatPredictShare(otherShare);
  cell.classList.toggle('maps-predict-grid-total-over', otherShare < 0);
}

/**
 * Serializes only the region/party share values that differ from baseline, plus the England expansion flag, into the v2 encoded payload string. Returns '' if nothing has changed.
 * @returns {string} Encoded predict payload string for use in the URL, or '' when no changes have been made.
 */
function buildPredictShareStatePayload() {
  if (state.currentParliament === 'holyrood') {
    const rows = currentPredictInputRows();
    const serializedRows = [];
    const passes = [
      { prefix: 'c', inputMap: _state.predictConstInputByRegionParty, baseline: _state.predictBaselineConstShareByRegionParty },
      { prefix: 'l', inputMap: _state.predictListInputByRegionParty, baseline: _state.predictBaselineListShareByRegionParty },
    ];
    passes.forEach(({ prefix, inputMap, baseline }) => {
      rows.forEach((row) => {
        const regionKey = row.regionKey;
        predictPartyKeysForRegion(regionKey).forEach((partyKey) => {
          const inputValue = roundPredictShareValue(getPredictInputShareValue(regionKey, partyKey, inputMap, baseline));
          const baselineValue = roundPredictShareValue(getPredictBaselineShare(regionKey, partyKey, baseline));
          if (inputValue === baselineValue) return;
          serializedRows.push([`${prefix}:${regionKey}`, partyKey, inputValue]);
        });
      });
      // Serialize national row values.
      const natBaselines = prefix === 'l' ? _state.predictNationalListBaselines : _state.predictNationalBaselines;
      _state.predictColumnPartyKeys.forEach((partyKey) => {
        const natInputKey = predictInputKey(HOLYROOD_NATIONAL_KEY, partyKey);
        const inputValue = roundPredictShareValue(inputMap.has(natInputKey) ? inputMap.get(natInputKey) : (natBaselines.get(partyKey) ?? 0));
        const baselineValue = roundPredictShareValue(natBaselines.get(partyKey) ?? 0);
        if (inputValue === baselineValue) return;
        serializedRows.push([`${prefix}:${HOLYROOD_NATIONAL_KEY}`, partyKey, inputValue]);
      });
    });
    if (!serializedRows.length) return '';
    return encodePredictPayload(serializedRows, false, buildPredictShareStateSlots());
  }

  const rows = currentPredictInputRows();
  const serializedRows = [];

  rows.forEach((row) => {
    const regionKey = row.regionKey;
    predictPartyKeysForRegion(regionKey).forEach((partyKey) => {
      const inputValue = roundPredictShareValue(getPredictInputShareValue(regionKey, partyKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty));
      const baselineValue = roundPredictShareValue(getPredictBaselineShare(regionKey, partyKey, _state.predictBaselineShareByRegionParty));
      if (inputValue === baselineValue) return;
      serializedRows.push([regionKey, partyKey, inputValue]);
    });
  });

  if (!serializedRows.length && !_state.predictEnglandExpanded) {
    return '';
  }

  return encodePredictPayload(serializedRows, _state.predictEnglandExpanded, buildPredictShareStateSlots());
}

/**
 * Reads and decodes the 'predict' URL parameter, returning the decoded state object or null.
 * @returns {{
 *   englandExpanded: boolean,
 *   rows: Array<[string, string, number]>
 * } | null} Decoded predict state, or null if the parameter is absent or malformed.
 */
function readPredictShareStateFromUrl() {
  const encoded = new URLSearchParams(window.location.search).get('predict');
  if (!encoded) return null;

  // Peek the englandExpanded flag from the payload before building slots so that
  // slot indices match those used during encoding (expanded vs collapsed differ).
  const parts = encoded.split('.');
  const peekExpanded = parts.length >= 2 && parts[0] === '2' && parts[1] === '1';
  const savedExpanded = _state.predictEnglandExpanded;
  _state.predictEnglandExpanded = peekExpanded;
  const slots = buildPredictShareStateSlots();
  _state.predictEnglandExpanded = savedExpanded;

  return decodePredictPayload(encoded, slots);
}

/**
 * Applies a decoded predict share state (from URL) to _state.predictInputByRegionParty, validating regions and parties before setting values.
 * @param {{englandExpanded: boolean, rows: Array<[string, string, number]>}|null} sharedState - Decoded predict state as returned by readPredictShareStateFromUrl; no-op if null.
 * @returns {void}
 */
function applyPredictShareStateFromUrl(sharedState) {
  if (!sharedState) return;

  if (state.currentParliament === 'holyrood') {
    const validRows = new Set([...currentPredictInputRows().map((row) => row.regionKey), HOLYROOD_NATIONAL_KEY]);
    const validParties = new Set(predictPartyKeysForRegion(HOLYROOD_NATIONAL_KEY));
    (sharedState.rows || []).forEach((entry) => {
      if (!Array.isArray(entry) || entry.length < 3) return;
      const prefixedKey = String(entry[0] || '');
      const partyKey = String(entry[1] || '');
      const colonIdx = prefixedKey.indexOf(':');
      if (colonIdx < 0) return;
      const pass = prefixedKey.slice(0, colonIdx);
      const regionKey = prefixedKey.slice(colonIdx + 1);
      if (!validRows.has(regionKey)) return;
      if (!validParties.has(partyKey)) return;
      const inputMap = pass === 'l' ? _state.predictListInputByRegionParty : _state.predictConstInputByRegionParty;
      inputMap.set(predictInputKey(regionKey, partyKey), roundPredictShareValue(clampNumber(entry[2], 0, 100)));
    });
    return;
  }

  _state.predictEnglandExpanded = Boolean(sharedState.englandExpanded);
  const validRows = new Set(currentPredictInputRows().map((row) => row.regionKey));

  (sharedState.rows || []).forEach((entry) => {
    if (!Array.isArray(entry) || entry.length < 3) return;

    const regionKey = String(entry[0] || '');
    const partyKey = String(entry[1] || '');
    if (!validRows.has(regionKey)) return;

    const validParties = new Set(predictPartyKeysForRegion(regionKey));
    if (!validParties.has(partyKey)) return;

    setPredictInputShareValue(regionKey, partyKey, entry[2]);
  });
}

/**
 * Builds the full shareable URL for the current predict scenario, including the encoded payload.
 * @returns {string} Absolute URL string with the current predict state encoded in the query string.
 */
function buildPredictShareUrl() {
  const params = buildRouteSearchParams('predict');
  const payload = buildPredictShareStatePayload();
  if (payload) params.set('predict', payload);
  else params.delete('predict');

  const query = params.toString();
  const origin = window.location.origin || '';
  const path = window.location.pathname || '';
  return query ? `${origin}${path}?${query}` : `${origin}${path}`;
}

/**
 * Updates the browser history entry with the current predict state URL without reloading the page.
 * @returns {void}
 */
function replacePredictRouteStateFromInputs() {
  const nextUrl = buildPredictShareUrl();
  window.history.replaceState({}, '', nextUrl);
}

/**
 * Shares the current predict URL via the Web Share API, falls back to clipboard copy, then to a prompt dialog.
 * @returns {Promise<void>}
 */
async function sharePredictScenario() {
  const shareUrl = buildPredictShareUrl();
  try {
    if (navigator.share) {
      await navigator.share({
        title: 'UK Election Maps prediction',
        text: 'My Predict 2029 scenario',
        url: shareUrl,
      });
      return;
    }
  } catch (_error) {
  }

  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(shareUrl).then(() => {
      window.alert('Prediction link copied to clipboard.');
    }).catch(() => {
      window.prompt('Copy your prediction link:', shareUrl);
    });
    return;
  }

  window.prompt('Copy your prediction link:', shareUrl);
}

/**
 * Returns an array of region rows where the entered party shares exceed 100% in total. Empty array means all rows are valid.
 * @returns {Array<{
 *   regionKey: string,
 *   regionLabel: string,
 *   total: number
 * }>} Array of invalid region rows with their computed totals; empty when all regions are valid.
 */
function validatePredictRowsNotOver100() {
  if (state.currentParliament === 'holyrood') {
    const invalid = [];
    // Validate both passes — either can be over 100% regardless of which tab is active.
    ['constituency', 'list'].forEach((pass) => {
      const tabMap = pass === 'list' ? _state.predictListInputByRegionParty : _state.predictConstInputByRegionParty;
      const passLabel = pass === 'list' ? ' (List)' : ' (Constituency)';
      const natOther = holyroodNationalOtherShare(tabMap, pass);
      if (natOther < 0) {
        invalid.push({ regionKey: HOLYROOD_NATIONAL_KEY, regionLabel: `National${passLabel}`, total: roundPredictShareValue(100 - natOther) });
      }
      currentPredictInputRows().forEach((row) => {
        if (holyroodResolvedOtherShare(row.regionKey, pass) < 0) {
          const total = roundPredictShareValue(
            _state.predictColumnPartyKeys.reduce((sum, pk) => sum + resolvedHolyroodShare(row.regionKey, pk, pass), 0)
          );
          invalid.push({ ...row, regionLabel: `${row.regionLabel}${passLabel}`, total });
        }
      });
    });
    return invalid;
  }
  return currentPredictInputRows()
    .map((row) => ({
      ...row,
      total: roundPredictShareValue(calculatePredictEnteredShareTotal(row.regionKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty)),
    }))
    .filter((row) => row.total > 100);
}

/**
 * Recomputes swing maps from current input values versus baseline, removing zero-swing entries.
 * For Holyrood: builds separate const and list swing maps from the three-tab stores.
 * For Westminster: rebuilds _state.predictRegionalSwingsByParty from _state.predictInputByRegionParty as before.
 * @returns {void}
 */
function rebuildPredictSwingsFromInputs() {
  if (state.currentParliament === 'holyrood') {
    _state.predictHolyroodConstSwingsByParty = new Map();
    _state.predictHolyroodListSwingsByParty = new Map();

    const rows = currentPredictInputRows();
    rows.forEach((row) => {
      _state.predictColumnPartyKeys.forEach((partyKey) => {
        // Constituency swing — measured against constituency baseline
        const constBaseline = getPredictBaselineShare(row.regionKey, partyKey, _state.predictBaselineConstShareByRegionParty);
        const constShare = resolvedHolyroodShare(row.regionKey, partyKey, 'constituency');
        const constSwing = constShare - constBaseline;
        if (!_state.predictHolyroodConstSwingsByParty.has(partyKey)) _state.predictHolyroodConstSwingsByParty.set(partyKey, new Map());
        if (Math.abs(constSwing) >= 1e-9) _state.predictHolyroodConstSwingsByParty.get(partyKey).set(row.regionKey, constSwing);

        // List swing — measured against list baseline
        const listBaseline = getPredictBaselineShare(row.regionKey, partyKey, _state.predictBaselineListShareByRegionParty);
        const listShare = resolvedHolyroodShare(row.regionKey, partyKey, 'list');
        const listSwing = listShare - listBaseline;
        if (!_state.predictHolyroodListSwingsByParty.has(partyKey)) _state.predictHolyroodListSwingsByParty.set(partyKey, new Map());
        if (Math.abs(listSwing) >= 1e-9) _state.predictHolyroodListSwingsByParty.get(partyKey).set(row.regionKey, listSwing);
      });
    });
    return;
  }

  _state.predictRegionalSwingsByParty = new Map();
  const rows = currentPredictInputRows();
  rows.forEach((row) => {
    predictPartyKeysForRegion(row.regionKey).forEach((partyKey) => {
      const baseline = getPredictBaselineShare(row.regionKey, partyKey, _state.predictBaselineShareByRegionParty);
      const inputShare = getPredictInputShareValue(row.regionKey, partyKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty);
      const swing = inputShare - baseline;
      const swingMap = ensurePredictPartySwingMap(partyKey);
      if (Math.abs(swing) < 1e-9) {
        swingMap.delete(row.regionKey);
      } else {
        swingMap.set(row.regionKey, swing);
      }
    });
  });
}

/**
 * Resets all predict inputs to baseline values and re-renders the grid.
 * For Holyrood: clears the three tab stores and returns to the Overall tab.
 * For Westminster: resets _state.predictInputByRegionParty and refreshes inputs in-place.
 * @returns {void}
 */
function resetPredictInputsToBaseline() {
  if (state.currentParliament === 'holyrood') {
    _state.predictHolyroodTab = 'constituency';
    _state.predictConstInputByRegionParty = new Map();
    _state.predictListInputByRegionParty = new Map();
    _state.predictHolyroodConstSwingsByParty = new Map();
    _state.predictHolyroodListSwingsByParty = new Map();
    renderHolyroodPredictTabs();
    renderPredictGrid();
    return;
  }

  _state.predictRegionalSwingsByParty = new Map();
  _state.predictInputByRegionParty = normalizePredictShareMap(_state.predictBaselineShareByRegionParty);

  document.querySelectorAll('.maps-predict-grid-input').forEach((input) => {
    const regionKey = input.dataset.regionKey;
    const partyKey = input.dataset.partyKey;
    if (!regionKey || !partyKey) {
      input.value = '0';
      return;
    }
    input.value = formatPredictShare(getPredictInputShareValue(regionKey, partyKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty));
    updatePredictOtherCell(regionKey);
  });
}

/**
 * Fully rebuilds the predict input grid DOM.
 * Renders a GB section (England aggregate + optional sub-regions, Scotland, Wales) and a NI section.
 * Each row contains numeric inputs per party column plus a live-updating 'other' total cell.
 * @returns {void}
 */
function renderPredictGrid() {
  if (!predictGrid) return;
  const regions = currentPredictInputRows();

  predictGrid.innerHTML = '';
  _state.predictOtherCellByRegion = new Map();

  /**
   * Renders a single section (GB or NI) of the predict input grid into predictGrid.
   * Creates a labelled table with one row per region and one numeric input column per party key.
   * @param {object} params - Section rendering parameters.
   * @param {string|null} params.sectionTitle - Optional section heading text.
   * @param {string} params.sectionClassName - CSS class name applied to the section wrapper element.
   * @param {Array<{regionKey: string, regionLabel: string, isEnglandAggregate?: boolean, isEnglandRegion?: boolean}>} params.sectionRegions - Region rows to render.
   * @param {Array<string|null>} params.sectionPartyKeys - Ordered party key columns; null entries render blank spacer cells.
   * @param {boolean} [params.blankRegionHeader=false] - If true, omits the 'Region' text from the header row.
   * @returns {void}
   */
  const renderPredictGridSection = ({ sectionTitle, sectionClassName, sectionRegions, sectionPartyKeys, blankRegionHeader = false }) => {
    if (!sectionRegions.length) return;

    const sectionWrap = document.createElement('section');
    sectionWrap.className = `maps-predict-grid-section ${sectionClassName}`.trim();

    if (sectionTitle) {
      const heading = document.createElement('h4');
      heading.className = 'maps-predict-grid-section-title';
      heading.textContent = sectionTitle;
      sectionWrap.appendChild(heading);
    }

    const table = document.createElement('table');
    table.className = 'maps-predict-grid-table';

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');

    const regionTh = document.createElement('th');
    regionTh.textContent = blankRegionHeader ? '' : 'Region';
    headRow.appendChild(regionTh);

    sectionPartyKeys.forEach((partyKey) => {
      const th = document.createElement('th');
      if (!partyKey) {
        th.title = '';
        th.innerHTML = '';
      } else if (partyKey === PREDICT_NAT_COLUMN_KEY) {
        th.title = 'NAT (SNP in Scotland, Plaid Cymru in Wales)';
        th.innerHTML = '<span class="maps-predict-grid-swatch maps-predict-grid-swatch-nat" aria-hidden="true"></span>';
      } else {
        th.title = manifest.labelParty(partyKey);
        th.innerHTML = `<span class="maps-predict-grid-swatch" style="background:${manifest.colourParty(partyKey)}" aria-hidden="true"></span>`;
      }
      headRow.appendChild(th);
    });

    const totalTh = document.createElement('th');
    totalTh.title = 'Other';
    totalTh.innerHTML = '<span class="maps-predict-grid-swatch maps-predict-grid-swatch-other" aria-hidden="true"></span>';
    headRow.appendChild(totalTh);
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    sectionRegions.forEach((region) => {
    const tr = document.createElement('tr');

    const labelTd = document.createElement('td');
    labelTd.className = 'maps-predict-grid-region';
    if (region.isEnglandAggregate) {
      const toggleButton = document.createElement('button');
      toggleButton.type = 'button';
      toggleButton.className = 'maps-predict-expand-btn';
      toggleButton.textContent = _state.predictEnglandExpanded ? 'Hide regions' : 'Show regions';
      toggleButton.addEventListener('click', () => {
        if (_state.predictEnglandExpanded) {
          // Collapsing: England aggregate becomes source of truth — clear sub-region entries
          // so stale region values cannot bleed into England-aggregate swing calculations.
          for (const key of Array.from(_state.predictInputByRegionParty.keys())) {
            const regionKey = key.split('::')[0];
            if (regionKey !== PREDICT_ENGLAND_KEY && isPredictEnglishRegion(regionKey)) {
              _state.predictInputByRegionParty.delete(key);
            }
          }
        } else {
          // Expanding: sub-regions become source of truth — clear all England aggregate entries
          // (including parties outside _state.predictColumnPartyKeys from simulation data).
          for (const key of Array.from(_state.predictInputByRegionParty.keys())) {
            if (key.split('::')[0] === PREDICT_ENGLAND_KEY) _state.predictInputByRegionParty.delete(key);
          }
        }
        _state.predictEnglandExpanded = !_state.predictEnglandExpanded;
        renderPredictGrid();
        syncPredictModeRightColumnLayout();
      });

      const labelWrap = document.createElement('div');
      labelWrap.className = 'maps-predict-region-label-wrap';
      labelWrap.innerHTML = `<span>${region.regionLabel}</span>`;
      labelWrap.appendChild(toggleButton);
      labelTd.appendChild(labelWrap);
    } else {
      labelTd.textContent = formatPredictRegionLabel(region.regionLabel);
      if (region.isEnglandRegion) {
        labelTd.classList.add('maps-predict-grid-region-child');
      }
    }
    tr.appendChild(labelTd);

    sectionPartyKeys.forEach((columnPartyKey) => {
      if (!columnPartyKey) {
        const td = document.createElement('td');
        td.className = 'maps-predict-grid-spacer';
        td.textContent = '';
        tr.appendChild(td);
        return;
      }

      const partyKey = resolvePredictInputPartyKey(region.regionKey, columnPartyKey);
      const td = document.createElement('td');
      if (!partyKey) {
        td.className = 'maps-predict-grid-total';
        td.textContent = '—';
        tr.appendChild(td);
        return;
      }
      const input = document.createElement('input');
      input.type = 'number';
      input.step = '1';
      input.min = '0';
      input.max = '100';
      input.className = 'maps-predict-grid-input';
      input.dataset.regionKey = region.regionKey;
      input.dataset.partyKey = partyKey;
      input.value = formatPredictShare(getPredictInputShareValue(region.regionKey, partyKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty));
      if (region.isEnglandAggregate && _state.predictEnglandExpanded) {
        input.disabled = true;
      } else {
        input.addEventListener('change', () => {
          const nextValue = setPredictInputShareValue(region.regionKey, partyKey, input.value);
          input.value = formatPredictShare(nextValue);
          updatePredictOtherCell(region.regionKey);
        });
      }
      td.appendChild(input);
      tr.appendChild(td);
    });

    const totalTd = document.createElement('td');
    totalTd.className = 'maps-predict-grid-total';
    totalTd.textContent = formatPredictShare(calculatePredictOtherShare(region.regionKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty));
    totalTd.classList.toggle('maps-predict-grid-total-over', calculatePredictOtherShare(region.regionKey, _state.predictInputByRegionParty, _state.predictBaselineShareByRegionParty) < 0);
    _state.predictOtherCellByRegion.set(region.regionKey, totalTd);
    tr.appendChild(totalTd);
    tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    sectionWrap.appendChild(table);
    predictGrid.appendChild(sectionWrap);
  };

  if (state.currentParliament === 'holyrood') {
    const tabMap = _state.predictHolyroodTab === 'list' ? _state.predictListInputByRegionParty
      : _state.predictConstInputByRegionParty;
    renderHolyroodTabGrid(tabMap, _state.predictHolyroodTab);
    return;
  } else {
    const northernIrelandRegions = regions.filter((region) => isPredictNorthernIrelandRegion(region.regionKey));
    const gbRegions = regions.filter((region) => !isPredictNorthernIrelandRegion(region.regionKey));

    renderPredictGridSection({
      sectionTitle: null,
      sectionClassName: 'maps-predict-grid-section-gb',
      sectionRegions: gbRegions,
      sectionPartyKeys: _state.predictColumnPartyKeys,
      blankRegionHeader: false,
    });

    renderPredictGridSection({
      sectionTitle: null,
      sectionClassName: 'maps-predict-grid-section-ni',
      sectionRegions: northernIrelandRegions,
      sectionPartyKeys: [...collectPredictNorthernIrelandPartyKeys(), null],
      blankRegionHeader: true,
    });
  }
}

/**
 * Loads the 2024 general election as the predict baseline if not already loaded. Returns true on success, false if the election is not found in the manifest or returns no seats.
 * @returns {Promise<boolean>} True if the baseline was loaded successfully, false if the election could not be found or yielded no seats.
 */
async function ensurePredictBaselineData() {
  if (!manifest) return false;

  const baselineElection = manifest.getElectionFromId(state.getPredictBaselineElectionId());
  if (!baselineElection) return false;

  const { mapFile, dataFile } = manifest.resolveElectionFiles(baselineElection);
  const [mapData, resultsData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`),
  ]);

  // TODO: consider migration into state when we get to this
  const electionData = new ElectionData(resultsData);
  if (!electionData.baseSeats.length) return false;

  _state.predictBaseSeats = electionData.currentSeats;
  _state.predictBaseSeatsByKey = electionData.seatsByKey;
  _state.predictBaseMapData = mapData;
  _state.predictBaseRegionLabelsByKey = manifest.buildRegionLabelLookup(baselineElection.mapId);
  _state.predictBaselineShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(_state.predictBaseSeats));

  return true;
}

/**
 * Loads and caches per-region vote shares from the current model prediction election's data
 * file (model_uns for Westminster, holyrood_uns for Holyrood). Results are stored in
 * _state.predictCurrentSimulationConstShares and _state.predictCurrentSimulationListShares. Returns true on
 * success, false if the election cannot be found or yields no seats.
 * @returns {Promise<boolean>} True if simulation data was loaded successfully.
 */
async function ensurePredictCurrentSimulationData() {
  if (_state.predictCurrentSimulationLoaded) return true;
  if (!manifest) return false;

  const simulationElection = manifest.getElectionFromId(state.getPredictAnchorElectionId());
  if (!simulationElection) return false;
  if (!simulationElection) return false;

  let resultsData;
  try {
    const { dataFile } = manifest.resolveElectionFiles(simulationElection);
    resultsData = await fetchJson(`data/${dataFile}`);
  } catch {
    return false;
  }

  // TODO: consider migration into state when we get to this
  const electionData = new ElectionData(resultsData);
  if (!electionData.baseSeats.length) return false;

  const seats = electionData.baseSeats;
  _state.predictCurrentSimulationSeats = seats;

  if (state.currentParliament === 'holyrood') {
    const constSeats = seats.filter((s) => !Seat.isList(s));
    const seenListRegions = new Set();
    const deduplicatedListSeats = seats.filter((s) => {
      if (!Seat.isList(s)) return false;
      if (seenListRegions.has(s.region)) return false;
      seenListRegions.add(s.region);
      return true;
    });
    _state.predictCurrentSimulationConstShares = normalizePredictShareMap(
      buildPredictBaselineShares(constSeats),
    );
    _state.predictCurrentSimulationListShares = normalizePredictShareMap(
      buildPredictBaselineShares(deduplicatedListSeats),
    );
  } else {
    _state.predictCurrentSimulationConstShares = normalizePredictShareMap(
      buildPredictBaselineShares(seats),
    );
  }

  _state.predictCurrentSimulationLoaded = true;
  return true;
}

/**
 * Commits a projected seat array as the current map state and re-renders.
 * Shared between applyPredictModeProjection (swing-based) and applyCurrentPredictionToInputs
 * (direct seat load). Callers are responsible for computing projectedSeats before calling.
 * @param {Array} projectedSeats - Projected seat objects to display.
 * @param {Object} projectedSummary - Pre-computed summary for projectedSeats.
 * @param {Object} baselineSummary - Pre-computed summary for the baseline seats.
 * @returns {void}
 */
function commitPredictProjectionState(projectedSeats, projectedSummary, baselineSummary) {
  state.currentElection.type = state.currentParliament === 'holyrood' ? 'holyrood_uns' : 'model_uns';
  state.electionData.currentSeats = projectedSeats;
  // TODO: migrate to ElectionData.buildSeatIndex(projectedSeats)
  state.electionData.seatsByKey = buildSeatIndex(projectedSeats);
  _state.currentComparisonSeats = _state.predictBaseSeats;
  _state.comparisonSeatsByKey = _state.predictBaseSeatsByKey;
  state.initMapData(_state.predictBaseMapData);
  state.currentRegionLabelsByKey = _state.predictBaseRegionLabelsByKey;

  window.__mapsCurrentSummary = projectedSummary;
  window.__mapsComparisonSummary = baselineSummary;

  const predictLabel = `Predict ${predictElectionYear()}`;
  // TODO: migrate to `state.electionData.summary.text` once predict mode writes its projected summary back onto state.electionData (will need to update electionData.electionName to predictLabel so the ElectionSummary constructor renders the right prefix)
  updateTopSummary({ name: predictLabel, parliament: state.currentParliament }, projectedSummary);
  drawMap(true);
  syncRightPanelHeightToMap();

  if (_state.currentOpenSeatName) {
    renderSeatPopup(_state.currentOpenSeatName);
    _state.mapInteractionController.highlightSeat(_state.currentOpenSeatName);
  }
}

/**
 * Applies current regional swings to the baseline seats, updates module state, and re-renders the map and summaries.
 * @returns {void}
 */
function applyPredictModeProjection() {
  if (state.view !== 'predict') return;
  if (!_state.predictBaseSeats.length || !_state.predictBaseMapData) return;

  const hasHolyroodSwings =
    [..._state.predictHolyroodConstSwingsByParty.values()].some((m) => m.size > 0) ||
    [..._state.predictHolyroodListSwingsByParty.values()].some((m) => m.size > 0);
  const hasWestminsterSwings = _state.predictRegionalSwingsByParty.size > 0;
  const projectedSeats = state.currentParliament === 'holyrood'
    ? hasHolyroodSwings
      ? projectHolyroodSeats(_state.predictBaseSeats, _state.predictHolyroodConstSwingsByParty, _state.predictHolyroodListSwingsByParty)
      : _state.predictBaseSeats.slice()
    : hasWestminsterSwings
      ? _state.predictBaseSeats.map((seat) => projectedSeatForPredictMode(seat, _state.predictRegionalSwingsByParty))
      : _state.predictBaseSeats.slice();
  const projectedSummary = ElectionSummary.summarize(projectedSeats, { mode: state.voteTotals.mode });
  const baselineSummary = ElectionSummary.summarize(_state.predictBaseSeats, { mode: state.voteTotals.mode });

  commitPredictProjectionState(projectedSeats, projectedSummary, baselineSummary);
}

/**
 * Switches the app into predict mode: loads baseline data if needed, applies any URL-encoded state, renders the grid, and runs the initial projection.
 * @returns {Promise<void>}
 */
async function activatePredictMode() {
  if (!state.electionData?.currentSeats.length || !state.mapData) return;

  syncPredictModeRightColumnLayout();

  if (!_state.predictBaseSeats.length || !_state.predictBaseMapData) {
    const loaded2024 = await ensurePredictBaselineData();
    if (!loaded2024) {
      _state.predictBaseSeats = state.electionData.currentSeats.map((seat) => new Seat(seat));
      // TODO: migrate to ElectionData.buildSeatIndex(_state.predictBaseSeats)
      _state.predictBaseSeatsByKey = buildSeatIndex(_state.predictBaseSeats);
      _state.predictBaseMapData = state.mapData;
      _state.predictBaseRegionLabelsByKey = new Map(state.currentRegionLabelsByKey);
      _state.predictBaselineShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(_state.predictBaseSeats));
    }
  }

  _state.predictRegionalSwingsByParty = new Map();
  _state.predictInputByRegionParty = normalizePredictShareMap(_state.predictBaselineShareByRegionParty);
  _state.predictEnglandExpanded = false;
  _state.predictColumnPartyKeys = collectPredictPartyKeys();

  if (state.currentParliament === 'holyrood') {
    _state.predictHolyroodTab = 'constituency';
    _state.predictHolyroodRegionsExpanded = false;
    _state.predictConstInputByRegionParty = new Map();
    _state.predictListInputByRegionParty = new Map();
    _state.predictHolyroodConstSwingsByParty = new Map();
    _state.predictHolyroodListSwingsByParty = new Map();
    const regionKeys = Array.from(_state.predictBaseRegionLabelsByKey.keys());
    // Build separate constituency and list baseline share maps
    const constSeats = _state.predictBaseSeats.filter((s) => !Seat.isList(s));
    const seenListRegions = new Set();
    const deduplicatedListSeats = _state.predictBaseSeats.filter((s) => {
      if (!Seat.isList(s)) return false;
      if (seenListRegions.has(s.region)) return false;
      seenListRegions.add(s.region);
      return true;
    });
    _state.predictBaselineConstShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(constSeats));
    _state.predictBaselineListShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(deduplicatedListSeats));
    _state.predictNationalBaselines = buildHolyroodNationalBaselines(_state.predictBaselineConstShareByRegionParty, _state.predictColumnPartyKeys, regionKeys);
    _state.predictNationalListBaselines = buildHolyroodNationalBaselines(_state.predictBaselineListShareByRegionParty, _state.predictColumnPartyKeys, regionKeys);
    renderHolyroodPredictTabs();
    applyPredictShareStateFromUrl(readPredictShareStateFromUrl());
  } else {
    if (predictTabNav) predictTabNav.hidden = true;
    applyPredictShareStateFromUrl(readPredictShareStateFromUrl());
  }

  syncPredictModeRightColumnLayout();
  renderPredictGrid();

  if (seatPreview) {
    seatPreview.textContent = 'Predict mode active: edit regional vote shares and click Submit.';
  }

  replacePredictRouteStateFromInputs();

  rebuildPredictSwingsFromInputs();
  applyPredictModeProjection();
}

/**
 * Loads the current model prediction's seats directly onto the map and populates the predict
 * grid inputs with the corresponding per-region vote shares. Seats are taken directly from the
 * prediction data file rather than re-projected through the simplified UNS, so the map reflects
 * the exact model output. For Westminster, replaces _state.predictInputByRegionParty with the simulation
 * shares. For Holyrood, replaces the constituency and list input maps (region-keyed entries only;
 * the national row is left blank so it continues to display baseline averages).
 * @returns {Promise<void>}
 */
async function applyCurrentPredictionToInputs() {
  const loaded = await ensurePredictCurrentSimulationData();
  if (!loaded) {
    window.alert('Current prediction data is not available.');
    return;
  }

  if (state.currentParliament === 'holyrood') {
    _state.predictConstInputByRegionParty = new Map(
      _state.predictHolyroodRegionsExpanded ? _state.predictCurrentSimulationConstShares : [],
    );
    _state.predictListInputByRegionParty = new Map(
      _state.predictHolyroodRegionsExpanded ? _state.predictCurrentSimulationListShares : [],
    );
    if (!_state.predictHolyroodRegionsExpanded) {
      // Collapsed: national is source of truth — set national entries only.
      const regionKeys = Array.from(_state.predictBaseRegionLabelsByKey.keys());
      const constNationals = buildHolyroodNationalBaselines(
        _state.predictCurrentSimulationConstShares, _state.predictColumnPartyKeys, regionKeys,
      );
      const listNationals = buildHolyroodNationalBaselines(
        _state.predictCurrentSimulationListShares, _state.predictColumnPartyKeys, regionKeys,
      );
      for (const pk of _state.predictColumnPartyKeys) {
        _state.predictConstInputByRegionParty.set(
          predictInputKey(HOLYROOD_NATIONAL_KEY, pk),
          roundPredictShareValue(constNationals.get(pk) ?? 0),
        );
        _state.predictListInputByRegionParty.set(
          predictInputKey(HOLYROOD_NATIONAL_KEY, pk),
          roundPredictShareValue(listNationals.get(pk) ?? 0),
        );
      }
    }
  } else if (_state.predictEnglandExpanded) {
    // Expanded: sub-regions are source of truth. Populate sub-region entries only;
    // omit the England aggregate so the disabled row shows no stale override.
    _state.predictInputByRegionParty = new Map();
    for (const [key, value] of _state.predictCurrentSimulationConstShares) {
      const regionKey = key.split('::')[0];
      if (regionKey !== PREDICT_ENGLAND_KEY) {
        _state.predictInputByRegionParty.set(key, value);
      }
    }
  } else {
    // Collapsed: England aggregate is source of truth. Set all entries including aggregate.
    _state.predictInputByRegionParty = new Map(_state.predictCurrentSimulationConstShares);
  }

  // Load the prediction seats directly rather than re-projecting from the derived
  // regional shares, so the map reflects the exact model output rather than an
  // approximation produced by the simplified UNS projection.
  const projectedSeats = _state.predictCurrentSimulationSeats.map((s) => new Seat(s));
  const projectedSummary = ElectionSummary.summarize(projectedSeats, { mode: state.voteTotals.mode });
  const baselineSummary = ElectionSummary.summarize(_state.predictBaseSeats, { mode: state.voteTotals.mode });

  renderPredictGrid();
  commitPredictProjectionState(projectedSeats, projectedSummary, baselineSummary);
  replacePredictRouteStateFromInputs();
}

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
  _state.currentOpenSeatName = null;
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
  _state.currentOpenSeatName = seatName;

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
 * Returns a sorted copy of party rows according to _state.currentSort (party name alpha, or numeric column with label tiebreak).
 * @param {Array<object>} rows - Party summary rows with a `party` key and numeric fields matching sort key names.
 * @returns {Array<object>} New sorted array of party rows.
 */
function sortPartyRows(rows) {
  const multiplier = _state.currentSort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (_state.currentSort.key === 'party') {
      return multiplier * manifest.labelParty(a.party).localeCompare(manifest.labelParty(b.party));
    }

    const av = Number(a[_state.currentSort.key] || 0);
    const bv = Number(b[_state.currentSort.key] || 0);
    if (av !== bv) return multiplier * (av - bv);
    // Tiebreak by vote share descending, then party name
    const voteDiff = Number(b.votePct || 0) - Number(a.votePct || 0);
    if (voteDiff !== 0) return voteDiff;
    return manifest.labelParty(a.party).localeCompare(manifest.labelParty(b.party));
  });
}

/**
 * Toggles the 'hide-comparison-cols' class on the vote totals table to show or hide comparison delta columns.
 * @param {boolean} showComparison - True to show comparison columns, false to hide them.
 * @returns {void}
 */
function toggleComparisonColumns(showComparison) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-comparison-cols', !showComparison);
}

/**
 * Toggles the 'hide-vote-total-col' class on the vote totals table to show or hide raw vote count
 * columns. Always hides when state.voteTotals.columns.votes is false (predict mode, model
 * elections, referendums) — caller's showVoteTotals can only restrict further, not override.
 * @param {boolean} showVoteTotals - True to show the raw vote count column, false to hide it.
 * @returns {void}
 */
function toggleVoteTotalColumns(showVoteTotals) {
  if (!voteTotalsTable) return;
  const show = showVoteTotals && state.voteTotalsColumnVisible('votes');
  voteTotalsTable.classList.toggle('hide-vote-total-col', !show);
}

/**
 * Renders the vote totals summary table, showing seat counts, vote share, and comparison deltas when a comparisonSummary is provided. Truncates to top 6 rows unless expanded.
 * @param {{parties: Array<object>, totalVotes: number}} summary - Current election summary as returned by `ElectionSummary.summarize`.
 * @param {{parties: Array<object>, totalVotes: number}|null} [comparisonSummary=null] - Optional comparison summary for rendering delta columns.
 * @param {{showVoteTotals?: boolean}} [options={}] - Rendering options; showVoteTotals controls raw vote count column visibility.
 * @returns {void}
 */
function renderVoteTotals(summary, comparisonSummary = null, options = {}) {
  if (!voteTotalsBody) return;
  voteTotalsBody.innerHTML = '';

  const showComparison = Boolean(comparisonSummary);
  const showVoteTotals = options.showVoteTotals !== false;
  toggleComparisonColumns(showComparison);
  toggleVoteTotalColumns(showVoteTotals);

  const comparisonByParty = new Map();
  if (comparisonSummary) {
    comparisonSummary.parties.forEach((partyRow) => {
      const votePct = comparisonSummary.totalVotes > 0 ? (partyRow.votes / comparisonSummary.totalVotes) * 100 : 0;
      comparisonByParty.set(partyRow.party, {
        seats: Number(partyRow.seats || 0),
        votePct,
      });
    });
  }

  const rows = summary.parties.map((partyRow) => {
    const votePct = summary.totalVotes > 0 ? (partyRow.votes / summary.totalVotes) * 100 : 0;
    const comparison = comparisonByParty.get(partyRow.party) || { seats: 0, votePct: 0 };
    return {
      ...partyRow,
      votePct,
      seatsDelta: Number(partyRow.seats || 0) - comparison.seats,
      votePctDelta: votePct - comparison.votePct,
    };
  });

  const sortedRows = sortPartyRows(rows).filter((r) => !options.hiddenParties?.has(r.party));
  const visibleRows = _state.voteTotalsExpanded ? sortedRows : sortedRows.slice(0, 7);

  if (voteTotalsToggle) {
    const canExpand = sortedRows.length > 7;
    voteTotalsToggle.hidden = !canExpand;
    if (canExpand) {
      voteTotalsToggle.textContent = _state.voteTotalsExpanded ? 'Show fewer' : 'Show all';
    }
  }

  visibleRows.forEach((partyRow) => {
    const tr = document.createElement('tr');

    tr.innerHTML = `
      <td><span class="maps-party-cell"><span class="maps-party-swatch" style="background:${manifest.colourParty(partyRow.party)}"></span>${manifest.labelParty(partyRow.party)}</span></td>
      <td>${formatInt(partyRow.seats)}</td>
      <td class="comparison-col ${showComparison ? deltaClass(partyRow.seatsDelta) : ''}">${showComparison ? formatSigned(partyRow.seatsDelta, 0) : ''}</td>
      <td class="vote-total-col">${formatInt(partyRow.votes)}</td>
      <td class="vote-pct-col">${formatPct(partyRow.votePct)}</td>
      <td class="comparison-col vote-pct-comparison-col ${showComparison ? deltaClass(partyRow.votePctDelta) : ''}">${showComparison ? formatSigned(partyRow.votePctDelta, 2) : ''}</td>
    `;
    voteTotalsBody.appendChild(tr);
  });
}

/**
 * Renders a region summary popup showing seat tallies and list vote shares.
 */
function renderRegionPopup(regionKey, regionSummary) {
  if (!seatPopup || !seatPopupTitle || !seatPopupMeta || !seatPopupList) return;
  const data = regionSummary.get(regionKey);
  if (!data) return;

  _state.currentOpenSeatName = null;
  seatPopupTitle.textContent = `${getRegionLabel(regionKey, state.currentRegionLabelsByKey)} List Vote`;

  const totalSeats = Object.values(data.seatsByParty).reduce((a, b) => a + b, 0);
  seatPopupMeta.innerHTML = `<span class="maps-popup-meta-item">Total seats: ${totalSeats}</span>`;

  const totalVotes = Object.values(data.votesByParty).reduce((a, b) => a + b, 0);
  const rows = Object.entries(data.votesByParty)
    .map(([party, votes]) => ({ party, votes, pct: totalVotes > 0 ? (votes / totalVotes) * 100 : 0 }))
    .sort((a, b) => b.votes - a.votes)
    .slice(0, 8);

  const maxPct = rows.reduce((m, r) => Math.max(m, r.pct), 0);
  seatPopupList.innerHTML = '';
  rows.forEach((row) => {
    const barWidth = maxPct > 0 ? Math.min(75, (row.pct / maxPct) * 75) : 0;
    const seats = data.seatsByParty[row.party] || 0;
    const item = document.createElement('div');
    item.className = 'maps-popup-row';
    item.style.setProperty('--maps-popup-bar-width', `${barWidth}%`);
    item.style.setProperty('--maps-popup-bar-colour', manifest.colourParty(row.party));
    item.innerHTML = `
      <div class="maps-popup-party"><span class="maps-seat-icon" style="background:${manifest.colourParty(row.party)}"></span>${escapeHtml(manifest.labelParty(row.party))}</div>
      <div class="maps-popup-values">
        <span>${formatPct(row.pct)}%</span>
        <span style="color:#6b7280">${seats} seat${seats !== 1 ? 's' : ''}</span>
      </div>
    `;
    seatPopupList.appendChild(item);
  });

  seatPopup.hidden = false;
}

/**
 * Renders the region table overlay showing list-seat colour bars for each region. Shows the card when regionSummary is provided, hides it otherwise. Each row click flashes the region on the map and opens the region popup.
 * @param {Map<string, object>|null} regionSummary - Region key → { seatsByParty, votesByParty } map, or null to hide.
 * @returns {void}
 */
function renderRegionTable(regionSummary) {
  if (!regionCard || !regionTableBody) return;
  if (!regionSummary || regionSummary.size === 0) {
    regionCard.hidden = true;
    return;
  }

  regionTableBody.innerHTML = '';

  regionSummary.forEach((data, regionKey) => {
    const entries = Object.entries(data.seatsByParty)
      .filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1]);
    const total = entries.reduce((s, [, n]) => s + n, 0);

    const tr = document.createElement('tr');
    tr.className = 'maps-region-table-row';
    tr.addEventListener('click', () => {
      _state.mapInteractionController.flashRegion(regionKey);
      renderRegionPopup(regionKey, regionSummary);
    });

    const tdName = document.createElement('td');
    tdName.className = 'maps-region-table-name';
    tdName.textContent = getRegionLabel(regionKey, state.currentRegionLabelsByKey);
    tr.appendChild(tdName);

    const tdSeats = document.createElement('td');
    tdSeats.className = 'maps-region-table-seats';

    if (total > 0) {
      const barEl = document.createElement('div');
      barEl.className = 'maps-region-table-bar';
      entries.forEach(([party, count]) => {
        const seg = document.createElement('div');
        seg.className = 'maps-region-table-bar-seg';
        seg.style.width = `${(count / total) * 100}%`;
        seg.style.background = manifest.colourParty(party);
        if (count >= 2) seg.textContent = count;
        barEl.appendChild(seg);
      });
      tdSeats.appendChild(barEl);
    }

    tr.appendChild(tdSeats);
    regionTableBody.appendChild(tr);
  });

  regionCard.hidden = false;

  const toggleBtn = document.getElementById('mapsRegionCardToggle');
  regionCard.classList.remove('maps-region-card--collapsed');
  if (toggleBtn) {
    toggleBtn.textContent = '▼';
    const onToggle = () => {
      const collapsed = regionCard.classList.toggle('maps-region-card--collapsed');
      toggleBtn.textContent = collapsed ? '▶' : '▼';
    };
    const thead = regionCard.querySelector('thead');
    if (thead) thead.onclick = onToggle;
  }
}

/**
 * Renders up to 300 seat rows sorted alphabetically into the seat list panel. Each row shows the winner colour, name, and gain-from indicator. Click zooms and opens the seat popup.
 * @param {Array<object>} seats - Visible seat objects to render in the list.
 * @param {Array<object>|null} [comparisonSeats=null] - Optional comparison seats used to show gain-from indicators.
 * @returns {void}
 */
function renderSeatList(seats, comparisonSeats = null) {
  if (!seatList) return;
  seatList.innerHTML = '';
  _state.selectedSeatRow = null;
  _state.seatListRowByKey = new Map();

  const comparisonWinnerBySeat = comparisonSeats ? buildWinnerBySeat(comparisonSeats) : new Map();

  const ordered = [...seats].sort((a, b) => a.seat.localeCompare(b.seat));

  const renderSeatRow = (seat) => {
    const seatName = seat.seat || 'Unknown seat';
    const seatKey = seatLookupKey(seatName);
    const winnerKey = seat.winner || 'others';
    const comparisonWinnerKey = comparisonWinnerBySeat.get(seatLookupKey(seatName));
    const gainedFrom = comparisonWinnerKey && comparisonWinnerKey !== winnerKey ? comparisonWinnerKey : null;
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'maps-seat-row';
    item.dataset.seatKey = seatKey;
    item.setAttribute('aria-label', `Zoom to ${seatName}`);
    item.innerHTML = `
      <span class="maps-seat-main">
        <span class="maps-seat-icon maps-seat-owner-icon" style="background:${manifest.colourParty(winnerKey)}" title="${manifest.labelParty(winnerKey)}"></span>
        <span class="maps-seat-name">${seatName}</span>
      </span>
      <span class="maps-seat-meta">
        ${gainedFrom ? `<span class="maps-seat-gain"><span class="maps-seat-gain-label">GAIN FROM</span><span class="maps-seat-icon" style="background:${manifest.colourParty(gainedFrom)}" title="${manifest.labelParty(gainedFrom)}"></span></span>` : '<span class="maps-seat-gain-placeholder"></span>'}
      </span>
    `;

    item.addEventListener('click', () => {
      setSelectedSeatRowByKey(seatKey);

      const zoomed = _state.mapInteractionController.zoomToSeat(seatName);
      if (seatPreview) {
        seatPreview.textContent = zoomed ? `Selected: ${seatName}` : `Seat not found on map: ${seatName}`;
      }
      renderSeatPopup(seatName);
    });

    _state.seatListRowByKey.set(seatKey, item);
    seatList.appendChild(item);
  };

  ordered.slice(0, 300).forEach(renderSeatRow);
}

/**
 * Marks the seat list row for seatKey as selected (is-selected class) and deselects the previously selected row.
 * @param {string} seatKey - Normalized seat lookup key identifying which row to select.
 * @returns {void}
 */
function setSelectedSeatRowByKey(seatKey) {
  const nextRow = _state.seatListRowByKey.get(seatKey);
  if (!nextRow) return;

  if (_state.selectedSeatRow && _state.selectedSeatRow !== nextRow) {
    _state.selectedSeatRow.classList.remove('is-selected');
  }
  nextRow.classList.add('is-selected');
  _state.selectedSeatRow = nextRow;
}

/**
 * Builds the module-level seat name search index (_state.seatSearchNames and _state.currentSeatNameByKey) from the provided seats. Returns the sorted name array.
 * @param {Array<object>} seats - Array of seat objects with a `seat` name property.
 * @returns {string[]} Sorted array of seat name strings for autocomplete use.
 */
function buildSeatSearchIndex(seats) {
  _state.currentSeatNameByKey = new Map();
  const names = [];

  (seats || []).forEach((seat) => {
    const seatName = String(seat?.seat || '').trim();
    if (!seatName) return;
    const key = seatLookupKey(seatName);
    if (_state.currentSeatNameByKey.has(key)) return;
    _state.currentSeatNameByKey.set(key, seatName);
    names.push(seatName);
  });

  names.sort((a, b) => a.localeCompare(b));
  _state.seatSearchNames = names;
  return names;
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
  _state.seatSearchNames.forEach((name) => {
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
 * Updates the seat name list used for autocomplete suggestions and hides any open dropdown.
 * @param {string[]} seatNames - New array of seat name strings to use for autocomplete.
 * @returns {void}
 */
function applySeatSearchSuggestions(seatNames) {
  if (!seatSearchInput) return;
  _state.seatSearchNames = Array.isArray(seatNames) ? [...seatNames] : [];
  hideSeatSearchSuggestions();
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
  let seatName = _state.currentSeatNameByKey.get(directKey) || null;

  if (!seatName) {
    const queryLower = rawQuery.toLowerCase();
    seatName = Array.from(_state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().startsWith(queryLower))
      || Array.from(_state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().includes(queryLower))
      || null;
  }

  if (!seatName) {
    if (seatPreview) seatPreview.textContent = `Seat not found: ${rawQuery}`;
    return;
  }

  const seatKey = seatLookupKey(seatName);
  const zoomed = _state.mapInteractionController.zoomToSeat(seatName);
  if (zoomed) {
    setSelectedSeatRowByKey(seatKey);
    renderSeatPopup(seatName);
    if (seatPreview) seatPreview.textContent = `Selected: ${seatName}`;
    if (seatSearchInput) seatSearchInput.value = seatName;
    return;
  }

  if (seatPreview) seatPreview.textContent = `Seat not found on map: ${seatName}`;
}

/**
 * Returns a descriptive name for a map ID. Checks the manifest first (in case a name
 * field is added later), then falls back to the known map name constants.
 * @param {number} mapId
 * @returns {string|null}
 */
function getMapName(mapId) {
  const manifestName = manifest?.mapModes?.[String(mapId)]?.name;
  if (manifestName) return manifestName;
  const knownNames = {
    1: WESTMINSTER_OLD_MAP_NAME,
    2: WESTMINSTER_NEW_MAP_NAME,
    11: HOLYROOD_OLD_MAP_NAME,
    12: HOLYROOD_NEW_MAP_NAME,
  };
  return knownNames[mapId] ?? null;
}

/**
 * Returns the mapId for the current election if it supports postcode lookup, otherwise null.
 * Only the current Westminster and Holyrood boundary maps are supported.
 * Use as a boolean check (null = unsupported) or to pass to lookupPostcode.
 * @returns {number|null}
 */
function getPostcodeMapId() {
  const mapId = state.currentElection.mapId;
  if (mapId == null) return null;
  const name = getMapName(mapId);
  return name === WESTMINSTER_NEW_MAP_NAME || name === HOLYROOD_NEW_MAP_NAME ? mapId : null;
}

/**
 * Shows or hides the postcode search group based on whether the current election supports
 * postcode lookup. Clears the input and any error state when hiding.
 * @returns {void}
 */
function updatePostcodeSearchVisibility() {
  if (!postcodeSearchGroup) return;
  const mapId = getPostcodeMapId();
  const visible = mapId !== null;
  const isHolyrood = mapId === 12;
  postcodeSearchGroup.hidden = !visible;
  if (postcodeWarningBtn) postcodeWarningBtn.hidden = !isHolyrood;
  if (!isHolyrood && postcodeWarningPanel) postcodeWarningPanel.hidden = true;
  if (!visible && postcodeSearchInput) {
    postcodeSearchInput.value = '';
    clearPostcodeError();
  }
}

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
  const mapId = getPostcodeMapId();
  if (!mapId) return null;

  // Strip all whitespace then re-insert the canonical space before the inward code
  // (always the last 3 characters). Both endpoints require this format.
  const stripped = postcode.trim().toUpperCase().replace(/\s+/g, '');
  const normalised = stripped.length >= 5 ? `${stripped.slice(0, -3)} ${stripped.slice(-3)}` : stripped;

  const mapName = getMapName(mapId);
  let url = '';
  let resultProperty = '';

  switch (mapName) {
    case HOLYROOD_NEW_MAP_NAME:
      url = `https://api.postcodes.io/scotland/postcodes/${encodeURIComponent(normalised)}`;
      resultProperty = 'scottish_parliamentary_constituency';
      break;
    case WESTMINSTER_NEW_MAP_NAME:
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
    if (!_state.currentSeatNameByKey.has(seatKey) && mapName === HOLYROOD_NEW_MAP_NAME) {
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
function syncRightPanelHeightToMap() {
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

// TODO: prefer `state.electionData.summary.text` for new callers; remove once the predict-mode caller migrates
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
      if (state.currentElection.id === 'eu-referendum-2016' && isPredictNorthernIrelandRegion(datum.properties?.region)) {
        return '#dce4ea';
      }

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
    .attr('stroke', (datum) => {
      if (state.currentElection.id !== 'eu-referendum-2016') return null;
      return isPredictNorthernIrelandRegion(datum.properties?.region) ? '#dce4ea' : null;
    })
    .on('mouseenter', (_event, datum) => {
      const seatName = seatNameFromFeature(datum);
      if (seatPreview && seatName) seatPreview.textContent = `Selected: ${seatName}`;
    })
    .on('click', (event, datum) => {
      event.stopPropagation();
      setActiveSeatPath(event.currentTarget);
      const seatName = seatNameFromFeature(datum);
      if (seatName) {
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

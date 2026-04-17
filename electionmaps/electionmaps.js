import * as d3 from '../site/vendor/d3.v7.esm.js';
import {
  feature as topojsonFeature,
  mesh as topojsonMesh,
  merge as topojsonMerge,
} from '../site/vendor/topojson-client.v3.esm.js';
import {
  PREDICT_BASE_PARTY_KEYS,
  PREDICT_NI_PARTY_KEYS,
  PREDICT_HOLYROOD_PARTY_KEYS,
  PREDICT_NAT_COLUMN_KEY,
  labelParty as coreLabelParty,
  colourParty as coreColourParty,
  HOLYROOD_NATIONAL_KEY,
  resolvedHolyroodShare as coreResolvedHolyroodShare,
  holyroodNationalOtherShare as coreHolyroodNationalOtherShare,
  holyroodResolvedOtherShare as coreHolyroodResolvedOtherShare,
  formatInt,
  formatPct,
  formatSigned,
  deltaClass,
  normalizeRegionKey,
  titleCaseFromRegionKey,
  seatLookupKey,
  totalVotesForSeat,
  seatMajorityStats,
  seatGainFromPartyKey,
  buildSeatIndex,
  summarizeElection,
  normalizeSeats,
  isPredictNorthernIrelandRegion,
  isPredictEnglishRegion,
  PREDICT_ENGLAND_KEY,
  roundPredictShareValue,
  clampNumber,
  predictInputKey,
  formatPredictShare,
  normalizePredictShareMap,
  resolvePredictInputPartyKey,
  collectPredictInputPartyKeysForRegion,
  formatPredictRegionLabel,
  buildPredictBaselineShares,
  projectedSeatForPredictMode,
  projectHolyroodSeats,
  collectHolyroodPredictInputRows,
  buildHolyroodNationalBaselines,
  isListSeat,
  seatNameFromFeature,
  buildWinnerBySeat,
  buildRegionSummary,
  cloneSeatRecord,
  encodePredictPayload,
  decodePredictPayload,
  buildRegionLabelLookup,
  buildVisibleSeatKeySet,
  getChoroplethValue,
  voteSharePct,
  resolveElectionFiles,
  getPredictBaselineShare,
  getPredictInputShareValue,
  calculatePredictEnteredShareTotal,
  calculatePredictOtherShare,
  collectPredictInputRows,
  parsePollTrackerData,
  escapeHtml,
} from './core.js';
import { state } from './state.js';

// =====================================================================
// REFACTORED — submodule imports above; orchestration code lifts here
// =====================================================================

// =====================================================================
// WIRE CONTROLS
// Refactor not complete in this section.
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
  wirePollTrackerControls();
  wireSeatSearch();
  wirePostcodeSearch();
  wireVoteTotalsSorting(() => {
    if (!window.__mapsCurrentSummary) return;
    renderVoteTotals(window.__mapsCurrentSummary, window.__mapsComparisonSummary || null, {
      showVoteTotals: window.__mapsShowVoteTotals !== false,
    });
    syncRightPanelHeightToMap();
  });
}

/**
 * Attaches click handlers to all [data-map-action] buttons for zoom-in, zoom-out, reset-zoom, and reset-view actions.
 * @returns {void}
 */
function wireMapInteractions() {
  document.querySelectorAll('[data-map-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.getAttribute('data-map-action');
      if (action === 'zoom-in') state.mapInteractionController.zoomBy(1.2);
      if (action === 'zoom-out') state.mapInteractionController.zoomBy(0.83);
      if (action === 'reset-zoom') state.mapInteractionController.reset();
      if (action === 'reset-view') {
        state.mapInteractionController.reset();
        resetPrimaryFilters();
        resetChoropleths();
        renderMapWithViewState();
      }
    });
  });

}

/**
 * Attaches click handlers to all [data-popup-action] buttons, supporting 'close' and 'toggle' actions on their target panel element. On mobile shows/hides a backdrop overlay. Guards against double-wiring.
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
 * Attaches change handlers to all filter and choropleth inputs, and click handlers to the gains, reset-filters, and reset-choropleths buttons. Guards against double-wiring.
 * @returns {void}
 */
function wireMapViewControls() {
  if (filterPartySelect?.dataset.wired === 'true') return;

  /** Reads all filter/choropleth input values into state and re-renders the map. */
  const applyFromInputs = () => {
    syncMapControlStateFromInputs();
    renderMapWithViewState({ preserveZoom: true });
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
      state.mapViewState.gainsOnly = !state.mapViewState.gainsOnly;
      syncMapControlInputsFromState();
      renderMapWithViewState({ preserveZoom: true });
    });
  }

  if (filtersResetButton) {
    filtersResetButton.addEventListener('click', () => {
      resetPrimaryFilters();
      renderMapWithViewState({ preserveZoom: true });
    });
  }

  if (choroplethsResetButton) {
    choroplethsResetButton.addEventListener('click', () => {
      resetChoropleths();
      renderMapWithViewState({ preserveZoom: true });
    });
  }

  if (filterPartySelect) filterPartySelect.dataset.wired = 'true';
}

/**
 * Attaches click handlers to the predict apply, submit, share, reset, and close buttons. Guards against double-wiring with a dataset flag.
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

  if (predictWindowCloseButton) {
    predictWindowCloseButton.addEventListener('click', () => {
      deactivatePredictMode();
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
 * Attaches a change handler to a poll tracker metric checkbox that re-renders the chart when poll tracker is active. Guards against double-wiring.
 * @param {HTMLInputElement|null} inputEl - The checkbox input element to wire; no-op if null or already wired.
 * @returns {void}
 */
function wirePollTrackerMetricInput(inputEl) {
  if (!inputEl || inputEl.dataset.wired === 'true') return;
  inputEl.addEventListener('change', () => {
    if (state.pollTrackerModeActive) renderPollTrackerChart();
  });
  inputEl.dataset.wired = 'true';
}

/**
 * Wires the seats and vote-% metric inputs and all [data-polltracker-range] range buttons, re-rendering the chart on change.
 * @returns {void}
 */
function wirePollTrackerControls() {
  wirePollTrackerMetricInput(pollTrackerMetricSeatsInput);
  wirePollTrackerMetricInput(pollTrackerMetricVotesInput);

  document.querySelectorAll('[data-polltracker-range]').forEach((button) => {
    if (button.dataset.wired === 'true') return;
    button.addEventListener('click', () => {
      const nextRange = button.getAttribute('data-polltracker-range') || 'all';
      state.pollTrackerRangeSelection = nextRange;
      document.querySelectorAll('[data-polltracker-range]').forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate.getAttribute('data-polltracker-range') === nextRange);
      });
      if (state.pollTrackerModeActive) renderPollTrackerChart();
    });
    button.dataset.wired = 'true';
  });
}

/**
 * Attaches all seat search event listeners (focus, input, change, blur, keydown for arrow/enter/escape navigation, outside-click to close). Guards against double-wiring.
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
      if (!state.seatSearchSuggestions.length) {
        showSeatSearchSuggestions(seatSearchInput.value);
      }
      if (!state.seatSearchSuggestions.length) return;
      event.preventDefault();
      state.seatSearchSuggestionIndex = Math.min(state.seatSearchSuggestionIndex + 1, state.seatSearchSuggestions.length - 1);
      updateSeatSearchHighlight();
      return;
    }

    if (event.key === 'ArrowUp') {
      if (!state.seatSearchSuggestions.length) return;
      event.preventDefault();
      state.seatSearchSuggestionIndex = Math.max(state.seatSearchSuggestionIndex - 1, 0);
      updateSeatSearchHighlight();
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      if (state.seatSearchSuggestionIndex >= 0 && state.seatSearchSuggestionIndex < state.seatSearchSuggestions.length) {
        const selectedName = state.seatSearchSuggestions[state.seatSearchSuggestionIndex];
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
    if (state.seatSearchMenuEl?.contains(event.target)) return;
    hideSeatSearchSuggestions();
  });

  seatSearchInput.dataset.wired = 'true';
}

/**
 * Attaches event listeners to the postcode search input. On Enter or blur, calls
 * lookupPostcode and passes the result to selectSeatBySearchQuery. Shows an inline
 * error if the lookup fails. Disables the input during fetch. Guards against double-wiring.
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
 * Attaches click and keyboard (Enter/Space) handlers to all [data-sort-key] table headers to trigger sort changes via the onSortChanged callback.
 * @param {function(): void} onSortChanged - Callback invoked after the sort direction is updated; typically re-renders the vote totals table.
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

const mapSvg = document.querySelector('.maps-svg');
const mapContent = document.getElementById('mapContent');
const zoomValue = document.getElementById('mapsZoomValue');
const seatPreview = document.getElementById('mapsSeatPreview');
const electionList = document.getElementById('mapsElectionList');
const subtitle = document.getElementById('mapsSubtitle');
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
const mapsTitle = document.querySelector('.maps-title');
const mapsStage = document.querySelector('.maps-stage');
const mapsPanelRight = document.querySelector('.maps-panel-right');
const mapsMain = document.querySelector('.maps-main');
const seatPopup = document.getElementById('mapsSeatPopup');
const seatPopupTitle = document.getElementById('mapsSeatPopupTitle');
const seatPopupMeta = document.getElementById('mapsSeatPopupMeta');
const seatPopupList = document.getElementById('mapsSeatPopupList');
const seatPopupClose = document.getElementById('mapsSeatPopupClose');
const choroplethLegend = document.getElementById('mapsChoroplethLegend');
const regionCard = document.getElementById('mapsRegionCard');
const regionTableBody = document.getElementById('mapsRegionTableBody');
const pollTrackerView = document.getElementById('mapsPollTrackerView');
const pollTrackerChartWrap = document.getElementById('mapsPollTrackerChartWrap');
const pollTrackerPartyControls = document.getElementById('mapsPollTrackerPartyControls');
const pollTrackerMetricSeatsInput = document.getElementById('mapsPollTrackerMetricSeats');
const pollTrackerMetricVotesInput = document.getElementById('mapsPollTrackerMetricVotes');

const filterPartySelect = document.getElementById('mapsFilterParty');
const filterRegionSelect = document.getElementById('mapsFilterRegion');
const filterSecondPartyGroup = document.getElementById('mapsFilterSecondPartyGroup');
const filterSecondPartySelect = document.getElementById('mapsFilterSecondParty');
const filterMajorityMinInput = document.getElementById('mapsFilterMajorityMin');
const filterMajorityMaxInput = document.getElementById('mapsFilterMajorityMax');
const filterGainsButton = document.getElementById('mapsFilterGainsOnly');
const filtersResetButton = document.getElementById('mapsFiltersReset');
const predictWindow = document.getElementById('mapsPredictWindow');
const predictWindowCloseButton = document.getElementById('mapsPredictWindowClose');
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
const electionCountdownEl = document.getElementById('mapsElectionCountdown');

const POLL_TRACKER_DATA_PATH = 'data/results/model_output_trends.json';
const POLL_TRACKER_META_PATH = 'data/results/model_output_trends_meta.json';
const HOLYROOD_PREDICTION_META_PATH = 'data/results/holyrood-prediction-meta.json';
const MAPS_PAGE_TITLE_SUFFIX = 'Election Maps | Principal Fish';
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
 * Fires a gtag page_view event for a URL, deduplicating against the last tracked path.
 * @param {string} nextUrl - Full URL string to track; parsed to extract pathname and search.
 * @returns {void}
 */
function trackVirtualPageView(nextUrl) {
  if (typeof window.gtag !== 'function') return;

  try {
    const parsed = new URL(nextUrl, window.location.origin);
    const pagePath = `${parsed.pathname}${parsed.search}`;
    if (pagePath === state.lastTrackedVirtualPagePath) return;

    state.lastTrackedVirtualPagePath = pagePath;
    window.gtag('event', 'page_view', {
      page_location: parsed.toString(),
      page_path: pagePath,
      page_title: document.title,
    });
  } catch (_error) {
  }
}

/**
 * Sets the browser tab title, prepending contextLabel when provided.
 * @param {string|null} contextLabel - Optional label to prepend (e.g. election name or mode name).
 * @param {string|null} [parliament=null] - Parliament key ('holyrood' | 'westminster' | null).
 * @returns {void}
 */
function setMapsPageTitle(contextLabel, parliament = null) {
  const label = String(contextLabel || '').trim();
  const parlLabel = parliament ? parliament[0].toUpperCase() + parliament.slice(1) : null;
  const suffix = parlLabel ? `${parlLabel} | ${MAPS_PAGE_TITLE_SUFFIX}` : MAPS_PAGE_TITLE_SUFFIX;
  document.title = label ? `${label} | ${suffix}` : suffix;
}

/**
 * Builds a URLSearchParams for the given view, setting/removing the election and predict params as appropriate.
 * @param {string} view - View name to set ('election', 'predict', or 'polltracker').
 * @param {string|null} [electionId=null] - Election ID to include; falls back to state.currentElectionId if null.
 * @returns {URLSearchParams} Updated search params with view, election, and predict params adjusted.
 */
function buildRouteSearchParams(view, electionId = null) {
  const params = new URLSearchParams(window.location.search);
  params.set('view', view);
  if (view !== 'predict') params.delete('predict');

  if (view === 'polltracker') {
    params.delete('election');
    return params;
  }

  const selectedElectionId = electionId || state.currentElectionId;
  if (selectedElectionId) {
    params.set('election', selectedElectionId);
  }
  return params;
}

/**
 * Replaces the current browser history entry with the URL for the given view, then fires a virtual page view.
 * @param {string} view - View name ('election', 'predict', or 'polltracker').
 * @param {string|null} [electionId=null] - Election ID to encode in the URL; falls back to state.currentElectionId.
 * @returns {void}
 */
function replaceRouteState(view, electionId = null) {
  const params = buildRouteSearchParams(view, electionId);
  const nextUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState({}, '', nextUrl);
  trackVirtualPageView(nextUrl);
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
      state.predictColumnPartyKeys.forEach((partyKey) => {
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

/** @param {string} partyKey @returns {string} */
function labelParty(partyKey) { return coreLabelParty(state.manifestPartiesByKey, partyKey); }

/** @param {string} partyKey @returns {string} */
function colourParty(partyKey) { return coreColourParty(state.manifestPartiesByKey, partyKey); }

/**
 * Fetches a URL and passes the Response through the provided parser function. Throws on non-OK status.
 * @param {string} url - URL to fetch.
 * @param {function(Response): Promise<*>} parser - Function that receives the Response and returns a parsed value.
 * @returns {Promise<*>} Resolved value returned by the parser function.
 * @throws {Error} When the response status is not OK.
 */
async function fetchResource(url, parser) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return parser(response);
}

/**
 * Fetches Holyrood prediction metadata (latest poll snippet) once per page load. Silently ignores fetch errors.
 * @returns {Promise<void>}
 */
async function loadHolyroodPredictionMetaIfNeeded() {
  if (state.holyroodPredictionSnippet) return;
  try {
    const payload = await fetchJson(HOLYROOD_PREDICTION_META_PATH);
    state.holyroodPredictionSnippet = String(payload?.latest_poll_snippet || '').trim();
  } catch (_error) {
    state.holyroodPredictionSnippet = '';
  }
}

/**
 * Fetches poll tracker metadata (latest snippet text) once per page load. Silently ignores fetch errors.
 * @returns {Promise<void>}
 */
async function loadPollTrackerMetaIfNeeded() {
  if (state.pollTrackerMetaLoaded) return;

    try {
      const payload = await fetchJson(POLL_TRACKER_META_PATH);
      state.pollTrackerLatestSnippet = String(payload?.latest_poll_snippet || '').trim();
  } catch (_error) {
    state.pollTrackerLatestSnippet = '';
  }

  state.pollTrackerMetaLoaded = true;
}

/**
 * Sets the subtitle element content, optionally appending the latest poll snippet as a secondary span.
 * @param {string} baseText - Primary subtitle text to display.
 * @param {{includeLatestPollSnippet?: boolean, snippetOverride?: string}} [options={}] - Options.
 * @returns {void}
 */
function setSubtitleText(baseText, options = {}) {
  if (!subtitle) return;

  const includeLatestPollSnippet = options.includeLatestPollSnippet === true;
  const latestPollSnippet = options.snippetOverride != null
    ? String(options.snippetOverride).trim()
    : includeLatestPollSnippet ? String(state.pollTrackerLatestSnippet || '').trim() : '';

  subtitle.textContent = '';
  subtitle.classList.toggle('maps-subtitle-has-latest', Boolean(latestPollSnippet));

  const mainSpan = document.createElement('span');
  mainSpan.className = 'maps-subtitle-main';
  mainSpan.textContent = String(baseText || '').trim();
  subtitle.appendChild(mainSpan);

  if (!latestPollSnippet) return;

  const latestSpan = document.createElement('span');
  latestSpan.className = 'maps-subtitle-latest';
  latestSpan.textContent = latestPollSnippet;
  subtitle.appendChild(latestSpan);
}

// 7 May 2026, 07:00 BST = 06:00 UTC
const HOLYROOD_ELECTION_DATE = new Date('2026-05-07T06:00:00Z');

/**
 * Formats a millisecond duration as "Xd Xh Xm Xs".
 * @param {number} ms - Milliseconds remaining (must be > 0).
 * @returns {string} Formatted countdown string.
 */
function formatCountdown(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${days}d ${hours}h ${minutes}m ${seconds}s`;
}

/**
 * Shows or hides the election countdown element based on the current election type and mode.
 * Starts a 1-second interval tick when visible; clears it when hidden or after election day.
 * Visible only when state.currentElectionType is 'holyrood_uns' and poll tracker is not active.
 * @returns {void}
 */
function updateElectionCountdown() {
  if (!electionCountdownEl) return;

  const shouldShow = state.currentElectionType === 'holyrood_uns' && !state.pollTrackerModeActive;

  if (state.countdownIntervalId !== null) {
    clearInterval(state.countdownIntervalId);
    state.countdownIntervalId = null;
  }

  if (!shouldShow) {
    electionCountdownEl.hidden = true;
    return;
  }

  const tick = () => {
    const msLeft = HOLYROOD_ELECTION_DATE - Date.now();
    if (msLeft <= 0) {
      electionCountdownEl.hidden = true;
      // Clear and null the handle so updateElectionCountdown can safely restart if called again.
      clearInterval(state.countdownIntervalId);
      state.countdownIntervalId = null;
      return;
    }
    electionCountdownEl.textContent = `${formatCountdown(msLeft)} · Holyrood election · 7 May 2026`;
    electionCountdownEl.hidden = false;
  };

  tick();
  state.countdownIntervalId = setInterval(tick, 1000);
}

/**
 * Fetches and parses a JSON resource from the given URL.
 * @param {string} url - URL of the JSON resource.
 * @returns {Promise<*>} Parsed JSON value.
 */
async function fetchJson(url) {
  return fetchResource(url, (response) => response.json());
}

/**
 * Toggles the 'active' CSS class on the poll tracker nav button.
 * @param {boolean} active - True to mark the button active, false to remove the class.
 * @returns {void}
 */
function setPollTrackerNavState(active) {
  if (!state.pollTrackerModeLinkEl) return;
  state.pollTrackerModeLinkEl.classList.toggle('active', active);
}

/**
 * Shows or hides the poll tracker view, toggling the map stage and right panel visibility accordingly.
 * @param {boolean} active - True to show the poll tracker layout and hide the map stage; false to restore the map layout.
 * @returns {void}
 */
function setPollTrackerLayoutVisible(active) {
  if (mapsStage) {
    mapsStage.hidden = active;
    mapsStage.style.display = active ? 'none' : '';
  }
  if (mapsPanelRight) {
    mapsPanelRight.hidden = active;
    mapsPanelRight.style.display = active ? 'none' : '';
  }
  if (pollTrackerView) {
    pollTrackerView.hidden = !active;
    pollTrackerView.style.display = active ? '' : 'none';
  }
  if (mapsMain) {
    mapsMain.style.gridTemplateColumns = active ? 'minmax(0, 1fr)' : '';
    mapsMain.style.width = active ? '100%' : '';
  }
}

/**
 * Returns the party key values of all checked party toggle checkboxes in the poll tracker controls.
 * @returns {string[]} Array of party key strings for all currently checked party toggle inputs.
 */
function getPollTrackerSelectedParties() {
  return Array.from(document.querySelectorAll('.maps-polltracker-party-toggle input[type="checkbox"]'))
    .filter((input) => input.checked)
    .map((input) => input.value);
}

/**
 * Renders the poll tracker D3 SVG chart into pollTrackerChartWrap.
 * Draws line series for selected parties with separate left (seats) and right (vote %) axes.
 * Includes a crosshair tooltip and respects the current date-range selection.
 * @returns {void}
 */
function renderPollTrackerChart() {
  if (!pollTrackerChartWrap) return;

    const selectedParties = getPollTrackerSelectedParties();
    const seatsEnabled = Boolean(pollTrackerMetricSeatsInput?.checked);
    const votePctEnabled = Boolean(pollTrackerMetricVotesInput?.checked);

  pollTrackerChartWrap.innerHTML = '';
  pollTrackerChartWrap.style.position = 'relative';

  if (!state.pollTrackerTimeline.length) {
    pollTrackerChartWrap.innerHTML = '<div class="maps-polltracker-empty">No poll tracker data available.</div>';
    return;
  }

  if (!selectedParties.length || !(seatsEnabled || votePctEnabled)) {
    pollTrackerChartWrap.innerHTML = '<div class="maps-polltracker-empty">Select at least one party and one metric (Seats/Vote %).</div>';
    return;
  }

  const rangeSize = state.pollTrackerRangeSelection === 'all'
    ? state.pollTrackerTimeline.length
    : Number(state.pollTrackerRangeSelection);
  const windowSize = Number.isFinite(rangeSize) && rangeSize > 0
    ? Math.min(rangeSize, state.pollTrackerTimeline.length)
    : state.pollTrackerTimeline.length;
  const windowStart = Math.max(0, state.pollTrackerTimeline.length - windowSize);
  const visibleTimeline = state.pollTrackerTimeline.slice(windowStart);

  const width = Math.max(760, pollTrackerChartWrap.clientWidth - 8);
  const height = 520;
  const margin = { top: 14, right: 84, bottom: 58, left: 70 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const svg = d3.create('svg').attr('viewBox', `0 0 ${width} ${height}`);
  const plot = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);
  const tooltip = document.createElement('div');
  tooltip.className = 'maps-polltracker-tooltip';
  tooltip.hidden = true;
  pollTrackerChartWrap.appendChild(tooltip);
  const crosshairLine = plot.append('line')
    .attr('class', 'maps-polltracker-crosshair')
    .attr('y1', 0)
    .attr('y2', innerHeight)
    .attr('opacity', 0);

  const visibleTimelineDates = visibleTimeline.map((entry) => entry.dateValue).filter((value) => value instanceof Date);
  const useTimeScale = visibleTimelineDates.length === visibleTimeline.length && visibleTimeline.length > 1;

  const x = useTimeScale
    ? d3.scaleTime()
      .domain(d3.extent(visibleTimelineDates))
      .range([0, innerWidth])
    : d3.scaleLinear()
      .domain([0, Math.max(0, visibleTimeline.length - 1)])
      .range([0, innerWidth]);

  const selectedSeries = selectedParties
    .map((partyKey) => state.pollTrackerSeriesByParty.get(partyKey))
    .filter(Boolean)
    .map((series) => ({
      ...series,
      seats: series.seats.slice(windowStart),
      votePct: series.votePct.slice(windowStart),
    }));

  const seatsMax = d3.max(selectedSeries.flatMap((series) => series.seats.filter((value) => Number.isFinite(value)))) || 1;
  const votePctMax = d3.max(selectedSeries.flatMap((series) => series.votePct.filter((value) => Number.isFinite(value)))) || 1;

  const ySeats = d3.scaleLinear().domain([0, seatsMax * 1.08]).nice().range([innerHeight, 0]);
  const yVotePct = d3.scaleLinear().domain([0, Math.min(100, votePctMax * 1.08)]).nice().range([innerHeight, 0]);

  const gridAxis = seatsEnabled ? d3.axisLeft(ySeats).ticks(6) : d3.axisRight(yVotePct).ticks(6);
  plot.append('g')
    .attr('class', 'maps-polltracker-axis')
    .call(gridAxis.tickSize(-innerWidth).tickFormat(''))
    .selectAll('line')
    .attr('class', 'maps-polltracker-grid-line');

  const maxTicksByWidth = Math.max(4, Math.floor(innerWidth / 105));
  const xAxisGroup = plot.append('g')
    .attr('class', 'maps-polltracker-axis')
    .attr('transform', `translate(0,${innerHeight})`)
    .call(useTimeScale
      ? d3.axisBottom(x)
        .ticks(maxTicksByWidth)
        .tickFormat((value) => d3.timeFormat('%Y-%m-%d')(value))
      : d3.axisBottom(x)
        .tickValues(d3.range(0, visibleTimeline.length, Math.max(1, Math.ceil(visibleTimeline.length / Math.max(1, maxTicksByWidth)))))
        .tickFormat((index) => visibleTimeline[index]?.label || '')
    );

  xAxisGroup.selectAll('text')
    .style('text-anchor', 'end')
    .attr('dx', '-0.38em')
    .attr('dy', '0.44em')
    .attr('transform', 'rotate(-32)');

  if (seatsEnabled) {
    plot.append('g')
      .attr('class', 'maps-polltracker-axis')
      .call(d3.axisLeft(ySeats).ticks(7));

    plot.append('text')
      .attr('class', 'maps-polltracker-axis-label')
      .attr('transform', 'rotate(-90)')
      .attr('x', -innerHeight / 2)
      .attr('y', -52)
      .attr('text-anchor', 'middle')
      .text('Seats');
  }

  if (votePctEnabled) {
    plot.append('g')
      .attr('class', 'maps-polltracker-axis')
      .attr('transform', `translate(${innerWidth},0)`)
      .call(d3.axisRight(yVotePct).ticks(7).tickFormat((value) => `${Number(value).toFixed(1)}%`));

    plot.append('text')
      .attr('class', 'maps-polltracker-axis-label')
      .attr('transform', 'rotate(90)')
      .attr('x', innerHeight / 2)
      .attr('y', -(innerWidth + 56))
      .attr('text-anchor', 'middle')
      .text('Vote %');
  }

  plot.append('text')
    .attr('class', 'maps-polltracker-axis-label')
    .attr('x', innerWidth / 2)
    .attr('y', innerHeight + 48)
    .attr('text-anchor', 'middle')
    .text('Date');

  const seatsLine = d3.line()
    .defined((value) => Number.isFinite(value))
    .x((_value, index) => (useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index)))
    .y((value) => ySeats(value));

  const votePctLine = d3.line()
    .defined((value) => Number.isFinite(value))
    .x((_value, index) => (useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index)))
    .y((value) => yVotePct(value));

  /**
   * Positions and populates the crosshair tooltip for a pointer event on a series path.
   * Reads the current pointer position, bisects the visible timeline to find the nearest data point,
   * updates the crosshair line, and sets the tooltip HTML and position.
   * @param {PointerEvent} event - The DOM pointer event from the SVG path element.
   * @param {{partyName: string, colour: string, seats: Array<number|null>, votePct: Array<number|null>}} series - The series being hovered.
   * @returns {void}
   */
  const showTrackerTooltip = (event, series) => {
    const [pointerX] = d3.pointer(event, svg.node());
    const plotX = pointerX - margin.left;
    if (plotX < 0 || plotX > innerWidth) {
      tooltip.hidden = true;
      return;
    }

    const index = useTimeScale
      ? (() => {
          const hoveredDate = x.invert(plotX);
          const bisectDate = d3.bisector((entry) => entry.dateValue.getTime()).left;
          const candidate = bisectDate(visibleTimeline, hoveredDate.getTime());
          const leftIndex = Math.max(0, candidate - 1);
          const rightIndex = Math.min(visibleTimeline.length - 1, candidate);
          const leftDistance = Math.abs(visibleTimeline[leftIndex].dateValue.getTime() - hoveredDate.getTime());
          const rightDistance = Math.abs(visibleTimeline[rightIndex].dateValue.getTime() - hoveredDate.getTime());
          return rightDistance < leftDistance ? rightIndex : leftIndex;
        })()
      : Math.max(0, Math.min(visibleTimeline.length - 1, Math.round(x.invert(plotX))));
    const xPos = useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index);
    const timelinePoint = visibleTimeline[index];
    const seatsValue = Number(series.seats[index] || 0);
    const votePctValue = Number(series.votePct[index] || 0);
    const partyColour = series.colour || '#9CA3AF';

    crosshairLine
      .attr('x1', xPos)
      .attr('x2', xPos)
      .attr('opacity', 1)
      .raise();

    tooltip.innerHTML = `
      <div class="maps-polltracker-tooltip-party"><span class="maps-predict-grid-swatch" style="background:${partyColour}"></span>${escapeHtml(series.partyName)}</div>
      <div>${timelinePoint?.label || ''}</div>
      <div>Seats: ${formatInt(seatsValue)}</div>
      <div>Vote %: ${formatPct(votePctValue)}%</div>
    `;

    const tooltipX = Math.min(width - 220, Math.max(8, pointerX + 14));
    const tooltipY = Math.min(height - 96, Math.max(8, event.offsetY + 10));
    tooltip.style.left = `${tooltipX}px`;
    tooltip.style.top = `${tooltipY}px`;
    tooltip.hidden = false;
  };

  /** Hides the crosshair tooltip and fades the crosshair line. */
  const hideTrackerTooltip = () => {
    tooltip.hidden = true;
    crosshairLine.attr('opacity', 0);
  };

  selectedSeries.forEach((series) => {
    if (seatsEnabled) {
      plot.append('path')
        .datum(series.seats)
        .attr('fill', 'none')
        .attr('stroke', series.colour)
        .attr('stroke-width', 2.1)
        .attr('d', seatsLine);

      plot.append('path')
        .datum(series.seats)
        .attr('fill', 'none')
        .attr('stroke', 'transparent')
        .attr('stroke-width', 14)
        .attr('d', seatsLine)
        .on('mousemove', (event) => showTrackerTooltip(event, series))
        .on('mouseleave', hideTrackerTooltip);
    }

    if (votePctEnabled) {
      plot.append('path')
        .datum(series.votePct)
        .attr('fill', 'none')
        .attr('stroke', series.colour)
        .attr('stroke-width', 2.1)
        .attr('stroke-dasharray', '6 4')
        .attr('d', votePctLine);

      plot.append('path')
        .datum(series.votePct)
        .attr('fill', 'none')
        .attr('stroke', 'transparent')
        .attr('stroke-width', 14)
        .attr('d', votePctLine)
        .on('mousemove', (event) => showTrackerTooltip(event, series))
        .on('mouseleave', hideTrackerTooltip);
    }
  });

  const legend = svg.append('g').attr('transform', `translate(${width - margin.right},${margin.top - 2})`);
  legend.append('text')
    .text('Solid = Seats, Dashed = Vote %')
    .attr('fill', '#334155')
    .attr('text-anchor', 'end')
    .style('font', '700 11px "DM Sans", "Segoe UI", sans-serif');

  pollTrackerChartWrap.appendChild(svg.node());
}

/**
 * Renders the party toggle checkboxes for the poll tracker, pre-selecting a fixed set of the main UK parties (Reform, Labour, Conservative, Lib Dems, Green, SNP).
 * @returns {void}
 */
function renderPollTrackerPartyControls() {
  if (!pollTrackerPartyControls) return;

  const partyRows = Array.from(state.pollTrackerSeriesByParty.values())
    .sort((a, b) => b.latestSeats - a.latestSeats || a.partyName.localeCompare(b.partyName));

  /** @param {string} name - Party name to normalize. @returns {string} Lowercased, trimmed party name. */
  const normalizePartyName = (name) => String(name || '').trim().toLowerCase();

  /** Returns true if the party name matches one of the fixed default UK parties. */
  const isDefaultParty = (name) => {
    const n = normalizePartyName(name);
    return n.includes('reform') ||
           n.includes('labour') ||
           n.includes('conservative') ||
           n.includes('liberal democrat') || n.includes('lib dem') ||
           n === 'green' ||
           n.includes('snp') || n.includes('scottish national');
  };

  const defaultSelectedPartySet = new Set(
    partyRows.filter((row) => isDefaultParty(row.partyName)).map((row) => row.partyKey)
  );

  pollTrackerPartyControls.innerHTML = '';

  partyRows.forEach((row) => {
    const label = document.createElement('label');
    label.className = 'maps-polltracker-party-toggle';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = row.partyKey;
    checkbox.checked = defaultSelectedPartySet.has(row.partyKey);
    checkbox.addEventListener('change', () => {
      renderPollTrackerChart();
    });

    const swatch = document.createElement('span');
    swatch.className = 'maps-predict-grid-swatch';
    swatch.style.background = row.colour;

    const text = document.createElement('span');
    text.textContent = row.partyName;

    label.appendChild(checkbox);
    label.appendChild(swatch);
    label.appendChild(text);
    pollTrackerPartyControls.appendChild(label);
  });
}

/**
 * Fetches and parses the poll tracker CSV once per page load, populating state.pollTrackerTimeline and state.pollTrackerSeriesByParty.
 * @returns {Promise<void>}
 */
async function loadPollTrackerDataIfNeeded() {
  if (state.pollTrackerDataLoaded) return;

  const data = await fetchJson(POLL_TRACKER_DATA_PATH);
  const parsed = parsePollTrackerData(data, state.manifestPartiesById);

  state.pollTrackerTimeline = parsed.timeline;
  state.pollTrackerSeriesByParty = parsed.seriesByParty;
  state.pollTrackerDataLoaded = true;
}

/**
 * Switches the app into poll tracker mode: deactivates predict mode, loads data, renders controls and chart, and updates the route.
 * @returns {Promise<void>}
 */
async function activatePollTrackerMode() {
  state.predictModeActive = false;
  setPredictModeNavState(false);
  if (predictWindow) predictWindow.hidden = true;

  state.pollTrackerModeActive = true;
  updateElectionCountdown();
  document.querySelectorAll('.maps-election-item.active').forEach((node) => {
    node.classList.remove('active');
  });
  setPollTrackerNavState(true);

  setPollTrackerLayoutVisible(true);
  await loadPollTrackerMetaIfNeeded();
  setMapsPageTitle('Poll tracker', 'westminster');
  setSubtitleText('Poll tracker · model output trends', { includeLatestPollSnippet: true });
  if (seatPreview) seatPreview.textContent = 'Poll tracker mode active.';
  replaceRouteState('polltracker');

  await loadPollTrackerDataIfNeeded();
  renderPollTrackerPartyControls();
  renderPollTrackerChart();
}

/**
 * Sets the active class on parliament tab links to match state.currentParliament, and updates the page
 * h1 to suffix the parliament name (Westminster / Holyrood).
 * @returns {void}
 */
function updateParliamentTabsUI() {
  document.querySelectorAll('[data-parliament]').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.parliament === state.currentParliament);
  });
  if (mapsTitle && state.currentParliament) {
    const label = state.currentParliament[0].toUpperCase() + state.currentParliament.slice(1);
    mapsTitle.textContent = `UK Election Maps · ${label}`;
  }
}

/** Toggles vote percentage column visibility on the totals table. */
function toggleVotePctColumns(show) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-vote-pct-col', !show);
}

/** Sets the active class on vote-totals tab buttons to match state.voteTotalsMode. */
function updateVoteTotalsTabsUI() {
  voteTotalsTabNav?.querySelectorAll('[data-vote-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.voteTab === state.voteTotalsMode);
  });
}

/** Builds vote-totals tab buttons (Overall / Constituency / List) from mapConfig.voteTotalsViews. */
function renderVoteTotalsTabs(mapConfig) {
  if (!voteTotalsTabNav) return;
  voteTotalsTabNav.innerHTML = '';
  const views = mapConfig?.voteTotalsViews ?? [];
  voteTotalsTabNav.hidden = views.length <= 1;
  views.forEach((view, i) => {
    const btn = document.createElement('button');
    btn.className = `maps-vote-tab${i === 0 ? ' active' : ''}`;
    btn.dataset.voteTab = view.id;
    btn.textContent = view.label;
    btn.addEventListener('click', () => {
      state.voteTotalsMode = view.id;
      updateVoteTotalsTabsUI();
      const seats = window.__mapsVisibleSeats || [];
      const compSeats = window.__mapsVisibleComparisonSeats || [];
      const showVotes = state.voteTotalsMode !== 'all';
      const summary = summarizeElection(seats, { mode: state.voteTotalsMode });
      const compSummary = compSeats.length ? summarizeElection(compSeats, { mode: state.voteTotalsMode }) : null;
      window.__mapsCurrentSummary = summary;
      window.__mapsComparisonSummary = compSummary;
      window.__mapsShowVoteTotals = showVotes;
      toggleVoteTotalColumns(showVotes);
      toggleVotePctColumns(showVotes);
      renderVoteTotals(summary, compSummary, { showVoteTotals: showVotes });
    });
    voteTotalsTabNav.appendChild(btn);
  });
}

/** Sets the active class on seat-view tab buttons to match state.currentSeatView. */
function updateSeatViewTabsUI() {
  seatViewTabNav?.querySelectorAll('[data-seat-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.seatView === state.currentSeatView);
  });
}

/** Builds seat-view tab buttons (Constituencies / Regions) from mapConfig.seatViews. */
function renderSeatViewTabs(mapConfig) {
  if (!seatViewTabNav) return;
  seatViewTabNav.innerHTML = '';
  const views = mapConfig?.seatViews ?? [];
  seatViewTabNav.hidden = views.length <= 1;
  views.forEach((view) => {
    const btn = document.createElement('button');
    btn.className = 'maps-seat-view-tab';
    btn.dataset.seatView = view.id;
    btn.textContent = view.label;
    btn.addEventListener('click', () => {
      state.currentSeatView = view.id;
      updateSeatViewTabsUI();
      renderMapWithViewState({ preserveZoom: true });
    });
    seatViewTabNav.appendChild(btn);
  });
}

/**
 * Rebuilds the election list nav, inserting Predict 2029 and Poll tracker buttons after the current-prediction entry (or near the top as a fallback). Stores references to state.predictModeLinkEl and state.pollTrackerModeLinkEl.
 * @param {object} manifest - Elections manifest object with an `elections` array.
 * @param {string|null} activeId - ID of the currently active election, used to highlight the active nav item.
 * @returns {void}
 */
function renderElectionLinks(manifest, activeId) {
  if (!electionList) return;

  const createPredictButton = () => {
    const predictButton = document.createElement('button');
    predictButton.type = 'button';
    predictButton.className = 'maps-election-item';
    predictButton.textContent = state.currentParliament === 'holyrood' ? 'Predict 2026' : 'Predict 2029';
    predictButton.addEventListener('click', () => {
      activatePredictMode().catch((error) => {
        console.error(error);
      });
    });
    return predictButton;
  };

  const createPollTrackerButton = () => {
    const trackerButton = document.createElement('button');
    trackerButton.type = 'button';
    trackerButton.className = 'maps-election-item';
    trackerButton.textContent = 'Poll tracker';
    trackerButton.addEventListener('click', () => {
      activatePollTrackerMode().catch((error) => {
        console.error(error);
      });
    });
    return trackerButton;
  };

  const parliamentElections = manifest.elections.filter((e) => e.parliament === state.currentParliament);
  const parlConfig = state.parliamentFeaturesConfig[state.currentParliament] ?? {};
  const hasPredictMode = parlConfig.features?.includes('predict') ?? false;
  const hasPollTracker = parlConfig.features?.includes('pollTracker') ?? false;
  const predictAnchorId = parlConfig.predictAnchorElectionId ?? null;

  electionList.innerHTML = '';
  state.predictModeLinkEl = null;
  state.pollTrackerModeLinkEl = null;
  let insertedPredictLink = false;
  let insertedPollTrackerLink = false;
  parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = `?view=election&election=${encodeURIComponent(election.id)}&parliament=${state.currentParliament}`;
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (hasPredictMode && !insertedPredictLink && election.id === predictAnchorId) {
      const predictButton = createPredictButton();
      electionList.appendChild(predictButton);
      state.predictModeLinkEl = predictButton;
      insertedPredictLink = true;
    }

    if (hasPollTracker && insertedPredictLink && !insertedPollTrackerLink && election.id !== predictAnchorId) {
      const trackerButton = createPollTrackerButton();
      electionList.appendChild(trackerButton);
      state.pollTrackerModeLinkEl = trackerButton;
      insertedPollTrackerLink = true;
    }
  });

  if (hasPredictMode && !insertedPredictLink) {
    const predictButton = createPredictButton();
    if (electionList.children.length > 0) {
      electionList.insertBefore(predictButton, electionList.children[1] || null);
    } else {
      electionList.appendChild(predictButton);
    }
    state.predictModeLinkEl = predictButton;

    if (hasPollTracker && !insertedPollTrackerLink) {
      const trackerButton = createPollTrackerButton();
      const predictIndex = Array.from(electionList.children).indexOf(predictButton);
      if (predictIndex >= 0 && electionList.children[predictIndex + 1]) {
        electionList.insertBefore(trackerButton, electionList.children[predictIndex + 1]);
      } else {
        electionList.appendChild(trackerButton);
      }
      state.pollTrackerModeLinkEl = trackerButton;
      insertedPollTrackerLink = true;
    }
  }

  if (hasPollTracker && !insertedPollTrackerLink) {
    const trackerButton = createPollTrackerButton();
    if (state.predictModeLinkEl && state.predictModeLinkEl.nextSibling) {
      electionList.insertBefore(trackerButton, state.predictModeLinkEl.nextSibling);
    } else {
      electionList.appendChild(trackerButton);
    }
    state.pollTrackerModeLinkEl = trackerButton;
  }
}

/**
 * Reads manifest.settings and populates the module-level party lookup maps (state.manifestPartiesByKey, state.manifestPartiesById), state.manifestRegionsById, and state.manifestRegionsByMapId.
 * @param {object} manifest - Elections manifest object with a `settings` property containing parties and regionsByMapId.
 * @returns {void}
 */
function hydrateManifestSettings(manifest) {
  const settings = manifest?.settings || {};

  // Build partiesByKey and partiesById from the canonical parties array.
  state.manifestPartiesByKey = {};
  state.manifestPartiesById = new Map();
  const partyRows = Array.isArray(settings.parties) ? settings.parties : [];
  partyRows.forEach((party) => {
    const id = Number(party?.id);
    if (!Number.isFinite(id)) return;
    state.manifestPartiesById.set(id, party);
    const key = party?.key;
    if (key && !state.manifestPartiesByKey[key]) state.manifestPartiesByKey[key] = party;
  });

  // Build integer region ID → normalized region key lookup across all maps.
  state.manifestRegionsById = new Map();
  state.manifestRegionsByMapId = settings.regionsByMapId || {};
  Object.values(state.manifestRegionsByMapId).forEach((regionRows) => {
    (regionRows || []).forEach((region) => {
      const id = Number(region?.id);
      if (!Number.isFinite(id)) return;
      state.manifestRegionsById.set(id, normalizeRegionKey(region?.name || ''));
    });
  });
}

/**
 * Returns the display label for a region, falling back to titleCaseFromRegionKey if not in the current region lookup.
 * @param {string} regionKey - Raw or normalized region key.
 * @returns {string} Human-readable region label from the current region map, or a title-cased derivation as fallback.
 */
function labelRegion(regionKey) {
  const normalized = normalizeRegionKey(regionKey);
  if (!normalized) return 'Unknown';
  const label = state.currentRegionLabelsByKey.get(normalized) || titleCaseFromRegionKey(regionKey);
  return label.replace(/ and /gi, ' & ');
}

/**
 * Updates state.currentSort: toggles direction if the same key is re-selected, otherwise switches to the new key with a default direction.
 * @param {string} sortKey - Column key to sort by (e.g. 'seats', 'votes', 'party').
 * @returns {void}
 */
function setSortDirection(sortKey) {
  if (state.currentSort.key === sortKey) {
    state.currentSort.direction = state.currentSort.direction === 'asc' ? 'desc' : 'asc';
    return;
  }
  state.currentSort.key = sortKey;
  state.currentSort.direction = sortKey === 'party' ? 'asc' : 'desc';
}

/**
 * Lazily initializes and returns the per-region swing Map for a party in state.predictRegionalSwingsByParty.
 * @param {string} partyKey - Party key to look up or initialize a swing map for.
 * @returns {Map<string, number>} Map from region key to swing value for the given party.
 */
function ensurePredictPartySwingMap(partyKey) {
  if (!state.predictRegionalSwingsByParty.has(partyKey)) {
    state.predictRegionalSwingsByParty.set(partyKey, new Map());
  }
  return state.predictRegionalSwingsByParty.get(partyKey);
}

/**
 * Toggles the 'active' CSS class on the predict mode nav button.
 * @param {boolean} active - True to mark the button active, false to remove the class.
 * @returns {void}
 */
function setPredictModeNavState(active) {
  if (!state.predictModeLinkEl) return;
  state.predictModeLinkEl.classList.toggle('active', active);
}

/**
 * Syncs predict window and seat card visibility based on predict mode state, vote totals expansion, and England expansion.
 * @returns {void}
 */
function syncPredictModeRightColumnLayout() {
  if (!predictWindow || !seatCard) return;

  const predictVisible = state.predictModeActive && !state.pollTrackerModeActive;
  predictWindow.hidden = !predictVisible;
  predictWindow.style.display = predictVisible ? '' : 'none';

  const predictCollapsed = predictWindow.classList.contains('maps-predict-window--collapsed');
  const hideSeatCard = predictVisible && !predictCollapsed;
  const forcePredictGridScroll = predictVisible && !predictCollapsed &&
    (state.predictEnglandExpanded || (state.currentParliament === 'holyrood' && state.predictHolyroodRegionsExpanded));
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
  if (state.currentParliament === 'holyrood') return collectHolyroodPredictInputRows(state.predictBaseRegionLabelsByKey);
  return collectPredictInputRows(state.predictBaseRegionLabelsByKey, state.predictEnglandExpanded);
}

function predictPartyKeysForRegion(regionKey) {
  if (state.currentParliament === 'holyrood') return state.predictColumnPartyKeys;
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
 * Clamps inputValue to [0, 100], stores it in state.predictInputByRegionParty, and returns the clamped integer value.
 * @param {string} regionKey - Normalized region key.
 * @param {string} partyKey - Party key.
 * @param {number|string} inputValue - Raw input value from the user (may be a string from an input element).
 * @returns {number} Clamped and rounded integer share value that was stored.
 */
function setPredictInputShareValue(regionKey, partyKey, inputValue) {
  const shareValue = roundPredictShareValue(clampNumber(inputValue, 0, 100));
  state.predictInputByRegionParty.set(predictInputKey(regionKey, partyKey), shareValue);
  return shareValue;
}

// ── Holyrood tab helpers ──────────────────────────────────────────────────────

/** Builds the Holyrood predict state object from current module globals. */
function holyroodPredictState() {
  return {
    constBaseline: state.predictBaselineConstShareByRegionParty,
    listBaseline: state.predictBaselineListShareByRegionParty,
    nationalBaseline: state.predictNationalBaselines,
    nationalListBaseline: state.predictNationalListBaselines,
    constInput: state.predictConstInputByRegionParty,
    listInput: state.predictListInputByRegionParty,
  };
}

function resolvedHolyroodShare(regionKey, partyKey, pass) {
  return coreResolvedHolyroodShare(regionKey, partyKey, pass, holyroodPredictState());
}

function holyroodNationalOtherShare(tabMap, pass) {
  return coreHolyroodNationalOtherShare(tabMap, pass, state.predictColumnPartyKeys, holyroodPredictState());
}


function holyroodResolvedOtherShare(regionKey, pass) {
  return coreHolyroodResolvedOtherShare(regionKey, pass, state.predictColumnPartyKeys, holyroodPredictState());
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
    btn.className = `maps-predict-tab-btn${state.predictHolyroodTab === key ? ' active' : ''}`;
    btn.textContent = label;
    btn.addEventListener('click', () => {
      state.predictHolyroodTab = key;
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
  state.predictOtherCellByRegion = new Map();

  const regions = currentPredictInputRows();
  const table = document.createElement('table');
  table.className = 'maps-predict-grid-table';

  // Header
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const regionTh = document.createElement('th');
  regionTh.textContent = 'Region';
  headRow.appendChild(regionTh);
  state.predictColumnPartyKeys.forEach((pk) => {
    const th = document.createElement('th');
    th.title = labelParty(pk);
    th.innerHTML = `<span class="maps-predict-grid-swatch" style="background:${colourParty(pk)}" aria-hidden="true"></span>`;
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
  natToggle.textContent = state.predictHolyroodRegionsExpanded ? 'Hide regions' : 'Show regions';
  natToggle.addEventListener('click', () => {
    if (state.predictHolyroodRegionsExpanded) {
      // Collapsing: national becomes source of truth — clear region entries from both maps.
      for (const map of [state.predictConstInputByRegionParty, state.predictListInputByRegionParty]) {
        for (const key of Array.from(map.keys())) {
          if (key.split('::')[0] !== HOLYROOD_NATIONAL_KEY) map.delete(key);
        }
      }
    } else {
      // Expanding: regions become source of truth — clear national entries from both maps.
      for (const map of [state.predictConstInputByRegionParty, state.predictListInputByRegionParty]) {
        for (const key of Array.from(map.keys())) {
          if (key.split('::')[0] === HOLYROOD_NATIONAL_KEY) map.delete(key);
        }
      }
    }
    state.predictHolyroodRegionsExpanded = !state.predictHolyroodRegionsExpanded;
    renderPredictGrid();
    syncPredictModeRightColumnLayout();
  });
  const natLabelWrap = document.createElement('div');
  natLabelWrap.className = 'maps-predict-region-label-wrap';
  natLabelWrap.innerHTML = '<span>National</span>';
  natLabelWrap.appendChild(natToggle);
  natLabelTd.appendChild(natLabelWrap);
  natTr.appendChild(natLabelTd);

  const natBaselines = pass === 'list' ? state.predictNationalListBaselines : state.predictNationalBaselines;
  state.predictColumnPartyKeys.forEach((pk) => {
    const natKey = predictInputKey(HOLYROOD_NATIONAL_KEY, pk);
    const currentVal = tabMap.has(natKey) ? tabMap.get(natKey) : (natBaselines.get(pk) ?? 0);
    const td = document.createElement('td');
    const input = document.createElement('input');
    input.type = 'number'; input.step = '1'; input.min = '0'; input.max = '100';
    input.className = 'maps-predict-grid-input';
    input.dataset.regionKey = HOLYROOD_NATIONAL_KEY;
    input.dataset.partyKey = pk;
    input.value = formatPredictShare(currentVal);
    if (state.predictHolyroodRegionsExpanded) {
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
        const natOtherCell = state.predictOtherCellByRegion.get(HOLYROOD_NATIONAL_KEY);
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
  state.predictOtherCellByRegion.set(HOLYROOD_NATIONAL_KEY, natOtherTd);
  natTr.appendChild(natOtherTd);
  tbody.appendChild(natTr);

  // ── Region rows ──
  if (!state.predictHolyroodRegionsExpanded) {
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

    const passBaselineMap = pass === 'list' ? state.predictBaselineListShareByRegionParty : state.predictBaselineConstShareByRegionParty;
    state.predictColumnPartyKeys.forEach((pk) => {
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
        const otherCell = state.predictOtherCellByRegion.get(region.regionKey);
        if (otherCell) {
          const total = state.predictColumnPartyKeys.reduce((sum, p) => {
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
    const displayedTotal = state.predictColumnPartyKeys.reduce((sum, p) => {
      const k = predictInputKey(region.regionKey, p);
      return sum + (tabMap.has(k) ? tabMap.get(k) : getPredictBaselineShare(region.regionKey, p, passBaselineMap));
    }, 0);
    const other = roundPredictShareValue(100 - displayedTotal);
    otherTd.textContent = formatPredictShare(other);
    otherTd.classList.toggle('maps-predict-grid-total-over', other < 0);
    state.predictOtherCellByRegion.set(region.regionKey, otherTd);
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
  const cell = state.predictOtherCellByRegion.get(regionKey);
  if (!cell) return;
  const otherShare = calculatePredictOtherShare(regionKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty);
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
      { prefix: 'c', inputMap: state.predictConstInputByRegionParty, baseline: state.predictBaselineConstShareByRegionParty },
      { prefix: 'l', inputMap: state.predictListInputByRegionParty, baseline: state.predictBaselineListShareByRegionParty },
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
      const natBaselines = prefix === 'l' ? state.predictNationalListBaselines : state.predictNationalBaselines;
      state.predictColumnPartyKeys.forEach((partyKey) => {
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
      const inputValue = roundPredictShareValue(getPredictInputShareValue(regionKey, partyKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty));
      const baselineValue = roundPredictShareValue(getPredictBaselineShare(regionKey, partyKey, state.predictBaselineShareByRegionParty));
      if (inputValue === baselineValue) return;
      serializedRows.push([regionKey, partyKey, inputValue]);
    });
  });

  if (!serializedRows.length && !state.predictEnglandExpanded) {
    return '';
  }

  return encodePredictPayload(serializedRows, state.predictEnglandExpanded, buildPredictShareStateSlots());
}

/**
 * Reads and decodes the 'predict' URL parameter, returning the decoded state object or null.
 * @returns {{englandExpanded: boolean, rows: Array<[string, string, number]>}|null} Decoded predict state, or null if the parameter is absent or malformed.
 */
function readPredictShareStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const encoded = params.get('predict');
  if (!encoded) return null;

  // Peek the englandExpanded flag from the payload before building slots so that
  // slot indices match those used during encoding (expanded vs collapsed differ).
  const parts = encoded.split('.');
  const peekExpanded = parts.length >= 2 && parts[0] === '2' && parts[1] === '1';
  const savedExpanded = state.predictEnglandExpanded;
  state.predictEnglandExpanded = peekExpanded;
  const slots = buildPredictShareStateSlots();
  state.predictEnglandExpanded = savedExpanded;

  return decodePredictPayload(encoded, slots);
}

/**
 * Applies a decoded predict share state (from URL) to state.predictInputByRegionParty, validating regions and parties before setting values.
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
      const inputMap = pass === 'l' ? state.predictListInputByRegionParty : state.predictConstInputByRegionParty;
      inputMap.set(predictInputKey(regionKey, partyKey), roundPredictShareValue(clampNumber(entry[2], 0, 100)));
    });
    return;
  }

  state.predictEnglandExpanded = Boolean(sharedState.englandExpanded);
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
  trackVirtualPageView(nextUrl);
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
 * @returns {Array<{regionKey: string, regionLabel: string, total: number}>} Array of invalid region rows with their computed totals; empty when all regions are valid.
 */
function validatePredictRowsNotOver100() {
  if (state.currentParliament === 'holyrood') {
    const invalid = [];
    // Validate both passes — either can be over 100% regardless of which tab is active.
    ['constituency', 'list'].forEach((pass) => {
      const tabMap = pass === 'list' ? state.predictListInputByRegionParty : state.predictConstInputByRegionParty;
      const passLabel = pass === 'list' ? ' (List)' : ' (Constituency)';
      const natOther = holyroodNationalOtherShare(tabMap, pass);
      if (natOther < 0) {
        invalid.push({ regionKey: HOLYROOD_NATIONAL_KEY, regionLabel: `National${passLabel}`, total: roundPredictShareValue(100 - natOther) });
      }
      currentPredictInputRows().forEach((row) => {
        if (holyroodResolvedOtherShare(row.regionKey, pass) < 0) {
          const total = roundPredictShareValue(
            state.predictColumnPartyKeys.reduce((sum, pk) => sum + resolvedHolyroodShare(row.regionKey, pk, pass), 0)
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
      total: roundPredictShareValue(calculatePredictEnteredShareTotal(row.regionKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty)),
    }))
    .filter((row) => row.total > 100);
}

/**
 * Recomputes swing maps from current input values versus baseline, removing zero-swing entries.
 * For Holyrood: builds separate const and list swing maps from the three-tab stores.
 * For Westminster: rebuilds state.predictRegionalSwingsByParty from state.predictInputByRegionParty as before.
 * @returns {void}
 */
function rebuildPredictSwingsFromInputs() {
  if (state.currentParliament === 'holyrood') {
    state.predictHolyroodConstSwingsByParty = new Map();
    state.predictHolyroodListSwingsByParty = new Map();

    const rows = currentPredictInputRows();
    rows.forEach((row) => {
      state.predictColumnPartyKeys.forEach((partyKey) => {
        // Constituency swing — measured against constituency baseline
        const constBaseline = getPredictBaselineShare(row.regionKey, partyKey, state.predictBaselineConstShareByRegionParty);
        const constShare = resolvedHolyroodShare(row.regionKey, partyKey, 'constituency');
        const constSwing = constShare - constBaseline;
        if (!state.predictHolyroodConstSwingsByParty.has(partyKey)) state.predictHolyroodConstSwingsByParty.set(partyKey, new Map());
        if (Math.abs(constSwing) >= 1e-9) state.predictHolyroodConstSwingsByParty.get(partyKey).set(row.regionKey, constSwing);

        // List swing — measured against list baseline
        const listBaseline = getPredictBaselineShare(row.regionKey, partyKey, state.predictBaselineListShareByRegionParty);
        const listShare = resolvedHolyroodShare(row.regionKey, partyKey, 'list');
        const listSwing = listShare - listBaseline;
        if (!state.predictHolyroodListSwingsByParty.has(partyKey)) state.predictHolyroodListSwingsByParty.set(partyKey, new Map());
        if (Math.abs(listSwing) >= 1e-9) state.predictHolyroodListSwingsByParty.get(partyKey).set(row.regionKey, listSwing);
      });
    });
    return;
  }

  state.predictRegionalSwingsByParty = new Map();
  const rows = currentPredictInputRows();
  rows.forEach((row) => {
    predictPartyKeysForRegion(row.regionKey).forEach((partyKey) => {
      const baseline = getPredictBaselineShare(row.regionKey, partyKey, state.predictBaselineShareByRegionParty);
      const inputShare = getPredictInputShareValue(row.regionKey, partyKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty);
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
 * For Westminster: resets state.predictInputByRegionParty and refreshes inputs in-place.
 * @returns {void}
 */
function resetPredictInputsToBaseline() {
  if (state.currentParliament === 'holyrood') {
    state.predictHolyroodTab = 'constituency';
    state.predictConstInputByRegionParty = new Map();
    state.predictListInputByRegionParty = new Map();
    state.predictHolyroodConstSwingsByParty = new Map();
    state.predictHolyroodListSwingsByParty = new Map();
    renderHolyroodPredictTabs();
    renderPredictGrid();
    return;
  }

  state.predictRegionalSwingsByParty = new Map();
  state.predictInputByRegionParty = normalizePredictShareMap(state.predictBaselineShareByRegionParty);

  document.querySelectorAll('.maps-predict-grid-input').forEach((input) => {
    const regionKey = input.dataset.regionKey;
    const partyKey = input.dataset.partyKey;
    if (!regionKey || !partyKey) {
      input.value = '0';
      return;
    }
    input.value = formatPredictShare(getPredictInputShareValue(regionKey, partyKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty));
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
  state.predictOtherCellByRegion = new Map();

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
        th.title = labelParty(partyKey);
        th.innerHTML = `<span class="maps-predict-grid-swatch" style="background:${colourParty(partyKey)}" aria-hidden="true"></span>`;
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
      toggleButton.textContent = state.predictEnglandExpanded ? 'Hide regions' : 'Show regions';
      toggleButton.addEventListener('click', () => {
        if (state.predictEnglandExpanded) {
          // Collapsing: England aggregate becomes source of truth — clear sub-region entries
          // so stale region values cannot bleed into England-aggregate swing calculations.
          for (const key of Array.from(state.predictInputByRegionParty.keys())) {
            const regionKey = key.split('::')[0];
            if (regionKey !== PREDICT_ENGLAND_KEY && isPredictEnglishRegion(regionKey)) {
              state.predictInputByRegionParty.delete(key);
            }
          }
        } else {
          // Expanding: sub-regions become source of truth — clear all England aggregate entries
          // (including parties outside state.predictColumnPartyKeys from simulation data).
          for (const key of Array.from(state.predictInputByRegionParty.keys())) {
            if (key.split('::')[0] === PREDICT_ENGLAND_KEY) state.predictInputByRegionParty.delete(key);
          }
        }
        state.predictEnglandExpanded = !state.predictEnglandExpanded;
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
      input.value = formatPredictShare(getPredictInputShareValue(region.regionKey, partyKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty));
      if (region.isEnglandAggregate && state.predictEnglandExpanded) {
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
    totalTd.textContent = formatPredictShare(calculatePredictOtherShare(region.regionKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty));
    totalTd.classList.toggle('maps-predict-grid-total-over', calculatePredictOtherShare(region.regionKey, state.predictInputByRegionParty, state.predictBaselineShareByRegionParty) < 0);
    state.predictOtherCellByRegion.set(region.regionKey, totalTd);
    tr.appendChild(totalTd);
    tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    sectionWrap.appendChild(table);
    predictGrid.appendChild(sectionWrap);
  };

  if (state.currentParliament === 'holyrood') {
    const tabMap = state.predictHolyroodTab === 'list' ? state.predictListInputByRegionParty
      : state.predictConstInputByRegionParty;
    renderHolyroodTabGrid(tabMap, state.predictHolyroodTab);
    return;
  } else {
    const northernIrelandRegions = regions.filter((region) => isPredictNorthernIrelandRegion(region.regionKey));
    const gbRegions = regions.filter((region) => !isPredictNorthernIrelandRegion(region.regionKey));

    renderPredictGridSection({
      sectionTitle: null,
      sectionClassName: 'maps-predict-grid-section-gb',
      sectionRegions: gbRegions,
      sectionPartyKeys: state.predictColumnPartyKeys,
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
 * Exits predict mode, restores the election view route, and reloads election data if any is available.
 * @returns {void}
 */
function deactivatePredictMode() {
  state.predictModeActive = false;
  setPredictModeNavState(false);
  syncPredictModeRightColumnLayout();
  replaceRouteState('election');

  if (!state.currentSeats.length && !state.currentMapData) return;
  initElectionData().catch((error) => {
    console.error(error);
  });
}

/**
 * Loads the 2024 general election as the predict baseline if not already loaded. Returns true on success, false if the election is not found in the manifest or returns no seats.
 * @returns {Promise<boolean>} True if the baseline was loaded successfully, false if the election could not be found or yielded no seats.
 */
async function ensurePredictBaselineData() {
  if (!state.currentManifest) return false;

  const parlConfig = state.parliamentFeaturesConfig[state.currentParliament] ?? {};
  const baselineId = parlConfig.predictBaselineElectionId ?? parlConfig.predictAnchorElectionId ?? '2024-general';
  const baselineElection = state.currentManifest.elections.find((entry) => entry.id === baselineId);
  if (!baselineElection) return false;

  const { mapFile, dataFile } = resolveElectionFiles(state.currentManifest, baselineElection);
  const [mapData, resultsData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`),
  ]);

  const seats = normalizeSeats(resultsData, state.manifestPartiesById, state.manifestRegionsById);
  if (!seats.length) return false;

  state.predictBaseSeats = seats.map((seat) => ({
    ...seat,
    votes: { ...(seat.votes || {}) },
  }));
  state.predictBaseSeatsByKey = buildSeatIndex(state.predictBaseSeats);
  state.predictBaseMapData = mapData;
  state.predictBaseRegionLabelsByKey = buildRegionLabelLookup(baselineElection.mapId, state.manifestRegionsByMapId);
  state.predictBaselineShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(state.predictBaseSeats));

  return true;
}

/**
 * Loads and caches per-region vote shares from the current model prediction election's data
 * file (model_uns for Westminster, holyrood_uns for Holyrood). Results are stored in
 * state.predictCurrentSimulationConstShares and state.predictCurrentSimulationListShares. Returns true on
 * success, false if the election cannot be found or yields no seats.
 * @returns {Promise<boolean>} True if simulation data was loaded successfully.
 */
async function ensurePredictCurrentSimulationData() {
  if (state.predictCurrentSimulationLoaded) return true;
  if (!state.currentManifest) return false;

  const parlConfig = state.parliamentFeaturesConfig[state.currentParliament] ?? {};
  const simulationId = parlConfig.predictAnchorElectionId
    ?? state.currentManifest.elections.find(
      (e) => e.parliament === state.currentParliament
        && (e.type === 'model_uns' || e.type === 'holyrood_uns'),
    )?.id;
  if (!simulationId) return false;

  const simulationElection = state.currentManifest.elections.find((e) => e.id === simulationId);
  if (!simulationElection) return false;

  let resultsData;
  try {
    const { dataFile } = resolveElectionFiles(state.currentManifest, simulationElection);
    resultsData = await fetchJson(`data/${dataFile}`);
  } catch {
    return false;
  }

  const seats = normalizeSeats(resultsData, state.manifestPartiesById, state.manifestRegionsById);
  if (!seats.length) return false;

  state.predictCurrentSimulationSeats = seats;

  if (state.currentParliament === 'holyrood') {
    const constSeats = seats.filter((s) => !isListSeat(s.seat));
    const seenListRegions = new Set();
    const deduplicatedListSeats = seats.filter((s) => {
      if (!isListSeat(s.seat)) return false;
      if (seenListRegions.has(s.region)) return false;
      seenListRegions.add(s.region);
      return true;
    });
    state.predictCurrentSimulationConstShares = normalizePredictShareMap(
      buildPredictBaselineShares(constSeats),
    );
    state.predictCurrentSimulationListShares = normalizePredictShareMap(
      buildPredictBaselineShares(deduplicatedListSeats),
    );
  } else {
    state.predictCurrentSimulationConstShares = normalizePredictShareMap(
      buildPredictBaselineShares(seats),
    );
  }

  state.predictCurrentSimulationLoaded = true;
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
  state.currentElectionType = state.currentParliament === 'holyrood' ? 'holyrood_uns' : 'model_uns';
  updateElectionCountdown();
  state.currentSeats = projectedSeats;
  state.currentSeatsByKey = buildSeatIndex(projectedSeats);
  state.currentComparisonSeats = state.predictBaseSeats;
  state.comparisonSeatsByKey = state.predictBaseSeatsByKey;
  state.currentMapData = state.predictBaseMapData;
  state.currentRegionLabelsByKey = state.predictBaseRegionLabelsByKey;

  window.__mapsShowVoteTotals = false;
  window.__mapsCurrentSummary = projectedSummary;
  window.__mapsComparisonSummary = baselineSummary;

  const predictLabel = `Predict ${predictElectionYear()}`;
  updateTopSummary({ name: predictLabel, parliament: state.currentParliament }, projectedSummary);
  renderMapWithViewState({ preserveZoom: true });
  syncRightPanelHeightToMap();

  if (state.currentOpenSeatName) {
    renderSeatPopup(state.currentOpenSeatName);
    state.mapInteractionController.highlightSeat(state.currentOpenSeatName);
  }
}

/**
 * Applies current regional swings to the baseline seats, updates module state, and re-renders the map and summaries.
 * @returns {void}
 */
function applyPredictModeProjection() {
  if (!state.predictModeActive) return;
  if (!state.predictBaseSeats.length || !state.predictBaseMapData) return;

  const hasHolyroodSwings =
    [...predictHolyroodConstSwingsByParty.values()].some((m) => m.size > 0) ||
    [...predictHolyroodListSwingsByParty.values()].some((m) => m.size > 0);
  const hasWestminsterSwings = state.predictRegionalSwingsByParty.size > 0;
  const projectedSeats = state.currentParliament === 'holyrood'
    ? hasHolyroodSwings
      ? projectHolyroodSeats(state.predictBaseSeats, state.predictHolyroodConstSwingsByParty, state.predictHolyroodListSwingsByParty)
      : state.predictBaseSeats.slice()
    : hasWestminsterSwings
      ? state.predictBaseSeats.map((seat) => projectedSeatForPredictMode(seat, state.predictRegionalSwingsByParty))
      : state.predictBaseSeats.slice();
  const projectedSummary = summarizeElection(projectedSeats, { mode: state.voteTotalsMode });
  const baselineSummary = summarizeElection(state.predictBaseSeats, { mode: state.voteTotalsMode });

  commitPredictProjectionState(projectedSeats, projectedSummary, baselineSummary);
}

/**
 * Switches the app into predict mode: loads baseline data if needed, applies any URL-encoded state, renders the grid, and runs the initial projection.
 * @returns {Promise<void>}
 */
async function activatePredictMode() {
  if (!state.currentSeats.length || !state.currentMapData) return;

  state.pollTrackerModeActive = false;
  setPollTrackerNavState(false);
  setPollTrackerLayoutVisible(false);

  state.predictModeActive = true;
  document.querySelectorAll('.maps-election-item.active').forEach((node) => {
    node.classList.remove('active');
  });
  setPredictModeNavState(true);
  syncPredictModeRightColumnLayout();

  if (!state.predictBaseSeats.length || !state.predictBaseMapData) {
    const loaded2024 = await ensurePredictBaselineData();
    if (!loaded2024) {
      state.predictBaseSeats = state.currentSeats.map((seat) => ({
        ...seat,
        votes: { ...(seat.votes || {}) },
      }));
      state.predictBaseSeatsByKey = buildSeatIndex(state.predictBaseSeats);
      state.predictBaseMapData = state.currentMapData;
      state.predictBaseRegionLabelsByKey = new Map(state.currentRegionLabelsByKey);
      state.predictBaselineShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(state.predictBaseSeats));
    }
  }

  state.predictRegionalSwingsByParty = new Map();
  state.predictInputByRegionParty = normalizePredictShareMap(state.predictBaselineShareByRegionParty);
  state.predictEnglandExpanded = false;
  state.predictColumnPartyKeys = collectPredictPartyKeys();

  if (state.currentParliament === 'holyrood') {
    state.predictHolyroodTab = 'constituency';
    state.predictHolyroodRegionsExpanded = false;
    state.predictConstInputByRegionParty = new Map();
    state.predictListInputByRegionParty = new Map();
    state.predictHolyroodConstSwingsByParty = new Map();
    state.predictHolyroodListSwingsByParty = new Map();
    const regionKeys = Array.from(state.predictBaseRegionLabelsByKey.keys());
    // Build separate constituency and list baseline share maps
    const constSeats = state.predictBaseSeats.filter((s) => !isListSeat(s.seat));
    const seenListRegions = new Set();
    const deduplicatedListSeats = state.predictBaseSeats.filter((s) => {
      if (!isListSeat(s.seat)) return false;
      if (seenListRegions.has(s.region)) return false;
      seenListRegions.add(s.region);
      return true;
    });
    state.predictBaselineConstShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(constSeats));
    state.predictBaselineListShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(deduplicatedListSeats));
    state.predictNationalBaselines = buildHolyroodNationalBaselines(state.predictBaselineConstShareByRegionParty, state.predictColumnPartyKeys, regionKeys);
    state.predictNationalListBaselines = buildHolyroodNationalBaselines(state.predictBaselineListShareByRegionParty, state.predictColumnPartyKeys, regionKeys);
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
 * the exact model output. For Westminster, replaces state.predictInputByRegionParty with the simulation
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
    state.predictConstInputByRegionParty = new Map(
      state.predictHolyroodRegionsExpanded ? state.predictCurrentSimulationConstShares : [],
    );
    state.predictListInputByRegionParty = new Map(
      state.predictHolyroodRegionsExpanded ? state.predictCurrentSimulationListShares : [],
    );
    if (!state.predictHolyroodRegionsExpanded) {
      // Collapsed: national is source of truth — set national entries only.
      const regionKeys = Array.from(state.predictBaseRegionLabelsByKey.keys());
      const constNationals = buildHolyroodNationalBaselines(
        state.predictCurrentSimulationConstShares, state.predictColumnPartyKeys, regionKeys,
      );
      const listNationals = buildHolyroodNationalBaselines(
        state.predictCurrentSimulationListShares, state.predictColumnPartyKeys, regionKeys,
      );
      for (const pk of state.predictColumnPartyKeys) {
        state.predictConstInputByRegionParty.set(
          predictInputKey(HOLYROOD_NATIONAL_KEY, pk),
          roundPredictShareValue(constNationals.get(pk) ?? 0),
        );
        state.predictListInputByRegionParty.set(
          predictInputKey(HOLYROOD_NATIONAL_KEY, pk),
          roundPredictShareValue(listNationals.get(pk) ?? 0),
        );
      }
    }
  } else if (state.predictEnglandExpanded) {
    // Expanded: sub-regions are source of truth. Populate sub-region entries only;
    // omit the England aggregate so the disabled row shows no stale override.
    state.predictInputByRegionParty = new Map();
    for (const [key, value] of state.predictCurrentSimulationConstShares) {
      const regionKey = key.split('::')[0];
      if (regionKey !== PREDICT_ENGLAND_KEY) {
        state.predictInputByRegionParty.set(key, value);
      }
    }
  } else {
    // Collapsed: England aggregate is source of truth. Set all entries including aggregate.
    state.predictInputByRegionParty = new Map(state.predictCurrentSimulationConstShares);
  }

  // Load the prediction seats directly rather than re-projecting from the derived
  // regional shares, so the map reflects the exact model output rather than an
  // approximation produced by the simplified UNS projection.
  const projectedSeats = state.predictCurrentSimulationSeats.map((s) => ({
    ...s,
    votes: { ...(s.votes || {}) },
  }));
  const projectedSummary = summarizeElection(projectedSeats, { mode: state.voteTotalsMode });
  const baselineSummary = summarizeElection(state.predictBaseSeats, { mode: state.voteTotalsMode });

  renderPredictGrid();
  commitPredictProjectionState(projectedSeats, projectedSummary, baselineSummary);
  replacePredictRouteStateFromInputs();
}

/**
 * Rebuilds the options on a select element from an array of { value, label } rows, preserving the current selection or falling back to fallbackValue.
 * @param {HTMLSelectElement|null} selectEl - The select element to repopulate.
 * @param {Array<{value: string, label: string}>} rows - Option rows to render.
 * @param {string} [fallbackValue='all'] - Value to select when the previously selected value is no longer available.
 * @returns {void}
 */
function setSelectOptions(selectEl, rows, fallbackValue = 'all') {
  if (!selectEl) return;
  const currentValue = selectEl.value;
  selectEl.innerHTML = '';

  rows.forEach((row) => {
    const option = document.createElement('option');
    option.value = row.value;
    option.textContent = row.label;
    selectEl.appendChild(option);
  });

  const availableValues = new Set(rows.map((row) => row.value));
  if (availableValues.has(currentValue)) {
    selectEl.value = currentValue;
    return;
  }

  if (availableValues.has(fallbackValue)) {
    selectEl.value = fallbackValue;
    return;
  }

  if (rows[0]) selectEl.value = rows[0].value;
}

/**
 * Returns { value, label } rows for all parties appearing as winners or voters in current/comparison seats, sorted by label, with 'all parties…' prepended.
 * @returns {Array<{value: string, label: string}>} Option rows for party filter and choropleth selects.
 */
function collectPartyKeysForControls() {
  const mergeKey = (key) => (key === 'other' ? 'others' : key);
  const keys = new Set(['all']);
  state.currentSeats.forEach((seat) => {
    keys.add(mergeKey(seat.winner || 'others'));
    Object.keys(seat.votes || {}).forEach((partyKey) => keys.add(mergeKey(partyKey)));
  });
  state.currentComparisonSeats.forEach((seat) => {
    keys.add(mergeKey(seat.winner || 'others'));
    Object.keys(seat.votes || {}).forEach((partyKey) => keys.add(mergeKey(partyKey)));
  });

  const sorted = Array.from(keys).filter((key) => key !== 'all')
    .sort((a, b) => labelParty(a).localeCompare(labelParty(b)));

  return [{ value: 'all', label: 'all parties...' }, ...sorted.map((key) => ({ value: key, label: labelParty(key) }))];
}

/**
 * Returns { value, label } rows for all regions present in current seats, sorted by label, with 'all regions…' prepended.
 * @returns {Array<{value: string, label: string}>} Option rows for the region filter select.
 */
function collectRegionsForControls() {
  const byKey = new Map();
  state.currentSeats.forEach((seat) => {
    const key = normalizeRegionKey(seat.region);
    if (!key) return;
    if (!byKey.has(key)) byKey.set(key, labelRegion(seat.region));
  });

  const rows = Array.from(byKey.entries())
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label));

  return [{ value: 'all', label: 'all regions...' }, ...rows];
}

/**
 * Pushes the current state.mapViewState values into the DOM filter/choropleth inputs and toggles second-party group visibility.
 * @returns {void}
 */
function syncMapControlInputsFromState() {
  if (filterPartySelect) filterPartySelect.value = state.mapViewState.filterParty;
  if (filterRegionSelect) filterRegionSelect.value = state.mapViewState.filterRegion;

  const showSecondPlaceFilter = state.mapViewState.filterParty !== 'all';
  if (filterSecondPartyGroup) filterSecondPartyGroup.hidden = !showSecondPlaceFilter;
  if (!showSecondPlaceFilter) {
    state.mapViewState.filterSecondParty = 'all';
  }
  if (filterSecondPartySelect) filterSecondPartySelect.value = state.mapViewState.filterSecondParty;

  if (filterMajorityMinInput) filterMajorityMinInput.value = String(state.mapViewState.majorityMin);
  if (filterMajorityMaxInput) filterMajorityMaxInput.value = String(state.mapViewState.majorityMax);
  if (filterGainsButton) filterGainsButton.classList.toggle('is-active', state.mapViewState.gainsOnly);

  if (choroplethTypeSelect) choroplethTypeSelect.value = state.mapViewState.choroplethType;
  if (choroplethPartySelect) choroplethPartySelect.value = state.mapViewState.choroplethParty;
}

/**
 * Reads the DOM filter/choropleth inputs into state.mapViewState, normalizing and clamping values, then syncs the inputs back.
 * @returns {void}
 */
function syncMapControlStateFromInputs() {
  if (filterPartySelect) state.mapViewState.filterParty = filterPartySelect.value || 'all';
  if (filterRegionSelect) state.mapViewState.filterRegion = filterRegionSelect.value || 'all';
  if (state.mapViewState.filterParty === 'all') {
    state.mapViewState.filterSecondParty = 'all';
  } else if (filterSecondPartySelect) {
    state.mapViewState.filterSecondParty = filterSecondPartySelect.value || 'all';
  }
  if (filterMajorityMinInput) state.mapViewState.majorityMin = clampNumber(filterMajorityMinInput.value, 0, 100);
  if (filterMajorityMaxInput) state.mapViewState.majorityMax = clampNumber(filterMajorityMaxInput.value, 0, 100);
  if (state.mapViewState.majorityMin > state.mapViewState.majorityMax) {
    const swap = state.mapViewState.majorityMin;
    state.mapViewState.majorityMin = state.mapViewState.majorityMax;
    state.mapViewState.majorityMax = swap;
  }

  if (choroplethTypeSelect) state.mapViewState.choroplethType = choroplethTypeSelect.value || 'none';
  if (choroplethPartySelect) state.mapViewState.choroplethParty = choroplethPartySelect.value || 'all';

  syncMapControlInputsFromState();
}

/**
 * Resets all primary filter state (party, region, majority range, gains toggle) to defaults and syncs the controls.
 * @returns {void}
 */
function resetPrimaryFilters() {
  state.mapViewState.filterParty = 'all';
  state.mapViewState.filterRegion = 'all';
  state.mapViewState.filterSecondParty = 'all';
  state.mapViewState.majorityMin = 0;
  state.mapViewState.majorityMax = 100;
  state.mapViewState.gainsOnly = false;
  syncMapControlInputsFromState();
}

/**
 * Resets choropleth type and party to defaults and syncs the controls.
 * @returns {void}
 */
function resetChoropleths() {
  state.mapViewState.choroplethType = 'none';
  state.mapViewState.choroplethParty = 'all';
  syncMapControlInputsFromState();
}

/**
 * Rebuilds all select options for party and region filter/choropleth controls from current seat data.
 * @returns {void}
 */
function populateMapControlOptions() {
  const partyRows = collectPartyKeysForControls();
  const regionRows = collectRegionsForControls();

  setSelectOptions(filterPartySelect, partyRows, 'all');
  setSelectOptions(filterSecondPartySelect, partyRows, 'all');
  setSelectOptions(choroplethPartySelect, partyRows, 'all');

  setSelectOptions(filterRegionSelect, regionRows, 'all');

  syncMapControlInputsFromState();
}

/**
 * Builds the choropleth rendering configuration for visible seats.
 * Returns { enabled: false } when no choropleth is selected.
 * For voteShareChange returns a diverging red-white-blue scale; for voteShare returns a white-to-party-colour scale.
 * Includes valueBySeatKey, toColour, and legend metadata.
 * @param {Set<string>} visibleSeatKeys - Set of seat lookup keys currently passing the active filters.
 * @returns {{enabled: false}|{enabled: true, valueBySeatKey: Map<string, number>, toColour: function(number): string, legendText: string, legend?: object}} Choropleth config object; enabled is false when choropleth is inactive.
 */
function buildChoroplethConfig(visibleSeatKeys) {
  if (state.currentElectionType === 'eu_referendum' && (state.mapViewState.choroplethType === 'none' || state.mapViewState.choroplethParty === 'all')) {
    const valueBySeatKey = new Map();
    const values = [];
    state.currentSeats.forEach((seat) => {
      const seatKey = seatLookupKey(seat.seat);
      if (!visibleSeatKeys.has(seatKey)) return;
      if (isPredictNorthernIrelandRegion(seat.region)) return;
      const v = voteSharePct(seat, 'leave');
      valueBySeatKey.set(seatKey, v);
      values.push(v);
    });
    const minLeave = Math.min(...values);
    const maxLeave = Math.max(...values);
    const scale = d3.scaleLinear()
      .domain([minLeave, 50, maxLeave])
      .range(['#F4A11D', '#f8fbff', '#1D3565']);
    return {
      enabled: true,
      valueBySeatKey,
      toColour: (value) => scale(value),
      legend: {
        isDelta: true,
        title: 'Leave vote share',
        startColour: '#F4A11D',
        midColour: '#f8fbff',
        endColour: '#1D3565',
        minLabel: `${formatPct(minLeave)}%`,
        midLabel: '50%',
        maxLabel: `${formatPct(maxLeave)}%`,
      },
    };
  }

  if (state.mapViewState.choroplethType === 'none' || state.mapViewState.choroplethParty === 'all') return { enabled: false };
  const isDelta = state.mapViewState.choroplethType === 'voteShareChange';

  const valueBySeatKey = new Map();
  const values = [];

  state.currentSeats.forEach((seat) => {
    const seatKey = seatLookupKey(seat.seat);
    if (!visibleSeatKeys.has(seatKey)) return;
    const comparisonSeat = state.comparisonSeatsByKey.get(seatKey) || null;
    const value = getChoroplethValue(seat, comparisonSeat, state.mapViewState.choroplethType, state.mapViewState.choroplethParty);
    if (!Number.isFinite(value)) return;
    valueBySeatKey.set(seatKey, value);
    values.push(value);
  });

  if (!values.length) return { enabled: false };

  const selectedPartyLabel = labelParty(state.mapViewState.choroplethParty);
  const selectedPartyColour = colourParty(state.mapViewState.choroplethParty);
  const legendBase = {
    party: selectedPartyLabel,
    isDelta,
  };

  if (isDelta) {
    const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 0.000001);
    const scale = d3.scaleLinear().domain([-maxAbs, 0, maxAbs]).range(['#991b1b', '#f8fbff', '#1d4ed8']);
    return {
      enabled: true,
      valueBySeatKey,
      toColour: (value) => scale(value),
      legendText: `${selectedPartyLabel} vote share change (${formatSigned(maxAbs, 2)} max abs)`,
      legend: {
        ...legendBase,
        title: `${selectedPartyLabel} vote share change`,
        startColour: '#991b1b',
        midColour: '#f8fbff',
        endColour: '#1d4ed8',
        minLabel: formatSigned(-maxAbs, 2),
        midLabel: '0',
        maxLabel: formatSigned(maxAbs, 2),
      },
    };
  }

  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  if (Math.abs(maxValue - minValue) < 1e-9) {
    return {
      enabled: true,
      valueBySeatKey,
      toColour: () => selectedPartyColour,
      legendText: `${selectedPartyLabel} vote share (uniform)`
    };
  }

  const scale = d3.scaleLinear().domain([minValue, maxValue]).range(['#f8fbff', selectedPartyColour]);
  return {
    enabled: true,
    valueBySeatKey,
    toColour: (value) => scale(value),
    legendText: `${selectedPartyLabel} vote share (${formatPct(minValue)} to ${formatPct(maxValue)})`,
    legend: {
      ...legendBase,
      title: `${selectedPartyLabel} vote share`,
      startColour: '#f8fbff',
      endColour: selectedPartyColour,
      minLabel: formatPct(minValue),
      maxLabel: formatPct(maxValue),
    },
  };
}

/**
 * Renders the choropleth colour gradient legend into the legend element, or hides it when choropleth is disabled.
 * @param {{enabled: boolean, legend?: object, legendText?: string}} choroplethConfig - Choropleth config as returned by buildChoroplethConfig.
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
 * Resets current and comparison seat state from base data, recomputes summaries, and triggers a full map + panel re-render.
 * @returns {void}
 */
function refreshElectionSeatStateAndRender() {
  if (!Array.isArray(state.baseElectionSeats) || !state.baseElectionSeats.length) return;

  state.currentSeats = state.baseElectionSeats.map((seat) => cloneSeatRecord(seat));
  state.currentSeatsByKey = buildSeatIndex(state.currentSeats);
  state.currentComparisonSeats = (state.defaultComparisonSeats || []).map((seat) => cloneSeatRecord(seat));
  state.comparisonSeatsByKey = buildSeatIndex(state.currentComparisonSeats);

  const currentElectionEntry = state.currentManifest?.elections?.find((e) => e.id === state.currentElectionId);
  const mapConfig = state.mapModesById[String(currentElectionEntry?.mapId)];
  state.voteTotalsMode = mapConfig?.voteTotalsViews?.[0]?.id ?? 'all';
  state.currentSeatView = mapConfig?.seatViews?.[0]?.id ?? 'seats';
  const summary = summarizeElection(state.currentSeats);
  const currentElection = state.currentManifest?.elections?.find((entry) => entry.id === state.currentElectionId) || null;
  if (currentElection) {
    updateTopSummary(currentElection, summary);
  }

  window.__mapsCurrentSummary = summary;
  window.__mapsComparisonSummary = state.defaultComparisonSummary;
  renderMapWithViewState();
  syncRightPanelHeightToMap();
}

/**
 * Renders the map, seat list, vote totals, and choropleth legend for the current filter/choropleth state.
 * Accepts { preserveZoom: true } to retain the current pan/zoom transform.
 * @param {{preserveZoom?: boolean}} [options={}] - Rendering options; set preserveZoom to true to keep the current d3 zoom transform.
 * @returns {void}
 */
function renderMapWithViewState(options = {}) {
  if (!state.currentMapData) return;

  const visibleSeatKeys = buildVisibleSeatKeySet(state.currentSeats, state.comparisonSeatsByKey, state.mapViewState, state.currentByElectionSeats);
  const visibleSeats = state.currentSeats.filter((seat) => visibleSeatKeys.has(seatLookupKey(seat.seat)));
  const visibleComparisonSeats = Array.from(visibleSeatKeys)
    .map((seatKey) => state.comparisonSeatsByKey.get(seatKey))
    .filter(Boolean);
  const choroplethConfig = buildChoroplethConfig(visibleSeatKeys);

  const currentElection = state.currentManifest?.elections?.find((e) => e.id === state.currentElectionId);
  const mapConfig = state.mapModesById[String(currentElection?.mapId)];

  state.hiddenVoteTotalsParties = new Set(mapConfig?.hiddenVoteTotalsParties ?? []);
  renderVoteTotalsTabs(mapConfig);
  updateVoteTotalsTabsUI();
  renderSeatViewTabs(mapConfig);
  updateSeatViewTabsUI();
  updatePostcodeSearchVisibility();

  window.__mapsVisibleSeats = visibleSeats;
  window.__mapsVisibleComparisonSeats = visibleComparisonSeats;

  const hasMultipleVoteViews = (mapConfig?.voteTotalsViews?.length ?? 0) > 1;
  const showVotes = !hasMultipleVoteViews || state.voteTotalsMode !== 'all';
  const filteredSummary = summarizeElection(visibleSeats, { mode: state.voteTotalsMode });
  const filteredComparisonSummary = state.currentComparisonSeats.length
    ? summarizeElection(visibleComparisonSeats, { mode: state.voteTotalsMode })
    : null;

  window.__mapsCurrentSummary = filteredSummary;
  window.__mapsComparisonSummary = filteredComparisonSummary;

  toggleVoteTotalColumns(showVotes);
  toggleVotePctColumns(showVotes);
  renderVoteTotals(filteredSummary, filteredComparisonSummary, {
    showVoteTotals: showVotes && window.__mapsShowVoteTotals !== false,
  });

  const preserveTransform = options.preserveZoom && mapSvg ? d3.zoomTransform(mapSvg) : null;
  const mapId = String(currentElection?.mapId ?? '');

  // Pass regionSummary (list seats) for Holyrood elections that have list seats.
  const hasRegionTable = state.currentSeats.some((s) => isListSeat(s.seat));
  const regionSummary = hasRegionTable
    ? buildRegionSummary(state.currentSeats.filter((s) => isListSeat(s.seat)))
    : null;

  renderTopoMap(state.currentMapData, state.currentSeats, {
    visibleSeatKeys,
    choroplethConfig,
    ...(preserveTransform ? { preserveTransform } : {}),
    regionSummary,
    mapId,
  });

  renderRegionTable(regionSummary);

  // Seat list: for Holyrood elections show constituency seats only (list seats appear in region table).
  const filteredSeats = hasRegionTable
    ? visibleSeats.filter((s) => !isListSeat(s.seat))
    : visibleSeats;
  renderSeatList(filteredSeats, state.currentComparisonSeats, {});

  applySeatSearchSuggestions(buildSeatSearchIndex(filteredSeats));
  renderChoroplethLegend(choroplethConfig);

  if (seatPreview) {
    let previewText;
    if (hasRegionTable) {
      const visibleConst = visibleSeats.filter((s) => !isListSeat(s.seat));
      const totalConst = state.currentSeats.filter((s) => !isListSeat(s.seat));
      previewText = `Showing ${formatInt(visibleConst.length)} of ${formatInt(totalConst.length)} constituency seats.`;
    } else {
      previewText = `Showing ${formatInt(visibleSeats.length)} of ${formatInt(state.currentSeats.length)} seats.`;
    }
    seatPreview.textContent = previewText;
  }
}

/**
 * Hides the seat detail popup and clears the tracked open seat name.
 * @returns {void}
 */
function hideSeatPopup() {
  if (!seatPopup) return;
  seatPopup.hidden = true;
  state.currentOpenSeatName = null;
}

/**
 * Renders the seat detail popup for seatName, showing majority, gain indicator, and a ranked vote share bar chart with comparison deltas.
 * @param {string} seatName - Display name of the seat to show; looked up in state.currentSeatsByKey.
 * @returns {void}
 */
function renderSeatPopup(seatName) {
  if (!seatPopup || !seatPopupTitle || !seatPopupMeta || !seatPopupList) return;

  const seatKey = seatLookupKey(seatName);
  const seat = state.currentSeatsByKey.get(seatKey);
  if (!seat) {
    hideSeatPopup();
    return;
  }
  state.currentOpenSeatName = seatName;

  const comparisonSeat = state.comparisonSeatsByKey.get(seatKey) || null;
  const gainFrom = seatGainFromPartyKey(seat, comparisonSeat);
  const turnout = totalVotesForSeat(seat);
  const majority = seatMajorityStats(seat);
  const isReferendum = state.currentElectionType === 'eu_referendum';
  const showTurnout = state.currentElectionType !== 'model_uns' && !isReferendum;
  const showRawMajority = state.currentElectionType !== 'model_uns' && !isReferendum;

  seatPopupTitle.textContent = seat.seat;
  seatPopupMeta.innerHTML = `
    ${gainFrom ? `<span class="maps-popup-meta-item">FROM ${labelParty(gainFrom)} <span class="maps-seat-icon" style="background:${colourParty(gainFrom)}"></span></span>` : ''}
    <span class="maps-popup-meta-item">${labelRegion(seat.region)}</span>
    <span class="maps-popup-meta-item">Majority: ${formatPct(majority.pct)}%${showRawMajority ? ` = ${formatInt(majority.raw)}` : ''}</span>
    ${showTurnout ? `<span class="maps-popup-meta-item">Turnout: ${formatInt(turnout)}</span>` : ''}
  `;

  const currentTurnout = totalVotesForSeat(seat);
  const comparisonTurnout = totalVotesForSeat(comparisonSeat);
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
    item.style.setProperty('--maps-popup-bar-colour', colourParty(row.party));
    item.innerHTML = `
      <div class="maps-popup-party"><span class="maps-seat-icon" style="background:${colourParty(row.party)}"></span>${escapeHtml(labelParty(row.party))}</div>
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
 * Returns a sorted copy of party rows according to state.currentSort (party name alpha, or numeric column with label tiebreak).
 * @param {Array<object>} rows - Party summary rows with a `party` key and numeric fields matching sort key names.
 * @returns {Array<object>} New sorted array of party rows.
 */
function sortPartyRows(rows) {
  const multiplier = state.currentSort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (state.currentSort.key === 'party') {
      return multiplier * labelParty(a.party).localeCompare(labelParty(b.party));
    }

    const av = Number(a[state.currentSort.key] || 0);
    const bv = Number(b[state.currentSort.key] || 0);
    if (av !== bv) return multiplier * (av - bv);
    // Tiebreak by vote share descending, then party name
    const voteDiff = Number(b.votePct || 0) - Number(a.votePct || 0);
    if (voteDiff !== 0) return voteDiff;
    return labelParty(a.party).localeCompare(labelParty(b.party));
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
 * Toggles the 'hide-vote-total-col' class on the vote totals table to show or hide raw vote count columns.
 * @param {boolean} showVoteTotals - True to show the raw vote count column, false to hide it.
 * @returns {void}
 */
function toggleVoteTotalColumns(showVoteTotals) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-vote-total-col', !showVoteTotals);
}

/**
 * Renders the vote totals summary table, showing seat counts, vote share, and comparison deltas when a comparisonSummary is provided. Truncates to top 6 rows unless expanded.
 * @param {{parties: Array<object>, totalVotes: number}} summary - Current election summary as returned by summarizeElection.
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

  const sortedRows = sortPartyRows(rows).filter((r) => !state.hiddenVoteTotalsParties.has(r.party));
  const visibleRows = state.voteTotalsExpanded ? sortedRows : sortedRows.slice(0, 7);

  if (voteTotalsToggle) {
    const canExpand = sortedRows.length > 7;
    voteTotalsToggle.hidden = !canExpand;
    if (canExpand) {
      voteTotalsToggle.textContent = state.voteTotalsExpanded ? 'Show fewer' : 'Show all';
    }
  }

  visibleRows.forEach((partyRow) => {
    const tr = document.createElement('tr');

    tr.innerHTML = `
      <td><span class="maps-party-cell"><span class="maps-party-swatch" style="background:${colourParty(partyRow.party)}"></span>${labelParty(partyRow.party)}</span></td>
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

  state.currentOpenSeatName = null;
  seatPopupTitle.textContent = `${labelRegion(regionKey)} List Vote`;

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
    item.style.setProperty('--maps-popup-bar-colour', colourParty(row.party));
    item.innerHTML = `
      <div class="maps-popup-party"><span class="maps-seat-icon" style="background:${colourParty(row.party)}"></span>${escapeHtml(labelParty(row.party))}</div>
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
      state.mapInteractionController.flashRegion(regionKey);
      renderRegionPopup(regionKey, regionSummary);
    });

    const tdName = document.createElement('td');
    tdName.className = 'maps-region-table-name';
    tdName.textContent = labelRegion(regionKey);
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
        seg.style.background = colourParty(party);
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
  state.selectedSeatRow = null;
  state.seatListRowByKey = new Map();

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
        <span class="maps-seat-icon maps-seat-owner-icon" style="background:${colourParty(winnerKey)}" title="${labelParty(winnerKey)}"></span>
        <span class="maps-seat-name">${seatName}</span>
      </span>
      <span class="maps-seat-meta">
        ${gainedFrom ? `<span class="maps-seat-gain"><span class="maps-seat-gain-label">GAIN FROM</span><span class="maps-seat-icon" style="background:${colourParty(gainedFrom)}" title="${labelParty(gainedFrom)}"></span></span>` : '<span class="maps-seat-gain-placeholder"></span>'}
      </span>
    `;

    item.addEventListener('click', () => {
      setSelectedSeatRowByKey(seatKey);

      const zoomed = state.mapInteractionController.zoomToSeat(seatName);
      if (seatPreview) {
        seatPreview.textContent = zoomed ? `Selected: ${seatName}` : `Seat not found on map: ${seatName}`;
      }
      renderSeatPopup(seatName);
    });

    state.seatListRowByKey.set(seatKey, item);
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
  const nextRow = state.seatListRowByKey.get(seatKey);
  if (!nextRow) return;

  if (state.selectedSeatRow && state.selectedSeatRow !== nextRow) {
    state.selectedSeatRow.classList.remove('is-selected');
  }
  nextRow.classList.add('is-selected');
  state.selectedSeatRow = nextRow;
}

/**
 * Builds the module-level seat name search index (state.seatSearchNames and state.currentSeatNameByKey) from the provided seats. Returns the sorted name array.
 * @param {Array<object>} seats - Array of seat objects with a `seat` name property.
 * @returns {string[]} Sorted array of seat name strings for autocomplete use.
 */
function buildSeatSearchIndex(seats) {
  state.currentSeatNameByKey = new Map();
  const names = [];

  (seats || []).forEach((seat) => {
    const seatName = String(seat?.seat || '').trim();
    if (!seatName) return;
    const key = seatLookupKey(seatName);
    if (state.currentSeatNameByKey.has(key)) return;
    state.currentSeatNameByKey.set(key, seatName);
    names.push(seatName);
  });

  names.sort((a, b) => a.localeCompare(b));
  state.seatSearchNames = names;
  return names;
}

/**
 * Creates the autocomplete dropdown menu element adjacent to the seat search input if it doesn't exist yet. Returns the element or null if the input is absent.
 * @returns {HTMLElement|null} The autocomplete menu element, or null if the seat search input is not in the DOM.
 */
function ensureSeatSearchMenu() {
  if (state.seatSearchMenuEl || !seatSearchInput) return state.seatSearchMenuEl;
  const searchGroup = seatSearchInput.closest('.maps-toolbar-group-search') || seatSearchInput.parentElement;
  if (!searchGroup) return null;

  const menu = document.createElement('div');
  menu.className = 'maps-seat-search-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'listbox');
  menu.id = 'mapsSeatSearchMenu';
  searchGroup.appendChild(menu);
  state.seatSearchMenuEl = menu;
  return state.seatSearchMenuEl;
}

/**
 * Hides the autocomplete dropdown and clears the keyboard suggestion index.
 * @returns {void}
 */
function hideSeatSearchSuggestions() {
  state.seatSearchSuggestionIndex = -1;
  if (!state.seatSearchMenuEl) return;
  state.seatSearchMenuEl.hidden = true;
  state.seatSearchMenuEl.innerHTML = '';
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

  state.seatSearchSuggestions = [...startsWithMatches, ...includesMatches].slice(0, MAX_SEAT_SEARCH_SUGGESTIONS);
  state.seatSearchSuggestionIndex = -1;
  menu.innerHTML = '';

  if (!state.seatSearchSuggestions.length) {
    menu.hidden = true;
    return;
  }

  state.seatSearchSuggestions.forEach((name, index) => {
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
 * Updates the keyboard-active (is-active) class on suggestion items to reflect state.seatSearchSuggestionIndex.
 * @returns {void}
 */
function updateSeatSearchHighlight() {
  if (!state.seatSearchMenuEl) return;
  const options = state.seatSearchMenuEl.querySelectorAll('.maps-seat-search-item');
  options.forEach((option) => {
    const index = Number(option.dataset.index);
    option.classList.toggle('is-active', index === state.seatSearchSuggestionIndex);
  });
}

/**
 * Updates the seat name list used for autocomplete suggestions and hides any open dropdown.
 * @param {string[]} seatNames - New array of seat name strings to use for autocomplete.
 * @returns {void}
 */
function applySeatSearchSuggestions(seatNames) {
  if (!seatSearchInput) return;
  state.seatSearchNames = Array.isArray(seatNames) ? [...seatNames] : [];
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
  let seatName = state.currentSeatNameByKey.get(directKey) || null;

  if (!seatName) {
    const queryLower = rawQuery.toLowerCase();
    seatName = Array.from(state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().startsWith(queryLower))
      || Array.from(state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().includes(queryLower))
      || null;
  }

  if (!seatName) {
    if (seatPreview) seatPreview.textContent = `Seat not found: ${rawQuery}`;
    return;
  }

  const seatKey = seatLookupKey(seatName);
  const zoomed = state.mapInteractionController.zoomToSeat(seatName);
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
  const manifestName = state.currentManifest?.mapModes?.[String(mapId)]?.name;
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
  const currentElection = state.currentManifest?.elections?.find((e) => e.id === state.currentElectionId);
  const mapId = currentElection?.mapId;
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
  state.postcodeErrorTimeout = window.setTimeout(() => {
    postcodeSearchInput.readOnly = false;
    postcodeSearchInput.value = '';
    postcodeSearchInput.classList.remove('is-postcode-error');
    state.postcodeErrorTimeout = null;
  }, 2000);
}

/**
 * Cancels any active postcode error flash and removes the error style.
 * Does not restore the input value — caller is responsible for that if needed.
 * @returns {void}
 */
function clearPostcodeError() {
  if (state.postcodeErrorTimeout) {
    clearTimeout(state.postcodeErrorTimeout);
    state.postcodeErrorTimeout = null;
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
    if (!state.currentSeatNameByKey.has(seatKey) && mapName === HOLYROOD_NEW_MAP_NAME) {
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

/**
 * Updates the page title and subtitle with the election name and leading-party majority (or hung-parliament message).
 * @param {object} election - Election entry object with a `name` and optionally a `type` property.
 * @param {{parties: Array<{party: string, seats: number}>, totalSeats: number}} summary - Election summary as returned by summarizeElection.
 * @returns {void}
 */
function updateTopSummary(election, summary) {
  setMapsPageTitle(election?.name, election?.parliament);
  const top = summary.parties[0];
  const leadSeats = Number(top?.seats || 0);
  const totalSeats = Number(summary.totalSeats || 0);
  const majorityThreshold = totalSeats / 2;
  const hasMajority = leadSeats > majorityThreshold;
  const majority = hasMajority ? Math.round(2 * (leadSeats - majorityThreshold)) : 0;

  if (subtitle) {
    const isHolyroodPrediction = election?.type === 'holyrood_uns';
    const isWestminsterPrediction = election?.type === 'model_uns';
    const subtitleOptions = isHolyroodPrediction
      ? { snippetOverride: state.holyroodPredictionSnippet }
      : { includeLatestPollSnippet: isWestminsterPrediction };
    if (hasMajority) {
      const baseText = `${election.name} · ${labelParty(top?.party || 'others')} majority: ${majority}`;
      setSubtitleText(baseText, subtitleOptions);
    } else {
      const baseText = `${election.name} · Hung parliament - largest party ${labelParty(top?.party || 'others')} with ${formatInt(leadSeats)} seats`;
      setSubtitleText(baseText, subtitleOptions);
    }
  }
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
 * draws region boundary overlays, and sets up state.mapInteractionController for external zoom/reset/highlight calls.
 * Accepts { visibleSeatKeys, choroplethConfig, preserveTransform } in options.
 * @param {object} mapData - TopoJSON topology object with a single named objects entry.
 * @param {Array<object>} seats - Current seat objects used to determine winner colours.
 * @param {{visibleSeatKeys?: Set<string>, choroplethConfig?: object, preserveTransform?: object}} [options={}] - Rendering options including filter visibility, choropleth config, and optional preserved zoom transform.
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
  state.activeSeatPathNode = null;

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
    if (!state.activeSeatPathNode) return;
    d3.select(state.activeSeatPathNode).classed('maps-region-path-active', false);
    state.activeSeatPathNode = null;
  };

  /**
   * Sets pathNode as the active seat path, removing the highlight from any previously active path and raising pathNode to the front.
   * @param {SVGPathElement} pathNode - The SVG path element to activate.
   * @returns {void}
   */
  const setActiveSeatPath = (pathNode) => {
    if (!pathNode) return;
    if (state.activeSeatPathNode && state.activeSeatPathNode !== pathNode) {
      d3.select(state.activeSeatPathNode).classed('maps-region-path-active', false);
    }
    state.activeSeatPathNode = pathNode;
    d3.select(pathNode).classed('maps-region-path-active', true).raise();
  };

  /** Hides the seat popup, clears the active path highlight, and animates the map back to the initial zoom transform. */
  const resetZoom = () => {
    hideSeatPopup();
    clearActiveSeatPath();
    svg.transition().duration(RESET_ZOOM_DURATION_MS).call(zoomBehavior.transform, initialTransform);
  };

  state.mapInteractionController = {
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
      if (!seatName) return colourParty('others');
      const seatKey = seatLookupKey(seatName);
      if (state.currentElectionType === 'eu_referendum' && isPredictNorthernIrelandRegion(datum.properties?.region)) {
        return '#dce4ea';
      }

      const seat = state.currentSeatsByKey.get(seatKey);
      if (!seat) return colourParty('others');

      if (visibleSeatKeys && !visibleSeatKeys.has(seatKey)) {
        return '#cbd5e1';
      }

      if (choroplethConfig.enabled && choroplethConfig.valueBySeatKey?.has(seatKey)) {
        const metricValue = choroplethConfig.valueBySeatKey.get(seatKey);
        return choroplethConfig.toColour(metricValue);
      }

      const winner = winnerBySeat.get(seatName) || winnerBySeat.get(seatLookupKey(seatName)) || 'others';
      return colourParty(winner);
    })
    .attr('stroke', (datum) => {
      if (state.currentElectionType !== 'eu_referendum') return null;
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
    state.mapInteractionController.flashRegion = (regionKey) => {
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

  svg.call(zoomBehavior.transform, options.preserveTransform || initialTransform);
}

/**
 * Clears all predict mode module-level state and hides the predict window.
 * @returns {void}
 */
function resetPredictModeState() {
  state.predictModeActive = false;
  state.predictBaseSeats = [];
  state.predictBaseSeatsByKey = new Map();
  state.predictBaseMapData = null;
  state.predictBaseRegionLabelsByKey = new Map();
  state.predictColumnPartyKeys = [];
  state.predictInputByRegionParty = new Map();
  state.predictBaselineShareByRegionParty = new Map();
  state.predictOtherCellByRegion = new Map();
  state.predictEnglandExpanded = false;
  state.predictRegionalSwingsByParty = new Map();
  // Holyrood-specific state
  state.predictHolyroodTab = 'constituency';
  state.predictHolyroodRegionsExpanded = false;
  state.predictConstInputByRegionParty = new Map();
  state.predictListInputByRegionParty = new Map();
  state.predictBaselineConstShareByRegionParty = new Map();
  state.predictBaselineListShareByRegionParty = new Map();
  state.predictHolyroodConstSwingsByParty = new Map();
  state.predictHolyroodListSwingsByParty = new Map();
  state.predictNationalBaselines = new Map();
  state.predictNationalListBaselines = new Map();
  state.predictCurrentSimulationLoaded = false;
  state.predictCurrentSimulationSeats = [];
  state.predictCurrentSimulationConstShares = new Map();
  state.predictCurrentSimulationListShares = new Map();
  if (predictWindow) predictWindow.hidden = true;
  syncPredictModeRightColumnLayout();
}

/**
 * Clears poll tracker mode state and hides the poll tracker layout.
 * @returns {void}
 */
function resetPollTrackerModeState() {
  state.pollTrackerModeActive = false;
  state.pollTrackerRangeSelection = 'all';
  setPollTrackerLayoutVisible(false);
  syncPredictModeRightColumnLayout();
}

/**
 * Bootstraps election data: fetches the manifest, resolves the active election from the URL or defaults,
 * loads map and results JSON in parallel, optionally loads comparison election data,
 * populates controls, and triggers the initial render.
 * @returns {Promise<void>}
 */
async function initElectionData() {
  const manifest = await fetchJson('data/map-modes.json');
  state.currentManifest = manifest;
  state.mapModesById = manifest.mapModes ?? {};
  state.parliamentFeaturesConfig = manifest.parliamentFeatures ?? {};
  hydrateManifestSettings(manifest);
  await Promise.all([loadPollTrackerMetaIfNeeded(), loadHolyroodPredictionMetaIfNeeded()]);
  const params = new URLSearchParams(window.location.search);
  const requestedId = params.get('election');
  const defaultParliament = manifest.elections.find((e) => e.id === manifest.defaultElection)?.parliament ?? '';
  state.currentParliament = params.get('parliament') || defaultParliament;
  updateParliamentTabsUI();

  const parliamentElections = manifest.elections.filter((e) => e.parliament === state.currentParliament);
  let currentElection = parliamentElections.find((e) => e.id === requestedId);
  if (!currentElection) {
    const parlConfig = state.parliamentFeaturesConfig[state.currentParliament] ?? {};
    const anchorId = parlConfig.predictAnchorElectionId;
    currentElection =
      (anchorId ? parliamentElections.find((e) => e.id === anchorId) : null)
      || parliamentElections.find((e) => e.id === manifest.defaultElection)
      || parliamentElections[0];
  }

  if (!currentElection) {
    throw new Error('No elections configured in data/map-modes.json');
  }

  state.currentElectionId = currentElection.id;
  state.currentByElectionSeats = currentElection.byElectionSeats?.length
    ? new Set(currentElection.byElectionSeats)
    : null;
  if (filterGainsButton) {
    filterGainsButton.textContent = state.currentByElectionSeats ? 'By-elections' : 'Gains';
    filterGainsButton.hidden = currentElection.type === 'eu_referendum';
  }

  resetPredictModeState();
  resetPollTrackerModeState();

  state.currentRegionLabelsByKey = buildRegionLabelLookup(currentElection.mapId, state.manifestRegionsByMapId);

  renderElectionLinks(manifest, currentElection.id);
  setPredictModeNavState(false);
  setPollTrackerNavState(false);

  const { mapFile, dataFile } = resolveElectionFiles(manifest, currentElection);

  const [mapData, resultsData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`),
  ]);

  const seats = normalizeSeats(resultsData, state.manifestPartiesById, state.manifestRegionsById);
  const showVoteTotals = currentElection.type !== 'model_uns' && currentElection.type !== 'eu_referendum';
  const isReferendumType = currentElection.type === 'eu_referendum';
  if (choroplethVoteShareChangeOption) choroplethVoteShareChangeOption.hidden = isReferendumType;
  if (dataInfoButton) dataInfoButton.hidden = !isReferendumType;
  if (isReferendumType && state.mapViewState.choroplethType === 'voteShareChange') {
    state.mapViewState.choroplethType = 'none';
  }
  state.currentElectionType = currentElection.type;
  updateElectionCountdown();
  state.baseElectionSeats = seats;
  state.currentSeats = seats.map((seat) => cloneSeatRecord(seat));
  state.currentMapData = mapData;
  state.currentSeatsByKey = buildSeatIndex(state.currentSeats);

  state.defaultComparisonSummary = null;
  state.defaultComparisonSeats = [];
  if (currentElection.comparisonElectionId) {
    const comparisonElection = manifest.elections.find((entry) => entry.id === currentElection.comparisonElectionId);
    if (comparisonElection) {
      const { dataFile: comparisonDataFile } = resolveElectionFiles(manifest, comparisonElection);
      const comparisonData = await fetchJson(`data/${comparisonDataFile}`);
      state.defaultComparisonSeats = normalizeSeats(comparisonData, state.manifestPartiesById, state.manifestRegionsById);
      state.defaultComparisonSummary = summarizeElection(state.defaultComparisonSeats);
    }
  }

  state.currentComparisonSeats = state.defaultComparisonSeats.map((seat) => cloneSeatRecord(seat));
  state.comparisonSeatsByKey = buildSeatIndex(state.currentComparisonSeats);

  populateMapControlOptions();
  syncMapControlStateFromInputs();

  window.__mapsShowVoteTotals = showVoteTotals;
  refreshElectionSeatStateAndRender();
}

/**
 * Entry point: wires all controls and event listeners, then loads election data and navigates to the correct initial view (election / predict / poll tracker).
 * @returns {Promise<void>}
 */
async function init() {
  wireInit();
  if (seatPopupClose) {
    seatPopupClose.addEventListener('click', () => {
      hideSeatPopup();
      state.mapInteractionController.clearSelection?.();
    });
  }
  if (voteTotalsToggle) {
    voteTotalsToggle.addEventListener('click', () => {
      state.voteTotalsExpanded = !state.voteTotalsExpanded;
      if (!window.__mapsCurrentSummary) return;
      renderVoteTotals(window.__mapsCurrentSummary, window.__mapsComparisonSummary || null, {
        showVoteTotals: window.__mapsShowVoteTotals !== false,
      });
      syncPredictModeRightColumnLayout();
    });
  }
  window.addEventListener('resize', () => {
    syncRightPanelHeightToMap();
    if (state.pollTrackerModeActive) renderPollTrackerChart();
  });

  try {
    await initElectionData();

    const params = new URLSearchParams(window.location.search);
    const routeView = String(params.get('view') || 'election').toLowerCase();

    if (routeView === 'predict') {
      await activatePredictMode();
    } else if (routeView === 'polltracker') {
      await activatePollTrackerMode();
    } else {
      replaceRouteState('election');
    }
  } catch (error) {
    setSubtitleText('Failed to load election data');
    if (seatList) {
      seatList.innerHTML = '<p>Unable to load configured election files.</p>';
    }
    console.error(error);
  }
}

init();

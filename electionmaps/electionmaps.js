import * as d3 from '../site/vendor/d3.v7.esm.js';
import { feature as topojsonFeature, mesh as topojsonMesh } from '../site/vendor/topojson-client.v3.esm.js';
import {
  PREDICT_BASE_PARTY_KEYS,
  PREDICT_NI_PARTY_KEYS,
  PREDICT_NAT_COLUMN_KEY,
  PREDICT_ENGLAND_KEY,
  formatInt,
  formatPct,
  formatSigned,
  deltaClass,
  normalizeRegionKey,
  titleCaseFromRegionKey,
  normalizePartyKey,
  seatLookupKey,
  totalVotesForSeat,
  seatMajorityStats,
  seatGainFromPartyKey,
  secondPlacePartyKey,
  voteSharePct,
  buildSeatIndex,
  summarizeElection,
  normalizeSeats,
  isPredictNorthernIrelandRegion,
  isPredictScotlandRegion,
  isPredictWalesRegion,
  isPredictEnglishRegion,
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
} from './core.js';

const mapSvg = document.querySelector('.maps-svg');
const mapContent = document.getElementById('mapContent');
const zoomValue = document.getElementById('mapsZoomValue');
const seatPreview = document.getElementById('mapsSeatPreview');
const electionList = document.getElementById('mapsElectionList');
const subtitle = document.getElementById('mapsSubtitle');
const voteTotalsBody = document.getElementById('mapsVoteTotalsBody');
const voteTotalsTable = document.getElementById('mapsVoteTotalsTable');
const voteTotalsToggle = document.getElementById('mapsVoteTotalsToggle');
const seatCard = document.getElementById('mapsSeatCard');
const seatSearchInput = document.getElementById('maps-seat-search');
const seatList = document.getElementById('mapsSeatList');
const mapsStage = document.querySelector('.maps-stage');
const mapsPanelRight = document.querySelector('.maps-panel-right');
const mapsMain = document.querySelector('.maps-main');
const seatPopup = document.getElementById('mapsSeatPopup');
const seatPopupTitle = document.getElementById('mapsSeatPopupTitle');
const seatPopupMeta = document.getElementById('mapsSeatPopupMeta');
const seatPopupList = document.getElementById('mapsSeatPopupList');
const seatPopupClose = document.getElementById('mapsSeatPopupClose');
const choroplethLegend = document.getElementById('mapsChoroplethLegend');
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
const predictSubmitButton = document.getElementById('mapsPredictSubmit');
const predictShareButton = document.getElementById('mapsPredictShare');
const predictResetAllButton = document.getElementById('mapsPredictResetAll');

const choroplethTypeSelect = document.getElementById('mapsChoroplethType');
const choroplethPartySelect = document.getElementById('mapsChoroplethParty');
const choroplethsResetButton = document.getElementById('mapsChoroplethsReset');

let currentSort = { key: 'seats', direction: 'desc' };
let currentManifest = null;
let manifestPartiesByKey = {};
let manifestPartiesById = new Map();
let manifestRegionsById = new Map();
let manifestRegionsByMapId = {};
let voteTotalsExpanded = false;
let selectedSeatRow = null;
let activeSeatPathNode = null;
let currentOpenSeatName = null;
let currentSeatsByKey = new Map();
let comparisonSeatsByKey = new Map();
let currentSeatNameByKey = new Map();
let seatListRowByKey = new Map();
let currentRegionLabelsByKey = new Map();
let currentElectionType = null;
let currentElectionId = null;
let currentSeats = [];
let currentComparisonSeats = [];
let currentMapData = null;
let baseElectionSeats = [];
let defaultComparisonSeats = [];
let defaultComparisonSummary = null;
let seatSearchNames = [];
let seatSearchSuggestions = [];
let seatSearchSuggestionIndex = -1;
let seatSearchMenuEl = null;
let predictModeActive = false;
let predictModeLinkEl = null;
let predictBaseSeats = [];
let predictBaseSeatsByKey = new Map();
let predictBaseMapData = null;
let predictBaseRegionLabelsByKey = new Map();
let predictColumnPartyKeys = [];
let predictInputByRegionParty = new Map();
let predictBaselineShareByRegionParty = new Map();
let predictRegionalSwingsByParty = new Map();
let predictEnglandExpanded = false;
let predictOtherCellByRegion = new Map();
let pollTrackerModeActive = false;
let pollTrackerModeLinkEl = null;
let pollTrackerDataLoaded = false;
let pollTrackerTimeline = [];
let pollTrackerSeriesByParty = new Map();
let pollTrackerRangeSelection = 'all';

const POLL_TRACKER_CSV_PATH = 'data/results/model_output_trends.csv';
const POLL_TRACKER_META_PATH = 'data/results/model_output_trends_meta.json';
const MAPS_PAGE_TITLE_SUFFIX = 'Election Maps | Principal Fish';

let pollTrackerMetaLoaded = false;
let pollTrackerLatestSnippet = '';
let lastTrackedVirtualPagePath = '';

/** Fires a gtag page_view event for a URL, deduplicating against the last tracked path. */
function trackVirtualPageView(nextUrl) {
  if (typeof window.gtag !== 'function') return;

  try {
    const parsed = new URL(nextUrl, window.location.origin);
    const pagePath = `${parsed.pathname}${parsed.search}`;
    if (pagePath === lastTrackedVirtualPagePath) return;

    lastTrackedVirtualPagePath = pagePath;
    window.gtag('event', 'page_view', {
      page_location: parsed.toString(),
      page_path: pagePath,
      page_title: document.title,
    });
  } catch (_error) {
  }
}

/** Sets the browser tab title, prepending contextLabel when provided. */
function setMapsPageTitle(contextLabel) {
  const label = String(contextLabel || '').trim();
  document.title = label ? `${label} | ${MAPS_PAGE_TITLE_SUFFIX}` : MAPS_PAGE_TITLE_SUFFIX;
}

/** Builds a URLSearchParams for the given view, setting/removing the election and predict params as appropriate. */
function buildRouteSearchParams(view, electionId = null) {
  const params = new URLSearchParams(window.location.search);
  params.set('view', view);
  if (view !== 'predict') params.delete('predict');

  if (view === 'polltracker') {
    params.delete('election');
    return params;
  }

  const selectedElectionId = electionId || currentElectionId;
  if (selectedElectionId) {
    params.set('election', selectedElectionId);
  }
  return params;
}

/** Replaces the current browser history entry with the URL for the given view, then fires a virtual page view. */
function replaceRouteState(view, electionId = null) {
  const params = buildRouteSearchParams(view, electionId);
  const nextUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState({}, '', nextUrl);
  trackVirtualPageView(nextUrl);
}


/** Returns an ordered array of [regionKey, partyKey] slot pairs used as the positional index for predict payload encoding. */
function buildPredictShareStateSlots() {
  const slots = [];
  const rows = collectPredictShareStateRows();

  rows.forEach((row) => {
    const regionKey = row.regionKey;
    collectPredictInputPartyKeysForRegion(regionKey).forEach((partyKey) => {
      slots.push([regionKey, partyKey]);
    });
  });

  return slots;
}

/** Encodes changed predict share values into a compact URL-safe string (base-36 slot indices). Returns '' if nothing has changed and England is not expanded. */
function encodePredictPayload(serializedRows, englandExpanded) {
  const slots = buildPredictShareStateSlots();
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

/** Decodes a predict payload string into { englandExpanded, rows: [[regionKey, partyKey, value], ...] }, or null on failure. */
function decodePredictPayload(encoded) {
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

  const slots = buildPredictShareStateSlots();
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


const mapViewState = {
  filterParty: 'all',
  filterRegion: 'all',
  filterSecondParty: 'all',
  majorityMin: 0,
  majorityMax: 100,
  gainsOnly: false,
  choroplethType: 'none',
  choroplethParty: 'all',
};
const INITIAL_MAP_SCALE = 1.2;
const ZOOM_MIN_SCALE = 1;
const ZOOM_MAX_SCALE = 10;
const LEGACY_CLICK_ZOOM_BASE = 0.05;
const CLICK_ZOOM_DURATION_MS = 1500;
const RESET_ZOOM_DURATION_MS = 500;
const MAX_SEAT_SEARCH_SUGGESTIONS = 10;
let mapInteractionController = {
  zoomBy: () => {},
  reset: () => {},
  clearSelection: () => {},
  zoomToSeat: () => false,
};

/** Converts a d3 zoom scale value to a human-readable percentage string relative to the initial map scale. */
function formatZoomPct(scaleValue) {
  const baselineScale = Math.max(1, Number(INITIAL_MAP_SCALE) || 1);
  const ratio = Number(scaleValue) / baselineScale;
  if (!Number.isFinite(ratio) || ratio <= 0) return '100%';
  return `${Math.round(ratio * 100)}%`;
}


/** Returns the display label for a party from the manifest, or the raw key if not found. */
function labelParty(partyKey) {
  const meta = manifestPartiesByKey[partyKey];
  if (meta?.name) return meta.name;
  return partyKey;
}

/** Returns the hex colour for a party from the manifest, or a grey fallback if not found. */
function colourParty(partyKey) {
  const meta = manifestPartiesByKey[partyKey];
  if (meta?.colour) return meta.colour;
  return '#9CA3AF';
}


/** Fetches a URL and passes the Response through the provided parser function. Throws on non-OK status. */
async function fetchResource(url, parser) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return parser(response);
}

/** Fetches poll tracker metadata (latest snippet text) once per page load. Silently ignores fetch errors. */
async function loadPollTrackerMetaIfNeeded() {
  if (pollTrackerMetaLoaded) return;

    try {
      const payload = await fetchJson(POLL_TRACKER_META_PATH);
      pollTrackerLatestSnippet = String(payload?.latest_poll_snippet || '').trim();
  } catch (_error) {
    pollTrackerLatestSnippet = '';
  }

  pollTrackerMetaLoaded = true;
}

/** Sets the subtitle element content, optionally appending the latest poll snippet as a secondary span. */
function setSubtitleText(baseText, options = {}) {
  if (!subtitle) return;

  const includeLatestPollSnippet = options.includeLatestPollSnippet === true;
  const latestPollSnippet = includeLatestPollSnippet ? String(pollTrackerLatestSnippet || '').trim() : '';

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

/** Fetches and parses a JSON resource from the given URL. */
async function fetchJson(url) {
  return fetchResource(url, (response) => response.json());
}

/** Fetches a plain text resource from the given URL. */
async function fetchText(url) {
  return fetchResource(url, (response) => response.text());
}


/** Toggles the 'active' CSS class on the poll tracker nav button. */
function setPollTrackerNavState(active) {
  if (!pollTrackerModeLinkEl) return;
  pollTrackerModeLinkEl.classList.toggle('active', active);
}

/** Shows or hides the poll tracker view, toggling the map stage and right panel visibility accordingly. */
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

/** Extracts a YYYY-MM-DD date string from electionName if present, otherwise returns the text or fallbackId as a string. */
function pollTrackerDateLabel(electionName, fallbackId) {
  const text = String(electionName || '').trim();
  const match = text.match(/(\d{4}-\d{2}-\d{2})/);
  if (match?.[1]) return match[1];
  return text || String(fallbackId);
}

/**
 * Parses the poll tracker CSV into { timeline, seriesByParty, partyMeta }.
 * Deduplicates rows by date, preferring the highest electionId.
 * Expands sparse date entries into a dense daily timeline when all entries are ISO dates.
 * Series values carry forward the last known value for dates with no data.
 */
function parsePollTrackerData(csvText) {
  const rows = d3.csvParse(csvText, (row) => {
    const electionId = Number(row.election_id);
    const partyId = Number(row.party_id);
    const seats = Number(row.seats_won);
    const votePct = Number(row.vote_pct);
    if (!Number.isFinite(electionId)) return null;
    if (!Number.isFinite(partyId)) return null;
    if (!Number.isFinite(seats) || !Number.isFinite(votePct)) return null;

    const electionName = String(row.election_name || '');
    const asOfDateRaw = String(row.as_of_date || '').trim();
    const unsDateMatch = electionName.match(/UNS\s+(\d{4}-\d{2}-\d{2})/);
    const manifestParty = manifestPartiesById.get(partyId);
    const normalizedPartyKey = normalizePartyKey(manifestParty?.key || manifestParty?.name || String(partyId));
    const partyName = manifestParty?.name || labelParty(normalizedPartyKey) || `Party ${partyId}`;

    return {
      electionId,
      partyId,
      partyKey: String(partyId),
      asOfDate: unsDateMatch?.[1] || asOfDateRaw,
      electionName,
      partyName,
      seats,
      votePct,
    };
  }).filter(Boolean);

  const timelineByDateKey = new Map();
  const byParty = new Map();
  const partyMeta = new Map();

  const toDateSortValue = (value, fallback) => {
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
    return String(fallback);
  };

  rows.forEach((row) => {
    const dateKey = row.asOfDate || pollTrackerDateLabel(row.electionName, row.electionId);
    const existingTimelineEntry = timelineByDateKey.get(dateKey);
    if (!existingTimelineEntry || row.electionId > existingTimelineEntry.electionId) {
      timelineByDateKey.set(dateKey, {
        dateKey,
        electionId: row.electionId,
        sortValue: toDateSortValue(dateKey, row.electionId),
        label: row.asOfDate || pollTrackerDateLabel(row.electionName, row.electionId),
      });
    }

    if (!byParty.has(row.partyKey)) byParty.set(row.partyKey, new Map());
    const byDateKey = byParty.get(row.partyKey);
    const existingPartyDateRow = byDateKey.get(dateKey);
    if (!existingPartyDateRow || row.electionId > existingPartyDateRow.electionId) {
      byDateKey.set(dateKey, row);
    }

    if (!partyMeta.has(row.partyKey)) {
      const manifestParty = Number.isFinite(row.partyId) ? manifestPartiesById.get(row.partyId) : null;
      const normalizedPartyKey = normalizePartyKey(manifestParty?.key || row.partyName);
      partyMeta.set(row.partyKey, {
        name: row.partyName,
        colour: manifestParty?.colour || colourParty(normalizedPartyKey),
      });
    }
  });

  const timeline = Array.from(timelineByDateKey.values())
    .sort((a, b) => {
      if (a.sortValue === b.sortValue) return a.electionId - b.electionId;
      return a.sortValue.localeCompare(b.sortValue);
    });

  const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const parseIsoDate = (value) => {
    const text = String(value || '').trim();
    if (!ISO_DATE_RE.test(text)) return null;
    const parsed = new Date(`${text}T00:00:00Z`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  };

  const formatIsoDate = (value) => {
    const year = value.getUTCFullYear();
    const month = String(value.getUTCMonth() + 1).padStart(2, '0');
    const day = String(value.getUTCDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  const allTimelineDates = timeline
    .map((entry) => parseIsoDate(entry.dateKey || entry.label))
    .filter(Boolean)
    .sort((a, b) => a.getTime() - b.getTime());

  const shouldExpandDailyTimeline = allTimelineDates.length === timeline.length && timeline.length > 1;
  const expandedTimeline = shouldExpandDailyTimeline
    ? (() => {
        const start = allTimelineDates[0];
        const end = allTimelineDates[allTimelineDates.length - 1];
        const entries = [];
        const current = new Date(start.getTime());
        while (current.getTime() <= end.getTime()) {
          const iso = formatIsoDate(current);
          const existing = timelineByDateKey.get(iso);
          entries.push({
            dateKey: iso,
            electionId: existing?.electionId || 0,
            sortValue: iso,
            label: iso,
            dateValue: new Date(current.getTime()),
          });
          current.setUTCDate(current.getUTCDate() + 1);
        }
        return entries;
      })()
    : timeline.map((entry) => ({
        ...entry,
        dateValue: parseIsoDate(entry.dateKey || entry.label),
      }));

  const seriesByParty = new Map();
  byParty.forEach((rowsByDateKey, partyKey) => {
    const seats = [];
    const votePct = [];
    let lastSeats = null;
    let lastVotePct = null;
    expandedTimeline.forEach((entry) => {
      const row = rowsByDateKey.get(entry.dateKey);
      if (row) {
        lastSeats = Number(row.seats || 0);
        lastVotePct = Number(row.votePct || 0);
      }
      seats.push(lastSeats);
      votePct.push(lastVotePct);
    });

    seriesByParty.set(partyKey, {
      partyKey,
      partyName: partyMeta.get(partyKey)?.name || partyKey,
      colour: partyMeta.get(partyKey)?.colour || '#9CA3AF',
      seats,
      votePct,
      latestSeats: Number(seats[seats.length - 1] || 0),
    });
  });

  return { timeline: expandedTimeline, seriesByParty, partyMeta };
}

/** Returns the party key values of all checked party toggle checkboxes in the poll tracker controls. */
function getPollTrackerSelectedParties() {
  return Array.from(document.querySelectorAll('.maps-polltracker-party-toggle input[type="checkbox"]'))
    .filter((input) => input.checked)
    .map((input) => input.value);
}

/**
 * Renders the poll tracker D3 SVG chart into pollTrackerChartWrap.
 * Draws line series for selected parties with separate left (seats) and right (vote %) axes.
 * Includes a crosshair tooltip and respects the current date-range selection.
 */
function renderPollTrackerChart() {
  if (!pollTrackerChartWrap) return;

    const selectedParties = getPollTrackerSelectedParties();
    const seatsEnabled = Boolean(pollTrackerMetricSeatsInput?.checked);
    const votePctEnabled = Boolean(pollTrackerMetricVotesInput?.checked);

  pollTrackerChartWrap.innerHTML = '';
  pollTrackerChartWrap.style.position = 'relative';

  if (!pollTrackerTimeline.length) {
    pollTrackerChartWrap.innerHTML = '<div class="maps-polltracker-empty">No poll tracker data available.</div>';
    return;
  }

  if (!selectedParties.length || !(seatsEnabled || votePctEnabled)) {
    pollTrackerChartWrap.innerHTML = '<div class="maps-polltracker-empty">Select at least one party and one metric (Seats/Vote %).</div>';
    return;
  }

  const rangeSize = pollTrackerRangeSelection === 'all'
    ? pollTrackerTimeline.length
    : Number(pollTrackerRangeSelection);
  const windowSize = Number.isFinite(rangeSize) && rangeSize > 0
    ? Math.min(rangeSize, pollTrackerTimeline.length)
    : pollTrackerTimeline.length;
  const windowStart = Math.max(0, pollTrackerTimeline.length - windowSize);
  const visibleTimeline = pollTrackerTimeline.slice(windowStart);

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
    .map((partyKey) => pollTrackerSeriesByParty.get(partyKey))
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
    .x((value, index) => (useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index)))
    .y((value) => ySeats(value));

  const votePctLine = d3.line()
    .defined((value) => Number.isFinite(value))
    .x((value, index) => (useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index)))
    .y((value) => yVotePct(value));

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
      <div class="maps-polltracker-tooltip-party"><span class="maps-predict-grid-swatch" style="background:${partyColour}"></span>${series.partyName}</div>
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

/** Renders the party toggle checkboxes for the poll tracker, pre-selecting the top parties by latest seats (excluding Others, always including Green). */
function renderPollTrackerPartyControls() {
  if (!pollTrackerPartyControls) return;

    const partyRows = Array.from(pollTrackerSeriesByParty.values())
      .sort((a, b) => b.latestSeats - a.latestSeats || a.partyName.localeCompare(b.partyName));

  const normalizePartyName = (name) => String(name || '').trim().toLowerCase();
  const isOtherParty = (name) => /^others?$/.test(normalizePartyName(name));
  const isGreenParty = (name) => normalizePartyName(name) === 'green';

  const defaultSelectedPartyKeys = partyRows
    .filter((row) => !isOtherParty(row.partyName))
    .slice(0, 6)
    .map((row) => row.partyKey);

  const greenRow = partyRows.find((row) => isGreenParty(row.partyName));
  if (greenRow && !defaultSelectedPartyKeys.includes(greenRow.partyKey)) {
    const removableIndex = defaultSelectedPartyKeys.findIndex((key) => key !== greenRow.partyKey);
    if (removableIndex >= 0) {
      defaultSelectedPartyKeys.splice(removableIndex, 1);
    } else if (defaultSelectedPartyKeys.length >= 6) {
      defaultSelectedPartyKeys.pop();
    }

    if (defaultSelectedPartyKeys.length < 6) {
      defaultSelectedPartyKeys.push(greenRow.partyKey);
    }
  }

  const defaultSelectedPartySet = new Set(defaultSelectedPartyKeys);

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

/** Fetches and parses the poll tracker CSV once per page load, populating pollTrackerTimeline and pollTrackerSeriesByParty. */
async function loadPollTrackerDataIfNeeded() {
  if (pollTrackerDataLoaded) return;

  const csvText = await fetchText(POLL_TRACKER_CSV_PATH);
  const parsed = parsePollTrackerData(csvText);

  pollTrackerTimeline = parsed.timeline;
  pollTrackerSeriesByParty = parsed.seriesByParty;
  pollTrackerDataLoaded = true;
}

/** Switches the app into poll tracker mode: deactivates predict mode, loads data, renders controls and chart, and updates the route. */
async function activatePollTrackerMode() {
  predictModeActive = false;
  setPredictModeNavState(false);
  if (predictWindow) predictWindow.hidden = true;

  pollTrackerModeActive = true;
  document.querySelectorAll('.maps-election-item.active').forEach((node) => {
    node.classList.remove('active');
  });
  setPollTrackerNavState(true);

  setPollTrackerLayoutVisible(true);
  await loadPollTrackerMetaIfNeeded();
  setMapsPageTitle('Poll tracker');
  setSubtitleText('Poll tracker · model output trends', { includeLatestPollSnippet: true });
  if (seatPreview) seatPreview.textContent = 'Poll tracker mode active.';
  replaceRouteState('polltracker');

  await loadPollTrackerDataIfNeeded();
  renderPollTrackerPartyControls();
  renderPollTrackerChart();
}


/** Returns a deep-ish copy of a seat object with normalized party keys and only positive vote totals retained. */
function cloneSeatRecord(seat) {
  const votes = {};
  Object.entries(seat?.votes || {}).forEach(([partyKey, value]) => {
    const voteTotal = Number(value || 0);
    if (voteTotal <= 0) return;
    votes[normalizePartyKey(partyKey)] = voteTotal;
  });

  return {
    seat: seat?.seat || 'Unknown seat',
    region: seat?.region || 'unknown',
    winner: normalizePartyKey(seat?.winner || 'others'),
    electorate: Number(seat?.electorate || 0),
    turnout: Number(seat?.turnout || 0),
    votes,
  };
}


/** Rebuilds the election list nav, inserting Predict 2029 and Poll tracker buttons after the current-prediction entry (or near the top as a fallback). Stores references to predictModeLinkEl and pollTrackerModeLinkEl. */
function renderElectionLinks(manifest, activeId) {
  if (!electionList) return;

  const createPredictButton = () => {
    const predictButton = document.createElement('button');
    predictButton.type = 'button';
    predictButton.className = 'maps-election-item';
    predictButton.textContent = 'Predict 2029';
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

  electionList.innerHTML = '';
  predictModeLinkEl = null;
  pollTrackerModeLinkEl = null;
  let insertedPredictLink = false;
  let insertedPollTrackerLink = false;
  manifest.elections.forEach((election) => {
    const link = document.createElement('a');
    link.href = `?view=election&election=${encodeURIComponent(election.id)}`;
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (!insertedPredictLink && election.id === 'current-prediction') {
      const predictButton = createPredictButton();
      electionList.appendChild(predictButton);
      predictModeLinkEl = predictButton;
      insertedPredictLink = true;

      const trackerButton = createPollTrackerButton();
      electionList.appendChild(trackerButton);
      pollTrackerModeLinkEl = trackerButton;
      insertedPollTrackerLink = true;
    }
  });

  if (!insertedPredictLink) {
    const predictButton = createPredictButton();
    if (electionList.children.length > 0) {
      electionList.insertBefore(predictButton, electionList.children[1] || null);
    } else {
      electionList.appendChild(predictButton);
    }
    predictModeLinkEl = predictButton;

    if (!insertedPollTrackerLink) {
      const trackerButton = createPollTrackerButton();
      const predictIndex = Array.from(electionList.children).indexOf(predictButton);
      if (predictIndex >= 0 && electionList.children[predictIndex + 1]) {
        electionList.insertBefore(trackerButton, electionList.children[predictIndex + 1]);
      } else {
        electionList.appendChild(trackerButton);
      }
      pollTrackerModeLinkEl = trackerButton;
      insertedPollTrackerLink = true;
    }
  }

  if (!insertedPollTrackerLink) {
    const trackerButton = createPollTrackerButton();
    if (predictModeLinkEl && predictModeLinkEl.nextSibling) {
      electionList.insertBefore(trackerButton, predictModeLinkEl.nextSibling);
    } else {
      electionList.appendChild(trackerButton);
    }
    pollTrackerModeLinkEl = trackerButton;
  }
}

/** Resolves the mapFile and dataFile paths for an election by checking manifest settings overrides then election-level properties. Throws if either is missing. */
function resolveElectionFiles(manifest, election) {
  const settings = manifest?.settings || {};
  const mapFilesById = settings.mapFilesById || {};
  const dataFilesByElectionId = settings.dataFilesByElectionId || {};

  const mapFileFromSettings = election?.mapId != null ? mapFilesById[String(election.mapId)] : undefined;
  const dataFileFromSettings = dataFilesByElectionId[election.id];

  const mapFile = mapFileFromSettings || election.mapFile;
  const dataFile = dataFileFromSettings || election.dataFile;

  if (!mapFile || !dataFile) {
    throw new Error(`Missing file configuration for election ${election?.id || 'unknown'}`);
  }

  return { mapFile, dataFile };
}

/** Reads manifest.settings and populates the module-level party lookup maps (manifestPartiesByKey, manifestPartiesById), manifestRegionsById, and manifestRegionsByMapId. */
function hydrateManifestSettings(manifest) {
  const settings = manifest?.settings || {};

  // Build partiesByKey and partiesById from the canonical parties array.
  manifestPartiesByKey = {};
  manifestPartiesById = new Map();
  const partyRows = Array.isArray(settings.parties) ? settings.parties : [];
  partyRows.forEach((party) => {
    const id = Number(party?.id);
    if (!Number.isFinite(id)) return;
    manifestPartiesById.set(id, party);
    const key = party?.key;
    if (key && !manifestPartiesByKey[key]) manifestPartiesByKey[key] = party;
  });

  // Build integer region ID → normalized region key lookup across all maps.
  manifestRegionsById = new Map();
  manifestRegionsByMapId = settings.regionsByMapId || {};
  Object.values(manifestRegionsByMapId).forEach((regionRows) => {
    (regionRows || []).forEach((region) => {
      const id = Number(region?.id);
      if (!Number.isFinite(id)) return;
      manifestRegionsById.set(id, normalizeRegionKey(region?.name || ''));
    });
  });
}


/** Returns a Map from normalized region key to display label for the given mapId, built from manifest region metadata. */
function buildRegionLabelLookup(mapId) {
  const lookup = new Map();
  const regionRows = manifestRegionsByMapId?.[String(mapId)] || [];
  regionRows.forEach((region) => {
    const key = normalizeRegionKey(region?.name || '');
    if (!key) return;
    lookup.set(key, region.name);
  });
  return lookup;
}


/** Returns the display label for a region, falling back to titleCaseFromRegionKey if not in the current region lookup. */
function labelRegion(regionKey) {
  const normalized = normalizeRegionKey(regionKey);
  if (!normalized) return 'Unknown';
  return currentRegionLabelsByKey.get(normalized) || titleCaseFromRegionKey(regionKey);
}

/** Updates currentSort: toggles direction if the same key is re-selected, otherwise switches to the new key with a default direction. */
function setSortDirection(sortKey) {
  if (currentSort.key === sortKey) {
    currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    return;
  }
  currentSort.key = sortKey;
  currentSort.direction = sortKey === 'party' ? 'asc' : 'desc';
}


/** Lazily initializes and returns the per-region swing Map for a party in predictRegionalSwingsByParty. */
function ensurePredictPartySwingMap(partyKey) {
  if (!predictRegionalSwingsByParty.has(partyKey)) {
    predictRegionalSwingsByParty.set(partyKey, new Map());
  }
  return predictRegionalSwingsByParty.get(partyKey);
}


/** Toggles the 'active' CSS class on the predict mode nav button. */
function setPredictModeNavState(active) {
  if (!predictModeLinkEl) return;
  predictModeLinkEl.classList.toggle('active', active);
}

/** Syncs predict window and seat card visibility based on predict mode state, vote totals expansion, and England expansion. */
function syncPredictModeRightColumnLayout() {
  if (!predictWindow || !seatCard) return;

  const predictVisible = predictModeActive && !pollTrackerModeActive;
  predictWindow.hidden = !predictVisible;
  predictWindow.style.display = predictVisible ? '' : 'none';

  const hideSeatCard = predictVisible && (voteTotalsExpanded || predictEnglandExpanded);
  const forcePredictGridScroll = predictVisible && voteTotalsExpanded && predictEnglandExpanded;
  seatCard.hidden = hideSeatCard;
  seatCard.style.display = hideSeatCard ? 'none' : '';

  predictWindow.classList.toggle('maps-predict-window-fill', hideSeatCard);
  predictWindow.classList.toggle('maps-predict-window-compact', predictVisible && !hideSeatCard);
  predictWindow.classList.toggle('maps-predict-window-force-scroll', forcePredictGridScroll);
}


/** Returns the GB predict grid column party keys (base parties + 'nat' column). */
function collectPredictPartyKeys() {
  return [...PREDICT_BASE_PARTY_KEYS, PREDICT_NAT_COLUMN_KEY];
}

/** Returns the NI predict grid column party keys. */
function collectPredictNorthernIrelandPartyKeys() {
  return [...PREDICT_NI_PARTY_KEYS];
}


/** Returns all predict regions as { regionKey, regionLabel } sorted alphabetically by label. */
function collectPredictAllRegions() {
  return Array.from(predictBaseRegionLabelsByKey.entries())
    .map(([regionKey, regionLabel]) => ({ regionKey, regionLabel }))
    .sort((a, b) => a.regionLabel.localeCompare(b.regionLabel));
}


/**
 * Returns the ordered list of row descriptors for the predict grid:
 * England aggregate (always first), optionally followed by English sub-regions if expanded,
 * then Scotland, Wales, and Northern Ireland.
 */
function collectPredictInputRows() {
  const allRegions = collectPredictAllRegions();
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

  if (predictEnglandExpanded) {
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


/** Clamps inputValue to [0, 100], stores it in predictInputByRegionParty, and returns the clamped integer value. */
function setPredictInputShareValue(regionKey, partyKey, inputValue) {
  const shareValue = roundPredictShareValue(clampNumber(inputValue, 0, 100));
  predictInputByRegionParty.set(predictInputKey(regionKey, partyKey), shareValue);
  return shareValue;
}

/** Returns the rounded baseline vote share for a region/party from the historical election data. */
function getPredictBaselineShare(regionKey, partyKey) {
  return roundPredictShareValue(
    Number(predictBaselineShareByRegionParty.get(predictInputKey(regionKey, partyKey)) || 0)
  );
}

/** Returns the current user-entered predict share for a region/party, falling back to the baseline if not yet entered. */
function getPredictInputShareValue(regionKey, partyKey) {
  const cached = predictInputByRegionParty.get(predictInputKey(regionKey, partyKey));
  if (Number.isFinite(cached)) return Number(cached);
  return roundPredictShareValue(getPredictBaselineShare(regionKey, partyKey));
}

/** Returns the sum of all predict input shares for a region (across its modelled parties). */
function calculatePredictEnteredShareTotal(regionKey) {
  return collectPredictInputPartyKeysForRegion(regionKey).reduce((sum, partyKey) => {
    return sum + Number(getPredictInputShareValue(regionKey, partyKey) || 0);
  }, 0);
}

/** Returns the implied 'other' share for a region (100 minus the entered parties total). Can be negative if inputs exceed 100%. */
function calculatePredictOtherShare(regionKey) {
  return roundPredictShareValue(100 - calculatePredictEnteredShareTotal(regionKey));
}

/** Updates the 'other' display cell for a region in the predict grid, marking it red when the total exceeds 100%. */
function updatePredictOtherCell(regionKey) {
  const cell = predictOtherCellByRegion.get(regionKey);
  if (!cell) return;
  const otherShare = calculatePredictOtherShare(regionKey);
  cell.textContent = formatPredictShare(otherShare);
  cell.classList.toggle('maps-predict-grid-total-over', otherShare < 0);
}

/** Returns all validation rows: England aggregate plus every English, Scottish, Welsh, and NI region. Used to check that no region exceeds 100%. */
function collectPredictValidationRows() {
  const allRegions = collectPredictAllRegions();
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

/** Returns the rows used for URL state serialization (same set as validation rows). */
function collectPredictShareStateRows() {
  return collectPredictValidationRows();
}

/** Serializes only the region/party share values that differ from baseline, plus the England expansion flag, into the v2 encoded payload string. Returns '' if nothing has changed. */
function buildPredictShareStatePayload() {
  const rows = collectPredictShareStateRows();
  const serializedRows = [];

  rows.forEach((row) => {
    const regionKey = row.regionKey;
    collectPredictInputPartyKeysForRegion(regionKey).forEach((partyKey) => {
      const inputValue = roundPredictShareValue(getPredictInputShareValue(regionKey, partyKey));
      const baselineValue = roundPredictShareValue(getPredictBaselineShare(regionKey, partyKey));
      if (inputValue === baselineValue) return;
      serializedRows.push([regionKey, partyKey, inputValue]);
    });
  });

  if (!serializedRows.length && !predictEnglandExpanded) {
    return '';
  }

  return encodePredictPayload(serializedRows, predictEnglandExpanded);
}

/** Reads and decodes the 'predict' URL parameter, returning the decoded state object or null. */
function readPredictShareStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const encoded = params.get('predict');
  if (!encoded) return null;

  return decodePredictPayload(encoded);
}

/** Applies a decoded predict share state (from URL) to predictInputByRegionParty, validating regions and parties before setting values. */
function applyPredictShareStateFromUrl(sharedState) {
  if (!sharedState) return;
  predictEnglandExpanded = Boolean(sharedState.englandExpanded);

  const validRows = new Set(collectPredictShareStateRows().map((row) => row.regionKey));

  (sharedState.rows || []).forEach((entry) => {
    if (!Array.isArray(entry) || entry.length < 3) return;

    const regionKey = String(entry[0] || '');
    const partyKey = String(entry[1] || '');
    if (!validRows.has(regionKey)) return;

    const validParties = new Set(collectPredictInputPartyKeysForRegion(regionKey));
    if (!validParties.has(partyKey)) return;

    setPredictInputShareValue(regionKey, partyKey, entry[2]);
  });
}

/** Builds the full shareable URL for the current predict scenario, including the encoded payload. */
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

/** Updates the browser history entry with the current predict state URL without reloading the page. */
function replacePredictRouteStateFromInputs() {
  const nextUrl = buildPredictShareUrl();
  window.history.replaceState({}, '', nextUrl);
  trackVirtualPageView(nextUrl);
}

/** Shares the current predict URL via the Web Share API, falls back to clipboard copy, then to a prompt dialog. */
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

/** Returns an array of region rows where the entered party shares exceed 100% in total. Empty array means all rows are valid. */
function validatePredictRowsNotOver100() {
  const invalidRows = collectPredictValidationRows()
    .map((row) => ({
      ...row,
      total: roundPredictShareValue(calculatePredictEnteredShareTotal(row.regionKey)),
    }))
    .filter((row) => row.total > 100);

  return invalidRows;
}


/** Recomputes predictRegionalSwingsByParty from current input values versus baseline, removing zero-swing entries. */
function rebuildPredictSwingsFromInputs() {
  predictRegionalSwingsByParty = new Map();

  const rows = collectPredictInputRows();
  rows.forEach((row) => {
    collectPredictInputPartyKeysForRegion(row.regionKey).forEach((partyKey) => {
      const baseline = getPredictBaselineShare(row.regionKey, partyKey);
      const inputShare = getPredictInputShareValue(row.regionKey, partyKey);
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

/** Resets all predict inputs to baseline values and refreshes grid input elements. */
function resetPredictInputsToBaseline() {
  predictRegionalSwingsByParty = new Map();
  predictInputByRegionParty = normalizePredictShareMap(predictBaselineShareByRegionParty);

  document.querySelectorAll('.maps-predict-grid-input').forEach((input) => {
    const regionKey = input.dataset.regionKey;
    const partyKey = input.dataset.partyKey;
    if (!regionKey || !partyKey) {
      input.value = '0';
      return;
    }
    input.value = formatPredictShare(getPredictInputShareValue(regionKey, partyKey));
    updatePredictOtherCell(regionKey);
  });
}

/**
 * Fully rebuilds the predict input grid DOM.
 * Renders a GB section (England aggregate + optional sub-regions, Scotland, Wales) and a NI section.
 * Each row contains numeric inputs per party column plus a live-updating 'other' total cell.
 */
function renderPredictGrid() {
  if (!predictGrid) return;
  const regions = collectPredictInputRows();

  predictGrid.innerHTML = '';
  predictOtherCellByRegion = new Map();

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
      toggleButton.textContent = predictEnglandExpanded ? 'Hide regions' : 'Show regions';
      toggleButton.addEventListener('click', () => {
        predictEnglandExpanded = !predictEnglandExpanded;
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
      input.value = formatPredictShare(getPredictInputShareValue(region.regionKey, partyKey));
      input.addEventListener('change', () => {
        const nextValue = setPredictInputShareValue(region.regionKey, partyKey, input.value);
        input.value = formatPredictShare(nextValue);
        updatePredictOtherCell(region.regionKey);
      });
      td.appendChild(input);
      tr.appendChild(td);
    });

    const totalTd = document.createElement('td');
    totalTd.className = 'maps-predict-grid-total';
    totalTd.textContent = formatPredictShare(calculatePredictOtherShare(region.regionKey));
    totalTd.classList.toggle('maps-predict-grid-total-over', calculatePredictOtherShare(region.regionKey) < 0);
    predictOtherCellByRegion.set(region.regionKey, totalTd);
    tr.appendChild(totalTd);
    tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    sectionWrap.appendChild(table);
    predictGrid.appendChild(sectionWrap);
  };

  const northernIrelandRegions = regions.filter((region) => isPredictNorthernIrelandRegion(region.regionKey));
  const gbRegions = regions.filter((region) => !isPredictNorthernIrelandRegion(region.regionKey));

  renderPredictGridSection({
    sectionTitle: null,
    sectionClassName: 'maps-predict-grid-section-gb',
    sectionRegions: gbRegions,
    sectionPartyKeys: predictColumnPartyKeys,
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

/** Exits predict mode, restores the election view route, and reloads election data if any is available. */
function deactivatePredictMode() {
  predictModeActive = false;
  setPredictModeNavState(false);
  syncPredictModeRightColumnLayout();
  replaceRouteState('election');

  if (!currentSeats.length && !currentMapData) return;
  initElectionData().catch((error) => {
    console.error(error);
  });
}

/** Loads the 2024 general election as the predict baseline if not already loaded. Returns true on success, false if the election is not found in the manifest or returns no seats. */
async function ensurePredictBaselineData() {
  if (!currentManifest) return false;

  const baselineElection = currentManifest.elections.find((entry) => entry.id === '2024-general');
  if (!baselineElection) return false;

  const { mapFile, dataFile } = resolveElectionFiles(currentManifest, baselineElection);
  const [mapData, resultsData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`),
  ]);

  const seats = normalizeSeats(resultsData, manifestPartiesById, manifestRegionsById);
  if (!seats.length) return false;

  predictBaseSeats = seats.map((seat) => ({
    ...seat,
    votes: { ...(seat.votes || {}) },
  }));
  predictBaseSeatsByKey = buildSeatIndex(predictBaseSeats);
  predictBaseMapData = mapData;
  predictBaseRegionLabelsByKey = buildRegionLabelLookup(baselineElection.mapId);
  predictBaselineShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(predictBaseSeats));

  return true;
}

/** Applies current regional swings to the baseline seats, updates module state, and re-renders the map and summaries. */
function applyPredictModeProjection() {
  if (!predictModeActive) return;
  if (!predictBaseSeats.length || !predictBaseMapData) return;

  const projectedSeats = predictBaseSeats.map((seat) => projectedSeatForPredictMode(seat, predictRegionalSwingsByParty));
  const projectedSummary = summarizeElection(projectedSeats);
  const baselineSummary = summarizeElection(predictBaseSeats);

  currentElectionType = 'model_uns';
  currentSeats = projectedSeats;
  currentSeatsByKey = buildSeatIndex(projectedSeats);
  currentComparisonSeats = predictBaseSeats;
  comparisonSeatsByKey = predictBaseSeatsByKey;
  currentMapData = predictBaseMapData;
  currentRegionLabelsByKey = predictBaseRegionLabelsByKey;

  window.__mapsShowVoteTotals = false;
  window.__mapsCurrentSummary = projectedSummary;
  window.__mapsComparisonSummary = baselineSummary;

  updateTopSummary({ name: 'Predict 2029' }, projectedSummary);
  renderMapWithViewState({ preserveZoom: true });
  syncRightPanelHeightToMap();

  if (currentOpenSeatName) {
    renderSeatPopup(currentOpenSeatName);
    mapInteractionController.highlightSeat(currentOpenSeatName);
  }
}


/** Switches the app into predict mode: loads baseline data if needed, applies any URL-encoded state, renders the grid, and runs the initial projection. */
async function activatePredictMode() {
  if (!currentSeats.length || !currentMapData) return;

  pollTrackerModeActive = false;
  setPollTrackerNavState(false);
  setPollTrackerLayoutVisible(false);

  predictModeActive = true;
  document.querySelectorAll('.maps-election-item.active').forEach((node) => {
    node.classList.remove('active');
  });
  setPredictModeNavState(true);
  syncPredictModeRightColumnLayout();

  if (!predictBaseSeats.length || !predictBaseMapData) {
    const loaded2024 = await ensurePredictBaselineData();
    if (!loaded2024) {
      predictBaseSeats = currentSeats.map((seat) => ({
        ...seat,
        votes: { ...(seat.votes || {}) },
      }));
      predictBaseSeatsByKey = buildSeatIndex(predictBaseSeats);
      predictBaseMapData = currentMapData;
      predictBaseRegionLabelsByKey = new Map(currentRegionLabelsByKey);
      predictBaselineShareByRegionParty = normalizePredictShareMap(buildPredictBaselineShares(predictBaseSeats));
    }
  }

  predictRegionalSwingsByParty = new Map();
  predictInputByRegionParty = normalizePredictShareMap(predictBaselineShareByRegionParty);
  predictEnglandExpanded = false;
  predictColumnPartyKeys = collectPredictPartyKeys();
  applyPredictShareStateFromUrl(readPredictShareStateFromUrl());
  renderPredictGrid();

  if (seatPreview) {
    seatPreview.textContent = 'Predict mode active: edit regional vote shares and click Submit.';
  }

  setMapsPageTitle('Predict 2029');
  replacePredictRouteStateFromInputs();

  rebuildPredictSwingsFromInputs();
  applyPredictModeProjection();
}

/** Attaches click handlers to the predict submit, share, reset, and close buttons. Guards against double-wiring with a dataset flag. */
function wirePredictControls() {
  if (!predictWindow || predictWindow.dataset.wired === 'true') return;

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

  predictWindow.dataset.wired = 'true';
}

/** Rebuilds the options on a select element from an array of { value, label } rows, preserving the current selection or falling back to fallbackValue. */
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

/** Returns { value, label } rows for all parties appearing as winners or voters in current/comparison seats, sorted by label, with 'all parties…' prepended. */
function collectPartyKeysForControls() {
  const keys = new Set(['all']);
  currentSeats.forEach((seat) => {
    keys.add(seat.winner || 'others');
    Object.keys(seat.votes || {}).forEach((partyKey) => keys.add(partyKey));
  });
  currentComparisonSeats.forEach((seat) => {
    keys.add(seat.winner || 'others');
    Object.keys(seat.votes || {}).forEach((partyKey) => keys.add(partyKey));
  });

  const sorted = Array.from(keys).filter((key) => key !== 'all')
    .sort((a, b) => labelParty(a).localeCompare(labelParty(b)));

  return [{ value: 'all', label: 'all parties...' }, ...sorted.map((key) => ({ value: key, label: labelParty(key) }))];
}

/** Returns { value, label } rows for all regions present in current seats, sorted by label, with 'all regions…' prepended. */
function collectRegionsForControls() {
  const byKey = new Map();
  currentSeats.forEach((seat) => {
    const key = normalizeRegionKey(seat.region);
    if (!key) return;
    if (!byKey.has(key)) byKey.set(key, labelRegion(seat.region));
  });

  const rows = Array.from(byKey.entries())
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label));

  return [{ value: 'all', label: 'all regions...' }, ...rows];
}

/** Pushes the current mapViewState values into the DOM filter/choropleth inputs and toggles second-party group visibility. */
function syncMapControlInputsFromState() {
  if (filterPartySelect) filterPartySelect.value = mapViewState.filterParty;
  if (filterRegionSelect) filterRegionSelect.value = mapViewState.filterRegion;

  const showSecondPlaceFilter = mapViewState.filterParty !== 'all';
  if (filterSecondPartyGroup) filterSecondPartyGroup.hidden = !showSecondPlaceFilter;
  if (!showSecondPlaceFilter) {
    mapViewState.filterSecondParty = 'all';
  }
  if (filterSecondPartySelect) filterSecondPartySelect.value = mapViewState.filterSecondParty;

  if (filterMajorityMinInput) filterMajorityMinInput.value = String(mapViewState.majorityMin);
  if (filterMajorityMaxInput) filterMajorityMaxInput.value = String(mapViewState.majorityMax);
  if (filterGainsButton) filterGainsButton.classList.toggle('is-active', mapViewState.gainsOnly);

  if (choroplethTypeSelect) choroplethTypeSelect.value = mapViewState.choroplethType;
  if (choroplethPartySelect) choroplethPartySelect.value = mapViewState.choroplethParty;
}

/** Reads the DOM filter/choropleth inputs into mapViewState, normalizing and clamping values, then syncs the inputs back. */
function syncMapControlStateFromInputs() {
  if (filterPartySelect) mapViewState.filterParty = filterPartySelect.value || 'all';
  if (filterRegionSelect) mapViewState.filterRegion = filterRegionSelect.value || 'all';
  if (mapViewState.filterParty === 'all') {
    mapViewState.filterSecondParty = 'all';
  } else if (filterSecondPartySelect) {
    mapViewState.filterSecondParty = filterSecondPartySelect.value || 'all';
  }
  if (filterMajorityMinInput) mapViewState.majorityMin = clampNumber(filterMajorityMinInput.value, 0, 100);
  if (filterMajorityMaxInput) mapViewState.majorityMax = clampNumber(filterMajorityMaxInput.value, 0, 100);
  if (mapViewState.majorityMin > mapViewState.majorityMax) {
    const swap = mapViewState.majorityMin;
    mapViewState.majorityMin = mapViewState.majorityMax;
    mapViewState.majorityMax = swap;
  }

  if (choroplethTypeSelect) mapViewState.choroplethType = choroplethTypeSelect.value || 'none';
  if (choroplethPartySelect) mapViewState.choroplethParty = choroplethPartySelect.value || 'all';

  syncMapControlInputsFromState();
}

/** Resets all primary filter state (party, region, majority range, gains toggle) to defaults and syncs the controls. */
function resetPrimaryFilters() {
  mapViewState.filterParty = 'all';
  mapViewState.filterRegion = 'all';
  mapViewState.filterSecondParty = 'all';
  mapViewState.majorityMin = 0;
  mapViewState.majorityMax = 100;
  mapViewState.gainsOnly = false;
  syncMapControlInputsFromState();
}

/** Resets choropleth type and party to defaults and syncs the controls. */
function resetChoropleths() {
  mapViewState.choroplethType = 'none';
  mapViewState.choroplethParty = 'all';
  syncMapControlInputsFromState();
}

/** Rebuilds all select options for party and region filter/choropleth controls from current seat data. */
function populateMapControlOptions() {
  const partyRows = collectPartyKeysForControls();
  const regionRows = collectRegionsForControls();

  setSelectOptions(filterPartySelect, partyRows, 'all');
  setSelectOptions(filterSecondPartySelect, partyRows, 'all');
  setSelectOptions(choroplethPartySelect, partyRows, 'all');

  setSelectOptions(filterRegionSelect, regionRows, 'all');

  syncMapControlInputsFromState();
}

/** Returns true if a seat passes all active primary filters (party, region, majority range, second party, gains-only). */
function seatMatchesPrimaryFilters(seat, comparisonSeat) {
  if (mapViewState.filterParty !== 'all' && seat.winner !== mapViewState.filterParty) return false;

  if (mapViewState.filterRegion !== 'all') {
    const seatRegion = normalizeRegionKey(seat.region);
    if (seatRegion !== mapViewState.filterRegion) return false;
  }

  const majority = seatMajorityStats(seat).pct;
  if (majority < mapViewState.majorityMin || majority > mapViewState.majorityMax) return false;

  if (mapViewState.filterSecondParty !== 'all') {
    const secondParty = secondPlacePartyKey(seat);
    if (secondParty !== mapViewState.filterSecondParty) return false;
  }

  if (mapViewState.gainsOnly) {
    const gainFrom = seatGainFromPartyKey(seat, comparisonSeat);
    if (!gainFrom) return false;
  }

  return true;
}

/** Returns a Set of seat keys for all seats that pass the current primary filters. */
function buildVisibleSeatKeySet() {
  const keySet = new Set();

  currentSeats.forEach((seat) => {
    const seatKey = seatLookupKey(seat.seat);
    const comparisonSeat = comparisonSeatsByKey.get(seatKey) || null;
    const visible = seatMatchesPrimaryFilters(seat, comparisonSeat);

    if (visible) keySet.add(seatKey);
  });

  return keySet;
}

/** Returns the choropleth metric value for a seat (vote share or vote share change), or null if choropleth is disabled or party is unset. */
function getChoroplethValue(seat, comparisonSeat) {
  if (mapViewState.choroplethType === 'none') return null;
  const partyKey = mapViewState.choroplethParty;
  if (!partyKey || partyKey === 'all') return null;

  if (mapViewState.choroplethType === 'voteShareChange') {
    if (!comparisonSeat) return null;
    return voteSharePct(seat, partyKey) - voteSharePct(comparisonSeat, partyKey);
  }

  if (mapViewState.choroplethType === 'voteShare') {
    return voteSharePct(seat, partyKey);
  }

  return null;
}

/**
 * Builds the choropleth rendering configuration for visible seats.
 * Returns { enabled: false } when no choropleth is selected.
 * For voteShareChange returns a diverging red-white-blue scale; for voteShare returns a white-to-party-colour scale.
 * Includes valueBySeatKey, toColour, and legend metadata.
 */
function buildChoroplethConfig(visibleSeatKeys) {
  if (mapViewState.choroplethType === 'none' || mapViewState.choroplethParty === 'all') return { enabled: false };
  const isDelta = mapViewState.choroplethType === 'voteShareChange';

  const valueBySeatKey = new Map();
  const values = [];

  currentSeats.forEach((seat) => {
    const seatKey = seatLookupKey(seat.seat);
    if (!visibleSeatKeys.has(seatKey)) return;
    const comparisonSeat = comparisonSeatsByKey.get(seatKey) || null;
    const value = getChoroplethValue(seat, comparisonSeat);
    if (!Number.isFinite(value)) return;
    valueBySeatKey.set(seatKey, value);
    values.push(value);
  });

  if (!values.length) return { enabled: false };

  const selectedPartyLabel = labelParty(mapViewState.choroplethParty);
  const selectedPartyColour = colourParty(mapViewState.choroplethParty);
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

/** Renders the choropleth colour gradient legend into the legend element, or hides it when choropleth is disabled. */
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

/** Resets current and comparison seat state from base data, recomputes summaries, and triggers a full map + panel re-render. */
function refreshElectionSeatStateAndRender() {
  if (!Array.isArray(baseElectionSeats) || !baseElectionSeats.length) return;

  currentSeats = baseElectionSeats.map((seat) => cloneSeatRecord(seat));
  currentSeatsByKey = buildSeatIndex(currentSeats);
  currentComparisonSeats = (defaultComparisonSeats || []).map((seat) => cloneSeatRecord(seat));
  comparisonSeatsByKey = buildSeatIndex(currentComparisonSeats);

  const summary = summarizeElection(currentSeats);
  const currentElection = currentManifest?.elections?.find((entry) => entry.id === currentElectionId) || null;
  if (currentElection) {
    updateTopSummary(currentElection, summary);
  }

  window.__mapsCurrentSummary = summary;
  window.__mapsComparisonSummary = defaultComparisonSummary;
  renderVoteTotals(summary, defaultComparisonSummary, {
    showVoteTotals: window.__mapsShowVoteTotals !== false,
  });
  renderMapWithViewState();
  syncRightPanelHeightToMap();
}

/**
 * Renders the map, seat list, vote totals, and choropleth legend for the current filter/choropleth state.
 * Accepts { preserveZoom: true } to retain the current pan/zoom transform.
 */
function renderMapWithViewState(options = {}) {
  if (!currentMapData) return;

  const visibleSeatKeys = buildVisibleSeatKeySet();
  const visibleSeats = currentSeats.filter((seat) => visibleSeatKeys.has(seatLookupKey(seat.seat)));
  const visibleComparisonSeats = Array.from(visibleSeatKeys)
    .map((seatKey) => comparisonSeatsByKey.get(seatKey))
    .filter(Boolean);
  const choroplethConfig = buildChoroplethConfig(visibleSeatKeys);

  const filteredSummary = summarizeElection(visibleSeats);
  const filteredComparisonSummary = currentComparisonSeats.length
    ? summarizeElection(visibleComparisonSeats)
    : null;

  window.__mapsCurrentSummary = filteredSummary;
  window.__mapsComparisonSummary = filteredComparisonSummary;

  renderVoteTotals(filteredSummary, filteredComparisonSummary, {
    showVoteTotals: window.__mapsShowVoteTotals !== false,
  });

  const preserveTransform = options.preserveZoom && mapSvg ? d3.zoomTransform(mapSvg) : null;
  renderTopoMap(currentMapData, currentSeats, {
    visibleSeatKeys,
    choroplethConfig,
    ...(preserveTransform ? { preserveTransform } : {}),
  });
  renderSeatList(visibleSeats, currentComparisonSeats);
  applySeatSearchSuggestions(buildSeatSearchIndex(visibleSeats));
  renderChoroplethLegend(choroplethConfig);

  if (seatPreview) {
    seatPreview.textContent = `Showing ${formatInt(visibleSeats.length)} of ${formatInt(currentSeats.length)} seats.`;
  }
}

/** Hides the seat detail popup and clears the tracked open seat name. */
function hideSeatPopup() {
  if (!seatPopup) return;
  seatPopup.hidden = true;
  currentOpenSeatName = null;
}

/** Renders the seat detail popup for seatName, showing majority, gain indicator, and a ranked vote share bar chart with comparison deltas. */
function renderSeatPopup(seatName) {
  if (!seatPopup || !seatPopupTitle || !seatPopupMeta || !seatPopupList) return;

  const seatKey = seatLookupKey(seatName);
  const seat = currentSeatsByKey.get(seatKey);
  if (!seat) {
    hideSeatPopup();
    return;
  }
  currentOpenSeatName = seatName;

  const comparisonSeat = comparisonSeatsByKey.get(seatKey) || null;
  const gainFrom = seatGainFromPartyKey(seat, comparisonSeat);
  const turnout = totalVotesForSeat(seat);
  const majority = seatMajorityStats(seat);
  const showTurnout = currentElectionType !== 'model_uns';
  const showRawMajority = currentElectionType !== 'model_uns';

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
      <div class="maps-popup-party"><span class="maps-seat-icon" style="background:${colourParty(row.party)}"></span>${labelParty(row.party)}</div>
      <div class="maps-popup-values">
        <span>${formatPct(row.pct)}%</span>
        ${row.delta == null ? '' : `<span class="${deltaClass(row.delta)}">${formatSigned(row.delta, 2)}</span>`}
      </div>
    `;
    seatPopupList.appendChild(item);
  });

  seatPopup.hidden = false;
}

/** Returns a sorted copy of party rows according to currentSort (party name alpha, or numeric column with label tiebreak). */
function sortPartyRows(rows) {
  const multiplier = currentSort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (currentSort.key === 'party') {
      return multiplier * labelParty(a.party).localeCompare(labelParty(b.party));
    }

    const av = Number(a[currentSort.key] || 0);
    const bv = Number(b[currentSort.key] || 0);
    if (av === bv) return labelParty(a.party).localeCompare(labelParty(b.party));
    return multiplier * (av - bv);
  });
}

/** Attaches click and keyboard (Enter/Space) handlers to all [data-sort-key] table headers to trigger sort changes via the onSortChanged callback. */
function wireVoteTotalsSorting(onSortChanged) {
  document.querySelectorAll('th[data-sort-key]').forEach((header) => {
    const sortKey = header.getAttribute('data-sort-key');
    if (!sortKey) return;

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

/** Toggles the 'hide-comparison-cols' class on the vote totals table to show or hide comparison delta columns. */
function toggleComparisonColumns(showComparison) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-comparison-cols', !showComparison);
}

/** Toggles the 'hide-vote-total-col' class on the vote totals table to show or hide raw vote count columns. */
function toggleVoteTotalColumns(showVoteTotals) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-vote-total-col', !showVoteTotals);
}

/** Renders the vote totals summary table, showing seat counts, vote share, and comparison deltas when a comparisonSummary is provided. Truncates to top 6 rows unless expanded. */
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

  const sortedRows = sortPartyRows(rows);
  const visibleRows = voteTotalsExpanded ? sortedRows : sortedRows.slice(0, 6);

  if (voteTotalsToggle) {
    const canExpand = sortedRows.length > 6;
    voteTotalsToggle.hidden = !canExpand;
    if (canExpand) {
      voteTotalsToggle.textContent = voteTotalsExpanded ? 'Show fewer' : 'Show all';
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
      <td class="comparison-col ${showComparison ? deltaClass(partyRow.votePctDelta) : ''}">${showComparison ? formatSigned(partyRow.votePctDelta, 2) : ''}</td>
    `;
    voteTotalsBody.appendChild(tr);
  });
}

/** Renders up to 300 seat rows sorted alphabetically into the seat list panel. Each row shows the winner colour, name, and gain-from indicator. Click zooms and opens the seat popup. */
function renderSeatList(seats, comparisonSeats = null) {
  if (!seatList) return;
  seatList.innerHTML = '';
  selectedSeatRow = null;
  seatListRowByKey = new Map();

  const comparisonWinnerBySeat = comparisonSeats ? buildWinnerBySeat(comparisonSeats) : new Map();

  const ordered = [...seats].sort((a, b) => a.seat.localeCompare(b.seat));
  ordered.slice(0, 300).forEach((seat) => {
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

      const zoomed = mapInteractionController.zoomToSeat(seatName);
      if (seatPreview) {
        seatPreview.textContent = zoomed ? `Selected: ${seatName}` : `Seat not found on map: ${seatName}`;
      }
      renderSeatPopup(seatName);
    });

    seatListRowByKey.set(seatKey, item);
    seatList.appendChild(item);
  });
}

/** Marks the seat list row for seatKey as selected (is-selected class) and deselects the previously selected row. */
function setSelectedSeatRowByKey(seatKey) {
  const nextRow = seatListRowByKey.get(seatKey);
  if (!nextRow) return;

  if (selectedSeatRow && selectedSeatRow !== nextRow) {
    selectedSeatRow.classList.remove('is-selected');
  }
  nextRow.classList.add('is-selected');
  selectedSeatRow = nextRow;
}

/** Builds the module-level seat name search index (seatSearchNames and currentSeatNameByKey) from the provided seats. Returns the sorted name array. */
function buildSeatSearchIndex(seats) {
  currentSeatNameByKey = new Map();
  const names = [];

  (seats || []).forEach((seat) => {
    const seatName = String(seat?.seat || '').trim();
    if (!seatName) return;
    const key = seatLookupKey(seatName);
    if (currentSeatNameByKey.has(key)) return;
    currentSeatNameByKey.set(key, seatName);
    names.push(seatName);
  });

  names.sort((a, b) => a.localeCompare(b));
  seatSearchNames = names;
  return names;
}

/** Creates the autocomplete dropdown menu element adjacent to the seat search input if it doesn't exist yet. Returns the element or null if the input is absent. */
function ensureSeatSearchMenu() {
  if (seatSearchMenuEl || !seatSearchInput) return seatSearchMenuEl;
  const searchGroup = seatSearchInput.closest('.maps-toolbar-group-search') || seatSearchInput.parentElement;
  if (!searchGroup) return null;

  const menu = document.createElement('div');
  menu.className = 'maps-seat-search-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'listbox');
  menu.id = 'mapsSeatSearchMenu';
  searchGroup.appendChild(menu);
  seatSearchMenuEl = menu;
  return seatSearchMenuEl;
}

/** Hides the autocomplete dropdown and clears the keyboard suggestion index. */
function hideSeatSearchSuggestions() {
  seatSearchSuggestionIndex = -1;
  if (!seatSearchMenuEl) return;
  seatSearchMenuEl.hidden = true;
  seatSearchMenuEl.innerHTML = '';
}

/** Populates the autocomplete dropdown with up to MAX_SEAT_SEARCH_SUGGESTIONS seat names matching query (starts-with first, then contains). */
function showSeatSearchSuggestions(query = '') {
  const menu = ensureSeatSearchMenu();
  if (!menu) return;

  const queryText = String(query || '').trim().toLowerCase();
  const startsWithMatches = [];
  const includesMatches = [];
  seatSearchNames.forEach((name) => {
    const lowerName = name.toLowerCase();
    if (!queryText || lowerName.startsWith(queryText)) {
      startsWithMatches.push(name);
      return;
    }
    if (lowerName.includes(queryText)) includesMatches.push(name);
  });

  seatSearchSuggestions = [...startsWithMatches, ...includesMatches].slice(0, MAX_SEAT_SEARCH_SUGGESTIONS);
  seatSearchSuggestionIndex = -1;
  menu.innerHTML = '';

  if (!seatSearchSuggestions.length) {
    menu.hidden = true;
    return;
  }

  seatSearchSuggestions.forEach((name, index) => {
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

/** Updates the keyboard-active (is-active) class on suggestion items to reflect seatSearchSuggestionIndex. */
function updateSeatSearchHighlight() {
  if (!seatSearchMenuEl) return;
  const options = seatSearchMenuEl.querySelectorAll('.maps-seat-search-item');
  options.forEach((option) => {
    const index = Number(option.dataset.index);
    option.classList.toggle('is-active', index === seatSearchSuggestionIndex);
  });
}

/** Updates the seat name list used for autocomplete suggestions and hides any open dropdown. */
function applySeatSearchSuggestions(seatNames) {
  if (!seatSearchInput) return;
  seatSearchNames = Array.isArray(seatNames) ? [...seatNames] : [];
  hideSeatSearchSuggestions();
}

/** Resolves a search query to a seat name (exact → starts-with → contains), zooms the map, selects the list row, and opens the popup. */
function selectSeatBySearchQuery(query) {
  const rawQuery = String(query || '').trim();
  if (!rawQuery) return;

  const directKey = seatLookupKey(rawQuery);
  let seatName = currentSeatNameByKey.get(directKey) || null;

  if (!seatName) {
    const queryLower = rawQuery.toLowerCase();
    seatName = Array.from(currentSeatNameByKey.values()).find((name) => name.toLowerCase().startsWith(queryLower))
      || Array.from(currentSeatNameByKey.values()).find((name) => name.toLowerCase().includes(queryLower))
      || null;
  }

  if (!seatName) {
    if (seatPreview) seatPreview.textContent = `Seat not found: ${rawQuery}`;
    return;
  }

  const seatKey = seatLookupKey(seatName);
  const zoomed = mapInteractionController.zoomToSeat(seatName);
  if (zoomed) {
    setSelectedSeatRowByKey(seatKey);
    renderSeatPopup(seatName);
    if (seatPreview) seatPreview.textContent = `Selected: ${seatName}`;
    if (seatSearchInput) seatSearchInput.value = seatName;
    return;
  }

  if (seatPreview) seatPreview.textContent = `Seat not found on map: ${seatName}`;
}

/** Attaches all seat search event listeners (focus, input, change, blur, keydown for arrow/enter/escape navigation, outside-click to close). Guards against double-wiring. */
function wireSeatSearch() {
  if (!seatSearchInput || seatSearchInput.dataset.wired === 'true') return;
  ensureSeatSearchMenu();

  let lastSubmittedQuery = '';
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
      if (!seatSearchSuggestions.length) {
        showSeatSearchSuggestions(seatSearchInput.value);
      }
      if (!seatSearchSuggestions.length) return;
      event.preventDefault();
      seatSearchSuggestionIndex = Math.min(seatSearchSuggestionIndex + 1, seatSearchSuggestions.length - 1);
      updateSeatSearchHighlight();
      return;
    }

    if (event.key === 'ArrowUp') {
      if (!seatSearchSuggestions.length) return;
      event.preventDefault();
      seatSearchSuggestionIndex = Math.max(seatSearchSuggestionIndex - 1, 0);
      updateSeatSearchHighlight();
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      if (seatSearchSuggestionIndex >= 0 && seatSearchSuggestionIndex < seatSearchSuggestions.length) {
        const selectedName = seatSearchSuggestions[seatSearchSuggestionIndex];
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
    if (seatSearchMenuEl?.contains(event.target)) return;
    hideSeatSearchSuggestions();
  });

  seatSearchInput.dataset.wired = 'true';
}

/** Sets the right panel's height to match the map stage height so the two columns stay aligned. */
function syncRightPanelHeightToMap() {
  if (!mapsStage || !mapsPanelRight) return;

  const stageHeight = mapsStage.getBoundingClientRect().height;
  if (!Number.isFinite(stageHeight) || stageHeight <= 0) return;

  mapsPanelRight.style.height = `${Math.round(stageHeight)}px`;
  mapsPanelRight.style.maxHeight = `${Math.round(stageHeight)}px`;
}

/** Updates the page title and subtitle with the election name and leading-party majority (or hung-parliament message). */
function updateTopSummary(election, summary) {
  setMapsPageTitle(election?.name);
  const top = summary.parties[0];
  const leadSeats = Number(top?.seats || 0);
  const totalSeats = Number(summary.totalSeats || 0);
  const majorityThreshold = totalSeats / 2;
  const hasMajority = leadSeats > majorityThreshold;
  const majority = hasMajority ? Math.round(2 * (leadSeats - majorityThreshold)) : 0;

  if (subtitle) {
    if (hasMajority) {
      const baseText = `${election.name} · ${labelParty(top?.party || 'others')} majority: ${majority}`;
      setSubtitleText(baseText, { includeLatestPollSnippet: election?.type === 'model_uns' });
    } else {
      const baseText = `${election.name} · Hung parliament - largest party ${labelParty(top?.party || 'others')} with ${formatInt(leadSeats)} seats`;
      setSubtitleText(baseText, { includeLatestPollSnippet: election?.type === 'model_uns' });
    }
  }
}

/** Extracts the seat name from a TopoJSON feature's properties, trying several common property name variations. Returns null if none found. */
function seatNameFromFeature(featureDatum) {
  const props = featureDatum?.properties || {};
  return props.name || props.seat_name || props.seat || props.constituency || props.Name || null;
}

/** Returns a Map from seat name (original and lowercase) to winner party key, used for fast map colour lookups. */
function buildWinnerBySeat(seats) {
  const bySeat = new Map();
  seats.forEach((seat) => {
    if (!seat?.seat) return;
    bySeat.set(seat.seat, seat.winner || 'others');
    bySeat.set(String(seat.seat).toLowerCase(), seat.winner || 'others');
  });
  return bySeat;
}


/** Returns the d3 zoom transform that centres the map at INITIAL_MAP_SCALE. */
function getInitialZoomTransform(width, height) {
  const scale = Math.max(1, Number(INITIAL_MAP_SCALE) || 1);
  const tx = width / 2 - scale * (width / 2);
  const ty = height / 2 - scale * (height / 2);
  return d3.zoomIdentity.translate(tx, ty).scale(scale);
}

/** Computes a d3 zoom transform to zoom into a specific map feature, scaling based on the square-root of its bounding box dimensions. */
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
 * draws region boundary overlays, and sets up mapInteractionController for external zoom/reset/highlight calls.
 * Accepts { visibleSeatKeys, choroplethConfig, preserveTransform } in options.
 */
function renderTopoMap(mapData, seats, options = {}) {
  if (!mapSvg || !mapContent || !zoomValue) return;

  const objectName = Object.keys(mapData?.objects || {})[0];
  if (!objectName) throw new Error('TopoJSON missing objects');

  const object = mapData.objects[objectName];
  const featureCollection = topojsonFeature(mapData, object);
  const regionBoundaryMesh = topojsonMesh(
    mapData,
    object,
    (a, b) => a && b && a !== b && a.properties?.region !== b.properties?.region
  );
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
  activeSeatPathNode = null;

  features.forEach((featureDatum) => {
    const seatName = seatNameFromFeature(featureDatum);
    if (!seatName) return;
    featureBySeat.set(seatLookupKey(seatName), featureDatum);
  });

  const zoomRoot = content.append('g').attr('class', 'maps-geo-root');
  zoomRoot.append('rect').attr('class', 'maps-map-bg').attr('x', 0).attr('y', 0).attr('width', width).attr('height', height);
  const zoomLayer = zoomRoot.append('g').attr('class', 'maps-geo-layer');
  const boundaryLayer = zoomLayer.append('g').attr('class', 'maps-boundary-layer');
  const seatLayer = zoomLayer.append('g').attr('class', 'maps-seat-layer');

  const zoomBehavior = d3
    .zoom()
    .scaleExtent([ZOOM_MIN_SCALE, ZOOM_MAX_SCALE])
    .on('zoom', (event) => {
      zoomLayer.attr('transform', event.transform.toString());
      zoomValue.textContent = formatZoomPct(event.transform.k);
    });

  svg.call(zoomBehavior);
  const initialTransform = getInitialZoomTransform(width, height);

  const zoomToFeature = (featureDatum) => {
    const targetTransform = getLegacySeatZoomTransform(path, featureDatum, width, height);
    svg.transition().duration(CLICK_ZOOM_DURATION_MS).call(zoomBehavior.transform, targetTransform);
  };

  const clearActiveSeatPath = () => {
    if (!activeSeatPathNode) return;
    d3.select(activeSeatPathNode).classed('maps-region-path-active', false);
    activeSeatPathNode = null;
  };

  const setActiveSeatPath = (pathNode) => {
    if (!pathNode) return;
    if (activeSeatPathNode && activeSeatPathNode !== pathNode) {
      d3.select(activeSeatPathNode).classed('maps-region-path-active', false);
    }
    activeSeatPathNode = pathNode;
    d3.select(pathNode).classed('maps-region-path-active', true).raise();
  };

  const resetZoom = () => {
    hideSeatPopup();
    clearActiveSeatPath();
    svg.transition().duration(RESET_ZOOM_DURATION_MS).call(zoomBehavior.transform, initialTransform);
  };

  mapInteractionController = {
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
  };

  if (regionBoundaryMesh) {
    boundaryLayer
      .append('path')
      .datum(regionBoundaryMesh)
      .attr('class', 'maps-region-boundary')
      .attr('d', path);
  }

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
      const seat = currentSeatsByKey.get(seatKey);
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
    .on('mouseenter', (event, datum) => {
      const seatName = seatNameFromFeature(datum);
      if (seatPreview && seatName) seatPreview.textContent = `Selected: ${seatName}`;
    })
    .on('click', (event, datum) => {
      event.stopPropagation();
      setActiveSeatPath(event.currentTarget);
      const seatName = seatNameFromFeature(datum);
      if (seatName) {
        renderSeatPopup(seatName);
      }
      zoomToFeature(datum);
    });

  seatPaths.each(function assignSeatPath(datum) {
    const seatName = seatNameFromFeature(datum);
    if (!seatName) return;
    seatPathByKey.set(seatLookupKey(seatName), this);
  });

  svg.on('click', (event) => {
    const target = event.target;
    if (target === mapSvg || target?.classList?.contains('maps-map-bg')) {
      resetZoom();
    }
  });

  svg.call(zoomBehavior.transform, options.preserveTransform || initialTransform);
}

/** Attaches click handlers to all [data-popup-action] buttons, supporting 'close' and 'toggle' actions on their target panel element. Guards against double-wiring. */
function wirePopupPanels() {
  document.querySelectorAll('[data-popup-action]').forEach((button) => {
    if (button.dataset.wired === 'true') return;

    button.addEventListener('click', () => {
      const action = button.getAttribute('data-popup-action');
      const targetId = button.getAttribute('data-popup-target');
      const panel = targetId ? document.getElementById(targetId) : null;
      if (!panel) return;

      if (action === 'close') {
        panel.hidden = true;
        return;
      }

      if (action === 'toggle') {
        panel.hidden = !panel.hidden;
      }
    });

    button.dataset.wired = 'true';
  });
}

/** Attaches change handlers to all filter and choropleth inputs, and click handlers to the gains, reset-filters, and reset-choropleths buttons. Guards against double-wiring. */
function wireMapViewControls() {
  if (filterPartySelect?.dataset.wired === 'true') return;

  const applyFromInputs = () => {
    syncMapControlStateFromInputs();
    renderMapWithViewState();
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
      mapViewState.gainsOnly = !mapViewState.gainsOnly;
      syncMapControlInputsFromState();
      renderMapWithViewState();
    });
  }

  if (filtersResetButton) {
    filtersResetButton.addEventListener('click', () => {
      resetPrimaryFilters();
      renderMapWithViewState();
    });
  }

  if (choroplethsResetButton) {
    choroplethsResetButton.addEventListener('click', () => {
      resetChoropleths();
      renderMapWithViewState();
    });
  }

  if (filterPartySelect) filterPartySelect.dataset.wired = 'true';
}

/** Attaches click handlers to all [data-map-action] buttons for zoom-in, zoom-out, reset-zoom, and reset-view actions. */
function wireMapInteractions() {
  document.querySelectorAll('[data-map-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.getAttribute('data-map-action');
      if (action === 'zoom-in') mapInteractionController.zoomBy(1.2);
      if (action === 'zoom-out') mapInteractionController.zoomBy(0.83);
      if (action === 'reset-zoom') mapInteractionController.reset();
      if (action === 'reset-view') {
        mapInteractionController.reset();
        resetPrimaryFilters();
        resetChoropleths();
        renderMapWithViewState();
      }
    });
  });

}

/** Attaches a change handler to a poll tracker metric checkbox that re-renders the chart when poll tracker is active. Guards against double-wiring. */
function wirePollTrackerMetricInput(inputEl) {
  if (!inputEl || inputEl.dataset.wired === 'true') return;
  inputEl.addEventListener('change', () => {
    if (pollTrackerModeActive) renderPollTrackerChart();
  });
  inputEl.dataset.wired = 'true';
}

/** Wires the seats and vote-% metric inputs and all [data-polltracker-range] range buttons, re-rendering the chart on change. */
function wirePollTrackerControls() {
  wirePollTrackerMetricInput(pollTrackerMetricSeatsInput);
  wirePollTrackerMetricInput(pollTrackerMetricVotesInput);

  document.querySelectorAll('[data-polltracker-range]').forEach((button) => {
    if (button.dataset.wired === 'true') return;
    button.addEventListener('click', () => {
      const nextRange = button.getAttribute('data-polltracker-range') || 'all';
      pollTrackerRangeSelection = nextRange;
      document.querySelectorAll('[data-polltracker-range]').forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate.getAttribute('data-polltracker-range') === nextRange);
      });
      if (pollTrackerModeActive) renderPollTrackerChart();
    });
    button.dataset.wired = 'true';
  });
}

/** Clears all predict mode module-level state and hides the predict window. */
function resetPredictModeState() {
  predictModeActive = false;
  predictBaseSeats = [];
  predictBaseSeatsByKey = new Map();
  predictBaseMapData = null;
  predictBaseRegionLabelsByKey = new Map();
  predictColumnPartyKeys = [];
  predictInputByRegionParty = new Map();
  predictBaselineShareByRegionParty = new Map();
  predictOtherCellByRegion = new Map();
  predictEnglandExpanded = false;
  predictRegionalSwingsByParty = new Map();
  if (predictWindow) predictWindow.hidden = true;
  syncPredictModeRightColumnLayout();
}

/** Clears poll tracker mode state and hides the poll tracker layout. */
function resetPollTrackerModeState() {
  pollTrackerModeActive = false;
  pollTrackerRangeSelection = 'all';
  setPollTrackerLayoutVisible(false);
  syncPredictModeRightColumnLayout();
}

/**
 * Bootstraps election data: fetches the manifest, resolves the active election from the URL or defaults,
 * loads map and results JSON in parallel, optionally loads comparison election data,
 * populates controls, and triggers the initial render.
 */
async function initElectionData() {
  const manifest = await fetchJson('data/elections.json');
  currentManifest = manifest;
  hydrateManifestSettings(manifest);
  await loadPollTrackerMetaIfNeeded();
  const params = new URLSearchParams(window.location.search);
  const requestedId = params.get('election');

  let currentElection = manifest.elections.find((e) => e.id === requestedId);
  if (!currentElection) {
    currentElection =
      manifest.elections.find((e) => e.id === 'current-prediction')
      || manifest.elections.find((e) => e.id === manifest.defaultElection)
      || manifest.elections[0];
  }

  if (!currentElection) {
    throw new Error('No elections configured in data/elections.json');
  }

  currentElectionId = currentElection.id;

  resetPredictModeState();
  resetPollTrackerModeState();

  currentRegionLabelsByKey = buildRegionLabelLookup(currentElection.mapId);

  renderElectionLinks(manifest, currentElection.id);
  setPredictModeNavState(false);
  setPollTrackerNavState(false);

  const { mapFile, dataFile } = resolveElectionFiles(manifest, currentElection);

  const [mapData, resultsData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`)
  ]);

  const seats = normalizeSeats(resultsData, manifestPartiesById, manifestRegionsById);
  const showVoteTotals = currentElection.type !== 'model_uns';
  currentElectionType = currentElection.type;
  baseElectionSeats = seats;
  currentSeats = seats.map((seat) => cloneSeatRecord(seat));
  currentMapData = mapData;
  currentSeatsByKey = buildSeatIndex(currentSeats);

  defaultComparisonSummary = null;
  defaultComparisonSeats = [];
  if (currentElection.comparisonElectionId) {
    const comparisonElection = manifest.elections.find((entry) => entry.id === currentElection.comparisonElectionId);
    if (comparisonElection) {
      const { dataFile: comparisonDataFile } = resolveElectionFiles(manifest, comparisonElection);
      const comparisonData = await fetchJson(`data/${comparisonDataFile}`);
      defaultComparisonSeats = normalizeSeats(comparisonData, manifestPartiesById, manifestRegionsById);
      defaultComparisonSummary = summarizeElection(defaultComparisonSeats);
    }
  }

  currentComparisonSeats = defaultComparisonSeats.map((seat) => cloneSeatRecord(seat));
  comparisonSeatsByKey = buildSeatIndex(currentComparisonSeats);

  populateMapControlOptions();
  syncMapControlStateFromInputs();

  window.__mapsShowVoteTotals = showVoteTotals;
  refreshElectionSeatStateAndRender();
}

/** Entry point: wires all controls and event listeners, then loads election data and navigates to the correct initial view (election / predict / poll tracker). */
async function init() {
  wireMapInteractions();
  wirePopupPanels();
  wireMapViewControls();
  wirePredictControls();
  wirePollTrackerControls();
  wireSeatSearch();
  if (seatPopupClose) {
    seatPopupClose.addEventListener('click', () => {
      hideSeatPopup();
      mapInteractionController.clearSelection?.();
    });
  }
  if (voteTotalsToggle) {
    voteTotalsToggle.addEventListener('click', () => {
      voteTotalsExpanded = !voteTotalsExpanded;
      if (!window.__mapsCurrentSummary) return;
      renderVoteTotals(window.__mapsCurrentSummary, window.__mapsComparisonSummary || null, {
        showVoteTotals: window.__mapsShowVoteTotals !== false,
      });
      syncPredictModeRightColumnLayout();
    });
  }
  wireVoteTotalsSorting(() => {
    if (!window.__mapsCurrentSummary) return;
    renderVoteTotals(window.__mapsCurrentSummary, window.__mapsComparisonSummary || null, {
      showVoteTotals: window.__mapsShowVoteTotals !== false,
    });
    syncRightPanelHeightToMap();
  });

  window.addEventListener('resize', () => {
    syncRightPanelHeightToMap();
    if (pollTrackerModeActive) renderPollTrackerChart();
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

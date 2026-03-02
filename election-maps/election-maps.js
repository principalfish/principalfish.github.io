import * as d3 from 'https://cdn.jsdelivr.net/npm/d3@7/+esm';
import { feature as topojsonFeature } from 'https://cdn.jsdelivr.net/npm/topojson-client@3/+esm';
import { mesh as topojsonMesh } from 'https://cdn.jsdelivr.net/npm/topojson-client@3/+esm';

const viewport = document.getElementById('mapsViewport');
const mapSvg = document.querySelector('.maps-svg');
const mapContent = document.getElementById('mapContent');
const zoomValue = document.getElementById('mapsZoomValue');
const seatPreview = document.getElementById('mapsSeatPreview');
const electionList = document.getElementById('mapsElectionList');
const subtitle = document.getElementById('mapsSubtitle');
const voteMeta = document.getElementById('mapsVoteMeta');
const voteTotalsBody = document.getElementById('mapsVoteTotalsBody');
const voteTotalsTable = document.getElementById('mapsVoteTotalsTable');
const voteTotalsToggle = document.getElementById('mapsVoteTotalsToggle');
const seatSearchInput = document.getElementById('maps-seat-search');
const seatList = document.getElementById('mapsSeatList');
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

let currentSort = { key: 'seats', direction: 'desc' };
let manifestPartiesByKey = {};
let manifestRegionsByMapId = {};
let voteTotalsExpanded = false;
let selectedSeatRow = null;
let activeSeatPathNode = null;
let currentSeatsByKey = new Map();
let comparisonSeatsByKey = new Map();
let currentSeatNameByKey = new Map();
let seatListRowByKey = new Map();
let currentRegionLabelsByKey = new Map();
let currentElectionType = null;
let currentSeats = [];
let currentComparisonSeats = [];
let currentMapData = null;

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
let mapInteractionController = {
  zoomBy: () => {},
  reset: () => {},
  clearSelection: () => {},
  zoomToSeat: () => false,
};

const PARTY_LABELS = {
  labour: 'Labour',
  conservative: 'Conservative',
  libdems: 'Liberal Democrats',
  reform: 'Reform UK',
  ukip: 'UKIP',
  green: 'Green',
  snp: 'SNP',
  plaidcymru: 'Plaid Cymru',
  dup: 'DUP',
  sdlp: 'SDLP',
  uu: 'UUP',
  uup: 'UUP',
  sinnfein: 'Sinn Fein',
  alliance: 'Alliance',
  others: 'Other'
};

const PARTY_COLOURS = {
  labour: '#E4003B',
  conservative: '#0087DC',
  libdems: '#FAA61A',
  reform: '#12B6CF',
  ukip: '#70147A',
  green: '#6AB023',
  snp: '#FDF38E',
  plaidcymru: '#005B54',
  dup: '#D46A4C',
  sdlp: '#2AA82A',
  uu: '#7BB7EA',
  uup: '#7BB7EA',
  sinnfein: '#2D6A4F',
  alliance: '#F6C744',
  others: '#9CA3AF'
};

const PARTY_KEY_ALIASES = {
  ukindependenceparty: 'ukip',
  reformuk: 'reform',
  liberaldemocrats: 'libdems',
  democraticunionistparty: 'dup',
  ulsterunionistparty: 'uu',
  scottishnationalparty: 'snp',
  other: 'others',
};

function normalizePartyKey(partyKey) {
  const raw = String(partyKey || '').trim();
  if (!raw) return 'others';

  const lower = raw.toLowerCase();
  if (PARTY_LABELS[lower]) return lower;

  const alnum = lower.replace(/[^a-z0-9]/g, '');
  if (PARTY_KEY_ALIASES[alnum]) return PARTY_KEY_ALIASES[alnum];
  if (PARTY_LABELS[alnum]) return alnum;

  return lower;
}

function labelParty(partyKey) {
  const meta = manifestPartiesByKey[partyKey];
  if (meta?.name) return meta.name;
  return PARTY_LABELS[partyKey] || partyKey;
}

function colourParty(partyKey) {
  const meta = manifestPartiesByKey[partyKey];
  if (meta?.colour) return meta.colour;
  return PARTY_COLOURS[partyKey] || '#9CA3AF';
}

function formatInt(value) {
  return Math.round(value).toLocaleString('en-GB');
}

function formatPct(value) {
  return Number(value).toFixed(2);
}

function formatSigned(value, digits = 0) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return '0';
  const sign = num > 0 ? '+' : '';
  return `${sign}${num.toFixed(digits)}`;
}

function deltaClass(value) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return 'maps-delta-neutral';
  return num > 0 ? 'maps-delta-positive' : 'maps-delta-negative';
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return response.json();
}

function parseLegacySeatObject(resultsObject) {
  return Object.entries(resultsObject)
    .filter(([, value]) => value && typeof value === 'object' && value.seatInfo && value.partyInfo)
    .map(([seatName, value]) => {
      const votes = {};
      Object.entries(value.partyInfo || {}).forEach(([party, info]) => {
        const voteTotal = Number(info?.total || 0);
        if (voteTotal <= 0) return;
        const partyKey = normalizePartyKey(party);
        votes[partyKey] = (votes[partyKey] || 0) + voteTotal;
      });

      return {
        seat: seatName,
        region: value.seatInfo?.region || 'unknown',
        winner: normalizePartyKey(value.seatInfo?.current || 'others'),
        electorate: Number(value.seatInfo?.electorate || 0),
        turnout: Number(value.seatInfo?.turnout || 0),
        votes
      };
    });
}

function normalizeSeats(resultsData) {
  if (Array.isArray(resultsData?.seats)) {
    return resultsData.seats.map((seat) => ({
      seat: seat.seat || seat.n || 'Unknown seat',
      region: seat.region || seat.r || 'unknown',
      winner: normalizePartyKey(seat.winner || seat.w || 'others'),
      electorate: Number(seat.electorate ?? seat.e ?? 0),
      turnout: Number(seat.turnout ?? seat.t ?? 0),
      votes: (() => {
        if (seat.votes && typeof seat.votes === 'object' && !Array.isArray(seat.votes)) {
          const normalizedVotes = {};
          Object.entries(seat.votes).forEach(([partyKey, voteValue]) => {
            const normalizedPartyKey = normalizePartyKey(partyKey);
            const voteTotal = Number(voteValue || 0);
            if (voteTotal <= 0) return;
            normalizedVotes[normalizedPartyKey] = (normalizedVotes[normalizedPartyKey] || 0) + voteTotal;
          });
          return normalizedVotes;
        }

        if (Array.isArray(seat.p)) {
          const compactVotes = {};
          seat.p.forEach((entry) => {
            if (!Array.isArray(entry) || entry.length < 2) return;
            const partyKey = normalizePartyKey(entry[0]);
            const voteTotal = Number(entry[1] || 0);
            if (!partyKey || voteTotal <= 0) return;
            compactVotes[partyKey] = (compactVotes[partyKey] || 0) + voteTotal;
          });
          return compactVotes;
        }

        return {};
      })()
    }));
  }

  if (resultsData && typeof resultsData === 'object') {
    return parseLegacySeatObject(resultsData);
  }

  return [];
}

function summarizeElection(seats) {
  const partyStats = new Map();
  let electorateSum = 0;
  let turnoutWeighted = 0;

  seats.forEach((seat) => {
    const winner = seat.winner || 'others';
    if (!partyStats.has(winner)) {
      partyStats.set(winner, { seats: 0, votes: 0 });
    }
    partyStats.get(winner).seats += 1;

    Object.entries(seat.votes || {}).forEach(([party, votes]) => {
      if (!partyStats.has(party)) {
        partyStats.set(party, { seats: 0, votes: 0 });
      }
      partyStats.get(party).votes += Number(votes || 0);
    });

    if (seat.electorate > 0 && seat.turnout > 0) {
      electorateSum += seat.electorate;
      turnoutWeighted += seat.turnout * seat.electorate;
    }
  });

  const parties = Array.from(partyStats.entries())
    .map(([party, stats]) => ({ party, ...stats }))
    .sort((a, b) => b.seats - a.seats || b.votes - a.votes);

  const totalVotes = parties.reduce((sum, p) => sum + p.votes, 0);
  const turnout = electorateSum > 0 ? turnoutWeighted / electorateSum : 0;

  return { parties, totalVotes, turnout, totalSeats: seats.length };
}

function renderElectionLinks(manifest, activeId) {
  if (!electionList) return;

  electionList.innerHTML = '';
  manifest.elections.forEach((election) => {
    const link = document.createElement('a');
    link.href = `?election=${encodeURIComponent(election.id)}`;
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);
  });
}

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

function hydrateManifestSettings(manifest) {
  const settings = manifest?.settings || {};
  manifestPartiesByKey = settings.partiesByKey || {};
  manifestRegionsByMapId = settings.regionsByMapId || {};
}

function normalizeRegionKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

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

function titleCaseFromRegionKey(regionKey) {
  const text = String(regionKey || '').trim();
  if (!text) return 'Unknown';
  const spaced = text.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/-/g, ' ').replace(/_/g, ' ');
  if (spaced.includes(' ')) {
    return spaced
      .split(/\s+/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
      .join(' ');
  }
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function labelRegion(regionKey) {
  const normalized = normalizeRegionKey(regionKey);
  if (!normalized) return 'Unknown';
  return currentRegionLabelsByKey.get(normalized) || titleCaseFromRegionKey(regionKey);
}

function setSortDirection(sortKey) {
  if (currentSort.key === sortKey) {
    currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    return;
  }
  currentSort.key = sortKey;
  currentSort.direction = sortKey === 'party' ? 'asc' : 'desc';
}

function buildSeatIndex(seats) {
  const byKey = new Map();
  (seats || []).forEach((seat) => {
    if (!seat?.seat) return;
    byKey.set(seatLookupKey(seat.seat), seat);
  });
  return byKey;
}

function totalVotesForSeat(seat) {
  const turnout = Number(seat?.turnout || 0);
  if (turnout > 0) return turnout;
  return Object.values(seat?.votes || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function seatGainFromPartyKey(currentSeat, comparisonSeat) {
  const winner = currentSeat?.winner || 'others';
  const previousWinner = comparisonSeat?.winner || null;
  if (!previousWinner || previousWinner === winner) return null;
  return previousWinner;
}

function seatMajorityStats(seat) {
  const voteRows = Object.entries(seat?.votes || {})
    .map(([party, votes]) => ({ party, votes: Number(votes || 0) }))
    .filter((row) => row.votes > 0)
    .sort((a, b) => b.votes - a.votes);

  if (voteRows.length < 2) return { pct: 0, raw: 0 };
  const marginVotes = voteRows[0].votes - voteRows[1].votes;
  const totalVotes = totalVotesForSeat(seat);
  if (totalVotes <= 0) return { pct: 0, raw: marginVotes };
  return {
    pct: (marginVotes / totalVotes) * 100,
    raw: marginVotes,
  };
}

function secondPlacePartyKey(seat) {
  const voteRows = Object.entries(seat?.votes || {})
    .map(([party, votes]) => ({ party, votes: Number(votes || 0) }))
    .filter((row) => row.votes > 0)
    .sort((a, b) => b.votes - a.votes);
  if (voteRows.length < 2) return null;
  return voteRows[1].party;
}

function clampNumber(value, minimum, maximum) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return minimum;
  return Math.max(minimum, Math.min(maximum, numeric));
}

function voteSharePct(seat, partyKey) {
  const totalVotes = totalVotesForSeat(seat);
  if (totalVotes <= 0) return 0;
  const partyVotes = Number(seat?.votes?.[partyKey] || 0);
  return (partyVotes / totalVotes) * 100;
}

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

function resetPrimaryFilters() {
  mapViewState.filterParty = 'all';
  mapViewState.filterRegion = 'all';
  mapViewState.filterSecondParty = 'all';
  mapViewState.majorityMin = 0;
  mapViewState.majorityMax = 100;
  mapViewState.gainsOnly = false;
  syncMapControlInputsFromState();
}

function populateMapControlOptions() {
  const partyRows = collectPartyKeysForControls();
  const regionRows = collectRegionsForControls();

  setSelectOptions(filterPartySelect, partyRows, 'all');
  setSelectOptions(filterSecondPartySelect, partyRows, 'all');
  setSelectOptions(choroplethPartySelect, partyRows, 'all');

  setSelectOptions(filterRegionSelect, regionRows, 'all');

  syncMapControlInputsFromState();
}

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

function renderMapWithViewState() {
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

  renderTopoMap(currentMapData, currentSeats, {
    visibleSeatKeys,
    choroplethConfig,
  });
  renderSeatList(visibleSeats, currentComparisonSeats);
  applySeatSearchSuggestions(buildSeatSearchIndex(visibleSeats));
  renderChoroplethLegend(choroplethConfig);

  if (seatPreview) {
    seatPreview.textContent = `Showing ${formatInt(visibleSeats.length)} of ${formatInt(currentSeats.length)} seats (filters).`;
  }
}

function hideSeatPopup() {
  if (!seatPopup) return;
  seatPopup.hidden = true;
}

function renderSeatPopup(seatName) {
  if (!seatPopup || !seatPopupTitle || !seatPopupMeta || !seatPopupList) return;

  const seatKey = seatLookupKey(seatName);
  const seat = currentSeatsByKey.get(seatKey);
  if (!seat) {
    hideSeatPopup();
    return;
  }

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

function toggleComparisonColumns(showComparison) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-comparison-cols', !showComparison);
}

function toggleVoteTotalColumns(showVoteTotals) {
  if (!voteTotalsTable) return;
  voteTotalsTable.classList.toggle('hide-vote-total-col', !showVoteTotals);
}

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

function setSelectedSeatRowByKey(seatKey) {
  const nextRow = seatListRowByKey.get(seatKey);
  if (!nextRow) return;

  if (selectedSeatRow && selectedSeatRow !== nextRow) {
    selectedSeatRow.classList.remove('is-selected');
  }
  nextRow.classList.add('is-selected');
  selectedSeatRow = nextRow;
}

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
  return names;
}

function applySeatSearchSuggestions(seatNames) {
  if (!seatSearchInput) return;

  let listEl = document.getElementById('mapsSeatSearchOptions');
  if (!listEl) {
    listEl = document.createElement('datalist');
    listEl.id = 'mapsSeatSearchOptions';
    document.body.appendChild(listEl);
  }

  seatSearchInput.setAttribute('list', 'mapsSeatSearchOptions');
  listEl.innerHTML = '';
  seatNames.forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    listEl.appendChild(option);
  });
}

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

function wireSeatSearch() {
  if (!seatSearchInput || seatSearchInput.dataset.wired === 'true') return;

  let lastSubmittedQuery = '';
  const submitSearch = () => {
    const query = String(seatSearchInput.value || '').trim();
    if (!query || query === lastSubmittedQuery) return;
    lastSubmittedQuery = query;
    selectSeatBySearchQuery(query);
  };

  seatSearchInput.addEventListener('change', submitSearch);
  seatSearchInput.addEventListener('blur', submitSearch);
  seatSearchInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      submitSearch();
    }
  });

  seatSearchInput.dataset.wired = 'true';
}

function syncRightPanelHeightToMap() {
  if (!mapsStage || !mapsPanelRight) return;

  const stageHeight = mapsStage.getBoundingClientRect().height;
  if (!Number.isFinite(stageHeight) || stageHeight <= 0) return;

  mapsPanelRight.style.height = `${Math.round(stageHeight)}px`;
  mapsPanelRight.style.maxHeight = `${Math.round(stageHeight)}px`;
}

function updateTopSummary(election, summary) {
  const top = summary.parties[0];
  const leadSeats = Number(top?.seats || 0);
  const totalSeats = Number(summary.totalSeats || 0);
  const majorityThreshold = totalSeats / 2;
  const hasMajority = leadSeats > majorityThreshold;
  const majority = hasMajority ? Math.round(2 * (leadSeats - majorityThreshold)) : 0;

  if (subtitle) {
    if (hasMajority) {
      subtitle.textContent = `${election.name} · ${labelParty(top?.party || 'others')} majority: ${majority}`;
    } else {
      subtitle.textContent = `${election.name} · Hung parliament - largest party ${labelParty(top?.party || 'others')} with ${formatInt(leadSeats)} seats`;
    }
  }

  if (voteMeta) {
    voteMeta.textContent = `United Kingdom · seats ${formatInt(summary.totalSeats)}`;
  }
}

function seatNameFromFeature(featureDatum) {
  const props = featureDatum?.properties || {};
  return props.name || props.seat_name || props.seat || props.constituency || props.Name || null;
}

function buildWinnerBySeat(seats) {
  const bySeat = new Map();
  seats.forEach((seat) => {
    if (!seat?.seat) return;
    bySeat.set(seat.seat, seat.winner || 'others');
    bySeat.set(String(seat.seat).toLowerCase(), seat.winner || 'others');
  });
  return bySeat;
}

function seatLookupKey(seatName) {
  return String(seatName || '').trim().toLowerCase();
}

function getInitialZoomTransform(width, height) {
  const scale = Math.max(1, Number(INITIAL_MAP_SCALE) || 1);
  const tx = width / 2 - scale * (width / 2);
  const ty = height / 2 - scale * (height / 2);
  return d3.zoomIdentity.translate(tx, ty).scale(scale);
}

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
      zoomValue.textContent = `${Math.round(event.transform.k * 100)}%`;
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

  svg.call(zoomBehavior.transform, initialTransform);
}

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

  if (filterPartySelect) filterPartySelect.dataset.wired = 'true';
}

function wireMapInteractions() {
  document.querySelectorAll('[data-map-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.getAttribute('data-map-action');
      if (action === 'zoom-in') mapInteractionController.zoomBy(1.2);
      if (action === 'zoom-out') mapInteractionController.zoomBy(0.83);
      if (action === 'reset-zoom' || action === 'reset-view') mapInteractionController.reset();
    });
  });
}

async function initElectionData() {
  const manifest = await fetchJson('data/elections.json');
  hydrateManifestSettings(manifest);
  const params = new URLSearchParams(window.location.search);
  const requestedId = params.get('election');

  let currentElection = manifest.elections.find((e) => e.id === requestedId);
  if (!currentElection) {
    currentElection = manifest.elections.find((e) => e.id === manifest.defaultElection) || manifest.elections[0];
  }

  if (!currentElection) {
    throw new Error('No elections configured in data/elections.json');
  }

  currentRegionLabelsByKey = buildRegionLabelLookup(currentElection.mapId);

  renderElectionLinks(manifest, currentElection.id);

  const { mapFile, dataFile } = resolveElectionFiles(manifest, currentElection);

  const [mapData, resultsData] = await Promise.all([
    fetchJson(`data/${mapFile}`),
    fetchJson(`data/${dataFile}`)
  ]);

  const seats = normalizeSeats(resultsData);
  const summary = summarizeElection(seats);
  const showVoteTotals = currentElection.type !== 'model_uns';
  currentElectionType = currentElection.type;
  currentSeats = seats;
  currentMapData = mapData;
  currentSeatsByKey = buildSeatIndex(seats);

  let comparisonSummary = null;
  let comparisonSeats = null;
  if (currentElection.comparisonElectionId) {
    const comparisonElection = manifest.elections.find((entry) => entry.id === currentElection.comparisonElectionId);
    if (comparisonElection) {
      const { dataFile: comparisonDataFile } = resolveElectionFiles(manifest, comparisonElection);
      const comparisonData = await fetchJson(`data/${comparisonDataFile}`);
      comparisonSeats = normalizeSeats(comparisonData);
      comparisonSummary = summarizeElection(comparisonSeats);
    }
  }

  currentComparisonSeats = comparisonSeats || [];
  comparisonSeatsByKey = buildSeatIndex(comparisonSeats || []);

  populateMapControlOptions();
  syncMapControlStateFromInputs();

  window.__mapsCurrentSummary = summary;
  window.__mapsComparisonSummary = comparisonSummary;
  window.__mapsShowVoteTotals = showVoteTotals;

  updateTopSummary(currentElection, summary);
  renderVoteTotals(summary, comparisonSummary, { showVoteTotals });
  renderMapWithViewState();
  syncRightPanelHeightToMap();
}

async function init() {
  wireMapInteractions();
  wirePopupPanels();
  wireMapViewControls();
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
  });

  try {
    await initElectionData();
  } catch (error) {
    if (subtitle) subtitle.textContent = 'Failed to load election data';
    if (voteMeta) voteMeta.textContent = error.message;
    if (seatList) {
      seatList.innerHTML = '<p>Unable to load configured election files.</p>';
    }
    console.error(error);
  }
}

init();

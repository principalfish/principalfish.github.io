// Shared mutable state for the electionmaps application.
// All modules import this object and mutate its properties directly.
// A single shared object reference means every importer sees the same state.

import { normalizeRegionKey } from './utils.js';

// ─── Init ────────────────────────────────────────────────────────────────────

export let manifest = null;

/**
 * Initialises shared state for a page load: sets the manifest, hydrates lookup maps,
 * and resolves initial URL params into state.
 * @param {object} manifestData - Raw manifest object from map-modes.json.
 * @returns {void}
 */
export function initState(manifestData) {
  manifest = manifestData;
  hydrateManifestSettings();
  setState();
}

/**
 * Resolves URL params and manifest defaults into the shared state object.
 * Called once per page load by initState, after the manifest is hydrated.
 * Throws if no elections are configured for the resolved parliament.
 * @returns {void}
 */
function setState() {
  const defaultParliament = manifest.elections.find((e) => e.id === manifest.defaultElection)?.parliament ?? '';
  state.currentParliament = getSearchParam('parliament') || defaultParliament;

  const requestedId = getSearchParam('election');
  const parliamentElections = manifest.elections.filter((e) => e.parliament === state.currentParliament);
  let currentElection = parliamentElections.find((e) => e.id === requestedId);
  if (!currentElection) {
    const parlConfig = manifest.parliamentFeatures[state.currentParliament] ?? {};
    const anchorId = parlConfig.predictAnchorElectionId;
    currentElection =
      (anchorId ? parliamentElections.find((e) => e.id === anchorId) : null)
      || parliamentElections.find((e) => e.id === manifest.defaultElection)
      || parliamentElections[0];
  }

  if (!currentElection) {
    throw new Error('No elections configured in data/map-modes.json');
  }

  state.currentElection = { ...currentElection };
}

/**
 * Normalises missing manifest fields and populates party and region lookup maps
 * from the manifest's top-level `parties` array and per-map `regions` in `mapModes`.
 * @returns {void}
 */
function hydrateManifestSettings() {
  manifest.mapModes ??= {};
  manifest.parliamentFeatures ??= {};
  manifest.parties ??= [];
  manifest.files ??= {};
  manifest.files.elections ??= {};
  manifest.files.elections.mapsById ??= {};
  manifest.files.elections.electionsById ??= {};
  manifest.files.meta ??= {};

  // manifest.partiesByKey — plain object keyed by party.key string (e.g. "labour").
  // Used for display lookups: name and colour given a key already known from seat data.
  // manifest.partiesById — Map keyed by numeric party.id from the DB.
  // Used during data normalisation to resolve raw [partyId, votes] pairs into party keys.
  manifest.partiesByKey = {};
  manifest.partiesById = new Map();
  manifest.parties.forEach((party) => {
    const id = Number(party?.id);
    if (!Number.isFinite(id)) return;
    manifest.partiesById.set(id, party);
    const key = party?.key;
    if (key && !manifest.partiesByKey[key]) manifest.partiesByKey[key] = party;
  });

  // manifest.regionsById — Map keyed by numeric region.id.
  // Used during seat normalisation to resolve a region ID to its normalised key string.
  // manifest.regionsByMapId — plain object keyed by mapId string.
  // Used to build per-election region label lookups for the filter UI.
  manifest.regionsById = new Map();
  manifest.regionsByMapId = {};
  Object.entries(manifest.mapModes).forEach(([mapId, mapMode]) => {
    const regionRows = mapMode.regions || [];
    manifest.regionsByMapId[mapId] = regionRows;
    regionRows.forEach((region) => {
      const id = Number(region?.id);
      if (!Number.isFinite(id)) return;
      manifest.regionsById.set(id, normalizeRegionKey(region?.name || ''));
    });
  });
}

// ─── Search params ────────────────────────────────────────────────────────────

/**
 * Returns a URLSearchParams object parsed from the current page URL.
 * @returns {URLSearchParams}
 */
export function getSearchParams() {
  return new URLSearchParams(window.location.search);
}

/**
 * Returns the value of a URL search param from the current page URL, or null if not present.
 * @param {string} key - Query parameter name.
 * @returns {string|null}
 */
export function getSearchParam(key) {
  return getSearchParams().get(key);
}

// ─── Application state ────────────────────────────────────────────────────────

export const _state = {
  // Sort / UI / totals
  currentSort: { key: 'seats', direction: 'desc' },
  voteTotalsExpanded: false,
  voteTotalsMode: 'all',
  hiddenVoteTotalsParties: new Set(),
  currentSeatView: 'seats',
  selectedSeatRow: null,
  activeSeatPathNode: null,
  currentOpenSeatName: null,

  // Election / seat data
  currentSeats: [],
  currentComparisonSeats: [],
  baseElectionSeats: [],
  defaultComparisonSeats: [],
  defaultComparisonSummary: null,
  currentSeatsByKey: new Map(),
  comparisonSeatsByKey: new Map(),
  currentSeatNameByKey: new Map(),
  seatListRowByKey: new Map(),
  currentRegionLabelsByKey: new Map(),
  currentMapData: null,

  // Map filters / choropleth
  mapViewState: {
    filterParty: 'all',
    filterRegion: 'all',
    filterSecondParty: 'all',
    majorityMin: 0,
    majorityMax: 100,
    gainsOnly: false,
    choroplethType: 'none',
    choroplethParty: 'all',
  },

  // Map interaction controller — replaced by renderTopoMap
  mapInteractionController: {
    zoomBy: () => {},
    reset: () => {},
    clearSelection: () => {},
    highlightSeat: () => {},
    zoomToSeat: () => false,
    flashRegion: () => {},
  },

  // Search
  seatSearchNames: [],
  seatSearchSuggestions: [],
  seatSearchSuggestionIndex: -1,
  seatSearchMenuEl: null,
  postcodeErrorTimeout: null,

  // Predict mode
  predictModeActive: false,
  predictModeLinkEl: null,
  predictBaseSeats: [],
  predictBaseSeatsByKey: new Map(),
  predictBaseMapData: null,
  predictBaseRegionLabelsByKey: new Map(),
  predictColumnPartyKeys: [],
  predictInputByRegionParty: new Map(),
  predictBaselineShareByRegionParty: new Map(),
  predictRegionalSwingsByParty: new Map(),
  predictEnglandExpanded: false,
  predictOtherCellByRegion: new Map(),
  predictHolyroodTab: 'constituency',
  predictHolyroodRegionsExpanded: false,
  predictConstInputByRegionParty: new Map(),
  predictListInputByRegionParty: new Map(),
  predictNationalBaselines: new Map(),
  predictNationalListBaselines: new Map(),
  predictBaselineConstShareByRegionParty: new Map(),
  predictBaselineListShareByRegionParty: new Map(),
  predictHolyroodConstSwingsByParty: new Map(),
  predictHolyroodListSwingsByParty: new Map(),
  predictCurrentSimulationLoaded: false,
  predictCurrentSimulationSeats: [],
  predictCurrentSimulationConstShares: new Map(),
  predictCurrentSimulationListShares: new Map(),

  // Poll tracker
  pollTrackerModeActive: false,
  pollTrackerModeLinkEl: null,
  pollTrackerDataLoaded: false,
  pollTrackerTimeline: [],
  pollTrackerSeriesByParty: new Map(),
  pollTrackerRangeSelection: 'all',
  predictionSnippet: null,

  // Misc
  countdownIntervalId: null,
  lastTrackedVirtualPagePath: '',
};

// ─── Current ─────────────────────────────────────────────────────────────────

/**
 * Current election context — updated on every election navigation.
 * Imported by any module that needs to read the active election identity.
 */
export const state = {
  /**
   * The currently loaded election, spread from its manifest entry with one computed addition.
   * Null before the first load. Sub-properties:
   * - id {string} — unique election key (e.g. 'general-election-2024')
   * - name {string} — display label shown in the nav
   * - type {string} — 'uk_general' | 'holyrood_general' | 'holyrood_uns' | 'model_uns';
   *     mutated to 'model_uns' or 'holyrood_uns' when predict mode activates
   * - parliament {string} — 'westminster' | 'holyrood'
   * - mapId {number} — key into manifest.mapModes for topology and region config
   * - model {boolean|undefined} — true only on prediction elections; gates snippet fetch and predict mode
   * - comparisonElectionId {string|undefined} — id of the election used for swing data
   * - byElectionSeats {string[]|undefined} — constituency name list from the manifest; use getByElectionSeatsSet() for Set form
   */
  currentElection: null,

  /** Parliament key for the currently active parliament tab ('westminster' | 'holyrood').
   * Resolved from the ?parliament= URL param on load, falling back to the manifest defaultElection's parliament. */
  currentParliament: '',
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Returns the manifest election entry for the given id, or undefined if not found.
 * @param {string} id - Election id to look up.
 * @returns {object|undefined}
 */
export function getElectionFromId(id) {
  return manifest.elections.find((e) => e.id === id);
}

/**
 * Returns a Set of by-election constituency names for the current election, or null if none.
 * @returns {Set<string>|null}
 */
export function getByElectionSeatsSet() {
  const seats = state.currentElection?.byElectionSeats;
  return seats?.length ? new Set(seats) : null;
}

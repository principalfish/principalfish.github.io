// Shared mutable state for the electionmaps application.
// All modules import these objects and mutate their properties directly.
// A single shared object reference means every importer sees the same state.

import { normalizeRegionKey } from './utils.js';
import { fetchJson } from './files.js';

// ─── Manifest ─────────────────────────────────────────────────────────────────

class Manifest {
  constructor() {
    this.elections = [];
    this.defaultElection = null;
    this.mapModes = {};
    this.parliamentFeatures = {};
    this.parties = [];
    this.files = {};
    this.misc = {};
    this.partiesByKey = {};
    this.partiesById = new Map();
    this.regionsById = new Map();
    this.regionsByMapId = {};
  }

  /**
   * Populates the manifest from raw JSON data and hydrates lookup maps.
   * @param {object} data - Raw manifest object from map-modes.json.
   * @returns {void}
   */
  init(data) {
    Object.assign(this, data);
    this.#hydrate();
  }

  /**
   * Normalises missing manifest fields and populates party and region lookup maps
   * from the manifest's top-level `parties` array and per-map `regions` in `mapModes`.
   * @returns {void}
   */
  #hydrate() {
    this.mapModes ??= {};
    this.parliamentFeatures ??= {};
    this.parties ??= [];
    this.files ??= {};
    this.files.elections ??= {};
    this.files.elections.mapsById ??= {};
    this.files.elections.electionsById ??= {};
    this.files.meta ??= {};

    // manifest.partiesByKey — plain object keyed by party.key string (e.g. "labour").
    // Used for display lookups: name and colour given a key already known from seat data.
    // manifest.partiesById — Map keyed by numeric party.id from the DB.
    // Used during data normalisation to resolve raw [partyId, votes] pairs into party keys.
    this.partiesByKey = {};
    this.partiesById = new Map();
    this.parties.forEach((party) => {
      const id = Number(party?.id);
      if (!Number.isFinite(id)) return;
      this.partiesById.set(id, party);
      const key = party?.key;
      if (key && !this.partiesByKey[key]) this.partiesByKey[key] = party;
    });

    // manifest.regionsById — Map keyed by numeric region.id.
    // Used during seat normalisation to resolve a region ID to its normalised key string.
    // manifest.regionsByMapId — plain object keyed by mapId string.
    // Used to build per-election region label lookups for the filter UI.
    this.regionsById = new Map();
    this.regionsByMapId = {};
    Object.entries(this.mapModes).forEach(([mapId, mapMode]) => {
      const regionRows = mapMode.regions || [];
      this.regionsByMapId[mapId] = regionRows;
      regionRows.forEach((region) => {
        const id = Number(region?.id);
        if (!Number.isFinite(id)) return;
        this.regionsById.set(id, normalizeRegionKey(region?.name || ''));
      });
    });
  }

  /**
   * Returns the manifest election entry for the given id, or undefined if not found.
   * @param {string} id - Election id to look up.
   * @returns {object|undefined}
   */
  getElectionFromId(id) {
    return this.elections.find((e) => e.id === id);
  }

  /**
   * Returns the parliament key for the manifest's default election.
   * @returns {string}
   */
  defaultParliament() {
    return this.elections.find((e) => e.id === this.defaultElection)?.parliament ?? '';
  }

  /**
   * Returns all elections for the given parliament.
   * @param {string} parliament - Parliament key ('westminster' | 'holyrood').
   * @returns {object[]}
   */
  electionsForParliament(parliament) {
    return this.elections.filter((e) => e.parliament === parliament);
  }

  /**
   * Returns the display label for a party, or the raw key if not found.
   * @param {string} partyKey - Canonical party key (e.g. "labour").
   * @returns {string}
   */
  labelParty(partyKey) {
    return this.partiesByKey?.[partyKey]?.name ?? partyKey;
  }

  /**
   * Returns the hex colour for a party, or a grey fallback if not found.
   * @param {string} partyKey - Canonical party key (e.g. "labour").
   * @returns {string}
   */
  colourParty(partyKey) {
    return this.partiesByKey?.[partyKey]?.colour ?? '#9CA3AF';
  }

  /**
   * Fetches the parliament meta file and returns the latest poll snippet string, or null on failure.
   * @param {string} parliament - Parliament key ('westminster' | 'holyrood').
   * @returns {Promise<string|null>}
   */
  async fetchPredictionMeta(parliament) {
    try {
      const payload = await fetchJson(`data/${this.files.meta[parliament]}`);
      return String(payload?.latest_poll_snippet || '').trim();
    } catch {
      return null;
    }
  }
}

export const manifest = new Manifest();

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
};

// ─── Current ─────────────────────────────────────────────────────────────────

class AppState {
  constructor() {
    /** Active view name for the current page load ('election' | 'predict' | 'polltracker').
     * Set by initState from the ?view= URL param, defaulting to 'election'. */
    this.view = 'election';

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
     * - byElectionSeats {string[]|undefined} — constituency name list from the manifest; use state.getByElectionSeatsSet() for Set form
     */
    this.currentElection = null;

    /** All elections belonging to the current parliament, filtered from manifest.elections.
     * Set by setState on every page load alongside currentParliament. */
    this.parliamentElections = [];

    /** Parliament key for the currently active parliament tab ('westminster' | 'holyrood').
     * Resolved from the ?parliament= URL param on load, falling back to the manifest defaultElection's parliament. */
    this.currentParliament = '';

    /** Latest poll snippet text for the current parliament, used in subtitle rendering.
     * Empty string until fetched or if the fetch failed. */
    this.predictionSnippet = '';

    /** Parsed poll tracker data: a dense daily timeline plus per-party seats/votePct series.
     * Populated by assigning to state.pollTrackerData when poll tracker mode activates; empty until then. */
    this.pollTrackerData = { timeline: [], seriesByParty: new Map() };
  }

  /**
   * Resolves URL params and manifest defaults into state.
   * Called once per page load by initState, after the manifest is hydrated.
   * Throws if no elections are configured for the resolved parliament.
   * @param {string} view - Active view name ('election' | 'predict' | 'polltracker').
   * @returns {Promise<void>}
   */
  async init(view) {
    this.view = view;
    this.currentParliament = getSearchParam('parliament') || manifest.defaultParliament();

    const requestedId = getSearchParam('election');
    const parliamentElections = manifest.electionsForParliament(this.currentParliament);
    this.parliamentElections = parliamentElections;
    let currentElection = parliamentElections.find((e) => e.id === requestedId);
    if (!currentElection) {
      // No ?election= param, or it named an election that doesn't exist in this parliament.
      // Prefer the predict anchor (the live/current election for this parliament) so that
      // bare parliament-tab clicks land on the most relevant view rather than an arbitrary
      // historical election. Fall back to the manifest default, then the first in the list.
      const anchorId = this.getPredictAnchorElectionId();
      currentElection =
        (anchorId ? parliamentElections.find((e) => e.id === anchorId) : null)
        || parliamentElections.find((e) => e.id === manifest.defaultElection)
        || parliamentElections[0];
    }

    if (!currentElection) {
      throw new Error('No elections configured in data/map-modes.json');
    }

    this.currentElection = { ...currentElection };

    // Fetch prediction snippet for model elections and poll tracker, where subtitle text references it.
    if (this.view === 'polltracker' || this.currentElection.model) {
      this.predictionSnippet = (await manifest.fetchPredictionMeta(this.currentParliament)) ?? '';
    }
  }

  /**
   * Returns a Set of by-election constituency names for the current election, or null if none.
   * @returns {Set<string>|null}
   */
  getByElectionSeatsSet() {
    const seats = this.currentElection?.byElectionSeats;
    return seats?.length ? new Set(seats) : null;
  }

  #parlConfig() {
    return manifest.parliamentFeatures[this.currentParliament] ?? {};
  }

  /**
   * Returns the predict anchor election id for the current parliament, or undefined if not set.
   * @returns {string|undefined}
   */
  getPredictAnchorElectionId() {
    return this.#parlConfig().predictAnchorElectionId;
  }

  /**
   * Returns the predict baseline election id for the current parliament, or undefined if not set.
   * @returns {string|undefined}
   */
  getPredictBaselineElectionId() {
    return this.#parlConfig().predictBaselineElectionId;
  }

  /**
   * Returns true when the election countdown should be visible.
   * The countdown shows only for the Holyrood UNS prediction when poll tracker mode is not active.
   * @returns {boolean}
   */
  shouldShowCountdown() {
    // TODO: generalise to support countdown for multiple concurrent elections
    return this.currentElection.type === 'holyrood_uns';
  }

  /**
   * Returns a query string URL for the given view in the current parliament.
   * @param {'election'|'predict'|'polltracker'} view - Target view.
   * @param {string} [electionId] - Election id; only used when view is 'election'.
   * @returns {string} Query string URL (e.g. '?view=election&election=2024&parliament=westminster').
   */
  viewUrl(view, electionId) {
    const electionPart = view === 'election' && electionId
      ? `&election=${encodeURIComponent(electionId)}`
      : '';
    return `?view=${view}${electionPart}&parliament=${this.currentParliament}`;
  }
}

export const state = new AppState();

// ─── Init ────────────────────────────────────────────────────────────────────

/**
 * Initialises shared state for a page load: sets the manifest, hydrates lookup maps,
 * and resolves initial URL params into state.
 * @param {object} manifestData - Raw manifest object from map-modes.json.
 * @param {string} view - Active view name ('election' | 'predict' | 'polltracker').
 * @returns {Promise<void>}
 */
export async function initState(manifestData, view) {
  manifest.init(manifestData);
  await state.init(view);
}


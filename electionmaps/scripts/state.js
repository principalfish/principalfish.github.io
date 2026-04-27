// Shared mutable state for the electionmaps application.
// All modules import these objects and mutate their properties directly.
// A single shared object reference means every importer sees the same state.

import { normalizeRegionKey, titleCaseFromRegionKey } from './utils.js';
import { fetchJson } from './files.js';
// Transitional: these handlers will be moved out of electionmaps.js eventually.
import { summarizeElection } from '../electionmaps.js';

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
   * Resolves a raw party reference (integer party_id or string key) to a canonical party key.
   * Numeric refs are looked up in `this.partiesById` and the party's `.key` is returned.
   * String refs are lowercased and returned as-is. Empty/unknown input becomes `'others'`.
   * @param {number|string} ref - Raw party reference from results data.
   * @returns {string} Canonical party key.
   */
  resolvePartyRef(ref) {
    const num = Number(ref);
    if (Number.isFinite(num) && num > 0) {
      const party = this.partiesById?.get(num);
      if (party?.key) return party.key;
      return String(num);
    }
    const raw = String(ref || '').trim().toLowerCase();
    return raw || 'others';
  }

  /**
   * Resolves the mapFile, dataFile, and (if configured) comparisonDataFile paths for an election.
   * @param {object} election - Election entry with `id`, `mapId`, and optional `comparisonElectionId`.
   * @returns {{mapFile: string, dataFile: string, comparisonDataFile: string|null}}
   * @throws {Error} When either the mapFile or dataFile path cannot be determined.
   */
  resolveElectionFiles(election) {
    const mapFile = this.files.elections.mapsById[String(election.mapId)];
    const dataFile = this.files.elections.electionsById[election.id];
    if (!mapFile || !dataFile) {
      throw new Error(`Missing file configuration for election ${election?.id || 'unknown'}`);
    }
    const comparisonDataFile = election.comparisonElectionId
      ? this.files.elections.electionsById[election.comparisonElectionId] ?? null
      : null;
    return { mapFile, dataFile, comparisonDataFile };
  }

  /**
   * Returns a Map from normalised region key to display label for the given mapId.
   * @param {string|number} mapId - Map identifier to look up in regionsByMapId.
   * @returns {Map<string, string>}
   */
  buildRegionLabelLookup(mapId) {
    const lookup = new Map();
    const regionRows = this.regionsByMapId?.[String(mapId)] || [];
    regionRows.forEach((region) => {
      const key = normalizeRegionKey(region?.name || '');
      if (!key) return;
      lookup.set(key, region.name);
    });
    return lookup;
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
  currentComparisonSeats: [],
  comparisonSeatsByKey: new Map(),
  currentSeatNameByKey: new Map(),
  seatListRowByKey: new Map(),

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

// ─── Seat ────────────────────────────────────────────────────────────────────

/**
 * A normalised seat record. The constructor takes a seat-shaped object (used for cloning);
 * use {@link Seat.fromRaw} to build from a raw pf-results-v4 seat (compact `{n, r, w, p}`
 * shape). In both paths party keys are normalised through the manifest and zero/negative
 * vote entries are dropped.
 */
export class Seat {
  /**
   * @param {{seat: string, region: string, winner: (string|number), votes: object}} input
   *   Normalised seat-shaped object. For raw JSON input, use {@link Seat.fromRaw} instead.
   */
  constructor(input) {
    this.seat = input?.seat || 'Unknown seat';
    this.region = String(input?.region || 'unknown');
    this.winner = manifest.resolvePartyRef(input?.winner ?? 'others');
    this.votes = Seat.#normalizeVotes(input?.votes);
  }

  /**
   * Builds a Seat from a raw pf-results-v4 compact seat record.
   * @param {{n: string, r: (number|string), w: (number|string), p: Array<[number|string, number]>}} rawSeat
   * @returns {Seat}
   */
  static fromRaw(rawSeat) {
    return new Seat({
      seat: rawSeat?.n,
      region: Seat.#resolveRegion(rawSeat?.r),
      winner: rawSeat?.w,
      votes: Seat.#decodeCompactVotes(rawSeat?.p),
    });
  }

  static #resolveRegion(raw) {
    if (typeof raw === 'number' && manifest.regionsById?.size) {
      return manifest.regionsById.get(raw) || 'unknown';
    }
    return String(raw || 'unknown');
  }

  static #decodeCompactVotes(p) {
    const votes = {};
    if (!Array.isArray(p)) return votes;
    p.forEach((entry) => {
      if (!Array.isArray(entry) || entry.length < 2) return;
      const partyKey = manifest.resolvePartyRef(entry[0]);
      const voteTotal = Number(entry[1] || 0);
      if (!partyKey || voteTotal <= 0) return;
      votes[partyKey] = (votes[partyKey] || 0) + voteTotal;
    });
    return votes;
  }

  static #normalizeVotes(rawVotes) {
    const votes = {};
    Object.entries(rawVotes || {}).forEach(([partyKey, value]) => {
      const voteTotal = Number(value || 0);
      if (voteTotal <= 0) return;
      const key = manifest.resolvePartyRef(partyKey);
      votes[key] = (votes[key] || 0) + voteTotal;
    });
    return votes;
  }
}

// ─── Election data ───────────────────────────────────────────────────────────

/**
 * Parsed seat data for a single election load. Used for both the active election and the
 * comparison election — the shape is symmetrical.
 */
export class ElectionData {
  constructor(resultsData) {
    /** Pristine normalised seat records as parsed from the results JSON. Never mutated;
     * use as the source of truth when rebuilding currentSeats from baseline. */
    this.baseSeats = ElectionData.normalizeSeats(resultsData);

    /** Mutable clone of baseSeats. Predict mode and other features write back into this list,
     * so it diverges from baseSeats over time within a single election load. */
    this.currentSeats = this.baseSeats.map((seat) => new Seat(seat));

    /** Map from seat lookup key to currentSeats entry, rebuilt whenever currentSeats is replaced. */
    this.seatsByKey = ElectionData.buildSeatIndex(this.currentSeats);
  }

  /**
   * Normalizes raw pf-results-v4 results data into a canonical array of {@link Seat} records.
   * Returns an empty array if `resultsData.seats` is missing or not an array — never throws
   * on malformed input.
   *
   * @param {object} resultsData - Parsed results JSON. Expected to have a `seats` array;
   *   anything else returns `[]`.
   * @returns {Seat[]} Normalised seat records, one per entry in `resultsData.seats`.
   */
  static normalizeSeats(resultsData) {
    if (!Array.isArray(resultsData?.seats)) return [];
    return resultsData.seats.map((seat) => Seat.fromRaw(seat));
  }

  /**
   * Builds a Map from seat lookup key (trimmed lowercase seat name) to seat for fast lookups.
   * Mirrors the standalone `buildSeatIndex` in electionmaps.js — duplicated here so state.js
   * has no dependency on electionmaps.js. Callers in electionmaps.js should migrate to this.
   * @param {Array<{seat: string}>} seats - Seat-shaped objects with a `seat` name property.
   * @returns {Map<string, object>} Map from lowercase seat name key to seat object.
   */
  static buildSeatIndex(seats) {
    const byKey = new Map();
    (seats || []).forEach((seat) => {
      if (!seat?.seat) return;
      byKey.set(String(seat.seat).trim().toLowerCase(), seat);
    });
    return byKey;
  }
}

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

    /** Map from normalised region key to display label for the current election.
     * Built from manifest.regionsByMapId on each election load; used by getRegionLabel. */
    this.currentRegionLabelsByKey = new Map();

    /** Seat records for the comparison election as loaded from JSON, before any cloning.
     * Reset to [] on each election load; populated after comparison data fetch completes. */
    this.defaultComparisonSeats = [];

    /** Summary object for the default comparison election (seat totals, vote share etc).
     * Reset to null on each election load; populated alongside defaultComparisonSeats. */
    this.defaultComparisonSummary = null;

    /** True if the current election is a referendum, as flagged in the manifest.
     * Controls visibility of gains/choropleth controls and data info button. */
    this.isReferendumType = false;

    /** UI flags for the vote totals panel.
     * - votes: whether the raw vote count column is visible.
     *   Initialised false for model elections and referendum-type elections; true otherwise.
     *   Also toggled at runtime when switching vote totals tabs or entering predict mode.
     */
    this.voteTotals = { votes: true };

    /** Raw topology JSON for the current election, used by renderTopoMap. Null until loaded. */
    this.mapData = null;

    /** Parsed seat data for the current election. ElectionData instance, or null until loaded. */
    this.electionData = null;

    /** Parsed seat data for the comparison election. ElectionData instance, or null when no comparison data. */
    this.comparisonElectionData = null;
  }

  /**
   * Resolves manifest defaults into state for the given URL-derived inputs.
   * Called once per page load by initState, after the manifest is hydrated.
   * Throws if no elections are configured for the resolved parliament.
   * @param {string} view - Active view name ('election' | 'predict' | 'polltracker').
   * @param {string|null} parliament - Requested parliament from the URL, or null to use the manifest default.
   * @param {string|null} requestedId - Requested election id from the URL, or null.
   * @returns {Promise<void>}
   */
  async init(view, parliament, requestedId) {
    this.view = view;
    this.currentParliament = parliament || manifest.defaultParliament();

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
    this.currentRegionLabelsByKey = manifest.buildRegionLabelLookup(this.currentElection.mapId);
    this.isReferendumType = !!this.currentElection.referendum;
    if (this.currentElection.model || this.isReferendumType) {
      this.voteTotals.votes = false;
    }

    // Fetch prediction snippet for model elections and poll tracker, where subtitle text references it.
    if (this.view === 'polltracker' || this.currentElection.model) {
      this.predictionSnippet = (await manifest.fetchPredictionMeta(this.currentParliament)) ?? '';
    }
  }

  /**
   * Returns whether the named column should be visible in the vote totals panel.
   * @param {string} column - Column key in this.voteTotals (e.g. 'votes').
   * @returns {boolean}
   */
  voteTotalsColumnVisible(column) {
    return !!this.voteTotals[column];
  }

  /**
   * Stores the freshly fetched topology as state.mapData.
   * @param {object} topology - Topology JSON for the active election.
   * @returns {void}
   */
  initMapData(topology) {
    this.mapData = topology;
  }

  /**
   * Builds an ElectionData instance for the active election and stores it as state.electionData.
   * @param {object} resultsData - Raw results JSON for the active election.
   * @returns {void}
   */
  initElectionData(resultsData) {
    this.electionData = new ElectionData(resultsData);
  }

  /**
   * Builds an ElectionData instance for the comparison election and stores it as state.comparisonElectionData.
   * Transitional: mirrors the comparison seats / index onto _state.
   * @param {object} comparisonData - Raw results JSON for the comparison election.
   * @returns {void}
   */
  initComparisonElectionData(comparisonData) {
    this.comparisonElectionData = new ElectionData(comparisonData);
    this.defaultComparisonSeats = this.comparisonElectionData.baseSeats;
    this.defaultComparisonSummary = summarizeElection(this.comparisonElectionData.baseSeats);
    _state.currentComparisonSeats = this.defaultComparisonSeats.map((seat) => new Seat(seat));
    _state.comparisonSeatsByKey = ElectionData.buildSeatIndex(_state.currentComparisonSeats);
  }

  /**
   * Returns a Set of by-election constituency names for the current election, or null if none.
   * @returns {Set<string>|null}
   */
  getByElectionSeatsSet() {
    const seats = this.currentElection?.byElectionSeats;
    return seats?.length ? new Set(seats) : null;
  }

  /**
   * Returns the display label for a region key, using the current election's region lookup
   * with a title-cased fallback for unknown keys.
   * @param {string} regionKey - Raw or normalised region key.
   * @returns {string}
   */
  getRegionLabel(regionKey) {
    const normalized = normalizeRegionKey(regionKey);
    if (!normalized) return 'Unknown';
    const label = this.currentRegionLabelsByKey.get(normalized) || titleCaseFromRegionKey(regionKey);
    return label.replace(/ and /gi, ' & ');
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
  const params = new URLSearchParams(window.location.search);
  await state.init(view, params.get('parliament'), params.get('election'));
}


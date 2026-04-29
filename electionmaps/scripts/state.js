// Shared mutable state for the electionmaps application.
// All modules import these objects and mutate their properties directly.
// A single shared object reference means every importer sees the same state.

import { normalizeRegionKey, formatInt, seatLookupKey, getRegionLabel } from './utils.js';
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

      // Guarantee a non-empty default tab so callers can read mapMode.voteTotalsViews[0].id and
      // mapMode.seatViews[0].id without per-call-site fallbacks. The tab nav hides itself when
      // length <= 1, so a single synthetic default is invisible to the user.
      if (!mapMode.voteTotalsViews?.length) {
        mapMode.voteTotalsViews = [{ id: 'all', label: 'Overall' }];
      }
      if (!mapMode.seatViews?.length) {
        mapMode.seatViews = [{ id: 'seats', label: 'Seats' }];
      }
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
   * Returns the per-parliament feature config (predict anchor/baseline election ids etc.),
   * or an empty object when no entry exists for the given parliament.
   * @param {string} parliament - Parliament key ('westminster' | 'holyrood').
   * @returns {object}
   */
  parliamentConfig(parliament) {
    return this.parliamentFeatures[parliament] ?? {};
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


// ─── Application state ────────────────────────────────────────────────────────

export const _state = {
  // Sort / UI / totals
  currentSort: { key: 'seats', direction: 'desc' },
  voteTotalsExpanded: false,
  hiddenVoteTotalsParties: new Set(),
  selectedSeatRow: null,
  activeSeatPathNode: null,
  currentOpenSeatName: null,

  // Election / seat data
  currentComparisonSeats: [],
  comparisonSeatsByKey: new Map(),
  currentSeatNameByKey: new Map(),
  seatListRowByKey: new Map(),

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

  /**
   * Resolves a raw region reference (integer region.id or string key) to a region key string.
   * Numeric refs are looked up in `manifest.regionsById`; unrecognised numbers and missing
   * values fall through to `'unknown'`. String refs pass through `String(...)` unchanged.
   * @param {number|string|null|undefined} raw - Raw region reference from a seat record.
   * @returns {string} Normalised region key, or `'unknown'` when the ref cannot be resolved.
   */
  static #resolveRegion(raw) {
    if (typeof raw === 'number' && manifest.regionsById?.size) {
      return manifest.regionsById.get(raw) || 'unknown';
    }
    return String(raw || 'unknown');
  }

  /**
   * Decodes the compact `p` array from a raw pf-results-v4 seat into a `{ partyKey: votes }`
   * object. Each entry is `[partyRef, voteTotal]`; party refs are resolved via the manifest
   * and entries with non-positive totals are dropped. Duplicate party keys (after
   * normalisation) are summed.
   * @param {Array<[number|string, number]>|undefined} p - Compact party-vote pairs.
   * @returns {Object<string, number>} Map of canonical party key to total votes.
   */
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

  /**
   * Normalises a votes object on the clone path: re-resolves each key through the manifest,
   * coerces values to numbers, drops non-positive totals, and sums any duplicate keys that
   * collapse to the same canonical form.
   * @param {Object<string, number>|undefined} rawVotes - Existing votes object from a seat.
   * @returns {Object<string, number>} Map of canonical party key to total votes.
   */
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

  // ── Seat data utilities ────────────────────────────────────────────────────

  /**
   * Returns the total votes cast in this seat, using the explicit turnout field if available,
   * otherwise summing all party vote totals.
   * @returns {number} Total votes cast in the seat.
   */
  totalVotes() {
    const turnout = Number(this?.turnout || 0);
    if (turnout > 0) return turnout;
    return Object.values(this.votes || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  }

  /**
   * Returns an array of { party, votes } objects for this seat, sorted descending by vote count,
   * excluding parties with zero votes.
   * @returns {Array<{party: string, votes: number}>}
   */
  #sortedVoteRows() {
    return Object.entries(this.votes || {})
      .map(([party, votes]) => ({ party, votes: Number(votes || 0) }))
      .filter((row) => row.votes > 0)
      .sort((a, b) => b.votes - a.votes);
  }

  /**
   * Returns { pct, raw } for the winning majority in this seat: pct as a percentage of total
   * votes, raw as the vote margin between first and second place.
   * @returns {{pct: number, raw: number}}
   */
  majorityStats() {
    const voteRows = this.#sortedVoteRows();
    if (voteRows.length < 2) return { pct: 0, raw: 0 };
    const marginVotes = voteRows[0].votes - voteRows[1].votes;
    const totalVotes = this.totalVotes();
    if (totalVotes <= 0) return { pct: 0, raw: marginVotes };
    return { pct: (marginVotes / totalVotes) * 100, raw: marginVotes };
  }

  /**
   * Returns the previous winner's party key if this seat changed hands, or null if there was
   * no change or no comparison available.
   * @param {string|null} comparisonSeatWinner - The winning party key from the comparison seat, or null.
   * @returns {string|null}
   */
  gainFromParty(comparisonSeatWinner) {
    const winner = this.winner || 'others';
    const previousWinner = comparisonSeatWinner || null;
    if (!previousWinner || previousWinner === winner) return null;
    return previousWinner;
  }

  /**
   * Returns the party key of the second-place finisher in this seat, or null if fewer than two
   * parties have votes.
   * @returns {string|null}
   */
  #secondPlaceParty() {
    const voteRows = this.#sortedVoteRows();
    if (voteRows.length < 2) return null;
    return voteRows[1].party;
  }

  /**
   * Returns true when this seat passes all active primary filters.
   * @param {object|null} comparisonSeat - Comparison seat for gains filtering; may be null.
   * @param {{party: string, region: string, secondParty: string, majorityMin: number, majorityMax: number, gainsOnly: boolean}} filterState - Active filter configuration.
   * @param {Set<string>|null} byElectionSeats - Set of seat names for by-election gain filtering, or null.
   * @returns {boolean} True if the seat passes all currently active filters.
   */
  matchesPrimaryFilters(comparisonSeat, filterState, byElectionSeats) {
    if (filterState.party !== 'all') {
      const winner = this.winner === 'other' ? 'others' : this.winner;
      if (winner !== filterState.party) return false;
    }

    if (filterState.region !== 'all') {
      const seatRegion = normalizeRegionKey(this.region);
      if (seatRegion !== filterState.region) return false;
    }

    const majority = this.majorityStats().pct;
    if (majority < filterState.majorityMin || majority > filterState.majorityMax) return false;

    if (filterState.secondParty !== 'all') {
      const secondParty = this.#secondPlaceParty();
      if (secondParty !== filterState.secondParty) return false;
    }

    if (filterState.gainsOnly) {
      if (byElectionSeats) {
        if (!byElectionSeats.has(this.seat)) return false;
      } else {
        const gainFrom = this.gainFromParty(comparisonSeat?.winner);
        if (!gainFrom) return false;
      }
    }

    return true;
  }
}

// ─── Election data ───────────────────────────────────────────────────────────

/**
 * Parsed seat data for a single election load. Used for both the active election and the
 * comparison election — the shape is symmetrical.
 */
export class ElectionData {
  constructor(resultsData, electionName = null) {
    /** Display label of the election this data represents (e.g. "2024 General Election").
     * Used as the prefix in {@link ElectionData#generateSubtitleSummaryText}. Null for the
     * comparison ElectionData instance, which never renders a subtitle. */
    this.electionName = electionName;

    /** Pristine normalised seat records as parsed from the results JSON. Never mutated;
     * use as the source of truth when rebuilding currentSeats from baseline. */
    this.baseSeats = ElectionData.normalizeSeats(resultsData);

    /** Mutable clone of baseSeats. Predict mode and other features write back into this list,
     * so it diverges from baseSeats over time within a single election load. */
    this.currentSeats = this.baseSeats.map((seat) => new Seat(seat));

    /** Map from seat lookup key to currentSeats entry, rebuilt whenever currentSeats is replaced. */
    this.seatsByKey = ElectionData.buildSeatIndex(this.currentSeats);

    /** Aggregated summary of currentSeats. Populated by AppState#initElectionData /
     * #initComparisonElectionData after construction; recompute via
     * {@link ElectionData#summarizeElection} when currentSeats changes. */
    this.summary = null;

    /** Pre-rendered subtitle text (e.g. "2024 General Election · Labour majority: 174").
     * Populated by AppState#initElectionData after summary is set; regenerate via
     * {@link ElectionData#generateSubtitleSummaryText} when summary changes. Only the active
     * election populates this — comparison ElectionData instances leave it null. */
    this.summaryText = null;
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
   * @param {Array<{seat: string}>} seats - Seat-shaped objects with a `seat` name property.
   * @returns {Map<string, object>} Map from lowercase seat name key to seat object.
   */
  static buildSeatIndex(seats) {
    const byKey = new Map();
    (seats || []).forEach((seat) => {
      if (!seat?.seat) return;
      byKey.set(seatLookupKey(seat.seat), seat);
    });
    return byKey;
  }

  // TODO: dedupe summarizeElection / summarizeElection2 (this is the canonical handler)
  /**
   * Aggregates seats and votes across all constituencies in `currentSeats` and stores
   * the result on `this.summary`. Call again whenever `currentSeats` changes (e.g. after
   * predict-mode projection) to refresh the cached summary.
   * List seats contribute their winner to the party seat count but not to vote totals
   * (list seats use a separate ballot — combining them would double-count the electorate).
   * Duplicate of the standalone `summarizeElection2` in electionmaps.js — kept temporarily
   * while callers migrate.
   * @returns {void}
   */
  summarizeElection() {
    const partyStats = new Map();
    let electorateSum = 0;
    let turnoutWeighted = 0;

    this.currentSeats.forEach((seat) => {
      const isList = /\bList\s+\d+$/i.test(seat.seat);

      const winner = seat.winner === 'other' ? 'others' : (seat.winner || 'others');
      if (!partyStats.has(winner)) partyStats.set(winner, { seats: 0, votes: 0 });
      partyStats.get(winner).seats += 1;

      if (!isList) {
        Object.entries(seat.votes || {}).forEach(([party, votes]) => {
          const key = party === 'other' ? 'others' : party;
          if (!partyStats.has(key)) partyStats.set(key, { seats: 0, votes: 0 });
          partyStats.get(key).votes += Number(votes || 0);
        });

        if (seat.electorate > 0 && seat.turnout > 0) {
          electorateSum += seat.electorate;
          turnoutWeighted += seat.turnout * seat.electorate;
        }
      }
    });

    const parties = Array.from(partyStats.entries())
      .map(([party, stats]) => ({ party, ...stats }))
      .sort((a, b) => b.seats - a.seats || b.votes - a.votes);

    const totalVotes = parties.reduce((sum, p) => sum + p.votes, 0);
    const turnout = electorateSum > 0 ? turnoutWeighted / electorateSum : 0;

    return { parties, totalVotes, turnout, totalSeats: this.currentSeats.length };
  }

  /**
   * Builds the subtitle text shown under the page title for this election: a single line
   * describing either the leading party's overall majority or, when no party has one,
   * a hung-parliament message naming the largest party.
   *
   * Inputs (all read from `this.summary`, which the caller must have populated):
   *   - `summary.parties` — sorted by seats desc, so `parties[0]` is the leading party.
   *   - `summary.totalSeats` — number of seats in the chamber (e.g. 650 for Westminster).
   *
   * Calculation:
   *   - `majorityThreshold = totalSeats / 2` — the bare half-line. A party needs *more*
   *     than this many seats to govern alone.
   *   - `hasMajority = leadSeats > majorityThreshold` — strict greater-than so an exact
   *     half-share counts as hung.
   *   - `majority = 2 × (leadSeats − majorityThreshold)` — the standard parliamentary
   *     "majority of N" figure: the number of seats by which the leading party's bench
   *     exceeds the rest of the chamber combined. For 650 total seats and a leader on
   *     412, that's 2 × (412 − 325) = 174. Rounded to handle odd totals (e.g. 129-seat
   *     Holyrood gives a half-integer threshold).
   *
   * Output forms (returned as a single string, ` · `-joined):
   *   - With majority:  `"<electionName> · <Party> majority: <N>"`
   *     e.g. `"2024 General Election · Labour majority: 174"`
   *   - Hung parliament: `"<electionName> · Hung parliament - largest party <Party> with <N> seats"`
   *     e.g. `"2017 General Election · Hung parliament - largest party Conservative with 317 seats"`
   *
   * Party labels go through `manifest.labelParty` so internal keys ("lab", "con", …)
   * render as their human display names. A missing winner falls back to the `'others'`
   * label rather than throwing or producing an empty string.
   *
   * @returns {string} Pre-formatted subtitle string ready to pass to `setHeader`.
   */
  generateSubtitleSummaryText() {
    const top = this.summary.parties[0];
    const leadSeats = Number(top?.seats || 0);
    const totalSeats = Number(this.summary.totalSeats || 0);
    const majorityThreshold = totalSeats / 2;
    const hasMajority = leadSeats > majorityThreshold;
    const majority = hasMajority ? Math.round(2 * (leadSeats - majorityThreshold)) : 0;

    return hasMajority
      ? `${this.electionName} · ${manifest.labelParty(top?.party || 'others')} majority: ${majority}`
      : `${this.electionName} · Hung parliament - largest party ${manifest.labelParty(top?.party || 'others')} with ${formatInt(leadSeats)} seats`;
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
     * - byElectionSeats {Set<string>|null} — constituency name set for by-election gain filtering, or null when not a by-election election
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

    /** True if the current election is a referendum, as flagged in the manifest.
     * Controls visibility of gains/choropleth controls and data info button. */
    this.isReferendumType = false;

    /** Vote totals panel state.
     * - columns.votes: whether the raw vote count column is visible. Initialised false for
     *   model elections and referendum-type elections; true otherwise. Flipped to false when
     *   entering predict mode. Read via voteTotalsColumnVisible().
     * - mode: id of the active vote-totals tab (e.g. 'all', 'constituency', 'list'). Initialised
     *   in state.init from manifest.mapModes[mapId].voteTotalsViews[0].id; mutated when the user
     *   clicks a different tab.
     */
    this.voteTotals = { columns: { votes: true }, mode: 'all' };

    /** Seat view panel state.
     * - mode: id of the active seat-view tab (e.g. 'seats', 'constituency', 'regions').
     *   Initialised in state.init from manifest.mapModes[mapId].seatViews[0].id; mutated when
     *   the user clicks a different tab.
     */
    this.seatView = { mode: 'seats' };

    /** Active map filter selections. Mutated by syncMapControlStateFromInputs (DOM → state) on
     * every filter change, by the gains-button click handler, and by resetPrimaryFilters. Read by
     * Seat.matchesPrimaryFilters when computing the visible seat set. */
    this.mapFilters = {
      party: 'all',
      region: 'all',
      secondParty: 'all',
      majorityMin: 0,
      majorityMax: 100,
      gainsOnly: false,
    };

    /** Active choropleth selection. Mutated by syncMapControlStateFromInputs and resetChoropleths.
     * Read by buildChoroplethConfig and the choropleth render path. */
    this.mapChoropleths = {
      type: 'none',
      party: 'all',
    };

    /** Derived visible-seat slice produced by applying mapFilters to electionData.currentSeats.
     * Recomputed at the top of renderMapWithViewState; read by the renderers and by the
     * vote-totals tab click handler when it re-summarises without a full re-render.
     * - seatKeys: Set<string> of seat lookup keys that pass the active filters.
     * - seats: Array of current-election seat objects matching seatKeys.
     * - comparisonSeats: Array of comparison-election seat objects keyed by seatKeys (Boolean-filtered). */
    this.mapVisible = {
      seatKeys: new Set(),
      seats: [],
      comparisonSeats: [],
    };

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
    this.currentElection.byElectionSeats = this.currentElection.byElectionSeats?.length
      ? new Set(this.currentElection.byElectionSeats)
      : null;
    this.currentRegionLabelsByKey = manifest.buildRegionLabelLookup(this.currentElection.mapId);
    this.isReferendumType = !!this.currentElection.referendum;
    if (this.currentElection.model || this.isReferendumType) {
      this.voteTotals.columns.votes = false;
    }

    // Default the active vote-totals / seat-view tabs from the mapMode config. The hydration
    // step in Manifest.#hydrate guarantees both arrays are non-empty.
    const mapConfig = manifest.mapModes[String(this.currentElection.mapId)];
    this.voteTotals.mode = mapConfig.voteTotalsViews[0].id;
    this.seatView.mode = mapConfig.seatViews[0].id;

    // Fetch prediction snippet for model elections and poll tracker, where subtitle text references it.
    if (this.view === 'polltracker' || this.currentElection.model) {
      this.predictionSnippet = (await manifest.fetchPredictionMeta(this.currentParliament)) ?? '';
    }
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
    this.electionData = new ElectionData(resultsData, this.currentElection?.name ?? null);
    this.electionData.summary = this.electionData.summarizeElection();
    this.electionData.summaryText = this.electionData.generateSubtitleSummaryText();
  }

  /**
   * Builds an ElectionData instance for the comparison election and stores it as state.comparisonElectionData.
   * Transitional: mirrors the comparison seats / index onto _state.
   * @param {object} comparisonData - Raw results JSON for the comparison election.
   * @returns {void}
   */
  initComparisonElectionData(comparisonData) {
    this.comparisonElectionData = new ElectionData(comparisonData);
    this.comparisonElectionData.summary = this.comparisonElectionData.summarizeElection();
    // Point the _state mirrors at comparisonElectionData's already-cloned arrays/index instead
    // of re-cloning. Predict mode reassigns these mirrors to predictBaseSeats during projection.
    // TODO these can be removed once  predict mode is migrated to use comparisonElectionData directly instead of the mirrors.
    _state.currentComparisonSeats = this.comparisonElectionData.currentSeats;
    _state.comparisonSeatsByKey = this.comparisonElectionData.seatsByKey;
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

  /**
   * Returns the predict anchor election id for the current parliament, or undefined if not set.
   * @returns {string|undefined}
   */
  getPredictAnchorElectionId() {
    return manifest.parliamentConfig(this.currentParliament).predictAnchorElectionId;
  }

  /**
   * Returns the predict baseline election id for the current parliament, or undefined if not set.
   * @returns {string|undefined}
   */
  getPredictBaselineElectionId() {
    return manifest.parliamentConfig(this.currentParliament).predictBaselineElectionId;
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
   * Recomputes mapVisible.{seatKeys, seats, comparisonSeats} by applying the active mapFilters
   * to electionData.currentSeats. Reads _state.comparisonSeatsByKey rather than
   * comparisonElectionData.seatsByKey because predict mode reassigns the _state mirror to the
   * predict baseline index, which then becomes the source of truth for gains-filtering.
   * @returns {void}
   */
  setMapVisible() {
    // TODO remove during refactor
    const comparisonSeatsByKey = _state.comparisonSeatsByKey;
    const byElectionSeats = this.currentElection.byElectionSeats;
    const seatKeys = new Set();
    this.electionData.currentSeats.forEach((seat) => {
      const seatKey = seatLookupKey(seat.seat);
      const comparisonSeat = comparisonSeatsByKey.get(seatKey) || null;
      if (seat.matchesPrimaryFilters(comparisonSeat, this.mapFilters, byElectionSeats)) {
        seatKeys.add(seatKey);
      }
    });
    this.mapVisible.seatKeys = seatKeys;
    this.mapVisible.seats = this.electionData.currentSeats.filter((seat) => seatKeys.has(seatLookupKey(seat.seat)));
    this.mapVisible.comparisonSeats = Array.from(seatKeys)
      .map((seatKey) => comparisonSeatsByKey.get(seatKey))
      .filter(Boolean);
  }

  /**
   * Returns option rows for the party filter, second-party filter, and choropleth-party
   * selects. Each row is `{ value, label }` ready for direct <option> rendering.
   *
   * The party set is the union of every key seen as a winner or as a voter across both
   * the current and (when present) comparison elections. Including comparison parties
   * means a party that has since lost all seats still appears as a filter option, so the
   * user can target it in the comparison data.
   *
   * Two normalisations are applied:
   *   - The legacy 'other' key is folded into the canonical 'others'. Older results files
   *     used 'other'; without folding, both would appear as separate rows.
   *   - Seats with no recorded winner contribute the synthetic 'others' key (rather than
   *     being skipped), matching how the rest of the app treats unattributed seats.
   *
   * Rows are sorted by their human-readable party label (via manifest.labelParty), with
   * an 'all parties...' row prepended as the dropdown default.
   *
   * @returns {Array<{value: string, label: string}>} Option rows for <option> rendering.
   */
  mapControlParties() {
    const mergeKey = (key) => (key === 'other' ? 'others' : key);
    const keys = new Set();
    // Walks one seat array, adding the winner key and every voter key into the shared
    // `keys` set. Run twice (current + comparison) to avoid duplicating the scan body.
    // The optional chain handles a null comparisonElectionData (election with no comparison).
    const addFromSeats = (seats) => {
      seats?.forEach((seat) => {
        keys.add(mergeKey(seat.winner || 'others'));
        Object.keys(seat.votes || {}).forEach((partyKey) => keys.add(mergeKey(partyKey)));
      });
    };
    addFromSeats(this.electionData?.currentSeats);
    addFromSeats(this.comparisonElectionData?.currentSeats);

    const sorted = Array.from(keys)
      .sort((a, b) => manifest.labelParty(a).localeCompare(manifest.labelParty(b)));

    return [
      { value: 'all', label: 'all parties...' },
      ...sorted.map((key) => ({ value: key, label: manifest.labelParty(key) })),
    ];
  }

  /**
   * Returns option rows for the region filter select. Each row is `{ value, label }`
   * ready for direct <option> rendering.
   *
   * Walks current seats only — the region list is intrinsic to the active election's map
   * geometry, so there's no equivalent need to extend it from the comparison election.
   * Region keys are normalised (so spelling/case variants collapse onto a single row) and
   * resolved to display labels via getRegionLabel. Seats with no resolvable region key
   * (e.g. a UK-wide referendum row that has no regional breakdown) are dropped.
   *
   * Rows are sorted by label, with an 'all regions...' row prepended as the dropdown default.
   *
   * @returns {Array<{value: string, label: string}>} Option rows for <option> rendering.
   */
  mapControlRegions() {
    const byKey = new Map();
    this.electionData?.currentSeats?.forEach((seat) => {
      const key = normalizeRegionKey(seat.region);
      if (!key || byKey.has(key)) return;
      byKey.set(key, getRegionLabel(seat.region, this.currentRegionLabelsByKey));
    });

    const rows = Array.from(byKey.entries())
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));

    return [{ value: 'all', label: 'all regions...' }, ...rows];
  }

  /**
   * Returns whether the named column should be visible in the vote totals panel.
   * @param {string} column - Column key in this.voteTotals.columns (e.g. 'votes').
   * @returns {boolean}
   */
  voteTotalsColumnVisible(column) {
    return !!this.voteTotals.columns[column];
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


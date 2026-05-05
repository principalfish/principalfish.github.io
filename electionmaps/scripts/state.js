// Shared mutable state for the electionmaps application.
// All modules import these objects and mutate their properties directly.
// A single shared object reference means every importer sees the same state.

import * as d3 from '../../site/vendor/d3.v7.esm.js';
import { normalizeRegionKey, formatInt, formatPct, formatSigned, seatLookupKey, getRegionLabel } from './utils.js';
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

      if (!Array.isArray(mapMode.voteTotalsViews) || mapMode.voteTotalsViews.length === 0) {
        mapMode.voteTotalsViews = [{ id: 'all', label: 'All' }];
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
  selectedSeatRow: null,
  activeSeatPathNode: null,
  currentOpenSeatName: null,

  // Election / seat data
  currentComparisonSeats: [],
  comparisonSeatsByKey: new Map(),
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
    this.turnout = Object.values(this.votes).reduce((sum, v) => sum + Number(v || 0), 0);
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

  /**
   * Returns the vote share percentage (0–100) for partyKey in the given seat. Uses the
   * explicit turnout field if available, otherwise sums all party vote totals. Returns 0
   * if total votes are zero. Static so it accepts any seat-shaped object, not just Seat
   * instances.
   * @param {{turnout?: number, votes?: object}} seat - Seat with optional turnout and a votes map.
   * @param {string} partyKey - The party whose vote share to calculate.
   * @returns {number} Vote share as a percentage in the range [0, 100].
   */
  static voteSharePct(seat, partyKey) {
    const turnout = Number(seat?.turnout || 0);
    const totalVotes = turnout > 0
      ? turnout
      : Object.values(seat?.votes || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    if (totalVotes <= 0) return 0;
    const partyVotes = Number(seat?.votes?.[partyKey] || 0);
    return (partyVotes / totalVotes) * 100;
  }

  /**
   * Returns the choropleth metric value for a seat, or null when data is unavailable.
   * Returns null when isDelta is true but no comparison seat is available.
   * Static so it accepts any seat-shaped object, not just Seat instances.
   * @param {Seat} seat - Current seat object.
   * @param {Seat|null} comparisonSeat - Comparison seat for delta calculations; may be null.
   * @param {boolean} isDelta - True for vote share change (voteShareChange), false for absolute (voteShare).
   * @param {string} choroplethParty - Party key to compute the metric for.
   * @returns {number|null} Choropleth metric value, or null when comparison data is unavailable.
   */
  static choroplethValue(seat, comparisonSeat, isDelta, choroplethParty) {
    if (isDelta) {
      if (!comparisonSeat) return null;
      return Seat.voteSharePct(seat, choroplethParty) - Seat.voteSharePct(comparisonSeat, choroplethParty);
    }
    return Seat.voteSharePct(seat, choroplethParty);
  }

  /**
   * Returns true if seat is a regional list seat (e.g. "Glasgow List 1"). List seats have no
   * map geometry and appear only in the seat list panel. Static so it accepts any seat-shaped
   * object, not just Seat instances.
   * @param {{seat?: string}} seat - Seat-shaped object with a `seat` name field.
   * @returns {boolean}
   */
  static isList(seat) {
    return /\bList\s+\d+$/i.test(seat?.seat || '');
  }

  // ── Seat data utilities ────────────────────────────────────────────────────

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
    if (this.turnout <= 0) return { pct: 0, raw: marginVotes };
    return { pct: (marginVotes / this.turnout) * 100, raw: marginVotes };
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
   * Returns true when seat passes all active primary filters.
   * Static so it accepts any Seat instance without binding to a particular `this`.
   * @param {Seat} seat - The seat to test.
   * @param {Seat|null} comparisonSeat - Comparison seat for gains filtering; may be null.
   * @param {{party: string, region: string, secondParty: string, majorityMin: number, majorityMax: number, gainsOnly: boolean}} filterState - Active filter configuration.
   * @param {Set<string>|null} byElectionSeats - Set of seat names for by-election gain filtering, or null.
   * @returns {boolean} True if the seat passes all currently active filters.
   */
  static matchesPrimaryFilters(seat, comparisonSeat, filterState, byElectionSeats) {
    if (filterState.party !== 'all') {
      const winner = seat.winner === 'other' ? 'others' : seat.winner;
      if (winner !== filterState.party) return false;
    }

    if (filterState.region !== 'all') {
      const seatRegion = normalizeRegionKey(seat.region);
      if (seatRegion !== filterState.region) return false;
    }

    const majority = seat.majorityStats().pct;
    if (majority < filterState.majorityMin || majority > filterState.majorityMax) return false;

    if (filterState.secondParty !== 'all') {
      const secondParty = seat.#secondPlaceParty();
      if (secondParty !== filterState.secondParty) return false;
    }

    if (filterState.gainsOnly) {
      if (byElectionSeats) {
        if (!byElectionSeats.has(seat.seat)) return false;
      } else {
        const gainFrom = seat.gainFromParty(comparisonSeat?.winner);
        if (!gainFrom) return false;
      }
    }

    return true;
  }
}

// ─── ElectionSummary ─────────────────────────────────────────────────────────────────

/**
 * Aggregated summary of an ElectionData's seats. Bundles the structured party/seat/vote
 * totals (`data`) and the pre-rendered subtitle string (`text`) so both are recomputed
 * together whenever the underlying seat list changes (e.g. after predict-mode projection).
 *
 * The `mode` option controls how list and constituency seats are folded together when
 * aggregating votes:
 *   - `'all'` (default) — every seat contributes its winner to the seat count, but only
 *     constituency seats contribute votes (list seats use a separate ballot, so combining
 *     would double-count the electorate).
 *   - `'constituency'` — list seats are skipped entirely.
 *   - `'list'` — constituency seats are skipped entirely; within list seats each
 *     `(region, party)` pair only contributes votes once (regional list totals are
 *     duplicated across every seat in the region).
 */
export class ElectionSummary {
  /**
   * @param {Seat[]} seats - Seats to aggregate.
   * @param {string|null} electionName - Display label for the election; when null, `text` is null.
   * @param {('all'|'constituency'|'list')} [mode='all'] - Aggregation mode forwarded to {@link ElectionSummary.summarize}; see class jsdoc for the per-mode behaviour.
   */
  constructor(seats, electionName, mode = 'all') {
    /** Aggregation mode used to build {@link ElectionSummary#data}. See class jsdoc for per-mode behaviour. */
    this.mode = mode;

    /** Structured aggregate: `{parties, totalVotes, turnout, totalSeats}`. Parties are
     * sorted by seats desc, then votes desc, so `parties[0]` is the leading party. */
    this.data = ElectionSummary.summarize(seats, mode);

    /** Pre-rendered subtitle string (e.g. "2024 General Election · Labour majority: 174"),
     * or null when no electionName was provided (comparison/predict-baseline summaries). */
    this.text = electionName ? ElectionSummary.#subtitleText(this.data, electionName) : null;
  }

  /**
   * Aggregates seats and votes across all constituencies into the structured form consumed
   * by the vote-totals panel and subtitle text. Honours the active vote-totals tab via
   * `mode`.
   *
   * Mode behaviour:
   *   - `'all'` (default) — every seat contributes its winner to the seat count, but only
   *     constituency seats contribute votes. List seats use a separate ballot, so combining
   *     both would double-count the electorate.
   *   - `'constituency'` — list seats are skipped entirely (neither seat count nor votes).
   *   - `'list'` — constituency seats are skipped entirely. Within list seats, regional
   *     vote totals are stored on every seat in the region; we only count each
   *     `(region, party)` pair once to avoid multiplying by the number of seats.
   *
   * Parties are returned sorted by seats descending, then votes descending as a tiebreaker,
   * so `parties[0]` is the leading party and the row order is stable. Two normalisations are
   * applied while accumulating: the legacy `'other'` key is folded into the canonical
   * `'others'` (older results files used `'other'`; without folding both would appear as
   * separate rows), and seats with no recorded winner contribute the synthetic `'others'`
   * key rather than being skipped.
   *
   * `totalSeats` is the input length and does *not* reflect mode filtering — it represents
   * the size of the chamber being summarised, not the number of seats that contributed to
   * stats.
   *
   * @param {Seat[]} seats - Seats to aggregate. Each seat must carry the normalised shape
   *   produced by {@link Seat} (i.e. `winner`, `votes`, `region`); raw pf-results-v4
   *   objects must be converted via `Seat.fromRaw` first.
   * @param {('all'|'constituency'|'list')} [mode='all'] - Vote-totals tab to summarise for; see Mode behaviour above.
   * @returns {{
   *   parties: Array<{party: string, seats: number, votes: number}>,
   *   totalVotes: number,
   *   totalSeats: number
   * }} Aggregated summary:
   *   - `parties` — one row per party that won at least one seat or received any votes,
   *     sorted by seats desc then votes desc. `party` is the canonical key (e.g. `'lab'`),
   *     `seats` is the number of seats won under the active mode, and `votes` is the total
   *     vote count under the active mode (zero when the party only contributed via the
   *     skipped ballot type).
   *   - `totalVotes` — sum of `votes` across all party rows; the denominator for vote-share
   *     percentages in the totals panel.
   *   - `totalSeats` — `seats.length` of the input array (chamber size), independent of
   *     mode filtering.
   */
  static summarize(seats, mode = 'all') {
    // partyStats accumulates per-party seat and vote counts in a single pass over `seats`.
    // We use a Map (not a plain object) because party keys are arbitrary user-data strings
    // and we want insertion-order iteration if needed for deterministic debugging.
    const partyStats = new Map();
    // Tracks `(region, party)` pairs we've already credited with list-mode votes. Each
    // region's list ballot is duplicated across every list seat in that region, so without
    // this guard a region with N list seats for one party would count its votes N times.
    // Only populated in list mode; an empty Set in other modes costs nothing.
    const listRegionPartyCountSeen = new Set();

    seats.forEach((seat) => {
      const isList = Seat.isList(seat);

      // Mode filtering: constituency mode hides the regional list ballot, list mode hides
      // the constituency ballot. Skipped seats contribute neither seat counts nor votes.
      // 'all' falls through and counts every seat (with the per-mode vote rules below).
      if (mode === 'constituency' && isList) return;
      if (mode === 'list' && !isList) return;

      // Seat-count accumulation: every non-skipped seat adds 1 to its winner's tally.
      // Fold the legacy 'other' key into 'others' (older results files used 'other'),
      // and missing/falsy winners fall back to 'others' rather than being dropped — the
      // synthetic key matches how the rest of the app treats unattributed seats.
      const winner = seat.winner === 'other' ? 'others' : (seat.winner || 'others');
      if (!partyStats.has(winner)) partyStats.set(winner, { seats: 0, votes: 0 });
      partyStats.get(winner).seats += 1;

      // Vote accumulation gate. List seats are excluded from votes in 'all' mode because
      // they're a separate ballot — combining them with constituency votes would
      // double-count the electorate. In 'constituency' and 'list' modes the filter above
      // has already restricted the seat set, so includeVotes is true for every survivor.
      const includeVotes = mode !== 'all' || !isList;
      if (includeVotes) {
        Object.entries(seat.votes || {}).forEach(([party, votes]) => {
          // Same 'other' → 'others' fold as for winners, applied per voter party.
          const key = party === 'other' ? 'others' : party;
          if (!partyStats.has(key)) partyStats.set(key, { seats: 0, votes: 0 });
          // List-mode dedupe: skip this party's votes if we've already counted them for
          // this region. The seenKey uses a NUL byte separator so it can't collide with
          // a real party key that contains the region name (or vice versa).
          if (mode === 'list') {
            const seenKey = `${normalizeRegionKey(seat.region)}\x00${key}`;
            if (listRegionPartyCountSeen.has(seenKey)) return;
            listRegionPartyCountSeen.add(seenKey);
          }
          partyStats.get(key).votes += Number(votes || 0);
        });
      }
    });

    // Flatten the Map into an array of `{party, seats, votes}` rows and order them so the
    // leading party is first. Seats descending is the primary sort (it's what callers care
    // about); votes descending breaks ties between parties on the same seat count, e.g. a
    // by-election year where two minor parties each won 1 seat.
    const parties = Array.from(partyStats.entries())
      .map(([party, stats]) => ({ party, ...stats }))
      .sort((a, b) => b.seats - a.seats || b.votes - a.votes);

    // totalVotes is the denominator used by the vote-totals panel for vote-share %s, so
    // it must be re-derived from the final party rows rather than tracked alongside the
    // accumulators (the list-mode dedupe means a running sum would double-count).
    const totalVotes = parties.reduce((sum, p) => sum + p.votes, 0);

    // totalSeats is the *input* length, not the size of partyStats — it represents the
    // chamber, not the number of seats that survived mode filtering.
    return { parties, totalVotes, totalSeats: seats.length };
  }

  /**
   * Builds the subtitle text shown under the page title: a single line describing either
   * the leading party's overall majority or, when no party has one, a hung-parliament
   * message naming the largest party.
   *
   * Inputs:
   *   - `data.parties` — sorted by seats desc, so `parties[0]` is the leading party.
   *   - `data.totalSeats` — number of seats in the chamber (e.g. 650 for Westminster).
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
   * @param {{parties: Array<{party: string, seats: number}>, totalSeats: number}} data - Structured summary aggregate.
   * @param {string} electionName - Display label prefix for the subtitle.
   * @returns {string} Pre-formatted subtitle string ready to pass to `setHeader`.
   */
  static #subtitleText(data, electionName) {
    const top = data.parties[0];
    const leadSeats = Number(top?.seats || 0);
    const totalSeats = Number(data.totalSeats || 0);
    const majorityThreshold = totalSeats / 2;
    const hasMajority = leadSeats > majorityThreshold;
    const majority = hasMajority ? Math.round(2 * (leadSeats - majorityThreshold)) : 0;

    return hasMajority
      ? `${electionName} · ${manifest.labelParty(top?.party || 'others')} majority: ${majority}`
      : `${electionName} · Hung parliament - largest party ${manifest.labelParty(top?.party || 'others')} with ${formatInt(leadSeats)} seats`;
  }

  /**
   * Aggregates seats and votes per region, producing the breakdown consumed by the
   * region-table overlay and the per-region popup. Currently only invoked with regional
   * list seats (Holyrood) — the constituency map is rendered seat-by-seat instead, so
   * there's no per-region rollup needed there — but the implementation works for any
   * mix of constituency + list seats and could be reused if a Westminster regional view
   * is ever added.
   *
   * Output shape (one entry per distinct `seat.region` encountered):
   *   - `seatsByParty: { [partyKey]: number }` — count of seats won by each party in
   *     the region. Used by the region-table renderer to size the colour-bar segments
   *     and by the popup as the seat-count per row.
   *   - `votesByParty: { [partyKey]: number }` — sum of votes per party in the region.
   *     For Holyrood list seats each seat carries the *full* regional list total, so
   *     summing across the N list seats per region inflates each party's votes by N.
   *     That's fine for the only consumer (vote-share % in the popup), since the N
   *     multiplier cancels out of `votes / Σvotes`, but absolute totals are NOT a true
   *     regional vote count.
   *
   * Region keys come straight from `seat.region`; missing/falsy values fall back to
   * `'unknown'` rather than being skipped, matching the rest of the app's
   * unattributed-seat handling. Party keys use the raw `seat.winner` and `seat.votes`
   * keys without the `'other'` → `'others'` fold applied by {@link summarize} —
   * downstream renderers only call `manifest.labelParty` / `manifest.colourParty` on
   * these keys, which handle either form.
   *
   * @param {Seat[]} seats - Seats to aggregate. Each seat must carry the normalised
   *   shape produced by {@link Seat} (i.e. `region`, `winner`, `votes`).
   * @returns {Map<string, {seatsByParty: Object<string, number>, votesByParty: Object<string, number>}>}
   *   Map keyed by region key; each value is the seat/vote breakdown described above.
   *   Insertion-ordered by first seat encountered per region.
   */
  static summarizeByRegion(seats) {
    // Map preserves insertion order, so callers iterating the result see regions in
    // the order their first seat appears in `seats` — the seat array is already
    // ordered in a meaningful way (north-to-south for Holyrood lists), and a plain
    // object would not give that guarantee for non-numeric keys.
    const regions = new Map();

    for (const seat of seats) {
      // Bucket by region. Falsy/missing region falls back to 'unknown' so the seat is
      // still aggregated rather than silently dropped — matches the synthetic-others
      // fallback used elsewhere for unattributed data.
      const region = seat.region || 'unknown';
      // Lazily create the per-region bucket on first encounter. Pre-allocating empty
      // buckets for every known region would require knowing the region list up front,
      // which this static doesn't have access to.
      if (!regions.has(region)) {
        regions.set(region, { seatsByParty: {}, votesByParty: {} });
      }
      const r = regions.get(region);

      // Increment the seat count for the winning party. Missing winners fall back to
      // `'others'` so the row still appears in the region table rather than being
      // dropped — same reasoning as the region fallback above.
      const winner = seat.winner || 'others';
      r.seatsByParty[winner] = (r.seatsByParty[winner] || 0) + 1;

      // Sum votes per party. See jsdoc for the list-seat duplication caveat: this
      // accumulator is only safe to read as ratios (vote shares), not absolute totals.
      for (const [party, votes] of Object.entries(seat.votes || {})) {
        r.votesByParty[party] = (r.votesByParty[party] || 0) + votes;
      }
    }

    return regions;
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
     * Null for the comparison ElectionData instance, which never renders a subtitle. */
    this.electionName = electionName;

    /** Pristine normalised seat records as parsed from the results JSON. Never mutated;
     * use as the source of truth when rebuilding currentSeats from baseline. */
    this.baseSeats = ElectionData.normalizeSeats(resultsData);

    /** Mutable clone of baseSeats. Predict mode and other features write back into this list,
     * so it diverges from baseSeats over time within a single election load. */
    this.currentSeats = this.baseSeats.map((seat) => new Seat(seat));

    /** Map from seat lookup key to currentSeats entry, rebuilt whenever currentSeats is replaced. */
    this.seatsByKey = ElectionData.buildSeatIndex(this.currentSeats);

    /** Aggregated summary of currentSeats: `{data, text}`. Rebuild by reassigning to a new
     * `ElectionSummary` instance whenever currentSeats changes (e.g. after predict-mode projection). */
    this.summary = new ElectionSummary(this.currentSeats, electionName);
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
     * - columns.votes: whether the raw vote-count column is visible. Hidden for predict / model /
     *   referendum elections (no meaningful raw counts) and on the Holyrood 'all' tab (where
     *   constituency and list ballots can't be summed coherently).
     * - columns.votePct: whether the vote-percentage column is visible. Hidden only on the
     *   Holyrood 'all' tab; predict / model / referendum still show vote shares.
     * - columns.comparison: whether the comparison delta columns are visible. True whenever
     *   comparison data is loaded.
     * All three are re-derived per render in setupMapData; read via voteTotalsColumnVisible().
     * - mode: id of the active vote-totals tab (e.g. 'all', 'constituency', 'list'). Initialised
     *   in state.init from manifest.mapModes[mapId].voteTotalsViews[0].id; mutated when the user
     *   clicks a different tab.
     */
    this.voteTotals = {
      columns: { votes: true, votePct: true },
      mode: 'all',
      expanded: false,
      sort: { key: 'seats', direction: 'desc' },
    };

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
     * Read by this.buildChoroplethConfig and the choropleth render path. */
    this.mapChoropleths = {
      type: 'none',
      party: 'all',
    };

    /** Derived visible-seat slice produced by applying mapFilters to electionData.currentSeats.
     * Recomputed at the top of drawMap; read by the renderers and by the
     * vote-totals tab click handler when it re-summarises without a full re-render.
     * - seatKeys: Set<string> of seat lookup keys that pass the active filters.
     * - seats: Array of current-election seat objects matching seatKeys.
     * - comparisonSeats: Array of comparison-election seat objects keyed by seatKeys (Boolean-filtered). */
    this.mapSeatsVisible = {
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

    /** Cached choropleth rendering config for the current visible seat set.
     * Set by buildChoroplethConfig; { enabled: false } until first computed. */
    this.choroplethConfig = { enabled: false };

    // Per-render derived data, populated by setupMapData() before each drawMap call
    // and read by drawMap plus the renderers it dispatches to. All initialised to
    // safe empty values so a caller reading them before the first setupMapData run
    // doesn't see undefined.

    /** Manifest mapMode entry for currentElection.mapId. Carries the per-map config
     * (regions, voteTotalsViews, hiddenVoteTotalsParties). Null until
     * setupMapData runs. */
    this.mapConfig = null;

    /** currentElection.mapId when the current map supports postcode lookup, otherwise null.
     * Derived from mapConfig.postcodeSupported (declared in map-modes.json). Null until
     * setupMapData runs. Used by dom.js to show/hide the postcode search group and to
     * distinguish Holyrood (mapId 12) from Westminster (mapId 2) when dispatching lookups. */
    this.postcodeMapId = null;

    /** Whether the current chamber includes regional list seats (Holyrood). False until
     * setupMapData runs. Drives the list-seat specialisation (region-table overlay and
     * constituency-only seat list). */
    this.hasListSeats = false;

    /** ElectionSummary.summarize result over mapSeatsVisible.seats — the aggregated summary
     * of the currently visible (filter-passing) seats under the active vote-totals tab.
     * Distinct from electionData.summary, which always covers the unfiltered chamber. */
    this.filteredSeatsSummary = null;

    /** Same as filteredSeatsSummary but over mapSeatsVisible.comparisonSeats. Null when no
     * comparison data is loaded. */
    this.filteredSeatsComparisonSummary = null;

    /** Per-region rollup from ElectionSummary.summarizeByRegion over the chamber's list
     * seats only. Null when the chamber has no list seats. Feeds the region-table
     * overlay and the per-region popup. */
    this.listRegionSummary = null;

    /** Visible seats minus list seats — when the chamber has list seats, the seat-list
     * panel hides them (they appear in the region-table overlay instead). Equals
     * mapSeatsVisible.seats unmodified when no list seats exist. */
    this.listFilteredSeats = [];

    /** Sorted seat-name array used by the autocomplete dropdown. Built from
     * listFilteredSeats so Holyrood searches resolve only to constituencies. */
    this.seatSearchNames = [];

    /** Map of seat-lookup key → display name for the current visible set. Used by
     * selectSeatBySearchQuery and the Holyrood new-boundary name fallback. */
    this.currentSeatNameByKey = new Map();
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

    const mapConfig = manifest.mapModes[String(this.currentElection.mapId)];
    this.voteTotals.mode = mapConfig.voteTotalsViews[0].id;

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
  }

  /**
   * Builds an ElectionData instance for the comparison election and stores it as state.comparisonElectionData.
   * Transitional: mirrors the comparison seats / index onto _state.
   * @param {object} comparisonData - Raw results JSON for the comparison election.
   * @returns {void}
   */
  initComparisonElectionData(comparisonData) {
    this.comparisonElectionData = new ElectionData(comparisonData);
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
   * The seat array used as the comparison/baseline for the active view. When a comparison
   * election is loaded this is `comparisonElectionData.currentSeats`; predict mode swaps
   * it for `predictBaseSeats`. Empty array when no comparison data exists. Reads through
   * the `_state.currentComparisonSeats` mirror so callers see the predict-mode reassignment
   * without having to know about the mirror.
   * TODO eventyually remove tyhis once the _state property is removed and predict mode reads directly from comparisonElectionData.
   * @returns {Seat[]}
   */
  get comparisonSeats() {
    return _state.currentComparisonSeats;
  }

  /**
   * Computes all derived per-render data for the active map view: applies filters,
   * builds the choropleth config, and produces the aggregated summaries plus the
   * list-seat specialisation slice. drawMap calls this once per render and then reads
   * the populated fields off `state` rather than recomputing locally.
   *
   * Sets: mapConfig, hasListSeats, listRegionSummary, listFilteredSeats, seatSearchNames,
   *   currentSeatNameByKey. Also refreshes the mapSeatsVisible slice (via applyMapFilters),
   *   the choroplethConfig (via buildChoroplethConfig), and the vote-totals column flags +
   *   summaries (via recomputeVoteTotalsForMode) as a side effect.
   * @returns {void}
   */
  setupMapData() {
    this.applyMapFilters();
    this.buildChoroplethConfig();

    this.mapConfig = manifest.mapModes[String(this.currentElection.mapId)];
    this.postcodeMapId = this.mapConfig?.postcodeSupported ? this.currentElection.mapId : null;
    this.hasListSeats = this.electionData.currentSeats.some((s) => Seat.isList(s));

    this.recomputeVoteTotalsForMode();

    // List-seat specialisation. Holyrood elections need a per-region rollup for the
    // region-table overlay (list seats render there rather than on the map), and the
    // seat-list panel shows constituencies only — list seats appear in the region table
    // instead. Westminster / by-elections / referenda have no list seats, so both vars
    // stay at their defaults: no region rollup, and the seat list shows every visible
    // seat unmodified.
    this.listRegionSummary = null;
    this.listFilteredSeats = this.mapSeatsVisible.seats;
    if (this.hasListSeats) {
      this.listRegionSummary = ElectionSummary.summarizeByRegion(this.electionData.currentSeats.filter((s) => Seat.isList(s)));
      this.listFilteredSeats = this.mapSeatsVisible.seats.filter((s) => !Seat.isList(s));
    }

    // Seat search index. Built from listFilteredSeats (not mapSeatsVisible.seats) so that on
    // list elections the autocomplete and postcode-lookup paths only see constituencies — list
    // seats have no map polygon to zoom to, so users reach them via the region table.
    // Two parallel structures are produced:
    //   - currentSeatNameByKey: lookup-key → display name, used to resolve a typed query or
    //     postcode-API constituency name back to the canonical seat name.
    //   - seatSearchNames: alphabetised display names for the autocomplete dropdown.
    // Dedupe-by-key drops collisions where two seats normalise to the same lookup key
    // (e.g. accent variants); the first wins.
    this.currentSeatNameByKey = new Map();
    const seatNames = [];
    this.listFilteredSeats.forEach((seat) => {
      const seatName = String(seat?.seat || '').trim();
      if (!seatName) return;
      const key = seatLookupKey(seatName);
      if (this.currentSeatNameByKey.has(key)) return;
      this.currentSeatNameByKey.set(key, seatName);
      seatNames.push(seatName);
    });
    seatNames.sort((a, b) => a.localeCompare(b));
    this.seatSearchNames = seatNames;
  }

  /**
   * Refreshes the vote-totals fields that depend on the active tab plus the comparison
   * column flags: `voteTotals.columns.{votes, votePct}` and the
   * filteredSeatsSummary / filteredSeatsComparisonSummary aggregates. Called by
   * setupMapData and by the vote-totals tab click handler — the latter avoids the full
   * setupMapData (filters, choropleth, search index) since none of that depends on the
   * active tab.
   *
   * Vote-percent columns are gated by the active tab only — Holyrood 'all' mixes
   * constituency vote counts with list seat counts (ElectionSummary.summarize counts only
   * constituency votes in that mode while seat counts include both, so the mismatch is
   * misleading). The raw vote-count column is gated by tab AND by election capability —
   * predict / model / referendum elections have no meaningful raw counts but still
   * display vote shares. The comparison column is toggled directly from comparisonSummary in the render.
   * @returns {void}
   */
  recomputeVoteTotalsForMode() {
    const electionAllowsVoteCounts = !(this.currentElection.model || this.isReferendumType || this.view === 'predict');
    const tabAllowsVotes = !this.hasListSeats || this.voteTotals.mode !== 'all';
    this.voteTotals.columns.votePct = tabAllowsVotes;
    this.voteTotals.columns.votes = tabAllowsVotes && electionAllowsVoteCounts;

    this.filteredSeatsSummary = ElectionSummary.summarize(this.mapSeatsVisible.seats, this.voteTotals.mode);
    this.filteredSeatsComparisonSummary = this.comparisonSeats.length
      ? ElectionSummary.summarize(this.mapSeatsVisible.comparisonSeats, this.voteTotals.mode)
      : null;

    // TODO: remove once the resize-driven renderVoteTotals callers and the predict
    // summary builders read state.filteredSeatsSummary / state.filteredSeatsComparisonSummary
    // directly (see also the activateElection mirrors guarded by the same TODO).
    window.__mapsCurrentSummary = this.filteredSeatsSummary;
    window.__mapsComparisonSummary = this.filteredSeatsComparisonSummary;
  }

  /**
   * Recomputes mapSeatsVisible.{seatKeys, seats, comparisonSeats} by applying the active mapFilters
   * to electionData.currentSeats. Reads _state.comparisonSeatsByKey rather than
   * comparisonElectionData.seatsByKey because predict mode reassigns the _state mirror to the
   * predict baseline index, which then becomes the source of truth for gains-filtering.
   * @returns {void}
   */
  applyMapFilters() {
    // TODO remove during refactor
    const comparisonSeatsByKey = _state.comparisonSeatsByKey;
    const seatKeys = new Set();
    this.electionData.currentSeats.forEach((seat) => {
      const seatKey = seatLookupKey(seat.seat);
      const comparisonSeat = comparisonSeatsByKey.get(seatKey) || null;
      if (Seat.matchesPrimaryFilters(seat, comparisonSeat, this.mapFilters, this.currentElection.byElectionSeats)) {
        seatKeys.add(seatKey);
      }
    });
    this.mapSeatsVisible.seatKeys = seatKeys;
    this.mapSeatsVisible.seats = this.electionData.currentSeats.filter((seat) => seatKeys.has(seatLookupKey(seat.seat)));
    this.mapSeatsVisible.comparisonSeats = Array.from(seatKeys)
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
   * Returns true when both choropleth dropdowns have been changed from their defaults
   * (type 'none', party 'all'), meaning the user has selected a real choropleth metric.
   * @returns {boolean}
   */
  choroplethOptionsSelected() {
    return this.mapChoropleths.type !== 'none' && this.mapChoropleths.party !== 'all';
  }

  /**
   * Builds the choropleth rendering configuration for visible seats (this.mapSeatsVisible.seatKeys).
   * Returns { enabled: false } when no choropleth is selected.
   * For voteShareChange returns a diverging red-white-blue scale; for voteShare returns a white-to-party-colour scale.
   * Includes valueBySeatKey, toColour, and legend metadata.
   * Sets this.choroplethConfig. { enabled: false } when no choropleth is active;
   * otherwise enabled is true with valueBySeatKey, toColour, and legend metadata.
   * legendText is absent for the referendum kind, which uses only the legend object.
   * @returns {void}
   */
  buildChoroplethConfig() {
    const visibleSeatKeys = this.mapSeatsVisible.seatKeys;
    if (!this.choroplethOptionsSelected() && !this.isReferendumType) {
      this.choroplethConfig = { enabled: false };
      return;
    }
    if (visibleSeatKeys.size === 0) {
      this.choroplethConfig = { enabled: false };
      return;
    }

    const config = { enabled: true };
    const valueBySeatKey = new Map();
    const values = [];

    // Referendum elections render a Leave-share map by default, but a user can still
    // pick choropleth dropdown options to override that. Only treat this as the
    // referendum special case when the dropdowns are at their defaults.
    const isReferendumTypeWithDefaults = this.isReferendumType && !this.choroplethOptionsSelected();
    const isDelta = this.mapChoropleths.type === 'voteShareChange';

    // Walk all seats to compute the choropleth metric value for each visible seat.
    // Seats outside visibleSeatKeys are skipped (they are filtered out on the map).
    // Null metric values (e.g. a voteShareChange where the comparison seat is missing)
    // are silently dropped rather than breaking the colour scale.
    this.electionData.currentSeats.forEach((seat) => {
      const seatKey = seatLookupKey(seat.seat);
      if (!visibleSeatKeys.has(seatKey)) return;
      let value;
      if (isReferendumTypeWithDefaults) {
        value = Seat.voteSharePct(seat, 'leave');
      } else {
        // TODO: remove _state.comparisonSeatsByKey access once comparison data is fully on AppState
        const comparisonSeat = _state.comparisonSeatsByKey.get(seatKey) || null;
        value = Seat.choroplethValue(seat, comparisonSeat, isDelta, this.mapChoropleths.party);
        if (value === null) return;
      }
      valueBySeatKey.set(seatKey, value);
      values.push(value);
    });

    // Belt-and-braces: visibleSeatKeys.size > 0 above gets the common case, but we can still
    // end up empty here if all seats return null metric values (e.g. voteShareChange with no comparison data).
    if (!values.length) {
      this.choroplethConfig = { enabled: false };
      return;
    }

    config.valueBySeatKey = valueBySeatKey;

    const minValue = Math.min(...values);
    const maxValue = Math.max(...values);
    // Referendum-with-defaults uses a fixed Leave/Remain colour scale with no party association,
    // so partyLabel and partyColour are unused in that branch. Set to empty string to avoid
    // passing them into legend text that doesn't need them.
    const partyLabel = isReferendumTypeWithDefaults ? '' : manifest.labelParty(this.mapChoropleths.party);
    const partyColour = isReferendumTypeWithDefaults ? '' : manifest.colourParty(this.mapChoropleths.party);

    // Near-white used as the neutral/zero anchor across all colour scales.
    const neutralColour = '#f8fbff';

    let kind;
    if (isReferendumTypeWithDefaults) kind = 'referendum';
    else if (isDelta) kind = 'delta';
    else if (Math.abs(maxValue - minValue) < 1e-9) kind = 'uniform';
    else kind = 'absolute';

    switch (kind) {
      case 'referendum': {
        // Three-point scale anchored at 50% (the political threshold). Low leave share
        // maps to the Remain colour, high leave share to the Leave colour. Both colours
        // come from the manifest so they stay in sync with the party list.
        // TODO: generalise this branch to be referendum-agnostic (configurable party pair + threshold)
        const leaveColour = manifest.colourParty('leave');
        const remainColour = manifest.colourParty('remain');
        const scale = d3.scaleLinear()
          .domain([minValue, 50, maxValue])
          .range([remainColour, neutralColour, leaveColour]);
        config.toColour = (value) => scale(value);
        config.legend = {
          isDelta: true,
          title: 'Leave vote share',
          startColour: remainColour,
          midColour: neutralColour,
          endColour: leaveColour,
          minLabel: `${formatPct(minValue)}%`,
          midLabel: '50%',
          maxLabel: `${formatPct(maxValue)}%`,
        };
        break;
      }
      case 'delta': {
        // Symmetric diverging scale centred on zero. maxAbs is the largest absolute swing
        // across visible seats, so both ends are equidistant and colour distance correctly
        // encodes magnitude. The tiny floor prevents a degenerate single-point domain when
        // all swings happen to be exactly zero.
        const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 0.000001);
        const scale = d3.scaleLinear().domain([-maxAbs, 0, maxAbs]).range(['#991b1b', neutralColour, '#1d4ed8']);
        config.toColour = (value) => scale(value);
        config.legendText = `${partyLabel} vote share change (${formatSigned(maxAbs, 2)} max abs)`;
        config.legend = {
          isDelta: true,
          title: `${partyLabel} vote share change`,
          startColour: '#991b1b',
          midColour: neutralColour,
          endColour: '#1d4ed8',
          minLabel: formatSigned(-maxAbs, 2),
          midLabel: '0',
          maxLabel: formatSigned(maxAbs, 2),
        };
        break;
      }
      case 'uniform': {
        // Every visible seat has an identical value — there is no range to interpolate.
        // Paint flat in the party colour to signal presence without implying a gradient.
        config.toColour = () => partyColour;
        config.legendText = `${partyLabel} vote share (uniform)`;
        break;
      }
      case 'absolute': {
        // Linear near-white → party-colour scale across the actual value range.
        // Darker seats have a stronger showing for the selected party.
        const scale = d3.scaleLinear().domain([minValue, maxValue]).range([neutralColour, partyColour]);
        config.toColour = (value) => scale(value);
        config.legendText = `${partyLabel} vote share (${formatPct(minValue)} to ${formatPct(maxValue)})`;
        config.legend = {
          isDelta: false,
          title: `${partyLabel} vote share`,
          startColour: neutralColour,
          endColour: partyColour,
          minLabel: formatPct(minValue),
          maxLabel: formatPct(maxValue),
        };
        break;
      }
    }

    this.choroplethConfig = config;
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


// Shared mutable state for the electionmaps application.
// All modules import these objects and mutate their properties directly.
// A single shared object reference means every importer sees the same state.

import * as d3 from '../../site/vendor/d3.v7.esm.js';
import { normalizeRegionKey, formatInt, formatPct, formatSigned, seatLookupKey, getRegionLabel, roundShare, base64urlEncode, base64urlDecode } from './utils.js';
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
   * Returns the per-parliament feature config (anchor/baseline election ids etc.),
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
   * @returns {string} Pre-formatted subtitle string ready to pass to `renderHeader`.
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
   * Builds an ElectionData from an already-normalised Seat array, skipping the raw-results
   * normalisation step. Used by predict mode to wrap a projected seat array as a regular
   * ElectionData so the existing render pipeline consumes it unchanged.
   * @param {Seat[]} seats - Already-normalised Seat instances.
   * @param {string|null} electionName - Display label (e.g. 'Predict 2029').
   * @returns {ElectionData}
   */
  static fromSeats(seats, electionName = null) {
    const instance = Object.create(ElectionData.prototype);
    instance.electionName = electionName;
    instance.baseSeats = seats;
    instance.currentSeats = seats;
    instance.seatsByKey = ElectionData.buildSeatIndex(seats);
    instance.summary = new ElectionSummary(seats, electionName);
    return instance;
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

    /** Seat list panel render state. Rebuilt on every renderSeatList call.
     * - rowByKey {Map<string, HTMLElement>} — seat lookup key → rendered row button.
     *   Used by setSelectedSeatRowByKey and the zoomToSeat flow to find a row without querying the DOM.
     * - selected {HTMLElement|null} — the currently highlighted row, or null when nothing is selected. */
    this.seatList = {
      rowByKey: new Map(),
      selected: null,
    };

    /** Active predict-mode model (WestminsterPredict | HolyroodPredict | null). Populated by
     * activatePredictMode in electionmaps.js; null whenever view !== 'predict'. */
    this.predictModel = null;
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
   * Stores topology JSON as state.mapData.
   * @param {object} mapData - Topology JSON.
   * @returns {void}
   */
  setMapData(mapData) {
    this.mapData = mapData;
  }

  /**
   * Wraps raw results JSON in a new ElectionData and stores it as the active election.
   * @param {object} resultsData - Parsed pf-results-v4 JSON.
   * @param {string|null} [electionName] - Display label (e.g. "2024 Election").
   * @returns {void}
   */
  setElectionData(resultsData, electionName = null) {
    this.electionData = new ElectionData(resultsData, electionName);
  }

  /**
   * Wraps an already-normalised Seat array as an ElectionData and stores it as the
   * active election. Used by predict mode to install a projected seat array via the
   * regular state.electionData slot.
   * @param {Seat[]} seats - Already-normalised Seat instances.
   * @param {string|null} [electionName] - Display label (e.g. "Predict 2029").
   * @returns {void}
   */
  setElectionDataFromSeats(seats, electionName = null) {
    this.electionData = ElectionData.fromSeats(seats, electionName);
  }

  /**
   * Wraps raw results JSON in a new ElectionData and stores it as the comparison
   * election (used for swing calculations and the gains-from-baseline column).
   * @param {object} resultsData - Parsed pf-results-v4 JSON.
   * @param {string|null} [electionName] - Display label.
   * @returns {void}
   */
  setComparisonElectionData(resultsData, electionName = null) {
    this.comparisonElectionData = new ElectionData(resultsData, electionName);
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

    this.hasListSeats = this.electionData.currentSeats.some((s) => Seat.isList(s));

    this.recomputeVoteTotalsForMode();

    // List-seat specialisation. Holyrood elections need a per-region rollup for the
    // region-table overlay (list seats render there rather than on the map), and the
    // seat-list panel shows constituencies only — list seats appear in the region table
    // instead. Westminster / by-elections / referenda have no list seats, so both vars
    // stay at their defaults: no region rollup, and the seat list shows every visible
    // seat unmodified.
    this.listRegionSummary = this.hasListSeats
      ? ElectionSummary.summarizeByRegion(this.electionData.currentSeats.filter((s) => Seat.isList(s)))
      : null;
    this.listFilteredSeats = this.hasListSeats
      ? this.mapSeatsVisible.seats.filter((s) => !Seat.isList(s))
      : this.mapSeatsVisible.seats;

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
    this.filteredSeatsComparisonSummary = this.comparisonElectionData?.currentSeats.length
      ? ElectionSummary.summarize(this.mapSeatsVisible.comparisonSeats, this.voteTotals.mode)
      : null;

  }

  /**
   * Recomputes mapSeatsVisible.{seatKeys, seats, comparisonSeats} by applying the active mapFilters
   * to electionData.currentSeats. Reads comparisonElectionData.seatsByKey.
   * @returns {void}
   */
  applyMapFilters() {
    const comparisonSeatsByKey = this.comparisonElectionData?.seatsByKey  || new Map();
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
        const comparisonSeat = this.comparisonElectionData?.seatsByKey.get(seatKey) ?? null;
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

  /**
   * Updates voteTotals.sort: toggles direction if the same key is re-selected, otherwise
   * switches to the new key with a default direction.
   * @param {string} sortKey - Column key to sort by (e.g. 'seats', 'votes', 'party').
   * @returns {void}
   */
  setSortDirection(sortKey) {
    if (this.voteTotals.sort.key === sortKey) {
      this.voteTotals.sort.direction = this.voteTotals.sort.direction === 'asc' ? 'desc' : 'asc';
      return;
    }
    this.voteTotals.sort.key = sortKey;
    this.voteTotals.sort.direction = sortKey === 'party' ? 'asc' : 'desc';
  }

  /**
   * Resets all primary filter state (party, region, second party, majority range, gains toggle)
   * to their defaults. Does not sync UI controls or re-render — callers are responsible for both.
   * @returns {void}
   */
  resetFilters() {
    this.mapFilters.party = 'all';
    this.mapFilters.region = 'all';
    this.mapFilters.secondParty = 'all';
    this.mapFilters.majorityMin = 0;
    this.mapFilters.majorityMax = 100;
    this.mapFilters.gainsOnly = false;
  }

  /**
   * Resets choropleth type and party to defaults.
   * Does not sync UI controls or re-render — callers are responsible for both.
   * @returns {void}
   */
  resetChoropleths() {
    this.mapChoropleths.type = 'none';
    this.mapChoropleths.party = 'all';
  }
}

export const state = new AppState();

// ─── Predict ─────────────────────────────────────────────────────────────────
//
// Predict mode lets the user enter per-region per-party vote shares, computes regional
// uniform-swing deltas vs. a baseline election, and projects a synthetic future result.
// The projected seats are wrapped in an ElectionData and assigned to state.electionData
// so the regular render pipeline (map / seat list / vote totals / subtitle) consumes them
// unchanged. The original baseline ElectionData becomes state.comparisonElectionData so
// gains-from-baseline and the comparison column work without further wiring.

/**
 * Computes baseline regional vote share percentages from a seat array. For each party in
 * `modelledPartyKeys` and each distinct seat region, returns share = (regional party votes
 * / regional turnout) × 100. When `aggregateConfig` is supplied, regions matching its
 * `isMember` predicate also contribute to a synthetic aggregate row keyed by `aggregateConfig.key`
 * (e.g. 'england' for Westminster, 'scotland' for Holyrood). Rounded to integers; if rounding
 * pushes a region's total above 100 the smallest non-zero party is decremented to compensate.
 * @param {Seat[]} seats
 * @param {string[]} modelledPartyKeys - Parties to compute shares for (others are ignored).
 * @param {{key: string, isMember: (regionKey: string) => boolean} | null} [aggregateConfig]
 * @returns {Map<string, Map<string, number>>} regionKey → partyKey → share (0–100).
 */
function buildBaselineShares(seats, modelledPartyKeys, aggregateConfig = null) {
  const byRegion = new Map();
  const ensureRegion = (regionKey) => {
    if (!byRegion.has(regionKey)) byRegion.set(regionKey, { totalVotes: 0, votesByParty: new Map() });
    return byRegion.get(regionKey);
  };

  (seats || []).forEach((seat) => {
    const regionKey = normalizeRegionKey(seat.region);
    if (!regionKey) return;
    const turnout = Number(seat.turnout || 0);
    if (turnout <= 0) return;

    const region = ensureRegion(regionKey);
    region.totalVotes += turnout;
    modelledPartyKeys.forEach((partyKey) => {
      const partyVotes = Number(seat.votes?.[partyKey] || 0);
      region.votesByParty.set(partyKey, Number(region.votesByParty.get(partyKey) || 0) + partyVotes);
    });

    if (aggregateConfig && aggregateConfig.isMember(regionKey)) {
      const agg = ensureRegion(aggregateConfig.key);
      agg.totalVotes += turnout;
      modelledPartyKeys.forEach((partyKey) => {
        const partyVotes = Number(seat.votes?.[partyKey] || 0);
        agg.votesByParty.set(partyKey, Number(agg.votesByParty.get(partyKey) || 0) + partyVotes);
      });
    }
  });

  const result = new Map();
  byRegion.forEach((stats, regionKey) => {
    const partyMap = new Map();
    modelledPartyKeys.forEach((partyKey) => {
      const votes = Number(stats.votesByParty.get(partyKey) || 0);
      const share = stats.totalVotes > 0 ? (votes / stats.totalVotes) * 100 : 0;
      partyMap.set(partyKey, roundShare(share));
    });

    // Rounding can push the integer sum above 100; trim the smallest non-zero party.
    let sum = 0;
    partyMap.forEach((v) => { sum += v; });
    if (sum > 100) {
      let minKey = null;
      let minVal = Infinity;
      partyMap.forEach((v, k) => { if (v > 0 && v < minVal) { minVal = v; minKey = k; } });
      // Clamp at zero — overshoot is bounded by ~3pp in practice but the guard prevents
      // a negative share leaking through if a future party set raises the bound.
      if (minKey) partyMap.set(minKey, Math.max(0, minVal - (sum - 100)));
    }
    result.set(regionKey, partyMap);
  });

  return result;
}

/**
 * Projects a single seat by applying regional swings to baseline vote shares. Modelled
 * party shares are adjusted by their swing; any remaining share is redistributed pro rata
 * to non-modelled parties (or assigned to 'others' if none exist). Returns a new Seat with
 * updated votes, preserved turnout, and recomputed winner.
 * @param {Seat} baseSeat
 * @param {Map<string, Map<string, number>>} swingsByRegionByParty - regionKey → partyKey → swing pp.
 * @param {string[]} modelledPartyKeys - Parties whose share is adjusted by swing (rest absorb residue).
 * @param {{key: string, isMember: (regionKey: string) => boolean} | null} [aggregateConfig]
 *   When set, sub-regions whose own swing is unset fall back to the aggregate's swing.
 * @returns {Seat}
 */
function projectSeatUniformSwing(baseSeat, swingsByRegionByParty, modelledPartyKeys, aggregateConfig = null) {
  const totalVotes = baseSeat.turnout;
  if (totalVotes <= 0) return new Seat(baseSeat);

  const regionKey = normalizeRegionKey(baseSeat.region);
  const baseVotes = baseSeat.votes || {};

  // Resolve the swing for a party: prefer the seat's own region, fall back to the
  // configured aggregate (e.g. 'england', 'scotland') for sub-regions when the region has
  // no direct entry.
  const resolveSwing = (partyKey) => {
    const direct = Number(swingsByRegionByParty.get(regionKey)?.get(partyKey) || 0);
    if (Math.abs(direct) > 1e-9) return direct;
    if (aggregateConfig && aggregateConfig.isMember(regionKey)) {
      return Number(swingsByRegionByParty.get(aggregateConfig.key)?.get(partyKey) || 0);
    }
    return 0;
  };

  let adjustedTrackedSum = 0;
  const adjustedTrackedShare = new Map();
  modelledPartyKeys.forEach((partyKey) => {
    const baseShare = (Number(baseVotes[partyKey] || 0) / totalVotes) * 100;
    const adjusted = Math.max(0, baseShare + resolveSwing(partyKey));
    adjustedTrackedShare.set(partyKey, adjusted);
    adjustedTrackedSum += adjusted;
  });

  const otherShare = Math.max(0, 100 - adjustedTrackedSum);
  const projected = {};
  adjustedTrackedShare.forEach((share, partyKey) => {
    if (share <= 0) return;
    projected[partyKey] = (share / 100) * totalVotes;
  });

  const nonTracked = Object.entries(baseVotes).filter(([k]) => !modelledPartyKeys.includes(k));
  const nonTrackedTotal = nonTracked.reduce((sum, [, v]) => sum + Number(v || 0), 0);
  if (otherShare > 0) {
    if (nonTrackedTotal > 0) {
      nonTracked.forEach(([partyKey, votes]) => {
        const weight = Number(votes || 0) / nonTrackedTotal;
        projected[partyKey] = ((otherShare * weight) / 100) * totalVotes;
      });
    } else {
      projected.others = (otherShare / 100) * totalVotes;
    }
  }

  // Re-scale so projected votes sum to baseSeat.turnout (rounding inside the share math
  // can drift by a fraction of a vote).
  const projectedSum = Object.values(projected).reduce((s, v) => s + Number(v || 0), 0);
  if (projectedSum > 0 && Math.abs(projectedSum - totalVotes) > 1e-6) {
    const scale = totalVotes / projectedSum;
    Object.keys(projected).forEach((k) => { projected[k] = projected[k] * scale; });
  }

  const winner = Object.entries(projected)
    .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))[0]?.[0] || baseSeat.winner || 'others';

  return new Seat({ ...baseSeat, votes: projected, turnout: totalVotes, winner });
}

/**
 * Runs D'Hondt seat allocation for one Holyrood region, deducting constituency wins from
 * the divisor so list seats top up under-represented parties.
 * @param {Map<string, number>} votesByParty
 * @param {number} nSeats
 * @param {Map<string, number>} constWinsByParty
 * @returns {string[]} Ordered array of winning party keys (one per seat).
 */
function dhondtAllocate(votesByParty, nSeats, constWinsByParty = new Map()) {
  const listSeatsWon = new Map();
  for (const key of votesByParty.keys()) listSeatsWon.set(key, 0);
  const winners = [];
  for (let i = 0; i < nSeats; i++) {
    let bestParty = null;
    let bestQuotient = -Infinity;
    for (const [party, votes] of votesByParty) {
      if (votes <= 0) continue;
      const total = (listSeatsWon.get(party) || 0) + (constWinsByParty.get(party) || 0);
      const quotient = votes / (total + 1);
      if (quotient > bestQuotient) { bestQuotient = quotient; bestParty = party; }
    }
    if (bestParty != null) {
      listSeatsWon.set(bestParty, (listSeatsWon.get(bestParty) || 0) + 1);
      winners.push(bestParty);
    }
  }
  return winners;
}

/**
 * Abstract base for predict models. Owns the baseline election, the user-input share map,
 * and the projection entry point. Reads/writes go through the active input/baseline maps,
 * which subclasses expose via currentInputMap()/currentBaselineMap() — Westminster has a
 * single pair, Holyrood swaps between const and list pairs as the active tab changes.
 *
 * Subclasses implement parliament-specific row/column structure (regions, parties,
 * gridSections), the projection algorithm (project), and serialization. Everything else
 * — share reads/writes, swing computation, validation, the 'other' share helper — is
 * shared.
 */
class PredictModel {
  /**
   * Reads the baseline election, map topology, and region-label lookup from module-level
   * `state` rather than holding its own references. Callers MUST populate
   * `state.comparisonElectionData`, `state.mapData`, and `state.currentRegionLabelsByKey`
   * before constructing the model — the constructor's call to `buildBaselineShares` (in
   * subclasses) reads `this.baseSeats`, which resolves through `this.baselineElectionData`
   * → `state.comparisonElectionData.currentSeats`.
   *
   * @param {number|undefined} nextElectionYear - Display year (e.g. 2029) for "Predict YYYY" labels.
   * @param {object} config - The `predict` block from manifest.parliamentFeatures. Recognised keys:
   *   - `model` {'westminster'|'holyrood'} — class selector read by `predictModelClassFor`
   *     before construction; the model class itself does not consume this field.
   *   - `modelledPartyKeys` {string[]} — parties carried through `buildBaselineShares` and
   *     `projectSeatUniformSwing`. Any party not in this list is folded into 'other'.
   *   - `aggregate` {{key: string, label?: string, excludeRegions?: string[]} | null} — synthetic
   *     aggregate-row config (see `#buildAggregateConfig`). Omit / null = no aggregate row.
   *   - `gridSections` {Array<SectionConfig>} — declarative grid layout. Each section has:
   *     `id`, `columnKeys`, optional `containsAggregate` (the section that renders the
   *     aggregate row + its member sub-rows when expanded), optional `extraRegionKeys`
   *     (non-aggregate regions that still belong here, e.g. Westminster's Scotland and
   *     Wales sit in GB), optional `regionKeys` (explicit non-aggregate regions, e.g.
   *     Westminster's NI), optional `blankRegionHeader` (rendering hint for dom.js).
   *   - `virtualColumns` {Object<string, Object<string, string>>} — maps a virtual column
   *     key (e.g. `'nat'`) to a per-region party lookup (e.g. `{ scotland: 'snp' }`).
   *     Resolved by `resolveColumnPartyKey`; regions without an entry produce a blank
   *     column (the virtual key is dropped from `parties(rk)`).
   *   - `regionLabelOverrides` {Object<string, string>} — normalised regionKey → short
   *     display label for the predict grid (e.g. `{ northernireland: 'N Ireland' }`).
   *     Region rows fall back to their map label when no override is present.
   *   - `tabs` {Array<{key: string, label: string}>} — Holyrood-only ballot tabs (typically
   *     constituency + list). First entry's `key` is the initial `activeTab`.
   */
  constructor(nextElectionYear, config) {
    this.nextElectionYear = nextElectionYear;
    this.config = config || {};
    this.modelledPartyKeys = this.config.modelledPartyKeys || [];
    this.aggregateConfig = PredictModel.#buildAggregateConfig(this.config.aggregate);
    this.virtualColumns = this.config.virtualColumns || {};
    this.regionLabelOverrides = this.config.regionLabelOverrides || {};
    this.gridSectionsConfig = this.config.gridSections || [];
    this.aggregateExpanded = false;
  }

  /** @returns {ElectionData} Baseline election the projection runs against. */
  get baselineElectionData() { return state.comparisonElectionData; }

  /** @returns {object} Topology JSON for the baseline map. */
  get baselineMapData() { return state.mapData; }

  /** @returns {Map<string, string>} regionKey → display label for the baseline map. */
  get regionLabelsByKey() { return state.currentRegionLabelsByKey; }

  /**
   * Builds the aggregate-row configuration from the manifest's `predict.aggregate` block.
   *
   * The aggregate is a synthetic region that lives only inside the predict model — it
   * doesn't exist in the data files. Its baseline shares are computed by summing every
   * `isMember` region's votes/turnout in `buildBaselineShares`; its row appears in the
   * grid courtesy of `buildRowsWithAggregate`. When the user enters values into the
   * aggregate row, `projectSeatUniformSwing` falls back to that swing for any sub-region
   * whose own swing is zero, applying the user's national-level intent uniformly.
   *
   * Westminster supplies `{ key: 'england', label: 'England', excludeRegions: ['scotland',
   * 'wales', 'northernireland'] }` so only English regions feed the aggregate. Holyrood
   * supplies `{ key: 'scotland', label: 'Scotland', excludeRegions: [] }` — every Holyrood
   * electoral region is Scottish, and `'scotland'` doesn't collide with any real region
   * key in the Holyrood map, so the synthetic aggregate is safe to key as `'scotland'`.
   *
   * @param {{key: string, label?: string, excludeRegions?: string[]} | null | undefined} aggCfg
   *   The manifest block, or absent.
   * @returns {{key: string, label: string, isMember: (rk: string) => boolean} | null}
   *   The frozen config, or null when the parliament has no aggregate (the rest of the
   *   predict pipeline treats null as "skip aggregate handling entirely").
   */
  static #buildAggregateConfig(aggCfg) {
    if (!aggCfg || !aggCfg.key) return null;
    const aggKey = normalizeRegionKey(aggCfg.key);
    const exclude = new Set((aggCfg.excludeRegions || []).map((rk) => normalizeRegionKey(rk)));
    return {
      key: aggKey,
      label: aggCfg.label || aggCfg.key,
      isMember: (rk) => {
        const k = normalizeRegionKey(rk);
        if (!k) return false;
        if (k === aggKey) return false;
        return !exclude.has(k);
      },
    };
  }

  /** @returns {Seat[]} Baseline seats (read-only reference). */
  get baseSeats() { return this.baselineElectionData.currentSeats; }

  /**
   * Computes baseline shares for a seat array using this model's modelled parties +
   * aggregate config. Subclasses call this in their constructor (Westminster: once over
   * all seats; Holyrood: once each over the const and deduped-list slices).
   * @param {Seat[]} seats
   * @returns {Map<string, Map<string, number>>}
   */
  baselineFor(seats) {
    return buildBaselineShares(seats, this.modelledPartyKeys, this.aggregateConfig);
  }

  /**
   * Returns the manifest's grid sections walked to produce ordered region rows. For each
   * section in declared order:
   * - If the section has `containsAggregate` and the model has an aggregate config,
   *   prepends the aggregate row + (when expanded) every aggregate-member sub-row,
   *   alpha-sorted by label.
   * - Then appends each `extraRegionKeys` entry in declared order.
   * - Then appends each `regionKeys` entry in declared order.
   * Every emitted row is tagged with `section: section.id` so `gridSections()` can split
   * them into separate tables.
   * @returns {Array<object>}
   */
  regions() {
    const labels = this.regionLabelsByKey;
    const allByNorm = new Map();
    Array.from(labels.entries()).forEach(([rk, label]) => {
      allByNorm.set(normalizeRegionKey(rk), { regionKey: rk, regionLabel: label });
    });

    const out = [];
    this.gridSectionsConfig.forEach((section) => {
      if (section.containsAggregate && this.aggregateConfig) {
        const subs = Array.from(allByNorm.values())
          .filter((r) => this.aggregateConfig.isMember(r.regionKey))
          .sort((a, b) => a.regionLabel.localeCompare(b.regionLabel));
        out.push(...this.buildRowsWithAggregate(subs, section.id));
      }
      const explicit = [...(section.extraRegionKeys || []), ...(section.regionKeys || [])];
      explicit.forEach((rk) => {
        const found = allByNorm.get(normalizeRegionKey(rk));
        if (found) out.push({ ...found, section: section.id });
      });
    });
    return out.map((r) => ({
      ...r,
      predictLabel: this.regionLabelOverrides[normalizeRegionKey(r.regionKey)] || r.regionLabel,
    }));
  }

  /**
   * Returns the columns to render for a region: the section's `columnKeys` with virtual
   * columns resolved to per-region parties (or dropped when blank).
   * @param {string} regionKey
   * @returns {string[]}
   */
  parties(regionKey) {
    const section = this.#sectionForRegion(regionKey);
    if (!section) return [];
    return section.columnKeys
      .map((k) => this.resolveColumnPartyKey(regionKey, k))
      .filter((k) => k !== null && k !== undefined);
  }

  /**
   * Resolves a section's column key to the actual party key for the given region. Plain
   * column keys pass through unchanged; keys present in `virtualColumns` are looked up by
   * normalised regionKey and may resolve to null (column blank for that region).
   * @param {string} regionKey
   * @param {string} columnKey
   * @returns {string|null}
   */
  resolveColumnPartyKey(regionKey, columnKey) {
    const virtual = this.virtualColumns[columnKey];
    if (!virtual) return columnKey;
    return virtual[normalizeRegionKey(regionKey)] || null;
  }

  /**
   * Builds the per-table grid sections rendered by dom.js. Driven entirely by
   * `gridSectionsConfig`; rows are partitioned from `regions()` by section id.
   * @returns {Array<object>}
   */
  gridSections() {
    const allRegions = this.regions();
    return this.gridSectionsConfig.map((section) => ({
      id: section.id,
      title: null,
      columnKeys: [...section.columnKeys],
      regions: allRegions.filter((r) => r.section === section.id),
      blankRegionHeader: !!section.blankRegionHeader,
    }));
  }

  /**
   * Returns the manifest section a given region belongs to. Resolution walks
   * `gridSectionsConfig` in declared order, matching on aggregate membership (incl. the
   * aggregate key itself), `extraRegionKeys`, then `regionKeys`. First match wins.
   * @param {string} regionKey
   * @returns {object|null}
   */
  #sectionForRegion(regionKey) {
    const rk = normalizeRegionKey(regionKey);
    for (const section of this.gridSectionsConfig) {
      if (section.containsAggregate && this.aggregateConfig) {
        if (rk === this.aggregateConfig.key) return section;
        if (this.aggregateConfig.isMember(rk)) return section;
      }
      const explicits = [...(section.extraRegionKeys || []), ...(section.regionKeys || [])];
      if (explicits.some((k) => normalizeRegionKey(k) === rk)) return section;
    }
    return null;
  }

  /** Override: returns the user-input map currently being read/written. Westminster always
   * returns the same map; Holyrood swaps based on activeTab. */
  currentInputMap() { return new Map(); }

  /** Override: returns the baseline map paired with the current input map (so getShare's
   * fallback uses the matching ballot's baseline). */
  currentBaselineMap() { return new Map(); }

  /** Writes a single share input into the active input map. */
  setShare(regionKey, partyKey, value) {
    const input = this.currentInputMap();
    if (!input.has(regionKey)) input.set(regionKey, new Map());
    input.get(regionKey).set(partyKey, roundShare(value));
  }

  /** Reads the current share (entered if set, else baseline). */
  getShare(regionKey, partyKey) {
    const cached = this.currentInputMap().get(regionKey)?.get(partyKey);
    if (Number.isFinite(cached)) return Number(cached);
    return this.getBaseline(regionKey, partyKey);
  }

  /** Reads the rounded baseline share for a region/party from the active baseline map. */
  getBaseline(regionKey, partyKey) {
    return roundShare(this.currentBaselineMap().get(regionKey)?.get(partyKey) ?? 0);
  }

  /** Returns the implied 'other' share for a region (100 − sum of party shares). */
  getOtherShare(regionKey) {
    const sum = this.parties(regionKey).reduce((s, p) => s + this.getShare(regionKey, p), 0);
    return roundShare(100 - sum);
  }

  /** Returns rows whose entered shares sum > 100. */
  validate() {
    const invalid = [];
    this.regions().forEach((row) => {
      const sum = this.parties(row.regionKey).reduce((s, p) => s + this.getShare(row.regionKey, p), 0);
      if (sum > 100) invalid.push({ ...row, total: roundShare(sum) });
    });
    return invalid;
  }

  /**
   * Builds a regionKey → partyKey → swing-pp map from explicit input/baseline pair. Zero
   * swings are dropped. Subclasses call this once (Westminster) or twice (Holyrood, for
   * const and list passes) inside project().
   * @param {Map<string, Map<string, number>>} inputMap
   * @param {Map<string, Map<string, number>>} baselineMap
   * @returns {Map<string, Map<string, number>>}
   */
  buildSwings(inputMap, baselineMap) {
    const swings = new Map();
    this.regions().forEach((row) => {
      const partyMap = new Map();
      this.parties(row.regionKey).forEach((partyKey) => {
        const baseline = roundShare(baselineMap.get(row.regionKey)?.get(partyKey) ?? 0);
        const cached = inputMap.get(row.regionKey)?.get(partyKey);
        const input = Number.isFinite(cached) ? Number(cached) : baseline;
        const swing = input - baseline;
        if (Math.abs(swing) >= 1e-9) partyMap.set(partyKey, swing);
      });
      if (partyMap.size > 0) swings.set(row.regionKey, partyMap);
    });
    return swings;
  }

  /** Override: project the baseline seats into a new Seat[] from current inputs. */
  project() { return this.baseSeats.slice(); }

  /** Reset shared state. Subclasses override to also clear their input maps and ballot
   * tab, calling `super.reset()` to handle the aggregate flag. */
  reset() { this.aggregateExpanded = false; }

  /**
   * Builds the wire payload shared by every subclass `serialize()`. Returns '' when the
   * model has no overrides AND no aggregate-expanded flag — the empty payload is what
   * suppresses the `?predict=` query param entirely.
   * @param {Array} overrides - From `collectOverrides`. Shape varies per subclass.
   * @param {object} [extra] - Extra envelope keys (e.g. Holyrood's `{ h: 1 }` discriminator).
   * @returns {string}
   */
  buildSerializedPayload(overrides, extra = {}) {
    if (overrides.length === 0 && !this.aggregateExpanded) return '';
    return base64urlEncode(JSON.stringify({
      e: this.aggregateExpanded ? 1 : 0,
      r: overrides,
      ...extra,
    }));
  }

  /** Override: serialize to a URL-safe payload string. */
  serialize() { return ''; }

  /** Override: load a serialized payload. */
  deserialize(_payload) {}

  /** Override: load from a model-output simulation seat array (Apply current forecast). */
  loadSimulationShares(_simulationSeats) {}

  /**
   * Override hook: returns every input map subject to aggregate expand/collapse handling.
   *
   * Westminster has a single nested `Map<regionKey, Map<partyKey, share>>` and returns
   * `[this.inputByRegion]`. Holyrood maintains separate const and list ballots and
   * returns `[this.constInput, this.listInput]` so `setAggregateExpanded`'s propagation
   * + drop logic touches both ballots in lockstep, preventing a stale value from leaking
   * through after a tab switch.
   *
   * Default returns `[]` so a subclass without input maps (or `aggregateConfig === null`)
   * is a safe no-op when `setAggregateExpanded` runs.
   *
   * @returns {Map<string, Map<string, number>>[]}
   */
  inputMaps() { return []; }

  /**
   * Toggles the predict grid between collapsed (single aggregate row) and expanded
   * (aggregate + sub-region rows). No-ops when no aggregate is configured or the flag
   * already matches.
   *
   * On expand: for every input map returned by `inputMaps()`, copies the aggregate row's
   * party shares onto each empty sub-region (preserving the user's national-level intent
   * as a per-region starting point), then clears the aggregate row so sub-rows become the
   * source of truth. Sub-regions that already carry inputs (e.g. populated by a previous
   * Apply while collapsed) are not overwritten.
   *
   * On collapse: drops every sub-region entry from each input map. The aggregate row's
   * own inputs are kept, so a user who toggled Show → Hide retains any aggregate-level
   * value they had typed earlier.
   *
   * The propagation step is what fixes the otherwise-jarring transition where expanding
   * after typing "SNP 50%" at the national level would silently revert sub-rows to
   * baseline despite the projection still using the user's swing.
   *
   * @param {boolean} expanded - True to show sub-regions, false to collapse to aggregate.
   * @returns {void}
   */
  setAggregateExpanded(expanded) {
    if (!this.aggregateConfig) return;
    if (expanded === this.aggregateExpanded) return;
    const { key, isMember } = this.aggregateConfig;
    const subKeys = Array.from(this.regionLabelsByKey.keys()).filter((rk) => isMember(rk));

    const propagateAndClear = (input) => {
      const aggInputs = input.get(key);
      if (aggInputs && aggInputs.size > 0) {
        subKeys.forEach((rk) => {
          if (input.has(rk)) return;
          input.set(rk, new Map(aggInputs));
        });
      }
      input.delete(key);
    };
    const dropSubs = (input) => {
      Array.from(input.keys()).forEach((rk) => {
        if (rk !== key && isMember(rk)) input.delete(rk);
      });
    };

    this.inputMaps().forEach((input) => {
      if (expanded) propagateAndClear(input);
      else dropSubs(input);
    });
    this.aggregateExpanded = expanded;
  }

  /**
   * Loads regional shares from a simulation seat array into a target input map. Used by
   * subclass `loadSimulationShares` (the "Use current forecast" button).
   *
   * Computes per-region shares via `buildBaselineShares` (passing the aggregate config so
   * the synthetic aggregate is included in the result), then writes each share map into
   * `target` while honouring the current aggregate-expanded state:
   * - Expanded: skips the synthetic aggregate key. Sub-regions are the source of truth;
   *   writing the aggregate would shadow the sub-region values via the swing fallback.
   * - Collapsed: skips every `isMember` sub-region. Only the aggregate row is visible to
   *   the user, so pre-populating sub-rows would orphan that data. If the user later
   *   clicks "Show regions", `setAggregateExpanded(true)` then propagates the aggregate
   *   onto each sub-row cleanly, instead of revealing a stale sim breakdown that may
   *   conflict with what the user was just looking at.
   *
   * @param {Seat[]} seats - Source seats (already filtered to the relevant ballot type
   *   for Holyrood — const vs list — by the caller).
   * @param {Map<string, Map<string, number>>} target - Input map to populate (mutated).
   * @returns {void}
   */
  loadSharesFromSeats(seats, target) {
    const aggKey = this.aggregateConfig?.key;
    const isMember = this.aggregateConfig?.isMember;
    buildBaselineShares(seats, this.modelledPartyKeys, this.aggregateConfig).forEach((partyMap, regionKey) => {
      if (this.aggregateExpanded && regionKey === aggKey) return;
      if (!this.aggregateExpanded && isMember?.(regionKey)) return;
      target.set(regionKey, new Map(partyMap));
    });
  }

  /**
   * Walks an input map and emits override entries for any value that differs from the
   * matching baseline share. Used by subclass `serialize()` to build a compact URL payload.
   *
   * Baseline values come from the supplied `baselineMap` (rounded via `roundShare` so the
   * "differs from baseline" comparison matches the integer-share representation that
   * lives in the input map). Every party share is emitted independently — a region with
   * three changed parties produces three entries.
   *
   * Output shape:
   * - Without prefix (Westminster, single ballot): `[regionKey, partyKey, value]`.
   * - With prefix (Holyrood, two ballots): `[prefix, regionKey, partyKey, value]` where
   *   prefix is `'c'` (constituency) or `'l'` (list) so deserialize can route the entry
   *   back to the correct input map.
   *
   * @param {Map<string, Map<string, number>>} inputMap - The user-input map to walk.
   * @param {Map<string, Map<string, number>>} baselineMap - The matching baseline map.
   * @param {string|null} [prefix=null] - Ballot tag prefix, or null to omit it.
   * @returns {Array<[string, string, number] | [string, string, string, number]>}
   */
  collectOverrides(inputMap, baselineMap, prefix = null) {
    const overrides = [];
    inputMap.forEach((partyMap, regionKey) => {
      partyMap.forEach((value, partyKey) => {
        const baseline = roundShare(baselineMap.get(regionKey)?.get(partyKey) ?? 0);
        if (value === baseline) return;
        overrides.push(prefix !== null ? [prefix, regionKey, partyKey, value] : [regionKey, partyKey, value]);
      });
    });
    return overrides;
  }

  /**
   * Helper for `regions()`: assembles the aggregate row + (optionally) its sub-region
   * children into a single ordered list. Subclasses call this after gathering the
   * sub-rows that should sit under the aggregate; rows that don't feed the aggregate
   * (e.g. Westminster's Scotland / Wales / NI) are appended by the caller.
   *
   * Output ordering:
   * - Always starts with one row tagged `isAggregate: true`, carrying the configured
   *   `regionKey` / `regionLabel` / `section`.
   * - When `aggregateExpanded` is true, every input sub-row is appended (in the order
   *   provided) and tagged `isAggregateChild: true`. dom.js uses both flags to render
   *   the Show/Hide toggle button on the aggregate and the indented child styling.
   *
   * Returns `subRows` unchanged when `aggregateConfig` is null — a parliament without
   * an aggregate just renders its sub-rows directly.
   *
   * @param {Array<{regionKey: string, regionLabel: string}>} subRows - Sub-region rows
   *   that feed the aggregate (the caller has already filtered via `aggregateConfig.isMember`).
   * @param {string} sectionKey - Section tag applied to all rows so `gridSections` can
   *   split them into separate tables.
   * @returns {Array<object>} Ordered rows ready for dom.js to render.
   */
  buildRowsWithAggregate(subRows, sectionKey) {
    if (!this.aggregateConfig) return subRows;
    const rows = [{
      regionKey: this.aggregateConfig.key,
      regionLabel: this.aggregateConfig.label,
      isAggregate: true,
      section: sectionKey,
    }];
    if (this.aggregateExpanded) {
      subRows.forEach((r) => rows.push({ ...r, isAggregateChild: true, section: sectionKey }));
    }
    return rows;
  }
}

/**
 * Westminster predict: GB regions (England aggregate or sub-regions) + NI region. Inputs
 * are a nested Map<region, Map<party, share>>. Grid layout, columns, and the 'nat'
 * virtual column are all driven by the manifest's `gridSections` + `virtualColumns`.
 */
export class WestminsterPredict extends PredictModel {
  constructor(nextElectionYear, config) {
    super(nextElectionYear, config);
    this.baselineByRegion = this.baselineFor(this.baseSeats);
    this.inputByRegion = new Map();
  }

  currentInputMap() { return this.inputByRegion; }
  currentBaselineMap() { return this.baselineByRegion; }
  inputMaps() { return [this.inputByRegion]; }

  reset() {
    super.reset();
    this.inputByRegion = new Map();
  }

  project() {
    const swings = this.buildSwings(this.inputByRegion, this.baselineByRegion);
    if (swings.size === 0) return this.baseSeats.slice();
    return this.baseSeats.map((seat) => projectSeatUniformSwing(seat, swings, this.modelledPartyKeys, this.aggregateConfig));
  }

  /** Populates inputs from a simulation seat array (model-output forecast). */
  loadSimulationShares(simulationSeats) {
    this.inputByRegion = new Map();
    this.loadSharesFromSeats(simulationSeats, this.inputByRegion);
  }

  /** Encodes inputs as a base64url-encoded JSON object. Empty string when no overrides. */
  serialize() {
    return this.buildSerializedPayload(this.collectOverrides(this.inputByRegion, this.baselineByRegion));
  }

  deserialize(payload) {
    const decoded = decodePredictPayload(payload);
    if (!decoded) return;
    this.aggregateExpanded = decoded.e === 1;
    const validRegions = new Set(this.regions().map((r) => r.regionKey));
    (decoded.r || []).forEach(([regionKey, partyKey, value]) => {
      if (!validRegions.has(regionKey)) return;
      if (!this.parties(regionKey).includes(partyKey)) return;
      this.setShare(regionKey, partyKey, value);
    });
  }
}

/**
 * Holyrood predict: 8 regional rows with separate constituency and list ballot inputs.
 * Active tab determines which input map the grid reads/writes; project() runs both passes
 * regardless of the active tab.
 */
export class HolyroodPredict extends PredictModel {
  constructor(nextElectionYear, config) {
    super(nextElectionYear, config);
    this.tabs = this.config.tabs || [];
    this.activeTab = this.tabs[0]?.key || 'constituency';
    this.constInput = new Map();
    this.listInput = new Map();
    this.constBaselineByRegion = this.baselineFor(this.baseSeats.filter((s) => !Seat.isList(s)));
    // List baselines: each list seat in a region carries the full regional list total
    // duplicated, so dedupe by region before computing shares.
    this.listBaselineByRegion = this.baselineFor(HolyroodPredict.#dedupeListSeatsByRegion(this.baseSeats));
  }

  static #dedupeListSeatsByRegion(seats) {
    const seen = new Set();
    return seats.filter((s) => {
      if (!Seat.isList(s)) return false;
      if (seen.has(s.region)) return false;
      seen.add(s.region);
      return true;
    });
  }

  currentInputMap() { return this.activeTab === 'list' ? this.listInput : this.constInput; }
  currentBaselineMap() { return this.activeTab === 'list' ? this.listBaselineByRegion : this.constBaselineByRegion; }
  inputMaps() { return [this.constInput, this.listInput]; }

  /**
   * Switches the active ballot tab. Subsequent reads/writes via `currentInputMap` and
   * `currentBaselineMap` route to the matching ballot's pair (const vs list).
   *
   * Validates `tab` against the manifest's `predict.tabs` list and silently no-ops on
   * unknown keys — guards against a bad URL fragment or stray caller. `project()` runs
   * both ballot passes regardless of the active tab, so switching tabs is purely a UI
   * concern: it doesn't trigger a re-projection on its own.
   *
   * @param {string} tab - Ballot key from `this.tabs[].key` (e.g. `'constituency'`, `'list'`).
   * @returns {void}
   */
  setActiveTab(tab) {
    if (!this.tabs.some((t) => t.key === tab)) return;
    this.activeTab = tab;
  }

  reset() {
    super.reset();
    this.activeTab = this.tabs[0]?.key || 'constituency';
    this.constInput = new Map();
    this.listInput = new Map();
  }

  project() {
    const constSwings = this.buildSwings(this.constInput, this.constBaselineByRegion);
    const listSwings = this.buildSwings(this.listInput, this.listBaselineByRegion);
    // Zero-swing short-circuit: with no inputs the user expects the published baseline
    // result exactly. Re-running D'Hondt on the baseline data can produce a mathematically
    // valid but different allocation when the source data wasn't itself a clean D'Hondt
    // recomputation (e.g. the "2021 Election (2026 boundaries)" file preserves the historical
    // 2021 list winners rather than re-allocating under the new region structure).
    if (constSwings.size === 0 && listSwings.size === 0) return this.baseSeats.slice();
    const effectiveListSwings = listSwings.size > 0 ? listSwings : constSwings;

    const constBase = this.baseSeats.filter((s) => !Seat.isList(s));
    const listBase = this.baseSeats.filter((s) => Seat.isList(s));

    // Pass 1: project constituency seats with FPTP swing.
    const projectedConst = constBase.map((s) => projectSeatUniformSwing(s, constSwings, this.modelledPartyKeys, this.aggregateConfig));

    // Group constituency wins by region for D'Hondt divisor seeding.
    const constWinsByRegion = new Map();
    projectedConst.forEach((seat) => {
      const rk = normalizeRegionKey(seat.region);
      if (!constWinsByRegion.has(rk)) constWinsByRegion.set(rk, new Map());
      const wins = constWinsByRegion.get(rk);
      if (seat.winner) wins.set(seat.winner, (wins.get(seat.winner) || 0) + 1);
    });

    // Group list seats by region.
    const listByRegion = new Map();
    listBase.forEach((s) => {
      const rk = normalizeRegionKey(s.region);
      if (!listByRegion.has(rk)) listByRegion.set(rk, []);
      listByRegion.get(rk).push(s);
    });

    // Pass 2: D'Hondt allocation per region using list swings.
    const projectedList = [];
    listByRegion.forEach((regionListSeats, rk) => {
      const projectedRegionList = regionListSeats.map((s) => projectSeatUniformSwing(s, effectiveListSwings, this.modelledPartyKeys, this.aggregateConfig));
      // List seats in a region share identical totals (each seat duplicates the regional vote);
      // accumulate from the first seat only.
      const voteSumByParty = new Map();
      const firstProj = projectedRegionList[0];
      Object.entries(firstProj?.votes || {}).forEach(([partyKey, voteCount]) => {
        voteSumByParty.set(partyKey, Number(voteCount || 0));
      });
      const winners = dhondtAllocate(voteSumByParty, regionListSeats.length, constWinsByRegion.get(rk) || new Map());
      projectedRegionList.forEach((proj, idx) => {
        projectedList.push(new Seat({ ...proj, winner: winners[idx] || null }));
      });
    });

    return [...projectedConst, ...projectedList];
  }

  loadSimulationShares(simulationSeats) {
    const constSeats = simulationSeats.filter((s) => !Seat.isList(s));
    const dedupedListSeats = HolyroodPredict.#dedupeListSeatsByRegion(simulationSeats);
    this.constInput = new Map();
    this.listInput = new Map();
    this.loadSharesFromSeats(constSeats, this.constInput);
    this.loadSharesFromSeats(dedupedListSeats, this.listInput);
  }

  serialize() {
    const overrides = [
      ...this.collectOverrides(this.constInput, this.constBaselineByRegion, 'c'),
      ...this.collectOverrides(this.listInput, this.listBaselineByRegion, 'l'),
    ];
    return this.buildSerializedPayload(overrides, { h: 1 });
  }

  deserialize(payload) {
    const decoded = decodePredictPayload(payload);
    if (!decoded || decoded.h !== 1) return;
    this.aggregateExpanded = decoded.e === 1;
    const validRegions = new Set(this.regions().map((r) => r.regionKey));
    (decoded.r || []).forEach(([prefix, regionKey, partyKey, value]) => {
      if (!validRegions.has(regionKey)) return;
      if (!this.parties(regionKey).includes(partyKey)) return;
      const target = prefix === 'l' ? this.listInput : this.constInput;
      if (!target.has(regionKey)) target.set(regionKey, new Map());
      target.get(regionKey).set(partyKey, roundShare(value));
    });
  }
}

function decodePredictPayload(payload) {
  const raw = String(payload || '').trim();
  if (!raw) return null;
  const json = base64urlDecode(raw);
  if (!json) return null;
  try { return JSON.parse(json); } catch { return null; }
}

/**
 * Resolves the predict model class for a parliament from its `predict.model` config field.
 * Returns null when the parliament has no predict block (predict feature disabled).
 * @param {string} parliament - Parliament key.
 * @returns {typeof PredictModel | null}
 */
export function predictModelClassFor(parliament) {
  const config = manifest.parliamentConfig(parliament).predict;
  if (!config?.model) return null;
  if (config.model === 'westminster') return WestminsterPredict;
  if (config.model === 'holyrood') return HolyroodPredict;
  return null;
}

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


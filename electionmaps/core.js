/**
 * Pure utility functions extracted from electionmaps.js for testability.
 * No DOM dependencies, no module-level state.
 */

// ── Party constants ──────────────────────────────────────────────────────────

export const PARTY_KEY_ALIASES = {
  ukindependenceparty: 'ukip',
  reformuk: 'reform',
  liberaldemocrats: 'libdems',
  democraticunionistparty: 'dup',
  ulsterunionistparty: 'uu',
  uup: 'uu',
  scottishnationalparty: 'snp',
};

// ── Party display helpers ─────────────────────────────────────────────────────

/**
 * Returns the display label for a party from the manifest, or the raw key if not found.
 * @param {object} partiesByKey - Manifest parties object keyed by party key.
 * @param {string} partyKey - Canonical party key to look up.
 * @returns {string} Human-readable party name, or the raw key as fallback.
 */
export function labelParty(partiesByKey, partyKey) {
  const meta = partiesByKey[partyKey];
  if (meta?.name) return meta.name;
  return partyKey;
}

/**
 * Returns the hex colour for a party from the manifest, or a grey fallback if not found.
 * @param {object} partiesByKey - Manifest parties object keyed by party key.
 * @param {string} partyKey - Canonical party key to look up.
 * @returns {string} Hex colour string (e.g. '#d50000'), or '#9CA3AF' if not found.
 */
export function colourParty(partiesByKey, partyKey) {
  const meta = partiesByKey[partyKey];
  if (meta?.colour) return meta.colour;
  return '#9CA3AF';
}

// ── Predict constants ────────────────────────────────────────────────────────

export const PREDICT_BASE_PARTY_KEYS = ['labour', 'conservative', 'libdems', 'green', 'reform'];
export const PREDICT_NI_PARTY_KEYS = ['sinnfein', 'dup', 'alliance', 'uu', 'sdlp'];
export const PREDICT_HOLYROOD_PARTY_KEYS = [
  'snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform',
];
export const PREDICT_MODELLED_PARTY_KEYS = [
  ...PREDICT_BASE_PARTY_KEYS,
  'snp',
  'plaidcymru',
  'scottishgreens',
  'alba',
  ...PREDICT_NI_PARTY_KEYS,
];
export const PREDICT_ENGLAND_KEY = 'england';
export const PREDICT_SCOTLAND_KEY = 'scotland';
export const PREDICT_WALES_KEY = 'wales';
export const PREDICT_NI_KEY = 'northernireland';

// ── List seat utilities ──────────────────────────────────────────────────────

/**
 * Returns true if the seat name is a regional list seat (e.g. "Glasgow List 1").
 * List seats have no map geometry and appear only in the seat list panel.
 * @param {string} seatName - Seat name to test.
 * @returns {boolean}
 */
export function isListSeat(seatName) {
  return /\bList\s+\d+$/i.test(seatName);
}

// ── Party normalization ──────────────────────────────────────────────────────

/**
 * Normalizes a raw party key string to a canonical lowercase key, applying aliases where needed. Returns 'others' for empty input.
 * @param {string} partyKey - Raw party key string, potentially mixed-case or containing special characters.
 * @returns {string} Canonical lowercase party key with aliases applied, or 'others' for empty input.
 */
export function normalizePartyKey(partyKey) {
  const raw = String(partyKey || '').trim();
  if (!raw) return 'others';

  const lower = raw.toLowerCase();
  const alnum = lower.replace(/[^a-z0-9]/g, '');
  if (PARTY_KEY_ALIASES[alnum]) return PARTY_KEY_ALIASES[alnum];

  return lower;
}

// ── Formatting ───────────────────────────────────────────────────────────────

/**
 * Escapes HTML special characters in a string for safe insertion into innerHTML.
 * @param {string} str - String to escape.
 * @returns {string} HTML-escaped string.
 */
export function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Rounds value to the nearest integer and formats it with GB locale thousands separators.
 * @param {number} value - Numeric value to format.
 * @returns {string} Rounded integer as a locale-formatted string (e.g. '1,234').
 */
export function formatInt(value) {
  return Math.round(value).toLocaleString('en-GB');
}

/**
 * Formats value as a percentage string to two decimal places.
 * @param {number} value - Numeric percentage value.
 * @returns {string} Value formatted to two decimal places (e.g. '42.35').
 */
export function formatPct(value) {
  return Number(value).toFixed(2);
}

/**
 * Formats value with an explicit '+' prefix for positive numbers and the specified decimal digits. Returns '0' for values within floating-point epsilon of zero.
 * @param {number} value - Numeric value to format with sign.
 * @param {number} [digits=0] - Number of decimal places to include.
 * @returns {string} Sign-prefixed string (e.g. '+3', '-1.50'), or '0' for near-zero values.
 */
export function formatSigned(value, digits = 0) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return '0';
  const sign = num > 0 ? '+' : '';
  return `${sign}${num.toFixed(digits)}`;
}

/**
 * Returns a CSS class name reflecting whether value is positive, negative, or neutral.
 * @param {number} value - Numeric delta value.
 * @returns {string} One of 'maps-delta-positive', 'maps-delta-negative', or 'maps-delta-neutral'.
 */
export function deltaClass(value) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return 'maps-delta-neutral';
  return num > 0 ? 'maps-delta-positive' : 'maps-delta-negative';
}

// ── Region normalization ─────────────────────────────────────────────────────

/**
 * Converts a region name to a lowercase alphanumeric key with all non-alphanumeric characters removed.
 * @param {string} value - Raw region name or key string.
 * @returns {string} Lowercase alphanumeric string suitable for use as a lookup key.
 */
export function normalizeRegionKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Converts a region key or name to title case, splitting on camelCase boundaries, hyphens, and underscores. Returns 'Unknown' for empty input.
 * @param {string} regionKey - Region key or name to convert.
 * @returns {string} Title-cased display label (e.g. 'North West England'), or 'Unknown' for empty input.
 */
export function titleCaseFromRegionKey(regionKey) {
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

// ── Seat utilities ───────────────────────────────────────────────────────────

/**
 * Returns a trimmed, lowercase string suitable for use as a seat lookup key.
 * @param {string} seatName - Raw seat name.
 * @returns {string} Trimmed lowercase seat name for use in Map lookups.
 */
export function seatLookupKey(seatName) {
  return String(seatName || '').trim().toLowerCase();
}

/**
 * Returns the total votes cast in a seat, using the explicit turnout field if available, otherwise summing all party vote totals.
 * @param {object} seat - Seat object with optional `turnout` number and `votes` object.
 * @returns {number} Total votes cast in the seat.
 */
export function totalVotesForSeat(seat) {
  const turnout = Number(seat?.turnout || 0);
  if (turnout > 0) return turnout;
  return Object.values(seat?.votes || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

/**
 * Returns an array of { party, votes } objects for a seat, sorted descending by vote count, excluding parties with zero votes.
 * @param {object} seat - Seat object with a `votes` map of party key to vote count.
 * @returns {Array<{party: string, votes: number}>} Sorted array of party vote entries, highest first.
 */
export function sortedSeatVoteRows(seat) {
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
export function seatMajorityStats(seat) {
  const voteRows = sortedSeatVoteRows(seat);
  if (voteRows.length < 2) return { pct: 0, raw: 0 };
  const marginVotes = voteRows[0].votes - voteRows[1].votes;
  const totalVotes = totalVotesForSeat(seat);
  if (totalVotes <= 0) return { pct: 0, raw: marginVotes };
  return { pct: (marginVotes / totalVotes) * 100, raw: marginVotes };
}

/**
 * Returns the previous winner's party key if the seat changed hands between comparisonSeat and currentSeat, or null if there was no change or no comparison available.
 * @param {object} currentSeat - The seat in its current state, with a `winner` property.
 * @param {object|null} comparisonSeat - The seat in its comparison state, or null if no comparison is available.
 * @returns {string|null} The previous winner's party key if a gain occurred, otherwise null.
 */
export function seatGainFromPartyKey(currentSeat, comparisonSeat) {
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

/**
 * Returns the party key of the second-place finisher in a seat, or null if fewer than two parties have votes.
 * @param {object} seat - Seat object with a `votes` map.
 * @returns {string|null} Party key of the second-place finisher, or null if unavailable.
 */
export function secondPlacePartyKey(seat) {
  const voteRows = sortedSeatVoteRows(seat);
  if (voteRows.length < 2) return null;
  return voteRows[1].party;
}

/**
 * Returns the vote share percentage (0–100) for partyKey in the given seat. Returns 0 if total votes are zero.
 * @param {object} seat - Seat object with a `votes` map and optional `turnout`.
 * @param {string} partyKey - The party whose vote share to calculate.
 * @returns {number} Vote share as a percentage in the range [0, 100].
 */
export function voteSharePct(seat, partyKey) {
  const totalVotes = totalVotesForSeat(seat);
  if (totalVotes <= 0) return 0;
  const partyVotes = Number(seat?.votes?.[partyKey] || 0);
  return (partyVotes / totalVotes) * 100;
}

// ── Election summary ─────────────────────────────────────────────────────────

/**
 * Aggregates seats and votes across all constituencies, returning { parties, totalVotes, turnout, totalSeats }. Parties are sorted by seats descending then votes descending. Turnout is electorate-weighted.
 * @param {Array<object>} seats - Array of seat objects with `winner`, `votes`, `electorate`, and `turnout` properties.
 * @returns {{parties: Array<{party: string, seats: number, votes: number}>, totalVotes: number, turnout: number, totalSeats: number}} Aggregated election summary.
 */
export function summarizeElection(seats, { mode = 'all' } = {}) {
  const partyStats = new Map();
  const listRegionPartyCountSeen = new Set();
  let electorateSum = 0;
  let turnoutWeighted = 0;

  seats.forEach((seat) => {
    const isList = isListSeat(seat.seat);

    // Mode filtering: skip seats that don't belong to the requested view.
    if (mode === 'constituency' && isList) return;
    if (mode === 'list' && !isList) return;

    const winner = seat.winner === 'other' ? 'others' : (seat.winner || 'others');
    if (!partyStats.has(winner)) partyStats.set(winner, { seats: 0, votes: 0 });
    partyStats.get(winner).seats += 1;

    // In 'all' mode, only accumulate votes from constituency seats. List seats
    // use a separate ballot paper, so combining both would double-count the
    // electorate and produce meaningless vote-share percentages.
    //
    // In 'list' mode, all seats within the same region share identical vote
    // totals (each region's list votes are stored on every seat in that region).
    // Only count votes for the first list seat encountered per region to avoid
    // multiplying regional totals by the number of seats.
    const includeVotes = mode !== 'all' || !isList;

    if (includeVotes) {
      Object.entries(seat.votes || {}).forEach(([party, votes]) => {
        const key = party === 'other' ? 'others' : party;
        if (!partyStats.has(key)) partyStats.set(key, { seats: 0, votes: 0 });
        // In list mode, each party's regional vote total is duplicated across all
        // seats they won in the region. Only count it once per (region, party) pair.
        if (mode === 'list') {
          const seenKey = `${normalizeRegionKey(seat.region)}\x00${key}`;
          if (listRegionPartyCountSeen.has(seenKey)) return;
          listRegionPartyCountSeen.add(seenKey);
        }
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

  return { parties, totalVotes, turnout, totalSeats: seats.length };
}

// ── Seat normalization ───────────────────────────────────────────────────────

/**
 * Resolves a raw party reference (integer party_id or string key) to a canonical string party key.
 * When ref is a number or numeric string, looks up in partiesById (Map<number, {key: string}>).
 * Falls back to normalizePartyKey on string refs or when the id is not found in the map.
 * @param {number|string} ref - Raw party reference from results data.
 * @param {Map<number, {key: string}>} [partiesById] - Optional manifest party lookup map.
 * @returns {string} Canonical party key.
 */
export function resolvePartyRef(ref, partiesById) {
  const num = Number(ref);
  if (Number.isFinite(num) && num > 0) {
    const party = partiesById?.get(num);
    if (party?.key) return party.key;
    return normalizePartyKey(String(num));
  }
  return normalizePartyKey(ref);
}

/**
 * Normalizes results data (pf-results-v4 format) into a canonical array of seat objects.
 * Supports the compact array format ({ seats: [...] }) and the compact per-seat p[] format.
 * When partiesById is provided, integer party references are resolved via the map.
 * When regionsById is provided, integer region IDs are resolved to region keys via the map.
 * @param {object} resultsData - Raw results payload with a `seats` array.
 * @param {Map<number, {key: string}>} [partiesById] - Optional manifest party lookup for integer party_id refs.
 * @param {Map<number, string>} [regionsById] - Optional manifest region lookup for integer region_id refs.
 * @returns {Array<{seat: string, region: string, winner: string, electorate: number, turnout: number, votes: object}>} Normalized seat objects.
 */
export function normalizeSeats(resultsData, partiesById, regionsById) {
  if (!Array.isArray(resultsData?.seats)) return [];

  return resultsData.seats.map((seat) => ({
    seat: seat.seat || seat.n || 'Unknown seat',
    region: (() => {
      const raw = seat.region ?? seat.r;
      if (typeof raw === 'number' && regionsById?.size) return regionsById.get(raw) || 'unknown';
      return String(raw || 'unknown');
    })(),
    winner: resolvePartyRef(seat.winner ?? seat.w ?? 'others', partiesById),
    electorate: Number(seat.electorate ?? seat.e ?? 0),
    turnout: Number(seat.turnout ?? seat.t ?? 0),
    votes: (() => {
      if (seat.votes && typeof seat.votes === 'object' && !Array.isArray(seat.votes)) {
        const normalizedVotes = {};
        Object.entries(seat.votes).forEach(([partyKey, voteValue]) => {
          const normalizedPartyKey = resolvePartyRef(partyKey, partiesById);
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
          const partyKey = resolvePartyRef(entry[0], partiesById);
          const voteTotal = Number(entry[1] || 0);
          if (!partyKey || voteTotal <= 0) return;
          compactVotes[partyKey] = (compactVotes[partyKey] || 0) + voteTotal;
        });
        return compactVotes;
      }
      return {};
    })(),
  }));
}

// ── Predict region predicates ────────────────────────────────────────────────

/**
 * Returns true if regionKey normalizes to the Northern Ireland predict region.
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the normalized key matches the Northern Ireland predict region.
 */
export function isPredictNorthernIrelandRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_NI_KEY;
}

/**
 * Returns true if regionKey normalizes to the Scotland predict region.
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the normalized key matches the Scotland predict region.
 */
export function isPredictScotlandRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_SCOTLAND_KEY;
}

/**
 * Returns true if regionKey normalizes to the Wales predict region.
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the normalized key matches the Wales predict region.
 */
export function isPredictWalesRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_WALES_KEY;
}

/**
 * Returns true if regionKey is a non-empty, non-NI, non-Scotland, non-Wales region (i.e. an English region).
 * @param {string} regionKey - Region key or name to test.
 * @returns {boolean} True when the region is non-empty and does not match any of the named devolved/NI regions.
 */
export function isPredictEnglishRegion(regionKey) {
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
export function roundPredictShareValue(value) {
  return Math.round(Number(value || 0));
}

/**
 * Clamps value to [minimum, maximum]. Returns minimum if value is not finite.
 * @param {number} value - Value to clamp.
 * @param {number} minimum - Lower bound (inclusive).
 * @param {number} maximum - Upper bound (inclusive).
 * @returns {number} Clamped numeric value within [minimum, maximum].
 */
export function clampNumber(value, minimum, maximum) {
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
export function predictInputKey(regionKey, partyKey) {
  return `${regionKey}::${partyKey}`;
}

/**
 * Formats a predict share value as an integer string.
 * @param {number} value - Vote share value to format.
 * @returns {string} Rounded integer share as a string (e.g. '42').
 */
export function formatPredictShare(value) {
  return String(roundPredictShareValue(value));
}

/**
 * Returns a new Map with all values rounded and clamped to [0, 100].
 * @param {Map<string, number>} sourceMap - Source Map of predict share values to normalize.
 * @returns {Map<string, number>} New Map with the same keys and values rounded and clamped to [0, 100].
 */
export function normalizePredictShareMap(sourceMap) {
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
export function predictNatPartyKeyForRegion(regionKey) {
  if (isPredictScotlandRegion(regionKey)) return 'snp';
  if (isPredictWalesRegion(regionKey)) return 'plaidcymru';
  return null;
}

export const PREDICT_NAT_COLUMN_KEY = 'nat';

/**
 * Resolves a grid column party key to the actual party key for a given region. The 'nat' column maps to SNP or Plaid Cymru depending on region, and null if not applicable.
 * @param {string} regionKey - Normalized region key used to resolve the nationalist party.
 * @param {string} columnPartyKey - Column party key, which may be the special 'nat' sentinel or a direct party key.
 * @returns {string|null} Resolved party key, or null when the 'nat' column has no applicable party for the region.
 */
export function resolvePredictInputPartyKey(regionKey, columnPartyKey) {
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
export function collectPredictInputPartyKeysForRegion(regionKey) {
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
export function formatPredictRegionLabel(regionLabel) {
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
export function buildPredictBaselineShares(seats) {
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

    const turnout = totalVotesForSeat(seat);
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
export function resolvedSwingValue(normalizedSeatRegion, partyKey, swingsByParty) {
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
 * Returns a new seat object with updated votes, turnout, and winner.
 * @param {object} baseSeat - Baseline seat object with `region`, `votes`, and `turnout`.
 * @param {Map<string, Map<string, number>>} swingsByParty - Map from party key to regional swing values.
 * @returns {object} New seat object with projected `votes`, `turnout`, and `winner`.
 */
export function projectedSeatForPredictMode(baseSeat, swingsByParty) {
  const totalVotes = totalVotesForSeat(baseSeat);
  if (totalVotes <= 0) return { ...baseSeat };

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

  return { ...baseSeat, votes: projectedVotes, turnout: totalVotes, winner };
}

/**
 * Runs D'Hondt seat allocation for one Holyrood region.
 * @param {Map<string, number>} votesByPartyKey - List vote totals per party key.
 * @param {number} nSeats - Number of list seats to allocate.
 * @param {Map<string, number>} constWinsByPartyKey - Constituency wins per party (deducted during allocation).
 * @returns {string[]} Ordered array of winning party keys, one entry per seat allocated.
 */
export function dhondt(votesByPartyKey, nSeats, constWinsByPartyKey = new Map()) {
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
export function buildHolyroodNationalBaselines(baselineShareByRegionParty, partyKeys, regionKeys) {
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
export function projectHolyroodSeats(baseSeats, constSwingsByParty, listSwingsByParty = null) {
  const effectiveListSwings = (listSwingsByParty && listSwingsByParty.size > 0) ? listSwingsByParty : constSwingsByParty;

  const constBaseSeats = baseSeats.filter((s) => !isListSeat(s.seat));
  const listBaseSeats = baseSeats.filter((s) => isListSeat(s.seat));

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
      projectedList.push({ ...proj, winner: listWinners[idx] || null });
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
export function collectHolyroodPredictInputRows(baseRegionLabelsByKey) {
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
export function seatNameFromFeature(featureDatum) {
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
export function buildWinnerBySeat(seats) {
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
export function buildRegionSummary(seats) {
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
    if (isListSeat(seat.seat)) r.listSeats.push(seat);
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

/**
 * Returns a deep-ish copy of a seat record with normalised party keys.
 * Zero-vote and negative-vote entries are filtered out.
 * Duplicate keys that collapse after normalisation are summed.
 * Numeric fields (`electorate`, `turnout`) are coerced to numbers.
 * @param {object} seat - Raw seat object with `seat`, `region`, `winner`, `electorate`, `turnout`, and `votes` properties.
 * @returns {{seat: string, region: string, winner: string, electorate: number, turnout: number, votes: object}} Normalised copy of the seat record.
 */
export function cloneSeatRecord(seat) {
  const votes = {};
  Object.entries(seat?.votes || {}).forEach(([partyKey, value]) => {
    const voteTotal = Number(value || 0);
    if (voteTotal <= 0) return;
    votes[normalizePartyKey(partyKey)] = (votes[normalizePartyKey(partyKey)] || 0) + voteTotal;
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

/**
 * Extracts a YYYY-MM-DD date string from an election name when present.
 * Returns the trimmed name when no date is found, or the stringified fallbackId when the name is empty.
 * @param {string} electionName - Election name string, which may contain an ISO date.
 * @param {number|string} fallbackId - Fallback identifier used when the name is empty.
 * @returns {string} ISO date string if found in the name, the trimmed name otherwise, or stringified fallbackId for empty names.
 */
export function pollTrackerDateLabel(electionName, fallbackId) {
  const text = String(electionName || '').trim();
  const match = text.match(/(\d{4}-\d{2}-\d{2})/);
  if (match?.[1]) return match[1];
  return text || String(fallbackId);
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
export function encodePredictPayload(serializedRows, englandExpanded, slots) {
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
 * @returns {{englandExpanded: boolean, rows: Array<[string, string, number]>}|null} Decoded state object, or null on parse failure.
 */
export function decodePredictPayload(encoded, slots) {
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

/**
 * Returns a Map from normalised region key to display label for the given mapId.
 * Built from the manifest's region metadata object keyed by map ID.
 * Regions whose names normalise to an empty string are skipped.
 * @param {string|number} mapId - Map identifier used to look up region metadata in regionsByMapId.
 * @param {object} regionsByMapId - Manifest settings object mapping map ID strings to arrays of region metadata objects.
 * @returns {Map<string, string>} Map from normalized region key to display label.
 */
export function buildRegionLabelLookup(mapId, regionsByMapId) {
  const lookup = new Map();
  const regionRows = regionsByMapId?.[String(mapId)] || [];
  regionRows.forEach((region) => {
    const key = normalizeRegionKey(region?.name || '');
    if (!key) return;
    lookup.set(key, region.name);
  });
  return lookup;
}

// ── Seat filter utilities ─────────────────────────────────────────────────────

/**
 * Returns true when a seat passes all active primary filters.
 * `filterState` mirrors the mapViewState shape: `{ filterParty, filterRegion, majorityMin, majorityMax, filterSecondParty, gainsOnly }`.
 * `byElectionSeats` is a Set of seat names for by-election gain filtering, or null to use the comparison seat method.
 * @param {object} seat - Current seat object to test against the filters.
 * @param {object|null} comparisonSeat - Comparison seat for gains filtering; may be null if no comparison is available.
 * @param {{filterParty: string, filterRegion: string, majorityMin: number, majorityMax: number, filterSecondParty: string, gainsOnly: boolean}} filterState - Active filter configuration.
 * @param {Set<string>|null} byElectionSeats - Set of seat names that are by-election gains, or null to use comparison-seat gain detection.
 * @returns {boolean} True if the seat passes all currently active filters.
 */
export function seatMatchesPrimaryFilters(seat, comparisonSeat, filterState, byElectionSeats) {
  if (filterState.filterParty !== 'all') {
    const winner = seat.winner === 'other' ? 'others' : seat.winner;
    if (winner !== filterState.filterParty) return false;
  }

  if (filterState.filterRegion !== 'all') {
    const seatRegion = normalizeRegionKey(seat.region);
    if (seatRegion !== filterState.filterRegion) return false;
  }

  const majority = seatMajorityStats(seat).pct;
  if (majority < filterState.majorityMin || majority > filterState.majorityMax) return false;

  if (filterState.filterSecondParty !== 'all') {
    const secondParty = secondPlacePartyKey(seat);
    if (secondParty !== filterState.filterSecondParty) return false;
  }

  if (filterState.gainsOnly) {
    if (byElectionSeats) {
      if (!byElectionSeats.has(seat.seat)) return false;
    } else {
      const gainFrom = seatGainFromPartyKey(seat, comparisonSeat);
      if (!gainFrom) return false;
    }
  }

  return true;
}

/**
 * Returns a Set of seat lookup keys for all seats that pass the current primary filters.
 * `comparisonSeatsByKey` is a Map from seatLookupKey to comparison seat object.
 * `filterState` and `byElectionSeats` are forwarded to `seatMatchesPrimaryFilters`.
 * @param {Array<object>} seats - Array of seat objects to filter.
 * @param {Map<string, object>} comparisonSeatsByKey - Map from seat lookup key to comparison seat, used for gains filtering.
 * @param {{filterParty: string, filterRegion: string, majorityMin: number, majorityMax: number, filterSecondParty: string, gainsOnly: boolean}} filterState - Active filter configuration.
 * @param {Set<string>|null} byElectionSeats - Set of by-election seat names, or null to use comparison-based gain detection.
 * @returns {Set<string>} Set of seat lookup keys for all seats that pass the active filters.
 */
export function buildVisibleSeatKeySet(seats, comparisonSeatsByKey, filterState, byElectionSeats) {
  const keySet = new Set();
  seats.forEach((seat) => {
    const seatKey = seatLookupKey(seat.seat);
    const comparisonSeat = comparisonSeatsByKey.get(seatKey) || null;
    if (seatMatchesPrimaryFilters(seat, comparisonSeat, filterState, byElectionSeats)) {
      keySet.add(seatKey);
    }
  });
  return keySet;
}

/**
 * Returns the choropleth metric value for a seat, or null when choropleth is disabled.
 * `choroplethType` is `'voteShare'`, `'voteShareChange'`, or `'none'`.
 * `choroplethParty` is a party key or `'all'`.
 * Returns null when the type is `'none'`, the party is unset/`'all'`, or no comparison seat is available for a change metric.
 * @param {object} seat - Current seat object.
 * @param {object|null} comparisonSeat - Comparison seat for delta calculations; may be null.
 * @param {string} choroplethType - Choropleth type: 'voteShare', 'voteShareChange', or 'none'.
 * @param {string} choroplethParty - Party key to compute the metric for, or 'all' to disable.
 * @returns {number|null} Choropleth metric value, or null when the choropleth is inactive or data is unavailable.
 */
export function getChoroplethValue(seat, comparisonSeat, choroplethType, choroplethParty) {
  if (choroplethType === 'none') return null;
  if (!choroplethParty || choroplethParty === 'all') return null;

  if (choroplethType === 'voteShareChange') {
    if (!comparisonSeat) return null;
    return voteSharePct(seat, choroplethParty) - voteSharePct(comparisonSeat, choroplethParty);
  }

  if (choroplethType === 'voteShare') {
    return voteSharePct(seat, choroplethParty);
  }

  return null;
}

// ── Election file resolution ──────────────────────────────────────────────────

/**
 * Resolves the mapFile and dataFile paths for an election.
 * Checks manifest settings overrides (by mapId / electionId) first, then falls back to
 * election-level properties. Throws if either file path cannot be determined.
 * @param {object} manifest - Full elections manifest object with a `settings` property.
 * @param {object} election - Election entry object with `id`, `mapId`, `mapFile`, and `dataFile` properties.
 * @returns {{mapFile: string, dataFile: string}} Resolved file paths for the map and results data.
 * @throws {Error} When either the mapFile or dataFile path cannot be determined for the election.
 */
export function resolveElectionFiles(manifest, election) {
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

// ── Predict share lookups ─────────────────────────────────────────────────────

/**
 * Returns the rounded baseline vote share for a region/party from the historical election data Map.
 * `baselineMap` is keyed by `predictInputKey(regionKey, partyKey)`. Returns 0 when not found.
 * @param {string} regionKey - Normalized region key.
 * @param {string} partyKey - Party key.
 * @param {Map<string, number>} baselineMap - Map of `regionKey::partyKey` to baseline share values.
 * @returns {number} Rounded integer baseline share for the region/party, or 0 if not found.
 */
export function getPredictBaselineShare(regionKey, partyKey, baselineMap) {
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
export function getPredictInputShareValue(regionKey, partyKey, inputMap, baselineMap) {
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
export function calculatePredictEnteredShareTotal(regionKey, inputMap, baselineMap) {
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
export function calculatePredictOtherShare(regionKey, inputMap, baselineMap) {
  return roundPredictShareValue(100 - calculatePredictEnteredShareTotal(regionKey, inputMap, baselineMap));
}

// ── Predict region collection ─────────────────────────────────────────────────

/**
 * Returns all predict regions as `{ regionKey, regionLabel }` sorted alphabetically by label.
 * `baseRegionLabelsByKey` is a Map from normalised region key to display label.
 * @param {Map<string, string>} baseRegionLabelsByKey - Map from normalized region key to display label.
 * @returns {Array<{regionKey: string, regionLabel: string}>} All regions sorted alphabetically by display label.
 */
export function collectPredictAllRegions(baseRegionLabelsByKey) {
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
export function collectPredictValidationRows(baseRegionLabelsByKey) {
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
export function collectPredictShareStateRows(baseRegionLabelsByKey) {
  return collectPredictValidationRows(baseRegionLabelsByKey);
}

/**
 * Returns the ordered list of row descriptors for the predict grid.
 * England aggregate is always first. English sub-regions follow when `englandExpanded` is true.
 * Scotland, Wales, and Northern Ireland are appended when present in `baseRegionLabelsByKey`.
 * Each row carries `{ regionKey, regionLabel, isEnglandAggregate, isEnglandRegion }`.
 * @param {Map<string, string>} baseRegionLabelsByKey - Map from normalized region key to display label.
 * @param {boolean} englandExpanded - Whether English sub-regions should be included after the England aggregate row.
 * @returns {Array<{regionKey: string, regionLabel: string, isEnglandAggregate: boolean, isEnglandRegion: boolean}>} Ordered row descriptors for the predict input grid.
 */
export function collectPredictInputRows(baseRegionLabelsByKey, englandExpanded) {
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

// ── Poll tracker parsing ──────────────────────────────────────────────────────

/**
 * Parses poll tracker JSON into { timeline, seriesByParty, partyMeta }.
 * Deduplicates rows by date, preferring the highest electionId.
 * Expands sparse date entries into a dense daily timeline when all entries are ISO dates.
 * Series values carry forward the last known value for dates with no data.
 * @param {Array} data - Parsed JSON array from the poll tracker data file.
 * @param {Map<number, {key?: string, name?: string, colour?: string}>} partiesById - Manifest party lookup keyed by integer party ID.
 * @returns {{timeline: Array<{dateKey: string, electionId: number, sortValue: string, label: string, dateValue: Date|null}>, seriesByParty: Map<string, {partyKey: string, partyName: string, colour: string, seats: Array<number|null>, votePct: Array<number|null>, latestSeats: number}>, partyMeta: Map<string, {name: string, colour: string}>}} Parsed poll tracker data.
 */
export function parsePollTrackerData(data, partiesById) {
  const rows = [];
  for (const entry of data) {
    const electionId = Number(entry.election_id);
    if (!Number.isFinite(electionId)) continue;
    const electionName = String(entry.election_name || '');
    const asOfDateRaw = String(entry.as_of_date || '').trim();
    const unsDateMatch = electionName.match(/UNS\s+(\d{4}-\d{2}-\d{2})/);
    const asOfDate = unsDateMatch?.[1] || asOfDateRaw;
    for (const [partyIdStr, pdata] of Object.entries(entry.parties || {})) {
      const partyId = Number(partyIdStr);
      const seats = Number(pdata.s);
      const votePct = Number(pdata.v);
      if (!Number.isFinite(partyId) || !Number.isFinite(seats) || !Number.isFinite(votePct)) continue;
      const manifestParty = partiesById?.get(partyId);
      const normalizedPartyKey = normalizePartyKey(manifestParty?.key || manifestParty?.name || String(partyId));
      const partyName = manifestParty?.name || normalizedPartyKey || `Party ${partyId}`;
      rows.push({
        electionId,
        partyId,
        partyKey: String(partyId),
        asOfDate,
        electionName,
        partyName,
        seats,
        votePct,
      });
    }
  }

  const timelineByDateKey = new Map();
  const byParty = new Map();
  const partyMeta = new Map();

  /**
   * Returns value unchanged if it is an ISO date string, otherwise returns fallback as a string.
   * @param {string} value - Candidate sort value.
   * @param {string|number} fallback - Fallback value used when value is not an ISO date.
   * @returns {string} ISO date string or stringified fallback.
   */
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
      const manifestParty = Number.isFinite(row.partyId) ? partiesById?.get(row.partyId) : null;
      partyMeta.set(row.partyKey, {
        name: row.partyName,
        colour: manifestParty?.colour || '#9CA3AF',
      });
    }
  });

  const timeline = Array.from(timelineByDateKey.values())
    .sort((a, b) => {
      if (a.sortValue === b.sortValue) return a.electionId - b.electionId;
      return a.sortValue.localeCompare(b.sortValue);
    });

  const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  /**
   * Parses an ISO date string into a UTC Date, or returns null if the input does not match ISO_DATE_RE.
   * @param {string} value - String to parse.
   * @returns {Date|null} UTC Date object, or null on invalid/non-ISO input.
   */
  const parseIsoDate = (value) => {
    const text = String(value || '').trim();
    if (!ISO_DATE_RE.test(text)) return null;
    const parsed = new Date(`${text}T00:00:00Z`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  };

  /**
   * Formats a UTC Date as a zero-padded ISO date string (YYYY-MM-DD).
   * @param {Date} value - UTC Date to format.
   * @returns {string} ISO date string derived from UTC year/month/day components.
   */
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

// ── Holyrood predict share resolution ────────────────────────────────────────

export const HOLYROOD_NATIONAL_KEY = 'national';

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
 * Resolves the share for a region/party from a single tab input map, applying national UNS if
 * no region-level override exists.
 * @param {string} regionKey
 * @param {string} partyKey
 * @param {Map<string,number>} tabMap - Input map for the tab being resolved.
 * @param {'overall'|'constituency'|'list'} pass
 * @param {object} state - { constBaseline, listBaseline, nationalBaseline, nationalListBaseline }
 * @returns {number|null} Resolved share, or null if the tab map has no entry for this party.
 */
export function resolvedTabShare(regionKey, partyKey, tabMap, pass, state) {
  const regionKey_ = predictInputKey(regionKey, partyKey);
  if (tabMap.has(regionKey_)) return tabMap.get(regionKey_);
  const natKey = predictInputKey(HOLYROOD_NATIONAL_KEY, partyKey);
  if (tabMap.has(natKey)) {
    const nationalInput = tabMap.get(natKey);
    const nationalBase = holyroodNationalBaselineForPass(pass, state.nationalBaseline, state.nationalListBaseline).get(partyKey) ?? 0;
    const regionalBase = getPredictBaselineShare(regionKey, partyKey, holyroodBaselineForPass(pass, state.constBaseline, state.listBaseline));
    return roundPredictShareValue(clampNumber(regionalBase + (nationalInput - nationalBase), 0, 100));
  }
  return null;
}

/**
 * Returns the effective resolved share for a region/party for a given pass.
 * Resolution order: tab-specific (region override → national UNS) → regional baseline.
 * @param {string} regionKey
 * @param {string} partyKey
 * @param {'constituency'|'list'} pass
 * @param {object} state - { constBaseline, listBaseline, nationalBaseline, nationalListBaseline, constInput, listInput }
 * @returns {number} Share (0–100).
 */
export function resolvedHolyroodShare(regionKey, partyKey, pass, state) {
  if (pass !== 'constituency' && pass !== 'list') throw new Error(`Unknown pass: ${pass}`);
  const tabMap = pass === 'list' ? state.listInput : state.constInput;
  const tabVal = resolvedTabShare(regionKey, partyKey, tabMap, pass, state);
  if (tabVal !== null) return tabVal;
  return getPredictBaselineShare(regionKey, partyKey, holyroodBaselineForPass(pass, state.constBaseline, state.listBaseline));
}

/**
 * Calculates the 'other' share for the national row of a Holyrood predict tab.
 * @param {Map<string,number>} tabMap - Tab input map.
 * @param {'overall'|'constituency'|'list'} pass
 * @param {string[]} partyKeys - Column party keys for the tab.
 * @param {object} state - { nationalBaseline, nationalListBaseline }
 * @returns {number}
 */
export function holyroodNationalOtherShare(tabMap, pass, partyKeys, state) {
  const natBaselines = holyroodNationalBaselineForPass(pass, state.nationalBaseline, state.nationalListBaseline);
  const total = partyKeys.reduce((sum, pk) => {
    const natKey = predictInputKey(HOLYROOD_NATIONAL_KEY, pk);
    return sum + (tabMap.has(natKey) ? tabMap.get(natKey) : (natBaselines.get(pk) ?? 0));
  }, 0);
  return roundPredictShareValue(100 - total);
}

/**
 * Calculates the 'other' share for a region row using resolved values for the given pass.
 * @param {string} regionKey
 * @param {'overall'|'constituency'|'list'} pass
 * @param {string[]} partyKeys - Column party keys for the tab.
 * @param {object} state - Full Holyrood predict state (passed to resolvedHolyroodShare).
 * @returns {number}
 */
export function holyroodResolvedOtherShare(regionKey, pass, partyKeys, state) {
  const total = partyKeys.reduce((sum, pk) => sum + resolvedHolyroodShare(regionKey, pk, pass, state), 0);
  return roundPredictShareValue(100 - total);
}


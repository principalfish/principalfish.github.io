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

// ── Predict constants ────────────────────────────────────────────────────────

export const PREDICT_BASE_PARTY_KEYS = ['labour', 'conservative', 'libdems', 'green', 'reform'];
export const PREDICT_NI_PARTY_KEYS = ['sinnfein', 'dup', 'alliance', 'uu', 'sdlp'];
export const PREDICT_MODELLED_PARTY_KEYS = [
  ...PREDICT_BASE_PARTY_KEYS,
  'snp',
  'plaidcymru',
  ...PREDICT_NI_PARTY_KEYS,
];
export const PREDICT_ENGLAND_KEY = 'england';
export const PREDICT_SCOTLAND_KEY = 'scotland';
export const PREDICT_WALES_KEY = 'wales';
export const PREDICT_NI_KEY = 'northernireland';

// ── Party normalization ──────────────────────────────────────────────────────

/** Normalizes a raw party key string to a canonical lowercase key, applying aliases where needed. Returns 'others' for empty input. */
export function normalizePartyKey(partyKey) {
  const raw = String(partyKey || '').trim();
  if (!raw) return 'others';

  const lower = raw.toLowerCase();
  const alnum = lower.replace(/[^a-z0-9]/g, '');
  if (PARTY_KEY_ALIASES[alnum]) return PARTY_KEY_ALIASES[alnum];

  return lower;
}

// ── Formatting ───────────────────────────────────────────────────────────────

/** Rounds value to the nearest integer and formats it with GB locale thousands separators. */
export function formatInt(value) {
  return Math.round(value).toLocaleString('en-GB');
}

/** Formats value as a percentage string to two decimal places. */
export function formatPct(value) {
  return Number(value).toFixed(2);
}

/** Formats value with an explicit '+' prefix for positive numbers and the specified decimal digits. Returns '0' for values within floating-point epsilon of zero. */
export function formatSigned(value, digits = 0) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return '0';
  const sign = num > 0 ? '+' : '';
  return `${sign}${num.toFixed(digits)}`;
}

/** Returns a CSS class name reflecting whether value is positive, negative, or neutral. */
export function deltaClass(value) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return 'maps-delta-neutral';
  return num > 0 ? 'maps-delta-positive' : 'maps-delta-negative';
}

// ── Region normalization ─────────────────────────────────────────────────────

/** Converts a region name to a lowercase alphanumeric key with all non-alphanumeric characters removed. */
export function normalizeRegionKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Converts a region key or name to title case, splitting on camelCase boundaries, hyphens, and underscores. Returns 'Unknown' for empty input. */
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

/** Returns a trimmed, lowercase string suitable for use as a seat lookup key. */
export function seatLookupKey(seatName) {
  return String(seatName || '').trim().toLowerCase();
}

/** Returns the total votes cast in a seat, using the explicit turnout field if available, otherwise summing all party vote totals. */
export function totalVotesForSeat(seat) {
  const turnout = Number(seat?.turnout || 0);
  if (turnout > 0) return turnout;
  return Object.values(seat?.votes || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

/** Returns an array of { party, votes } objects for a seat, sorted descending by vote count, excluding parties with zero votes. */
export function sortedSeatVoteRows(seat) {
  return Object.entries(seat?.votes || {})
    .map(([party, votes]) => ({ party, votes: Number(votes || 0) }))
    .filter((row) => row.votes > 0)
    .sort((a, b) => b.votes - a.votes);
}

/** Returns { pct, raw } for the winning majority in a seat: pct as a percentage of total votes, raw as the vote margin between first and second place. */
export function seatMajorityStats(seat) {
  const voteRows = sortedSeatVoteRows(seat);
  if (voteRows.length < 2) return { pct: 0, raw: 0 };
  const marginVotes = voteRows[0].votes - voteRows[1].votes;
  const totalVotes = totalVotesForSeat(seat);
  if (totalVotes <= 0) return { pct: 0, raw: marginVotes };
  return { pct: (marginVotes / totalVotes) * 100, raw: marginVotes };
}

/** Returns the previous winner's party key if the seat changed hands between comparisonSeat and currentSeat, or null if there was no change or no comparison available. */
export function seatGainFromPartyKey(currentSeat, comparisonSeat) {
  const winner = currentSeat?.winner || 'others';
  const previousWinner = comparisonSeat?.winner || null;
  if (!previousWinner || previousWinner === winner) return null;
  return previousWinner;
}

/** Builds a Map from seatLookupKey to seat object for fast seat lookups. */
export function buildSeatIndex(seats) {
  const byKey = new Map();
  (seats || []).forEach((seat) => {
    if (!seat?.seat) return;
    byKey.set(seatLookupKey(seat.seat), seat);
  });
  return byKey;
}

/** Returns the party key of the second-place finisher in a seat, or null if fewer than two parties have votes. */
export function secondPlacePartyKey(seat) {
  const voteRows = sortedSeatVoteRows(seat);
  if (voteRows.length < 2) return null;
  return voteRows[1].party;
}

/** Returns the vote share percentage (0–100) for partyKey in the given seat. Returns 0 if total votes are zero. */
export function voteSharePct(seat, partyKey) {
  const totalVotes = totalVotesForSeat(seat);
  if (totalVotes <= 0) return 0;
  const partyVotes = Number(seat?.votes?.[partyKey] || 0);
  return (partyVotes / totalVotes) * 100;
}

// ── Election summary ─────────────────────────────────────────────────────────

/** Aggregates seats and votes across all constituencies, returning { parties, totalVotes, turnout, totalSeats }. Parties are sorted by seats descending then votes descending. Turnout is electorate-weighted. */
export function summarizeElection(seats) {
  const partyStats = new Map();
  let electorateSum = 0;
  let turnoutWeighted = 0;

  seats.forEach((seat) => {
    const winner = seat.winner === 'other' ? 'others' : (seat.winner || 'others');
    if (!partyStats.has(winner)) partyStats.set(winner, { seats: 0, votes: 0 });
    partyStats.get(winner).seats += 1;

    Object.entries(seat.votes || {}).forEach(([party, votes]) => {
      const key = party === 'other' ? 'others' : party;
      if (!partyStats.has(key)) partyStats.set(key, { seats: 0, votes: 0 });
      partyStats.get(key).votes += Number(votes || 0);
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
 * @param {object} resultsData - Raw results payload.
 * @param {Map<number, {key: string}>} [partiesById] - Optional manifest party lookup for integer party_id refs.
 * @param {Map<number, string>} [regionsById] - Optional manifest region lookup for integer region_id refs.
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

/** Returns true if regionKey normalizes to the Northern Ireland predict region. */
export function isPredictNorthernIrelandRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_NI_KEY;
}

/** Returns true if regionKey normalizes to the Scotland predict region. */
export function isPredictScotlandRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_SCOTLAND_KEY;
}

/** Returns true if regionKey normalizes to the Wales predict region. */
export function isPredictWalesRegion(regionKey) {
  return normalizeRegionKey(regionKey) === PREDICT_WALES_KEY;
}

/** Returns true if regionKey is a non-empty, non-NI, non-Scotland, non-Wales region (i.e. an English region). */
export function isPredictEnglishRegion(regionKey) {
  const key = normalizeRegionKey(regionKey);
  if (!key) return false;
  if (isPredictNorthernIrelandRegion(key)) return false;
  if (isPredictScotlandRegion(key)) return false;
  if (isPredictWalesRegion(key)) return false;
  return true;
}

// ── Predict baseline shares ──────────────────────────────────────────────────

/** Rounds a predict vote share value to the nearest integer. */
export function roundPredictShareValue(value) {
  return Math.round(Number(value || 0));
}

/** Clamps value to [minimum, maximum]. Returns minimum if value is not finite. */
export function clampNumber(value, minimum, maximum) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return minimum;
  return Math.max(minimum, Math.min(maximum, numeric));
}

/** Returns the composite Map key string used to store predict inputs: `${regionKey}::${partyKey}`. */
export function predictInputKey(regionKey, partyKey) {
  return `${regionKey}::${partyKey}`;
}

/** Formats a predict share value as an integer string. */
export function formatPredictShare(value) {
  return String(roundPredictShareValue(value));
}

/** Returns a new Map with all values rounded and clamped to [0, 100]. */
export function normalizePredictShareMap(sourceMap) {
  const normalized = new Map();
  (sourceMap || new Map()).forEach((value, key) => {
    normalized.set(key, roundPredictShareValue(clampNumber(value, 0, 100)));
  });
  return normalized;
}

/** Returns the nationalist party key for a region ('snp' for Scotland, 'plaidcymru' for Wales, null otherwise). */
export function predictNatPartyKeyForRegion(regionKey) {
  if (isPredictScotlandRegion(regionKey)) return 'snp';
  if (isPredictWalesRegion(regionKey)) return 'plaidcymru';
  return null;
}

export const PREDICT_NAT_COLUMN_KEY = 'nat';

/** Resolves a grid column party key to the actual party key for a given region. The 'nat' column maps to SNP or Plaid Cymru depending on region, and null if not applicable. */
export function resolvePredictInputPartyKey(regionKey, columnPartyKey) {
  if (columnPartyKey === PREDICT_NAT_COLUMN_KEY) {
    return predictNatPartyKeyForRegion(regionKey);
  }
  return columnPartyKey;
}

/** Returns the list of party keys for which predict inputs are shown for a given region (NI parties for NI, base + optional nationalist party for GB). */
export function collectPredictInputPartyKeysForRegion(regionKey) {
  if (isPredictNorthernIrelandRegion(regionKey)) {
    return [...PREDICT_NI_PARTY_KEYS];
  }
  const keys = [...PREDICT_BASE_PARTY_KEYS];
  const natPartyKey = predictNatPartyKeyForRegion(regionKey);
  if (natPartyKey) keys.push(natPartyKey);
  return keys;
}

/** Returns a shortened display label for a predict region, applying known abbreviations (e.g. 'Northern Ireland' → 'N Ireland'). */
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
  });

  return shareMap;
}

// ── Predict projection ───────────────────────────────────────────────────────

/**
 * Looks up the swing value for a party in a region from swingsByParty (Map<partyKey, Map<regionKey, swing>>).
 * Falls back to the 'england' aggregate swing for English sub-regions if no direct entry is found.
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

  const winner = Object.entries(projectedVotes)
    .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))[0]?.[0] || baseSeat.winner || 'others';

  return { ...baseSeat, votes: projectedVotes, turnout: totalVotes, winner };
}

// ── Seat / feature utilities ──────────────────────────────────────────────────

/**
 * Extracts the seat name from a TopoJSON feature's properties.
 * Tries `name`, `seat_name`, `seat`, `constituency`, and `Name` in order.
 * Returns null if none of the known properties are present.
 */
export function seatNameFromFeature(featureDatum) {
  const props = featureDatum?.properties || {};
  return props.name || props.seat_name || props.seat || props.constituency || props.Name || null;
}

/**
 * Returns a Map from seat name to winner party key for fast map colour lookups.
 * Each seat is stored under both its original name and a lowercase variant.
 * Seats without a `seat` property are skipped. Winner defaults to `'others'` if missing.
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
 * Returns a deep-ish copy of a seat record with normalised party keys.
 * Zero-vote and negative-vote entries are filtered out.
 * Duplicate keys that collapse after normalisation are summed.
 * Numeric fields (`electorate`, `turnout`) are coerced to numbers.
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
 */
export function getPredictInputShareValue(regionKey, partyKey, inputMap, baselineMap) {
  const cached = inputMap.get(predictInputKey(regionKey, partyKey));
  if (Number.isFinite(cached)) return Number(cached);
  return roundPredictShareValue(getPredictBaselineShare(regionKey, partyKey, baselineMap));
}

/**
 * Returns the sum of all predict input shares for a region across its modelled parties.
 * Uses `inputMap` for entered values, falling back to `baselineMap`.
 */
export function calculatePredictEnteredShareTotal(regionKey, inputMap, baselineMap) {
  return collectPredictInputPartyKeysForRegion(regionKey).reduce((sum, partyKey) => {
    return sum + Number(getPredictInputShareValue(regionKey, partyKey, inputMap, baselineMap) || 0);
  }, 0);
}

/**
 * Returns the implied 'other' share for a region: `100 - sum of entered party shares`, rounded.
 * Can be negative when inputs exceed 100%.
 */
export function calculatePredictOtherShare(regionKey, inputMap, baselineMap) {
  return roundPredictShareValue(100 - calculatePredictEnteredShareTotal(regionKey, inputMap, baselineMap));
}

// ── Predict region collection ─────────────────────────────────────────────────

/**
 * Returns all predict regions as `{ regionKey, regionLabel }` sorted alphabetically by label.
 * `baseRegionLabelsByKey` is a Map from normalised region key to display label.
 */
export function collectPredictAllRegions(baseRegionLabelsByKey) {
  return Array.from(baseRegionLabelsByKey.entries())
    .map(([regionKey, regionLabel]) => ({ regionKey, regionLabel }))
    .sort((a, b) => a.regionLabel.localeCompare(b.regionLabel));
}

/**
 * Returns all validation rows: the England aggregate row first, then every English, Scottish,
 * Welsh, and NI region from `baseRegionLabelsByKey`. Used to check no region exceeds 100%.
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
 */
export function collectPredictShareStateRows(baseRegionLabelsByKey) {
  return collectPredictValidationRows(baseRegionLabelsByKey);
}

/**
 * Returns the ordered list of row descriptors for the predict grid.
 * England aggregate is always first. English sub-regions follow when `englandExpanded` is true.
 * Scotland, Wales, and Northern Ireland are appended when present in `baseRegionLabelsByKey`.
 * Each row carries `{ regionKey, regionLabel, isEnglandAggregate, isEnglandRegion }`.
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

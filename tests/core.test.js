import { describe, it, expect } from 'vitest';
import {
  // Party
  normalizePartyKey,
  PREDICT_NI_PARTY_KEYS,
  PREDICT_MODELLED_PARTY_KEYS,
  PREDICT_BASE_PARTY_KEYS,
  PREDICT_NAT_COLUMN_KEY,
  // Formatting
  formatInt,
  formatPct,
  formatSigned,
  deltaClass,
  // Region
  normalizeRegionKey,
  titleCaseFromRegionKey,
  // Seat utilities
  seatLookupKey,
  totalVotesForSeat,
  sortedSeatVoteRows,
  seatMajorityStats,
  seatGainFromPartyKey,
  secondPlacePartyKey,
  voteSharePct,
  buildSeatIndex,
  // Election summary
  summarizeElection,
  // Seat normalization
  resolvePartyRef,
  normalizeSeats,
  // Region predicates
  isPredictNorthernIrelandRegion,
  isPredictScotlandRegion,
  isPredictWalesRegion,
  isPredictEnglishRegion,
  // Predict utilities
  roundPredictShareValue,
  clampNumber,
  predictInputKey,
  formatPredictShare,
  normalizePredictShareMap,
  predictNatPartyKeyForRegion,
  resolvePredictInputPartyKey,
  collectPredictInputPartyKeysForRegion,
  formatPredictRegionLabel,
  buildPredictBaselineShares,
  resolvedSwingValue,
  projectedSeatForPredictMode,
  // Holyrood utilities
  isListSeat,
  // Seat / feature utilities
  seatNameFromFeature,
  buildWinnerBySeat,
  cloneSeatRecord,
  pollTrackerDateLabel,
  // URL encode/decode
  encodePredictPayload,
  decodePredictPayload,
  // Map / region / filter utilities
  buildRegionLabelLookup,
  seatMatchesPrimaryFilters,
  buildVisibleSeatKeySet,
  getChoroplethValue,
  // Election file resolution
  resolveElectionFiles,
  // Predict share lookups
  getPredictBaselineShare,
  getPredictInputShareValue,
  calculatePredictEnteredShareTotal,
  calculatePredictOtherShare,
  // Predict region collection
  collectPredictAllRegions,
  collectPredictValidationRows,
  collectPredictShareStateRows,
  collectPredictInputRows,
  buildRegionSummary,
} from '../electionmaps/core.js';

// ── normalizePartyKey ────────────────────────────────────────────────────────

describe('normalizePartyKey', () => {
  it('returns known keys unchanged', () => {
    expect(normalizePartyKey('labour')).toBe('labour');
    expect(normalizePartyKey('conservative')).toBe('conservative');
    expect(normalizePartyKey('libdems')).toBe('libdems');
    expect(normalizePartyKey('reform')).toBe('reform');
    expect(normalizePartyKey('green')).toBe('green');
    expect(normalizePartyKey('snp')).toBe('snp');
    expect(normalizePartyKey('plaidcymru')).toBe('plaidcymru');
    expect(normalizePartyKey('uu')).toBe('uu');
    expect(normalizePartyKey('dup')).toBe('dup');
    expect(normalizePartyKey('sdlp')).toBe('sdlp');
    expect(normalizePartyKey('sinnfein')).toBe('sinnfein');
    expect(normalizePartyKey('alliance')).toBe('alliance');
    expect(normalizePartyKey('others')).toBe('others');
  });

  it('lowercases input before matching', () => {
    expect(normalizePartyKey('Labour')).toBe('labour');
    expect(normalizePartyKey('REFORM')).toBe('reform');
    expect(normalizePartyKey('Conservative')).toBe('conservative');
  });

  it('resolves full-name aliases by stripping non-alphanumeric', () => {
    expect(normalizePartyKey('Liberal Democrats')).toBe('libdems');
    expect(normalizePartyKey('Reform UK')).toBe('reform');
    expect(normalizePartyKey('Democratic Unionist Party')).toBe('dup');
    expect(normalizePartyKey('Scottish National Party')).toBe('snp');
    expect(normalizePartyKey('UK Independence Party')).toBe('ukip');
  });

  it('resolves ulsterunionistparty alias to uu (regression: was returning uup)', () => {
    expect(normalizePartyKey('Ulster Unionist Party')).toBe('uu');
    expect(normalizePartyKey('ulsterunionistparty')).toBe('uu');
    expect(normalizePartyKey('Ulster Unionist')).toBe('ulster unionist'); // no alias match — space preserved in return value
  });

  it('normalizes uup alias to canonical uu key', () => {
    expect(normalizePartyKey('uup')).toBe('uu');
    expect(normalizePartyKey('UUP')).toBe('uu');
  });

  it('returns others for empty/null/undefined', () => {
    expect(normalizePartyKey('')).toBe('others');
    expect(normalizePartyKey(null)).toBe('others');
    expect(normalizePartyKey(undefined)).toBe('others');
    expect(normalizePartyKey(0)).toBe('others');
  });

  it('returns lowercased unknown keys as-is', () => {
    expect(normalizePartyKey('SomeNewParty')).toBe('somenewparty');
    expect(normalizePartyKey('workers')).toBe('workers');
  });

  it('strips whitespace before matching', () => {
    expect(normalizePartyKey('  labour  ')).toBe('labour');
  });
});

// ── Party key constant invariants ────────────────────────────────────────────

describe('PREDICT_NI_PARTY_KEYS', () => {
  it('contains uu not uup (regression guard)', () => {
    expect(PREDICT_NI_PARTY_KEYS).toContain('uu');
    expect(PREDICT_NI_PARTY_KEYS).not.toContain('uup');
  });

  it('contains all expected NI parties', () => {
    expect(PREDICT_NI_PARTY_KEYS).toContain('sinnfein');
    expect(PREDICT_NI_PARTY_KEYS).toContain('dup');
    expect(PREDICT_NI_PARTY_KEYS).toContain('alliance');
    expect(PREDICT_NI_PARTY_KEYS).toContain('sdlp');
  });

  it('is a subset of PREDICT_MODELLED_PARTY_KEYS', () => {
    for (const key of PREDICT_NI_PARTY_KEYS) {
      expect(PREDICT_MODELLED_PARTY_KEYS).toContain(key);
    }
  });
});

describe('PREDICT_BASE_PARTY_KEYS', () => {
  it('contains the five main GB parties', () => {
    expect(PREDICT_BASE_PARTY_KEYS).toEqual(
      expect.arrayContaining(['labour', 'conservative', 'libdems', 'green', 'reform'])
    );
  });

  it('does not contain NI parties', () => {
    for (const key of PREDICT_NI_PARTY_KEYS) {
      expect(PREDICT_BASE_PARTY_KEYS).not.toContain(key);
    }
  });
});

// ── formatInt ────────────────────────────────────────────────────────────────

describe('formatInt', () => {
  it('formats with en-GB locale separators', () => {
    expect(formatInt(1000)).toBe('1,000');
    expect(formatInt(1000000)).toBe('1,000,000');
    expect(formatInt(0)).toBe('0');
  });

  it('rounds before formatting', () => {
    expect(formatInt(999.7)).toBe('1,000');
    expect(formatInt(999.4)).toBe('999');
  });
});

// ── formatPct ────────────────────────────────────────────────────────────────

describe('formatPct', () => {
  it('formats to 2 decimal places', () => {
    expect(formatPct(12.345)).toBe('12.35');
    expect(formatPct(0)).toBe('0.00');
    expect(formatPct(100)).toBe('100.00');
  });
});

// ── formatSigned ─────────────────────────────────────────────────────────────

describe('formatSigned', () => {
  it('prefixes positive values with +', () => {
    expect(formatSigned(5)).toBe('+5');
    expect(formatSigned(3.7, 1)).toBe('+3.7');
  });

  it('does not prefix negative values', () => {
    expect(formatSigned(-5)).toBe('-5');
    expect(formatSigned(-2.5, 1)).toBe('-2.5');
  });

  it('returns 0 for near-zero values', () => {
    expect(formatSigned(0)).toBe('0');
    expect(formatSigned(1e-10)).toBe('0');
    expect(formatSigned(-1e-10)).toBe('0');
  });

  it('respects digits parameter', () => {
    expect(formatSigned(3.456, 2)).toBe('+3.46');
    expect(formatSigned(-1.1, 0)).toBe('-1');
  });
});

// ── deltaClass ───────────────────────────────────────────────────────────────

describe('deltaClass', () => {
  it('returns positive class for positive values', () => {
    expect(deltaClass(5)).toBe('maps-delta-positive');
  });

  it('returns negative class for negative values', () => {
    expect(deltaClass(-3)).toBe('maps-delta-negative');
  });

  it('returns neutral class for zero or near-zero', () => {
    expect(deltaClass(0)).toBe('maps-delta-neutral');
    expect(deltaClass(1e-10)).toBe('maps-delta-neutral');
    expect(deltaClass(null)).toBe('maps-delta-neutral');
  });
});

// ── normalizeRegionKey ───────────────────────────────────────────────────────

describe('normalizeRegionKey', () => {
  it('lowercases and strips non-alphanumeric', () => {
    expect(normalizeRegionKey('Northern Ireland')).toBe('northernireland');
    expect(normalizeRegionKey('South East England')).toBe('southeastengland');
    expect(normalizeRegionKey('Yorkshire and The Humber')).toBe('yorkshireandthehumber');
    expect(normalizeRegionKey('East of England')).toBe('eastofengland');
  });

  it('returns empty string for empty/null input', () => {
    expect(normalizeRegionKey('')).toBe('');
    expect(normalizeRegionKey(null)).toBe('');
    expect(normalizeRegionKey(undefined)).toBe('');
  });

  it('handles already-normalized keys', () => {
    expect(normalizeRegionKey('scotland')).toBe('scotland');
    expect(normalizeRegionKey('wales')).toBe('wales');
    expect(normalizeRegionKey('northernireland')).toBe('northernireland');
  });
});

// ── titleCaseFromRegionKey ───────────────────────────────────────────────────

describe('titleCaseFromRegionKey', () => {
  it('returns Unknown for empty input', () => {
    expect(titleCaseFromRegionKey('')).toBe('Unknown');
    expect(titleCaseFromRegionKey(null)).toBe('Unknown');
  });

  it('title-cases space-separated words', () => {
    expect(titleCaseFromRegionKey('south east')).toBe('South East');
  });

  it('capitalises first letter of single word', () => {
    expect(titleCaseFromRegionKey('london')).toBe('London');
  });

  it('handles hyphens and underscores as word separators', () => {
    expect(titleCaseFromRegionKey('north-east')).toBe('North East');
    expect(titleCaseFromRegionKey('north_west')).toBe('North West');
  });
});

// ── seatLookupKey ────────────────────────────────────────────────────────────

describe('seatLookupKey', () => {
  it('lowercases and trims', () => {
    expect(seatLookupKey('  Bristol West  ')).toBe('bristol west');
    expect(seatLookupKey('HACKNEY NORTH')).toBe('hackney north');
  });

  it('preserves spaces within name', () => {
    expect(seatLookupKey('Cities of London and Westminster')).toBe('cities of london and westminster');
  });

  it('returns empty string for null/undefined', () => {
    expect(seatLookupKey(null)).toBe('');
    expect(seatLookupKey(undefined)).toBe('');
  });
});

// ── totalVotesForSeat ────────────────────────────────────────────────────────

describe('totalVotesForSeat', () => {
  it('prefers explicit turnout field over summing votes', () => {
    const seat = { turnout: 50000, votes: { labour: 30000, conservative: 15000 } };
    expect(totalVotesForSeat(seat)).toBe(50000);
  });

  it('sums votes object when turnout is 0', () => {
    const seat = { turnout: 0, votes: { labour: 30000, conservative: 15000, others: 5000 } };
    expect(totalVotesForSeat(seat)).toBe(50000);
  });

  it('sums votes when turnout is absent', () => {
    const seat = { votes: { labour: 20000, reform: 8000 } };
    expect(totalVotesForSeat(seat)).toBe(28000);
  });

  it('returns 0 for null/empty seat', () => {
    expect(totalVotesForSeat(null)).toBe(0);
    expect(totalVotesForSeat({})).toBe(0);
    expect(totalVotesForSeat({ votes: {} })).toBe(0);
  });

  it('ignores non-numeric vote values', () => {
    const seat = { turnout: 0, votes: { labour: 10000, conservative: null, others: undefined } };
    expect(totalVotesForSeat(seat)).toBe(10000);
  });
});

// ── sortedSeatVoteRows ───────────────────────────────────────────────────────

describe('sortedSeatVoteRows', () => {
  it('returns entries sorted by votes descending', () => {
    const seat = { votes: { labour: 10000, reform: 3000, conservative: 7000 } };
    const rows = sortedSeatVoteRows(seat);
    expect(rows.map((r) => r.party)).toEqual(['labour', 'conservative', 'reform']);
  });

  it('filters out zero and negative votes', () => {
    const seat = { votes: { labour: 10000, conservative: 0, others: -1 } };
    const rows = sortedSeatVoteRows(seat);
    expect(rows).toHaveLength(1);
    expect(rows[0].party).toBe('labour');
  });

  it('returns empty array for no votes', () => {
    expect(sortedSeatVoteRows({})).toEqual([]);
    expect(sortedSeatVoteRows(null)).toEqual([]);
  });
});

// ── seatMajorityStats ────────────────────────────────────────────────────────

describe('seatMajorityStats', () => {
  it('calculates raw margin and percentage', () => {
    const seat = { turnout: 0, votes: { labour: 30000, conservative: 20000, green: 5000 } };
    const stats = seatMajorityStats(seat);
    expect(stats.raw).toBe(10000);
    expect(stats.pct).toBeCloseTo((10000 / 55000) * 100);
  });

  it('returns pct=0 raw=0 for single candidate', () => {
    expect(seatMajorityStats({ votes: { labour: 30000 } })).toEqual({ pct: 0, raw: 0 });
  });

  it('returns pct=0 raw=margin when total votes is 0', () => {
    const seat = { turnout: 0, votes: {} };
    expect(seatMajorityStats(seat)).toEqual({ pct: 0, raw: 0 });
  });

  it('uses turnout for total when set', () => {
    // turnout 60000, first 30000, second 20000 → margin 10000, pct = 10000/60000
    const seat = { turnout: 60000, votes: { labour: 30000, conservative: 20000 } };
    const stats = seatMajorityStats(seat);
    expect(stats.raw).toBe(10000);
    expect(stats.pct).toBeCloseTo((10000 / 60000) * 100);
  });
});

// ── seatGainFromPartyKey ─────────────────────────────────────────────────────

describe('seatGainFromPartyKey', () => {
  it('returns previous winner when seat changes hands', () => {
    expect(seatGainFromPartyKey({ winner: 'labour' }, { winner: 'conservative' })).toBe('conservative');
    expect(seatGainFromPartyKey({ winner: 'reform' }, { winner: 'conservative' })).toBe('conservative');
  });

  it('returns null when seat holder unchanged', () => {
    expect(seatGainFromPartyKey({ winner: 'labour' }, { winner: 'labour' })).toBeNull();
  });

  it('returns null when no comparison seat', () => {
    expect(seatGainFromPartyKey({ winner: 'labour' }, null)).toBeNull();
    expect(seatGainFromPartyKey({ winner: 'labour' }, undefined)).toBeNull();
  });

  it('defaults missing winner to others', () => {
    expect(seatGainFromPartyKey({}, { winner: 'labour' })).toBe('labour');
  });
});

// ── secondPlacePartyKey ──────────────────────────────────────────────────────

describe('secondPlacePartyKey', () => {
  it('returns the second-highest vote party', () => {
    const seat = { votes: { labour: 30000, conservative: 20000, reform: 5000 } };
    expect(secondPlacePartyKey(seat)).toBe('conservative');
  });

  it('returns null for single candidate', () => {
    expect(secondPlacePartyKey({ votes: { labour: 30000 } })).toBeNull();
    expect(secondPlacePartyKey({ votes: {} })).toBeNull();
    expect(secondPlacePartyKey(null)).toBeNull();
  });
});

// ── voteSharePct ─────────────────────────────────────────────────────────────

describe('voteSharePct', () => {
  it('calculates percentage of total votes', () => {
    const seat = { turnout: 0, votes: { labour: 30000, conservative: 20000 } };
    expect(voteSharePct(seat, 'labour')).toBeCloseTo(60);
    expect(voteSharePct(seat, 'conservative')).toBeCloseTo(40);
  });

  it('returns 0 for party not in seat', () => {
    const seat = { votes: { labour: 30000 } };
    expect(voteSharePct(seat, 'reform')).toBe(0);
  });

  it('returns 0 when total votes is 0', () => {
    expect(voteSharePct({ votes: {} }, 'labour')).toBe(0);
  });

  it('uses turnout as denominator when set', () => {
    const seat = { turnout: 60000, votes: { labour: 30000 } };
    expect(voteSharePct(seat, 'labour')).toBeCloseTo(50);
  });
});

// ── buildSeatIndex ───────────────────────────────────────────────────────────

describe('buildSeatIndex', () => {
  it('indexes seats by lowercase trimmed name', () => {
    const seats = [
      { seat: 'Bristol West', winner: 'labour' },
      { seat: 'Hackney North', winner: 'labour' },
    ];
    const index = buildSeatIndex(seats);
    expect(index.get('bristol west')).toBe(seats[0]);
    expect(index.get('hackney north')).toBe(seats[1]);
    expect(index.size).toBe(2);
  });

  it('skips entries without a seat name', () => {
    const index = buildSeatIndex([{ winner: 'labour' }, null]);
    expect(index.size).toBe(0);
  });

  it('returns empty map for empty/null input', () => {
    expect(buildSeatIndex([]).size).toBe(0);
    expect(buildSeatIndex(null).size).toBe(0);
  });
});

// ── summarizeElection ────────────────────────────────────────────────────────

describe('summarizeElection', () => {
  it('counts seats per party', () => {
    const seats = [
      { winner: 'labour', votes: {}, electorate: 0, turnout: 0 },
      { winner: 'labour', votes: {}, electorate: 0, turnout: 0 },
      { winner: 'conservative', votes: {}, electorate: 0, turnout: 0 },
    ];
    const { parties } = summarizeElection(seats);
    expect(parties.find((p) => p.party === 'labour').seats).toBe(2);
    expect(parties.find((p) => p.party === 'conservative').seats).toBe(1);
  });

  it('totals votes across all seats', () => {
    const seats = [
      { winner: 'labour', votes: { labour: 30000, conservative: 15000 }, electorate: 0, turnout: 0 },
      { winner: 'labour', votes: { labour: 25000, reform: 10000 }, electorate: 0, turnout: 0 },
    ];
    const { parties, totalVotes } = summarizeElection(seats);
    expect(parties.find((p) => p.party === 'labour').votes).toBe(55000);
    expect(totalVotes).toBe(80000);
  });

  it('sorts by seats desc then votes desc', () => {
    const seats = [
      { winner: 'conservative', votes: { conservative: 5000 }, electorate: 0, turnout: 0 },
      { winner: 'labour', votes: { labour: 30000 }, electorate: 0, turnout: 0 },
      { winner: 'labour', votes: { labour: 25000 }, electorate: 0, turnout: 0 },
    ];
    const { parties } = summarizeElection(seats);
    expect(parties[0].party).toBe('labour');
    expect(parties[1].party).toBe('conservative');
  });

  it('calculates weighted average turnout', () => {
    const seats = [
      { winner: 'labour', votes: {}, electorate: 40000, turnout: 30000 },
      { winner: 'conservative', votes: {}, electorate: 60000, turnout: 40000 },
    ];
    const { turnout } = summarizeElection(seats);
    // weighted: (30000*40000 + 40000*60000) / (40000+60000) = 3600000000/100000 = 36000
    expect(turnout).toBeCloseTo(36000);
  });

  it('returns zero turnout when electorate is 0', () => {
    const seats = [{ winner: 'labour', votes: {}, electorate: 0, turnout: 30000 }];
    expect(summarizeElection(seats).turnout).toBe(0);
  });

  it('handles empty array', () => {
    const summary = summarizeElection([]);
    expect(summary.parties).toEqual([]);
    expect(summary.totalSeats).toBe(0);
    expect(summary.totalVotes).toBe(0);
  });

  it('merges "other" into "others" for seats and votes', () => {
    const seats = [
      { winner: 'other', votes: { other: 24120, labour: 15000 }, electorate: 0, turnout: 0 },
      { winner: 'others', votes: { others: 500, labour: 10000 }, electorate: 0, turnout: 0 },
    ];
    const { parties } = summarizeElection(seats);
    expect(parties.find((p) => p.party === 'other')).toBeUndefined();
    const othersRow = parties.find((p) => p.party === 'others');
    expect(othersRow.seats).toBe(2);
    expect(othersRow.votes).toBe(24620);
  });
});

// ── normalizeSeats ───────────────────────────────────────────────────────────

describe('normalizeSeats', () => {
  it('parses compact array format (seat.p) — v4 style', () => {
    const raw = {
      schema: 'pf-results-v4',
      seats: [{
        n: 'Bristol West', r: 'southwestengland', w: 'labour',
        p: [['labour', 28000], ['uu', 12000], ['reform', 8000]],
      }],
    };
    const [seat] = normalizeSeats(raw);
    expect(seat.seat).toBe('Bristol West');
    expect(seat.region).toBe('southwestengland');
    expect(seat.winner).toBe('labour');
    expect(seat.votes.labour).toBe(28000);
    expect(seat.votes.uu).toBe(12000);
    expect(seat.votes.reform).toBe(8000);
  });

  it('parses object votes format and normalizes party keys', () => {
    const raw = {
      seats: [{
        seat: 'Belfast South', region: 'Northern Ireland',
        winner: 'uu', votes: { uu: 15000, sinnfein: 12000 },
      }],
    };
    const [seat] = normalizeSeats(raw);
    expect(seat.winner).toBe('uu');
    expect(seat.votes.uu).toBe(15000);
    expect(seat.votes.sinnfein).toBe(12000);
  });

  it('normalizes winner key through normalizePartyKey', () => {
    const raw = {
      seats: [{ seat: 'X', region: 'r', winner: 'Liberal Democrats', votes: {} }],
    };
    expect(normalizeSeats(raw)[0].winner).toBe('libdems');
  });

  it('normalizes party keys in compact format', () => {
    const raw = {
      seats: [{
        n: 'X', r: 'r', w: 'uu',
        p: [['Ulster Unionist Party', 5000], ['Reform UK', 2000]],
      }],
    };
    const [seat] = normalizeSeats(raw);
    expect(seat.votes.uu).toBe(5000);
    expect(seat.votes.reform).toBe(2000);
  });

  it('handles p tuples with legacy 3-element format (name in index 2) gracefully', () => {
    const raw = {
      seats: [{
        n: 'X', r: 'r', w: 'labour',
        p: [['labour', 28000, 'Jane Smith'], ['reform', 8000, 'Some Candidate']],
      }],
    };
    const [seat] = normalizeSeats(raw);
    expect(seat.votes.labour).toBe(28000);
    expect(seat.votes.reform).toBe(8000);
  });

  it('filters out zero/negative votes in object format', () => {
    const raw = {
      seats: [{
        seat: 'X', region: 'r', winner: 'labour',
        votes: { labour: 10000, conservative: 0, others: -1 },
      }],
    };
    const [seat] = normalizeSeats(raw);
    expect(seat.votes.labour).toBe(10000);
    expect(seat.votes.conservative).toBeUndefined();
    expect(seat.votes.others).toBeUndefined();
  });

  it('handles compact format with zero votes', () => {
    const raw = {
      seats: [{ n: 'X', r: 'r', w: 'labour', p: [['labour', 0], ['reform', 5000]] }],
    };
    expect(normalizeSeats(raw)[0].votes.labour).toBeUndefined();
    expect(normalizeSeats(raw)[0].votes.reform).toBe(5000);
  });

  it('returns empty array for invalid input shapes', () => {
    expect(normalizeSeats(null)).toEqual([]);
    expect(normalizeSeats({})).toEqual([]);
    expect(normalizeSeats({ seats: 'oops' })).toEqual([]);
  });

  it('merges duplicate party keys within a seat', () => {
    const raw = {
      seats: [{
        n: 'X', r: 'r', w: 'labour',
        p: [['Labour', 10000], ['labour', 5000]],
      }],
    };
    // Both normalize to 'labour' — should be summed
    expect(normalizeSeats(raw)[0].votes.labour).toBe(15000);
  });
});

// ── resolvePartyRef ──────────────────────────────────────────────────────────

describe('resolvePartyRef', () => {
  const partiesById = new Map([
    [1, { key: 'labour' }],
    [2, { key: 'reform' }],
    [7, { key: 'others' }],
    [14, { key: 'ukip' }],
  ]);

  it('resolves integer id to canonical key via partiesById', () => {
    expect(resolvePartyRef(1, partiesById)).toBe('labour');
    expect(resolvePartyRef(2, partiesById)).toBe('reform');
    expect(resolvePartyRef(14, partiesById)).toBe('ukip');
  });

  it('resolves numeric string id to canonical key via partiesById', () => {
    expect(resolvePartyRef('1', partiesById)).toBe('labour');
    expect(resolvePartyRef('7', partiesById)).toBe('others');
  });

  it('falls back to normalizePartyKey for integer id not in map', () => {
    expect(resolvePartyRef(99, partiesById)).toBe('99');
  });

  it('resolves string party key via normalizePartyKey when not a numeric ref', () => {
    expect(resolvePartyRef('labour', partiesById)).toBe('labour');
    expect(resolvePartyRef('Reform UK', partiesById)).toBe('reform');
    expect(resolvePartyRef('', partiesById)).toBe('others');
  });

  it('works without partiesById (string keys only)', () => {
    expect(resolvePartyRef('conservative', undefined)).toBe('conservative');
    expect(resolvePartyRef('Liberal Democrats', undefined)).toBe('libdems');
  });
});

// ── normalizeSeats with integer party refs (pf-results-v4) ───────────────────

describe('normalizeSeats with partiesById (pf-results-v4 format)', () => {
  const partiesById = new Map([
    [1, { key: 'labour' }],
    [2, { key: 'reform' }],
    [4, { key: 'conservative' }],
    [7, { key: 'others' }],
  ]);

  it('resolves integer winner and p entries to canonical keys', () => {
    const raw = {
      schema: 'pf-results-v4',
      seats: [{
        n: 'Bristol West', r: 'southwestengland', w: 1,
        p: [[1, 28000], [4, 12000], [2, 8000]],
      }],
    };
    const [seat] = normalizeSeats(raw, partiesById);
    expect(seat.winner).toBe('labour');
    expect(seat.votes.labour).toBe(28000);
    expect(seat.votes.conservative).toBe(12000);
    expect(seat.votes.reform).toBe(8000);
  });

  it('resolves integer winner when using w field', () => {
    const raw = {
      seats: [{ n: 'X', r: 'r', w: 7, p: [[7, 1000]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById);
    expect(seat.winner).toBe('others');
    expect(seat.votes.others).toBe(1000);
  });

  it('falls back to string representation for unknown integer id', () => {
    const raw = {
      seats: [{ n: 'X', r: 'r', w: 99, p: [[99, 500]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById);
    expect(seat.winner).toBe('99');
    expect(seat.votes['99']).toBe(500);
  });

  it('still handles string keys when partiesById is provided (backwards compat)', () => {
    const raw = {
      seats: [{ n: 'X', r: 'r', w: 'labour', p: [['labour', 5000]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById);
    expect(seat.winner).toBe('labour');
    expect(seat.votes.labour).toBe(5000);
  });

  it('electorate and turnout default to 0 when fields absent (v4 omits them)', () => {
    const raw = {
      schema: 'pf-results-v4',
      seats: [{ n: 'X', r: 'r', w: 1, p: [[1, 10000]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById);
    expect(seat.electorate).toBe(0);
    expect(seat.turnout).toBe(0);
  });
});

// ── normalizeSeats with integer region IDs (pf-results-v4) ───────────────────

describe('normalizeSeats with regionsById (pf-results-v4 region IDs)', () => {
  const partiesById = new Map([[1, { key: 'labour' }], [4, { key: 'conservative' }]]);
  const regionsById = new Map([
    [23, 'wales'],
    [15, 'northwestengland'],
    [17, 'london'],
    [22, 'scotland'],
  ]);

  it('resolves integer r to region key via regionsById', () => {
    const raw = {
      schema: 'pf-results-v4',
      seats: [{ n: 'Aberafan Maesteg', r: 23, w: 1, p: [[1, 17838], [4, 7484]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById, regionsById);
    expect(seat.region).toBe('wales');
  });

  it('resolves multiple different integer region IDs', () => {
    const raw = {
      schema: 'pf-results-v4',
      seats: [
        { n: 'Seat A', r: 15, w: 1, p: [[1, 10000]] },
        { n: 'Seat B', r: 17, w: 1, p: [[1, 8000]] },
        { n: 'Seat C', r: 22, w: 1, p: [[1, 9000]] },
      ],
    };
    const seats = normalizeSeats(raw, partiesById, regionsById);
    expect(seats[0].region).toBe('northwestengland');
    expect(seats[1].region).toBe('london');
    expect(seats[2].region).toBe('scotland');
  });

  it('falls back to string "unknown" for region ID not in map', () => {
    const raw = {
      seats: [{ n: 'X', r: 999, w: 1, p: [[1, 5000]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById, regionsById);
    expect(seat.region).toBe('unknown');
  });

  it('still resolves string r when regionsById is provided (backwards compat)', () => {
    const raw = {
      seats: [{ n: 'X', r: 'wales', w: 1, p: [[1, 5000]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById, regionsById);
    expect(seat.region).toBe('wales');
  });

  it('returns string "0" region for r=0 with no regionsById match', () => {
    const raw = {
      seats: [{ n: 'X', r: 0, w: 1, p: [[1, 5000]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById, regionsById);
    // 0 is falsy — falls through to String(raw || 'unknown') → 'unknown'
    expect(seat.region).toBe('unknown');
  });

  it('works without regionsById — integer r becomes string', () => {
    const raw = {
      seats: [{ n: 'X', r: 23, w: 1, p: [[1, 5000]] }],
    };
    const [seat] = normalizeSeats(raw, partiesById);
    expect(seat.region).toBe('23');
  });
});

// ── Region predicates ────────────────────────────────────────────────────────

describe('isPredictNorthernIrelandRegion', () => {
  it('matches northernireland in various forms', () => {
    expect(isPredictNorthernIrelandRegion('northernireland')).toBe(true);
    expect(isPredictNorthernIrelandRegion('Northern Ireland')).toBe(true);
    expect(isPredictNorthernIrelandRegion('NORTHERN IRELAND')).toBe(true);
  });

  it('does not match other regions', () => {
    expect(isPredictNorthernIrelandRegion('scotland')).toBe(false);
    expect(isPredictNorthernIrelandRegion('london')).toBe(false);
    expect(isPredictNorthernIrelandRegion('')).toBe(false);
  });
});

describe('isPredictScotlandRegion', () => {
  it('matches scotland', () => {
    expect(isPredictScotlandRegion('scotland')).toBe(true);
    expect(isPredictScotlandRegion('Scotland')).toBe(true);
  });
  it('does not match others', () => {
    expect(isPredictScotlandRegion('wales')).toBe(false);
    expect(isPredictScotlandRegion('northernireland')).toBe(false);
  });
});

describe('isPredictWalesRegion', () => {
  it('matches wales', () => {
    expect(isPredictWalesRegion('wales')).toBe(true);
    expect(isPredictWalesRegion('Wales')).toBe(true);
  });
  it('does not match others', () => {
    expect(isPredictWalesRegion('scotland')).toBe(false);
    expect(isPredictWalesRegion('london')).toBe(false);
  });
});

describe('isPredictEnglishRegion', () => {
  it('matches English regions', () => {
    expect(isPredictEnglishRegion('london')).toBe(true);
    expect(isPredictEnglishRegion('southeastengland')).toBe(true);
    expect(isPredictEnglishRegion('South West England')).toBe(true);
    expect(isPredictEnglishRegion('East Midlands')).toBe(true);
  });

  it('excludes NI, Scotland, Wales', () => {
    expect(isPredictEnglishRegion('northernireland')).toBe(false);
    expect(isPredictEnglishRegion('scotland')).toBe(false);
    expect(isPredictEnglishRegion('wales')).toBe(false);
  });

  it('returns false for empty input', () => {
    expect(isPredictEnglishRegion('')).toBe(false);
    expect(isPredictEnglishRegion(null)).toBe(false);
  });
});

// ── Predict utilities ────────────────────────────────────────────────────────

describe('roundPredictShareValue', () => {
  it('rounds to nearest integer', () => {
    expect(roundPredictShareValue(14.6)).toBe(15);
    expect(roundPredictShareValue(14.4)).toBe(14);
    expect(roundPredictShareValue(14.5)).toBe(15);
    expect(roundPredictShareValue(0)).toBe(0);
  });

  it('handles null/undefined as 0', () => {
    expect(roundPredictShareValue(null)).toBe(0);
    expect(roundPredictShareValue(undefined)).toBe(0);
  });
});

describe('clampNumber', () => {
  it('clamps within range', () => {
    expect(clampNumber(150, 0, 100)).toBe(100);
    expect(clampNumber(-5, 0, 100)).toBe(0);
    expect(clampNumber(50, 0, 100)).toBe(50);
    expect(clampNumber(0, 0, 100)).toBe(0);
    expect(clampNumber(100, 0, 100)).toBe(100);
  });

  it('returns minimum for NaN and Infinity (both non-finite)', () => {
    expect(clampNumber(NaN, 0, 100)).toBe(0);
    expect(clampNumber(Infinity, 0, 100)).toBe(0); // Number.isFinite(Infinity) === false
    expect(clampNumber(-Infinity, 0, 100)).toBe(0);
  });
});

describe('predictInputKey', () => {
  it('joins regionKey and partyKey with ::', () => {
    expect(predictInputKey('london', 'labour')).toBe('london::labour');
    expect(predictInputKey('northernireland', 'uu')).toBe('northernireland::uu');
  });
});

describe('formatPredictShare', () => {
  it('rounds and stringifies', () => {
    expect(formatPredictShare(14.7)).toBe('15');
    expect(formatPredictShare(0)).toBe('0');
    expect(formatPredictShare(33.3)).toBe('33');
  });
});

describe('normalizePredictShareMap', () => {
  it('rounds and clamps all values', () => {
    const input = new Map([
      ['london::labour', 45.7],
      ['london::conservative', 110],
      ['london::reform', -5],
    ]);
    const result = normalizePredictShareMap(input);
    expect(result.get('london::labour')).toBe(46);
    expect(result.get('london::conservative')).toBe(100);
    expect(result.get('london::reform')).toBe(0);
  });

  it('handles null/undefined input', () => {
    expect(normalizePredictShareMap(null).size).toBe(0);
    expect(normalizePredictShareMap(undefined).size).toBe(0);
  });
});

describe('predictNatPartyKeyForRegion', () => {
  it('returns snp for Scotland', () => {
    expect(predictNatPartyKeyForRegion('scotland')).toBe('snp');
    expect(predictNatPartyKeyForRegion('Scotland')).toBe('snp');
  });

  it('returns plaidcymru for Wales', () => {
    expect(predictNatPartyKeyForRegion('wales')).toBe('plaidcymru');
    expect(predictNatPartyKeyForRegion('Wales')).toBe('plaidcymru');
  });

  it('returns null for all other regions', () => {
    expect(predictNatPartyKeyForRegion('london')).toBeNull();
    expect(predictNatPartyKeyForRegion('northernireland')).toBeNull();
    expect(predictNatPartyKeyForRegion('')).toBeNull();
  });
});

describe('resolvePredictInputPartyKey', () => {
  it('resolves nat column to snp for Scotland', () => {
    expect(resolvePredictInputPartyKey('scotland', PREDICT_NAT_COLUMN_KEY)).toBe('snp');
  });

  it('resolves nat column to plaidcymru for Wales', () => {
    expect(resolvePredictInputPartyKey('wales', PREDICT_NAT_COLUMN_KEY)).toBe('plaidcymru');
  });

  it('returns nat column as null for English regions (no nat party)', () => {
    expect(resolvePredictInputPartyKey('london', PREDICT_NAT_COLUMN_KEY)).toBeNull();
  });

  it('returns the party key unchanged for non-nat columns', () => {
    expect(resolvePredictInputPartyKey('london', 'labour')).toBe('labour');
    expect(resolvePredictInputPartyKey('scotland', 'conservative')).toBe('conservative');
  });
});

describe('collectPredictInputPartyKeysForRegion', () => {
  it('returns NI-specific keys for Northern Ireland', () => {
    const keys = collectPredictInputPartyKeysForRegion('northernireland');
    expect(keys).toEqual(PREDICT_NI_PARTY_KEYS);
    expect(keys).toContain('uu');
    expect(keys).not.toContain('labour');
  });

  it('returns base + snp for Scotland', () => {
    const keys = collectPredictInputPartyKeysForRegion('scotland');
    expect(keys).toContain('snp');
    for (const k of PREDICT_BASE_PARTY_KEYS) expect(keys).toContain(k);
    expect(keys).not.toContain('plaidcymru');
  });

  it('returns base + plaidcymru for Wales', () => {
    const keys = collectPredictInputPartyKeysForRegion('wales');
    expect(keys).toContain('plaidcymru');
    expect(keys).not.toContain('snp');
  });

  it('returns only base keys for English regions', () => {
    const keys = collectPredictInputPartyKeysForRegion('london');
    expect(keys).toEqual(PREDICT_BASE_PARTY_KEYS);
    expect(keys).not.toContain('snp');
    expect(keys).not.toContain('plaidcymru');
  });
});

describe('formatPredictRegionLabel', () => {
  it('abbreviates known long region names', () => {
    expect(formatPredictRegionLabel('Northern Ireland')).toBe('N Ireland');
    expect(formatPredictRegionLabel('North East England')).toBe('North East');
    expect(formatPredictRegionLabel('North West England')).toBe('North West');
    expect(formatPredictRegionLabel('South East England')).toBe('South East');
    expect(formatPredictRegionLabel('South West England')).toBe('South West');
    expect(formatPredictRegionLabel('East of England')).toBe('E of England');
    expect(formatPredictRegionLabel('Yorkshire and The Humber')).toBe('Yorks');
  });

  it('returns label unchanged for unrecognised regions', () => {
    expect(formatPredictRegionLabel('London')).toBe('London');
    expect(formatPredictRegionLabel('Scotland')).toBe('Scotland');
    expect(formatPredictRegionLabel('Wales')).toBe('Wales');
  });

  it('handles empty/null input', () => {
    expect(formatPredictRegionLabel('')).toBe('');
    expect(formatPredictRegionLabel(null)).toBe('');
  });
});

// ── buildPredictBaselineShares ───────────────────────────────────────────────

describe('buildPredictBaselineShares', () => {
  it('calculates NI party shares using uu key (regression guard)', () => {
    const seats = [
      { seat: 'Belfast South', region: 'northernireland', winner: 'uu', turnout: 0,
        votes: { uu: 15000, sinnfein: 10000, dup: 8000, alliance: 7000, sdlp: 5000 } },
      { seat: 'Belfast East', region: 'northernireland', winner: 'dup', turnout: 0,
        votes: { uu: 5000, sinnfein: 8000, dup: 20000, alliance: 4000, sdlp: 3000 } },
    ];
    // Total NI: 45000 + 40000 = 85000; UU: 15000+5000=20000
    const shareMap = buildPredictBaselineShares(seats);
    expect(shareMap.get('northernireland::uu')).toBe(Math.round((20000 / 85000) * 100));
    expect(shareMap.get('northernireland::uu')).toBeGreaterThan(0);
  });

  it('does not produce a uup key in the share map', () => {
    const seats = [
      { seat: 'Belfast South', region: 'northernireland', winner: 'uu', turnout: 0,
        votes: { uu: 15000, sinnfein: 10000 } },
    ];
    const shareMap = buildPredictBaselineShares(seats);
    expect(shareMap.has('northernireland::uup')).toBe(false);
    expect(shareMap.has('northernireland::uu')).toBe(true);
  });

  it('aggregates English regions into england rollup key', () => {
    const seats = [
      { seat: 'Bristol West', region: 'southwestengland', winner: 'labour', turnout: 0,
        votes: { labour: 30000, conservative: 15000 } },
      { seat: 'Hackney North', region: 'london', winner: 'labour', turnout: 0,
        votes: { labour: 25000, conservative: 10000 } },
    ];
    const shareMap = buildPredictBaselineShares(seats);
    // England: 55000 labour / 80000 total
    expect(shareMap.get('england::labour')).toBe(Math.round((55000 / 80000) * 100));
    expect(shareMap.has('southwestengland::labour')).toBe(true);
    expect(shareMap.has('london::labour')).toBe(true);
  });

  it('does not roll NI/Scotland/Wales into england key', () => {
    const seats = [
      { seat: 'Glasgow East', region: 'scotland', winner: 'snp', turnout: 0,
        votes: { snp: 20000, labour: 10000 } },
    ];
    const shareMap = buildPredictBaselineShares(seats);
    expect(shareMap.has('england::snp')).toBe(false);
    expect(shareMap.has('scotland::snp')).toBe(true);
  });

  it('skips seats with zero total votes', () => {
    const seats = [{ seat: 'X', region: 'london', winner: 'others', turnout: 0, votes: {} }];
    expect(buildPredictBaselineShares(seats).size).toBe(0);
  });

  it('handles null/empty input', () => {
    expect(buildPredictBaselineShares([]).size).toBe(0);
    expect(buildPredictBaselineShares(null).size).toBe(0);
  });
});

// ── resolvedSwingValue ───────────────────────────────────────────────────────

describe('resolvedSwingValue', () => {
  const makeSwings = (regionKey, partyKey, value) => {
    const swingsByParty = new Map();
    swingsByParty.set(partyKey, new Map([[regionKey, value]]));
    return swingsByParty;
  };

  it('returns direct regional swing when present', () => {
    const swings = makeSwings('london', 'labour', 5);
    expect(resolvedSwingValue('london', 'labour', swings)).toBe(5);
  });

  it('falls back to england-level swing for English regions', () => {
    const swings = makeSwings('england', 'labour', 8);
    expect(resolvedSwingValue('southwestengland', 'labour', swings)).toBe(8);
  });

  it('does not apply england fallback for NI/Scotland/Wales', () => {
    const swings = makeSwings('england', 'labour', 8);
    expect(resolvedSwingValue('scotland', 'labour', swings)).toBe(0);
    expect(resolvedSwingValue('wales', 'labour', swings)).toBe(0);
    expect(resolvedSwingValue('northernireland', 'labour', swings)).toBe(0);
  });

  it('prefers direct swing over england fallback', () => {
    const swingsByParty = new Map();
    const partySwings = new Map([['london', 3], ['england', 8]]);
    swingsByParty.set('labour', partySwings);
    expect(resolvedSwingValue('london', 'labour', swingsByParty)).toBe(3);
  });

  it('returns 0 for unknown party', () => {
    expect(resolvedSwingValue('london', 'labour', new Map())).toBe(0);
  });

  it('returns 0 for empty region', () => {
    expect(resolvedSwingValue('', 'labour', new Map())).toBe(0);
  });
});

// ── projectedSeatForPredictMode ──────────────────────────────────────────────

describe('projectedSeatForPredictMode', () => {
  const baseSeat = {
    seat: 'Bristol West',
    region: 'southwestengland',
    winner: 'labour',
    turnout: 0,
    votes: { labour: 30000, conservative: 15000, reform: 5000 },
  };

  it('returns base seat unchanged when no swings', () => {
    const projected = projectedSeatForPredictMode(baseSeat, new Map());
    // Labour still highest
    expect(projected.winner).toBe('labour');
    expect(projected.votes.labour).toBeCloseTo(30000, 0);
  });

  it('applies positive swing to boost a party', () => {
    const swingsByParty = new Map();
    // +35 swing to conservative: base 30% → 65%, labour stays at 60% → conservative wins
    swingsByParty.set('conservative', new Map([['southwestengland', 35]]));
    const projected = projectedSeatForPredictMode(baseSeat, swingsByParty);
    expect(projected.votes.conservative).toBeGreaterThan(15000);
    expect(projected.winner).toBe('conservative');
  });

  it('applies negative swing to reduce a party to 0 minimum', () => {
    const swingsByParty = new Map();
    swingsByParty.set('reform', new Map([['southwestengland', -20]]));
    const projected = projectedSeatForPredictMode(baseSeat, swingsByParty);
    // Reform had 10% share, -20 swing → clamped to 0
    expect(projected.votes.reform ?? 0).toBe(0);
  });

  it('preserves total vote count (turnout)', () => {
    const swingsByParty = new Map();
    swingsByParty.set('labour', new Map([['southwestengland', -10]]));
    const projected = projectedSeatForPredictMode(baseSeat, swingsByParty);
    expect(projected.turnout).toBe(50000);
  });

  it('returns base seat as-is when total votes is 0', () => {
    const emptySeat = { ...baseSeat, votes: {}, turnout: 0 };
    const projected = projectedSeatForPredictMode(emptySeat, new Map());
    expect(projected).toEqual(emptySeat);
  });

  it('uses england-level swing as fallback for English seat', () => {
    const swingsByParty = new Map();
    swingsByParty.set('labour', new Map([['england', -15]]));
    const projected = projectedSeatForPredictMode(baseSeat, swingsByParty);
    // Labour base share 60%, -15 → 45%, should have fewer votes
    expect(projected.votes.labour).toBeLessThan(30000);
  });

  it('does not use england-level swing for NI seat', () => {
    const niSeat = {
      seat: 'Belfast South', region: 'northernireland', winner: 'uu', turnout: 0,
      votes: { uu: 15000, sinnfein: 10000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('uu', new Map([['england', 10]]));
    const projected = projectedSeatForPredictMode(niSeat, swingsByParty);
    // england swing should NOT apply to NI seat — uu share unchanged
    expect(projected.votes.uu).toBeCloseTo(15000, 0);
  });

  it('normalizes projected votes to totalVotes when swings push shares over 100%', () => {
    // UUP base 60%, SF base 40% in this NI seat → both modelled
    // Apply +60pp swing to UUP and -20pp to SF → projected: UUP 120%, SF 20% (sum 140%)
    // After normalization total votes should still equal 25000
    const niSeat = {
      seat: 'Fermanagh South Tyrone', region: 'northernireland', winner: 'uu', turnout: 0,
      votes: { uu: 15000, sinnfein: 10000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('uu', new Map([['northernireland', 60]]));
    swingsByParty.set('sinnfein', new Map([['northernireland', -20]]));
    const projected = projectedSeatForPredictMode(niSeat, swingsByParty);
    const totalProjected = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(totalProjected).toBeCloseTo(25000, 0);
    expect(projected.turnout).toBe(25000);
    expect(projected.winner).toBe('uu');
    Object.values(projected.votes).forEach((v) => {
      expect((Number(v) / projected.turnout) * 100).toBeLessThanOrEqual(100);
    });
  });

  // ── Scotland / Wales / NI region-specific tests ───────────────────────────

  it('applies SNP swing in a Scotland seat and changes winner', () => {
    // SNP base 37.5% (15k/40k), Labour base 50% (20k/40k)
    // +20pp SNP swing → SNP 57.5% > Labour 50% → SNP wins
    const seat = {
      seat: 'Edinburgh East', region: 'scotland', winner: 'labour', turnout: 0,
      votes: { labour: 20000, snp: 15000, conservative: 5000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('snp', new Map([['scotland', 20]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    expect(projected.winner).toBe('snp');
    expect(projected.votes.snp).toBeGreaterThan(projected.votes.labour);
    const total = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(total).toBeCloseTo(40000, 0);
  });

  it('applies Plaid Cymru swing in a Wales seat and changes winner', () => {
    // Plaid base 50% (15k/30k), Labour base 33.3% (10k/30k)
    // -30pp Plaid swing → Plaid 20%, Labour 33.3% → Labour wins
    const seat = {
      seat: 'Ceredigion Preseli', region: 'wales', winner: 'plaidcymru', turnout: 0,
      votes: { plaidcymru: 15000, labour: 10000, libdems: 5000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('plaidcymru', new Map([['wales', -30]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    expect(projected.winner).toBe('labour');
    const total = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(total).toBeCloseTo(30000, 0);
  });

  it('does not apply england-level swing to a Scotland seat', () => {
    const seat = {
      seat: 'Glasgow East', region: 'scotland', winner: 'labour', turnout: 0,
      votes: { labour: 20000, snp: 15000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('snp', new Map([['england', 30]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    // england fallback only applies to English sub-regions — SNP unchanged
    expect(projected.votes.snp).toBeCloseTo(15000, 0);
    expect(projected.winner).toBe('labour');
  });

  it('applies england-level swing as fallback for an English sub-region', () => {
    // Conservative base 22.2% (10k/45k) in Northwest seat
    // +20pp via england aggregate → Conservative 42.2% → more votes
    const seat = {
      seat: 'Manchester Central', region: 'northwest', winner: 'labour', turnout: 0,
      votes: { labour: 30000, conservative: 10000, libdems: 5000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('conservative', new Map([['england', 20]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    expect(projected.votes.conservative).toBeGreaterThan(10000);
    const total = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(total).toBeCloseTo(45000, 0);
  });

  it('handles NI multi-party mixed swings (DUP down, Alliance up)', () => {
    // DUP base 40% (16k), SF base 35% (14k), Alliance base 15% (6k), SDLP 10% (4k)
    // DUP -10pp → 30%, Alliance +15pp → 30% — turnout preserved
    const seat = {
      seat: 'Belfast North', region: 'northernireland', winner: 'dup', turnout: 0,
      votes: { dup: 16000, sinnfein: 14000, alliance: 6000, sdlp: 4000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('dup', new Map([['northernireland', -10]]));
    swingsByParty.set('alliance', new Map([['northernireland', 15]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    expect(projected.votes.dup).toBeLessThan(16000);
    expect(projected.votes.alliance).toBeGreaterThan(6000);
    const total = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(total).toBeCloseTo(40000, 0);
  });

  it('redistributes freed share to non-modelled parties when modelled parties shrink', () => {
    // Labour 50% (30k), Conservative 33.3% (20k), Independent 16.7% (10k) — total 60k
    // Independent is NOT in PREDICT_MODELLED_PARTY_KEYS
    // Labour -10pp → adjustedTrackedShareSum drops → adjustedOtherShare grows → Independent gains
    const seat = {
      seat: 'Hereford', region: 'westmidlands', winner: 'labour', turnout: 0,
      votes: { labour: 30000, conservative: 20000, independent_joe: 10000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('labour', new Map([['westmidlands', -10]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    expect(projected.votes.independent_joe).toBeGreaterThan(10000);
    const total = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(total).toBeCloseTo(60000, 0);
  });

  it('assigns zero to non-modelled parties when modelled shares exceed 100%', () => {
    // Labour 50% (30k), Conservative 33.3% (20k), Independent 16.7% (10k) — total 60k
    // Labour +40pp → adjusted modelled sum > 100% → adjustedOtherShare clamped to 0
    // Independent should get no votes
    const seat = {
      seat: 'Birmingham Hall Green', region: 'westmidlands', winner: 'labour', turnout: 0,
      votes: { labour: 30000, conservative: 20000, independent_ali: 10000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('labour', new Map([['westmidlands', 40]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    expect(projected.votes.independent_ali ?? 0).toBe(0);
    expect(projected.winner).toBe('labour');
    const total = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(total).toBeCloseTo(60000, 0);
  });

  it('normalizes correctly when multiple GB parties all receive large positive swings', () => {
    // England seat: Labour 30% (15k), Conservative 40% (20k), LibDems 20% (10k), Reform 10% (5k) — total 50k
    // Conservative +40pp, Labour +30pp → projected modelled sum > 100%
    // All vote % must remain ≤ 100% after normalization
    const seat = {
      seat: 'Swindon North', region: 'southwestengland', winner: 'conservative', turnout: 0,
      votes: { labour: 15000, conservative: 20000, libdems: 10000, reform: 5000 },
    };
    const swingsByParty = new Map();
    swingsByParty.set('conservative', new Map([['southwestengland', 40]]));
    swingsByParty.set('labour', new Map([['southwestengland', 30]]));
    const projected = projectedSeatForPredictMode(seat, swingsByParty);
    const total = Object.values(projected.votes).reduce((s, v) => s + Number(v || 0), 0);
    expect(total).toBeCloseTo(50000, 0);
    Object.values(projected.votes).forEach((v) => {
      expect((Number(v) / projected.turnout) * 100).toBeLessThanOrEqual(100);
    });
  });

  // ── Exact vote arithmetic ─────────────────────────────────────────────────
  // Seat used throughout: Labour 60% (30k), Conservative 30% (15k), Reform 10% (5k) — total 50k

  describe('exact vote arithmetic', () => {
    const seat = {
      seat: 'Bristol West', region: 'southwestengland', winner: 'labour', turnout: 0,
      votes: { labour: 30000, conservative: 15000, reform: 5000 },
    };

    it('computes exact Labour vote reduction for a -10pp swing', () => {
      // Labour 60% -10pp → 50% → 25000 votes
      const swingsByParty = new Map([['labour', new Map([['southwestengland', -10]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.labour).toBeCloseTo(25000, 1);
    });

    it('computes exact Conservative vote increase for a +10pp swing (paired with Labour -10pp to keep sum at 100%)', () => {
      // Conservative 30% +10pp → 40% → 20000 votes; Labour -10pp keeps total modelled share at 100%
      const swingsByParty = new Map([
        ['conservative', new Map([['southwestengland', 10]])],
        ['labour', new Map([['southwestengland', -10]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.conservative).toBeCloseTo(20000, 1);
    });

    it('applies simultaneous swings to two parties independently', () => {
      // Labour -5pp → 55% (27500), Reform +5pp → 15% (7500)
      // adjustedTrackedShareSum = 55+30+15 = 100% → no normalization
      const swingsByParty = new Map([
        ['labour', new Map([['southwestengland', -5]])],
        ['reform', new Map([['southwestengland', 5]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.labour).toBeCloseTo(27500, 1);
      expect(projected.votes.reform).toBeCloseTo(7500, 1);
      expect(projected.votes.conservative).toBeCloseTo(15000, 1);
    });

    it('applies a sub-1pp fractional swing correctly', () => {
      // Labour 60% -0.5pp → 59.5% → 29750 votes; total modelled = 99.5% → no normalization
      const swingsByParty = new Map([['labour', new Map([['southwestengland', -0.5]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.labour).toBeCloseTo(29750, 0);
    });

    it('produces an "others" bucket for freed share when all parties are modelled', () => {
      // Labour 60% -10pp → 50%; all three parties modelled; freed 10% → others
      // adjustedTrackedShareSum = 50+30+10 = 90%, no non-tracked → others = 5000
      const swingsByParty = new Map([['labour', new Map([['southwestengland', -10]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.others).toBeCloseTo(5000, 1);
    });

    it('total projected votes always equals totalVotes (no swing)', () => {
      const projected = projectedSeatForPredictMode(seat, new Map());
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
    });

    it('total projected votes always equals totalVotes (with swing under 100%)', () => {
      const swingsByParty = new Map([['labour', new Map([['southwestengland', -15]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
    });

    it('total projected votes always equals totalVotes (with swing over 100%)', () => {
      const swingsByParty = new Map([['conservative', new Map([['southwestengland', 50]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
    });
  });

  // ── Winner determination ──────────────────────────────────────────────────

  describe('winner determination', () => {
    const seat = {
      seat: 'Bristol West', region: 'southwestengland', winner: 'labour', turnout: 0,
      votes: { labour: 30000, conservative: 15000, reform: 5000 },
    };

    it('winner stays Labour with a small Conservative swing that does not overtake', () => {
      // Conservative +20pp → 50%, Labour 60% — Labour still leads
      const swingsByParty = new Map([['conservative', new Map([['southwestengland', 20]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.winner).toBe('labour');
    });

    it('winner flips to Conservative when swing is large enough to overtake (over-100% case)', () => {
      // Conservative +35pp → 65%, Labour stays 60%; modelled sum > 100% so normalize
      // After normalization Conservative (32500) still > Labour (30000) pre-scale,
      // so Conservative wins regardless: 32500/67500 > 30000/67500
      const swingsByParty = new Map([['conservative', new Map([['southwestengland', 35]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.winner).toBe('conservative');
    });

    it('winner flips to Reform when it gets a very large positive swing', () => {
      // Reform +65pp → 75%, Labour 60%, Conservative 30%; sum > 100% → normalize
      // Reform projected = 37500, Labour = 30000 → Reform wins
      const swingsByParty = new Map([['reform', new Map([['southwestengland', 65]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.winner).toBe('reform');
    });

    it('winner becomes "others" when all modelled parties are clamped to 0 and no non-tracked parties exist', () => {
      const swingsByParty = new Map([
        ['labour', new Map([['southwestengland', -100]])],
        ['conservative', new Map([['southwestengland', -100]])],
        ['reform', new Map([['southwestengland', -100]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.winner).toBe('others');
    });

    it('preserves winner from baseSeat when all parties produce zero projected votes (empty projectedVotes)', () => {
      // Contrived: pass a seat with 0 votes so we hit the early return
      const emptySeat = { seat: 'X', region: 'southwestengland', winner: 'labour', turnout: 0, votes: {} };
      const projected = projectedSeatForPredictMode(emptySeat, new Map());
      expect(projected.winner).toBe('labour');
    });
  });

  // ── Non-modelled party redistribution ────────────────────────────────────

  describe('non-modelled party redistribution', () => {
    it('single non-modelled party receives freed share exactly', () => {
      // Labour 50% (25k), Conservative 20% (10k), ukip 30% (15k) — total 50k
      // ukip is non-modelled; Labour -10pp → adjustedTrackedShareSum=60%, adjustedOtherShare=40%
      // ukip gets 40% of 50k = 20000
      const seat = {
        seat: 'Clacton', region: 'eastofengland', winner: 'ukip', turnout: 0,
        votes: { labour: 25000, conservative: 10000, ukip: 15000 },
      };
      const swingsByParty = new Map([['labour', new Map([['eastofengland', -10]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.ukip).toBeCloseTo(20000, 1);
    });

    it('two non-modelled parties share freed votes in proportion to their baseline', () => {
      // Labour 50% (25k), Conservative 20% (10k), ukip 20% (10k), ind 10% (5k) — total 50k
      // Labour -10pp → adjustedTrackedShareSum=60%, adjustedOtherShare=40%
      // nonTrackedVotes = 15k; ukip weight=2/3, ind weight=1/3
      // ukip gets (40*2/3)% * 50k = 13333.3, ind gets (40*1/3)% * 50k = 6666.7
      const seat = {
        seat: 'Thurrock', region: 'eastofengland', winner: 'labour', turnout: 0,
        votes: { labour: 25000, conservative: 10000, ukip: 10000, ind: 5000 },
      };
      const swingsByParty = new Map([['labour', new Map([['eastofengland', -10]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.ukip).toBeCloseTo(13333, 0);
      expect(projected.votes.ind).toBeCloseTo(6667, 0);
      // Proportional ratio preserved
      expect(projected.votes.ukip / projected.votes.ind).toBeCloseTo(2, 2);
    });

    it('non-modelled party with zero baseline is not weighted — freed share goes to synthetic others instead', () => {
      // Labour 50% (25k), Conservative 50% (25k), ind 0 — total 50k
      // Labour -10pp; adjustedOtherShare=10%; nonTrackedVotes=0 → projectedVotes.others created
      const seat = {
        seat: 'X', region: 'eastofengland', winner: 'conservative', turnout: 0,
        votes: { labour: 25000, conservative: 25000, ind: 0 },
      };
      const swingsByParty = new Map([['labour', new Map([['eastofengland', -10]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.ind ?? 0).toBe(0);
      expect(projected.votes.others).toBeCloseTo(5000, 1);
    });

    it('non-modelled party wins seat when all modelled parties are clamped to zero', () => {
      // Labour 50% (25k), Conservative 30% (15k), ukip 20% (10k) — total 50k
      // Both modelled → 0; adjustedOtherShare=100%; ukip gets all 50k
      const seat = {
        seat: 'Clacton', region: 'eastofengland', winner: 'labour', turnout: 0,
        votes: { labour: 25000, conservative: 15000, ukip: 10000 },
      };
      const swingsByParty = new Map([
        ['labour', new Map([['eastofengland', -100]])],
        ['conservative', new Map([['eastofengland', -100]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.ukip).toBeCloseTo(50000, 0);
      expect(projected.winner).toBe('ukip');
    });

    it('non-modelled parties get zero votes when modelled shares exceed 100%', () => {
      // adjustedOtherShare is clamped to 0; non-tracked parties are not added to projectedVotes
      const seat = {
        seat: 'X', region: 'eastofengland', winner: 'labour', turnout: 0,
        votes: { labour: 25000, conservative: 15000, ukip: 10000 },
      };
      const swingsByParty = new Map([['labour', new Map([['eastofengland', 50]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // Labour 50%+50=100%, Conservative 30%; sum=130% > 100% → adjustedOtherShare=0
      expect(projected.votes.ukip ?? 0).toBe(0);
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
    });
  });

  // ── Region swing fallback logic ───────────────────────────────────────────

  describe('region swing fallback', () => {
    it('direct sub-region swing takes precedence over england aggregate', () => {
      // Northwest-specific Labour swing = +20pp; england aggregate = -30pp
      // Direct entry should win → Labour gains votes
      const seat = {
        seat: 'Manchester Gorton', region: 'northwest', winner: 'labour', turnout: 0,
        votes: { labour: 30000, conservative: 15000, reform: 5000 },
      };
      const swingsByParty = new Map([
        ['labour', new Map([['northwest', 20], ['england', -30]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // Labour should increase (direct +20pp used, not england -30pp)
      expect(projected.votes.labour).toBeGreaterThan(30000);
    });

    it('england aggregate is used when no direct sub-region entry exists', () => {
      const seat = {
        seat: 'Manchester Gorton', region: 'northwest', winner: 'labour', turnout: 0,
        votes: { labour: 30000, conservative: 15000, reform: 5000 },
      };
      const swingsByParty = new Map([
        ['labour', new Map([['england', -20]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // Labour 60% -20pp → 40% → 20000
      expect(projected.votes.labour).toBeCloseTo(20000, 1);
    });

    it('Wales seat does not use england aggregate swing', () => {
      const seat = {
        seat: 'Cardiff Central', region: 'wales', winner: 'labour', turnout: 0,
        votes: { labour: 20000, conservative: 15000, plaidcymru: 15000 },
      };
      const swingsByParty = new Map([
        ['labour', new Map([['england', -20]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // Wales is not English → england fallback ignored → Labour unchanged
      expect(projected.votes.labour).toBeCloseTo(20000, 1);
    });

    it('Scotland seat does not use england aggregate swing', () => {
      const seat = {
        seat: 'Dundee East', region: 'scotland', winner: 'snp', turnout: 0,
        votes: { snp: 25000, labour: 15000, conservative: 10000 },
      };
      const swingsByParty = new Map([
        ['snp', new Map([['england', 30]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // Scotland is not English → england fallback ignored → SNP unchanged
      expect(projected.votes.snp).toBeCloseTo(25000, 1);
    });

    it('NI seat does not use england aggregate swing for a GB party', () => {
      const seat = {
        seat: 'Belfast East', region: 'northernireland', winner: 'dup', turnout: 0,
        votes: { dup: 20000, alliance: 15000, sinnfein: 10000 },
      };
      const swingsByParty = new Map([
        ['alliance', new Map([['england', 25]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // NI is not English → england fallback ignored → Alliance unchanged
      expect(projected.votes.alliance).toBeCloseTo(15000, 1);
    });

    it('party absent from swingsByParty gets zero swing', () => {
      // Labour has no entry at all; Conservative -10pp (negative keeps modelled sum ≤ 100%)
      const seat = {
        seat: 'Bath', region: 'southwestengland', winner: 'libdems', turnout: 0,
        votes: { libdems: 25000, labour: 15000, conservative: 10000 },
      };
      const swingsByParty = new Map([
        ['conservative', new Map([['southwestengland', -10]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // Labour has no swing entry → its votes should be exactly unchanged
      expect(projected.votes.labour).toBeCloseTo(15000, 1);
    });

    it('direct swing of exactly 0 falls through to england aggregate', () => {
      // A manually set zero entry (unusual but possible in tests) should not count as a direct match
      // because resolvedSwingValue uses Math.abs(direct) > 1e-9 as the guard
      const seat = {
        seat: 'Norwich North', region: 'eastofengland', winner: 'labour', turnout: 0,
        votes: { labour: 30000, conservative: 15000, reform: 5000 },
      };
      const swingsByParty = new Map([
        ['labour', new Map([['eastofengland', 0], ['england', -10]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      // Direct 0 is ignored; england -10pp used → Labour 60%-10 = 50% → 25000
      expect(projected.votes.labour).toBeCloseTo(25000, 1);
    });
  });

  // ── Normalization when shares exceed 100% ────────────────────────────────

  describe('normalization when modelled shares exceed 100%', () => {
    const seat = {
      seat: 'Bristol West', region: 'southwestengland', winner: 'labour', turnout: 0,
      votes: { labour: 30000, conservative: 15000, reform: 5000 },
    };

    it('does not alter votes when sum is exactly 100%', () => {
      // No swings → no normalization path triggered
      const projected = projectedSeatForPredictMode(seat, new Map());
      expect(projected.votes.labour).toBeCloseTo(30000, 1);
      expect(projected.votes.conservative).toBeCloseTo(15000, 1);
      expect(projected.votes.reform).toBeCloseTo(5000, 1);
    });

    it('normalizes correctly for a sum just over 100% (101%)', () => {
      // Conservative +1pp → 31%; sum of modelled = 60+31+10 = 101%
      // scale = 50000/50500 ≈ 0.99010
      const swingsByParty = new Map([['conservative', new Map([['southwestengland', 1]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
      expect(projected.winner).toBe('labour');
    });

    it('normalizes correctly for a sum far over 100% (200%)', () => {
      // Conservative +70pp → 100%; Labour 60%, Reform 10% → sum = 170%
      const swingsByParty = new Map([['conservative', new Map([['southwestengland', 70]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
    });

    it('winner order is preserved after normalization', () => {
      // Conservative +35pp → 65% vs Labour 60% → Conservative wins pre- and post-normalization
      const swingsByParty = new Map([['conservative', new Map([['southwestengland', 35]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.winner).toBe('conservative');
      expect(projected.votes.conservative).toBeGreaterThan(projected.votes.labour);
    });

    it('all per-party vote percentages stay ≤ 100% after normalization', () => {
      // All parties get large positive swings
      const swingsByParty = new Map([
        ['labour', new Map([['southwestengland', 50]])],
        ['conservative', new Map([['southwestengland', 50]])],
        ['reform', new Map([['southwestengland', 50]])],
      ]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      Object.values(projected.votes).forEach((v) => {
        expect((Number(v) / projected.turnout) * 100).toBeLessThanOrEqual(100);
      });
    });
  });

  // ── Modelled party with zero baseline ────────────────────────────────────

  describe('modelled party with zero baseline in the seat', () => {
    it('a modelled party absent from the baseline gains votes when given a positive swing', () => {
      // Reform is not in this Scotland seat — baseline share = 0%
      // Reform +20pp → projected Reform share = 20%, causes over-100% → normalized
      const seat = {
        seat: 'Edinburgh North', region: 'scotland', winner: 'labour', turnout: 0,
        votes: { labour: 30000, snp: 20000 },
      };
      const swingsByParty = new Map([['reform', new Map([['scotland', 20]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(Number(projected.votes.reform || 0)).toBeGreaterThan(0);
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
    });

    it('a modelled party absent from the baseline stays at zero with a negative swing', () => {
      // SNP not in this England seat; SNP -20pp → adjusted = max(0, 0-20) = 0 → no votes
      const seat = {
        seat: 'Bristol West', region: 'southwestengland', winner: 'labour', turnout: 0,
        votes: { labour: 30000, conservative: 15000, reform: 5000 },
      };
      const swingsByParty = new Map([['snp', new Map([['southwestengland', -20]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(projected.votes.snp ?? 0).toBe(0);
    });

    it('Green party absent from a seat gains votes proportionally when given a positive swing', () => {
      const seat = {
        seat: 'Cheltenham', region: 'southwestengland', winner: 'libdems', turnout: 0,
        votes: { libdems: 25000, labour: 15000, conservative: 10000 },
      };
      const swingsByParty = new Map([['green', new Map([['southwestengland', 15]])]]);
      const projected = projectedSeatForPredictMode(seat, swingsByParty);
      expect(Number(projected.votes.green || 0)).toBeGreaterThan(0);
      const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
      expect(total).toBeCloseTo(50000, 0);
    });
  });

  // ── Turnout invariant across scenarios ───────────────────────────────────

  describe('turnout invariant', () => {
    const seat = {
      seat: 'Bristol West', region: 'southwestengland', winner: 'labour', turnout: 0,
      votes: { labour: 30000, conservative: 15000, reform: 5000 },
    };

    const scenarios = [
      ['no swings', new Map()],
      ['small swing', new Map([['labour', new Map([['southwestengland', -5]])]])],
      ['large swing under 100%', new Map([['labour', new Map([['southwestengland', -30]])]])],
      ['swing that exceeds 100%', new Map([['conservative', new Map([['southwestengland', 60]])]])],
      ['all parties clamped to 0', new Map([
        ['labour', new Map([['southwestengland', -100]])],
        ['conservative', new Map([['southwestengland', -100]])],
        ['reform', new Map([['southwestengland', -100]])],
      ])],
    ];

    scenarios.forEach(([label, swingsByParty]) => {
      it(`total projected votes equals totalVotes for: ${label}`, () => {
        const projected = projectedSeatForPredictMode(seat, swingsByParty);
        const total = Object.values(projected.votes).reduce((s, v) => s + Number(v), 0);
        expect(total).toBeCloseTo(50000, 0);
        expect(projected.turnout).toBe(50000);
      });
    });
  });
});

// ── seatNameFromFeature ───────────────────────────────────────────────────────

describe('seatNameFromFeature', () => {
  it('returns name property when present', () => {
    expect(seatNameFromFeature({ properties: { name: 'Oxford East' } })).toBe('Oxford East');
  });

  it('falls back to seat_name when name is absent', () => {
    expect(seatNameFromFeature({ properties: { seat_name: 'Oxford East' } })).toBe('Oxford East');
  });

  it('falls back to seat then constituency then Name in order', () => {
    expect(seatNameFromFeature({ properties: { seat: 'Oxford East' } })).toBe('Oxford East');
    expect(seatNameFromFeature({ properties: { constituency: 'Oxford East' } })).toBe('Oxford East');
    expect(seatNameFromFeature({ properties: { Name: 'Oxford East' } })).toBe('Oxford East');
  });

  it('prefers name over all other properties', () => {
    expect(seatNameFromFeature({
      properties: { name: 'Primary', seat_name: 'Secondary', constituency: 'Tertiary' },
    })).toBe('Primary');
  });

  it('returns null when no known property exists', () => {
    expect(seatNameFromFeature({ properties: { unknown: 'foo' } })).toBeNull();
  });

  it('returns null for null input', () => {
    expect(seatNameFromFeature(null)).toBeNull();
  });

  it('returns null for missing properties object', () => {
    expect(seatNameFromFeature({})).toBeNull();
  });
});

// ── buildWinnerBySeat ─────────────────────────────────────────────────────────

describe('buildWinnerBySeat', () => {
  it('maps seat name to winner party key', () => {
    const map = buildWinnerBySeat([{ seat: 'Oxford East', winner: 'labour' }]);
    expect(map.get('Oxford East')).toBe('labour');
  });

  it('also stores a lowercase variant of the seat name', () => {
    const map = buildWinnerBySeat([{ seat: 'Oxford East', winner: 'labour' }]);
    expect(map.get('oxford east')).toBe('labour');
  });

  it('defaults winner to others when winner field is missing', () => {
    const map = buildWinnerBySeat([{ seat: 'Oxford East' }]);
    expect(map.get('Oxford East')).toBe('others');
  });

  it('skips seats without a seat property', () => {
    const map = buildWinnerBySeat([{ winner: 'labour' }]);
    expect(map.size).toBe(0);
  });

  it('handles multiple seats', () => {
    const map = buildWinnerBySeat([
      { seat: 'Oxford East', winner: 'labour' },
      { seat: 'Windsor', winner: 'conservative' },
    ]);
    expect(map.get('Windsor')).toBe('conservative');
    expect(map.get('windsor')).toBe('conservative');
    expect(map.size).toBe(4);
  });

  it('returns an empty Map for an empty array', () => {
    expect(buildWinnerBySeat([]).size).toBe(0);
  });
});

// ── cloneSeatRecord ───────────────────────────────────────────────────────────

describe('cloneSeatRecord', () => {
  const seat = {
    seat: 'Oxford East',
    region: 'SouthEastEngland',
    winner: 'labour',
    electorate: 72000,
    turnout: 48000,
    votes: { labour: 22000, conservative: 15000, libdems: 8000 },
  };

  it('returns a new object, not the same reference', () => {
    expect(cloneSeatRecord(seat)).not.toBe(seat);
  });

  it('preserves seat, region, electorate, and turnout', () => {
    const clone = cloneSeatRecord(seat);
    expect(clone.seat).toBe('Oxford East');
    expect(clone.region).toBe('SouthEastEngland');
    expect(clone.electorate).toBe(72000);
    expect(clone.turnout).toBe(48000);
  });

  it('normalizes the winner via normalizePartyKey', () => {
    const clone = cloneSeatRecord({ ...seat, winner: 'Labour' });
    expect(clone.winner).toBe('labour');
  });

  it('filters out zero-vote entries', () => {
    const clone = cloneSeatRecord({ ...seat, votes: { labour: 22000, conservative: 0 } });
    expect(clone.votes.conservative).toBeUndefined();
    expect(clone.votes.labour).toBe(22000);
  });

  it('filters out negative-vote entries', () => {
    const clone = cloneSeatRecord({ ...seat, votes: { labour: 22000, conservative: -1 } });
    expect(clone.votes.conservative).toBeUndefined();
  });

  it('normalizes party keys in votes', () => {
    const clone = cloneSeatRecord({ ...seat, votes: { Labour: 22000 } });
    expect(clone.votes.labour).toBe(22000);
    expect(clone.votes.Labour).toBeUndefined();
  });

  it('sums duplicate keys that collapse after normalisation', () => {
    // 'reformuk' normalises to 'reform'; both entries should be summed
    const clone = cloneSeatRecord({ ...seat, votes: { reform: 5000, reformuk: 3000 } });
    expect(clone.votes.reform).toBe(8000);
  });

  it('falls back to defaults for null input', () => {
    const clone = cloneSeatRecord(null);
    expect(clone.seat).toBe('Unknown seat');
    expect(clone.region).toBe('unknown');
    expect(clone.winner).toBe('others');
    expect(clone.electorate).toBe(0);
    expect(clone.turnout).toBe(0);
    expect(clone.votes).toEqual({});
  });
});

// ── pollTrackerDateLabel ──────────────────────────────────────────────────────

describe('pollTrackerDateLabel', () => {
  it('extracts YYYY-MM-DD date from election name', () => {
    expect(pollTrackerDateLabel('General Election 2024-07-04', 99)).toBe('2024-07-04');
  });

  it('extracts date when embedded in UNS label', () => {
    expect(pollTrackerDateLabel('UNS 2024-07-04 model', 99)).toBe('2024-07-04');
  });

  it('returns the trimmed name when no date is present', () => {
    expect(pollTrackerDateLabel('  General Election  ', 99)).toBe('General Election');
  });

  it('returns stringified fallbackId when name is empty', () => {
    expect(pollTrackerDateLabel('', 42)).toBe('42');
  });

  it('returns stringified fallbackId when name is null', () => {
    expect(pollTrackerDateLabel(null, 7)).toBe('7');
  });

  it('returns the date even when other numbers appear in the name', () => {
    expect(pollTrackerDateLabel('2019 Election 2024-07-04', 0)).toBe('2024-07-04');
  });
});

// ── encodePredictPayload / decodePredictPayload ───────────────────────────────

describe('encodePredictPayload / decodePredictPayload', () => {
  // Minimal slot list covering two regions and two parties each
  const slots = [
    ['england', 'labour'],
    ['england', 'conservative'],
    ['scotland', 'labour'],
    ['scotland', 'snp'],
  ];

  it('returns empty string when no rows and englandExpanded is false', () => {
    expect(encodePredictPayload([], false, slots)).toBe('');
  });

  it('encodes englandExpanded: true even with no changed rows', () => {
    const encoded = encodePredictPayload([], true, slots);
    expect(encoded).toMatch(/^2\.1\./);
  });

  it('round-trips a single changed row', () => {
    const rows = [['england', 'labour', 45]];
    const encoded = encodePredictPayload(rows, false, slots);
    const decoded = decodePredictPayload(encoded, slots);
    expect(decoded).not.toBeNull();
    expect(decoded.englandExpanded).toBe(false);
    expect(decoded.rows).toEqual([['england', 'labour', 45]]);
  });

  it('round-trips multiple rows and preserves englandExpanded', () => {
    const rows = [
      ['england', 'labour', 38],
      ['scotland', 'snp', 42],
    ];
    const encoded = encodePredictPayload(rows, true, slots);
    const decoded = decodePredictPayload(encoded, slots);
    expect(decoded.englandExpanded).toBe(true);
    expect(decoded.rows).toEqual(expect.arrayContaining([
      ['england', 'labour', 38],
      ['scotland', 'snp', 42],
    ]));
    expect(decoded.rows).toHaveLength(2);
  });

  it('returns null for malformed input', () => {
    expect(decodePredictPayload('garbage', slots)).toBeNull();
    expect(decodePredictPayload('', slots)).toBeNull();
    expect(decodePredictPayload(null, slots)).toBeNull();
    expect(decodePredictPayload('1.0.', slots)).toBeNull();
  });

  it('returns { englandExpanded, rows: [] } for a payload with no entries', () => {
    const decoded = decodePredictPayload('2.0.', slots);
    expect(decoded).toEqual({ englandExpanded: false, rows: [] });
  });

  it('ignores row entries with out-of-range slot indices', () => {
    // slot index 'zz' in base-36 is way beyond our 4-slot list
    const decoded = decodePredictPayload('2.0.zz-a', slots);
    expect(decoded).not.toBeNull();
    expect(decoded.rows).toHaveLength(0);
  });

  it('ignores row entries for unknown region/party pairs', () => {
    // slot index 0 is england::labour — passing an unknown key in serializedRows skips it
    const rows = [['unknown', 'party', 50]];
    const encoded = encodePredictPayload(rows, false, slots);
    expect(encoded).toBe('');
  });

  it('ignores values outside [0, 100] during encoding', () => {
    const rows = [['england', 'labour', 150]];
    const encoded = encodePredictPayload(rows, false, slots);
    expect(encoded).toBe('');
  });

  it('returns empty string when slots array is empty', () => {
    expect(encodePredictPayload([['england', 'labour', 40]], false, [])).toBe('');
  });

  it('returns null when decoding with an empty slots array', () => {
    const encoded = encodePredictPayload([['england', 'labour', 40]], false, slots);
    expect(decodePredictPayload(encoded, [])).toBeNull();
  });
});

// ── buildRegionLabelLookup ────────────────────────────────────────────────────

describe('buildRegionLabelLookup', () => {
  const regionsByMapId = {
    'uk2024': [
      { name: 'South East England' },
      { name: 'North West England' },
      { name: 'Scotland' },
    ],
  };

  it('returns a Map from normalised region key to display label', () => {
    const map = buildRegionLabelLookup('uk2024', regionsByMapId);
    expect(map.get('southeastengland')).toBe('South East England');
    expect(map.get('scotland')).toBe('Scotland');
  });

  it('covers all provided regions', () => {
    const map = buildRegionLabelLookup('uk2024', regionsByMapId);
    expect(map.size).toBe(3);
  });

  it('returns an empty Map for an unknown mapId', () => {
    const map = buildRegionLabelLookup('unknown', regionsByMapId);
    expect(map.size).toBe(0);
  });

  it('returns an empty Map when regionsByMapId is null', () => {
    expect(buildRegionLabelLookup('uk2024', null).size).toBe(0);
  });

  it('skips regions whose name normalises to an empty string', () => {
    const sparse = { 'x': [{ name: '' }, { name: 'London' }] };
    const map = buildRegionLabelLookup('x', sparse);
    expect(map.size).toBe(1);
    expect(map.get('london')).toBe('London');
  });
});

// ── seatMatchesPrimaryFilters ─────────────────────────────────────────────────

describe('seatMatchesPrimaryFilters', () => {
  const seat = {
    seat: 'Oxford East',
    region: 'South East England',
    winner: 'labour',
    votes: { labour: 22000, conservative: 15000, libdems: 8000 },
  };
  const comparisonSeat = {
    seat: 'Oxford East',
    region: 'South East England',
    winner: 'conservative',
    votes: { labour: 14000, conservative: 20000, libdems: 8000 },
  };
  const openFilters = {
    filterParty: 'all',
    filterRegion: 'all',
    majorityMin: 0,
    majorityMax: 100,
    filterSecondParty: 'all',
    gainsOnly: false,
  };

  it('returns true when all filters are open', () => {
    expect(seatMatchesPrimaryFilters(seat, null, openFilters, null)).toBe(true);
  });

  it('filters by winner party', () => {
    const f = { ...openFilters, filterParty: 'labour' };
    expect(seatMatchesPrimaryFilters(seat, null, f, null)).toBe(true);
    expect(seatMatchesPrimaryFilters({ ...seat, winner: 'conservative' }, null, f, null)).toBe(false);
  });

  it('normalises "other" winner to "others" for party filter', () => {
    const f = { ...openFilters, filterParty: 'others' };
    expect(seatMatchesPrimaryFilters({ ...seat, winner: 'other' }, null, f, null)).toBe(true);
  });

  it('filters by region', () => {
    const f = { ...openFilters, filterRegion: 'southeastengland' };
    expect(seatMatchesPrimaryFilters(seat, null, f, null)).toBe(true);
    expect(seatMatchesPrimaryFilters({ ...seat, region: 'Scotland' }, null, f, null)).toBe(false);
  });

  it('filters by majority range', () => {
    // labour majority: (22000-15000)/45000 ≈ 15.6%
    expect(seatMatchesPrimaryFilters(seat, null, { ...openFilters, majorityMin: 10, majorityMax: 20 }, null)).toBe(true);
    expect(seatMatchesPrimaryFilters(seat, null, { ...openFilters, majorityMin: 20, majorityMax: 50 }, null)).toBe(false);
  });

  it('filters by second party', () => {
    const f = { ...openFilters, filterSecondParty: 'conservative' };
    expect(seatMatchesPrimaryFilters(seat, null, f, null)).toBe(true);
    expect(seatMatchesPrimaryFilters(seat, null, { ...openFilters, filterSecondParty: 'libdems' }, null)).toBe(false);
  });

  it('gainsOnly: uses byElectionSeats Set when provided', () => {
    const f = { ...openFilters, gainsOnly: true };
    const byElection = new Set(['Oxford East']);
    expect(seatMatchesPrimaryFilters(seat, null, f, byElection)).toBe(true);
    expect(seatMatchesPrimaryFilters({ ...seat, seat: 'Windsor' }, null, f, byElection)).toBe(false);
  });

  it('gainsOnly: falls back to seatGainFromPartyKey when byElectionSeats is null', () => {
    const f = { ...openFilters, gainsOnly: true };
    // seat changed hands (labour won, comparison shows conservative won) → gain
    expect(seatMatchesPrimaryFilters(seat, comparisonSeat, f, null)).toBe(true);
    // seat unchanged (labour won both times) → not a gain
    const sameWinner = { ...comparisonSeat, winner: 'labour' };
    expect(seatMatchesPrimaryFilters(seat, sameWinner, f, null)).toBe(false);
  });
});

// ── buildVisibleSeatKeySet ────────────────────────────────────────────────────

describe('buildVisibleSeatKeySet', () => {
  const seats = [
    { seat: 'Oxford East', region: 'southeastengland', winner: 'labour', votes: { labour: 22000, conservative: 15000 } },
    { seat: 'Windsor', region: 'southeastengland', winner: 'conservative', votes: { conservative: 20000, labour: 10000 } },
    { seat: 'Edinburgh North', region: 'scotland', winner: 'snp', votes: { snp: 18000, labour: 9000 } },
  ];
  const comparisonMap = new Map();
  const openFilters = {
    filterParty: 'all', filterRegion: 'all',
    majorityMin: 0, majorityMax: 100,
    filterSecondParty: 'all', gainsOnly: false,
  };

  it('returns all seat keys when all filters are open', () => {
    const keys = buildVisibleSeatKeySet(seats, comparisonMap, openFilters, null);
    expect(keys.size).toBe(3);
  });

  it('returns only matching seat keys when a party filter is active', () => {
    const f = { ...openFilters, filterParty: 'labour' };
    const keys = buildVisibleSeatKeySet(seats, comparisonMap, f, null);
    expect(keys.size).toBe(1);
    expect(keys.has('oxford east')).toBe(true);
  });

  it('returns only matching seat keys when a region filter is active', () => {
    const f = { ...openFilters, filterRegion: 'scotland' };
    const keys = buildVisibleSeatKeySet(seats, comparisonMap, f, null);
    expect(keys.size).toBe(1);
    expect(keys.has('edinburgh north')).toBe(true);
  });

  it('returns an empty Set when no seats match', () => {
    const f = { ...openFilters, filterParty: 'greens' };
    expect(buildVisibleSeatKeySet(seats, comparisonMap, f, null).size).toBe(0);
  });
});

// ── getChoroplethValue ────────────────────────────────────────────────────────

describe('getChoroplethValue', () => {
  const seat = { votes: { labour: 20000, conservative: 15000 }, turnout: 35000 };
  const comparison = { votes: { labour: 15000, conservative: 20000 }, turnout: 35000 };

  it('returns null when choroplethType is none', () => {
    expect(getChoroplethValue(seat, comparison, 'none', 'labour')).toBeNull();
  });

  it('returns null when choroplethParty is all', () => {
    expect(getChoroplethValue(seat, comparison, 'voteShare', 'all')).toBeNull();
  });

  it('returns null when choroplethParty is falsy', () => {
    expect(getChoroplethValue(seat, comparison, 'voteShare', '')).toBeNull();
  });

  it('returns vote share percentage for voteShare type', () => {
    // labour: 20000 / 35000 ≈ 57.14%
    const val = getChoroplethValue(seat, null, 'voteShare', 'labour');
    expect(val).toBeCloseTo(57.14, 1);
  });

  it('returns null for voteShareChange when comparisonSeat is null', () => {
    expect(getChoroplethValue(seat, null, 'voteShareChange', 'labour')).toBeNull();
  });

  it('returns the vote share change for voteShareChange type', () => {
    // labour: 57.14% now vs 42.86% before → change ≈ +14.28
    const val = getChoroplethValue(seat, comparison, 'voteShareChange', 'labour');
    expect(val).toBeCloseTo(14.28, 0);
  });

  it('returns null for an unknown choroplethType', () => {
    expect(getChoroplethValue(seat, comparison, 'unknown', 'labour')).toBeNull();
  });
});

// ── resolveElectionFiles ──────────────────────────────────────────────────────

describe('resolveElectionFiles', () => {
  const manifest = {
    settings: {
      mapFilesById: { '1': 'maps/uk2024.json' },
      dataFilesByElectionId: { '42': 'results/2024.json' },
    },
  };
  const election = { id: '42', mapId: 1 };

  it('returns mapFile and dataFile from manifest settings', () => {
    expect(resolveElectionFiles(manifest, election)).toEqual({
      mapFile: 'maps/uk2024.json',
      dataFile: 'results/2024.json',
    });
  });

  it('falls back to election.mapFile when mapId has no settings entry', () => {
    const e = { id: '42', mapId: 99, mapFile: 'maps/fallback.json' };
    const { mapFile } = resolveElectionFiles(manifest, e);
    expect(mapFile).toBe('maps/fallback.json');
  });

  it('falls back to election.dataFile when id has no settings entry', () => {
    const e = { id: '99', mapId: 1, dataFile: 'results/fallback.json' };
    const { dataFile } = resolveElectionFiles(manifest, e);
    expect(dataFile).toBe('results/fallback.json');
  });

  it('throws when mapFile cannot be resolved', () => {
    const e = { id: '42', mapId: 99 };
    expect(() => resolveElectionFiles(manifest, e)).toThrow(/Missing file configuration/);
  });

  it('throws when dataFile cannot be resolved', () => {
    const e = { id: '99', mapId: 1 };
    expect(() => resolveElectionFiles(manifest, e)).toThrow(/Missing file configuration/);
  });

  it('handles missing manifest settings gracefully', () => {
    const e = { id: '1', mapFile: 'a.json', dataFile: 'b.json' };
    expect(resolveElectionFiles({}, e)).toEqual({ mapFile: 'a.json', dataFile: 'b.json' });
  });
});

// ── getPredictBaselineShare ───────────────────────────────────────────────────

describe('getPredictBaselineShare', () => {
  const baselineMap = new Map([
    ['england::labour', 45.7],
    ['england::conservative', 35.2],
  ]);

  it('returns the rounded baseline share for a known region/party', () => {
    expect(getPredictBaselineShare('england', 'labour', baselineMap)).toBe(46);
  });

  it('returns 0 when the key is not in the map', () => {
    expect(getPredictBaselineShare('scotland', 'snp', baselineMap)).toBe(0);
  });

  it('rounds fractional values', () => {
    expect(getPredictBaselineShare('england', 'conservative', baselineMap)).toBe(35);
  });
});

// ── getPredictInputShareValue ─────────────────────────────────────────────────

describe('getPredictInputShareValue', () => {
  const baselineMap = new Map([['england::labour', 45]]);
  const inputMap = new Map([['england::labour', 50]]);
  const emptyInput = new Map();

  it('returns the input value when present in inputMap', () => {
    expect(getPredictInputShareValue('england', 'labour', inputMap, baselineMap)).toBe(50);
  });

  it('falls back to the baseline when key is absent from inputMap', () => {
    expect(getPredictInputShareValue('england', 'labour', emptyInput, baselineMap)).toBe(45);
  });

  it('returns 0 when absent from both maps', () => {
    expect(getPredictInputShareValue('scotland', 'snp', emptyInput, new Map())).toBe(0);
  });
});

// ── calculatePredictEnteredShareTotal ─────────────────────────────────────────

describe('calculatePredictEnteredShareTotal', () => {
  // For 'england' (GB region), parties are the base predict party keys
  const inputMap = new Map([
    ['england::labour', 38],
    ['england::conservative', 28],
    ['england::libdems', 12],
    ['england::reform', 14],
    ['england::green', 6],
  ]);
  const baselineMap = new Map();

  it('sums all party shares for a region', () => {
    const total = calculatePredictEnteredShareTotal('england', inputMap, baselineMap);
    expect(total).toBe(38 + 28 + 12 + 14 + 6);
  });

  it('returns 0 when both maps are empty', () => {
    expect(calculatePredictEnteredShareTotal('england', new Map(), new Map())).toBe(0);
  });
});

// ── calculatePredictOtherShare ────────────────────────────────────────────────

describe('calculatePredictOtherShare', () => {
  it('returns 100 when no shares are entered', () => {
    expect(calculatePredictOtherShare('england', new Map(), new Map())).toBe(100);
  });

  it('returns 0 when shares sum exactly to 100', () => {
    const inputMap = new Map([
      ['england::labour', 40],
      ['england::conservative', 30],
      ['england::libdems', 15],
      ['england::reform', 10],
      ['england::green', 5],
    ]);
    expect(calculatePredictOtherShare('england', inputMap, new Map())).toBe(0);
  });

  it('returns a negative value when shares exceed 100', () => {
    const inputMap = new Map([
      ['england::labour', 60],
      ['england::conservative', 60],
    ]);
    expect(calculatePredictOtherShare('england', inputMap, new Map())).toBeLessThan(0);
  });
});

// ── collectPredictAllRegions ──────────────────────────────────────────────────

describe('collectPredictAllRegions', () => {
  const regionMap = new Map([
    ['southwestengland', 'South West England'],
    ['scotland', 'Scotland'],
    ['northwestengland', 'North West England'],
  ]);

  it('returns { regionKey, regionLabel } entries sorted by label', () => {
    const result = collectPredictAllRegions(regionMap);
    expect(result.map((r) => r.regionLabel)).toEqual([
      'North West England',
      'Scotland',
      'South West England',
    ]);
  });

  it('returns the correct regionKey for each entry', () => {
    const result = collectPredictAllRegions(regionMap);
    expect(result[0]).toEqual({ regionKey: 'northwestengland', regionLabel: 'North West England' });
  });

  it('returns an empty array for an empty map', () => {
    expect(collectPredictAllRegions(new Map())).toEqual([]);
  });
});

// ── collectPredictValidationRows ──────────────────────────────────────────────

describe('collectPredictValidationRows', () => {
  const regionMap = new Map([
    ['southeastengland', 'South East England'],
    ['scotland', 'Scotland'],
    ['wales', 'Wales'],
    ['northernireland', 'Northern Ireland'],
  ]);

  it('always includes the England aggregate row first', () => {
    const rows = collectPredictValidationRows(regionMap);
    expect(rows[0].regionKey).toBe('england');
    expect(rows[0].regionLabel).toBe('England');
  });

  it('includes English, Scottish, Welsh, and NI regions', () => {
    const rows = collectPredictValidationRows(regionMap);
    const keys = rows.map((r) => r.regionKey);
    expect(keys).toContain('southeastengland');
    expect(keys).toContain('scotland');
    expect(keys).toContain('wales');
    expect(keys).toContain('northernireland');
  });

  it('does not duplicate the England aggregate', () => {
    const rows = collectPredictValidationRows(regionMap);
    expect(rows.filter((r) => r.regionKey === 'england')).toHaveLength(1);
  });
});

// ── collectPredictShareStateRows ──────────────────────────────────────────────

describe('collectPredictShareStateRows', () => {
  const regionMap = new Map([['scotland', 'Scotland']]);

  it('returns the same result as collectPredictValidationRows', () => {
    expect(collectPredictShareStateRows(regionMap)).toEqual(
      collectPredictValidationRows(regionMap)
    );
  });
});

// ── collectPredictInputRows ───────────────────────────────────────────────────

describe('collectPredictInputRows', () => {
  const regionMap = new Map([
    ['southeastengland', 'South East England'],
    ['northwestengland', 'North West England'],
    ['scotland', 'Scotland'],
    ['wales', 'Wales'],
    ['northernireland', 'Northern Ireland'],
  ]);

  it('always has England aggregate as the first row', () => {
    const rows = collectPredictInputRows(regionMap, false);
    expect(rows[0]).toEqual({
      regionKey: 'england',
      regionLabel: 'England',
      isEnglandAggregate: true,
      isEnglandRegion: false,
    });
  });

  it('does not include English sub-regions when englandExpanded is false', () => {
    const rows = collectPredictInputRows(regionMap, false);
    expect(rows.some((r) => r.isEnglandRegion)).toBe(false);
  });

  it('includes English sub-regions when englandExpanded is true', () => {
    const rows = collectPredictInputRows(regionMap, true);
    const subRegions = rows.filter((r) => r.isEnglandRegion);
    expect(subRegions.length).toBe(2);
    expect(subRegions.every((r) => !r.isEnglandAggregate)).toBe(true);
  });

  it('always includes Scotland, Wales, and NI when present in the map', () => {
    const rows = collectPredictInputRows(regionMap, false);
    const keys = rows.map((r) => r.regionKey);
    expect(keys).toContain('scotland');
    expect(keys).toContain('wales');
    expect(keys).toContain('northernireland');
  });

  it('Scotland, Wales, NI rows have isEnglandAggregate and isEnglandRegion both false', () => {
    const rows = collectPredictInputRows(regionMap, false);
    ['scotland', 'wales', 'northernireland'].forEach((key) => {
      const row = rows.find((r) => r.regionKey === key);
      expect(row.isEnglandAggregate).toBe(false);
      expect(row.isEnglandRegion).toBe(false);
    });
  });

  it('returns only England aggregate when the map has no known regions', () => {
    const rows = collectPredictInputRows(new Map(), false);
    expect(rows).toHaveLength(1);
    expect(rows[0].isEnglandAggregate).toBe(true);
  });
});


// ── buildRegionSummary ────────────────────────────────────────────────────────

describe('buildRegionSummary', () => {
  const makeConstSeat = (seat, region, winner, votes = {}) => ({ seat, region, winner, votes });
  const makeListSeat = (seat, region, winner, votes = {}) => ({ seat: `${region} List ${seat}`, region, winner, votes });

  it('counts seats by party per region', () => {
    const seats = [
      makeConstSeat('Aberdeen North', 'North East Scotland', 'snp'),
      makeConstSeat('Aberdeen South', 'North East Scotland', 'conservative'),
      makeListSeat(1, 'North East Scotland', 'conservative'),
    ];
    const summary = buildRegionSummary(seats);
    expect(summary.get('North East Scotland').seatsByParty.snp).toBe(1);
    expect(summary.get('North East Scotland').seatsByParty.conservative).toBe(2);
  });

  it('sets dominantParty to party with most seats', () => {
    const seats = [
      makeConstSeat('A', 'Lothian', 'snp'),
      makeConstSeat('B', 'Lothian', 'snp'),
      makeConstSeat('C', 'Lothian', 'labour'),
    ];
    expect(buildRegionSummary(seats).get('Lothian').dominantParty).toBe('snp');
  });

  it('breaks ties by list vote total', () => {
    const seats = [
      makeConstSeat('A', 'Glasgow', 'snp', { snp: 50000 }),
      makeConstSeat('B', 'Glasgow', 'labour', { labour: 80000 }),
    ];
    // Tied on seats (1 each); labour has more votes → labour wins
    expect(buildRegionSummary(seats).get('Glasgow').dominantParty).toBe('labour');
  });

  it('partitions list seats into listSeats array', () => {
    const seats = [
      makeConstSeat('Dundee East', 'Mid Scotland and Fife', 'snp'),
      makeListSeat(1, 'Mid Scotland and Fife', 'conservative'),
      makeListSeat(2, 'Mid Scotland and Fife', 'snp'),
    ];
    const r = buildRegionSummary(seats).get('Mid Scotland and Fife');
    expect(r.listSeats).toHaveLength(2);
    expect(r.listSeats.every((s) => s.seat.includes('List'))).toBe(true);
  });

  it('accumulates votesByParty across all seats in a region', () => {
    const seats = [
      makeConstSeat('X', 'Central Scotland', 'snp', { snp: 10000, labour: 5000 }),
      makeConstSeat('Y', 'Central Scotland', 'snp', { snp: 8000, labour: 3000 }),
    ];
    const r = buildRegionSummary(seats).get('Central Scotland');
    expect(r.votesByParty.snp).toBe(18000);
    expect(r.votesByParty.labour).toBe(8000);
  });

  it('handles seats with no region as unknown', () => {
    const seats = [makeConstSeat('Mystery', undefined, 'others')];
    expect(buildRegionSummary(seats).has('unknown')).toBe(true);
  });
});

// ── isListSeat ───────────────────────────────────────────────────────────────

describe('isListSeat', () => {
  it('matches standard list seat names', () => {
    expect(isListSeat('Glasgow List 1')).toBe(true);
    expect(isListSeat('West Scotland List 7')).toBe(true);
    expect(isListSeat('Highlands and Islands List 10')).toBe(true);
    expect(isListSeat('Central Scotland List 1')).toBe(true);
  });

  it('is case-insensitive', () => {
    expect(isListSeat('Glasgow list 1')).toBe(true);
    expect(isListSeat('Glasgow LIST 3')).toBe(true);
  });

  it('does not match regular constituency seats', () => {
    expect(isListSeat('Glasgow North')).toBe(false);
    expect(isListSeat('Aberdeen Central')).toBe(false);
    expect(isListSeat('Edinburgh South West')).toBe(false);
  });

  it('does not match seats where "List" appears but not at the end as "List N"', () => {
    expect(isListSeat('Listed Place')).toBe(false);
    expect(isListSeat('List Road')).toBe(false);
    expect(isListSeat('Glasgow List')).toBe(false); // no trailing digit
  });

  it('returns false for empty string', () => {
    expect(isListSeat('')).toBe(false);
  });
});

// ── summarizeElection — mode parameter ──────────────────────────────────────

describe('summarizeElection mode parameter', () => {
  const constSeat = (winner, votes, region = 'Lothian') => ({
    seat: 'Edinburgh Central',
    winner,
    votes,
    electorate: 0,
    turnout: 0,
    region,
  });

  const listSeat = (n, winner, votes, region = 'Lothian') => ({
    seat: `${region} List ${n}`,
    winner,
    votes,
    electorate: 0,
    turnout: 0,
    region,
  });

  const mixedSeats = [
    constSeat('snp', { snp: 20000, labour: 10000 }),
    constSeat('labour', { labour: 18000, snp: 9000 }),
    listSeat(1, 'snp', { snp: 100000, labour: 60000, conservative: 40000 }),
    listSeat(2, 'labour', { snp: 100000, labour: 60000, conservative: 40000 }),
  ];

  it('mode=constituency counts only constituency seats and votes', () => {
    const { parties } = summarizeElection(mixedSeats, { mode: 'constituency' });
    expect(parties.find((p) => p.party === 'snp').seats).toBe(1);
    expect(parties.find((p) => p.party === 'labour').seats).toBe(1);
    // list vote totals not included
    expect(parties.find((p) => p.party === 'conservative')).toBeUndefined();
    expect(parties.find((p) => p.party === 'snp').votes).toBe(29000);
  });

  it('mode=list counts only list seats and deduplicates votes by region', () => {
    const { parties } = summarizeElection(mixedSeats, { mode: 'list' });
    // constituency seats excluded from seat counts
    expect(parties.find((p) => p.party === 'snp').seats).toBe(1);
    expect(parties.find((p) => p.party === 'labour').seats).toBe(1);
    // regional votes counted once per (region, party), not once per seat slot
    expect(parties.find((p) => p.party === 'snp').votes).toBe(100000);
    expect(parties.find((p) => p.party === 'labour').votes).toBe(60000);
    expect(parties.find((p) => p.party === 'conservative').votes).toBe(40000);
  });

  it('mode=all counts seats from both types but votes from constituency only', () => {
    const { parties } = summarizeElection(mixedSeats, { mode: 'all' });
    expect(parties.find((p) => p.party === 'snp').seats).toBe(2);
    expect(parties.find((p) => p.party === 'labour').seats).toBe(2);
    // list votes excluded from totals
    expect(parties.find((p) => p.party === 'conservative')).toBeUndefined();
    expect(parties.find((p) => p.party === 'snp').votes).toBe(29000);
  });

  it('mode=list deduplicates across multiple seats in same region', () => {
    const seats = [
      listSeat(1, 'snp', { snp: 80000, labour: 50000 }, 'Glasgow'),
      listSeat(2, 'snp', { snp: 80000, labour: 50000 }, 'Glasgow'),
      listSeat(3, 'labour', { snp: 80000, labour: 50000 }, 'Glasgow'),
    ];
    const { parties } = summarizeElection(seats, { mode: 'list' });
    expect(parties.find((p) => p.party === 'snp').votes).toBe(80000);
    expect(parties.find((p) => p.party === 'labour').votes).toBe(50000);
  });

  it('mode=constituency calculates turnout from constituency seats only', () => {
    const seats = [
      { seat: 'Edinburgh Central', winner: 'snp', votes: { snp: 20000 }, electorate: 50000, turnout: 0.6, region: 'Lothian' },
      { seat: 'Lothian List 1', winner: 'labour', votes: { labour: 60000 }, electorate: 200000, turnout: 0.7, region: 'Lothian' },
    ];
    const { turnout } = summarizeElection(seats, { mode: 'constituency' });
    // Only the constituency seat contributes: 0.6 * 50000 / 50000 = 0.6
    expect(turnout).toBeCloseTo(0.6, 5);
  });

  it('mode=list calculates turnout from list seats only', () => {
    const seats = [
      { seat: 'Edinburgh Central', winner: 'snp', votes: { snp: 20000 }, electorate: 50000, turnout: 0.6, region: 'Lothian' },
      { seat: 'Lothian List 1', winner: 'labour', votes: { labour: 60000 }, electorate: 200000, turnout: 0.7, region: 'Lothian' },
    ];
    const { turnout } = summarizeElection(seats, { mode: 'list' });
    // Only the list seat contributes: 0.7 * 200000 / 200000 = 0.7
    expect(turnout).toBeCloseTo(0.7, 5);
  });
});

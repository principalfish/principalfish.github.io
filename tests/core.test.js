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
  // URL encode/decode
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
});


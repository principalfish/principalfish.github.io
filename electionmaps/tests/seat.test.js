import { describe, it, expect, afterEach } from 'vitest';
import { Seat, state } from '../scripts/state.js';

describe('Seat.voteSharePct', () => {
  it('calculates share against the explicit turnout when present', () => {
    const seat = { turnout: 1000, votes: { labour: 400, conservative: 300 } };
    expect(Seat.voteSharePct(seat, 'labour')).toBe(40);
  });

  it('falls back to summing votes when turnout is missing', () => {
    const seat = { votes: { labour: 100, conservative: 100 } };
    expect(Seat.voteSharePct(seat, 'labour')).toBe(50);
  });

  it('falls back to summing votes when turnout is zero', () => {
    const seat = { turnout: 0, votes: { labour: 100, conservative: 100 } };
    expect(Seat.voteSharePct(seat, 'labour')).toBe(50);
  });

  it('returns 0 when total votes are zero', () => {
    expect(Seat.voteSharePct({ votes: {} }, 'labour')).toBe(0);
  });

  it('returns 0 for a party with no recorded votes', () => {
    const seat = { votes: { conservative: 100 } };
    expect(Seat.voteSharePct(seat, 'labour')).toBe(0);
  });

  it('handles a missing votes object', () => {
    expect(Seat.voteSharePct({}, 'labour')).toBe(0);
  });

  it('handles null/undefined seat input', () => {
    expect(Seat.voteSharePct(null, 'labour')).toBe(0);
    expect(Seat.voteSharePct(undefined, 'labour')).toBe(0);
  });
});

describe('Seat constructor', () => {
  it('computes turnout as the sum of votes and applies defaults', () => {
    const seat = new Seat({ seat: 'A', region: 'london', winner: 'labour', votes: { labour: 600, conservative: 400 } });
    expect(seat.turnout).toBe(1000);
    expect(seat.seat).toBe('A');
    expect(seat.region).toBe('london');
  });

  it('drops zero and negative vote entries', () => {
    const seat = new Seat({ seat: 'A', region: 'r', winner: 'labour', votes: { labour: 100, green: 0, reform: -5 } });
    expect(seat.votes).toEqual({ labour: 100 });
    expect(seat.turnout).toBe(100);
  });

  it('falls back to placeholder seat/region/winner when omitted', () => {
    const seat = new Seat({});
    expect(seat.seat).toBe('Unknown seat');
    expect(seat.region).toBe('unknown');
    expect(seat.winner).toBe('others');
  });

  it('folds non-canonical string winner keys onto their canonical alias', () => {
    // resolvePartyRef applies PARTY_KEY_ALIASES to string refs after alnum-normalising.
    expect(new Seat({ winner: 'uup', votes: { uu: 1 } }).winner).toBe('uu');
    expect(new Seat({ winner: 'Reform UK', votes: { reform: 1 } }).winner).toBe('reform');
    expect(new Seat({ winner: 'Liberal Democrats', votes: { libdems: 1 } }).winner).toBe('libdems');
  });
});

describe('Seat.fromRaw', () => {
  it('decodes a compact pf-results-v4 seat with string refs', () => {
    const seat = Seat.fromRaw({ n: 'Glasgow', r: 'glasgow', w: 'snp', p: [['snp', 600], ['labour', 400]] });
    expect(seat.seat).toBe('Glasgow');
    expect(seat.region).toBe('glasgow');
    expect(seat.winner).toBe('snp');
    expect(seat.turnout).toBe(1000);
  });

  it('drops non-positive vote pairs and malformed entries', () => {
    const seat = Seat.fromRaw({ n: 'A', r: 'r', w: 'labour', p: [['labour', 100], ['green', 0], ['bad']] });
    expect(seat.votes).toEqual({ labour: 100 });
  });
});

describe('Seat.isList', () => {
  it('matches regional list seat names', () => {
    expect(Seat.isList({ seat: 'Glasgow List 1' })).toBe(true);
    expect(Seat.isList({ seat: 'Highlands and Islands List 7' })).toBe(true);
  });

  it('rejects constituency seat names and missing input', () => {
    expect(Seat.isList({ seat: 'Glasgow Pollok' })).toBe(false);
    expect(Seat.isList({ seat: 'Listowel' })).toBe(false);
    expect(Seat.isList({})).toBe(false);
  });
});

describe('Seat.isList (configurable per-map pattern)', () => {
  afterEach(() => { state.mapConfig = null; });

  it('uses the default "<region> List <n>" convention when no pattern is configured', () => {
    state.mapConfig = null;
    expect(Seat.isList({ seat: 'Glasgow List 1' })).toBe(true);
    expect(Seat.isList({ seat: 'Glasgow Pollok' })).toBe(false);
  });

  it('honours a per-map listSeatPattern override', () => {
    state.mapConfig = { listSeatPattern: '^Region:' };
    expect(Seat.isList({ seat: 'Region: North 1' })).toBe(true);
    // The default "List N" convention no longer matches once a chamber overrides the pattern.
    expect(Seat.isList({ seat: 'Glasgow List 1' })).toBe(false);
  });

  it('falls back to the default when the configured pattern is malformed', () => {
    state.mapConfig = { listSeatPattern: '[' };
    expect(Seat.isList({ seat: 'Glasgow List 1' })).toBe(true);
  });
});

describe('Seat.majorityStats', () => {
  it('returns the winning margin as a percentage of turnout and raw votes', () => {
    const seat = new Seat({ seat: 'A', region: 'r', winner: 'labour', votes: { labour: 600, conservative: 400 } });
    const { pct, raw } = seat.majorityStats();
    expect(raw).toBe(200);
    expect(pct).toBe(20);
  });

  it('returns zeroes when fewer than two parties have votes', () => {
    const seat = new Seat({ seat: 'A', region: 'r', winner: 'labour', votes: { labour: 500 } });
    expect(seat.majorityStats()).toEqual({ pct: 0, raw: 0 });
  });
});

describe('Seat.gainFromParty', () => {
  const seat = new Seat({ seat: 'A', region: 'r', winner: 'labour', votes: { labour: 600, conservative: 400 } });

  it('returns the previous winner when the seat changed hands', () => {
    expect(seat.gainFromParty('conservative')).toBe('conservative');
  });

  it('returns null when the seat held or there is no comparison', () => {
    expect(seat.gainFromParty('labour')).toBe(null);
    expect(seat.gainFromParty(null)).toBe(null);
  });
});

import { describe, it, expect } from 'vitest';
import { Seat } from '../scripts/state.js';

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

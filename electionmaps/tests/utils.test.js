import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../scripts/state.js', () => ({
  manifest: {
    partiesByKey: {
      labour: { name: 'Labour', colour: '#E4003B' },
      snp: { name: 'Scottish National Party', colour: '#FFF95D' },
      noname: { colour: '#123456' },
      nocolour: { name: 'No Colour Party' },
    },
  },
}));

import { normalizeRegionKey, labelParty, colourParty } from '../scripts/utils.js';

describe('normalizeRegionKey', () => {
  it('lowercases and strips non-alphanumeric characters', () => {
    expect(normalizeRegionKey('North East England')).toBe('northeastengland');
  });

  it('handles hyphens and punctuation', () => {
    expect(normalizeRegionKey('Yorkshire & The Humber')).toBe('yorkshirethehumber');
  });

  it('returns empty string for empty input', () => {
    expect(normalizeRegionKey('')).toBe('');
  });

  it('returns empty string for null/undefined', () => {
    expect(normalizeRegionKey(null)).toBe('');
    expect(normalizeRegionKey(undefined)).toBe('');
  });

  it('handles numeric input', () => {
    expect(normalizeRegionKey(123)).toBe('123');
  });
});

describe('labelParty', () => {
  it('returns the party name for a known key', () => {
    expect(labelParty('labour')).toBe('Labour');
    expect(labelParty('snp')).toBe('Scottish National Party');
  });

  it('falls back to the raw key for an unknown party', () => {
    expect(labelParty('ukip')).toBe('ukip');
  });

  it('falls back to the raw key when the party entry has no name', () => {
    expect(labelParty('noname')).toBe('noname');
  });
});

describe('colourParty', () => {
  it('returns the party colour for a known key', () => {
    expect(colourParty('labour')).toBe('#E4003B');
    expect(colourParty('snp')).toBe('#FFF95D');
  });

  it('returns grey fallback for an unknown party', () => {
    expect(colourParty('ukip')).toBe('#9CA3AF');
  });

  it('returns grey fallback when the party entry has no colour', () => {
    expect(colourParty('nocolour')).toBe('#9CA3AF');
  });
});

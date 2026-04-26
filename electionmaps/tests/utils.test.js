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

import { normalizeRegionKey, escapeHtml, formatInt, formatPct } from '../scripts/utils.js';

describe('escapeHtml', () => {
  it('escapes ampersands', () => {
    expect(escapeHtml('a & b')).toBe('a &amp; b');
  });

  it('escapes angle brackets', () => {
    expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
  });

  it('escapes double quotes', () => {
    expect(escapeHtml('"hello"')).toBe('&quot;hello&quot;');
  });

  it('escapes all special characters in one string', () => {
    expect(escapeHtml('<a href="x&y">')).toBe('&lt;a href=&quot;x&amp;y&quot;&gt;');
  });

  it('returns empty string for empty input', () => {
    expect(escapeHtml('')).toBe('');
  });

  it('returns empty string for null/undefined', () => {
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
  });

  it('leaves plain text unchanged', () => {
    expect(escapeHtml('Hello world')).toBe('Hello world');
  });
});

describe('formatInt', () => {
  it('formats integers with thousands separators', () => {
    expect(formatInt(1234567)).toBe('1,234,567');
  });

  it('rounds floats to nearest integer', () => {
    expect(formatInt(1.6)).toBe('2');
    expect(formatInt(1.4)).toBe('1');
  });

  it('handles zero', () => {
    expect(formatInt(0)).toBe('0');
  });

  it('handles negative numbers', () => {
    expect(formatInt(-1234)).toBe('-1,234');
  });
});

describe('formatPct', () => {
  it('formats to two decimal places', () => {
    expect(formatPct(42.356)).toBe('42.36');
    expect(formatPct(42.354)).toBe('42.35');
  });

  it('pads whole numbers with .00', () => {
    expect(formatPct(10)).toBe('10.00');
  });

  it('handles zero', () => {
    expect(formatPct(0)).toBe('0.00');
  });

  it('handles negative values', () => {
    expect(formatPct(-3.5)).toBe('-3.50');
  });
});

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

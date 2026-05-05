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

import { normalizeRegionKey, titleCaseFromRegionKey, escapeHtml, formatInt, formatPct, formatSigned, getRegionLabel, buildWinnerBySeat } from '../scripts/utils.js';

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

describe('titleCaseFromRegionKey', () => {
  it('title-cases a space-separated string', () => {
    expect(titleCaseFromRegionKey('north east england')).toBe('North East England');
  });

  it('splits on hyphens', () => {
    expect(titleCaseFromRegionKey('north-east-england')).toBe('North East England');
  });

  it('splits on underscores', () => {
    expect(titleCaseFromRegionKey('north_east_england')).toBe('North East England');
  });

  it('splits on camelCase boundaries', () => {
    expect(titleCaseFromRegionKey('northEastEngland')).toBe('North East England');
  });

  it('title-cases a single word', () => {
    expect(titleCaseFromRegionKey('scotland')).toBe('Scotland');
  });

  it('returns Unknown for empty string', () => {
    expect(titleCaseFromRegionKey('')).toBe('Unknown');
  });

  it('returns Unknown for null/undefined', () => {
    expect(titleCaseFromRegionKey(null)).toBe('Unknown');
    expect(titleCaseFromRegionKey(undefined)).toBe('Unknown');
  });
});

describe('getRegionLabel', () => {
  const labels = new Map([
    ['northeastengland', 'North East England'],
    ['yorkshirethehumber', 'Yorkshire and The Humber'],
    ['scotland', 'Scotland'],
  ]);

  it('returns the label from the lookup map when the normalised key matches', () => {
    expect(getRegionLabel('North East England', labels)).toBe('North East England');
  });

  it('replaces " and " with " & "', () => {
    expect(getRegionLabel('Yorkshire & The Humber', labels)).toBe('Yorkshire & The Humber');
  });

  it('falls back to a title-cased form when the key is not in the lookup', () => {
    expect(getRegionLabel('northWest', labels)).toBe('North West');
  });

  it('returns Unknown for empty input', () => {
    expect(getRegionLabel('', labels)).toBe('Unknown');
  });

  it('returns Unknown for null/undefined input', () => {
    expect(getRegionLabel(null, labels)).toBe('Unknown');
    expect(getRegionLabel(undefined, labels)).toBe('Unknown');
  });

  it('falls back to a title-cased form when the lookup map is missing', () => {
    expect(getRegionLabel('scotland', undefined)).toBe('Scotland');
    expect(getRegionLabel('scotland', null)).toBe('Scotland');
  });
});

describe('formatSigned', () => {
  it('prefixes positive values with +', () => {
    expect(formatSigned(3)).toBe('+3');
    expect(formatSigned(1.5, 2)).toBe('+1.50');
  });

  it('keeps negative sign on negative values', () => {
    expect(formatSigned(-3)).toBe('-3');
    expect(formatSigned(-1.5, 2)).toBe('-1.50');
  });

  it('returns "0" for exact zero', () => {
    expect(formatSigned(0)).toBe('0');
  });

  it('returns "0" for values within floating-point epsilon of zero', () => {
    expect(formatSigned(1e-10)).toBe('0');
    expect(formatSigned(-1e-10)).toBe('0');
  });

  it('defaults to zero decimal places', () => {
    expect(formatSigned(3.7)).toBe('+4');
  });

  it('treats null/undefined as zero', () => {
    expect(formatSigned(null)).toBe('0');
    expect(formatSigned(undefined)).toBe('0');
  });
});

describe('buildWinnerBySeat', () => {
  it('maps seat name to winner party key', () => {
    const result = buildWinnerBySeat([{ seat: 'Bristol East', winner: 'labour' }]);
    expect(result.get('Bristol East')).toBe('labour');
  });

  it('also stores a lowercase key for each seat', () => {
    const result = buildWinnerBySeat([{ seat: 'Bristol East', winner: 'labour' }]);
    expect(result.get('bristol east')).toBe('labour');
  });

  it('defaults winner to "others" when winner is missing', () => {
    const result = buildWinnerBySeat([{ seat: 'Bristol East' }]);
    expect(result.get('Bristol East')).toBe('others');
    expect(result.get('bristol east')).toBe('others');
  });

  it('skips entries without a seat property', () => {
    const result = buildWinnerBySeat([{ winner: 'labour' }, null, undefined]);
    expect(result.size).toBe(0);
  });

  it('handles multiple seats', () => {
    const result = buildWinnerBySeat([
      { seat: 'Bristol East', winner: 'labour' },
      { seat: 'Orkney', winner: 'libdem' },
    ]);
    expect(result.get('Bristol East')).toBe('labour');
    expect(result.get('Orkney')).toBe('libdem');
    expect(result.size).toBe(4);
  });

  it('returns an empty Map for an empty array', () => {
    expect(buildWinnerBySeat([]).size).toBe(0);
  });
});


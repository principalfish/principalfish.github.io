import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../state.js', () => ({
  manifest: {
    partiesByKey: {
      labour: { name: 'Labour', colour: '#E4003B' },
      snp: { name: 'Scottish National Party', colour: '#FFF95D' },
      noname: { colour: '#123456' },
      nocolour: { name: 'No Colour Party' },
    },
  },
}));

import { normalizeRegionKey, titleCaseFromRegionKey, escapeHtml, formatInt, formatPct, formatSigned, getRegionLabel, clampNumber, roundShare, base64urlEncode, base64urlDecode, deltaClass } from '../utils.js';

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

describe('clampNumber', () => {
  it('returns value when within range', () => {
    expect(clampNumber(5, 0, 10)).toBe(5);
  });

  it('clamps to minimum when value is below range', () => {
    expect(clampNumber(-5, 0, 100)).toBe(0);
  });

  it('clamps to maximum when value is above range', () => {
    expect(clampNumber(150, 0, 100)).toBe(100);
  });

  it('returns minimum for non-finite values', () => {
    expect(clampNumber(NaN, 0, 100)).toBe(0);
    expect(clampNumber(Infinity, 0, 100)).toBe(0);
    expect(clampNumber(-Infinity, 0, 100)).toBe(0);
  });

  it('coerces string input', () => {
    expect(clampNumber('50', 0, 100)).toBe(50);
    expect(clampNumber('abc', 0, 100)).toBe(0);
  });

  it('returns minimum for null/undefined', () => {
    expect(clampNumber(null, 0, 100)).toBe(0);
    expect(clampNumber(undefined, 0, 100)).toBe(0);
  });

  it('handles boundary values inclusively', () => {
    expect(clampNumber(0, 0, 100)).toBe(0);
    expect(clampNumber(100, 0, 100)).toBe(100);
  });
});

describe('roundShare', () => {
  it('rounds an in-range value to the nearest integer', () => {
    expect(roundShare(42.4)).toBe(42);
    expect(roundShare(42.5)).toBe(43);
    expect(roundShare(42.6)).toBe(43);
  });

  it('clamps below zero to 0', () => {
    expect(roundShare(-5)).toBe(0);
    expect(roundShare(-0.4)).toBe(0);
  });

  it('clamps above 100 to 100', () => {
    expect(roundShare(101)).toBe(100);
    expect(roundShare(150.7)).toBe(100);
  });

  it('returns 0 for non-finite input', () => {
    expect(roundShare(NaN)).toBe(0);
    expect(roundShare(Infinity)).toBe(0);
    expect(roundShare(-Infinity)).toBe(0);
  });

  it('returns 0 for null/undefined', () => {
    expect(roundShare(null)).toBe(0);
    expect(roundShare(undefined)).toBe(0);
  });

  it('coerces numeric strings', () => {
    expect(roundShare('33.6')).toBe(34);
    expect(roundShare('-10')).toBe(0);
    expect(roundShare('abc')).toBe(0);
  });

  it('preserves the boundary values', () => {
    expect(roundShare(0)).toBe(0);
    expect(roundShare(100)).toBe(100);
  });
});

describe('base64urlEncode / base64urlDecode', () => {
  it('round-trips ASCII text', () => {
    const text = 'hello world';
    expect(base64urlDecode(base64urlEncode(text))).toBe(text);
  });

  it('round-trips JSON payloads with predict-like shape', () => {
    const payload = JSON.stringify({ e: 1, r: [['scotland', 'snp', 50], ['wales', 'plaidcymru', 30]] });
    expect(base64urlDecode(base64urlEncode(payload))).toBe(payload);
  });

  it('substitutes URL-unsafe characters', () => {
    // Inputs chosen to force btoa output to contain '+' and '/'.
    const encoded = base64urlEncode('\xff\xfe\xfd\xfc');
    expect(encoded).not.toMatch(/[+/=]/);
  });

  it('strips trailing padding from the encoded output', () => {
    expect(base64urlEncode('a')).toBe('YQ');
    expect(base64urlEncode('ab')).toBe('YWI');
    expect(base64urlEncode('abc')).toBe('YWJj');
  });

  it('returns empty string when encoding empty input', () => {
    expect(base64urlEncode('')).toBe('');
  });

  it('decodes payloads with arbitrary padding state', () => {
    expect(base64urlDecode('YQ')).toBe('a');
    expect(base64urlDecode('YWI')).toBe('ab');
    expect(base64urlDecode('YWJj')).toBe('abc');
  });

  it('returns null for malformed input', () => {
    expect(base64urlDecode('!!!not base64!!!')).toBeNull();
  });
});

describe('deltaClass', () => {
  it('classifies positive, negative, and neutral values', () => {
    expect(deltaClass(3)).toBe('maps-delta-positive');
    expect(deltaClass(-1.5)).toBe('maps-delta-negative');
    expect(deltaClass(0)).toBe('maps-delta-neutral');
  });

  it('treats values within floating-point epsilon of zero as neutral', () => {
    expect(deltaClass(1e-12)).toBe('maps-delta-neutral');
    expect(deltaClass(null)).toBe('maps-delta-neutral');
  });
});

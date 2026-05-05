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
 * Returns a trimmed, lowercase string suitable for use as a seat lookup key.
 * @param {string} seatName - Raw seat name.
 * @returns {string} Trimmed lowercase seat name for use in Map lookups.
 */
export function seatLookupKey(seatName) {
  return String(seatName || '').trim().toLowerCase();
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
 * Returns a CSS class name reflecting whether value is positive, negative, or neutral.
 * @param {number} value - Numeric delta value.
 * @returns {string} One of 'maps-delta-positive', 'maps-delta-negative', or 'maps-delta-neutral'.
 */
export function deltaClass(value) {
  const num = Number(value || 0);
  if (Math.abs(num) < 1e-9) return 'maps-delta-neutral';
  return num > 0 ? 'maps-delta-positive' : 'maps-delta-negative';
}

/**
 * Formats value with an explicit '+' prefix for positive numbers and the specified
 * decimal digits. Returns '0' for values within floating-point epsilon of zero.
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
 * Converts a region name to a lowercase alphanumeric key with all non-alphanumeric characters removed.
 * @param {string} value - Raw region name or key string.
 * @returns {string} Lowercase alphanumeric string suitable for use as a lookup key.
 */
export function normalizeRegionKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Converts a raw region key into a title-cased display label.
 * Splits on camelCase, hyphens, and underscores; title-cases each word.
 * @param {string} regionKey - Raw region key string.
 * @returns {string} Title-cased label, or 'Unknown' if empty.
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

/**
 * Resolves a region key to its display label using the provided lookup map, falling back
 * to a title-cased form of the raw key. Replaces ' and ' with ' & ' for compactness.
 * @param {string} regionKey - Raw or normalised region key.
 * @param {Map<string, string>} labelsByKey - Lookup from normalised key to display label.
 * @returns {string}
 */
export function getRegionLabel(regionKey, labelsByKey) {
  const normalized = normalizeRegionKey(regionKey);
  if (!normalized) return 'Unknown';
  const label = labelsByKey?.get(normalized) || titleCaseFromRegionKey(regionKey);
  return label.replace(/ and /gi, ' & ');
}

/**
 * Clamps value to [minimum, maximum]. Returns minimum if value is not a finite number.
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

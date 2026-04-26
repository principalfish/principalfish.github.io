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
 * Converts a region name to a lowercase alphanumeric key with all non-alphanumeric characters removed.
 * @param {string} value - Raw region name or key string.
 * @returns {string} Lowercase alphanumeric string suitable for use as a lookup key.
 */
export function normalizeRegionKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}


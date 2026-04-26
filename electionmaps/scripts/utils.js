import { manifest } from './state.js';

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

/**
 * Returns the display label for a party from the manifest, or the raw key if not found.
 * @param {string} partyKey - Canonical party key (e.g. "labour").
 * @returns {string} Human-readable party name, or the raw key as fallback.
 */
export function labelParty(partyKey) {
  const meta = manifest?.partiesByKey?.[partyKey];
  return meta?.name ?? partyKey;
}

/**
 * Returns the hex colour for a party from the manifest, or a grey fallback if not found.
 * @param {string} partyKey - Canonical party key (e.g. "labour").
 * @returns {string} Hex colour string (e.g. '#d50000'), or '#9CA3AF' if not found.
 */
export function colourParty(partyKey) {
  const meta = manifest?.partiesByKey?.[partyKey];
  return meta?.colour ?? '#9CA3AF';
}

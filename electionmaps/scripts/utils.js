import { manifest } from './state.js';

/**
 * Fetches a URL and passes the Response through the provided parser function. Throws on non-OK status.
 * @param {string} url - URL to fetch.
 * @param {function(Response): Promise<*>} parser - Function that receives the Response and returns a parsed value.
 * @returns {Promise<*>} Resolved value returned by the parser function.
 * @throws {Error} When the response status is not OK.
 */
async function fetchResource(url, parser) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return parser(response);
}

/**
 * Fetches and parses a JSON resource from the given URL.
 * @param {string} url - URL of the JSON resource.
 * @returns {Promise<*>} Parsed JSON value.
 */
export async function fetchJson(url) {
  return fetchResource(url, (response) => response.json());
}

// ─── Google Analytics ─────────────────────────────────────────────────────────

let lastTrackedPath = '';

/**
 * Fires a gtag page_view event for the current location, deduplicating against the last tracked path.
 * No-ops on dev hosts because ga-setup.js leaves window.gtag undefined there.
 * @returns {void}
 */
export function trackVirtualPageView() {
  if (typeof window.gtag !== 'function') return;

  const pagePath = `${window.location.pathname}${window.location.search}`;
  if (pagePath === lastTrackedPath) return;

  lastTrackedPath = pagePath;
  window.gtag('event', 'page_view', {
    page_location: window.location.href,
    page_path: pagePath,
    page_title: document.title,
  });
}

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
 * Fetches the parliament meta file and returns the latest poll snippet string, or null on failure.
 * @param {string} parliament - Parliament key ('westminster' | 'holyrood').
 * @returns {Promise<string|null>}
 */
export async function fetchElectionPredictionMeta(parliament) {
  const metaPath = manifest.files.meta[parliament];
  try {
    const payload = await fetchJson(`data/${metaPath}`);
    return String(payload?.latest_poll_snippet || '').trim();
  } catch {
    return null;
  }
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

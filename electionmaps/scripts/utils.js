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
 * Fires a gtag page_view event for a URL, deduplicating against the last tracked path.
 * @param {string} nextUrl - Full URL string to track; parsed to extract pathname and search.
 * @returns {void}
 */
export function trackVirtualPageView(nextUrl) {
  if (typeof window.gtag !== 'function') return;

  try {
    const parsed = new URL(nextUrl, window.location.origin);
    const pagePath = `${parsed.pathname}${parsed.search}`;
    if (pagePath === lastTrackedPath) return;

    lastTrackedPath = pagePath;
    window.gtag('event', 'page_view', {
      page_location: parsed.toString(),
      page_path: pagePath,
      page_title: document.title,
    });
  } catch (_error) {
  }
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

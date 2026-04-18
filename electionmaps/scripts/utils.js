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

/**
 * Converts a region name to a lowercase alphanumeric key with all non-alphanumeric characters removed.
 * @param {string} value - Raw region name or key string.
 * @returns {string} Lowercase alphanumeric string suitable for use as a lookup key.
 */
export function normalizeRegionKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

// Open-Meteo API usage estimates. Open-Meteo sends no rate-limit headers, so
// "usage left" can only be estimated: weigh each request the way Open-Meteo
// does, keep a per-browser log of what this page has spent, and compare it with
// the free-tier limits. The limits are per IP, so other apps or devices on the
// same network spend from the same allowance without showing up here.

// Free (non-commercial) tier limits, in weighted calls.
export const FREE_LIMITS = Object.freeze({
  minute: 600,
  hour: 5000,
  day: 10000,
  month: 300000,
});

const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
// Open-Meteo doesn't say whether "month" is a calendar month; a rolling 30 days
// is the cautious reading.
const MONTH_MS = 30 * DAY_MS;

const WINDOWS_MS = Object.freeze({
  minute: MINUTE_MS,
  hour: HOUR_MS,
  day: DAY_MS,
  month: MONTH_MS,
});

export const STORAGE_KEY = "routeweather.apiUsage";

// Ages are measured from now, and an entry counts only while
// 0 <= age < window. A future-dated entry (the clock was wrong when it was
// written) would otherwise count in every window and never be pruned.
const inWindow = (age, windowMs) => age >= 0 && age < windowMs;

// --- weighting ---

// Open-Meteo counts one call per location for up to 10 variables and up to 2
// weeks of data, and fractionally more beyond either (15 variables = 1.5 calls,
// 4 weeks = 2x). Weights are per location, so a batched request costs the sum.
export function forecastCallWeight({ locations, variables, hours }) {
  const variableFactor = Math.max(1, variables / 10);
  const lengthFactor = Math.max(1, hours / (14 * 24));
  return locations * variableFactor * lengthFactor;
}

// Open-Meteo doesn't document the elevation API's weighting; one call per
// coordinate is an assumption.
export function elevationCallWeight(locations) {
  return locations;
}

// --- usage log ---

// localStorage can be missing or throw (private windows, blocked site data);
// fall back to an in-memory store so the log still works for this page load.
function defaultStorage() {
  try {
    const probe = `${STORAGE_KEY}.probe`;
    globalThis.localStorage.setItem(probe, "1");
    globalThis.localStorage.removeItem(probe);
    return globalThis.localStorage;
  } catch {
    const memory = new Map();
    return {
      getItem: (key) => (memory.has(key) ? memory.get(key) : null),
      setItem: (key, value) => memory.set(key, String(value)),
    };
  }
}

function readEntries(storage) {
  try {
    // getItem itself can throw when site data is blocked.
    const parsed = JSON.parse(storage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e) => Array.isArray(e) && Number.isFinite(e[0]) && Number.isFinite(e[1]),
    );
  } catch {
    return [];
  }
}

function writeEntries(storage, entries) {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Quota or access errors: the estimate just won't persist.
  }
}

// Entries are [epochMs, weightedCalls]. Anything older than the longest window
// is dropped on every write so the log stays small.
export function createUsageLog(storage = defaultStorage(), now = Date.now) {
  function record(calls) {
    const t = now();
    const entries = readEntries(storage).filter(([time]) => inWindow(t - time, MONTH_MS));
    entries.push([t, calls]);
    writeEntries(storage, entries);
  }

  function totals() {
    const t = now();
    const entries = readEntries(storage);
    const result = {};
    for (const [name, windowMs] of Object.entries(WINDOWS_MS)) {
      result[name] = entries
        .filter(([time]) => inWindow(t - time, windowMs))
        .reduce((sum, [, calls]) => sum + calls, 0);
    }
    return result;
  }

  return { record, totals };
}

// --- reporting ---

// Console-ready text: this route's cost, then this browser's estimated use
// against each limit.
export function formatUsageReport({ label, calls, totals, limits = FREE_LIMITS }) {
  const lines = [`Open-Meteo: ${label} used ~${calls.toFixed(1)} calls.`];
  for (const name of Object.keys(limits)) {
    const used = totals[name] ?? 0;
    const left = Math.max(0, limits[name] - used);
    lines.push(
      `  ${name}: ~${used.toFixed(1)} of ${limits[name]} used, ~${left.toFixed(1)} left`,
    );
  }
  lines.push("  (estimate for this browser only; limits are per IP address)");
  return lines.join("\n");
}

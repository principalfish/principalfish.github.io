// Open-Meteo client: builds forecast/elevation URLs, fetches them in chunks and
// normalises the JSON into flat typed arrays. The only module that talks to
// Open-Meteo; everything downstream (weather.js) works on normalised locations.
//
// `fetchImpl` is injectable so tests (and Node) can supply their own fetch.
// Requests are sent one after another, never in parallel, to stay well inside
// Open-Meteo's free-tier rate limit.

import { HOURLY_VARS } from "./config.js";

export const FORECAST_URL = "https://api.open-meteo.com/v1/forecast";
export const ELEVATION_URL = "https://api.open-meteo.com/v1/elevation";

// Locations per forecast request. Keeps each URL comfortably short (~2 kB with
// every hourly variable) and each response small.
export const CHUNK_SIZE = 50;

// The elevation API's own per-call coordinate limit.
export const ELEVATION_CHUNK_SIZE = 100;

// Per-request timeout. Open-Meteo normally answers in well under a second; a
// request still hanging after this is reported rather than left spinning.
export const REQUEST_TIMEOUT_MS = 20000;

// 5 dp is ~1 m — far finer than any model grid cell, short enough for the URL.
const COORD_DECIMALS = 5;

const HOUR_MS = 3600 * 1000;

// --- URL building ---

// null (Open-Meteo's gap marker) and anything else non-numeric -> NaN, so
// downstream maths propagates the gap instead of reading it as 0.
const toNumber = (v) => (typeof v === "number" ? v : NaN);

// "54.45400" -> "54.454": Number() drops the trailing zeros toFixed() adds.
const formatCoord = (deg) => String(Number(deg.toFixed(COORD_DECIMALS)));

// Epoch ms -> "YYYY-MM-DDTHH:00" in UTC, the format start_hour/end_hour take
// (read in `timezone`, which is always GMT here).
const formatHour = (ms) => `${new Date(ms).toISOString().slice(0, 13)}:00`;

function coordParams(points) {
  return [
    `latitude=${points.map((p) => formatCoord(p.lat)).join(",")}`,
    `longitude=${points.map((p) => formatCoord(p.lon)).join(",")}`,
  ];
}

// Forecast URL for `samples` (`{ lat, lon }`) covering [startHour, endHour]
// (epoch ms; start floored and end ceiled to whole UTC hours, both inclusive).
//
// `elevation=nan` turns off Open-Meteo's own downscaling to the point's
// elevation, so the 2 m / 10 m values refer to the model cell's terrain height
// (returned as `elevation`); weather.js does the altitude correction itself
// from the pressure levels. `cell_selection=land` avoids sea cells on coasts.
//
// Every value is a number or a fixed identifier, so the query is built by hand
// to keep the commas readable instead of URLSearchParams' %2C.
export function buildForecastUrl(samples, { startHour, endHour }) {
  if (!Number.isFinite(startHour) || !Number.isFinite(endHour) || startHour > endHour) {
    throw new Error("Forecast window needs a finite startHour no later than endHour");
  }
  const params = [
    ...coordParams(samples),
    `elevation=${samples.map(() => "nan").join(",")}`,
    "cell_selection=land",
    "models=ukmo_seamless",
    "timezone=GMT",
    `hourly=${HOURLY_VARS.join(",")}`,
    `start_hour=${formatHour(Math.floor(startHour / HOUR_MS) * HOUR_MS)}`,
    `end_hour=${formatHour(Math.ceil(endHour / HOUR_MS) * HOUR_MS)}`,
  ];
  return `${FORECAST_URL}?${params.join("&")}`;
}

export function buildElevationUrl(points) {
  return `${ELEVATION_URL}?${coordParams(points).join("&")}`;
}

// --- requests ---

function chunk(items, size) {
  const chunks = [];
  for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
  return chunks;
}

// A caller's abort (a newer route superseding this one) is reported as its own
// error, named "AbortError" so callers can tell it from a timeout and ignore it.
function abortedError(what, cause) {
  const err = new Error(`Open-Meteo ${what} request was aborted`, { cause });
  err.name = "AbortError";
  return err;
}

// Rethrows a caller abort or a timed-out request (or body read) as one clear
// Error. AbortSignal.timeout rejects with a TimeoutError; some fetch
// implementations surface it as a plain AbortError, so the caller's own signal
// is checked first.
function throwIfAbortedOrTimeout(err, what, signal) {
  if (signal?.aborted) throw abortedError(what, err);
  if (err?.name === "TimeoutError" || err?.name === "AbortError") {
    throw new Error(
      `Open-Meteo ${what} request timed out after ${REQUEST_TIMEOUT_MS / 1000} s`,
      { cause: err },
    );
  }
}

// --- abort signals ---

// Feature-detected so older browsers (AbortSignal.any: Chrome < 116, Firefox
// < 124, Safari < 17.4; AbortSignal.timeout: Safari < 16) still work. Each
// helper returns { signal, cleanup }; call cleanup once the request settles.

const noCleanup = () => {};

// Aborts with a TimeoutError after `ms`.
function timeoutSignal(ms) {
  if (typeof AbortSignal.timeout === "function") {
    return { signal: AbortSignal.timeout(ms), cleanup: noCleanup };
  }
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new DOMException("The operation timed out", "TimeoutError")),
    ms,
  );
  return { signal: controller.signal, cleanup: () => clearTimeout(timer) };
}

// Aborts as soon as any of `signals` does, with that signal's reason. The
// fallback's listeners are removed on cleanup, so a long-lived caller signal
// doesn't collect one per request.
function anySignal(signals) {
  if (typeof AbortSignal.any === "function") {
    return { signal: AbortSignal.any(signals), cleanup: noCleanup };
  }
  const controller = new AbortController();
  const listeners = [];
  for (const source of signals) {
    if (source.aborted) {
      controller.abort(source.reason);
      break;
    }
    const onAbort = () => controller.abort(source.reason);
    source.addEventListener("abort", onAbort, { once: true });
    listeners.push([source, onAbort]);
  }
  const cleanup = () => {
    for (const [source, onAbort] of listeners) source.removeEventListener("abort", onAbort);
  };
  return { signal: controller.signal, cleanup };
}

// The per-request signal: the timeout, plus the caller's signal if given.
function requestSignal(callerSignal) {
  const timeout = timeoutSignal(REQUEST_TIMEOUT_MS);
  if (!callerSignal) return timeout;
  const combined = anySignal([callerSignal, timeout.signal]);
  return {
    signal: combined.signal,
    cleanup: () => {
      combined.cleanup();
      timeout.cleanup();
    },
  };
}

// GET `url` and return its JSON, turning every failure into one descriptive
// Error. Open-Meteo reports bad requests as `{ error: true, reason }` (HTTP
// 400), so the reason is surfaced whenever the body has one.
//
// `signal` (optional) is the caller's abort signal, combined with the
// per-request timeout. `onSent` runs once the request has been handed to
// fetch, before its response, so a caller can count the quota spent even when
// the request then fails.
async function getJson(url, fetchImpl, what, { signal, onSent } = {}) {
  if (signal?.aborted) throw abortedError(what, signal.reason);
  const request = requestSignal(signal);
  try {
    return await sendAndRead(url, fetchImpl, what, { signal, onSent, combined: request.signal });
  } finally {
    request.cleanup();
  }
}

async function sendAndRead(url, fetchImpl, what, { signal, onSent, combined }) {
  // The async wrapper turns a synchronous throw from fetchImpl into a
  // rejection, so onSent still runs in order and the catch below sees it.
  const pending = (async () => fetchImpl(url, { signal: combined }))();
  onSent?.();
  let res;
  try {
    res = await pending;
  } catch (err) {
    throwIfAbortedOrTimeout(err, what, signal);
    throw new Error(`Could not reach Open-Meteo for ${what}: ${err.message}`, { cause: err });
  }
  if (res.status === 429) {
    throw new Error(`Open-Meteo rate limited the ${what} request, try again in a minute`);
  }
  let body = null;
  try {
    body = await res.json();
  } catch (err) {
    // The timeout signal also covers reading the body, so a timeout after the
    // headers arrive surfaces here.
    throwIfAbortedOrTimeout(err, what, signal);
    // Otherwise a non-JSON body (e.g. a proxy's HTML error page): reported below.
  }
  const reason = body && body.error ? `: ${body.reason ?? "unknown error"}` : "";
  if (!res.ok) throw new Error(`Open-Meteo ${what} request failed (HTTP ${res.status})${reason}`);
  if (reason) throw new Error(`Open-Meteo ${what} request failed${reason}`);
  if (body === null) throw new Error(`Open-Meteo ${what} response was not JSON`);
  return body;
}

// Hourly forecast for every sample over `range` ({ startHour, endHour }, epoch
// ms). Returns one normalised location (see normaliseLocation) per sample, in
// sample order.
//
// Options: `signal` aborts the remaining requests (rejecting with an Error
// named "AbortError"); `onRequest({ locations })` is called as each chunk's
// request is sent, with that chunk's location count, for usage accounting.
export async function fetchForecast(samples, range, fetchImpl = fetch, options = {}) {
  const { signal, onRequest } = options;
  const locations = [];
  for (const part of chunk(samples, CHUNK_SIZE)) {
    const json = await getJson(buildForecastUrl(part, range), fetchImpl, "forecast", {
      signal,
      onSent: () => onRequest?.({ locations: part.length }),
    });
    // A single-location request returns a bare object rather than an array.
    const list = Array.isArray(json) ? json : [json];
    if (list.length !== part.length) {
      throw new Error(
        `Open-Meteo returned ${list.length} forecast locations, expected ${part.length}`,
      );
    }
    // Each location carries its 0-based index in the request as `location_id`
    // (omitted on the first, and on a single-location response). Results are
    // matched to samples by position, so check the API kept the order.
    list.forEach((loc, i) => {
      if ((loc?.location_id ?? 0) !== i) {
        throw new Error(`Open-Meteo returned forecast locations out of order (at position ${i})`);
      }
    });
    locations.push(...list.map(normaliseLocation));
  }
  return locations;
}

// Terrain elevation (m) for each `{ lat, lon }` point, in order. NaN where the
// API has no value. Options as for fetchForecast.
export async function fetchElevations(points, fetchImpl = fetch, options = {}) {
  const { signal, onRequest } = options;
  const elevations = [];
  for (const part of chunk(points, ELEVATION_CHUNK_SIZE)) {
    const json = await getJson(buildElevationUrl(part), fetchImpl, "elevation", {
      signal,
      onSent: () => onRequest?.({ locations: part.length }),
    });
    const list = json.elevation;
    if (!Array.isArray(list) || list.length !== part.length) {
      const got = Array.isArray(list) ? list.length : 0;
      throw new Error(`Open-Meteo returned ${got} elevations, expected ${part.length}`);
    }
    elevations.push(...list.map(toNumber));
  }
  return elevations;
}

// --- normalising ---

// One Open-Meteo location object ->
//
//   cellLat, cellLon  centre of the grid cell the model used
//   cellElev          that cell's terrain height (m): what 2 m / 10 m refer to
//   timesMs           Float64Array of epoch ms, one per hourly step
//   vars              { [HOURLY_VARS name]: Float64Array }, parallel to timesMs
//
// Throws if the times are unparseable, or any requested variable is missing or
// not parallel to the times, rather than letting weather.js read past the end.
export function normaliseLocation(json) {
  const hourly = json && json.hourly;
  if (!hourly || !Array.isArray(hourly.time)) {
    throw new Error("Open-Meteo location has no hourly times");
  }
  // Times are ISO without a zone; with timezone=GMT they are UTC.
  const timesMs = Float64Array.from(hourly.time, (t) => Date.parse(`${t}Z`));
  if (timesMs.some(Number.isNaN)) {
    throw new Error("Open-Meteo location has unparseable hourly times");
  }

  const vars = {};
  for (const name of HOURLY_VARS) {
    const values = hourly[name];
    if (!Array.isArray(values) || values.length !== timesMs.length) {
      throw new Error(`Open-Meteo location has a missing or mis-sized hourly ${name}`);
    }
    vars[name] = Float64Array.from(values, toNumber);
  }
  return {
    cellLat: toNumber(json.latitude),
    cellLon: toNumber(json.longitude),
    cellElev: toNumber(json.elevation),
    timesMs,
    vars,
  };
}

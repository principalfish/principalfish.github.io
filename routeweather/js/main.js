// Route weather page controller. Owns the single `state` object, wires the
// views to it and is the only module that starts Open-Meteo requests.
//
// Flow: a GPX file is parsed, built into a route and sampled, then the route's
// time zone, the forecast (and, without GPX elevation, the terrain elevation)
// are fetched ONCE for the whole available forecast window. A full route costs ~345 weighted
// calls against a 600/minute limit, so changing activity, speed or start only
// recomputes arrival times and per-sample weather from the stored locations,
// and changing units only re-renders. A new file (or Retry) aborts whatever the
// previous load still had in flight.
//
// Views: `addView(update)` registers `update(state, reason)`, called on EVERY
// render() in registration order. `reason` says what prompted the render:
//
//   "load"     the route changed: a file was loaded or cleared, the samples
//              got their elevations (state.route / state.samples), or the
//              route's time zone became known (state.timeZone)
//   "results"  the forecast arrived: locations, cells, times, results
//   "inputs"   activity, speed or start changed: times/results recomputed
//   "units"    display units changed only (the preset or a per-quantity
//              toggle; state.units is a units.js units set, interned, so it
//              is a new object exactly when some unit changed)
//   "status"   only messages changed (status line, Retry, plan error)
//
// State objects are replaced, never mutated in place, so a view that does
// real work (a map, charts) should compare identities against what it drew
// last (state.route, state.results, state.times, state.units) and skip work
// when they are unchanged; `reason` is a hint for the same purpose.
//
// `init` has no side effects until called; `entry.js` calls it on the real
// page and tests call it with a jsdom document and a mocked fetch.

import {
  ACTIVITIES, HOURLY_VARS, OPEN_METEO_ATTRIBUTION, SAMPLING, SPEED_RANGE_KMH,
} from "./config.js";
import { parseGpx } from "./gpx.js";
import { arrivalTimes, bearingDeg, buildRoute, haversineKm, sampleRoute } from "./route.js";
import { fetchElevations, fetchForecast, fetchTimeZone } from "./openmeteo.js";
import { weatherForSample } from "./weather.js";
import { isCustomised, kmhToSpeed, resolveUnits, speedToKmh } from "./units.js";
import { readState, writeState } from "./urlstate.js";
import {
  createUsageLog, elevationCallWeight, forecastCallWeight, formatUsageReport,
} from "./usage.js";
import {
  forecastSpan, forecastWindow, fromLocalInputValue, HOUR_MS, latestStartMs, MINUTE_MS,
  nextWholeHour, windowHours,
} from "./schedule.js";
import { formatDateTime, formatDuration } from "./display.js";
import { isValidTimeZone } from "./zone.js";
import { createFormUI } from "./form-view.js";
import { createPointsTableUI, createSummaryUI } from "./results-view.js";
import { createMapUI } from "./map-view.js";
import { createPlaybackController } from "./playback-controller.js";

// Larger GPX files are rejected before reading: a multi-day track is a few MB,
// and parsing hundreds of MB of XML would hang the tab.
export const MAX_GPX_BYTES = 25 * 1024 * 1024;

const SPEED_UNIT = { metric: "km/h", imperial: "mph" };

// --- helpers ---

// File.text() everywhere modern; FileReader where it's missing (jsdom).
function readFileText(file, win) {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new win.FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file"));
    reader.readAsText(file);
  });
}

// URL start: ISO in UTC, minute precision ("2026-10-26T10:00Z").
const toIsoStart = (ms) => new Date(ms).toISOString().replace(/:00\.000Z$/, "Z");

// Where each sample's forecast comes from: the model grid cell centre and its
// offset from the route point (km, initial bearing from point to cell).
function cellsFor(samples, locations) {
  return locations.map((loc, i) => {
    const cell = { lat: loc.cellLat, lon: loc.cellLon };
    const known = Number.isFinite(cell.lat) && Number.isFinite(cell.lon);
    return {
      ...cell,
      offsetKm: known ? haversineKm(samples[i], cell) : NaN,
      offsetBearingDeg: known ? bearingDeg(samples[i], cell) : NaN,
    };
  });
}

function speedRangeText(units) {
  const show = (kmh) => Math.round(kmhToSpeed(kmh, units) * 10) / 10;
  return `${show(SPEED_RANGE_KMH.min)}–${show(SPEED_RANGE_KMH.max)} ${SPEED_UNIT[units.system]}`;
}

// A URL start is only honoured if it is still ahead and inside the window;
// null when it isn't (the caller then defaults to the next whole hour).
function urlStartMs(isoStart, nowMs) {
  const ms = isoStart === null ? NaN : Date.parse(isoStart);
  return ms >= nowMs && ms <= forecastWindow(nowMs).endHour ? ms : null;
}

const routeDurationMs = (route, speedKmh) => (route.totalDistKm / speedKmh) * HOUR_MS;

// The time zone lookup's weight: one location, one variable, one day.
const TIME_ZONE_CALLS = 1;

// --- init ---

export function init({
  doc = document,
  win = window,
  fetchImpl = fetch,
  now = Date.now,
  storage,
} = {}) {
  const $ = (id) => doc.getElementById(id);
  const usage = createUsageLog(storage, now);
  const fromUrl = readState(win.location.search);
  const linkedStartMs = urlStartMs(fromUrl.start, now());
  // True while the start is the page's own default (the next whole hour), not
  // one the user or a link chose: it is re-snapped to the next whole hour in
  // the route's zone once that is known (a +5:30 browser's default would
  // otherwise land on :30 in a whole-hour zone).
  let startIsDefault = linkedStartMs === null;

  const state = {
    // inputs (internal units: km/h, epoch ms)
    activity: fromUrl.activity,
    speedKmh: fromUrl.speed, // NaN while the typed speed is invalid
    startMs: linkedStartMs ?? nextWholeHour(now()),
    // { system, wind, rain, temp }: replaced, never mutated (resolveUnits)
    units: resolveUnits(fromUrl.units, fromUrl),
    // per GPX load
    // The route's IANA zone (first sample), used for every displayed time and
    // the start input; null (the browser's zone) until known or if the lookup
    // failed.
    timeZone: null,
    routeName: null,
    route: null, // buildRoute()
    samples: null, // sampleRoute(), ele filled from the elevation API if needed
    locations: null, // fetchForecast(), parallel to samples
    cells: null, // [{ lat, lon, offsetKm, offsetBearingDeg }], parallel to samples
    forecastStartMs: null, // first and last hour with data everywhere
    forecastEndMs: null,
    // per recompute
    nowMs: now(),
    times: null, // arrival epoch ms, parallel to samples
    results: null, // weatherForSample(), parallel to samples; null = nothing to show
    startBounds: { minMs: now(), maxMs: now() },
    // messages
    status: { kind: "info", message: "Choose a GPX file to get started." },
    retry: null, // null (no Retry) | "ready" | "busy"
    planError: null,
  };

  const views = [];
  const addView = (update) => views.push(update);
  const render = (reason) => views.forEach((update) => update(state, reason));

  // Aborted when a newer load or retry starts, so a superseded load stops
  // spending quota and can never overwrite newer state.
  let loadController = null;

  // Set once this route's elevation request has succeeded, so Retry never
  // repeats it — even when the API had no value for some points (left null).
  let elevationFetched = false;
  // Likewise the time zone lookup, once it has succeeded for this route.
  let zoneFetched = false;
  function beginLoad() {
    loadController?.abort();
    loadController = new AbortController();
    return loadController.signal;
  }

  // --- recompute (no network) ---

  function updateStartBounds() {
    const minMs = Math.floor(state.nowMs / MINUTE_MS) * MINUTE_MS;
    const lastMs = state.forecastEndMs ?? forecastWindow(state.nowMs).endHour;
    const durationMs = state.route && Number.isFinite(state.speedKmh)
      ? routeDurationMs(state.route, state.speedKmh)
      : 0;
    state.startBounds = { minMs, maxMs: Math.max(minMs, latestStartMs(lastMs, durationMs)) };
  }

  // Checks the plan against the forecast; an error message, or null if it fits.
  function planProblem(times) {
    const at = (ms) => formatDateTime(ms, state.timeZone);
    const startMs = times[0];
    const finishMs = times[times.length - 1];
    if (startMs < state.forecastStartMs) {
      return `The forecast starts at ${at(state.forecastStartMs)}; `
        + "pick a later start.";
    }
    if (finishMs <= state.forecastEndMs) return null;
    const ends = at(state.forecastEndMs);
    const latest = latestStartMs(state.forecastEndMs, finishMs - startMs);
    if (latest < Math.max(state.startBounds.minMs, state.forecastStartMs)) {
      return `At this speed the route takes ${formatDuration(finishMs - startMs)}, longer `
        + `than the forecast has left (it ends ${ends}). Try a higher speed.`;
    }
    return `You'd finish at ${at(finishMs)}, after the forecast ends (${ends}). `
      + `Latest valid start: ${at(latest)}.`;
  }

  function recompute() {
    state.nowMs = now();
    state.times = null;
    state.results = null;
    state.planError = null;
    updateStartBounds();

    if (!Number.isFinite(state.speedKmh)) {
      state.planError = `Enter a speed between ${speedRangeText(state.units)}.`;
      return;
    }
    if (!Number.isFinite(state.startMs)) {
      state.planError = "Enter a start date and time.";
      return;
    }
    // The input's `min` only guides the picker; typed values aren't held to it.
    if (state.startMs < state.startBounds.minMs) {
      const from = formatDateTime(state.startBounds.minMs, state.timeZone);
      state.planError = `Pick a start from ${from} onwards.`;
      return;
    }
    if (!state.locations) return;

    const { samples, locations } = state;
    const times = arrivalTimes(samples.map((s) => s.cumDistKm), state.speedKmh, state.startMs);
    state.planError = planProblem(times);
    if (state.planError) return;
    state.times = times;
    state.results = samples.map(
      (s, i) => weatherForSample(locations[i], s, times[i], s.bearingDeg),
    );
  }

  // --- fetching (once per GPX load) ---

  const setStatus = (kind, message) => { state.status = { kind, message }; };

  async function fetchWeather(signal) {
    const n = state.samples.length;
    const range = forecastWindow(now());
    // Usage is recorded per request as it is sent, so a failed or superseded
    // load still counts what it spent; the console report prints once per load.
    let spent = 0;
    const spend = (calls) => {
      usage.record(calls);
      spent += calls;
    };
    const options = { signal };

    try {
      // The zone first: one call, and every time shown from here on uses it.
      // A failed lookup isn't worth failing the load for; times then stay in
      // the browser's zone, and a Retry tries again.
      if (!zoneFetched) {
        setStatus("loading", "Looking up the route's time zone…");
        render("status");
        try {
          const zone = await fetchTimeZone(state.samples[0], fetchImpl, {
            ...options,
            onRequest: () => spend(TIME_ZONE_CALLS),
          });
          if (signal.aborted) return;
          if (isValidTimeZone(zone)) {
            zoneFetched = true;
            setTimeZone(zone);
          }
        } catch (err) {
          if (signal.aborted) return;
          console.warn(`Route weather: ${err.message}; showing times in your own time zone.`);
        }
      }

      // Elevation only when the GPX had none, and only until it has succeeded
      // once for this route (a Retry after a forecast failure skips it).
      if (!state.route.hasElevation && !elevationFetched) {
        setStatus("loading", `Fetching elevation for ${n} points…`);
        render("status");
        const elevations = await fetchElevations(state.samples, fetchImpl, {
          ...options,
          onRequest: ({ locations }) => spend(elevationCallWeight(locations)),
        });
        if (signal.aborted) return;
        state.samples = state.samples.map((s, i) => ({
          ...s,
          ele: Number.isFinite(elevations[i]) ? elevations[i] : null,
        }));
        elevationFetched = true;
        render("load");
      }

      setStatus("loading", `Fetching the forecast for ${n} locations…`);
      render("status");
      const weigh = ({ locations }) => forecastCallWeight({
        locations,
        variables: HOURLY_VARS.length,
        hours: windowHours(range),
      });
      const locations = await fetchForecast(state.samples, range, fetchImpl, {
        ...options,
        onRequest: (sent) => spend(weigh(sent)),
      });
      if (signal.aborted) return;

      const span = forecastSpan(locations);
      state.locations = locations;
      state.cells = cellsFor(state.samples, locations);
      state.forecastStartMs = span.startMs;
      state.forecastEndMs = span.endMs;
      state.retry = null;
      const until = formatDateTime(span.endMs, state.timeZone);
      setStatus("info", `Forecast loaded for ${n} points, up to ${until}.`);
      recompute();
      render("results");
    } catch (err) {
      if (signal.aborted) return;
      setStatus("error", err.message);
      state.retry = "ready";
      render("status");
    } finally {
      if (spent > 0) {
        const label = `this route (${n} points)`;
        console.info(formatUsageReport({ label, calls: spent, totals: usage.totals() }));
      }
    }
  }

  // Switches every displayed time (and the start input) to `zone`, or back to
  // the browser's for null. The start instant is kept, except a default start
  // re-snaps to the next whole hour on the new clock.
  function setTimeZone(zone) {
    if (zone === state.timeZone) return;
    state.timeZone = zone;
    if (startIsDefault) state.startMs = nextWholeHour(now(), zone);
    form.syncStart(state);
    recompute();
    render("load");
  }

  function clearRoute() {
    elevationFetched = false;
    zoneFetched = false;
    Object.assign(state, {
      routeName: null,
      route: null,
      samples: null,
      locations: null,
      cells: null,
      forecastStartMs: null,
      forecastEndMs: null,
      retry: null,
    });
  }

  async function loadFile(file) {
    const signal = beginLoad();
    clearRoute();
    // Until the new route's zone is known, back to the browser's.
    if (state.timeZone !== null) {
      state.timeZone = null;
      form.syncStart(state);
    }
    recompute();
    if (file.size > MAX_GPX_BYTES) {
      const mb = (file.size / 1024 / 1024).toFixed(1);
      setStatus("error", `That file is ${mb} MB; GPX files over 25 MB aren't supported.`);
      render("load");
      return;
    }

    setStatus("loading", `Reading ${file.name}…`);
    render("load");
    let parsed;
    let route;
    let samples;
    try {
      const text = await readFileText(file, win);
      if (signal.aborted) return;
      parsed = parseGpx(text);
      route = buildRoute(parsed.points);
      samples = sampleRoute(route, SAMPLING);
    } catch (err) {
      if (signal.aborted) return;
      setStatus("error", `Couldn't load that GPX file: ${err.message}`);
      render("status");
      return;
    }

    state.routeName = parsed.name ?? file.name;
    state.route = route;
    state.samples = samples;
    recompute();
    render("load");
    await fetchWeather(signal);
  }

  function retry() {
    if (!state.samples || state.retry !== "ready") return;
    state.retry = "busy";
    // Focus moves off the now-disabled button so keyboard users keep their place.
    form.focusStatus();
    fetchWeather(beginLoad());
  }

  // --- input handling ---

  // Written on committed changes only (radios, and `change` on the text
  // inputs), not per keystroke, so the history isn't rewritten as you type.
  function writeUrl() {
    const { system, wind, rain, temp } = state.units;
    win.history.replaceState(null, "", writeState({
      activity: state.activity,
      speed: state.speedKmh,
      start: Number.isFinite(state.startMs) ? toIsoStart(state.startMs) : null,
      units: system,
      wind,
      rain,
      temp,
    }));
  }

  function onChange(event) {
    switch (event.type) {
      case "file":
        loadFile(event.file);
        return;
      case "retry":
        retry();
        return;
      case "commit":
        writeUrl();
        return;
      case "activity": {
        state.activity = event.value;
        const preset = ACTIVITIES[event.value].defaultSpeedKmh;
        if (preset !== null) {
          state.speedKmh = preset;
          form.syncSpeed(state);
        }
        recompute();
        writeUrl();
        render("inputs");
        return;
      }
      case "speed":
        // Typing a speed makes it a custom activity, even if it matches a preset.
        state.activity = "custom";
        state.speedKmh = speedToKmh(event.value, state.units);
        recompute();
        render("inputs");
        return;
      case "start":
        startIsDefault = false;
        state.startMs = fromLocalInputValue(event.value, state.timeZone);
        recompute();
        render("inputs");
        return;
      case "units": {
        // Picking a preset resets wind, rain and temperature to its defaults.
        const units = resolveUnits(event.value);
        if (units === state.units) return; // re-picked with nothing to reset
        state.units = units;
        if (Number.isFinite(state.speedKmh)) form.syncSpeed(state);
        // Local maths only; keeps the speed-range message in the new units.
        recompute();
        writeUrl();
        render("units");
        return;
      }
      case "unit": {
        // One quantity overridden; the preset (and so distance, elevation and
        // the speed input) stays. Nothing to recompute: only the speed-range
        // message depends on units, and it follows the preset.
        const units = resolveUnits(state.units.system, {
          ...state.units,
          [event.quantity]: event.value,
        });
        if (units === state.units) return;
        state.units = units;
        writeUrl();
        render("units");
        return;
      }
      default:
        // Unknown events are ignored.
    }
  }

  // --- views ---

  const form = createFormUI(doc, onChange);
  addView(form.update);
  addView(createSummaryUI(doc).update);
  // Panel visibility: the map as soon as there is a route (it can draw it
  // while the forecast loads), charts and playback once there are results.
  // Only touched when visibility actually changes.
  const show = (id, visible) => {
    if ($(id).hidden === visible) $(id).hidden = !visible;
  };
  addView((s) => {
    show("mapPanel", Boolean(s.route));
    show("chartsPanel", Boolean(s.results));
    show("playback", Boolean(s.results));
  });
  addView(createPointsTableUI(doc).update);
  // After the visibility view, so the map's panel is already shown when it
  // first measures itself.
  const mapView = createMapUI($("mapPanel"));
  addView(mapView.update);
  // After the map, so each playback tick lands after the map's own update.
  const playback = createPlaybackController({
    mapView,
    chartsEl: $("chartsPanel"),
    playbackEl: $("playback"),
  });
  addView(playback.update);

  // A constant string from config.js, not user input.
  $("attribution").innerHTML = OPEN_METEO_ATTRIBUTION;

  form.syncSpeed(state);
  form.syncStart(state);
  // A link that overrides a unit shows the toggles it set.
  if (isCustomised(state.units)) form.openCustomise();
  recompute();
  render("load");

  return { state, addView, render, recompute, mapView, playback };
}

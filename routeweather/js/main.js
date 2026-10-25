// Route weather page controller. Owns the single `state` object, wires the
// views to it and is the only module that starts Open-Meteo requests.
//
// Flow: a GPX file is parsed, built into a route and sampled, then the
// forecast (and, without GPX elevation, the terrain elevation) is fetched ONCE
// for the whole available forecast window. A full route costs ~345 weighted
// calls against a 600/minute limit, so changing activity, speed or start only
// recomputes arrival times and per-sample weather from the stored locations,
// and changing units only re-renders. A new file (or Retry) aborts whatever the
// previous load still had in flight.
//
// Views: `addView(update)` registers `update(state, reason)`, called on EVERY
// render() in registration order. `reason` says what prompted the render:
//
//   "load"     the route changed: a file was loaded or cleared, or the samples
//              got their elevations (state.route / state.samples)
//   "results"  the forecast arrived: locations, cells, times, results
//   "inputs"   activity, speed or start changed: times/results recomputed
//   "units"    display units changed only
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
import { fetchElevations, fetchForecast } from "./openmeteo.js";
import { weatherForSample } from "./weather.js";
import { kmhToSpeed, speedToKmh } from "./units.js";
import { readState, writeState } from "./urlstate.js";
import {
  createUsageLog, elevationCallWeight, forecastCallWeight, formatUsageReport,
} from "./usage.js";
import {
  forecastSpan, forecastWindow, fromLocalInputValue, HOUR_MS, latestStartMs, MINUTE_MS,
  nextWholeHour, windowHours,
} from "./schedule.js";
import { formatDateTime, formatDuration } from "./display.js";
import { createFormUI } from "./form-view.js";
import { createPointsTableUI, createSummaryUI } from "./results-view.js";

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
  return `${show(SPEED_RANGE_KMH.min)}–${show(SPEED_RANGE_KMH.max)} ${SPEED_UNIT[units]}`;
}

// A URL start is only honoured if it is still ahead and inside the window.
function initialStartMs(isoStart, nowMs) {
  const ms = isoStart === null ? NaN : Date.parse(isoStart);
  const ok = ms >= nowMs && ms <= forecastWindow(nowMs).endHour;
  return ok ? ms : nextWholeHour(nowMs);
}

const routeDurationMs = (route, speedKmh) => (route.totalDistKm / speedKmh) * HOUR_MS;

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

  const state = {
    // inputs (internal units: km/h, epoch ms)
    activity: fromUrl.activity,
    speedKmh: fromUrl.speed, // NaN while the typed speed is invalid
    startMs: initialStartMs(fromUrl.start, now()),
    units: fromUrl.units,
    // per GPX load
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
    const startMs = times[0];
    const finishMs = times[times.length - 1];
    if (startMs < state.forecastStartMs) {
      return `The forecast starts at ${formatDateTime(state.forecastStartMs)}; `
        + "pick a later start.";
    }
    if (finishMs <= state.forecastEndMs) return null;
    const ends = formatDateTime(state.forecastEndMs);
    const latest = latestStartMs(state.forecastEndMs, finishMs - startMs);
    if (latest < Math.max(state.startBounds.minMs, state.forecastStartMs)) {
      return `At this speed the route takes ${formatDuration(finishMs - startMs)}, longer `
        + `than the forecast has left (it ends ${ends}). Try a higher speed.`;
    }
    return `You'd finish at ${formatDateTime(finishMs)}, after the forecast ends (${ends}). `
      + `Latest valid start: ${formatDateTime(latest)}.`;
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
      state.planError = `Pick a start from ${formatDateTime(state.startBounds.minMs)} onwards.`;
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
      setStatus("info", `Forecast loaded for ${n} points, up to ${formatDateTime(span.endMs)}.`);
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

  function clearRoute() {
    elevationFetched = false;
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
    win.history.replaceState(null, "", writeState({
      activity: state.activity,
      speed: state.speedKmh,
      start: Number.isFinite(state.startMs) ? toIsoStart(state.startMs) : null,
      units: state.units,
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
        state.startMs = fromLocalInputValue(event.value);
        recompute();
        render("inputs");
        return;
      case "units":
        state.units = event.value;
        if (Number.isFinite(state.speedKmh)) form.syncSpeed(state);
        // Local maths only; keeps the speed-range message in the new units.
        recompute();
        writeUrl();
        render("units");
        return;
      default:
        // Unknown events are ignored.
    }
  }

  // --- views ---

  const form = createFormUI(doc, onChange);
  addView(form.update);
  addView(createSummaryUI(doc).update);
  // Mount points for pieces 6/7: the map as soon as there is a route (it can
  // draw it while the forecast loads), charts and playback once there are
  // results. Only touched when visibility actually changes.
  const show = (id, visible) => {
    if ($(id).hidden === visible) $(id).hidden = !visible;
  };
  addView((s) => {
    show("mapPanel", Boolean(s.route));
    show("chartsPanel", Boolean(s.results));
    show("playback", Boolean(s.results));
  });
  addView(createPointsTableUI(doc).update);

  // A constant string from config.js, not user input.
  $("attribution").innerHTML = OPEN_METEO_ATTRIBUTION;

  form.syncSpeed(state);
  form.syncStart(state);
  recompute();
  render("load");

  return { state, addView, render, recompute };
}

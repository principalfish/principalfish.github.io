// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from "vitest";

import { init, MAX_GPX_BYTES } from "../js/main.js";
import { ELEVATION_URL, FORECAST_URL } from "../js/openmeteo.js";
import { HOURLY_VARS } from "../js/config.js";
import {
  forecastWindow, latestStartMs, MINUTE_MS, nextWholeHour, toLocalInputValue,
} from "../js/schedule.js";
import { formatDateTime } from "../js/display.js";

// jsdom replaces the global URL, which node:fs rejects; resolve to a path.
const here = (rel) => fileURLToPath(new URL(rel, import.meta.url).href);

// Real Open-Meteo response (Lake District + London), 2026-10-26 09:00–14:00
// UTC (cloud_cover, sunshine_duration and relative_humidity_2m added by
// hand). The mock below repeats its six hours across whatever window is asked
// for, so the page's full 7-day request gets a full 7-day answer.
const FIXTURE = JSON.parse(readFileSync(here("./fixtures/forecast-2loc.json"), "utf8"));

const PAGE = readFileSync(here("../index.html"), "utf8");
const BODY = /<body[^>]*>([\s\S]*)<\/body>/.exec(PAGE)[1]
  .replace(/<script[\s\S]*?<\/script>/g, "");

// Inside the fixture's day: the forecast window is 09:00 Oct 26 to 09:00 Nov 2.
const NOW = Date.UTC(2026, 9, 26, 9, 20);
const HOUR_MS = 3600 * 1000;
const WINDOW = forecastWindow(NOW);

// Every test runs in each zone (with its UTC offset at NOW, in minutes as
// getTimezoneOffset reports it): a UTC-only run would hide local/UTC mix-ups.
const ZONES = [
  ["Europe/London", 0],
  ["America/New_York", 240],
  ["Asia/Kolkata", -330],
];

const listParam = (url, name) => url.searchParams.get(name).split(",").map(Number);
const parseHour = (text) => Date.parse(`${text}Z`);
const isoHour = (ms) => new Date(ms).toISOString().slice(0, 16);

function syntheticLocation(lat, lon, i, times, { leadingNulls, trailingNulls }) {
  const base = FIXTURE[i % FIXTURE.length];
  const n = base.hourly.time.length;
  const hourly = { time: times.map(isoHour) };
  for (const name of HOURLY_VARS) hourly[name] = times.map((_, t) => base.hourly[name][t % n]);
  // Open-Meteo pads hours a model run hasn't reached with nulls.
  hourly.temperature_2m = hourly.temperature_2m.map(
    (v, t) => (t < leadingNulls || t >= times.length - trailingNulls ? null : v),
  );
  // Cell centre a little off the requested point, like a real model grid.
  const loc = {
    latitude: lat + 0.004, longitude: lon - 0.006, elevation: base.elevation, hourly,
  };
  // Like the real API: the first location has no location_id.
  return i === 0 ? loc : { ...loc, location_id: i };
}

// Mock fetch serving forecast and elevation requests. Counts each kind and
// keeps every request's signal. Options:
//   failForecast   answer the first N forecast requests with HTTP 429
//   holdFirst      "forecast" | "elevation": hold the first such response
//                  until release()
//   maxHours       serve at most this many hours from the requested start
//   leadingNulls / trailingNulls   null temperature_2m at the window's ends
//   nullElevation  the elevation API has no value for the first point
//   timeZone       the zone the time zone lookup answers with (default: the
//                  test's own TZ, so times read as before); null fails it
// Time zone lookups are counted in `zoneCalls`, not `calls`.
function mockFetch({
  failForecast = 0, holdFirst = null, maxHours = Infinity, leadingNulls = 0, trailingNulls = 0,
  nullElevation = false, timeZone = process.env.TZ,
} = {}) {
  const calls = { forecast: 0, elevation: 0 };
  const zoneCalls = [];
  const signals = [];
  let failuresLeft = failForecast;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const impl = async (href, options) => {
    signals.push(options?.signal);
    const url = new URL(href);
    const lats = listParam(url, "latitude");
    const lons = listParam(url, "longitude");
    if (href.startsWith(ELEVATION_URL)) {
      calls.elevation++;
      if (holdFirst === "elevation" && calls.elevation === 1) await held;
      const elevation = lats.map((_, i) => (nullElevation && i === 0 ? null : 321));
      return new Response(JSON.stringify({ elevation }));
    }
    if (!href.startsWith(FORECAST_URL)) throw new Error(`unexpected URL ${href}`);
    if (url.searchParams.get("timezone") === "auto") {
      zoneCalls.push(href);
      if (timeZone === null) return new Response("{}", { status: 500 });
      const body = { latitude: lats[0], longitude: lons[0], timezone: timeZone };
      return new Response(JSON.stringify(body));
    }
    calls.forecast++;
    // Deliberately ignores the abort signal: proves the controller drops a
    // superseded response even when it still arrives.
    if (holdFirst === "forecast" && calls.forecast === 1) await held;
    if (failuresLeft > 0) {
      failuresLeft--;
      return new Response("{}", { status: 429 });
    }
    const start = parseHour(url.searchParams.get("start_hour"));
    const requestedEnd = parseHour(url.searchParams.get("end_hour"));
    const end = Math.min(requestedEnd, start + (maxHours - 1) * HOUR_MS);
    const times = [];
    for (let t = start; t <= end; t += HOUR_MS) times.push(t);
    const locations = lats.map(
      (lat, i) => syntheticLocation(lat, lons[i], i, times, { leadingNulls, trailingNulls }),
    );
    return new Response(JSON.stringify(locations.length === 1 ? locations[0] : locations));
  };
  return { impl, calls, zoneCalls, signals, release: () => release() };
}

// ~4.5 km east-west at 54.45°N; the default name proves textContent rendering.
// `name: null` leaves the track unnamed.
function gpxText({ withElevation = true, name = "Fell <b>loop</b>" } = {}) {
  const eles = [100, 160, 230, 280, 200, 140];
  const pts = eles.map((ele, i) => {
    const lon = (-3.2 + i * 0.014).toFixed(3);
    const eleTag = withElevation ? `<ele>${ele}</ele>` : "";
    return `<trkpt lat="54.45" lon="${lon}">${eleTag}</trkpt>`;
  }).join("");
  const nameTag = name === null
    ? ""
    : `<name>${name.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</name>`;
  return `<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">`
    + `<trk>${nameTag}<trkseg>${pts}</trkseg></trk></gpx>`;
}

const $ = (id) => document.getElementById(id);

const gpxFile = (text, name = "route.gpx") => (
  new File([text], name, { type: "application/gpx+xml" })
);

function chooseFile(file) {
  const input = $("gpxFile");
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new Event("change"));
}

function dropEvent(target, files) {
  const event = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { files } });
  target.dispatchEvent(event);
  return event;
}

function typeInto(id, value) {
  $(id).value = value;
  $(id).dispatchEvent(new Event("input"));
}

// `change` is what commits an edit into the URL.
function commit(id) {
  $(id).dispatchEvent(new Event("change"));
}

const checkedValue = (name) => document.querySelector(`input[name="${name}"]:checked`).value;
const clickRadio = (name, value) => (
  document.querySelector(`input[name="${name}"][value="${value}"]`).click()
);

async function waitForLoad(page) {
  await vi.waitFor(() => {
    expect(page.state.status.kind).not.toBe("loading");
    expect(page.state.routeName).not.toBeNull();
  });
}

async function loadRoute(page, options) {
  chooseFile(gpxFile(gpxText(options)));
  await waitForLoad(page);
}

// A Map-backed Storage stand-in for the usage log.
class MemoryStorage {
  constructor() { this.data = new Map(); }
  getItem(key) { return this.data.has(key) ? this.data.get(key) : null; }
  setItem(key, value) { this.data.set(key, String(value)); }
}

function startPage(options = {}) {
  window.history.replaceState(null, "", `/routeweather/${options.search ?? ""}`);
  document.body.innerHTML = BODY;
  const fetch = mockFetch(options.fetchOptions);
  const page = init({
    doc: document,
    win: window,
    fetchImpl: fetch.impl,
    now: () => NOW,
    storage: new MemoryStorage(),
  });
  return { page, fetch, calls: fetch.calls };
}

const usageReports = () => console.info.mock.calls
  .map(([text]) => String(text))
  .filter((text) => text.startsWith("Open-Meteo:"));

describe.each(ZONES)("route weather page (TZ=%s)", (zone, offsetMin) => {
  const originalTz = process.env.TZ;
  beforeAll(() => { process.env.TZ = zone; });
  afterAll(() => {
    if (originalTz === undefined) delete process.env.TZ;
    else process.env.TZ = originalTz;
  });
  beforeEach(() => {
    vi.spyOn(console, "info").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("really runs in the zone", () => {
    expect(new Date(NOW).getTimezoneOffset()).toBe(offsetMin);
  });

  // --- start-up ---

  it("starts with defaults: walk preset speed, next whole hour, results hidden", () => {
    const { page } = startPage();
    expect($("speedInput").value).toBe("5");
    expect(checkedValue("activity")).toBe("walk");
    expect($("startInput").value).toBe(toLocalInputValue(nextWholeHour(NOW)));
    expect($("summary").hidden).toBe(true);
    expect($("pointsDetails").hidden).toBe(true);
    expect($("mapPanel").hidden).toBe(true);
    expect($("attribution").querySelector("a").href).toContain("open-meteo.com");
    expect(page.state.results).toBeNull();
  });

  it("keeps the plan error rendered but empty, and the status as authored", () => {
    startPage();
    expect($("planError").hidden).toBe(false);
    expect($("planError").textContent).toBe("");
    expect($("status").textContent).toBe("Choose a GPX file to get started.");
  });

  it("shows no-drop outside the zone while dragging", () => {
    startPage();
    const dragover = (target) => {
      const event = new Event("dragover", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "dataTransfer", { value: { dropEffect: "copy" } });
      target.dispatchEvent(event);
      return event;
    };
    const outside = dragover(document.body);
    expect(outside.defaultPrevented).toBe(true);
    expect(outside.dataTransfer.dropEffect).toBe("none");
    expect(dragover($("dropZone").querySelector(".drop-title")).dataTransfer.dropEffect)
      .toBe("copy");
  });

  it("bounds the start input from now to the end of the forecast", () => {
    startPage();
    expect($("startInput").min).toBe(toLocalInputValue(NOW));
    expect($("startInput").max).toBe(toLocalInputValue(WINDOW.endHour));
  });

  it("reads activity, speed, start and units from the URL", () => {
    const startIso = "2026-10-27T08:00Z";
    const search = `?activity=cycle&speed=16.09344&start=${startIso}&units=imperial`;
    const { page } = startPage({ search });
    expect(page.state.activity).toBe("cycle");
    expect($("speedInput").value).toBe("10");
    expect(page.state.startMs).toBe(Date.parse(startIso));
    expect($("startInput").value).toBe(toLocalInputValue(Date.parse(startIso)));
    expect($("speedUnit").textContent).toBe("mph");
  });

  it("ignores a URL start in the past or beyond the forecast", () => {
    const past = startPage({ search: "?start=2026-10-25T08:00Z" });
    expect(past.page.state.startMs).toBe(nextWholeHour(NOW));
    const late = startPage({ search: "?start=2026-11-05T08:00Z" });
    expect(late.page.state.startMs).toBe(nextWholeHour(NOW));
  });

  // --- loading ---

  it("loads a GPX, fetches once and renders the summary and one row per sample", async () => {
    const { page, calls } = startPage();
    await loadRoute(page);

    expect(calls).toEqual({ forecast: 1, elevation: 0 });
    expect(page.state.status.kind).toBe("info");
    expect($("routeName").textContent).toBe("Fell <b>loop</b>");
    expect($("routeName").querySelector("b")).toBeNull();
    expect($("summary").hidden).toBe(false);
    expect($("summaryList").textContent).toContain("Distance");

    const rows = $("pointsBody").querySelectorAll("tr");
    expect(page.state.samples.length).toBeGreaterThan(2);
    expect(rows).toHaveLength(page.state.samples.length);
    expect(page.state.results).toHaveLength(page.state.samples.length);
    expect(page.state.times).toHaveLength(page.state.samples.length);
    expect($("pointsDetails").hidden).toBe(false);
    // Shown, but left collapsed until opened.
    expect($("pointsDetails").open).toBe(false);
    expect($("chartsPanel").hidden).toBe(false);
    expect($("playback").hidden).toBe(false);
    expect(rows[0].textContent).toContain("Start");
  });

  it("draws the route map once and keeps it across recomputes and reloads", async () => {
    const { page } = startPage();
    await loadRoute(page);

    const panel = $("mapPanel");
    expect(panel.hidden).toBe(false);
    expect(panel.querySelectorAll(".leaflet-container")).toHaveLength(1);
    expect(panel.querySelectorAll(".leaflet-rwCells-pane path"))
      .toHaveLength(page.state.samples.length * 2);

    $("speedInput").value = "7";
    $("speedInput").dispatchEvent(new Event("change", { bubbles: true }));
    await loadRoute(page);
    expect(panel.querySelectorAll(".leaflet-container")).toHaveLength(1);

    expect(() => page.mapView.setPosition(page.state.route.totalDistKm / 2)).not.toThrow();
  });

  it("moves the readout and slider together when playback ticks", async () => {
    const { page } = startPage();
    await loadRoute(page);

    const distance = () => $("playback").querySelector(".readout-distance dd").textContent;
    const slider = () => $("playback").querySelector("input[type=range]");
    const before = distance();
    page.playback.tick(0.5);
    expect(distance()).not.toBe(before);
    expect(Number(slider().value)).toBe(500);
    expect(slider().getAttribute("aria-valuetext")).toContain("km");
  });

  it("shows each point's forecast cell and its offset", async () => {
    const { page } = startPage();
    await loadRoute(page);

    const headers = Array.from(
      document.querySelectorAll("#pointsTable th"),
      (th) => th.textContent,
    );
    const col = headers.indexOf("Forecast cell");
    expect(col).toBeGreaterThan(-1);
    expect(headers[col + 1]).toBe("Model cell elevation");
    expect(headers).not.toContain("Model 10 m wind");

    expect(page.state.cells).toHaveLength(page.state.samples.length);
    for (const cell of page.state.cells) {
      expect(cell.offsetKm).toBeGreaterThanOrEqual(0);
      expect(Number.isFinite(cell.offsetBearingDeg)).toBe(true);
    }
    // Cell is 0.004° N and 0.006° W of the point: ~0.6 km to the NW.
    const text = $("pointsBody").querySelector("tr").children[col].textContent;
    expect(text).toMatch(/^54\.454, −3\.206 · 0\.6 km NW$/);
  });

  it("shows the map panel while the forecast is still loading", async () => {
    const { page, fetch } = startPage({ fetchOptions: { holdFirst: "forecast" } });
    chooseFile(gpxFile(gpxText()));
    await vi.waitFor(() => expect(fetch.calls.forecast).toBe(1));
    expect($("mapPanel").hidden).toBe(false);
    expect($("chartsPanel").hidden).toBe(true);
    expect($("playback").hidden).toBe(true);
    fetch.release();
    await waitForLoad(page);
    expect($("chartsPanel").hidden).toBe(false);
  });

  it("falls back to the file name for an unnamed track", async () => {
    const { page } = startPage();
    chooseFile(gpxFile(gpxText({ name: null }), "Morning <i>ride</i>.gpx"));
    await waitForLoad(page);
    expect($("routeName").textContent).toBe("Morning <i>ride</i>.gpx");
  });

  it("loads a dropped file, and stops drops elsewhere navigating away", async () => {
    const { page } = startPage();
    const outside = dropEvent(document.body, []);
    expect(outside.defaultPrevented).toBe(true);

    dropEvent($("dropZone"), [gpxFile(gpxText({ name: "Dropped" }))]);
    await waitForLoad(page);
    expect(page.state.routeName).toBe("Dropped");
    expect(page.state.results).not.toBeNull();
  });

  it("shows an error for a bad GPX and stays usable", async () => {
    const { page, calls } = startPage();
    chooseFile(gpxFile("<gpx><oops"));
    await vi.waitFor(() => expect(page.state.status.kind).toBe("error"));
    expect($("status").textContent).toMatch(/Couldn't load that GPX file/);
    expect(calls.forecast).toBe(0);
    expect($("summary").hidden).toBe(true);

    await loadRoute(page);
    expect(page.state.results).not.toBeNull();
  });

  it("rejects an oversized file before reading it", async () => {
    const { page, calls } = startPage();
    const file = gpxFile(gpxText());
    Object.defineProperty(file, "size", { value: MAX_GPX_BYTES + 1 });
    chooseFile(file);
    await vi.waitFor(() => expect(page.state.status.kind).toBe("error"));
    expect($("status").textContent).toContain("25 MB");
    expect(calls.forecast).toBe(0);
  });

  it("fetches elevation exactly once for a GPX without it", async () => {
    const { page, calls } = startPage();
    await loadRoute(page, { withElevation: false });
    expect(calls).toEqual({ forecast: 1, elevation: 1 });
    expect(page.state.samples.every((s) => s.ele === 321)).toBe(true);
    expect($("summaryList").textContent).toContain("No elevation data");
    expect(page.state.results).not.toBeNull();
  });

  it("drops a superseded load: the newer file wins and the older one writes nothing", async () => {
    const { page, fetch } = startPage({ fetchOptions: { holdFirst: "forecast" } });
    chooseFile(gpxFile(gpxText({ name: "Route A" })));
    await vi.waitFor(() => expect(fetch.calls.forecast).toBe(1));

    chooseFile(gpxFile(gpxText({ name: "Route B" })));
    await waitForLoad(page);
    expect(page.state.routeName).toBe("Route B");
    expect(fetch.signals[0].aborted).toBe(true);
    const { results, locations } = page.state;

    fetch.release();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(page.state.routeName).toBe("Route B");
    expect(page.state.results).toBe(results);
    expect(page.state.locations).toBe(locations);
    expect(page.state.status.kind).toBe("info");
    // Both loads spent quota, so both report it.
    expect(usageReports()).toHaveLength(2);
  });

  it("drops a superseded elevation response", async () => {
    const { page, fetch } = startPage({ fetchOptions: { holdFirst: "elevation" } });
    chooseFile(gpxFile(gpxText({ name: "Route A", withElevation: false })));
    await vi.waitFor(() => expect(fetch.calls.elevation).toBe(1));

    chooseFile(gpxFile(gpxText({ name: "Route B" })));
    await waitForLoad(page);
    const { samples } = page.state;

    fetch.release();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(page.state.samples).toBe(samples);
    // A never got as far as its forecast request.
    expect(fetch.calls.forecast).toBe(1);
    expect(usageReports()).toHaveLength(2);
  });

  it("keeps the drop highlight while dragging over the zone's children", () => {
    startPage();
    const zone = $("dropZone");
    zone.dispatchEvent(new Event("dragover", { cancelable: true }));
    expect(zone.classList.contains("dragging")).toBe(true);
    const child = zone.querySelector(".drop-title");
    zone.dispatchEvent(new MouseEvent("dragleave", { relatedTarget: child }));
    expect(zone.classList.contains("dragging")).toBe(true);
    zone.dispatchEvent(new MouseEvent("dragleave", { relatedTarget: document.body }));
    expect(zone.classList.contains("dragging")).toBe(false);
  });

  // --- forecast span ---

  it("uses the last hour with data everywhere as the forecast end", async () => {
    const { page } = startPage({ fetchOptions: { trailingNulls: 5 } });
    await loadRoute(page);
    expect(page.state.forecastEndMs).toBe(WINDOW.endHour - 5 * HOUR_MS);
  });

  it("errors when the start is before the first hour with data", async () => {
    const { page } = startPage({ fetchOptions: { leadingNulls: 3 } });
    await loadRoute(page);
    expect(page.state.forecastStartMs).toBe(WINDOW.startHour + 3 * HOUR_MS);
    expect(page.state.results).toBeNull();
    expect($("planError").textContent).toContain(
      `The forecast starts at ${formatDateTime(WINDOW.startHour + 3 * HOUR_MS)}`,
    );
  });

  // --- recomputing ---

  it("recomputes on a speed change without fetching again", async () => {
    const { page, calls } = startPage();
    await loadRoute(page);
    const durationBefore = page.state.times.at(-1) - page.state.times[0];

    typeInto("speedInput", "2.5");
    expect(calls.forecast).toBe(1);
    expect(page.state.activity).toBe("custom");
    expect(checkedValue("activity")).toBe("custom");
    expect(page.state.times.at(-1) - page.state.times[0]).toBeCloseTo(2 * durationBefore, -3);
    // Typing alone doesn't touch the URL; committing does.
    expect(window.location.search).not.toContain("speed=2.5");
    commit("speedInput");
    expect(window.location.search).toContain("speed=2.5");
    expect(window.location.search).toContain("activity=custom");
  });

  it("converts a speed typed in mph to km/h", () => {
    const { page } = startPage({ search: "?units=imperial" });
    typeInto("speedInput", "10");
    expect(page.state.speedKmh).toBeCloseTo(16.0934, 3);
    commit("speedInput");
    expect(window.location.search).toContain("speed=16.09");
  });

  it.each([
    ["metric", { run: "10", cycle: "20", walk: "5" }],
    ["imperial", { run: "6.2", cycle: "12.4", walk: "3.1" }],
  ])("sets each activity's preset speed (%s)", (units, expected) => {
    const { page } = startPage({ search: `?units=${units}` });
    for (const [activity, speed] of Object.entries(expected)) {
      clickRadio("activity", activity);
      expect($("speedInput").value).toBe(speed);
      expect(page.state.activity).toBe(activity);
      expect(window.location.search).toContain(`activity=${activity}`);
    }
    // Custom keeps whatever speed is set.
    clickRadio("activity", "custom");
    expect($("speedInput").value).toBe(expected.walk);
  });

  it("re-renders units without fetching and converts the speed input", async () => {
    const { page, calls } = startPage();
    await loadRoute(page);
    clickRadio("units", "imperial");

    expect(calls.forecast).toBe(1);
    expect($("speedInput").value).toBe("3.1");
    expect($("speedUnit").textContent).toBe("mph");
    expect($("summaryList").textContent).toContain("mi");
    expect(page.state.results).not.toBeNull();
    expect(window.location.search).toContain("units=imperial");
  });

  // --- per-quantity units ---

  // A table row's cells by column: distance, temp, feels like, wind, gust,
  // head/tail, cross, rain.
  const rowText = (row) => {
    const cells = [...row.querySelectorAll("td")].map((td) => td.textContent);
    return {
      distance: cells[0], temp: cells[5], feels: cells[6], wind: cells[7], gust: cells[8],
      head: cells[9], cross: cells[10], rain: cells[11],
    };
  };
  const firstRow = () => rowText($("pointsBody").querySelector("tr"));
  const readoutText = (key) => $("playback").querySelector(`.readout-${key} dd`).textContent;
  const urlParams = () => new URLSearchParams(window.location.search);

  it("starts with the toggles on the preset's defaults and the disclosure closed", () => {
    const { page } = startPage();
    expect(page.state.units).toEqual({ system: "metric", wind: "kmh", rain: "mm", temp: "c" });
    expect(checkedValue("windUnit")).toBe("kmh");
    expect(checkedValue("rainUnit")).toBe("mm");
    expect(checkedValue("tempUnit")).toBe("c");
    expect($("unitsCustomise").open).toBe(false);
  });

  it("shows wind in mph over metric without touching distance, speed or the data", async () => {
    const { page, calls } = startPage();
    const reasons = [];
    page.addView((state, reason) => reasons.push(reason));
    await loadRoute(page);
    // Through the clock, so the position survives the units re-render.
    page.playback.clock.seek(0.5);
    const { results, times } = page.state;
    const before = firstRow();
    const distanceBefore = readoutText("distance");

    clickRadio("windUnit", "mph");

    expect(reasons.at(-1)).toBe("units");
    expect(calls.forecast).toBe(1);
    // No recompute: the same results and times.
    expect(page.state.results).toBe(results);
    expect(page.state.times).toBe(times);
    expect(page.state.units).toEqual({ system: "metric", wind: "mph", rain: "mm", temp: "c" });
    expect(checkedValue("units")).toBe("metric");

    const after = firstRow();
    for (const key of ["wind", "gust", "head", "cross"]) {
      expect(after[key], key).toMatch(/mph/);
      expect(after[key], key).not.toMatch(/km\/h/);
    }
    expect(after.distance).toBe(before.distance);
    expect(after.temp).toBe(before.temp);
    expect(after.rain).toBe(before.rain);
    expect(readoutText("wind")).toMatch(/^\d+ mph .*gusts \d+ mph$/);
    expect(readoutText("relative")).not.toMatch(/km\/h/);
    expect(readoutText("model")).toMatch(/^10 m wind \d+ mph · cell elevation \d+ m$/);
    expect(readoutText("distance")).toBe(distanceBefore);
    expect($("speedUnit").textContent).toBe("km/h");
    expect($("speedInput").value).toBe("5");
    expect([...$("chartsPanel").querySelectorAll(".strip-title")].map((t) => t.textContent))
      .toEqual([
        "Elevation (m)", "Temperature (°C)", "Wind (mph)", "Rain (mm/h)",
        "Sky (cloud %, sunshine)",
      ]);

    expect(urlParams().get("units")).toBe("metric");
    expect(urlParams().get("wind")).toBe("mph");
    expect(urlParams().has("rain")).toBe(false);
    expect(urlParams().has("temp")).toBe(false);
  });

  it("shows rain and temperature in their own units", async () => {
    const { page } = startPage({ search: "?units=imperial" });
    await loadRoute(page);
    clickRadio("rainUnit", "mm");
    clickRadio("tempUnit", "c");
    const row = firstRow();
    expect(row.temp).toMatch(/°C$/);
    expect(row.feels).toMatch(/°C$/);
    expect(row.rain).toMatch(/ mm · /);
    expect(row.wind).toMatch(/mph/);
    expect(row.distance).toMatch(/mi$/);
    expect(urlParams().get("rain")).toBe("mm");
    expect(urlParams().get("temp")).toBe("c");
    expect(urlParams().has("wind")).toBe(false);
  });

  it("resets the toggles when a preset is picked, even the one already picked", async () => {
    const { page } = startPage();
    await loadRoute(page);
    clickRadio("windUnit", "mph");
    clickRadio("tempUnit", "f");

    clickRadio("units", "imperial");
    expect(page.state.units).toEqual({ system: "imperial", wind: "mph", rain: "in", temp: "f" });
    expect(checkedValue("rainUnit")).toBe("in");
    expect(urlParams().get("units")).toBe("imperial");
    for (const key of ["wind", "rain", "temp"]) expect(urlParams().has(key), key).toBe(false);

    clickRadio("tempUnit", "c");
    expect(page.state.units.temp).toBe("c");
    clickRadio("units", "imperial");
    expect(page.state.units.temp).toBe("f");
    expect(checkedValue("tempUnit")).toBe("f");
    expect(firstRow().temp).toMatch(/°F$/);

    clickRadio("units", "metric");
    expect(page.state.units).toEqual({ system: "metric", wind: "kmh", rain: "mm", temp: "c" });
    expect(checkedValue("windUnit")).toBe("kmh");
  });

  it("ignores re-picking a preset or toggle with nothing to change", () => {
    const { page } = startPage();
    const reasons = [];
    page.addView((state, reason) => reasons.push(reason));
    const units = page.state.units;
    clickRadio("units", "metric");
    clickRadio("windUnit", "kmh");
    expect(page.state.units).toBe(units);
    expect(reasons).toEqual([]);
  });

  it("restores unit overrides from the URL and opens the disclosure", async () => {
    const { page } = startPage({ search: "?units=metric&wind=mph&temp=f" });
    expect(page.state.units).toEqual({ system: "metric", wind: "mph", rain: "mm", temp: "f" });
    expect($("unitsCustomise").open).toBe(true);
    expect(checkedValue("units")).toBe("metric");
    expect(checkedValue("windUnit")).toBe("mph");
    expect(checkedValue("rainUnit")).toBe("mm");
    expect(checkedValue("tempUnit")).toBe("f");
    await loadRoute(page);
    expect(firstRow().temp).toMatch(/°F$/);
    expect(firstRow().distance).toMatch(/km$/);
  });

  it("falls back to the preset for invalid URL overrides", () => {
    const { page } = startPage({ search: "?units=imperial&wind=knots&temp=K" });
    expect(page.state.units).toEqual({ system: "imperial", wind: "mph", rain: "in", temp: "f" });
    expect($("unitsCustomise").open).toBe(false);
  });

  it("flags an invalid speed instead of showing stale results", async () => {
    const { page } = startPage();
    await loadRoute(page);
    typeInto("speedInput", "500");
    expect(page.state.results).toBeNull();
    expect($("planError").hidden).toBe(false);
    expect($("planError").getAttribute("role")).toBe("status");
    expect($("planError").textContent).toContain("0.5–100 km/h");
    expect($("pointsDetails").hidden).toBe(true);
  });

  it("writes a committed start to the URL as UTC", () => {
    startPage();
    const startMs = Date.UTC(2026, 9, 27, 14, 30);
    typeInto("startInput", toLocalInputValue(startMs));
    commit("startInput");
    expect(new URLSearchParams(window.location.search).get("start")).toBe("2026-10-27T14:30Z");
  });

  it("rejects a start in the past", async () => {
    const { page } = startPage();
    await loadRoute(page);
    typeInto("startInput", toLocalInputValue(NOW - 10 * MINUTE_MS));
    expect(page.state.results).toBeNull();
    expect($("planError").textContent).toBe(
      `Pick a start from ${formatDateTime(Math.floor(NOW / MINUTE_MS) * MINUTE_MS)} onwards.`,
    );
  });

  it("limits the start input to the latest start that still fits", async () => {
    const { page } = startPage();
    await loadRoute(page);
    const durationMs = page.state.times.at(-1) - page.state.times[0];
    expect($("startInput").max).toBe(
      toLocalInputValue(latestStartMs(page.state.forecastEndMs, durationMs)),
    );
  });

  it("gives the exact latest valid start when the finish is beyond the forecast", async () => {
    const { page, calls } = startPage();
    await loadRoute(page);
    const durationMs = page.state.times.at(-1) - page.state.times[0];
    // Forecast ends Nov 2 09:00 UTC; starting half an hour before can't fit.
    typeInto("startInput", toLocalInputValue(Date.UTC(2026, 10, 2, 8, 30)));

    expect(calls.forecast).toBe(1);
    expect(page.state.results).toBeNull();
    const latest = Math.floor((WINDOW.endHour - durationMs) / MINUTE_MS) * MINUTE_MS;
    expect($("planError").textContent).toContain(`Latest valid start: ${formatDateTime(latest)}.`);
    expect($("pointsDetails").hidden).toBe(true);
  });

  it("asks for a higher speed when no start fits", async () => {
    const { page } = startPage({ fetchOptions: { maxHours: 4 } });
    await loadRoute(page);
    expect(page.state.results).not.toBeNull();
    typeInto("speedInput", "0.5");
    expect(page.state.results).toBeNull();
    expect($("planError").textContent).toContain("Try a higher speed.");
  });

  // --- failures, retry, usage ---

  it("reports a failed fetch and recovers with Retry", async () => {
    const { page, calls } = startPage({ fetchOptions: { failForecast: 1 } });
    await loadRoute(page);
    expect(page.state.status.kind).toBe("error");
    expect($("status").textContent).toContain("rate limited");
    expect($("retryButton").hidden).toBe(false);
    expect($("retryButton").disabled).toBe(false);
    expect(page.state.results).toBeNull();

    $("retryButton").click();
    expect($("retryButton").hidden).toBe(false);
    expect($("retryButton").disabled).toBe(true);
    expect(document.activeElement).toBe($("status"));
    await vi.waitFor(() => expect(page.state.status.kind).toBe("info"));
    expect(calls.forecast).toBe(2);
    expect($("retryButton").hidden).toBe(true);
    expect(page.state.results).not.toBeNull();
  });

  it("does not refetch elevation on Retry", async () => {
    const { page, calls } = startPage({ fetchOptions: { failForecast: 1 } });
    await loadRoute(page, { withElevation: false });
    expect(calls).toEqual({ forecast: 1, elevation: 1 });

    $("retryButton").click();
    await vi.waitFor(() => expect(page.state.status.kind).toBe("info"));
    expect(calls).toEqual({ forecast: 2, elevation: 1 });
  });

  it("does not refetch elevation on Retry even when some points had none", async () => {
    const { page, calls } = startPage({ fetchOptions: { failForecast: 1, nullElevation: true } });
    await loadRoute(page, { withElevation: false });
    expect(page.state.samples[0].ele).toBeNull();

    $("retryButton").click();
    await vi.waitFor(() => expect(page.state.status.kind).toBe("info"));
    expect(calls).toEqual({ forecast: 2, elevation: 1 });
    expect(page.state.results).not.toBeNull();
  });

  it("logs one usage report per load, after success and after failure", async () => {
    const failing = startPage({ fetchOptions: { failForecast: 1 } });
    await loadRoute(failing.page, { withElevation: false });
    expect(usageReports()).toHaveLength(1);
    // The time zone lookup (1 call), elevation and the failed forecast
    // request, all counted.
    const n = failing.page.state.samples.length;
    const forecastWeight = n * (HOURLY_VARS.length / 10);
    const spent = Number(/used ~([\d.]+) calls/.exec(usageReports()[0])[1]);
    expect(spent).toBeCloseTo(1 + n + forecastWeight, 1);

    $("retryButton").click();
    await vi.waitFor(() => expect(failing.page.state.status.kind).toBe("info"));
    expect(usageReports()).toHaveLength(2);
    expect(usageReports()[1]).toMatch(/minute: ~[\d.]+ of 600 used/);
  });

  // --- view API ---

  it("shows times in the route's zone once it is known", async () => {
    const { page, fetch } = startPage({ fetchOptions: { timeZone: "Asia/Tokyo" } });
    expect($("startZone").textContent).toBe("your local time");
    await loadRoute(page);
    expect(fetch.zoneCalls).toHaveLength(1);
    expect(new URL(fetch.zoneCalls[0]).searchParams.get("timezone")).toBe("auto");
    expect(page.state.timeZone).toBe("Asia/Tokyo");
    // The default start re-snaps to the next whole hour on the route's clock.
    expect(page.state.startMs).toBe(nextWholeHour(NOW, "Asia/Tokyo"));
    expect($("startInput").value).toBe(toLocalInputValue(page.state.startMs, "Asia/Tokyo"));
    expect($("startInput").min).toBe(toLocalInputValue(NOW, "Asia/Tokyo"));
    expect($("startZone").textContent).toBe("route time: Asia/Tokyo (GMT+9)");
    expect($("summaryList").textContent).toContain("Times inAsia/Tokyo (GMT+9)");
    expect($("summaryList").textContent)
      .toContain(formatDateTime(page.state.times[0], "Asia/Tokyo"));
    expect($("pointsBody").rows[0].cells[1].textContent)
      .toBe(formatDateTime(page.state.times[0], "Asia/Tokyo").slice(-5));
  });

  it("keeps a linked start's instant when the zone arrives", async () => {
    const start = "2026-10-26T12:00Z";
    const { page } = startPage({
      search: `?start=${start}`, fetchOptions: { timeZone: "Asia/Tokyo" },
    });
    await loadRoute(page);
    expect(page.state.startMs).toBe(Date.parse(start));
    expect($("startInput").value).toBe("2026-10-26T21:00");
  });

  it("reads a typed start in the route's zone", async () => {
    const { page } = startPage({ fetchOptions: { timeZone: "Asia/Tokyo" } });
    await loadRoute(page);
    typeInto("startInput", "2026-10-27T08:00");
    expect(page.state.startMs).toBe(Date.UTC(2026, 9, 26, 23, 0));
    commit("startInput");
    expect(window.location.search).toContain("start=2026-10-26T23%3A00Z");
  });

  it("stays in the browser's zone when the zone lookup fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { page, calls } = startPage({ fetchOptions: { timeZone: null } });
    await loadRoute(page);
    expect(page.state.timeZone).toBeNull();
    expect(page.state.results).not.toBeNull();
    expect(calls.forecast).toBe(1);
    expect($("startZone").textContent).toBe("your local time");
    expect($("summaryList").textContent).not.toContain("Times in");
    expect(warn).toHaveBeenCalledWith(expect.stringMatching(/your own time zone/));
  });

  it("returns to the browser's zone for a new file until its zone is known", async () => {
    const { page, fetch } = startPage({
      fetchOptions: { timeZone: "Asia/Tokyo", holdFirst: "forecast" },
    });
    chooseFile(gpxFile(gpxText()));
    await vi.waitFor(() => expect(page.state.timeZone).toBe("Asia/Tokyo"));
    fetch.release();
    await waitForLoad(page);
    chooseFile(gpxFile(gpxText({ name: "Second" })));
    expect(page.state.timeZone).toBeNull();
    expect($("startZone").textContent).toBe("your local time");
    await vi.waitFor(() => expect(page.state.routeName).toBe("Second"));
    await waitForLoad(page);
    expect(page.state.timeZone).toBe("Asia/Tokyo");
    expect(fetch.zoneCalls).toHaveLength(2);
  });

  it("shows the sun and sky in the summary, table and readout", async () => {
    const { page } = startPage({ fetchOptions: { timeZone: "Europe/London" } });
    await loadRoute(page);
    const summary = $("summaryList").textContent;
    // 26 Oct at 54.45°N, 3.2°W.
    expect(summary).toMatch(/Sun at the start↑ 07:\d\d · ↓ 16:\d\d/);
    expect(summary).toMatch(/Sunshine on the route~\d+\.\d h of \d+\.\d h daylight/);
    expect(page.state.daylight.startsInDark).toBe(false);
    const row = $("pointsBody").rows[0].cells;
    expect(row[12].textContent).toMatch(/^\d+%$/);
    expect(row[13].textContent).toMatch(/^\d+ min\/h$/);
    expect($("playback").querySelector(".readout-light dd").textContent)
      .toMatch(/^Daylight · sunset 16:\d\d \(in /);
    expect($("chartsPanel").querySelectorAll(".strip")).toHaveLength(5);
  });

  it("passes a reason to registered views", async () => {
    const { page } = startPage();
    const reasons = [];
    page.addView((state, reason) => reasons.push(reason));
    await loadRoute(page);
    expect(reasons).toContain("load");
    expect(reasons).toContain("status");
    expect(reasons.at(-1)).toBe("results");

    clickRadio("units", "imperial");
    expect(reasons.at(-1)).toBe("units");
    typeInto("speedInput", "4");
    expect(reasons.at(-1)).toBe("inputs");
  });
});

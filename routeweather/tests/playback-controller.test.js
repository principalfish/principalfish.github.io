// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from "vitest";

import { createPlaybackController } from "../js/playback-controller.js";
import { buildRoute } from "../js/route.js";

// --- fixtures ---

const POINTS = [
  { lat: 54.4, lon: -3.2, ele: 100 },
  { lat: 54.41, lon: -3.165, ele: 400 },
  { lat: 54.395, lon: -3.13, ele: 200 },
];

function makeResults(samples, tempC = 10) {
  return samples.map((s, i) => ({
    tempC: tempC + i, model2mTempC: tempC, windKmh: 20, windFromDeg: 270, gustKmh: 30,
    headKmh: 5, crossKmh: 1, precipMm: 0, precipProb: 0, code: 0, model10mWindKmh: 15,
    model10mGustKmh: 25, cellElev: 300, eleUsed: s.ele, outsideForecast: false,
  }));
}

function makeState(route = buildRoute(POINTS)) {
  const samples = route.points.map((p, i) => ({
    index: i, lat: p.lat, lon: p.lon, ele: p.ele, cumDistKm: p.cumDistKm,
    bearingDeg: p.bearingDeg, kind: "filler",
  }));
  return {
    route, samples, results: makeResults(samples), units: "metric",
    startMs: Date.UTC(2026, 9, 26, 9, 0), speedKmh: 5,
  };
}

function fakeFrames() {
  let t = 0;
  const pending = new Map();
  let nextId = 1;
  return {
    now: () => t,
    raf: (cb) => { pending.set(nextId, cb); return nextId++; },
    caf: (id) => pending.delete(id),
    step(ms) {
      t += ms;
      const due = [...pending.values()];
      pending.clear();
      due.forEach((cb) => cb(t));
    },
  };
}

// --- tests ---

describe("createPlaybackController", () => {
  let positions;
  let frames;
  let controller;

  beforeEach(() => {
    document.body.innerHTML = "<section id=\"chartsPanel\"></section><div id=\"playback\"></div>";
    positions = [];
    frames = fakeFrames();
    const mapView = { setPosition: (km, weather) => positions.push({ km, weather }) };
    controller = createPlaybackController({
      mapView,
      chartsEl: document.getElementById("chartsPanel"),
      playbackEl: document.getElementById("playback"),
      clockOptions: { durationMs: 1000, now: frames.now, raf: frames.raf, caf: frames.caf },
    });
  });

  afterEach(() => controller.destroy());

  const slider = () => document.querySelector(".pb-slider");
  const button = () => document.querySelector(".pb-toggle");
  const temp = () => document.querySelector(".readout-temp dd").textContent;

  it("builds the charts, controls and readout into their panels", () => {
    expect(document.querySelector("#chartsPanel svg")).not.toBeNull();
    expect(document.querySelector("#playback .pb-controls")).not.toBeNull();
    expect(document.querySelector("#playback .readout")).not.toBeNull();
  });

  it("puts a new route at the start, paused", () => {
    const state = makeState();
    controller.update(state, "load");
    expect(positions.at(-1).km).toBe(0);
    expect(positions.at(-1).weather.tempC).toBe(10);
    expect(controller.clock.isPlaying()).toBe(false);
    expect(slider().value).toBe("0");
    expect(temp()).toBe("10 °C");
  });

  it("moves the map, cursor, readout and slider together on each frame", () => {
    const state = makeState();
    controller.update(state, "load");
    button().click();
    expect(button().textContent).toContain("Pause");
    for (let i = 0; i < 5; i++) frames.step(100);
    const half = state.route.totalDistKm / 2;
    expect(positions.at(-1).km).toBeCloseTo(half, 9);
    expect(slider().value).toBe("500");
    // The spoken value is throttled while playing; pausing catches it up.
    button().click();
    expect(slider().getAttribute("aria-valuetext")).toMatch(/^\d+\.\d km · (\w{3} )?\d\d:\d\d$/);
    expect(document.querySelector(".readout-distance dd").textContent).toMatch(/^\d+\.\d km of/);
    const cursor = Number(document.querySelector(".chart-cursor").getAttribute("x1"));
    const hit = document.querySelector(".chart-hit");
    const mid = Number(hit.getAttribute("x")) + Number(hit.getAttribute("width")) / 2;
    expect(cursor).toBeCloseTo(mid, 0);
  });

  it("seeks from the slider and from a chart click", () => {
    const state = makeState();
    controller.update(state, "load");
    slider().value = "250";
    slider().dispatchEvent(new Event("input"));
    expect(positions.at(-1).km).toBeCloseTo(state.route.totalDistKm / 4, 9);
    const hit = document.querySelector(".chart-hit");
    const x = Number(hit.getAttribute("x")) + Number(hit.getAttribute("width")) * 0.75;
    document.querySelector("#chartsPanel svg")
      .dispatchEvent(new MouseEvent("pointerdown", { clientX: x }));
    expect(positions.at(-1).km).toBeCloseTo(state.route.totalDistKm * 0.75, 6);
    expect(slider().value).toBe("750");
  });

  it("keeps position and playing state when the results change", () => {
    const state = makeState();
    controller.update(state, "load");
    button().click();
    for (let i = 0; i < 4; i++) frames.step(100);
    const next = { ...state, results: makeResults(state.samples, 20) };
    controller.update(next, "inputs");
    expect(controller.clock.isPlaying()).toBe(true);
    expect(controller.clock.fraction()).toBeCloseTo(0.4, 9);
    expect(positions.at(-1).km).toBeCloseTo(state.route.totalDistKm * 0.4, 9);
    expect(positions.at(-1).weather.tempC).toBeGreaterThanOrEqual(20);
  });

  it("re-renders the readout on a units change", () => {
    const state = makeState();
    controller.update(state, "load");
    controller.update({ ...state, units: "imperial" }, "units");
    expect(temp()).toBe("50 °F");
  });

  it("ignores renders that change nothing it shows", () => {
    const state = makeState();
    controller.update(state, "load");
    const count = positions.length;
    controller.update({ ...state, status: { kind: "info", message: "hi" } }, "status");
    expect(positions).toHaveLength(count);
  });

  it("pauses when the results go away, and resets on a new route", () => {
    const state = makeState();
    controller.update(state, "load");
    button().click();
    frames.step(300);
    controller.update({ ...state, results: null }, "inputs");
    expect(controller.clock.isPlaying()).toBe(false);
    expect(button().textContent).toContain("Play");

    const other = makeState(buildRoute(POINTS.slice(0, 2)));
    controller.update(other, "load");
    expect(controller.clock.fraction()).toBe(0);
    expect(positions.at(-1).km).toBe(0);
  });

  it("pauses on a new route even while playing", () => {
    controller.update(makeState(), "load");
    button().click();
    frames.step(100);
    controller.update(makeState(buildRoute(POINTS.slice(0, 2))), "load");
    expect(controller.clock.isPlaying()).toBe(false);
    expect(button().textContent).toContain("Play");
  });

  it("pauses while the slider is dragged, and resumes after if it was playing", () => {
    const state = makeState();
    controller.update(state, "load");
    button().click();
    slider().dispatchEvent(new MouseEvent("pointerdown", { button: 0 }));
    expect(controller.clock.isPlaying()).toBe(false);
    slider().value = "600";
    slider().dispatchEvent(new Event("input"));
    slider().dispatchEvent(new MouseEvent("pointerup"));
    expect(controller.clock.isPlaying()).toBe(true);
    expect(controller.clock.fraction()).toBeCloseTo(0.6, 9);
  });

  it("stays paused after a drag that started paused", () => {
    controller.update(makeState(), "load");
    slider().dispatchEvent(new MouseEvent("pointerdown", { button: 0 }));
    slider().dispatchEvent(new Event("change"));
    expect(controller.clock.isPlaying()).toBe(false);
  });

  it("doesn't resume after a drag to the finish", () => {
    controller.update(makeState(), "load");
    button().click();
    slider().dispatchEvent(new MouseEvent("pointerdown", { button: 0 }));
    slider().value = "1000";
    slider().dispatchEvent(new Event("input"));
    slider().dispatchEvent(new Event("change"));
    expect(controller.clock.isPlaying()).toBe(false);
    expect(controller.clock.fraction()).toBe(1);
  });

  it("pauses while the chart is dragged, and resumes after", () => {
    controller.update(makeState(), "load");
    button().click();
    const svg = document.querySelector("#chartsPanel svg");
    const hit = document.querySelector(".chart-hit");
    const x = Number(hit.getAttribute("x")) + Number(hit.getAttribute("width")) * 0.3;
    svg.dispatchEvent(new MouseEvent("pointerdown", { clientX: x }));
    expect(controller.clock.isPlaying()).toBe(false);
    expect(controller.clock.fraction()).toBeCloseTo(0.3, 6);
    svg.dispatchEvent(new MouseEvent("pointerup", { clientX: x }));
    expect(controller.clock.isPlaying()).toBe(true);
  });

  it("shows the weekday in the visible time when it isn't today", () => {
    const state = makeState();
    // "Now" two days before the start.
    controller.update({ ...state, nowMs: state.startMs - 2 * 24 * 3600 * 1000 }, "load");
    const text = document.querySelector(".pb-time").textContent;
    expect(text).toMatch(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d\d:\d\d · /);
    controller.update({ ...state, nowMs: state.startMs, results: [...state.results] }, "inputs");
    expect(document.querySelector(".pb-time").textContent).toMatch(/^\d\d:\d\d · /);
  });

  it("applies the speed select to the clock", () => {
    controller.update(makeState(), "load");
    const select = document.querySelector(".pb-speed select");
    select.value = "4";
    select.dispatchEvent(new Event("change"));
    expect(controller.clock.speed()).toBe(4);
  });
});

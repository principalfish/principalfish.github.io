// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from "vitest";

import {
  createChartsUI, extremeLabels, outsideSpans, profilePoints, stepPoints,
} from "../js/charts.js";
import { weatherAtDistance } from "../js/along-route.js";
import { buildRoute } from "../js/route.js";
import { KM_TO_MI, resolveUnits } from "../js/units.js";

// --- fixtures ---

const POINTS = [
  { lat: 54.4, lon: -3.2, ele: 100 },
  { lat: 54.41, lon: -3.165, ele: 400 },
  { lat: 54.395, lon: -3.13, ele: 200 },
  { lat: 54.405, lon: -3.095, ele: 250 },
];

const KINDS = ["start", "peak", "valley", "end"];

const METRIC = resolveUnits("metric");
const IMPERIAL = resolveUnits("imperial");

function makeState(route = buildRoute(POINTS), overrides = {}) {
  const samples = route.points.map((p, i) => ({
    index: i, lat: p.lat, lon: p.lon, ele: p.ele, cumDistKm: p.cumDistKm,
    bearingDeg: p.bearingDeg, kind: KINDS[i] ?? "filler",
  }));
  const results = samples.map((s, i) => ({
    tempC: 8 - i, model2mTempC: 9 - i, windKmh: 20, windFromDeg: 270, gustKmh: 35,
    headKmh: [5, -10, 15, 0][i], crossKmh: [3, 4, -6, 2][i], precipMm: [0, 0.4, 1.2, 0][i],
    precipProb: [10, 40, 80, 20][i], code: 61, model10mWindKmh: 15, model10mGustKmh: 30,
    cellElev: 300, eleUsed: s.ele, outsideForecast: i === 3,
    cloudPct: [90, 60, 30, 0][i], sunMin: [0, 20, 45, NaN][i], sunEstimated: i === 2,
  }));
  return { route, samples, results, units: METRIC, ...overrides };
}

// Fixed-width container: jsdom has no layout, so clientWidth is stubbed.
function mount(width = 600) {
  document.body.innerHTML = "<section id=\"chartsPanel\"></section>";
  const container = document.getElementById("chartsPanel");
  setWidth(container, width);
  return container;
}

function setWidth(container, width) {
  Object.defineProperty(container, "clientWidth", { configurable: true, get: () => width });
}

// A pointer event: jsdom has no PointerEvent, so a MouseEvent of that type
// with pointerType/pointerId added.
function pointer(type, { clientX = 0, clientY = 0, button = 0, pointerType = "mouse" } = {}) {
  const event = new MouseEvent(type, { clientX, clientY, button, bubbles: true });
  Object.defineProperty(event, "pointerType", { value: pointerType });
  Object.defineProperty(event, "pointerId", { value: 1 });
  return event;
}

const svgOf = (container) => container.querySelector("svg");
const hitOf = (container) => container.querySelector(".chart-hit");
const cursorX = (container) => Number(container.querySelector(".chart-cursor").getAttribute("x1"));
const titles = (container) => [...container.querySelectorAll(".strip-title")]
  .map((t) => t.textContent);

// The x axis maps [0, total] (display units) to [left, right]; read those
// ends back off the drawn hit area.
function plotRange(container) {
  const hit = container.querySelector(".chart-hit");
  const left = Number(hit.getAttribute("x"));
  return { left, right: left + Number(hit.getAttribute("width")) };
}

// --- pure helpers ---

describe("profilePoints", () => {
  it("uses the route's own points when it has elevation", () => {
    const route = buildRoute(POINTS);
    expect(profilePoints(route, null).map((p) => p.ele)).toEqual([100, 400, 200, 250]);
  });

  it("thins a long route by distance bins, keeping each bin's extremes", () => {
    const points = Array.from({ length: 5000 }, (_, i) => ({
      lat: 54 + i * 0.0001, lon: -3, ele: i === 2500 ? 999 : 100 + (i % 7),
    }));
    const profile = profilePoints(buildRoute(points), null);
    expect(profile.length).toBeLessThanOrEqual(800);
    expect(Math.max(...profile.map((p) => p.ele))).toBe(999);
    const kms = profile.map((p) => p.km);
    expect(kms).toEqual([...kms].sort((a, b) => a - b));
  });

  it("falls back to the samples' elevation, and to nothing", () => {
    const route = buildRoute(POINTS.map(({ lat, lon }) => ({ lat, lon })));
    const samples = [{ cumDistKm: 0, ele: 10 }, { cumDistKm: 2, ele: 20 }];
    expect(profilePoints(route, samples)).toEqual([{ km: 0, ele: 10 }, { km: 2, ele: 20 }]);
    const none = [{ cumDistKm: 0, ele: null }, { cumDistKm: 1, ele: null }];
    expect(profilePoints(route, none)).toEqual([]);
  });
});

describe("outsideSpans", () => {
  const samples = [0, 2, 4, 6, 8].map((cumDistKm) => ({ cumDistKm }));
  const flags = (...outs) => outs.map((outsideForecast) => ({ outsideForecast }));

  it("covers each run of outside samples out to its neighbouring samples", () => {
    expect(outsideSpans(samples, flags(false, true, true, false, false), 8)).toEqual([[0, 6]]);
    expect(outsideSpans(samples, flags(false, false, true, false, false), 8)).toEqual([[2, 6]]);
  });

  it("runs to the route's ends at the first and last sample", () => {
    expect(outsideSpans(samples, flags(true, false, false, false, true), 8))
      .toEqual([[0, 2], [6, 8]]);
    expect(outsideSpans(samples, flags(false, false, false, false, false), 8)).toEqual([]);
  });

  it("merges runs whose spans touch", () => {
    expect(outsideSpans(samples, flags(false, true, false, true, false), 8)).toEqual([[0, 8]]);
  });

  it("greys exactly where along-route says the weather is outside", () => {
    const state = {
      route: { totalDistKm: 8 },
      samples: samples.map((s) => ({ ...s, ele: 0 })),
      results: flags(false, false, true, false, false).map((r) => ({ ...r, tempC: 1 })),
    };
    const [[from, to]] = outsideSpans(state.samples, state.results, 8);
    // 0.75 of the way from an inside sample (2 km) to the outside one (4 km),
    // and just inside each end of the band.
    for (const km of [3.5, from + 0.01, to - 0.01, 5.5]) {
      expect(weatherAtDistance(state, km).outsideForecast, `${km} km`).toBe(true);
    }
    for (const km of [from - 0.01, to + 0.01, 1, 7]) {
      expect(weatherAtDistance(state, km).outsideForecast, `${km} km`).toBe(false);
    }
  });
});

describe("stepPoints", () => {
  const samples = [0, 2, 4, 6].map((cumDistKm) => ({ cumDistKm }));

  it("holds each value from halfway before its sample to halfway after", () => {
    expect(stepPoints(samples, [10, 20, 30, 40], 7)).toEqual([
      { km: 0, v: 10 }, { km: 1, v: 10 },
      { km: 1, v: 20 }, { km: 3, v: 20 },
      { km: 3, v: 30 }, { km: 5, v: 30 },
      { km: 5, v: 40 }, { km: 7, v: 40 },
    ]);
  });

  it("leaves a gap over exactly a missing value's own span", () => {
    const points = stepPoints(samples, [10, NaN, 30, null], 6);
    expect(points.slice(0, 2)).toEqual([{ km: 0, v: 10 }, { km: 1, v: 10 }]);
    expect(points.slice(2, 4).every((p) => Number.isNaN(p.v))).toBe(true);
    expect(points.slice(4, 6)).toEqual([{ km: 3, v: 30 }, { km: 5, v: 30 }]);
    expect(Number.isNaN(points[6].v)).toBe(true);
  });

  it("draws the value before a gap right up to halfway, as the readout reads it", () => {
    const state = makeState();
    const probs = [10, NaN, 80, 20];
    const container = mount();
    const charts = createChartsUI(container, { raf: () => {} });
    charts.update({
      ...state, results: state.results.map((r, i) => ({ ...r, precipProb: probs[i] })),
    });
    const d = container.querySelector(".prob-area").getAttribute("d");
    // Two separate runs (the gap splits the path).
    expect(d.match(/M/g)).toHaveLength(2);
    const { left, right } = plotRange(container);
    const midKm = state.samples[1].cumDistKm / 2;
    const midX = left + ((right - left) * midKm) / state.route.totalDistKm;
    const firstRunXs = d.split("M")[1].split("L").map((p) => Number(p.split(",")[0]));
    expect(Math.max(...firstRunXs)).toBeCloseTo(midX, 2);
    charts.destroy();
  });
});

describe("extremeLabels", () => {
  const extreme = (km, ele, kind = "peak") => ({ cumDistKm: km, ele, kind });
  // 1 km = 10 px, every point at y 50.
  const at = (s) => [s.cumDistKm * 10, 50];

  it("places each label just right of its marker, above a summit and below a valley", () => {
    const labels = extremeLabels([extreme(0, 978), extreme(20, 120, "valley")], at, "metric", 500);
    expect(labels).toEqual([
      { kind: "peak", text: "978 m", x: 6, y: 47 },
      { kind: "valley", text: "120 m", x: 206, y: 61 },
    ]);
  });

  it("skips a label that would overlap the previous one kept", () => {
    // "978 m" is ~30 px wide: from x 6 to 36, so the next must start at 40+.
    const labels = extremeLabels(
      [extreme(0, 978), extreme(2, 900, "valley"), extreme(3.4, 950)], at, "metric", 500,
    );
    expect(labels.map((l) => l.text)).toEqual(["978 m", "950 m"]);
  });

  it("skips a label that would run past the plot's right edge", () => {
    const labels = extremeLabels([extreme(10, 978), extreme(48, 500)], at, "metric", 500);
    expect(labels.map((l) => l.text)).toEqual(["978 m"]);
  });

  it("uses the active units", () => {
    expect(extremeLabels([extreme(0, 1000)], at, IMPERIAL, 500)[0].text).toBe("3281 ft");
    // A plain system string still works.
    expect(extremeLabels([extreme(0, 1000)], at, "imperial", 500)[0].text).toBe("3281 ft");
  });
});

// --- factory ---

describe("createChartsUI", () => {
  let container;
  let calls;
  let charts;
  let frames;

  beforeEach(() => {
    container = mount();
    calls = [];
    frames = [];
    charts = createChartsUI(container, {
      onSeek: (km) => calls.push(["seek", km]),
      onScrubStart: () => calls.push(["start"]),
      onScrubEnd: () => calls.push(["end"]),
      raf: (cb) => frames.push(cb),
      caf: () => {},
    });
  });

  afterEach(() => charts.destroy());

  const seeks = () => calls.filter(([kind]) => kind === "seek").map(([, km]) => km);
  const runFrames = () => frames.splice(0).forEach((cb) => cb());

  it("draws nothing without results", () => {
    charts.update(makeState(undefined, { results: null }));
    expect(svgOf(container).children).toHaveLength(0);
  });

  it("builds one SVG with five strips, the series and one cursor", () => {
    charts.update(makeState());
    expect(container.querySelectorAll("svg")).toHaveLength(1);
    expect(titles(container)).toEqual([
      "Elevation (m)", "Temperature (°C)", "Head/tailwind (km/h)", "Rain (mm/h)",
      "Sky (cloud %, sunshine)",
    ]);
    expect(container.querySelectorAll(".strip")).toHaveLength(5);
    const classes = ["elev-area", "s-temp", "s-temp-model", "s-head", "s-gust", "prob-area"];
    for (const cls of classes) {
      expect(container.querySelector(`.${cls}`), cls).not.toBeNull();
    }
    // Rain bars only where it rains.
    expect(container.querySelectorAll(".rain-bars rect")).toHaveLength(2);
    expect(container.querySelectorAll(".extreme.peak")).toHaveLength(1);
    expect(container.querySelectorAll(".extreme.valley")).toHaveLength(1);
    expect(container.querySelectorAll(".chart-cursor")).toHaveLength(1);
    expect(svgOf(container).getAttribute("width")).toBe("600");
  });

  it("names the strips in the SVG's accessible name", () => {
    expect(svgOf(container).getAttribute("aria-label")).toMatch(new RegExp(
      "^Elevation, temperature, head/tailwind, rain \\(amount and chance\\) "
      + "and sky \\(cloud and sunshine\\) along the route\\.",
    ));
  });

  it("draws cloud and sunshine in the sky strip, estimates fainter", () => {
    charts.update(makeState());
    expect(container.querySelector(".cloud-area")).not.toBeNull();
    const bars = [...container.querySelectorAll(".sun-bars rect")];
    // No bar for no sun or a gap.
    expect(bars.map((b) => b.getAttribute("class"))).toEqual(["s-sun", "s-sun est"]);
    expect(container.querySelectorAll(".night-bands rect")).toHaveLength(0);
    expect(container.querySelector(".chart-legend").textContent).not.toContain("sunset");
  });

  it("shades the dark stretches and marks sunrise and sunset", () => {
    const state = makeState();
    const total = state.route.totalDistKm;
    const daylight = {
      startsInDark: false,
      events: [
        { kind: "sunset", km: total * 0.25, tMs: 0 },
        { kind: "sunrise", km: total * 0.75, tMs: 0 },
      ],
      nightSpans: [[total * 0.25, total * 0.75]],
      daylightMs: 0,
    };
    charts.update({ ...state, daylight });
    expect(container.querySelectorAll(".night-bands rect")).toHaveLength(1);
    expect(container.querySelectorAll(".sun-event-line")).toHaveLength(2);
    expect([...container.querySelectorAll(".sun-event-label")].map((t) => t.textContent))
      .toEqual(["☀↓", "☀↑"]);
    const names = [...container.querySelectorAll(".chart-legend .legend-strip")]
      .map((n) => n.textContent);
    expect(names).toContain("Dark");
  });

  it("leaves crosswind out of the wind strip", () => {
    charts.update(makeState());
    expect(container.querySelector(".s-cross")).toBeNull();
    expect(container.querySelector(".chart-legend").textContent).not.toMatch(/cross/i);
  });

  it("labels summits and valleys with their elevation in the active units", () => {
    const state = makeState();
    charts.update(state);
    const labels = () => [...container.querySelectorAll(".extreme-label")]
      .map((t) => [t.getAttribute("class"), t.textContent]);
    expect(labels()).toEqual([["extreme-label peak", "400 m"], ["extreme-label valley", "200 m"]]);
    charts.update({ ...state, units: IMPERIAL });
    expect(labels().map(([, text]) => text)).toEqual(["1312 ft", "656 ft"]);
  });

  it("shades chance of rain behind the bars, marked 100% at the right edge only", () => {
    charts.update(makeState());
    const rainStrip = container.querySelectorAll(".strip")[3];
    const area = rainStrip.querySelector(".prob-area");
    const bars = rainStrip.querySelector(".rain-bars");
    // Behind: earlier in document order.
    expect(area.compareDocumentPosition(bars) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector(".axis-right")).toBeNull();
    expect(svgOf(container).querySelector(".s-prob")).toBeNull();
    expect(rainStrip.querySelector(".prob-max").textContent).toBe("100%");
    const { right } = plotRange(container);
    expect(Number(rainStrip.querySelector(".prob-max").getAttribute("x"))).toBeGreaterThan(right);
  });

  it("draws chance of rain as steps that change halfway between samples", () => {
    const state = makeState();
    charts.update(state);
    const d = container.querySelector(".prob-area").getAttribute("d");
    const coords = d.replace(/^M/, "").replace(/Z$/, "").split("L")
      .map((p) => p.split(",").map(Number));
    // Horizontal and vertical segments only.
    for (let i = 1; i < coords.length; i++) {
      const [x0, y0] = coords[i - 1];
      const [x1, y1] = coords[i];
      expect(x0 === x1 || y0 === y1, `segment ${i}`).toBe(true);
    }
    // The first change of value sits halfway between samples 0 and 1.
    const { left, right } = plotRange(container);
    const midKm = state.samples[1].cumDistKm / 2;
    const midX = left + ((right - left) * midKm) / state.route.totalDistKm;
    expect(coords.some(([cx]) => Math.abs(cx - midX) < 0.01)).toBe(true);
  });

  it("draws no bar where the rain is 0 or missing", () => {
    const state = makeState();
    const precip = [0, NaN, null, 0];
    charts.update({ ...state, results: state.results.map((r, i) => ({ ...r, precipMm: precip[i] })) });
    expect(container.querySelectorAll(".rain-bars rect")).toHaveLength(0);
    // A dry forecast still gets clean axis ticks: a few, all distinct.
    const ticks = [...container.querySelectorAll(".strip")[3].querySelectorAll(".axis-y .tick text")]
      .map((t) => t.textContent);
    expect(ticks.length).toBeGreaterThanOrEqual(2);
    expect(ticks.length).toBeLessThanOrEqual(4);
    expect(new Set(ticks).size).toBe(ticks.length);
    expect(Number(ticks.at(-1))).toBeCloseTo(0.4, 9);
  });

  it("puts each rain bar over the stretch its sample is the nearer one for", () => {
    const state = makeState();
    charts.update(state);
    const { left, right } = plotRange(container);
    const xAt = (km) => left + ((right - left) * km) / state.route.totalDistKm;
    const [bar] = container.querySelectorAll(".rain-bars rect");
    const kms = state.samples.map((s) => s.cumDistKm);
    expect(Number(bar.getAttribute("x"))).toBeCloseTo(xAt((kms[0] + kms[1]) / 2) + 1, 6);
    const end = Number(bar.getAttribute("x")) + Number(bar.getAttribute("width"));
    expect(end).toBeCloseTo(xAt((kms[1] + kms[2]) / 2) - 1, 6);
  });

  it("greys the stretch outside the forecast with a hatch and adds it to the legend", () => {
    charts.update(makeState());
    const bands = container.querySelectorAll(".outside-bands rect");
    expect(bands).toHaveLength(1);
    expect(bands[0].getAttribute("fill")).toMatch(/^url\(#rwOutsideHatch\d+\)$/);
    expect(bands[0].querySelector("title")).toBeNull();
    expect(container.querySelector(".chart-legend").textContent).toContain("outside the forecast");
    const state = makeState();
    charts.update({
      ...state, results: state.results.map((r) => ({ ...r, outsideForecast: false })),
    });
    expect(container.querySelectorAll(".outside-bands rect")).toHaveLength(0);
    expect(container.querySelector(".chart-legend").textContent).not.toContain("outside");
  });

  it("groups the legend by strip", () => {
    charts.update(makeState());
    const groups = [...container.querySelectorAll(".chart-legend > li")];
    const names = groups.map((li) => li.querySelector(".legend-strip").textContent);
    expect(names).toEqual(["Elevation", "Temperature", "Head/tailwind", "Rain", "Sky", "Grey"]);
    const items = (li) => [...li.querySelectorAll(".legend-item")].map((item) => item.textContent);
    expect(items(groups[0])).toEqual(["summit", "valley"]);
    expect(items(groups[2])).toEqual(["headwind + / tailwind −", "gust"]);
    expect(items(groups[3])).toEqual(["rain per hour", "chance of rain"]);
  });

  it("leaves headroom above the profile for a peak marker", () => {
    charts.update(makeState());
    const [, y] = container.querySelector(".extreme.peak").getAttribute("transform")
      .match(/translate\([^,]+,([^)]+)\)/).map(Number);
    // The ▲ is drawn 3–10 px above the point; it must stay inside the plot.
    expect(y - 10).toBeGreaterThanOrEqual(0);
  });

  it("notes when there is no elevation data", () => {
    const route = buildRoute(POINTS.map(({ lat, lon }) => ({ lat, lon })));
    const state = makeState(route);
    charts.update({ ...state, samples: state.samples.map((s) => ({ ...s, ele: null })) });
    expect(container.querySelector(".chart-note").textContent).toMatch(/No elevation data/);
    expect(container.querySelector(".elev-area")).toBeNull();
  });

  it("rebuilds only when route, samples, results or units change identity", () => {
    const state = makeState();
    charts.update(state);
    let strip = container.querySelector(".strip");
    charts.update({ ...state, status: { kind: "info", message: "x" } });
    expect(container.querySelector(".strip")).toBe(strip);
    for (const change of [
      { results: [...state.results] },
      { samples: [...state.samples] },
      { units: IMPERIAL },
    ]) {
      charts.update({ ...state, ...change });
      expect(container.querySelector(".strip"), Object.keys(change)[0]).not.toBe(strip);
      strip = container.querySelector(".strip");
    }
  });

  it("moves only the cursor line on setCursor, and only when it moved", () => {
    const state = makeState();
    charts.update(state);
    const strip = container.querySelector(".strip");
    const { left, right } = plotRange(container);
    expect(cursorX(container)).toBeCloseTo(left, 1);
    charts.setCursor(state.route.totalDistKm / 2);
    expect(cursorX(container)).toBeCloseTo((left + right) / 2, 0);
    charts.setCursor(state.route.totalDistKm * 10);
    expect(cursorX(container)).toBeCloseTo(right, 1);
    expect(container.querySelector(".strip")).toBe(strip);

    // Same position: the attribute isn't written again.
    const cursor = container.querySelector(".chart-cursor");
    cursor.setAttribute("x1", "sentinel");
    charts.setCursor(state.route.totalDistKm * 10);
    expect(cursor.getAttribute("x1")).toBe("sentinel");
  });

  it("keeps the cursor across a rebuild, and resets it on a new route", () => {
    const state = makeState();
    charts.update(state);
    charts.setCursor(state.route.totalDistKm / 2);
    const before = cursorX(container);
    charts.update({ ...state, results: [...state.results] });
    expect(cursorX(container)).toBe(before);
    charts.update(makeState(buildRoute(POINTS.slice(0, 3))));
    expect(cursorX(container)).toBeCloseTo(plotRange(container).left, 1);
  });

  it("re-labels the axes for imperial units", () => {
    const state = makeState();
    charts.update(state);
    expect(container.querySelector(".axis-label").textContent).toBe("Distance (km)");
    charts.update({ ...state, units: IMPERIAL });
    expect(container.querySelector(".axis-label").textContent).toBe("Distance (mi)");
    expect(titles(container)).toEqual([
      "Elevation (ft)", "Temperature (°F)", "Head/tailwind (mph)", "Rain (in/h)",
      "Sky (cloud %, sunshine)",
    ]);
    // Ticks are in miles: the last tick can't exceed the route length in miles.
    const ticks = [...container.querySelectorAll(".axis-x .tick text")]
      .map((t) => Number(t.textContent));
    expect(Math.max(...ticks)).toBeLessThanOrEqual(state.route.totalDistKm * KM_TO_MI);
    // Rain in inches: 1.2 mm is ~0.05 in, so the top tick is well under 1.
    const rainTicks = [...container.querySelectorAll(".strip")[3].querySelectorAll(".tick text")]
      .map((t) => Number(t.textContent));
    expect(Math.max(...rainTicks)).toBeGreaterThan(0);
    expect(Math.max(...rainTicks)).toBeLessThan(0.1);
  });

  it("labels temperature, wind and rain by their own settings, the rest by the preset", () => {
    const state = makeState();
    charts.update({ ...state, units: resolveUnits("metric", { wind: "mph", temp: "f" }) });
    expect(container.querySelector(".axis-label").textContent).toBe("Distance (km)");
    expect(titles(container)).toEqual([
      "Elevation (m)", "Temperature (°F)", "Head/tailwind (mph)", "Rain (mm/h)",
      "Sky (cloud %, sunshine)",
    ]);
    charts.update({ ...state, units: resolveUnits("imperial", { rain: "mm", temp: "c" }) });
    expect(container.querySelector(".axis-label").textContent).toBe("Distance (mi)");
    expect(titles(container)).toEqual([
      "Elevation (ft)", "Temperature (°C)", "Head/tailwind (mph)", "Rain (mm/h)",
      "Sky (cloud %, sunshine)",
    ]);
  });

  it("converts each strip by its own unit", () => {
    const state = makeState();
    // Each strip's y tick labels, top to bottom.
    const stripTicks = () => [...container.querySelectorAll(".strip")]
      .map((strip) => [...strip.querySelectorAll(".axis-y .tick text")].map((t) => t.textContent));
    charts.update(state);
    const metric = stripTicks();
    charts.update({ ...state, units: IMPERIAL });
    const imperial = stripTicks();
    // Strips: elevation, temperature, head/tailwind, rain, then sky, whose
    // percentages have no unit. Each mixed set's strips match the preset its
    // own unit comes from.
    metric.slice(0, 4).forEach((ticks, i) => expect(imperial[i]).not.toEqual(ticks));
    expect(imperial[4]).toEqual(metric[4]);
    charts.update({ ...state, units: resolveUnits("metric", { wind: "mph", rain: "in" }) });
    expect(stripTicks()).toEqual([metric[0], metric[1], imperial[2], imperial[3], metric[4]]);
    charts.update({ ...state, units: resolveUnits("imperial", { temp: "c" }) });
    expect(stripTicks()).toEqual([imperial[0], metric[1], imperial[2], imperial[3], metric[4]]);
  });

  it("still accepts a plain system string", () => {
    charts.update(makeState(undefined, { units: "imperial" }));
    expect(container.querySelector(".axis-label").textContent).toBe("Distance (mi)");
    expect(titles(container)[1]).toBe("Temperature (°F)");
  });

  // --- pointer ---

  it("seeks on mouse down and follows the drag, bracketed by scrub start/end", () => {
    const state = makeState();
    charts.update(state);
    const { left, right } = plotRange(container);
    const svg = svgOf(container);
    const at = (fraction) => left + (right - left) * fraction;
    hitOf(container).dispatchEvent(pointer("pointerdown", { clientX: at(0.25) }));
    expect(calls[0]).toEqual(["start"]);
    expect(seeks()[0]).toBeCloseTo(state.route.totalDistKm * 0.25, 6);
    svg.dispatchEvent(pointer("pointermove", { clientX: at(0.5) }));
    expect(seeks()[1]).toBeCloseTo(state.route.totalDistKm * 0.5, 6);
    svg.dispatchEvent(pointer("pointerup", { clientX: at(0.5) }));
    expect(calls.at(-1)).toEqual(["end"]);
    svg.dispatchEvent(pointer("pointermove", { clientX: at(0.75) }));
    expect(seeks()).toHaveLength(2);
    // Outside the plot clamps to the route's ends.
    hitOf(container).dispatchEvent(pointer("pointerdown", { clientX: 0 }));
    expect(seeks()[2]).toBe(0);
  });

  it("ignores non-primary mouse buttons", () => {
    charts.update(makeState());
    hitOf(container).dispatchEvent(pointer("pointerdown", { clientX: 200, button: 2 }));
    expect(calls).toEqual([]);
  });

  it("does not seek on touch down, but on a sideways drag", () => {
    const state = makeState();
    charts.update(state);
    const svg = svgOf(container);
    const touch = { pointerType: "touch" };
    hitOf(container).dispatchEvent(pointer("pointerdown", { ...touch, clientX: 200, clientY: 100 }));
    expect(calls).toEqual([]);
    // Under the slop: still undecided.
    svg.dispatchEvent(pointer("pointermove", { ...touch, clientX: 204, clientY: 101 }));
    expect(calls).toEqual([]);
    svg.dispatchEvent(pointer("pointermove", { ...touch, clientX: 230, clientY: 104 }));
    expect(calls.map(([kind]) => kind)).toEqual(["start", "seek"]);
    svg.dispatchEvent(pointer("pointermove", { ...touch, clientX: 300, clientY: 140 }));
    svg.dispatchEvent(pointer("pointerup", { ...touch, clientX: 300, clientY: 140 }));
    expect(calls.map(([kind]) => kind)).toEqual(["start", "seek", "seek", "end"]);
  });

  it("leaves a vertical touch swipe to the page", () => {
    charts.update(makeState());
    const svg = svgOf(container);
    const touch = { pointerType: "touch" };
    hitOf(container).dispatchEvent(pointer("pointerdown", { ...touch, clientX: 200, clientY: 100 }));
    svg.dispatchEvent(pointer("pointermove", { ...touch, clientX: 203, clientY: 140 }));
    svg.dispatchEvent(pointer("pointermove", { ...touch, clientX: 260, clientY: 150 }));
    svg.dispatchEvent(pointer("pointerup", { ...touch, clientX: 260, clientY: 150 }));
    expect(calls).toEqual([]);
  });

  it("seeks on a touch tap, but not on a cancelled touch", () => {
    const state = makeState();
    charts.update(state);
    const { left, right } = plotRange(container);
    const svg = svgOf(container);
    const touch = { pointerType: "touch", clientX: (left + right) / 2, clientY: 50 };
    hitOf(container).dispatchEvent(pointer("pointerdown", touch));
    svg.dispatchEvent(pointer("pointerup", touch));
    expect(calls).toHaveLength(1);
    expect(seeks()[0]).toBeCloseTo(state.route.totalDistKm / 2, 6);
    hitOf(container).dispatchEvent(pointer("pointerdown", touch));
    svg.dispatchEvent(pointer("pointercancel", touch));
    expect(calls).toHaveLength(1);
  });

  it("seeks in km whatever the display units", () => {
    const state = makeState(undefined, { units: IMPERIAL });
    charts.update(state);
    const { left, right } = plotRange(container);
    hitOf(container).dispatchEvent(pointer("pointerdown", { clientX: (left + right) / 2 }));
    expect(seeks()[0]).toBeCloseTo(state.route.totalDistKm / 2, 6);
  });

  it("treats a missing value as a gap in imperial units too, not as 0", () => {
    const state = makeState(undefined, { units: IMPERIAL });
    const results = state.results.map((r, i) => (i === 1 ? { ...r, tempC: null, headKmh: null } : r));
    charts.update({ ...state, results });
    expect(container.querySelector(".s-temp").getAttribute("d").match(/M/g)).toHaveLength(2);
    expect(container.querySelector(".s-head").getAttribute("d").match(/M/g)).toHaveLength(2);
  });

  it("starts a seek only from the plot area, not titles or axes", () => {
    charts.update(makeState());
    container.querySelector(".strip-title")
      .dispatchEvent(pointer("pointerdown", { clientX: 200 }));
    container.querySelector(".axis-x").dispatchEvent(pointer("pointerdown", { clientX: 200 }));
    svgOf(container).dispatchEvent(pointer("pointerdown", { clientX: 200 }));
    expect(calls).toEqual([]);
  });

  it("ignores pointer input before anything is drawn", () => {
    svgOf(container).dispatchEvent(pointer("pointerdown", { clientX: 100 }));
    expect(calls).toEqual([]);
  });

  // --- resize ---

  it("rebuilds at the new width on the next frame, once per burst of resizes", () => {
    charts.update(makeState());
    setWidth(container, 400);
    window.dispatchEvent(new Event("resize"));
    window.dispatchEvent(new Event("resize"));
    expect(frames).toHaveLength(1);
    expect(svgOf(container).getAttribute("width")).toBe("600");
    runFrames();
    expect(svgOf(container).getAttribute("width")).toBe("400");
  });

  it("skips the rebuild when the width is unchanged", () => {
    charts.update(makeState());
    const strip = container.querySelector(".strip");
    window.dispatchEvent(new Event("resize"));
    runFrames();
    expect(container.querySelector(".strip")).toBe(strip);
  });

  it("destroy empties the container", () => {
    charts.update(makeState());
    charts.destroy();
    expect(container.children).toHaveLength(0);
  });
});

// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from "vitest";

import { createReadoutUI, readoutValues } from "../js/readout-view.js";
import { buildRoute } from "../js/route.js";
import { formatDateTime } from "../js/display.js";
import { MISSING, resolveUnits } from "../js/units.js";

// --- fixtures ---

const POINTS = [
  { lat: 54.4, lon: -3.2, ele: 100 },
  { lat: 54.5, lon: -3.2, ele: 300 },
];

const START = Date.UTC(2026, 9, 26, 9, 0);

function makeState(overrides = {}) {
  return {
    route: buildRoute(POINTS),
    startMs: START,
    speedKmh: 5,
    units: resolveUnits("metric"),
    ...overrides,
  };
}

const WEATHER = {
  tempC: 7.6,
  windKmh: 24,
  windFromDeg: 225,
  gustKmh: 41,
  headKmh: 12,
  crossKmh: -8,
  precipMm: 0.4,
  precipProb: 62.4,
  code: 61,
  model10mWindKmh: 15,
  cellElev: 340,
  eleUsed: 180,
  eleFromModel: false,
  outsideForecast: false,
};

// --- values ---

describe("readoutValues", () => {
  it("formats every field at the position", () => {
    const state = makeState();
    const total = state.route.totalDistKm;
    const v = readoutValues(state, WEATHER, 5);
    expect(v.time).toBe(formatDateTime(START + 3600 * 1000));
    expect(v.elapsed).toBe("1 h 00 min");
    expect(v.distance).toBe(`5.0 km of ${total.toFixed(1)} km`);
    // GPX elevation interpolated along the route, not the weather's eleUsed.
    expect(v.elevation).toBe(`${Math.round(100 + (200 * 5) / total)} m`);
    expect(v.weather).toBe("Light rain");
    expect(v.temp).toBe("8 °C");
    expect(v.wind).toBe("24 km/h from SW · gusts 41 km/h");
    expect(v.relative).toBe("12 km/h headwind · 8 km/h from left");
    expect(v.rain).toBe("0.4 mm/h · 62% chance");
    expect(v.model).toBe("10 m wind 15 km/h · cell elevation 340 m");
  });

  it("marks rain from a 3-hour forecast step as its average", () => {
    const v = readoutValues(makeState(), { ...WEATHER, precipStepH: 3 }, 5);
    expect(v.rain).toBe("0.4 mm/h (3-h avg) · 62% chance");
  });

  it("uses imperial units", () => {
    const v = readoutValues(makeState({ units: resolveUnits("imperial") }), WEATHER, 5);
    expect(v.distance).toMatch(/^3\.1 mi of /);
    expect(v.temp).toBe("46 °F");
    expect(v.wind).toBe("15 mph from SW · gusts 25 mph");
    expect(v.rain).toBe("0.02 in/h · 62% chance");
    // A plain system string reads as that preset.
    expect(readoutValues(makeState({ units: "imperial" }), WEATHER, 5)).toEqual(v);
  });

  it("shows every wind in the wind unit, temperature and rain in theirs", () => {
    const units = resolveUnits("metric", { wind: "mph", rain: "in", temp: "f" });
    const v = readoutValues(makeState({ units }), WEATHER, 5);
    expect(v.wind).toBe("15 mph from SW · gusts 25 mph");
    expect(v.relative).toBe("7 mph headwind · 5 mph from left");
    expect(v.model).toBe("10 m wind 9 mph · cell elevation 340 m");
    expect(v.temp).toBe("46 °F");
    expect(v.rain).toBe("0.02 in/h · 62% chance");
    // Distance and elevation stay on the preset.
    expect(v.distance).toMatch(/^5\.0 km of /);
    expect(v.elevation).toMatch(/^\d+ m$/);
  });

  it("marks a model-only elevation when the route has none", () => {
    const route = buildRoute(POINTS.map(({ lat, lon }) => ({ lat, lon })));
    const state = makeState({ route });
    expect(readoutValues(state, { ...WEATHER, eleFromModel: true }, 1).elevation).toBe("180 m (model)");
    expect(readoutValues(state, WEATHER, 1).elevation).toBe("180 m");
    expect(readoutValues(state, null, 1).elevation).toBe(MISSING);
  });

  it("shows the feels-like temperature", () => {
    expect(readoutValues(makeState(), { ...WEATHER, feelsLikeC: 2.6 }, 5).feels).toBe("3 °C");
    expect(readoutValues(makeState(), WEATHER, 5).feels).toBe(MISSING);
  });

  it("says when temperature and wind are held above the top level", () => {
    const v = readoutValues(makeState(), { ...WEATHER, aboveProfile: true }, 5);
    expect(v.temp).toBe("8 °C (held at the ~3,000 m value)");
    expect(v.wind).toBe("24 km/h from SW · gusts 41 km/h (held at the ~3,000 m value)");
  });

  it("shows the time in the route's zone", () => {
    const v = readoutValues(makeState({ timeZone: "Asia/Tokyo" }), WEATHER, 5);
    // 10:00 UTC is 19:00 in Tokyo.
    expect(v.time).toBe("Mon 26 Oct 19:00");
  });

  it("says when the position is outside the forecast", () => {
    const v = readoutValues(makeState(), { ...WEATHER, outsideForecast: true }, 5);
    expect(v.weather).toBe("Light rain (outside the forecast: nearest hour shown)");
  });

  it("shows dashes for missing weather and gaps", () => {
    const v = readoutValues(makeState(), null, 5);
    for (const key of ["weather", "temp", "wind", "relative", "rain", "model"]) {
      expect(v[key], key).toBe(MISSING);
    }
    const gaps = readoutValues(makeState(), {
      ...WEATHER, windKmh: NaN, precipProb: NaN, headKmh: null, crossKmh: null,
    }, 5);
    expect(gaps.wind).toBe(`${MISSING} · gusts 41 km/h`);
    expect(gaps.rain).toBe(`0.4 mm/h · ${MISSING}`);
    expect(gaps.relative).toBe(`${MISSING} · ${MISSING}`);
  });

  it("uses the arrival time it is given", () => {
    const v = readoutValues(makeState(), WEATHER, 5, START + 30 * 60 * 1000);
    expect(v.time).toBe(formatDateTime(START + 30 * 60 * 1000));
    expect(v.elapsed).toBe("30 min");
  });

  it("has no time without a valid speed", () => {
    const v = readoutValues(makeState({ speedKmh: NaN }), WEATHER, 5);
    expect(v.time).toBe(MISSING);
    expect(v.elapsed).toBe(MISSING);
  });
});

// --- factory ---

describe("createReadoutUI", () => {
  let container;

  beforeEach(() => {
    document.body.innerHTML = "<div id=\"readout\"></div>";
    container = document.getElementById("readout");
  });

  const valueOf = (key) => container.querySelector(`.readout-${key} dd`).textContent;

  it("builds a labelled list showing dashes until updated", () => {
    createReadoutUI(container);
    const labels = [...container.querySelectorAll("dt")].map((dt) => dt.textContent);
    expect(labels).toEqual([
      "Time", "Elapsed", "Light", "Distance", "Elevation", "Weather", "Temperature",
      "Feels like", "Wind", "Head / cross", "Rain", "Sky", "Model",
    ]);
    expect(valueOf("temp")).toBe(MISSING);
  });

  it("writes the values, and only the ones that changed", () => {
    const readout = createReadoutUI(container);
    const state = makeState();
    readout.update(state, WEATHER, 5);
    expect(valueOf("temp")).toBe("8 °C");
    expect(valueOf("weather")).toBe("Light rain");
    const tempNode = container.querySelector(".readout-temp dd").firstChild;
    readout.update(state, { ...WEATHER, windKmh: 30 }, 5);
    expect(valueOf("wind")).toMatch(/^30 km\/h/);
    // Unchanged text keeps its node.
    expect(container.querySelector(".readout-temp dd").firstChild).toBe(tempNode);
  });

  it("flags the outside-forecast state on the list", () => {
    const readout = createReadoutUI(container);
    readout.update(makeState(), { ...WEATHER, outsideForecast: true }, 5);
    expect(container.querySelector(".readout").classList.contains("outside")).toBe(true);
    readout.update(makeState(), WEATHER, 5);
    expect(container.querySelector(".readout").classList.contains("outside")).toBe(false);
  });

  it("never parses values as HTML", () => {
    const readout = createReadoutUI(container);
    readout.update(makeState(), { ...WEATHER, code: 61 }, 5);
    expect(container.querySelectorAll(".readout dd *")).toHaveLength(0);
  });
});

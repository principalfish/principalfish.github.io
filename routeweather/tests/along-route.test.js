import { describe, it, expect } from "vitest";

import {
  clampDistance, nearestSpan, nearestSpanTimes, routeRain, sampleBracket, timeAtDistance,
  weatherAtDistance,
} from "../js/along-route.js";

// --- fixtures ---

const result = (overrides = {}) => ({
  tempC: 10,
  model2mTempC: 12,
  windKmh: 20,
  windFromDeg: 270,
  gustKmh: 30,
  headKmh: 5,
  crossKmh: -3,
  precipMm: 0,
  precipProb: 10,
  code: 1,
  model10mWindKmh: 15,
  model10mGustKmh: 25,
  cellElev: 300,
  eleUsed: 100,
  outsideForecast: false,
  ...overrides,
});

function makeState(overrides = {}) {
  const samples = [
    { cumDistKm: 0, ele: 100, kind: "start" },
    { cumDistKm: 10, ele: 200, kind: "peak" },
    { cumDistKm: 20, ele: null, kind: "end" },
  ];
  const results = [
    result({ tempC: 10, windKmh: 20, windFromDeg: 350, precipMm: 0, code: 1, cellElev: 300 }),
    result({
      tempC: 20, windKmh: 40, windFromDeg: 10, precipMm: 2, code: 61, cellElev: 320,
      eleUsed: 200, headKmh: 15, crossKmh: 7,
    }),
    result({ tempC: NaN, eleUsed: 330, cellElev: 330, outsideForecast: true }),
  ];
  return {
    route: { totalDistKm: 20 },
    samples,
    results,
    startMs: Date.UTC(2026, 9, 26, 9, 0),
    speedKmh: 5,
    units: "metric",
    ...overrides,
  };
}

// --- position ---

describe("clampDistance", () => {
  it("clamps to the route and treats junk as the start", () => {
    const state = makeState();
    expect(clampDistance(state, 7.5)).toBe(7.5);
    expect(clampDistance(state, -3)).toBe(0);
    expect(clampDistance(state, 99)).toBe(20);
    expect(clampDistance(state, NaN)).toBe(0);
    expect(clampDistance({ route: null }, 5)).toBe(0);
  });
});

describe("sampleBracket", () => {
  const { samples } = makeState();

  it("finds the samples either side and the fraction between them", () => {
    expect(sampleBracket(samples, 2.5)).toEqual({ i0: 0, i1: 1, frac: 0.25 });
    expect(sampleBracket(samples, 15)).toEqual({ i0: 1, i1: 2, frac: 0.5 });
  });

  it("lands exactly on a sample with a zero fraction", () => {
    expect(sampleBracket(samples, 10)).toEqual({ i0: 1, i1: 2, frac: 0 });
    expect(sampleBracket(samples, 0)).toEqual({ i0: 0, i1: 0, frac: 0 });
    expect(sampleBracket(samples, 20)).toEqual({ i0: 2, i1: 2, frac: 0 });
  });

  it("clamps outside the samples and is null without any", () => {
    expect(sampleBracket(samples, -1)).toEqual({ i0: 0, i1: 0, frac: 0 });
    expect(sampleBracket(samples, 50)).toEqual({ i0: 2, i1: 2, frac: 0 });
    expect(sampleBracket([], 1)).toBeNull();
    expect(sampleBracket(null, 1)).toBeNull();
  });
});

// --- weather ---

describe("weatherAtDistance", () => {
  it("is null without results, or with results for different samples", () => {
    expect(weatherAtDistance(makeState({ results: null }), 5)).toBeNull();
    expect(weatherAtDistance(makeState({ samples: null }), 5)).toBeNull();
    const state = makeState();
    expect(weatherAtDistance({ ...state, results: state.results.slice(1) }, 5)).toBeNull();
  });

  it("returns a sample's own values exactly on it", () => {
    const state = makeState();
    const w = weatherAtDistance(state, 10);
    expect(w.tempC).toBe(20);
    expect(w.windKmh).toBe(40);
    expect(w.windFromDeg).toBe(10);
    expect(w.code).toBe(61);
    expect(w.nearestIndex).toBe(1);
    expect(w.distKm).toBe(10);
  });

  it("interpolates temperature, gusts and head/cross linearly by distance", () => {
    const w = weatherAtDistance(makeState(), 2.5);
    expect(w.tempC).toBeCloseTo(12.5, 9);
    expect(w.headKmh).toBeCloseTo(7.5, 9);
    expect(w.crossKmh).toBeCloseTo(-0.5, 9);
    expect(w.eleUsed).toBeCloseTo(125, 9);
  });

  it("blends wind speed as a scalar and direction through north via u/v", () => {
    const w = weatherAtDistance(makeState(), 5);
    expect(w.windKmh).toBeCloseTo(30, 9);
    // 350° at 20 km/h and 10° at 40 km/h: between them, through north, and
    // pulled towards the stronger wind's 10°.
    const deg = w.windFromDeg > 180 ? w.windFromDeg - 360 : w.windFromDeg;
    expect(deg).toBeGreaterThan(0);
    expect(deg).toBeLessThan(10);
  });

  it("takes precipitation, weather code and cell elevation from the nearer sample", () => {
    const state = makeState();
    const before = weatherAtDistance(state, 4.9);
    expect(before.precipMm).toBe(0);
    expect(before.code).toBe(1);
    expect(before.cellElev).toBe(300);
    expect(before.nearestIndex).toBe(0);
    const after = weatherAtDistance(state, 5.1);
    expect(after.precipMm).toBe(2);
    expect(after.code).toBe(61);
    expect(after.cellElev).toBe(320);
    expect(after.nearestIndex).toBe(1);
  });

  it("takes the rain's forecast step from the nearer sample, with its amount", () => {
    const state = makeState();
    state.results[0] = { ...state.results[0], precipStepH: 1, precipStepEndMs: 1 };
    state.results[1] = { ...state.results[1], precipStepH: 3, precipStepEndMs: 2 };
    const after = weatherAtDistance(state, 6);
    expect([after.precipMm, after.precipStepH, after.precipStepEndMs]).toEqual([2, 3, 2]);
  });

  it("breaks an exact halfway tie towards the earlier sample", () => {
    const w = weatherAtDistance(makeState(), 5);
    expect(w.nearestIndex).toBe(0);
    expect(w.code).toBe(1);
    expect(w.precipMm).toBe(0);
  });

  it("takes the whole wind from the nearer side when one side has none", () => {
    const state = makeState();
    state.results[0] = { ...state.results[0], windKmh: NaN };
    const near0 = weatherAtDistance(state, 3);
    expect(near0.windKmh).toBeNaN();
    expect(near0.windFromDeg).toBe(350);
    const near1 = weatherAtDistance(state, 7);
    expect(near1.windKmh).toBe(40);
    expect(near1.windFromDeg).toBe(10);
    state.results[0] = { ...state.results[0], windKmh: 20, windFromDeg: NaN };
    expect(weatherAtDistance(state, 7).windFromDeg).toBe(10);
    expect(weatherAtDistance(state, 3).windFromDeg).toBeNaN();
  });

  it("uses the nearer value where one side is missing", () => {
    const state = makeState();
    // Sample 2's temperature is NaN.
    expect(weatherAtDistance(state, 12).tempC).toBe(20);
    expect(weatherAtDistance(state, 18).tempC).toBeNaN();
  });

  it("keeps a null head/cross (no bearing) as null on its side", () => {
    const state = makeState();
    state.results[0] = { ...state.results[0], headKmh: null, crossKmh: null };
    expect(weatherAtDistance(state, 1).headKmh).toBeNull();
    expect(weatherAtDistance(state, 9).headKmh).toBe(15);
  });

  it("flags the whole span after a sample outside the forecast", () => {
    const state = makeState();
    state.results[0] = { ...state.results[0], outsideForecast: true };
    state.results[2] = { ...state.results[2], outsideForecast: false };
    expect(weatherAtDistance(state, 0).outsideForecast).toBe(true);
    // 0.75 of the way to the inside sample: the outside one is still in play.
    expect(weatherAtDistance(state, 7.5).outsideForecast).toBe(true);
    expect(weatherAtDistance(state, 10).outsideForecast).toBe(false);
  });

  it("flags the whole span next to a sample outside the forecast", () => {
    const state = makeState();
    expect(weatherAtDistance(state, 17.5).outsideForecast).toBe(true);
    expect(weatherAtDistance(state, 9.9).outsideForecast).toBe(false);
    expect(weatherAtDistance(state, 10.1).outsideForecast).toBe(true);
    expect(weatherAtDistance(state, 10).outsideForecast).toBe(false);
    expect(weatherAtDistance(state, 20).outsideForecast).toBe(true);
  });

  it("says when the nearer sample's elevation was only the model's", () => {
    const state = makeState();
    expect(weatherAtDistance(state, 14).eleFromModel).toBe(false);
    expect(weatherAtDistance(state, 16).eleFromModel).toBe(true);
  });

  it("clamps the distance to the route", () => {
    const state = makeState();
    expect(weatherAtDistance(state, -5).distKm).toBe(0);
    expect(weatherAtDistance(state, 500).distKm).toBe(20);
    expect(weatherAtDistance(state, 500).eleUsed).toBe(330);
  });
});

// --- time ---

describe("timeAtDistance", () => {
  it("is the start plus distance over speed", () => {
    const state = makeState();
    expect(timeAtDistance(state, 0)).toBe(state.startMs);
    expect(timeAtDistance(state, 7.5)).toBe(state.startMs + 1.5 * 3600 * 1000);
    expect(timeAtDistance(state, 99)).toBe(state.startMs + 4 * 3600 * 1000);
  });

  it("is NaN without a valid speed or start", () => {
    expect(timeAtDistance(makeState({ speedKmh: NaN }), 5)).toBeNaN();
    expect(timeAtDistance(makeState({ speedKmh: 0 }), 5)).toBeNaN();
    expect(timeAtDistance(makeState({ startMs: NaN }), 5)).toBeNaN();
  });
});

// --- per-sample spans ---

describe("nearestSpan", () => {
  const samples = [0, 4, 10].map((cumDistKm) => ({ cumDistKm }));

  it("reaches halfway to each neighbour, and to the route's ends", () => {
    expect(nearestSpan(samples, 0, 12)).toEqual([0, 2]);
    expect(nearestSpan(samples, 1, 12)).toEqual([2, 7]);
    expect(nearestSpan(samples, 2, 12)).toEqual([7, 12]);
  });
});

describe("nearestSpanTimes", () => {
  it("is each span's time at the plan's speed", () => {
    // Spans 0–5, 5–15, 15–20 km at 5 km/h.
    expect(nearestSpanTimes(makeState())).toEqual([1, 2, 1].map((h) => h * 3600000));
  });

  it("is null without results, a route or a valid speed", () => {
    expect(nearestSpanTimes(makeState({ results: null }))).toBeNull();
    expect(nearestSpanTimes(makeState({ route: null }))).toBeNull();
    expect(nearestSpanTimes(makeState({ speedKmh: 0 }))).toBeNull();
  });
});

describe("routeRain", () => {
  it("adds each sample's rate over the time spent in its span", () => {
    // 0 mm/h for 1 h, 2 mm/h for 2 h, the end sample (0 mm/h) for 1 h.
    expect(routeRain(makeState())).toBeCloseTo(4, 9);
  });

  it("counts an hour once however many samples it is split between", () => {
    // The same 20 km at 1 mm/h: 4 h, so 4 mm, with 3 samples or 21.
    const flat = (n) => makeState({
      samples: Array.from({ length: n }, (_, i) => ({ cumDistKm: (20 * i) / (n - 1) })),
      results: Array.from({ length: n }, () => result({ precipMm: 1 })),
    });
    expect(routeRain(flat(3))).toBeCloseTo(4, 9);
    expect(routeRain(flat(21))).toBeCloseTo(4, 9);
  });

  it("skips missing amounts, and is null without a plan", () => {
    const state = makeState();
    state.results[1] = { ...state.results[1], precipMm: NaN };
    expect(routeRain(state)).toBe(0);
    expect(routeRain(makeState({ speedKmh: NaN }))).toBeNull();
  });
});

// @vitest-environment jsdom
import { describe, it, expect } from "vitest";

import {
  coldestFeelsText, coldestTempText, strongestWindText, summaryNotes,
} from "../js/results-view.js";
import { resolveUnits } from "../js/units.js";

const metric = resolveUnits("metric");

const event = (kind, km, tMs) => ({ kind, km, tMs });

describe("coldestFeelsText", () => {
  it("names the coldest feels-like temperature, where and when", () => {
    const state = {
      results: [{ feelsLikeC: 2 }, { feelsLikeC: -3.4 }, { feelsLikeC: NaN }],
      samples: [{ cumDistKm: 0 }, { cumDistKm: 6.2 }, { cumDistKm: 9 }],
      times: [Date.UTC(2026, 9, 26, 11), Date.UTC(2026, 9, 26, 13, 20), Date.UTC(2026, 9, 26, 14)],
      units: metric,
      nowMs: Date.UTC(2026, 9, 26, 9),
      timeZone: "UTC",
    };
    expect(coldestFeelsText(state)).toBe("-3 °C at 6.2 km (13:20)");
    expect(coldestFeelsText({ ...state, results: [{ feelsLikeC: NaN }] })).toBe("–");
    expect(coldestFeelsText({ ...state, results: null })).toBe("–");
  });

  it("names the coldest temperature and the strongest wind the same way", () => {
    const state = {
      results: [
        { tempC: 4, windKmh: 20 }, { tempC: 1.2, windKmh: 47.6 }, { tempC: 1.2, windKmh: 30 },
      ],
      samples: [{ cumDistKm: 0 }, { cumDistKm: 6.2 }, { cumDistKm: 9 }],
      times: [Date.UTC(2026, 9, 26, 11), Date.UTC(2026, 9, 26, 13, 20), Date.UTC(2026, 9, 26, 14)],
      units: metric,
      nowMs: Date.UTC(2026, 9, 26, 9),
      timeZone: "UTC",
    };
    // A tie keeps the first.
    expect(coldestTempText(state)).toBe("1 °C at 6.2 km (13:20)");
    expect(strongestWindText(state)).toBe("48 km/h at 6.2 km (13:20)");
    expect(strongestWindText({ ...state, units: resolveUnits("metric", { wind: "mph" }) }))
      .toBe("30 mph at 6.2 km (13:20)");
  });
});

describe("summaryNotes", () => {
  it("has nothing to say without results", () => {
    expect(summaryNotes({ results: null, units: metric })).toEqual([]);
  });

  it("says when any point is above the forecast's top level", () => {
    const results = [{ aboveProfile: false }, { aboveProfile: true }];
    expect(summaryNotes({ results, units: metric })).toEqual([
      "Above about 3,000 m, temperature and wind are held at their ~3,000 m values.",
    ]);
    expect(summaryNotes({ results, units: resolveUnits("imperial") })).toEqual([
      "Above about 9,800 ft, temperature and wind are held at their ~9,800 ft values.",
    ]);
    expect(summaryNotes({ results: [{ aboveProfile: false }], units: metric })).toEqual([]);
  });

  it("says when the route starts before sunrise or runs past sunset", () => {
    const results = [{}];
    const base = { results, units: metric, timeZone: "UTC" };
    const flat = "Sunrise and sunset are for a flat horizon; hills and valleys can hide the sun "
      + "for longer.";
    const dawn = {
      startsInDark: true, events: [event("sunrise", 3.4, Date.UTC(2026, 9, 26, 6, 43))],
    };
    expect(summaryNotes({ ...base, daylight: dawn })).toEqual([
      "Starts before sunrise: the sun rises at 06:43, 3.4 km in.", flat,
    ]);
    const dusk = {
      startsInDark: false, events: [event("sunset", 12.3, Date.UTC(2026, 9, 26, 16, 44))],
    };
    expect(summaryNotes({ ...base, daylight: dusk })).toEqual([
      "Runs past sunset: the sun sets at 16:44, 12.3 km in.", flat,
    ]);
    expect(summaryNotes({ ...base, daylight: { startsInDark: true, events: [] } }))
      .toEqual(["The whole route is in the dark.", flat]);
    expect(summaryNotes({ ...base, daylight: { startsInDark: false, events: [] } })).toEqual([]);
  });
});

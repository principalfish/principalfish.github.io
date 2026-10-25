// @vitest-environment jsdom
import { describe, it, expect } from "vitest";

import { summaryNotes } from "../js/results-view.js";
import { resolveUnits } from "../js/units.js";

const metric = resolveUnits("metric");

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
});

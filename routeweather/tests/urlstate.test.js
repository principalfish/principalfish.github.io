import { describe, it, expect } from "vitest";

import { readState, writeState, DEFAULT_STATE } from "../js/urlstate.js";
import { ACTIVITIES } from "../js/config.js";

// Imperial with its own defaults, so no overrides are written.
const IMPERIAL_UNITS = { units: "imperial", wind: "mph", rain: "in", temp: "f" };
const METRIC_UNITS = { units: "metric", wind: "kmh", rain: "mm", temp: "c" };

const FULL = {
  activity: "cycle",
  speed: 22.5,
  start: "2026-10-26T08:30:00.000Z",
  ...IMPERIAL_UNITS,
};

const FULL_QUERY = "?activity=cycle&speed=22.5&start=2026-10-26T08%3A30%3A00.000Z&units=imperial";

describe("DEFAULT_STATE", () => {
  it("defaults to walking at the walk preset speed, metric, no start", () => {
    expect(DEFAULT_STATE).toEqual({
      activity: "walk",
      speed: ACTIVITIES.walk.defaultSpeedKmh,
      start: null,
      ...METRIC_UNITS,
    });
  });
});

describe("readState", () => {
  it("reads a full valid query string", () => {
    expect(readState(FULL_QUERY)).toEqual(FULL);
  });

  it("accepts a query string without the leading ?", () => {
    expect(readState(FULL_QUERY.slice(1))).toEqual(FULL);
  });

  it("accepts a URLSearchParams", () => {
    expect(readState(new URLSearchParams(FULL_QUERY))).toEqual(FULL);
  });

  it("returns all defaults for an empty or missing query", () => {
    expect(readState("")).toEqual(DEFAULT_STATE);
    expect(readState("?")).toEqual(DEFAULT_STATE);
    expect(readState()).toEqual(DEFAULT_STATE);
  });

  it("accepts every configured activity", () => {
    for (const key of Object.keys(ACTIVITIES)) {
      expect(readState(`?activity=${key}`).activity).toBe(key);
    }
  });

  describe("missing fields fall back individually", () => {
    it("activity", () => {
      expect(readState("?speed=22.5&start=2026-10-26T08%3A30%3A00.000Z&units=imperial"))
        .toEqual({ ...FULL, activity: "walk" });
    });

    it("speed -> the activity's preset", () => {
      expect(readState("?activity=cycle&start=2026-10-26T08%3A30%3A00.000Z&units=imperial"))
        .toEqual({ ...FULL, speed: ACTIVITIES.cycle.defaultSpeedKmh });
    });

    it("speed for custom (no preset) -> the walk preset", () => {
      expect(readState("?activity=custom").speed).toBe(ACTIVITIES.walk.defaultSpeedKmh);
    });

    it("start", () => {
      expect(readState("?activity=cycle&speed=22.5&units=imperial"))
        .toEqual({ ...FULL, start: null });
    });

    it("units -> metric, with metric's wind, rain and temp", () => {
      expect(readState("?activity=cycle&speed=22.5&start=2026-10-26T08%3A30%3A00.000Z"))
        .toEqual({ ...FULL, ...METRIC_UNITS });
    });
  });

  describe("invalid fields fall back individually", () => {
    const withParam = (key, value) => {
      const params = new URLSearchParams(FULL_QUERY);
      params.set(key, value);
      return readState(`?${params}`);
    };

    it("unknown activity -> walk", () => {
      expect(withParam("activity", "swim")).toEqual({ ...FULL, activity: "walk" });
    });

    it("activity that is an Object.prototype key -> walk", () => {
      expect(withParam("activity", "toString").activity).toBe("walk");
      expect(withParam("activity", "__proto__").activity).toBe("walk");
    });

    it.each([
      "-5", "0", "0.1", "500", "fast", "", "12abc", "Infinity", "NaN", "0x10", "1e1",
    ])("speed %j -> the activity's preset", (value) => {
      expect(withParam("speed", value))
        .toEqual({ ...FULL, speed: ACTIVITIES.cycle.defaultSpeedKmh });
    });

    it.each([
      "not-a-date",
      "",
      "2026-13-45T99:99:99Z",
      "1",
      "Oct 26 2026",
      "2026-10-26",
      "2026-10-26T08:30",
      "2026-02-30T08:00:00Z",
      "2026-10-26T24:00Z",
      "<img src=x onerror=alert(1)> 1 Jan 2026",
    ])("start %j -> null", (value) => {
      expect(withParam("start", value)).toEqual({ ...FULL, start: null });
    });

    it.each(["furlongs", "Imperial", ""])("units %j -> metric", (value) => {
      expect(withParam("units", value)).toEqual({ ...FULL, ...METRIC_UNITS });
    });

    it.each([
      ["wind", "knots"], ["wind", "MPH"], ["wind", "km/h"], ["wind", ""],
      ["rain", "cm"], ["rain", "IN"], ["temp", "k"], ["temp", "F"], ["temp", "°c"],
      ["temp", "__proto__"],
    ])("%s %j -> the preset's default", (key, value) => {
      expect(withParam(key, value)).toEqual(FULL);
      expect(readState(`?${key}=${encodeURIComponent(value)}`)).toEqual(DEFAULT_STATE);
    });
  });

  describe("unit overrides", () => {
    it("reads each override over the metric preset", () => {
      expect(readState("?wind=mph")).toEqual({ ...DEFAULT_STATE, wind: "mph" });
      expect(readState("?rain=in")).toEqual({ ...DEFAULT_STATE, rain: "in" });
      expect(readState("?temp=f")).toEqual({ ...DEFAULT_STATE, temp: "f" });
    });

    it("reads overrides over the imperial preset", () => {
      expect(readState("?units=imperial&wind=kmh&temp=c")).toEqual({
        ...DEFAULT_STATE, units: "imperial", wind: "kmh", rain: "in", temp: "c",
      });
    });

    it("accepts an override equal to the preset's default", () => {
      expect(readState("?units=metric&wind=kmh&rain=mm&temp=c")).toEqual(DEFAULT_STATE);
    });
  });
});

describe("writeState", () => {
  it("writes every set field as a ?-prefixed query string", () => {
    expect(writeState(FULL)).toBe(FULL_QUERY);
  });

  it("omits a null start", () => {
    const query = writeState({ ...FULL, start: null });
    expect(query).toBe("?activity=cycle&speed=22.5&units=imperial");
    expect(new URLSearchParams(query).has("start")).toBe(false);
  });

  it("writes the same query as before for callers without overrides", () => {
    const { activity, speed, start, units } = FULL;
    expect(writeState({ activity, speed, start, units })).toBe(FULL_QUERY);
  });

  it("writes only the overrides that differ from the preset's default", () => {
    expect(writeState({ ...FULL, wind: "kmh" })).toBe(`${FULL_QUERY}&wind=kmh`);
    expect(writeState({ units: "metric", wind: "mph", rain: "mm", temp: "f" }))
      .toBe("?units=metric&wind=mph&temp=f");
    expect(writeState({ units: "imperial", wind: "kmh", rain: "mm", temp: "c" }))
      .toBe("?units=imperial&wind=kmh&rain=mm&temp=c");
  });

  it("compares overrides with metric when units is unset", () => {
    expect(writeState({ wind: "kmh", rain: "mm", temp: "c" })).toBe("?");
    expect(writeState({ wind: "mph" })).toBe("?wind=mph");
  });

  it("omits invalid overrides", () => {
    expect(writeState({ units: "metric", wind: "knots", rain: "", temp: null }))
      .toBe("?units=metric");
  });

  it("omits undefined and empty values", () => {
    expect(writeState({ activity: "run", speed: undefined, start: "", units: "metric" }))
      .toBe("?activity=run&units=metric");
  });

  it("rounds speed to 2 dp", () => {
    expect(writeState({ speed: 16.093444978925632 })).toBe("?speed=16.09");
  });

  it("omits a non-finite speed", () => {
    expect(writeState({ speed: NaN })).toBe("?");
  });

  it("returns a bare ? when nothing is set", () => {
    expect(writeState({})).toBe("?");
    expect(writeState()).toBe("?");
  });
});

describe("round trip", () => {
  it("readState(writeState(state)) reproduces a full valid state", () => {
    expect(readState(writeState(FULL))).toEqual(FULL);
  });

  it("reproduces a state with a null start", () => {
    const state = { ...FULL, start: null };
    expect(readState(writeState(state))).toEqual(state);
  });

  it("accepts a start without seconds", () => {
    const state = { ...FULL, start: "2026-10-26T08:30Z" };
    expect(readState(writeState(state))).toEqual(state);
  });

  it("accepts Feb 29 in a leap year", () => {
    expect(readState("?start=2028-02-29T08%3A00Z").start).toBe("2028-02-29T08:00Z");
  });

  it("preserves a start with a non-UTC offset exactly", () => {
    const state = { ...FULL, start: "2026-10-26T08:30:00+01:00" };
    expect(readState(writeState(state))).toEqual(state);
  });

  it.each([
    { ...METRIC_UNITS, wind: "mph" },
    { ...METRIC_UNITS, rain: "in", temp: "f" },
    { ...IMPERIAL_UNITS, wind: "kmh", rain: "mm", temp: "c" },
    { ...IMPERIAL_UNITS, temp: "c" },
  ])("reproduces unit overrides %j", (units) => {
    const state = { ...FULL, ...units };
    expect(readState(writeState(state))).toEqual(state);
  });

  it("reproduces the defaults", () => {
    expect(readState(writeState(DEFAULT_STATE))).toEqual(DEFAULT_STATE);
  });
});

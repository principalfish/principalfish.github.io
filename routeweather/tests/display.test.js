import { describe, it, expect } from "vitest";

import {
  compassPoint, formatArrival, formatCell, formatClock, formatCrossWind, formatDateTime,
  formatDuration, formatHeadWind,
} from "../js/display.js";
import { MISSING } from "../js/units.js";

const local = (...args) => new Date(...args).getTime();

describe("times", () => {
  it("formats a local clock", () => {
    expect(formatClock(local(2026, 9, 26, 7, 5))).toBe("07:05");
  });

  it("adds the weekday only when not today", () => {
    const now = local(2026, 9, 26, 9, 0); // a Monday
    expect(formatArrival(local(2026, 9, 26, 13, 30), now)).toBe("13:30");
    expect(formatArrival(local(2026, 9, 27, 1, 0), now)).toBe("Tue 01:00");
    expect(formatArrival(NaN, now)).toBe(MISSING);
  });

  it("formats a full local date-time", () => {
    expect(formatDateTime(local(2026, 9, 26, 10, 0))).toBe("Mon 26 Oct 10:00");
    expect(formatDateTime(null)).toBe(MISSING);
  });

  it("formats durations", () => {
    expect(formatDuration(45 * 60000)).toBe("45 min");
    expect(formatDuration((3 * 60 + 5) * 60000 + 20000)).toBe("3 h 05 min");
    expect(formatDuration(NaN)).toBe(MISSING);
    expect(formatDuration(-1)).toBe(MISSING);
  });
});

describe("compassPoint", () => {
  it("maps degrees onto 16 or 8 points, wrapping", () => {
    expect(compassPoint(0)).toBe("N");
    expect(compassPoint(225)).toBe("SW");
    expect(compassPoint(350)).toBe("N");
    expect(compassPoint(22.5)).toBe("NNE");
    expect(compassPoint(-90)).toBe("W");
    expect(compassPoint(22.5, 8)).toBe("NE");
    expect(compassPoint(340, 8)).toBe("N");
    expect(compassPoint(NaN)).toBe(MISSING);
  });
});

describe("relative wind", () => {
  it("labels head and tail wind", () => {
    expect(formatHeadWind(12.4, "metric")).toBe("12 km/h headwind");
    expect(formatHeadWind(-8, "metric")).toBe("8 km/h tailwind");
    expect(formatHeadWind(null, "metric")).toBe(MISSING);
  });

  it("labels the crosswind side", () => {
    expect(formatCrossWind(8, "metric")).toBe("8 km/h from right");
    expect(formatCrossWind(-16.0934, "imperial")).toBe("10 mph from left");
    expect(formatCrossWind(null, "metric")).toBe(MISSING);
  });
});

describe("formatCell", () => {
  it("shows the cell centre and its offset from the point", () => {
    const cell = { lat: 54.4482, lon: -3.2115, offsetKm: 0.71, offsetBearingDeg: 230 };
    expect(formatCell(cell, "metric")).toBe("54.448, −3.212 · 0.7 km SW");
    expect(formatCell(cell, "imperial")).toBe("54.448, −3.212 · 0.4 mi SW");
  });

  it("drops a meaningless bearing for a cell on top of the point", () => {
    const cell = { lat: 51.5, lon: 0.1, offsetKm: 0.01, offsetBearingDeg: 0 };
    expect(formatCell(cell, "metric")).toBe("51.500, 0.100 · 0.0 km");
  });

  it("handles missing data", () => {
    expect(formatCell(null, "metric")).toBe(MISSING);
    expect(formatCell({ lat: NaN, lon: 1 }, "metric")).toBe(MISSING);
    expect(formatCell({ lat: 1, lon: 2, offsetKm: NaN }, "metric")).toBe("1.000, 2.000");
  });
});

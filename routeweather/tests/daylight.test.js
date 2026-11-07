import { describe, it, expect } from "vitest";

import { daylightAt, routeDaylight, routeSunshine, startDaySun } from "../js/daylight.js";
import { buildRoute } from "../js/route.js";
import { sunTimes } from "../js/sun.js";
import { formatClock, formatDaylight } from "../js/display.js";

const HOUR = 3600 * 1000;
const MINUTE = 60 * 1000;

// A 10 km route due east from central London.
const ROUTE = buildRoute(
  Array.from({ length: 11 }, (_, i) => ({ lat: 51.5074, lon: -0.1278 + i * 0.01444, ele: 20 })),
);
// London, 26 Oct 2026: sunrise 06:43, sunset 16:44 UTC (sun.test.js).
const { sunriseMs, sunsetMs } = sunTimes(
  Date.UTC(2026, 9, 26), Date.UTC(2026, 9, 27), 51.5074, -0.1278,
);

const plan = (startMs, overrides = {}) => ({
  route: ROUTE, startMs, speedKmh: 5, timeZone: null, ...overrides,
});

describe("routeDaylight", () => {
  it("is all daylight in the middle of the day", () => {
    const d = routeDaylight(plan(Date.UTC(2026, 9, 26, 11)));
    expect(d.startsInDark).toBe(false);
    expect(d.events).toEqual([]);
    expect(d.nightSpans).toEqual([]);
    expect(d.daylightMs).toBeCloseTo((ROUTE.totalDistKm / 5) * HOUR, -3);
  });

  it("finds sunset part-way and shades the rest", () => {
    // Start an hour before sunset: dark from just before 5 km, as the sun
    // sets slightly earlier further east.
    const d = routeDaylight(plan(sunsetMs - HOUR));
    expect(d.startsInDark).toBe(false);
    expect(d.events).toHaveLength(1);
    const [sunset] = d.events;
    expect(sunset.kind).toBe("sunset");
    expect(sunset.km).toBeGreaterThan(4.5);
    expect(sunset.km).toBeLessThan(5);
    expect(Math.abs(sunset.tMs - sunsetMs)).toBeLessThan(5 * MINUTE);
    expect(d.nightSpans).toHaveLength(1);
    expect(d.nightSpans[0][0]).toBeCloseTo(sunset.km, 9);
    expect(d.nightSpans[0][1]).toBeCloseTo(ROUTE.totalDistKm, 9);
    expect(d.daylightMs).toBeCloseTo((sunset.km / 5) * HOUR, -3);
  });

  it("starts in the dark before sunrise", () => {
    const d = routeDaylight(plan(sunriseMs - HOUR));
    expect(d.startsInDark).toBe(true);
    expect(d.events.map((e) => e.kind)).toEqual(["sunrise"]);
    expect(d.nightSpans[0][0]).toBe(0);
  });

  it("is null without a valid plan", () => {
    expect(routeDaylight(plan(NaN))).toBeNull();
    expect(routeDaylight(plan(sunriseMs, { speedKmh: NaN }))).toBeNull();
    expect(routeDaylight(plan(sunriseMs, { route: null }))).toBeNull();
  });
});

describe("routeSunshine", () => {
  const samples = [0, 5, 10].map((cumDistKm) => ({ cumDistKm }));

  it("adds each sample's sunshine over its stretch of the route", () => {
    // Stretches: 0–2.5, 2.5–7.5, 7.5–end km, i.e. 0.5 h, 1 h and ~0.5 h at 5 km/h.
    const results = [{ sunMin: 60 }, { sunMin: 30 }, { sunMin: 0 }];
    const s = routeSunshine({ ...plan(0), samples, results });
    expect(s.sunMs).toBeCloseTo(0.5 * HOUR + 0.5 * HOUR, -3);
    expect(s.estimated).toBe(false);
  });

  it("flags estimates and skips gaps", () => {
    const results = [{ sunMin: NaN }, { sunMin: 30, sunEstimated: true }, { sunMin: 0 }];
    const s = routeSunshine({ ...plan(0), samples, results });
    expect(s.sunMs).toBeCloseTo(0.5 * HOUR, -3);
    expect(s.estimated).toBe(true);
    expect(routeSunshine({ ...plan(0), samples, results: null })).toBeNull();
  });
});

describe("startDaySun", () => {
  it("uses the start's calendar day in the route's zone", () => {
    const sun = startDaySun(plan(Date.UTC(2026, 9, 26, 9), { timeZone: "Europe/London" }));
    expect(sun.dayStartMs).toBe(Date.UTC(2026, 9, 26));
    expect(formatClock(sun.sunriseMs, "UTC")).toBe("06:43");
    // 23:30 UTC on the 26th is already the 27th in Tokyo: that day's times.
    const tokyo = startDaySun(plan(Date.UTC(2026, 9, 26, 23, 30), { timeZone: "Asia/Tokyo" }));
    expect(tokyo.dayStartMs).toBe(Date.UTC(2026, 9, 26, 15));
  });
});

describe("daylightAt", () => {
  it("says whether it is light and when that changes", () => {
    const t = sunsetMs - 80 * MINUTE;
    const d = daylightAt(plan(t), 0, t);
    expect(d.up).toBe(true);
    expect(d.next.kind).toBe("sunset");
    expect(formatDaylight(d, t, "UTC")).toBe("Daylight · sunset 16:44 (in 1 h 20 min)");
    expect(daylightAt(plan(t), 0, NaN)).toBeNull();
  });
});

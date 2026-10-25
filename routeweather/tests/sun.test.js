import { describe, it, expect } from "vitest";

import {
  daylightMinutes, isDaylight, nextSunEvent, solarElevationDeg, sunEvents, sunTimes,
} from "../js/sun.js";

const MINUTE = 60 * 1000;
const utcDay = (y, m, d) => [Date.UTC(y, m - 1, d), Date.UTC(y, m - 1, d + 1)];
const clock = (ms) => new Date(ms).toISOString().slice(11, 16);

// Reference times (UTC) from the `astral` Python library for the same days.
const LONDON = [51.5074, -0.1278];
const DENVER = [39.74, -104.99];
const TROMSO = [69.65, 18.96];

describe("sunTimes", () => {
  it("matches reference sunrise and sunset to the minute", () => {
    const oct = sunTimes(...utcDay(2026, 10, 26), ...LONDON);
    expect(clock(oct.sunriseMs)).toBe("06:43");
    expect(clock(oct.sunsetMs)).toBe("16:44");
    expect(oct.allDay).toBeNull();
    const june = sunTimes(...utcDay(2026, 6, 21), ...LONDON);
    expect(clock(june.sunriseMs)).toBe("03:43");
    expect(clock(june.sunsetMs)).toBe("20:21");
  });

  it("works west of Greenwich, where sunset falls on the next UTC day", () => {
    const t = sunTimes(...utcDay(2026, 10, 26), ...DENVER);
    expect(clock(t.sunriseMs)).toBe("13:22");
    expect(clock(t.sunsetMs)).toBe("00:06");
  });

  it("reports polar night and midnight sun", () => {
    expect(sunTimes(...utcDay(2026, 12, 21), ...TROMSO))
      .toEqual({ sunriseMs: null, sunsetMs: null, allDay: "down" });
    expect(sunTimes(...utcDay(2026, 6, 21), ...TROMSO))
      .toEqual({ sunriseMs: null, sunsetMs: null, allDay: "up" });
  });
});

describe("solar elevation", () => {
  it("is high at noon and below the horizon at midnight", () => {
    const noon = solarElevationDeg(Date.UTC(2026, 9, 26, 11, 44), ...LONDON);
    expect(noon).toBeGreaterThan(25);
    expect(noon).toBeLessThan(27);
    expect(isDaylight(Date.UTC(2026, 9, 26, 12), ...LONDON)).toBe(true);
    expect(isDaylight(Date.UTC(2026, 9, 26, 0), ...LONDON)).toBe(false);
  });

  it("is NaN for missing input", () => {
    expect(solarElevationDeg(NaN, ...LONDON)).toBeNaN();
    expect(solarElevationDeg(0, NaN, 0)).toBeNaN();
    expect(sunEvents(NaN, 0, ...LONDON)).toEqual([]);
  });
});

describe("sunEvents / nextSunEvent / daylightMinutes", () => {
  it("lists a day's sunrise then sunset", () => {
    const events = sunEvents(...utcDay(2026, 10, 26), ...LONDON);
    expect(events.map((e) => e.kind)).toEqual(["sunrise", "sunset"]);
  });

  it("finds the next event from any time", () => {
    const next = nextSunEvent(Date.UTC(2026, 9, 26, 12), ...LONDON);
    expect(next.kind).toBe("sunset");
    expect(clock(next.tMs)).toBe("16:44");
    expect(nextSunEvent(Date.UTC(2026, 11, 21), ...TROMSO)).toBeNull();
  });

  it("adds up the daylight in a window", () => {
    const [start, end] = utcDay(2026, 10, 26);
    const { sunriseMs, sunsetMs } = sunTimes(start, end, ...LONDON);
    expect(daylightMinutes(start, end, ...LONDON)).toBeCloseTo((sunsetMs - sunriseMs) / MINUTE, 0);
    // An hour around sunset: half of it light, give or take a minute.
    const half = daylightMinutes(sunsetMs - 30 * MINUTE, sunsetMs + 30 * MINUTE, ...LONDON);
    expect(half).toBeCloseTo(30, 0);
    expect(daylightMinutes(end, start, ...LONDON)).toBe(0);
  });
});

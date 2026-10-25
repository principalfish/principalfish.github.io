import { describe, it, expect } from "vitest";

import {
  isValidTimeZone, sameZonedDay, zoneAbbreviation, zoneLabel, zoneOffsetMs, zonedParts,
  zonedTimeToMs,
} from "../js/zone.js";
import {
  fromLocalInputValue, HOUR_MS, nextWholeHour, toLocalInputValue,
} from "../js/schedule.js";
import { formatArrival, formatClock, formatDateTime } from "../js/display.js";

// Every check names its zone, so none depends on the machine's TZ.

describe("zonedParts", () => {
  it("reads the wall clock in a named zone", () => {
    // 09:30 UTC on Mon 26 Oct 2026 is 10:30 in Zurich (CET) and 18:30 in Tokyo.
    const ms = Date.UTC(2026, 9, 26, 9, 30, 15);
    expect(zonedParts(ms, "Europe/Zurich")).toEqual({
      year: 2026, month: 10, day: 26, hour: 10, minute: 30, second: 15, weekday: 1,
    });
    expect(zonedParts(ms, "Asia/Tokyo").hour).toBe(18);
  });

  it("reads midnight as hour 0, not 24", () => {
    expect(zonedParts(Date.UTC(2026, 9, 26, 15, 0), "Asia/Tokyo").hour).toBe(0);
  });

  it("gives NaN parts for a non-finite time", () => {
    expect(zonedParts(NaN, "Europe/Zurich").hour).toBeNaN();
    expect(zonedParts(null).hour).toBeNaN();
  });
});

describe("zoneOffsetMs", () => {
  it("follows daylight saving", () => {
    expect(zoneOffsetMs(Date.UTC(2026, 6, 1), "Europe/London")).toBe(HOUR_MS);
    expect(zoneOffsetMs(Date.UTC(2026, 11, 1), "Europe/London")).toBe(0);
    expect(zoneOffsetMs(Date.UTC(2026, 11, 1), "Asia/Kathmandu")).toBe(5.75 * HOUR_MS);
    expect(zoneOffsetMs(Date.UTC(2026, 11, 1), "America/New_York")).toBe(-5 * HOUR_MS);
  });
});

describe("zonedTimeToMs", () => {
  const wall = (year, month, day, hour, minute = 0) => ({ year, month, day, hour, minute });

  it("inverts zonedParts away from clock changes", () => {
    expect(zonedTimeToMs(wall(2026, 10, 26, 10, 30), "Europe/Zurich"))
      .toBe(Date.UTC(2026, 9, 26, 9, 30));
    expect(zonedTimeToMs(wall(2026, 10, 27, 0), "Asia/Tokyo")).toBe(Date.UTC(2026, 9, 26, 15));
  });

  it("reads a repeated hour as the earlier instant", () => {
    // 01:30 on 25 Oct 2026 in London is 00:30 UTC (BST) and 01:30 UTC (GMT).
    expect(zonedTimeToMs(wall(2026, 10, 25, 1, 30), "Europe/London"))
      .toBe(Date.UTC(2026, 9, 25, 0, 30));
    // New York falls back on 1 Nov 2026: 01:30 EDT is 05:30 UTC.
    expect(zonedTimeToMs(wall(2026, 11, 1, 1, 30), "America/New_York"))
      .toBe(Date.UTC(2026, 10, 1, 5, 30));
  });

  it("moves a skipped time on by the gap", () => {
    // 01:30 on 29 Mar 2026 never happens in London; it reads as 02:30 BST.
    const ms = zonedTimeToMs(wall(2026, 3, 29, 1, 30), "Europe/London");
    expect(ms).toBe(Date.UTC(2026, 2, 29, 1, 30));
    expect(zonedParts(ms, "Europe/London").hour).toBe(2);
  });

  it("is NaN for non-finite parts", () => {
    expect(zonedTimeToMs(wall(NaN, 1, 1, 0), "Europe/London")).toBeNaN();
  });
});

describe("zone names", () => {
  it("validates IANA names", () => {
    expect(isValidTimeZone("Europe/Zurich")).toBe(true);
    expect(isValidTimeZone("Not/AZone")).toBe(false);
    expect(isValidTimeZone("")).toBe(false);
    expect(isValidTimeZone(null)).toBe(false);
  });

  it("labels a zone with its abbreviation at that time", () => {
    expect(zoneLabel(Date.UTC(2026, 6, 1), "Europe/London")).toBe("Europe/London (BST)");
    expect(zoneLabel(Date.UTC(2026, 11, 1), "Europe/London")).toBe("Europe/London (GMT)");
    expect(zoneLabel(Date.UTC(2026, 11, 1), "America/New_York"))
      .toMatch(/^America\/New York \((EST|GMT-5)\)$/);
    expect(zoneLabel(Date.UTC(2026, 11, 1), null)).toBe("");
  });

  it("falls back to a GMT offset where there is no abbreviation", () => {
    expect(zoneAbbreviation(Date.UTC(2026, 11, 1), "Asia/Kathmandu")).toBe("GMT+5:45");
  });
});

describe("zoned schedule and display", () => {
  const ms = Date.UTC(2026, 9, 26, 9, 30);

  it("round-trips the start input in the route's zone", () => {
    expect(toLocalInputValue(ms, "Asia/Tokyo")).toBe("2026-10-26T18:30");
    expect(fromLocalInputValue("2026-10-26T18:30", "Asia/Tokyo")).toBe(ms);
    expect(fromLocalInputValue("2026-10-26T18:30:00", "Asia/Tokyo")).toBe(ms);
  });

  it("rejects dates that don't exist", () => {
    expect(fromLocalInputValue("2026-02-30T10:00", "Asia/Tokyo")).toBeNaN();
    expect(fromLocalInputValue("2026-13-01T10:00", "Asia/Tokyo")).toBeNaN();
    expect(fromLocalInputValue("2026-01-01T24:00", "Asia/Tokyo")).toBeNaN();
  });

  it("defaults to the next whole hour on the route's clock", () => {
    // 09:30 UTC is 15:15 in Kathmandu: the next whole hour there is 16:00.
    const next = nextWholeHour(ms, "Asia/Kathmandu");
    expect(toLocalInputValue(next, "Asia/Kathmandu")).toBe("2026-10-26T16:00");
    expect(next - ms).toBe(45 * 60 * 1000);
  });

  it("formats times and days in the route's zone", () => {
    expect(formatClock(ms, "Asia/Tokyo")).toBe("18:30");
    expect(formatDateTime(ms, "Asia/Tokyo")).toBe("Mon 26 Oct 18:30");
    // 23:30 UTC on the 26th is already Tuesday morning in Tokyo.
    const late = Date.UTC(2026, 9, 26, 23, 30);
    expect(formatArrival(late, ms, "Asia/Tokyo")).toBe("Tue 08:30");
    expect(formatArrival(late, ms, "Europe/London")).toBe("23:30");
    expect(sameZonedDay(late, ms, "Europe/London")).toBe(true);
    expect(sameZonedDay(late, NaN, "Europe/London")).toBe(false);
  });
});

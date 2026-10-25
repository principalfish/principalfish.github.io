import { describe, it, expect, beforeAll, afterAll } from "vitest";

import {
  forecastSpan, forecastWindow, fromLocalInputValue, HOUR_MS, latestStartMs, nextWholeHour,
  toLocalInputValue, windowHours,
} from "../js/schedule.js";
import { FORECAST_HOURS } from "../js/config.js";

// Sets process.env.TZ for a describe block and restores it exactly afterwards
// (deleting it if it was unset). Node applies a TZ change immediately.
function pinTimeZone(zone) {
  const original = process.env.TZ;
  beforeAll(() => { process.env.TZ = zone; });
  afterAll(() => {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  });
}

// --- zone-independent ---

describe("forecastWindow", () => {
  it("runs from the current UTC hour to the last hour of today's run", () => {
    const range = forecastWindow(Date.UTC(2026, 9, 26, 9, 20, 15));
    expect(range.startHour).toBe(Date.UTC(2026, 9, 26, 9));
    expect(range.endHour).toBe(Date.UTC(2026, 9, 26) + (FORECAST_HOURS - 1) * HOUR_MS);
    expect(new Date(range.endHour).toISOString()).toBe("2026-11-01T23:00:00.000Z");
  });

  it("counts hours inclusively", () => {
    expect(windowHours({ startHour: 0, endHour: 0 })).toBe(1);
    expect(windowHours(forecastWindow(Date.UTC(2026, 9, 26, 0, 0)))).toBe(FORECAST_HOURS);
  });
});

describe("latestStartMs", () => {
  it("subtracts the duration and floors to the minute", () => {
    const last = Date.UTC(2026, 10, 1, 23);
    expect(latestStartMs(last, 90 * 60000 + 30000)).toBe(Date.UTC(2026, 10, 1, 21, 29));
  });
});

describe("forecastSpan", () => {
  const times = Float64Array.from([0, 1, 2, 3, 4], (h) => h * HOUR_MS);
  const loc = (temps) => ({ timesMs: times, vars: { temperature_2m: Float64Array.from(temps) } });

  it("spans the whole series when every hour has data", () => {
    expect(forecastSpan([loc([1, 2, 3, 4, 5])])).toEqual({ startMs: 0, endMs: 4 * HOUR_MS });
  });

  it("trims hours missing in ANY location at either end", () => {
    const span = forecastSpan([loc([1, 2, 3, 4, NaN]), loc([NaN, 2, 3, NaN, 5])]);
    expect(span).toEqual({ startMs: HOUR_MS, endMs: 2 * HOUR_MS });
  });

  it("keeps a single usable hour", () => {
    expect(forecastSpan([loc([NaN, NaN, 3, NaN, NaN])]))
      .toEqual({ startMs: 2 * HOUR_MS, endMs: 2 * HOUR_MS });
  });

  it("throws when no hour has data everywhere", () => {
    expect(() => forecastSpan([loc([1, NaN, NaN, NaN, NaN]), loc([NaN, 2, 3, 4, 5])]))
      .toThrow(/no forecast data/);
  });
});

// --- local time, in several zones ---

describe.each([
  ["America/New_York"],
  ["Asia/Kolkata"],
  ["Europe/London"],
])("local time (TZ=%s)", (zone) => {
  pinTimeZone(zone);

  it("rounds up to the next local hour, moving on from an exact hour", () => {
    const d = new Date(2026, 9, 26, 9, 20);
    expect(nextWholeHour(d.getTime())).toBe(new Date(2026, 9, 26, 10, 0).getTime());
    const exact = new Date(2026, 9, 26, 10, 0).getTime();
    expect(nextWholeHour(exact)).toBe(new Date(2026, 9, 26, 11, 0).getTime());
  });

  it("round-trips local minutes through datetime-local values", () => {
    const ms = new Date(2026, 0, 5, 7, 3).getTime();
    expect(toLocalInputValue(ms)).toBe("2026-01-05T07:03");
    expect(fromLocalInputValue("2026-01-05T07:03")).toBe(ms);
  });

  it("drops seconds when formatting", () => {
    expect(toLocalInputValue(new Date(2026, 0, 5, 7, 3, 59).getTime())).toBe("2026-01-05T07:03");
  });

  it("rejects empty and loose input", () => {
    expect(fromLocalInputValue("")).toBeNaN();
    expect(fromLocalInputValue("2026-01-05")).toBeNaN();
    expect(fromLocalInputValue("Jan 5 2026 07:03")).toBeNaN();
    expect(fromLocalInputValue(undefined)).toBeNaN();
  });
});

describe("local time zone offsets", () => {
  describe("Asia/Kolkata (UTC+5:30)", () => {
    pinTimeZone("Asia/Kolkata");

    it("defaults to the next LOCAL hour, which is half past in UTC", () => {
      const next = nextWholeHour(Date.UTC(2026, 9, 26, 9, 20)); // 14:50 IST
      expect(new Date(next).toISOString()).toBe("2026-10-26T09:30:00.000Z"); // 15:00 IST
    });

    it("reads a datetime-local value as IST", () => {
      expect(fromLocalInputValue("2026-10-26T15:00")).toBe(Date.UTC(2026, 9, 26, 9, 30));
    });
  });

  describe("America/New_York (UTC-4 in EDT)", () => {
    pinTimeZone("America/New_York");

    it("reads a datetime-local value as EDT", () => {
      expect(fromLocalInputValue("2026-10-26T06:00")).toBe(Date.UTC(2026, 9, 26, 10));
      expect(toLocalInputValue(Date.UTC(2026, 9, 26, 10))).toBe("2026-10-26T06:00");
    });
  });
});

// Europe/London clock changes in 2026: 29 Mar 01:00 GMT -> 02:00 BST (01:xx
// never happens) and 25 Oct 02:00 BST -> 01:00 GMT (01:xx happens twice). The
// page inherits JavaScript's Date rules for these, documented here:
describe("Europe/London clock changes", () => {
  pinTimeZone("Europe/London");

  it("reads a time in the spring-forward gap with the pre-change offset (GMT)", () => {
    // 01:30 on 29 Mar doesn't exist; it is read as 01:30 GMT = 02:30 BST.
    const ms = fromLocalInputValue("2026-03-29T01:30");
    expect(ms).toBe(Date.UTC(2026, 2, 29, 1, 30));
    expect(toLocalInputValue(ms)).toBe("2026-03-29T02:30");
  });

  it("reads an ambiguous fall-back time as the EARLIER instant (BST)", () => {
    // 01:30 on 25 Oct happens at 00:30 UTC (BST) and 01:30 UTC (GMT).
    const ms = fromLocalInputValue("2026-10-25T01:30");
    expect(ms).toBe(Date.UTC(2026, 9, 25, 0, 30));
    expect(toLocalInputValue(ms)).toBe("2026-10-25T01:30");
    // The second 01:30 formats the same, so it can't be told apart in the input.
    expect(toLocalInputValue(Date.UTC(2026, 9, 25, 1, 30))).toBe("2026-10-25T01:30");
  });

  it("steps over the gap to the next real hour", () => {
    // 00:30 GMT -> next local hour 01:00 doesn't exist -> 02:00 BST (01:00 UTC).
    expect(nextWholeHour(Date.UTC(2026, 2, 29, 0, 30))).toBe(Date.UTC(2026, 2, 29, 1));
  });

  it("counts the repeated hour on the way through it", () => {
    // 01:30 BST (00:30 UTC) -> the next local hour is 01:00 GMT (01:00 UTC).
    expect(nextWholeHour(Date.UTC(2026, 9, 25, 0, 30))).toBe(Date.UTC(2026, 9, 25, 1));
  });
});

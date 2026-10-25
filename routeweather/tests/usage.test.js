import { describe, it, expect, vi, afterEach } from "vitest";

import {
  FREE_LIMITS,
  STORAGE_KEY,
  forecastCallWeight,
  elevationCallWeight,
  createUsageLog,
  formatUsageReport,
} from "../js/usage.js";
import { HOURLY_VARS } from "../js/config.js";

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    data,
  };
}

function clock(startMs) {
  let t = startMs;
  const now = () => t;
  now.advance = (ms) => {
    t += ms;
  };
  return now;
}

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

describe("forecastCallWeight", () => {
  it("counts one call per location at up to 10 variables and 2 weeks", () => {
    expect(forecastCallWeight({ locations: 3, variables: 10, hours: 168 })).toBe(3);
    expect(forecastCallWeight({ locations: 1, variables: 1, hours: 1 })).toBe(1);
  });

  it("scales fractionally with variables (15 vars over 2 weeks = 1.5)", () => {
    expect(forecastCallWeight({ locations: 1, variables: 15, hours: 14 * 24 })).toBe(1.5);
  });

  it("scales with length beyond 2 weeks (15 vars over 4 weeks = 3)", () => {
    expect(forecastCallWeight({ locations: 1, variables: 15, hours: 28 * 24 })).toBe(3);
  });

  it("costs a full 150-sample route with every hourly variable", () => {
    const perLocation = Math.max(1, HOURLY_VARS.length / 10);
    expect(forecastCallWeight({ locations: 150, variables: HOURLY_VARS.length, hours: 168 }))
      .toBeCloseTo(150 * perLocation, 6);
  });
});

describe("elevationCallWeight", () => {
  it("assumes one call per coordinate", () => {
    expect(elevationCallWeight(42)).toBe(42);
  });
});

describe("createUsageLog", () => {
  it("totals recorded calls inside each rolling window", () => {
    const now = clock(Date.UTC(2026, 9, 25, 12));
    const log = createUsageLog(memoryStorage(), now);
    log.record(100);
    now.advance(2 * MINUTE);
    log.record(50);

    expect(log.totals()).toEqual({ minute: 50, hour: 150, day: 150, month: 150 });

    now.advance(2 * HOUR);
    expect(log.totals()).toEqual({ minute: 0, hour: 0, day: 150, month: 150 });

    now.advance(2 * DAY);
    expect(log.totals()).toEqual({ minute: 0, hour: 0, day: 0, month: 150 });
  });

  it("persists across log instances sharing storage", () => {
    const storage = memoryStorage();
    const now = clock(Date.UTC(2026, 9, 25, 12));
    createUsageLog(storage, now).record(20);
    expect(createUsageLog(storage, now).totals().day).toBe(20);
  });

  it("prunes entries older than 30 days on write", () => {
    const storage = memoryStorage();
    const now = clock(Date.UTC(2026, 9, 1));
    const log = createUsageLog(storage, now);
    log.record(5);
    now.advance(31 * DAY);
    log.record(7);
    expect(JSON.parse(storage.data.get(STORAGE_KEY))).toHaveLength(1);
    expect(log.totals().month).toBe(7);
  });

  it.each([
    ["minute", MINUTE],
    ["hour", HOUR],
    ["day", DAY],
    ["month", 30 * DAY],
  ])("pins the %s window edge", (name, windowMs) => {
    const storage = memoryStorage();
    const now = clock(Date.UTC(2026, 9, 25, 12));
    const log = createUsageLog(storage, now);
    log.record(3);
    now.advance(windowMs - 1);
    expect(log.totals()[name]).toBe(3);
    now.advance(1);
    expect(log.totals()[name]).toBe(0);
  });

  it("prunes an entry exactly 30 days old", () => {
    const storage = memoryStorage();
    const now = clock(Date.UTC(2026, 9, 1));
    const log = createUsageLog(storage, now);
    log.record(5);
    now.advance(30 * DAY);
    log.record(7);
    expect(JSON.parse(storage.data.get(STORAGE_KEY))).toHaveLength(1);
  });

  it("ignores and prunes entries dated in the future", () => {
    const storage = memoryStorage();
    const now = clock(Date.UTC(2030, 0, 1));
    const log = createUsageLog(storage, now);
    log.record(501);
    now.advance(-4 * 365 * DAY);
    expect(log.totals()).toEqual({ minute: 0, hour: 0, day: 0, month: 0 });
    log.record(2);
    expect(JSON.parse(storage.data.get(STORAGE_KEY))).toHaveLength(1);
    expect(log.totals().minute).toBe(2);
  });

  it("ignores corrupt or malformed stored data", () => {
    const now = clock(Date.UTC(2026, 9, 25));
    expect(createUsageLog(memoryStorage({ [STORAGE_KEY]: "{not json" }), now).totals().day)
      .toBe(0);
    const mixed = JSON.stringify([[now(), 4], ["x", 1], [now(), "y"], 7]);
    expect(createUsageLog(memoryStorage({ [STORAGE_KEY]: mixed }), now).totals().day)
      .toBe(4);
  });

  it("keeps working when storage writes throw", () => {
    const storage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    const log = createUsageLog(storage, clock(0));
    expect(() => log.record(1)).not.toThrow();
  });

  it("returns zero totals when reads throw", () => {
    const storage = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {},
    };
    expect(createUsageLog(storage, clock(0)).totals().day).toBe(0);
  });
});

describe("createUsageLog default storage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function expectInMemoryLog() {
    const log = createUsageLog();
    log.record(4);
    log.record(1);
    expect(log.totals().minute).toBe(5);
  }

  it("falls back to memory when localStorage writes throw", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: () => {},
    });
    expectInMemoryLog();
  });

  it("falls back to memory when accessing localStorage throws", () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new Error("SecurityError");
      },
    });
    try {
      expectInMemoryLog();
    } finally {
      if (original) Object.defineProperty(globalThis, "localStorage", original);
      else delete globalThis.localStorage;
    }
  });

  it("falls back to memory when there is no localStorage", () => {
    vi.stubGlobal("localStorage", undefined);
    expectInMemoryLog();
  });
});

describe("formatUsageReport", () => {
  it("reports the route cost and what is left in each window", () => {
    const text = formatUsageReport({
      label: "this route",
      calls: 345,
      totals: { minute: 345, hour: 500, day: 500, month: 1200 },
    });
    expect(text).toContain("this route used ~345.0 calls");
    expect(text).toContain(`minute: ~345.0 of ${FREE_LIMITS.minute} used, ~255.0 left`);
    expect(text).toContain("day: ~500.0 of 10000 used, ~9500.0 left");
    expect(text).toContain("per IP address");
  });

  it("never reports a negative amount left", () => {
    const text = formatUsageReport({
      label: "x",
      calls: 1,
      totals: { minute: 900, hour: 0, day: 0, month: 0 },
    });
    expect(text).toContain("minute: ~900.0 of 600 used, ~0.0 left");
  });
});

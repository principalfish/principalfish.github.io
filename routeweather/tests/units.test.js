import { describe, it, expect } from "vitest";

import {
  formatDistance, formatSpeed, formatTemp, formatPrecip, formatElevation, formatWind,
  kmhToSpeed, speedToKmh, isValidSpeedKmh, MISSING,
  asUnits, isCustomised, resolveUnits, UNIT_CHOICES, UNIT_LABELS,
} from "../js/units.js";
import { SPEED_RANGE_KMH } from "../js/config.js";

// Metric preset with every overridable quantity in imperial, and the reverse.
const METRIC_OVERRIDDEN = resolveUnits("metric", { wind: "mph", rain: "in", temp: "f" });
const IMPERIAL_OVERRIDDEN = resolveUnits("imperial", { wind: "kmh", rain: "mm", temp: "c" });

describe("resolveUnits", () => {
  it("gives each preset's defaults", () => {
    expect(resolveUnits("metric"))
      .toEqual({ system: "metric", wind: "kmh", rain: "mm", temp: "c" });
    expect(resolveUnits("imperial"))
      .toEqual({ system: "imperial", wind: "mph", rain: "in", temp: "f" });
  });

  it("treats an unknown or missing system as metric", () => {
    expect(resolveUnits(undefined)).toBe(resolveUnits("metric"));
    expect(resolveUnits("nautical")).toBe(resolveUnits("metric"));
  });

  it("applies each valid override, leaving the rest at the preset's default", () => {
    expect(resolveUnits("metric", { wind: "mph" }))
      .toEqual({ system: "metric", wind: "mph", rain: "mm", temp: "c" });
    expect(resolveUnits("imperial", { temp: "c" }))
      .toEqual({ system: "imperial", wind: "mph", rain: "in", temp: "c" });
  });

  it("ignores invalid overrides and unknown keys", () => {
    const units = resolveUnits("imperial", {
      wind: "knots", rain: null, temp: "C", system: "metric", extra: "x",
    });
    expect(units).toBe(resolveUnits("imperial"));
    expect(resolveUnits("metric", null)).toBe(resolveUnits("metric"));
  });

  it("returns the same frozen object for the same combination", () => {
    const a = resolveUnits("metric", { wind: "mph" });
    expect(resolveUnits("metric", { wind: "mph", rain: "mm" })).toBe(a);
    expect(Object.isFrozen(a)).toBe(true);
    expect(resolveUnits("metric", { wind: "kmh" })).not.toBe(a);
  });

  it("has a label for every choice", () => {
    for (const [quantity, choices] of Object.entries(UNIT_CHOICES)) {
      for (const choice of choices) {
        expect(UNIT_LABELS[quantity][choice]).toEqual(expect.any(String));
      }
    }
  });
});

describe("asUnits", () => {
  it("reads a system string as its preset", () => {
    expect(asUnits("imperial")).toBe(resolveUnits("imperial"));
    expect(asUnits(undefined)).toBe(resolveUnits("metric"));
  });

  it("passes a units set through and completes a partial one", () => {
    expect(asUnits(METRIC_OVERRIDDEN)).toBe(METRIC_OVERRIDDEN);
    expect(asUnits({ system: "imperial", wind: "kmh" }))
      .toBe(resolveUnits("imperial", { wind: "kmh" }));
  });
});

describe("isCustomised", () => {
  it("is false for the presets and true with any override", () => {
    expect(isCustomised("metric")).toBe(false);
    expect(isCustomised(resolveUnits("imperial"))).toBe(false);
    expect(isCustomised(resolveUnits("metric", { temp: "f" }))).toBe(true);
    expect(isCustomised(IMPERIAL_OVERRIDDEN)).toBe(true);
  });
});

describe("formatWind", () => {
  it("follows the preset given a system string", () => {
    expect(formatWind(20, "metric")).toBe("20 km/h");
    expect(formatWind(10, "imperial")).toBe("6 mph");
  });

  it("follows the wind setting, not the preset", () => {
    expect(formatWind(10, METRIC_OVERRIDDEN)).toBe("6 mph");
    expect(formatWind(20, IMPERIAL_OVERRIDDEN)).toBe("20 km/h");
  });

  it("returns the missing placeholder for invalid input", () => {
    expect(formatWind(null, METRIC_OVERRIDDEN)).toBe(MISSING);
  });
});

describe("unit overrides", () => {
  it("convert temperature and rain by their own settings", () => {
    expect(formatTemp(0, METRIC_OVERRIDDEN)).toBe("32 °F");
    expect(formatTemp(12.4, IMPERIAL_OVERRIDDEN)).toBe("12 °C");
    expect(formatPrecip(25.4, METRIC_OVERRIDDEN)).toBe("1.00 in");
    expect(formatPrecip(2.34, IMPERIAL_OVERRIDDEN)).toBe("2.3 mm");
  });

  it("leave distance, elevation and the activity speed on the preset", () => {
    expect(formatDistance(12.34, METRIC_OVERRIDDEN)).toBe("12.3 km");
    expect(formatElevation(1000, IMPERIAL_OVERRIDDEN)).toBe("3281 ft");
    expect(formatSpeed(20, METRIC_OVERRIDDEN)).toBe("20 km/h");
    expect(formatSpeed(10, IMPERIAL_OVERRIDDEN)).toBe("6 mph");
    expect(kmhToSpeed(10, IMPERIAL_OVERRIDDEN)).toBeCloseTo(6.21371, 5);
    expect(kmhToSpeed(10, METRIC_OVERRIDDEN)).toBe(10);
    expect(speedToKmh("10", IMPERIAL_OVERRIDDEN)).toBeCloseTo(16.0934, 3);
    expect(speedToKmh("10", METRIC_OVERRIDDEN)).toBe(10);
  });
});

describe("formatDistance", () => {
  it("passes metric through with 1 decimal", () => {
    expect(formatDistance(12.34, "metric")).toBe("12.3 km");
  });

  it("converts km to miles", () => {
    expect(formatDistance(12.3, "imperial")).toBe("7.6 mi");
    expect(formatDistance(1.609344, "imperial")).toBe("1.0 mi");
  });

  it("rounds half up to 1 decimal", () => {
    expect(formatDistance(0.25, "metric")).toBe("0.3 km");
    expect(formatDistance(0, "metric")).toBe("0.0 km");
  });

  it("treats an unknown system as metric", () => {
    expect(formatDistance(5, undefined)).toBe("5.0 km");
    expect(formatDistance(5, "nautical")).toBe("5.0 km");
  });

  it("returns the missing placeholder for null/undefined/NaN/Infinity/non-numbers", () => {
    expect(formatDistance(null, "metric")).toBe(MISSING);
    expect(formatDistance(undefined, "imperial")).toBe(MISSING);
    expect(formatDistance(NaN, "metric")).toBe(MISSING);
    expect(formatDistance(Infinity, "metric")).toBe(MISSING);
    expect(formatDistance("12", "metric")).toBe(MISSING);
  });
});

describe("formatSpeed", () => {
  it("passes metric through as a whole number", () => {
    expect(formatSpeed(20, "metric")).toBe("20 km/h");
    expect(formatSpeed(14.6, "metric")).toBe("15 km/h");
  });

  it("converts km/h to mph", () => {
    expect(formatSpeed(10, "imperial")).toBe("6 mph");
    expect(formatSpeed(100, "imperial")).toBe("62 mph");
  });

  it("returns the missing placeholder for invalid input", () => {
    expect(formatSpeed(null, "imperial")).toBe(MISSING);
  });
});

describe("formatTemp", () => {
  it("passes metric through as whole degrees", () => {
    expect(formatTemp(12.4, "metric")).toBe("12 °C");
    expect(formatTemp(12.5, "metric")).toBe("13 °C");
  });

  it("converts Celsius to Fahrenheit", () => {
    expect(formatTemp(0, "imperial")).toBe("32 °F");
    expect(formatTemp(100, "imperial")).toBe("212 °F");
    expect(formatTemp(-40, "imperial")).toBe("-40 °F");
  });

  it("never renders negative zero", () => {
    expect(formatTemp(-0.3, "metric")).toBe("0 °C");
  });

  it("keeps genuine negatives", () => {
    expect(formatTemp(-3.6, "metric")).toBe("-4 °C");
  });

  it("returns the missing placeholder for invalid input", () => {
    expect(formatTemp(NaN, "metric")).toBe(MISSING);
  });
});

describe("formatPrecip", () => {
  it("passes metric through with 1 decimal", () => {
    expect(formatPrecip(2.34, "metric")).toBe("2.3 mm");
  });

  it("converts mm to inches with 2 decimals", () => {
    expect(formatPrecip(25.4, "imperial")).toBe("1.00 in");
    expect(formatPrecip(2.5, "imperial")).toBe("0.10 in");
  });

  it("returns the missing placeholder for invalid input", () => {
    expect(formatPrecip(undefined, "metric")).toBe(MISSING);
  });
});

describe("formatElevation", () => {
  it("passes metric through as whole metres", () => {
    expect(formatElevation(1344.6, "metric")).toBe("1345 m");
  });

  it("converts metres to feet", () => {
    expect(formatElevation(1000, "imperial")).toBe("3281 ft");
    expect(formatElevation(0.3048, "imperial")).toBe("1 ft");
  });

  it("returns the missing placeholder for invalid input", () => {
    expect(formatElevation(null, "metric")).toBe(MISSING);
  });
});

describe("kmhToSpeed", () => {
  it("passes metric through unrounded", () => {
    expect(kmhToSpeed(12.5, "metric")).toBe(12.5);
  });

  it("converts km/h to mph unrounded", () => {
    expect(kmhToSpeed(10, "imperial")).toBeCloseTo(6.21371, 5);
  });

  it("returns NaN for invalid input", () => {
    expect(kmhToSpeed(null, "metric")).toBeNaN();
  });
});

describe("speedToKmh", () => {
  it("passes metric through", () => {
    expect(speedToKmh(12.5, "metric")).toBe(12.5);
  });

  it("converts mph to km/h", () => {
    expect(speedToKmh(6.21371, "imperial")).toBeCloseTo(10, 5);
  });

  it("round-trips with kmhToSpeed", () => {
    expect(speedToKmh(kmhToSpeed(17.3, "imperial"), "imperial")).toBeCloseTo(17.3, 10);
  });

  it("accepts numeric strings from an input field", () => {
    expect(speedToKmh("5", "metric")).toBe(5);
    expect(speedToKmh(" 3.5 ", "imperial")).toBeCloseTo(3.5 / 0.621371, 10);
  });

  it("returns NaN for invalid input", () => {
    expect(speedToKmh(null, "metric")).toBeNaN();
    expect(speedToKmh(undefined, "imperial")).toBeNaN();
    expect(speedToKmh("", "metric")).toBeNaN();
    expect(speedToKmh("fast", "metric")).toBeNaN();
    expect(speedToKmh(NaN, "metric")).toBeNaN();
  });

  it("rejects zero, negative, hex and exponent strings", () => {
    for (const value of ["0", "-5", "0x10", "1e1", 0, -5]) {
      expect(speedToKmh(value, "metric")).toBeNaN();
    }
  });

  it("rejects speeds outside the range after conversion", () => {
    expect(speedToKmh(150, "metric")).toBeNaN();
    // 70 mph is ~112.7 km/h, above the 100 km/h ceiling.
    expect(speedToKmh(70, "imperial")).toBeNaN();
    expect(speedToKmh(60, "imperial")).toBeCloseTo(96.56, 2);
  });
});

describe("isValidSpeedKmh", () => {
  it("accepts the range bounds inclusive", () => {
    expect(isValidSpeedKmh(SPEED_RANGE_KMH.min)).toBe(true);
    expect(isValidSpeedKmh(SPEED_RANGE_KMH.max)).toBe(true);
  });

  it("rejects values outside the range and non-numbers", () => {
    expect(isValidSpeedKmh(SPEED_RANGE_KMH.min - 0.01)).toBe(false);
    expect(isValidSpeedKmh(SPEED_RANGE_KMH.max + 0.01)).toBe(false);
    expect(isValidSpeedKmh(NaN)).toBe(false);
    expect(isValidSpeedKmh("5")).toBe(false);
  });
});

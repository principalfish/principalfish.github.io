// Unit conversion and display formatting. Values are always stored in metric
// (km, km/h, °C, mm, m) and only converted here, for display.
//
// Display units are a units set { system, wind, rain, temp } (resolveUnits):
// `system` ("metric" | "imperial") is the preset and drives distance,
// elevation and the activity speed; wind, rain and temperature follow the
// preset unless overridden one by one. Every function taking `units` also
// accepts a plain system string, read as that preset; any system other than
// "imperial" is treated as metric.
//
// Rounding: distance 1 dp, speed whole numbers, temperature whole degrees,
// elevation whole metres/feet, precipitation 1 dp in mm but 2 dp in inches
// (0.1 in is ~2.5 mm, too coarse for rain amounts).

import { SPEED_RANGE_KMH } from "./config.js";

export const KM_TO_MI = 0.621371;
export const MM_TO_IN = 0.0393701;
export const M_TO_FT = 3.28084;

// Shown in place of a value when live data has a gap (null/undefined/NaN).
export const MISSING = "–";

// Plain decimal numbers only: rejects hex ("0x10"), exponents and stray text
// that Number() would otherwise accept.
const DECIMAL_PATTERN = /^\d+(\.\d+)?$/;

function isValidNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function fixed(value, decimals) {
  const factor = 10 ** decimals;
  const rounded = Math.round(value * factor) / factor;
  // `|| 0` folds -0 into 0 so small negatives don't render as "-0".
  return (rounded || 0).toFixed(decimals);
}

// --- units sets ---

export const UNIT_SYSTEMS = ["metric", "imperial"];

// The choices for each overridable quantity, metric first. The tokens are
// also the URL values (urlstate.js).
export const UNIT_CHOICES = Object.freeze({
  wind: Object.freeze(["kmh", "mph"]),
  rain: Object.freeze(["mm", "in"]),
  temp: Object.freeze(["c", "f"]),
});

// Display labels per token.
export const UNIT_LABELS = Object.freeze({
  wind: Object.freeze({ kmh: "km/h", mph: "mph" }),
  rain: Object.freeze({ mm: "mm", in: "in" }),
  temp: Object.freeze({ c: "°C", f: "°F" }),
});

const PRESET_DEFAULTS = {
  metric: { wind: "kmh", rain: "mm", temp: "c" },
  imperial: { wind: "mph", rain: "in", temp: "f" },
};

// One frozen object per combination, so equal units sets are the same object:
// views diff state.units by identity, and re-picking what is already shown
// changes nothing.
const unitsSets = new Map();

// The units set for preset `system` with any valid `overrides` ({ wind, rain,
// temp }, e.g. from the URL or a toggle) applied; an invalid or missing
// override takes the preset's default.
export function resolveUnits(system, overrides = {}) {
  const preset = system === "imperial" ? "imperial" : "metric";
  const pick = (quantity) => {
    const value = overrides?.[quantity];
    return UNIT_CHOICES[quantity].includes(value) ? value : PRESET_DEFAULTS[preset][quantity];
  };
  const wind = pick("wind");
  const rain = pick("rain");
  const temp = pick("temp");
  const key = `${preset}|${wind}|${rain}|${temp}`;
  if (!unitsSets.has(key)) {
    unitsSets.set(key, Object.freeze({ system: preset, wind, rain, temp }));
  }
  return unitsSets.get(key);
}

// A units set from either a units set (possibly partial) or a system string.
export function asUnits(units) {
  return units !== null && typeof units === "object"
    ? resolveUnits(units.system, units)
    : resolveUnits(units);
}

// True when any quantity differs from its preset's default.
export function isCustomised(units) {
  const resolved = asUnits(units);
  return resolved !== resolveUnits(resolved.system);
}

const isImperial = (units) => asUnits(units).system === "imperial";

// --- formatting ---

function format(value, imperial, {
  factor = 1,
  toImperial,
  decimals,
  imperialDecimals = decimals,
  metricUnit,
  imperialUnit,
}) {
  if (!isValidNumber(value)) return MISSING;
  if (imperial) {
    const converted = toImperial ? toImperial(value) : value * factor;
    return `${fixed(converted, imperialDecimals)} ${imperialUnit}`;
  }
  return `${fixed(value, decimals)} ${metricUnit}`;
}

export function formatDistance(km, units) {
  return format(km, isImperial(units), {
    factor: KM_TO_MI,
    decimals: 1,
    metricUnit: "km",
    imperialUnit: "mi",
  });
}

// The activity speed: follows the preset.
export function formatSpeed(kmh, units) {
  return format(kmh, isImperial(units), {
    factor: KM_TO_MI,
    decimals: 0,
    metricUnit: "km/h",
    imperialUnit: "mph",
  });
}

// Wind (speed, gust, head/tail, cross): follows the wind setting.
export function formatWind(kmh, units) {
  return format(kmh, asUnits(units).wind === "mph", {
    factor: KM_TO_MI,
    decimals: 0,
    metricUnit: "km/h",
    imperialUnit: "mph",
  });
}

export function formatTemp(celsius, units) {
  return format(celsius, asUnits(units).temp === "f", {
    toImperial: (c) => c * 9 / 5 + 32,
    decimals: 0,
    metricUnit: "°C",
    imperialUnit: "°F",
  });
}

export function formatPrecip(mm, units) {
  return format(mm, asUnits(units).rain === "in", {
    factor: MM_TO_IN,
    decimals: 1,
    imperialDecimals: 2,
    metricUnit: "mm",
    imperialUnit: "in",
  });
}

export function formatElevation(m, units) {
  return format(m, isImperial(units), {
    factor: M_TO_FT,
    decimals: 0,
    metricUnit: "m",
    imperialUnit: "ft",
  });
}

// The one speed rule, shared with the URL state: finite and inside
// SPEED_RANGE_KMH.
export function isValidSpeedKmh(kmh) {
  return isValidNumber(kmh) && kmh >= SPEED_RANGE_KMH.min && kmh <= SPEED_RANGE_KMH.max;
}

// Internal km/h -> a plain number in the preset's speed unit, unrounded (for
// pre-filling a speed input). NaN for invalid input.
export function kmhToSpeed(kmh, units) {
  if (!isValidNumber(kmh)) return NaN;
  return isImperial(units) ? kmh * KM_TO_MI : kmh;
}

// A user-typed speed in the preset's speed unit -> internal km/h, unrounded.
// Accepts numbers or plain decimal strings; NaN for anything else or for a
// speed outside SPEED_RANGE_KMH.
export function speedToKmh(value, units) {
  let n = value;
  if (typeof value === "string") {
    const trimmed = value.trim();
    n = DECIMAL_PATTERN.test(trimmed) ? Number(trimmed) : NaN;
  }
  if (!isValidNumber(n)) return NaN;
  const kmh = isImperial(units) ? n / KM_TO_MI : n;
  return isValidSpeedKmh(kmh) ? kmh : NaN;
}

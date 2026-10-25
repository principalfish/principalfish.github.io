// Unit conversion and display formatting. Values are always stored in metric
// (km, km/h, °C, mm, m) and only converted here, for display. Any `system`
// other than "imperial" is treated as metric.
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

function format(value, system, {
  factor = 1,
  toImperial,
  decimals,
  imperialDecimals = decimals,
  metricUnit,
  imperialUnit,
}) {
  if (!isValidNumber(value)) return MISSING;
  if (system === "imperial") {
    const converted = toImperial ? toImperial(value) : value * factor;
    return `${fixed(converted, imperialDecimals)} ${imperialUnit}`;
  }
  return `${fixed(value, decimals)} ${metricUnit}`;
}

export function formatDistance(km, system) {
  return format(km, system, {
    factor: KM_TO_MI,
    decimals: 1,
    metricUnit: "km",
    imperialUnit: "mi",
  });
}

export function formatSpeed(kmh, system) {
  return format(kmh, system, {
    factor: KM_TO_MI,
    decimals: 0,
    metricUnit: "km/h",
    imperialUnit: "mph",
  });
}

export function formatTemp(celsius, system) {
  return format(celsius, system, {
    toImperial: (c) => c * 9 / 5 + 32,
    decimals: 0,
    metricUnit: "°C",
    imperialUnit: "°F",
  });
}

export function formatPrecip(mm, system) {
  return format(mm, system, {
    factor: MM_TO_IN,
    decimals: 1,
    imperialDecimals: 2,
    metricUnit: "mm",
    imperialUnit: "in",
  });
}

export function formatElevation(m, system) {
  return format(m, system, {
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

// Internal km/h -> a plain number in the active system's speed unit, unrounded
// (for pre-filling a speed input). NaN for invalid input.
export function kmhToSpeed(kmh, system) {
  if (!isValidNumber(kmh)) return NaN;
  return system === "imperial" ? kmh * KM_TO_MI : kmh;
}

// A user-typed speed in the active system's unit -> internal km/h, unrounded.
// Accepts numbers or plain decimal strings; NaN for anything else or for a
// speed outside SPEED_RANGE_KMH.
export function speedToKmh(value, system) {
  let n = value;
  if (typeof value === "string") {
    const trimmed = value.trim();
    n = DECIMAL_PATTERN.test(trimmed) ? Number(trimmed) : NaN;
  }
  if (!isValidNumber(n)) return NaN;
  const kmh = system === "imperial" ? n / KM_TO_MI : n;
  return isValidSpeedKmh(kmh) ? kmh : NaN;
}

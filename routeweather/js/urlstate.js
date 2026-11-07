// Shareable URL query state: activity, speed (km/h, internal units), start
// (ISO 8601 date-time with a Z or ±hh:mm offset), units ("metric" |
// "imperial") and the per-quantity unit overrides wind ("kmh" | "mph"), rain
// ("mm" | "in") and temp ("c" | "f"). The overrides are only written when
// they differ from the units preset's default, so a plain metric or imperial
// link looks as it always has.

import { ACTIVITIES, DEFAULT_ACTIVITY } from "./config.js";
import { resolveUnits, speedToKmh, UNIT_CHOICES, UNIT_SYSTEMS } from "./units.js";

export { UNIT_SYSTEMS };

const UNIT_QUANTITIES = Object.keys(UNIT_CHOICES);

export const DEFAULT_STATE = Object.freeze({
  activity: DEFAULT_ACTIVITY,
  speed: ACTIVITIES[DEFAULT_ACTIVITY].defaultSpeedKmh,
  start: null, // "not set": the caller decides (typically now)
  units: "metric",
  // The metric preset's defaults (resolveUnits("metric")).
  wind: "kmh",
  rain: "mm",
  temp: "c",
});

// Strict shape check before Date.parse, whose fallback parser accepts loose
// text ("1", "Oct 26", HTML around a date) and differs between engines. An
// explicit zone is required so every browser reads the same instant.
const ISO_START_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):\d{2}(:\d{2}(\.\d{1,3})?)?(Z|[+-]\d{2}:\d{2})$/;

function parseActivity(value) {
  return value !== null && Object.hasOwn(ACTIVITIES, value)
    ? value
    : DEFAULT_STATE.activity;
}

// A missing or invalid speed falls back to the activity's preset; `custom`
// has none, so it falls back to the default activity's.
function parseSpeed(value, activity) {
  const kmh = value === null ? NaN : speedToKmh(value, "metric");
  if (!Number.isNaN(kmh)) return kmh;
  return ACTIVITIES[activity].defaultSpeedKmh ?? DEFAULT_STATE.speed;
}

// The pattern only checks shape; V8 rolls impossible days forward (Feb 30 ->
// Mar 2) where other engines return NaN, so check the calendar fields too.
function parseStart(value) {
  const match = value === null ? null : ISO_START_PATTERN.exec(value);
  if (!match) return DEFAULT_STATE.start;
  const [, year, month, day, hour] = match.map(Number);
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const validFields = month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth && hour <= 23;
  return validFields && !Number.isNaN(Date.parse(value)) ? value : DEFAULT_STATE.start;
}

function parseUnits(value) {
  return UNIT_SYSTEMS.includes(value) ? value : DEFAULT_STATE.units;
}

// `search` is a query string (with or without the leading "?", e.g.
// `location.search`) or a URLSearchParams. Missing or invalid fields fall back
// to defaults; a missing or invalid wind, rain or temp to the units preset's
// default. A valid `start` is returned as given (offset preserved).
export function readState(search = "") {
  const params = search instanceof URLSearchParams ? search : new URLSearchParams(search);
  const activity = parseActivity(params.get("activity"));
  const units = parseUnits(params.get("units"));
  const { wind, rain, temp } = resolveUnits(
    units,
    Object.fromEntries(UNIT_QUANTITIES.map((key) => [key, params.get(key)])),
  );
  return {
    activity,
    speed: parseSpeed(params.get("speed"), activity),
    start: parseStart(params.get("start")),
    units,
    wind,
    rain,
    temp,
  };
}

// Returns a query string starting with "?" (just "?" when nothing is set, which
// still clears the query when passed to history.replaceState). Keys whose value
// is null, undefined or "" are omitted, and wind, rain and temp also when
// invalid or equal to the default of `units` (metric when unset). Speed is
// rounded to 2 dp so an mph entry doesn't leak a long float into the shared
// link.
export function writeState({
  activity, speed, start, units, wind, rain, temp,
} = {}) {
  const params = new URLSearchParams();
  // A non-finite speed is treated as unset rather than written as "NaN".
  const roundedSpeed = Number.isFinite(speed) ? Math.round(speed * 100) / 100 : undefined;
  const preset = resolveUnits(units);
  const override = (key, value) => (
    UNIT_CHOICES[key].includes(value) && value !== preset[key] ? value : undefined
  );
  const fields = [
    ["activity", activity],
    ["speed", roundedSpeed],
    ["start", start],
    ["units", units],
    ["wind", override("wind", wind)],
    ["rain", override("rain", rain)],
    ["temp", override("temp", temp)],
  ];
  for (const [key, value] of fields) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  return `?${params.toString()}`;
}

// Weather at a route sample: pure maths over one normalised Open-Meteo location
// (see openmeteo.js normaliseLocation). Interpolates the hourly series to the
// arrival time, builds a vertical profile from the surface and pressure-level
// values, and reads temperature and wind off it at the walker's real altitude.
//
// Conventions: heights and elevations in metres above sea level, temperatures
// °C, wind km/h, directions in degrees clockwise from north that the wind blows
// FROM (meteorological), times in epoch ms (UTC).
//
// Why a profile: the model's 2 m / 10 m values refer to the grid cell's average
// terrain height (`cellElev`), which on a fell top can be hundreds of metres
// below the summit. Pressure-level geopotential heights give temperature and
// wind at known altitudes above that, so the route's own elevation picks the
// right point between them.

import { PRESSURE_LEVELS } from "./config.js";
import { MISSING } from "./units.js";

// Heights above the model terrain of the surface variables.
export const SCREEN_HEIGHT_M = 2;
export const WIND_HEIGHT_M = 10;

// A pressure level must sit at least this far above the cell's terrain to be
// used: at or below it the level is underground (1000 hPa often is under high
// pressure; its geopotential height can even be negative) and its values are
// extrapolated by the model, not real air.
export const MIN_LEVEL_CLEARANCE_M = 10;

// Standard-atmosphere lapse rate (K per metre) for extrapolating temperature
// below the lowest profile point (a route dipping beneath the cell's terrain).
export const LAPSE_RATE_K_PER_M = 0.0065;

const toRad = (deg) => (deg * Math.PI) / 180;
const toDeg = (rad) => (rad * 180) / Math.PI;

// --- wind vectors ---

// Speed + FROM direction -> (u, v) components of where the air is going (u
// east, v north). Directions are interpolated through components rather than
// as angles, so 350° and 10° average to north, not south.
export function toUV(speed, fromDeg) {
  const rad = toRad(fromDeg);
  return { u: -speed * Math.sin(rad), v: -speed * Math.cos(rad) };
}

// (u, v) -> { speed, fromDeg } with fromDeg in [0, 360). Calm air (speed 0) has
// no direction; it reports 0.
export function fromUV(u, v) {
  const speed = Math.hypot(u, v);
  if (speed === 0) return { speed, fromDeg: 0 };
  const deg = (toDeg(Math.atan2(-u, -v)) + 360) % 360;
  // A tiny negative angle + 360 can round up to exactly 360.
  return { speed, fromDeg: deg >= 360 ? 0 : deg };
}

// Wind `frac` of the way from `a` to `b` (each { speed, fromDeg }). Speed is
// linear as a scalar; only the direction comes from the interpolated u/v.
// Averaging the vectors alone would shrink the speed wherever the direction
// turns (two 20 km/h winds 90° apart average to 14 km/h). If the two vectors
// cancel exactly (opposite directions, equal speeds, halfway) the direction
// falls back to fromUV's calm-air 0°.
function blendWind(a, b, frac) {
  if (frac === 0) return { speed: a.speed, fromDeg: a.fromDeg };
  const ua = toUV(a.speed, a.fromDeg);
  const ub = toUV(b.speed, b.fromDeg);
  const { fromDeg } = fromUV(ua.u + (ub.u - ua.u) * frac, ua.v + (ub.v - ua.v) * frac);
  return { speed: a.speed + (b.speed - a.speed) * frac, fromDeg };
}

// --- time interpolation ---

// Where `tMs` sits in the hourly series: steps i0/i1 and the fraction between
// them. Times outside the series clamp to its ends; null for no data.
function timeBracket(timesMs, tMs) {
  const n = timesMs.length;
  if (n === 0 || Number.isNaN(tMs)) return null;
  if (tMs <= timesMs[0]) return { i0: 0, i1: 0, frac: 0 };
  if (tMs >= timesMs[n - 1]) return { i0: n - 1, i1: n - 1, frac: 0 };
  // Largest i with timesMs[i] <= tMs.
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (timesMs[mid] <= tMs) lo = mid;
    else hi = mid;
  }
  return { i0: lo, i1: lo + 1, frac: (tMs - timesMs[lo]) / (timesMs[lo + 1] - timesMs[lo]) };
}

// Exactly on a step reads that step alone, so a gap (NaN) in the neighbouring
// hour doesn't poison it.
function lerpAt(values, br) {
  if (!br) return NaN;
  const v0 = values[br.i0];
  return br.frac === 0 ? v0 : v0 + (values[br.i1] - v0) * br.frac;
}

function windAtTime(speeds, dirs, br) {
  if (!br) return { speed: NaN, fromDeg: NaN };
  const a = { speed: speeds[br.i0], fromDeg: dirs[br.i0] };
  const b = { speed: speeds[br.i1], fromDeg: dirs[br.i1] };
  return blendWind(a, b, br.frac);
}

// Linear interpolation of an hourly series at `tMs`, clamped to its first/last
// value outside the series. NaN for an empty series.
export function interpHourly(timesMs, values, tMs) {
  return lerpAt(values, timeBracket(timesMs, tMs));
}

// Index of the step ending the hour bucket that contains `tMs` (see
// bucketHourly), or -1 for no data.
function bucketIndex(timesMs, tMs) {
  const br = timeBracket(timesMs, tMs);
  if (!br) return -1;
  return br.frac === 0 ? br.i0 : br.i1;
}

// The hour bucket containing `tMs`, for per-hour quantities: precipitation is
// the sum over the PRECEDING hour and wind_gusts_10m the maximum over it, both
// stamped at the hour's end. That is the value at the next whole hour at or
// after `tMs`: arriving at 10:20 reads the 11:00 value (the 10–11 bucket),
// arriving at exactly 10:00 reads the 10:00 value (the end of the 09–10 bucket
// you have just walked through). Clamped to the series' ends; NaN for an empty
// series.
export function bucketHourly(timesMs, values, tMs) {
  const i = bucketIndex(timesMs, tMs);
  return i < 0 ? NaN : values[i];
}

// Gust excess over the mean 10 m wind for the hour bucket containing `tMs`:
// the bucket's maximum gust minus the mean of the 10 m speeds at the bucket's
// two ends (just the one when the bucket starts before the series), floored at
// 0. NaN for no data.
function gustExcessAt(loc, tMs) {
  const i = bucketIndex(loc.timesMs, tMs);
  if (i < 0) return NaN;
  const speeds = loc.vars.wind_speed_10m;
  const meanWind10 = i === 0 ? speeds[0] : (speeds[i - 1] + speeds[i]) / 2;
  return Math.max(0, loc.vars.wind_gusts_10m[i] - meanWind10);
}

// --- vertical profile ---

// The location's vertical profile at `tMs`:
//
//   cellElev          model terrain height (m)
//   temps             [{ heightM, tempC }] ascending: the 2 m value, then each
//   winds             [{ heightM, speed, fromDeg }] usable pressure level (see
//                                          MIN_LEVEL_CLEARANCE_M)
//   model2mTempC      the model's own surface values at `tMs`, kept for
//   model10mWindKmh   on-page transparency
//   model10mFromDeg
//   gust10Kmh         max 10 m gust over the hour bucket containing `tMs`
//   gustExcessKmh     that gust minus the hour's mean 10 m wind (>= 0)
//
// Every series (temperature, geopotential height, wind) is interpolated in time
// first, so a level's inclusion is judged at `tMs`. Points with a gap (NaN) are
// left out rather than interpolated across.
export function profileAt(loc, tMs) {
  const { vars, cellElev } = loc;
  const br = timeBracket(loc.timesMs, tMs);
  const at = (name) => lerpAt(vars[name], br);

  const model2mTempC = at("temperature_2m");
  const wind10 = windAtTime(vars.wind_speed_10m, vars.wind_direction_10m, br);
  const temps = [{ heightM: cellElev + SCREEN_HEIGHT_M, tempC: model2mTempC }];
  const winds = [{ heightM: cellElev + WIND_HEIGHT_M, ...wind10 }];

  for (const level of PRESSURE_LEVELS) {
    const heightM = at(`geopotential_height_${level}hPa`);
    // Also false for NaN height or terrain.
    if (!(heightM > cellElev + MIN_LEVEL_CLEARANCE_M)) continue;
    temps.push({ heightM, tempC: at(`temperature_${level}hPa`) });
    winds.push({
      heightM,
      ...windAtTime(vars[`wind_speed_${level}hPa`], vars[`wind_direction_${level}hPa`], br),
    });
  }

  const byHeight = (a, b) => a.heightM - b.heightM;
  const finite = (p, keys) => keys.every((key) => Number.isFinite(p[key]));
  return {
    cellElev,
    temps: temps.filter((p) => finite(p, ["heightM", "tempC"])).sort(byHeight),
    winds: winds.filter((p) => finite(p, ["heightM", "speed", "fromDeg"])).sort(byHeight),
    model2mTempC,
    model10mWindKmh: wind10.speed,
    model10mFromDeg: wind10.fromDeg,
    gust10Kmh: bucketHourly(loc.timesMs, vars.wind_gusts_10m, tMs),
    gustExcessKmh: gustExcessAt(loc, tMs),
  };
}

// Where height `h` falls among ascending `points`: indices and fraction, with
// `below` set when it is under the lowest point. Above the top clamps to it.
function heightBracket(points, h) {
  const last = points.length - 1;
  if (h <= points[0].heightM) return { below: true, i0: 0, i1: 0, frac: 0 };
  if (h >= points[last].heightM) return { below: false, i0: last, i1: last, frac: 0 };
  let i = 0;
  while (points[i + 1].heightM < h) i++;
  const frac = (h - points[i].heightM) / (points[i + 1].heightM - points[i].heightM);
  return { below: false, i0: i, i1: i + 1, frac };
}

// Air temperature 2 m above ground at elevation `eleM`, linear in height
// between profile points (so an inversion — warmer air above colder — is kept).
// Below the lowest point it warms downward at LAPSE_RATE_K_PER_M; above the top
// level it holds the top level's value. NaN without a profile or elevation.
export function temperatureAt(profile, eleM) {
  const { temps } = profile;
  if (temps.length === 0 || !Number.isFinite(eleM)) return NaN;
  const h = eleM + SCREEN_HEIGHT_M;
  const br = heightBracket(temps, h);
  const t0 = temps[br.i0].tempC;
  if (br.below) return t0 + (temps[0].heightM - h) * LAPSE_RATE_K_PER_M;
  return t0 + (temps[br.i1].tempC - t0) * br.frac;
}

// Wind 10 m above ground at elevation `eleM` -> { speed, fromDeg }, linear in
// height (speed as a scalar, direction via u/v; see blendWind). Below the
// lowest point it is the 10 m wind (the model has nothing below its own
// terrain to say otherwise); above the top level it holds the top level's
// wind. NaNs without a profile or elevation.
export function windAt(profile, eleM) {
  const { winds } = profile;
  if (winds.length === 0 || !Number.isFinite(eleM)) return { speed: NaN, fromDeg: NaN };
  const br = heightBracket(winds, eleM + WIND_HEIGHT_M);
  return blendWind(winds[br.i0], winds[br.i1], br.frac);
}

// --- relative wind ---

// Splits a wind into components relative to the direction of travel:
// headKmh positive = into your face (negative = tailwind), crossKmh positive =
// from your right (negative = from the left). Both null when there is no
// bearing (e.g. a route with no distinct positions).
export function headCross(windFromDeg, speed, bearingDeg) {
  if (!Number.isFinite(bearingDeg)) return { headKmh: null, crossKmh: null };
  const rel = toRad(windFromDeg - bearingDeg);
  return { headKmh: speed * Math.cos(rel), crossKmh: speed * Math.sin(rel) };
}

// True when height `eleM` (the ground under the walker) puts the 2 m
// temperature or the 10 m wind above the highest pressure level of its
// profile. False without elevation or without any usable pressure level (a
// surface-only profile isn't "held at ~3,000 m").
export function isAboveProfile(profile, eleM) {
  if (!Number.isFinite(eleM)) return false;
  const top = (points) => (points.length > 1 ? points[points.length - 1].heightM : Infinity);
  return eleM + SCREEN_HEIGHT_M > top(profile.temps) || eleM + WIND_HEIGHT_M > top(profile.winds);
}

// --- per sample ---

// Everything the page shows for one sample (`{ ele }`; ele may be null,
// undefined or NaN) reached at `tMs` heading `bearingDeg`. A sample without a
// finite elevation uses the cell's terrain height, i.e. the plain model surface
// values. The model* fields and cellElev/eleUsed are there so the page can show
// how the altitude correction moved the numbers.
//
// Gusts: the 10 m gust's excess over the 10 m mean wind is added to the wind at
// altitude. Turbulence is generated at the surface, so a constant excess — not
// a gust/wind RATIO, which would scale with the much stronger free-air wind on
// a summit — keeps ridge gusts plausible.
//
// precipProb and code come from the same hour bucket as precipMm. Open-Meteo's
// weather_code is an instantaneous value, but reading it from the bucket keeps
// the label consistent with the rain amount shown beside it.
//
// outsideForecast is true when `tMs` is before the first or after the last
// hourly time (values are then clamped to the nearest end), so the page can
// flag it.
//
// aboveProfile is true when the walker's 2 m or 10 m height is above the top
// of its profile (the 700 hPa level, ~3,000 m): temperature or wind is then
// held at that level's value rather than read off the profile.
export function weatherForSample(loc, sample, tMs, bearingDeg) {
  const eleUsed = Number.isFinite(sample.ele) ? sample.ele : loc.cellElev;
  const profile = profileAt(loc, tMs);
  const wind = windAt(profile, eleUsed);
  const { headKmh, crossKmh } = headCross(wind.fromDeg, wind.speed, bearingDeg);
  const bucket = (name) => bucketHourly(loc.timesMs, loc.vars[name], tMs);
  const { timesMs } = loc;
  return {
    tempC: temperatureAt(profile, eleUsed),
    windKmh: wind.speed,
    windFromDeg: wind.fromDeg,
    gustKmh: wind.speed + profile.gustExcessKmh,
    headKmh,
    crossKmh,
    precipMm: bucket("precipitation"),
    precipProb: bucket("precipitation_probability"),
    code: bucket("weather_code"),
    model10mWindKmh: profile.model10mWindKmh,
    model10mGustKmh: profile.gust10Kmh,
    model2mTempC: profile.model2mTempC,
    cellElev: loc.cellElev,
    eleUsed,
    aboveProfile: isAboveProfile(profile, eleUsed),
    // Also true for a NaN time or an empty series.
    outsideForecast: !(tMs >= timesMs[0] && tMs <= timesMs[timesMs.length - 1]),
  };
}

// --- weather codes ---

// WMO weather interpretation codes as Open-Meteo uses them, as short labels.
export const WMO_DESCRIPTIONS = Object.freeze({
  0: "Clear sky",
  1: "Mainly clear",
  2: "Partly cloudy",
  3: "Overcast",
  45: "Fog",
  48: "Freezing fog",
  51: "Light drizzle",
  53: "Drizzle",
  55: "Heavy drizzle",
  56: "Light freezing drizzle",
  57: "Freezing drizzle",
  61: "Light rain",
  63: "Rain",
  65: "Heavy rain",
  66: "Light freezing rain",
  67: "Freezing rain",
  71: "Light snow",
  73: "Snow",
  75: "Heavy snow",
  77: "Snow grains",
  80: "Light showers",
  81: "Showers",
  82: "Violent showers",
  85: "Light snow showers",
  86: "Heavy snow showers",
  95: "Thunderstorm",
  96: "Thunderstorm with hail",
  99: "Thunderstorm with heavy hail",
});

// Label for a weather code: MISSING for a gap, "Unknown" for a code outside
// the table.
export function describeWeather(code) {
  if (!Number.isFinite(code)) return MISSING;
  return Object.hasOwn(WMO_DESCRIPTIONS, code) ? WMO_DESCRIPTIONS[code] : "Unknown";
}

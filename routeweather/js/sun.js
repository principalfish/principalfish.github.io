// The sun's position, sunrise and sunset, worked out locally (no API call)
// with NOAA's solar position algorithm (the one behind its solar calculator
// spreadsheet; good to about a minute for sunrise and sunset at mid
// latitudes). Pure functions over epoch ms and degrees (latitude north
// positive, longitude east positive).
//
// Sunrise and sunset are when the sun's centre is SUNRISE_ELEVATION_DEG below
// a flat horizon (refraction plus the sun's radius), so hills, valleys and the
// walker's own altitude are not accounted for. "Daylight" everywhere on the
// page means the sun is above that same line.

export const SUNRISE_ELEVATION_DEG = -0.833;

const MINUTE_MS = 60 * 1000;
const DAY_MS = 24 * 60 * MINUTE_MS;

// Search step for crossings: the sun can't rise and set again within it
// anywhere it matters (only within days of the polar day's edge).
const SCAN_STEP_MS = 10 * MINUTE_MS;
// Crossings are refined to this precision.
const EVENT_PRECISION_MS = 10 * 1000;

const toRad = (deg) => (deg * Math.PI) / 180;
const toDeg = (rad) => (rad * 180) / Math.PI;
const mod360 = (deg) => ((deg % 360) + 360) % 360;

// Solar declination (rad) and the equation of time (minutes) at `ms`.
function solarGeometry(ms) {
  const julianDay = ms / DAY_MS + 2440587.5;
  const t = (julianDay - 2451545) / 36525; // Julian centuries since J2000.0
  const meanLong = mod360(280.46646 + t * (36000.76983 + t * 0.0003032));
  const meanAnomaly = 357.52911 + t * (35999.05029 - 0.0001537 * t);
  const eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
  const m = toRad(meanAnomaly);
  const centre = Math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
    + Math.sin(2 * m) * (0.019993 - 0.000101 * t)
    + Math.sin(3 * m) * 0.000289;
  const omega = toRad(125.04 - 1934.136 * t);
  const apparentLong = toRad(meanLong + centre - 0.00569 - 0.00478 * Math.sin(omega));
  const meanObliquity = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60)
    / 60;
  const obliquity = toRad(meanObliquity + 0.00256 * Math.cos(omega));
  const declination = Math.asin(Math.sin(obliquity) * Math.sin(apparentLong));

  const y = Math.tan(obliquity / 2) ** 2;
  const l0 = toRad(meanLong);
  const equationOfTime = 4 * toDeg(
    y * Math.sin(2 * l0)
    - 2 * eccentricity * Math.sin(m)
    + 4 * eccentricity * y * Math.sin(m) * Math.cos(2 * l0)
    - 0.5 * y * y * Math.sin(4 * l0)
    - 1.25 * eccentricity * eccentricity * Math.sin(2 * m),
  );
  return { declination, equationOfTime };
}

// The sun's elevation above a flat horizon (degrees, no refraction) at `ms`
// seen from (lat, lon). NaN for non-finite input.
export function solarElevationDeg(ms, lat, lon) {
  if (![ms, lat, lon].every(Number.isFinite)) return NaN;
  const { declination, equationOfTime } = solarGeometry(ms);
  const utcMinutes = (((ms % DAY_MS) + DAY_MS) % DAY_MS) / MINUTE_MS;
  const trueSolarMinutes = utcMinutes + equationOfTime + 4 * lon;
  const hourAngle = toRad(trueSolarMinutes / 4 - 180);
  const phi = toRad(lat);
  const cosZenith = Math.sin(phi) * Math.sin(declination)
    + Math.cos(phi) * Math.cos(declination) * Math.cos(hourAngle);
  return 90 - toDeg(Math.acos(Math.min(1, Math.max(-1, cosZenith))));
}

// True when the sun is up (above the sunrise line) at `ms` at (lat, lon).
export function isDaylight(ms, lat, lon) {
  return solarElevationDeg(ms, lat, lon) > SUNRISE_ELEVATION_DEG;
}

// Bisects a change of `up(t)` between `lo` (state `upAtLo`) and `hi`.
function refine(up, lo, hi, upAtLo) {
  let a = lo;
  let b = hi;
  while (b - a > EVENT_PRECISION_MS) {
    const mid = (a + b) / 2;
    if (up(mid) === upAtLo) a = mid;
    else b = mid;
  }
  return Math.round((a + b) / 2);
}

// Every sunrise and sunset at (lat, lon) in [fromMs, toMs], in order, as
// [{ kind: "sunrise" | "sunset", tMs }]. [] for non-finite input.
export function sunEvents(fromMs, toMs, lat, lon) {
  if (![fromMs, toMs, lat, lon].every(Number.isFinite) || toMs <= fromMs) return [];
  const up = (t) => isDaylight(t, lat, lon);
  const events = [];
  let prevT = fromMs;
  let prevUp = up(fromMs);
  while (prevT < toMs) {
    const t = Math.min(prevT + SCAN_STEP_MS, toMs);
    const nowUp = up(t);
    if (nowUp !== prevUp) {
      events.push({ kind: nowUp ? "sunrise" : "sunset", tMs: refine(up, prevT, t, prevUp) });
    }
    prevT = t;
    prevUp = nowUp;
  }
  return events;
}

// Minutes of daylight at (lat, lon) in [fromMs, toMs]. NaN for non-finite
// input.
export function daylightMinutes(fromMs, toMs, lat, lon) {
  if (![fromMs, toMs, lat, lon].every(Number.isFinite)) return NaN;
  if (toMs <= fromMs) return 0;
  let total = 0;
  let upSince = isDaylight(fromMs, lat, lon) ? fromMs : null;
  for (const { kind, tMs } of sunEvents(fromMs, toMs, lat, lon)) {
    if (kind === "sunrise") upSince = tMs;
    else if (upSince !== null) {
      total += tMs - upSince;
      upSince = null;
    }
  }
  if (upSince !== null) total += toMs - upSince;
  return total / MINUTE_MS;
}

// The next sunrise or sunset at (lat, lon) after `ms`, looking up to
// `horizonMs` ahead: { kind, tMs }, or null (polar day or night, or
// non-finite input).
export function nextSunEvent(ms, lat, lon, horizonMs = 2 * DAY_MS) {
  return sunEvents(ms, ms + horizonMs, lat, lon)[0] ?? null;
}

// Sunrise and sunset on the day running from `dayStartMs` to `dayEndMs`
// (the route zone's midnights): { sunriseMs, sunsetMs, allDay }, where
// sunriseMs/sunsetMs are the day's first sunrise and last sunset (null when
// there is none) and allDay is "up" or "down" when the sun neither rises nor
// sets that day (else null).
export function sunTimes(dayStartMs, dayEndMs, lat, lon) {
  const events = sunEvents(dayStartMs, dayEndMs, lat, lon);
  const sunrise = events.find((e) => e.kind === "sunrise");
  const sunset = events.filter((e) => e.kind === "sunset").pop();
  let allDay = null;
  if (events.length === 0 && Number.isFinite(solarElevationDeg(dayStartMs, lat, lon))) {
    allDay = isDaylight(dayStartMs, lat, lon) ? "up" : "down";
  }
  return { sunriseMs: sunrise?.tMs ?? null, sunsetMs: sunset?.tMs ?? null, allDay };
}


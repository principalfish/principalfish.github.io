// Daylight along the route: where (and when) you pass sunrise and sunset,
// the stretches walked in the dark, the start day's sunrise and sunset, and
// the sunshine you would get. Pure functions over the page state (main.js),
// using sun.js for the sun and the constant-speed timing of along-route.js.
//
// The sun is judged at the walker's own position and arrival time, so a
// route heading west into the evening sees its sunset later than the start
// point does. Flat horizon throughout (see sun.js).

import { positionAtDistance } from "./route.js";
import { nearestSpanTimes, timeAtDistance } from "./along-route.js";
import { isDaylight, nextSunEvent, sunTimes } from "./sun.js";
import { zonedParts, zonedTimeToMs } from "./zone.js";
import { isFiniteNumber as isNum } from "./display.js";

// The route is scanned in at least this many steps, and finer when it takes
// long enough that a step would pass 10 minutes; each change is then refined
// by bisection.
const MIN_SCAN_STEPS = 600;
const MAX_STEP_MS = 10 * 60 * 1000;
const BISECT_ITERATIONS = 24;

// --- along the route ---

// Daylight along the route for the current plan:
//
//   startsInDark   true when the start is before sunrise / after sunset
//   events         [{ kind: "sunrise" | "sunset", km, tMs }] in route order
//   nightSpans     [[fromKm, toKm]] walked in the dark
//   daylightMs     time on the move in daylight
//
// null without a route or a valid start and speed.
export function routeDaylight(state) {
  const { route } = state;
  if (!route || !isNum(timeAtDistance(state, 0))) return null;
  const totalKm = route.totalDistKm;
  const up = (km) => {
    const { lat, lon } = positionAtDistance(route, km);
    return isDaylight(timeAtDistance(state, km), lat, lon);
  };

  const durationMs = timeAtDistance(state, totalKm) - timeAtDistance(state, 0);
  const steps = Math.max(MIN_SCAN_STEPS, Math.ceil(durationMs / MAX_STEP_MS));
  const startsInDark = !up(0);
  const events = [];
  let prevKm = 0;
  let prevUp = !startsInDark;
  for (let k = 1; k <= steps && totalKm > 0; k++) {
    const km = (totalKm * k) / steps;
    const nowUp = up(km);
    if (nowUp !== prevUp) {
      let lo = prevKm;
      let hi = km;
      for (let i = 0; i < BISECT_ITERATIONS; i++) {
        const mid = (lo + hi) / 2;
        if (up(mid) === prevUp) lo = mid;
        else hi = mid;
      }
      const at = (lo + hi) / 2;
      events.push({ kind: nowUp ? "sunrise" : "sunset", km: at, tMs: timeAtDistance(state, at) });
    }
    prevKm = km;
    prevUp = nowUp;
  }

  const nightSpans = [];
  let darkFrom = startsInDark ? 0 : null;
  for (const { kind, km } of events) {
    if (kind === "sunset") darkFrom = km;
    else if (darkFrom !== null) {
      nightSpans.push([darkFrom, km]);
      darkFrom = null;
    }
  }
  if (darkFrom !== null) nightSpans.push([darkFrom, totalKm]);

  const nightKm = nightSpans.reduce((sum, [a, b]) => sum + (b - a), 0);
  const daylightMs = totalKm > 0 ? durationMs * (1 - nightKm / totalKm) : 0;
  return { startsInDark, events, nightSpans, daylightMs };
}

// Sunshine you would get on the move: each sample's minutes of sunshine per
// hour (weather.js sunMin) over the time spent nearer to it than to its
// neighbours (along-route.js nearestSpanTimes). Returns
// { sunMs, estimated } (estimated: some of it comes from an estimate), or
// null without results. Samples with no sunshine value add nothing.
export function routeSunshine(state) {
  const spans = nearestSpanTimes(state);
  if (!spans) return null;
  let sunMs = 0;
  let estimated = false;
  spans.forEach((spanMs, i) => {
    const r = state.results[i];
    if (!isNum(r.sunMin)) return;
    sunMs += spanMs * (r.sunMin / 60);
    if (r.sunEstimated && spanMs > 0) estimated = true;
  });
  return { sunMs, estimated };
}

// --- start day ---

// Sunrise and sunset at the route's start point on the start's calendar day
// in `timeZone` (the route's, or the browser's for null): sun.js sunTimes()
// plus that day's midnight. null without a route or start.
export function startDaySun(state) {
  const { route, startMs, timeZone } = state;
  if (!route || !isNum(startMs)) return null;
  const { year, month, day } = zonedParts(startMs, timeZone);
  const next = new Date(Date.UTC(year, month - 1, day + 1));
  const dayStartMs = zonedTimeToMs({ year, month, day, hour: 0, minute: 0 }, timeZone);
  const dayEndMs = zonedTimeToMs({
    year: next.getUTCFullYear(),
    month: next.getUTCMonth() + 1,
    day: next.getUTCDate(),
    hour: 0,
    minute: 0,
  }, timeZone);
  const { lat, lon } = route.points[0];
  return { dayStartMs, ...sunTimes(dayStartMs, dayEndMs, lat, lon) };
}

// --- at a position ---

// Daylight at `distKm` at time `tMs`: { up, next } where `next` is the next
// sunrise or sunset there ({ kind, tMs }) within 2 days, or null (polar day
// or night). null without a route or time.
export function daylightAt(state, distKm, tMs) {
  if (!state.route || !isNum(tMs)) return null;
  const { lat, lon } = positionAtDistance(state.route, distKm);
  return { up: isDaylight(tMs, lat, lon), next: nextSunEvent(tMs, lat, lon) };
}

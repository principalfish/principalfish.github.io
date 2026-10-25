// Time maths for the planner: the forecast window to request, the default and
// latest valid start, and conversion between epoch ms and the browser's local
// `datetime-local` input value. Pure functions; "local" means the browser's own
// time zone, which is what a `datetime-local` input is entered in.

import { FORECAST_HOURS } from "./config.js";

export const MINUTE_MS = 60 * 1000;
export const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

const pad2 = (n) => String(n).padStart(2, "0");

// --- forecast window ---

// The whole window Open-Meteo can serve right now: from the current hour
// (floored, UTC) to the last hour of its FORECAST_HOURS run, which starts at
// 00:00 UTC today, not now. The page fetches this once per route so any start
// inside it can be recomputed without another request.
export function forecastWindow(nowMs) {
  const todayUtc = Math.floor(nowMs / DAY_MS) * DAY_MS;
  return {
    startHour: Math.floor(nowMs / HOUR_MS) * HOUR_MS,
    endHour: todayUtc + (FORECAST_HOURS - 1) * HOUR_MS,
  };
}

// Hours spanned by a window, both ends inclusive (what Open-Meteo returns).
export function windowHours({ startHour, endHour }) {
  return Math.round((endHour - startHour) / HOUR_MS) + 1;
}

// --- starts ---

// The next whole LOCAL hour after `nowMs` (10:00 exactly -> 11:00), so a zone
// with a half-hour offset still gets an on-the-hour default. Done as "back to
// the top of the local hour, then one real hour on" rather than with
// setMinutes(60), which reads the result as a local wall time and so skips the
// repeated hour when clocks go back (01:30 BST -> 02:00 GMT, 90 minutes on).
export function nextWholeHour(nowMs) {
  const d = new Date(nowMs);
  const intoHourMs = d.getMinutes() * MINUTE_MS + d.getSeconds() * 1000 + d.getMilliseconds();
  return nowMs - intoHourMs + HOUR_MS;
}

// Latest start (epoch ms, floored to the minute) that still finishes by
// `lastForecastMs` after `durationMs` on the move.
export function latestStartMs(lastForecastMs, durationMs) {
  return Math.floor((lastForecastMs - durationMs) / MINUTE_MS) * MINUTE_MS;
}

// --- datetime-local conversion ---

// Epoch ms -> "YYYY-MM-DDTHH:MM" in local time (seconds dropped).
export function toLocalInputValue(ms) {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
    + `T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// "YYYY-MM-DDTHH:MM[:SS]" read as local time -> epoch ms, or NaN for anything
// else (an empty or half-typed input). The shape is checked first because
// Date.parse accepts loose text; a date-time without an offset is local time.
const LOCAL_INPUT_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/;

export function fromLocalInputValue(value) {
  if (typeof value !== "string" || !LOCAL_INPUT_PATTERN.test(value)) return NaN;
  return new Date(value).getTime();
}

// --- forecast span ---

// First and last hour (epoch ms) with real data in EVERY location, judged by
// temperature_2m. Open-Meteo pads hours a model run hasn't reached yet with
// nulls (NaN after normalising), so the requested window can overstate what is
// usable. All locations come from one request window and share `timesMs`.
// Throws when no hour has data everywhere.
export function forecastSpan(locations) {
  const { timesMs } = locations[0];
  const hasData = (t) => locations.every((loc) => Number.isFinite(loc.vars.temperature_2m[t]));
  let first = 0;
  while (first < timesMs.length && !hasData(first)) first++;
  let last = timesMs.length - 1;
  while (last > first && !hasData(last)) last--;
  if (first >= timesMs.length) {
    throw new Error("Open-Meteo returned no forecast data for this route");
  }
  return { startMs: timesMs[first], endMs: timesMs[last] };
}

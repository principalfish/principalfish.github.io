// Time maths for the planner: the forecast window to request, the default and
// latest valid start, and conversion between epoch ms and a `datetime-local`
// input value. Pure functions. "Local" means the wall clock in `timeZone`: the
// route's own zone once it is known (zone.js), or the browser's own zone when
// `timeZone` is null/undefined, which is what a `datetime-local` input is
// otherwise entered in.

import { FORECAST_HOURS } from "./config.js";
import { zonedParts, zonedTimeToMs } from "./zone.js";

export const MINUTE_MS = 60 * 1000;
export const HOUR_MS = 60 * MINUTE_MS;

const pad2 = (n) => String(n).padStart(2, "0");

// --- forecast window ---

// The window to request: from the current hour (floored, UTC) to
// FORECAST_HOURS after it. The page fetches this once per route so any start
// inside it can be recomputed without another request; the hours the model
// actually covers are found afterwards (forecastSpan).
export function forecastWindow(nowMs) {
  const startHour = Math.floor(nowMs / HOUR_MS) * HOUR_MS;
  return { startHour, endHour: startHour + FORECAST_HOURS * HOUR_MS };
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
export function nextWholeHour(nowMs, timeZone) {
  const { minute, second } = zonedParts(nowMs, timeZone);
  const ms = ((nowMs % 1000) + 1000) % 1000;
  return nowMs - (minute * MINUTE_MS + second * 1000 + ms) + HOUR_MS;
}

// Latest start (epoch ms, floored to the minute) that still finishes by
// `lastForecastMs` after `durationMs` on the move.
export function latestStartMs(lastForecastMs, durationMs) {
  return Math.floor((lastForecastMs - durationMs) / MINUTE_MS) * MINUTE_MS;
}

// --- datetime-local conversion ---

// Epoch ms -> "YYYY-MM-DDTHH:MM" in local time (seconds dropped).
export function toLocalInputValue(ms, timeZone) {
  const p = zonedParts(ms, timeZone);
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}T${pad2(p.hour)}:${pad2(p.minute)}`;
}

// "YYYY-MM-DDTHH:MM[:SS]" read as local time -> epoch ms, or NaN for anything
// else (an empty or half-typed input, or a date that doesn't exist). Seconds
// are dropped, as the input's step="60" never sets them. Clock changes resolve
// as zone.js describes (a repeated hour: the earlier one).
const LOCAL_INPUT_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(:\d{2})?$/;

export function fromLocalInputValue(value, timeZone) {
  const m = typeof value === "string" ? LOCAL_INPUT_PATTERN.exec(value) : null;
  if (!m) return NaN;
  const [year, month, day, hour, minute] = m.slice(1, 6).map(Number);
  const real = new Date(Date.UTC(year, month - 1, day));
  if (month < 1 || month > 12 || real.getUTCDate() !== day || hour > 23 || minute > 59) {
    return NaN;
  }
  return zonedTimeToMs({ year, month, day, hour, minute }, timeZone);
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

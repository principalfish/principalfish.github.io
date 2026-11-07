// Display helpers for the results views: local times, durations, rain rates,
// compass points, wind relative to travel and forecast-cell offsets. Pure
// functions returning plain text (always rendered with textContent).
//
// Times are wall-clock times in `timeZone`: the route's IANA zone once it is
// known, or the browser's own zone for null/undefined (zone.js).

import { formatDistance, formatPrecip, formatWind, MISSING } from "./units.js";
import { sameZonedDay, zonedParts } from "./zone.js";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const COMPASS_16 = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
];
const COMPASS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

const pad2 = (n) => String(n).padStart(2, "0");
// A real number: not NaN, ±Infinity, null or a numeric string. Shared by the
// playback modules.
export const isFiniteNumber = (v) => typeof v === "number" && Number.isFinite(v);

// --- time ---

// Local "HH:MM".
export function formatClock(ms, timeZone) {
  if (!isFiniteNumber(ms)) return MISSING;
  const p = zonedParts(ms, timeZone);
  return `${pad2(p.hour)}:${pad2(p.minute)}`;
}

// Local "HH:MM", prefixed with the weekday ("Tue 09:30") when not on the same
// local day as `nowMs`. For table rows, where the full date would be noise.
export function formatArrival(ms, nowMs, timeZone) {
  if (!isFiniteNumber(ms)) return MISSING;
  const clock = formatClock(ms, timeZone);
  if (sameZonedDay(ms, nowMs, timeZone)) return clock;
  return `${WEEKDAYS[zonedParts(ms, timeZone).weekday]} ${clock}`;
}

// Local "Mon 26 Oct 10:00". English names are fixed (the page is en-GB) rather
// than toLocaleString, whose output varies by engine and locale.
export function formatDateTime(ms, timeZone) {
  if (!isFiniteNumber(ms)) return MISSING;
  const p = zonedParts(ms, timeZone);
  return `${WEEKDAYS[p.weekday]} ${p.day} ${MONTHS[p.month - 1]} ${formatClock(ms, timeZone)}`;
}

// Duration -> "3 h 05 min" / "45 min", rounded to the minute.
export function formatDuration(ms) {
  if (!isFiniteNumber(ms) || ms < 0) return MISSING;
  const totalMin = Math.round(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h === 0 ? `${m} min` : `${h} h ${pad2(m)} min`;
}

// Duration -> hours to 1 dp ("2.4 h"), for sunshine and daylight totals.
export function formatHours(ms) {
  if (!isFiniteNumber(ms) || ms < 0) return MISSING;
  return `${(Math.round(ms / 360000) / 10).toFixed(1)} h`;
}

// --- rain ---

// Precipitation per hour -> "0.7 mm/h", marked "(3-h avg)" when it is a
// 3-hour forecast step's average (weather.js precipStepAt) rather than the
// hour's own amount; not on 0, where averaging changes nothing.
export function formatRainRate(mm, stepH, units) {
  if (!isFiniteNumber(mm)) return MISSING;
  const rate = `${formatPrecip(mm, units)}/h`;
  return stepH === 3 && mm > 0 ? `${rate} (3-h avg)` : rate;
}

// --- sky ---

// Cloud cover -> "72%".
export function formatCloud(pct) {
  return isFiniteNumber(pct) ? `${Math.round(pct)}%` : MISSING;
}

// Minutes of sunshine in the hour -> "12 min/h", "est." added when it is
// estimated rather than forecast (weather.js sunshineAt).
export function formatSun(sunMin, estimated = false) {
  if (!isFiniteNumber(sunMin)) return MISSING;
  return `${Math.round(sunMin)} min/h${estimated ? " est." : ""}`;
}

// daylight.js daylightAt() -> "Daylight · sunset 16:44 (in 1 h 20 min)" /
// "Dark · sunrise 06:43 (in 9 h 05 min)"; polar day or night without a next
// event within two days.
export function formatDaylight(daylight, tMs, timeZone) {
  if (!daylight) return MISSING;
  const now = daylight.up ? "Daylight" : "Dark";
  if (!daylight.next) return `${now} (${daylight.up ? "sun up all day" : "sun down all day"})`;
  const { kind, tMs: at } = daylight.next;
  return `${now} · ${kind} ${formatClock(at, timeZone)} (in ${formatDuration(at - tMs)})`;
}

// --- directions ---

// Degrees -> compass point; `points` is 16 (wind) or 8 (offsets).
export function compassPoint(deg, points = 16) {
  if (!isFiniteNumber(deg)) return MISSING;
  const names = points === 8 ? COMPASS_8 : COMPASS_16;
  const step = 360 / names.length;
  const i = Math.round((((deg % 360) + 360) % 360) / step) % names.length;
  return names[i];
}

// --- relative wind ---

// weather.js signs: headKmh > 0 is into your face, < 0 behind you.
// `units` is a units set or system string (units.js); both relative winds
// follow its wind setting.
export function formatHeadWind(headKmh, units) {
  if (!isFiniteNumber(headKmh)) return MISSING;
  const label = headKmh >= 0 ? "headwind" : "tailwind";
  return `${formatWind(Math.abs(headKmh), units)} ${label}`;
}

// crossKmh > 0 is from your right, < 0 from your left.
export function formatCrossWind(crossKmh, units) {
  if (!isFiniteNumber(crossKmh)) return MISSING;
  const side = crossKmh >= 0 ? "from right" : "from left";
  return `${formatWind(Math.abs(crossKmh), units)} ${side}`;
}

// --- forecast cell ---

// Signed decimal with a typographic minus ("−3.211").
const formatCoord = (deg) => (isFiniteNumber(deg) ? deg.toFixed(3).replace("-", "−") : MISSING);

// "54.448, −3.211 · 0.7 km SW": the model cell centre and where it lies from
// the route point. `cell` is { lat, lon, offsetKm, offsetBearingDeg }.
export function formatCell(cell, units) {
  if (!cell || !isFiniteNumber(cell.lat) || !isFiniteNumber(cell.lon)) return MISSING;
  const where = `${formatCoord(cell.lat)}, ${formatCoord(cell.lon)}`;
  if (!isFiniteNumber(cell.offsetKm)) return where;
  // Under ~50 m the bearing is noise (identical points give 0° = "N").
  if (cell.offsetKm < 0.05) return `${where} · ${formatDistance(cell.offsetKm, units)}`;
  const direction = compassPoint(cell.offsetBearingDeg, 8);
  return `${where} · ${formatDistance(cell.offsetKm, units)} ${direction}`;
}

// Labels for sample kinds that get a badge or marker; fillers have none.
export const KIND_LABELS = Object.freeze({
  start: "Start",
  end: "Finish",
  peak: "Peak",
  valley: "Valley",
});

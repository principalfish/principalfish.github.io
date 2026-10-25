// Display helpers for the results views: local times, durations, compass
// points, wind relative to travel and forecast-cell offsets. Pure functions
// returning plain text (always rendered with textContent).

import { formatDistance, formatSpeed, MISSING } from "./units.js";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const COMPASS_16 = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
];
const COMPASS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

const pad2 = (n) => String(n).padStart(2, "0");
const isFiniteNumber = (v) => typeof v === "number" && Number.isFinite(v);

// --- time ---

// Local "HH:MM".
export function formatClock(ms) {
  const d = new Date(ms);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

const sameLocalDay = (a, b) => new Date(a).toDateString() === new Date(b).toDateString();

// Local "HH:MM", prefixed with the weekday ("Tue 09:30") when not on the same
// local day as `nowMs`. For table rows, where the full date would be noise.
export function formatArrival(ms, nowMs) {
  if (!isFiniteNumber(ms)) return MISSING;
  const clock = formatClock(ms);
  return sameLocalDay(ms, nowMs) ? clock : `${WEEKDAYS[new Date(ms).getDay()]} ${clock}`;
}

// Local "Mon 26 Oct 10:00". English names are fixed (the page is en-GB) rather
// than toLocaleString, whose output varies by engine and locale.
export function formatDateTime(ms) {
  if (!isFiniteNumber(ms)) return MISSING;
  const d = new Date(ms);
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} ${formatClock(ms)}`;
}

// Duration -> "3 h 05 min" / "45 min", rounded to the minute.
export function formatDuration(ms) {
  if (!isFiniteNumber(ms) || ms < 0) return MISSING;
  const totalMin = Math.round(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h === 0 ? `${m} min` : `${h} h ${pad2(m)} min`;
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
export function formatHeadWind(headKmh, system) {
  if (!isFiniteNumber(headKmh)) return MISSING;
  const label = headKmh >= 0 ? "headwind" : "tailwind";
  return `${formatSpeed(Math.abs(headKmh), system)} ${label}`;
}

// crossKmh > 0 is from your right, < 0 from your left.
export function formatCrossWind(crossKmh, system) {
  if (!isFiniteNumber(crossKmh)) return MISSING;
  const side = crossKmh >= 0 ? "from right" : "from left";
  return `${formatSpeed(Math.abs(crossKmh), system)} ${side}`;
}

// --- forecast cell ---

// Signed decimal with a typographic minus ("−3.211").
const formatCoord = (deg) => (isFiniteNumber(deg) ? deg.toFixed(3).replace("-", "−") : MISSING);

// "54.448, −3.211 · 0.7 km SW": the model cell centre and where it lies from
// the route point. `cell` is { lat, lon, offsetKm, offsetBearingDeg }.
export function formatCell(cell, system) {
  if (!cell || !isFiniteNumber(cell.lat) || !isFiniteNumber(cell.lon)) return MISSING;
  const where = `${formatCoord(cell.lat)}, ${formatCoord(cell.lon)}`;
  if (!isFiniteNumber(cell.offsetKm)) return where;
  // Under ~50 m the bearing is noise (identical points give 0° = "N").
  if (cell.offsetKm < 0.05) return `${where} · ${formatDistance(cell.offsetKm, system)}`;
  const direction = compassPoint(cell.offsetBearingDeg, 8);
  return `${where} · ${formatDistance(cell.offsetKm, system)} ${direction}`;
}

// Result views: the route summary and the "Weather points" table. Each factory
// owns its part of the page and returns `update(state)`, called on every
// render; each rebuilds only when the state it reads has changed (by
// identity). Everything is written with textContent: the route name comes from
// the GPX file and is never parsed as HTML.

import { describeWeather } from "./weather.js";
import {
  formatDistance, formatElevation, formatPrecip, formatTemp, formatWind, MISSING,
} from "./units.js";
import {
  compassPoint, formatArrival, formatCell, formatClock, formatCloud, formatCrossWind,
  formatDateTime, formatDuration, formatHeadWind, formatHours, formatSun, KIND_LABELS,
} from "./display.js";
import { startDaySun } from "./daylight.js";
import { HOUR_MS } from "./schedule.js";
import { zoneLabel } from "./zone.js";

const OUTSIDE_NOTE = "outside the forecast window: nearest forecast hour shown";

// Points above the forecast's top level (weather.js aboveProfile): the height
// in each preset's units, rounded (the 700 hPa level wanders ±200 m).
const TOP_LEVEL_TEXT = { metric: "about 3,000 m", imperial: "about 9,800 ft" };
const FLAT_HORIZON_NOTE = "Sunrise and sunset are for a flat horizon; hills and valleys "
  + "can hide the sun for longer.";

const heldNote = (units) => `Above ${TOP_LEVEL_TEXT[units.system]}, temperature and wind are `
  + `held at their ${TOP_LEVEL_TEXT[units.system].replace("about ", "~")} values.`;

// True when any of `keys` differs from the last call's snapshot (then stored).
function createChangeCheck(keys) {
  let last = null;
  return (state) => {
    const changed = !last || keys.some((key) => last[key] !== state[key]);
    if (changed) last = Object.fromEntries(keys.map((key) => [key, state[key]]));
    return changed;
  };
}

function el(doc, tag, text, className) {
  const node = doc.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

// --- summary ---

// Notes under the summary list, as plain strings; [] without results.
export function summaryNotes(state) {
  const { results, units, daylight, timeZone } = state;
  if (!results) return [];
  const notes = [];
  if (results.some((r) => r.aboveProfile)) notes.push(heldNote(units));
  if (daylight) {
    const where = (e) => `${formatClock(e.tMs, timeZone)}, ${formatDistance(e.km, units)} in`;
    const sunrise = daylight.events.find((e) => e.kind === "sunrise");
    const sunset = daylight.events.find((e) => e.kind === "sunset");
    if (daylight.startsInDark && daylight.events.length === 0) {
      notes.push("The whole route is in the dark.");
    } else if (daylight.startsInDark) {
      notes.push(`Starts before sunrise: the sun rises at ${where(sunrise)}.`);
    }
    if (sunset) notes.push(`Runs past sunset: the sun sets at ${where(sunset)}.`);
    if (daylight.startsInDark || sunset) notes.push(FLAT_HORIZON_NOTE);
  }
  return notes;
}

// "↑ 06:43 · ↓ 16:44" at the start point on the start day, or "Up all day" /
// "Down all day".
function startDaySunText(state) {
  const sun = startDaySun(state);
  if (!sun) return MISSING;
  if (sun.allDay === "up") return "Up all day";
  if (sun.allDay === "down") return "Down all day";
  const clock = (ms) => (ms === null ? MISSING : formatClock(ms, state.timeZone));
  return `↑ ${clock(sun.sunriseMs)} · ↓ ${clock(sun.sunsetMs)}`;
}

// "~2.4 h of 9.5 h daylight", flagged when part of it is estimated.
function sunshineText({ sunshine, daylight }) {
  if (!sunshine || !daylight) return MISSING;
  const text = `~${formatHours(sunshine.sunMs)} of ${formatHours(daylight.daylightMs)} daylight`;
  return sunshine.estimated ? `${text} (partly est.)` : text;
}

export function createSummaryUI(doc) {
  const $ = (id) => doc.getElementById(id);
  const changed = createChangeCheck([
    "route", "routeName", "samples", "results", "speedKmh", "units", "timeZone", "startMs",
  ]);

  function update(state) {
    if (!changed(state)) return;
    const { route, units, results, times } = state;
    $("summary").hidden = !route;
    if (!route) return;

    $("routeName").textContent = state.routeName;
    const up = formatElevation(route.ascentM, units);
    const down = formatElevation(route.descentM, units);
    const climb = route.hasElevation ? `${up} up · ${down} down` : "No elevation data";
    const durationMs = (route.totalDistKm / state.speedKmh) * HOUR_MS;
    const { timeZone } = state;
    const at = (ms) => formatDateTime(ms, timeZone);
    const timing = results ? `${at(times[0])} → ${at(times[times.length - 1])}` : MISSING;
    const rows = [
      ["Distance", formatDistance(route.totalDistKm, units)],
      ["Ascent / descent", climb],
      ["Moving time", formatDuration(durationMs)],
      ["Start → finish", timing],
      ["Weather points", state.samples ? String(state.samples.length) : MISSING],
      ["Sun at the start", startDaySunText(state)],
      ["Sunshine on the route", sunshineText(state)],
    ];
    // Labelled at the start, so the abbreviation is the one in force then.
    if (timeZone) rows.push(["Times in", zoneLabel(times?.[0] ?? state.startMs, timeZone)]);

    const list = $("summaryList");
    list.innerHTML = "";
    for (const [term, value] of rows) {
      const item = el(doc, "div", undefined, "summary-item");
      item.append(el(doc, "dt", term), el(doc, "dd", value));
      list.append(item);
    }

    const notes = summaryNotes(state);
    $("summaryNotes").replaceChildren(...notes.map((text) => el(doc, "li", text)));
  }

  return { update };
}

// --- weather points table ---

function kindCell(doc, kind, aboveProfile, units) {
  const td = el(doc, "td");
  if (KIND_LABELS[kind]) td.append(el(doc, "span", KIND_LABELS[kind], `badge badge-${kind}`));
  if (aboveProfile) {
    const badge = el(doc, "span", "Held", "badge badge-held");
    badge.title = heldNote(units);
    td.append(badge, el(doc, "span", ` (${heldNote(units)})`, "sr-only"));
  }
  return td;
}

function pointRow(doc, state, i) {
  const { samples, results, times, cells, units } = state;
  const sample = samples[i];
  const w = results[i];
  // Without the route's own elevation, weather.js falls back to the model
  // cell's terrain height; say so rather than pass it off as the route's.
  const elevation = Number.isFinite(sample.ele)
    ? formatElevation(w.eleUsed, units)
    : `${formatElevation(w.eleUsed, units)} (model)`;
  const wind = `${formatWind(w.windKmh, units)} ${compassPoint(w.windFromDeg)}`;
  const rainChance = Number.isFinite(w.precipProb) ? `${Math.round(w.precipProb)}%` : MISSING;

  const tr = el(doc, "tr");
  const distance = el(doc, "td", formatDistance(sample.cumDistKm, units));
  if (w.outsideForecast) {
    tr.className = "outside";
    tr.title = OUTSIDE_NOTE;
    distance.append(el(doc, "span", ` (${OUTSIDE_NOTE})`, "sr-only"));
  }
  tr.append(
    distance,
    el(doc, "td", formatArrival(times[i], state.nowMs, state.timeZone)),
    el(doc, "td", elevation),
    kindCell(doc, sample.kind, w.aboveProfile, units),
    el(doc, "td", describeWeather(w.code)),
    el(doc, "td", formatTemp(w.tempC, units)),
    el(doc, "td", wind),
    el(doc, "td", formatWind(w.gustKmh, units)),
    el(doc, "td", formatHeadWind(w.headKmh, units)),
    el(doc, "td", formatCrossWind(w.crossKmh, units)),
    el(doc, "td", `${formatPrecip(w.precipMm, units)} · ${rainChance}`),
    el(doc, "td", formatCloud(w.cloudPct)),
    el(doc, "td", formatSun(w.sunMin, w.sunEstimated)),
    el(doc, "td", formatCell(cells[i], units)),
    el(doc, "td", formatElevation(w.cellElev, units)),
  );
  return tr;
}

export function createPointsTableUI(doc) {
  const $ = (id) => doc.getElementById(id);
  // results is replaced on every recompute; cells/samples change with a load.
  const changed = createChangeCheck([
    "results", "samples", "cells", "units", "nowMs", "timeZone",
  ]);

  function update(state) {
    if (!changed(state)) return;
    const body = $("pointsBody");
    body.innerHTML = "";
    $("pointsDetails").hidden = !state.results;
    if (!state.results) return;
    state.results.forEach((_, i) => body.append(pointRow(doc, state, i)));
  }

  return { update };
}

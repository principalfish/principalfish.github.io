// Result views: the route summary and the "Weather points" table. Each factory
// owns its part of the page and returns `update(state)`, called on every
// render; each rebuilds only when the state it reads has changed (by
// identity). Everything is written with textContent: the route name comes from
// the GPX file and is never parsed as HTML.

import { describeWeather } from "./weather.js";
import {
  formatDistance, formatElevation, formatPrecip, formatSpeed, formatTemp, MISSING,
} from "./units.js";
import {
  compassPoint, formatArrival, formatCell, formatCrossWind, formatDateTime, formatDuration,
  formatHeadWind, KIND_LABELS,
} from "./display.js";
import { HOUR_MS } from "./schedule.js";

const OUTSIDE_NOTE = "outside the forecast window: nearest forecast hour shown";

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

export function createSummaryUI(doc) {
  const $ = (id) => doc.getElementById(id);
  const changed = createChangeCheck([
    "route", "routeName", "samples", "results", "speedKmh", "units",
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
    const timing = results
      ? `${formatDateTime(times[0])} → ${formatDateTime(times[times.length - 1])}`
      : MISSING;
    const rows = [
      ["Distance", formatDistance(route.totalDistKm, units)],
      ["Ascent / descent", climb],
      ["Moving time", formatDuration(durationMs)],
      ["Start → finish", timing],
      ["Weather points", state.samples ? String(state.samples.length) : MISSING],
    ];

    const list = $("summaryList");
    list.innerHTML = "";
    for (const [term, value] of rows) {
      const item = el(doc, "div", undefined, "summary-item");
      item.append(el(doc, "dt", term), el(doc, "dd", value));
      list.append(item);
    }
  }

  return { update };
}

// --- weather points table ---

function kindCell(doc, kind) {
  const td = el(doc, "td");
  if (KIND_LABELS[kind]) td.append(el(doc, "span", KIND_LABELS[kind], `badge badge-${kind}`));
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
  const wind = `${formatSpeed(w.windKmh, units)} ${compassPoint(w.windFromDeg)}`;
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
    el(doc, "td", formatArrival(times[i], state.nowMs)),
    el(doc, "td", elevation),
    kindCell(doc, sample.kind),
    el(doc, "td", describeWeather(w.code)),
    el(doc, "td", formatTemp(w.tempC, units)),
    el(doc, "td", wind),
    el(doc, "td", formatSpeed(w.gustKmh, units)),
    el(doc, "td", formatHeadWind(w.headKmh, units)),
    el(doc, "td", formatCrossWind(w.crossKmh, units)),
    el(doc, "td", `${formatPrecip(w.precipMm, units)} · ${rainChance}`),
    el(doc, "td", formatSpeed(w.model10mWindKmh, units)),
    el(doc, "td", formatCell(cells[i], units)),
    el(doc, "td", formatElevation(w.cellElev, units)),
  );
  return tr;
}

export function createPointsTableUI(doc) {
  const $ = (id) => doc.getElementById(id);
  // results is replaced on every recompute; cells/samples change with a load.
  const changed = createChangeCheck(["results", "samples", "cells", "units", "nowMs"]);

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

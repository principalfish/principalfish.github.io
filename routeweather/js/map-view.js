// The route map: the GPX route on an OpenTopoMap base layer with start and
// finish, peaks and valleys, the Open-Meteo grid cell each weather point came
// from, and a position marker whose arrow shows the wind where you are.
//
// `createMapUI(container)` owns everything inside `container` (#mapPanel): a
// small toolbar with the forecast-cell toggle, and the Leaflet map element.
// It returns:
//
//   update(state)                  called on every render (a second `reason`
//                                  argument is ignored: diffing on identity,
//                                  below, already skips no-op renders)
//   setPosition(distKm, weather)   move the position marker along the route;
//                                  cheap enough for every animation frame
//                                  (see "Position and wind" below)
//   destroy()                      remove the map and the panel's contents
//
// The map is built on the first update with a route and then kept; a new
// route only replaces the overlay layers, so the tile cache survives a file
// change. The route, start/finish and peaks/valleys draw as soon as there is a
// route; forecast cells once `state.cells` exists; the wind arrow only while
// there are results.
//
// The controller shows and hides the panel, and Leaflet can't measure or fit a
// hidden (0×0) element and only re-measures itself on window resize. So a
// ResizeObserver on the map element calls invalidateSize() on every size
// change, and re-fits the route if it was fitted while the map had no size.
//
// What redraws when (by identity, since the controller replaces these objects
// rather than mutating them):
//   state.route            -> route lines, start/finish, fit bounds, position 0
//   state.samples / cells  -> peaks/valleys, forecast cells and connectors
//   results/locations/units-> tooltip text and the wind arrow only
//   none of them           -> nothing
//
// Position and wind: `setPosition(distKm, weather)` moves the marker and
// extends the "done" line (drawn over the full route, which never changes).
// `weather` (e.g. interpolated by the playback clock) sets the wind arrow and
// is kept: update() re-applies it on renders that leave `state.results` and
// the route unchanged (a units change, say), so a render never snaps the arrow
// back to a sample's wind. Passing null, a new route or new results drop it;
// without it the arrow shows the nearest sample's result.
//
// Drawn geometry: the route lines use a simplified copy of the GPX vertices
// (radial pre-pass + Douglas–Peucker, ~1 m), so a 60k-point track draws a few
// hundred to a few thousand vertices. The marker itself is still placed on the
// full route with positionAtDistance.
//
// Antimeridian: Leaflet draws a straight line between consecutive longitudes,
// so 179.9 -> -179.9 would cross the whole world. Longitudes are unwrapped
// along the route (179.9 -> 180.1), and every other point (samples, cells,
// the position marker) is shifted to within 180° of the route beside it.
//
// No GPX text ever reaches HTML: tooltips are DOM nodes written with
// textContent, and the only strings handed to Leaflet as HTML are constants.

import * as leaflet from "../../site/vendor/leaflet/leaflet.esm.js";
import { TILE_ATTRIBUTION, TILE_MAX_ZOOM, TILE_SUBDOMAINS, TILE_URL } from "./config.js";
import { positionAtDistance } from "./route.js";
import { formatElevation } from "./units.js";
import { KIND_LABELS, formatCell } from "./display.js";

// Path styles. Leaflet sets these as SVG attributes, so they live here rather
// than in map.css.
const STYLE = {
  // `done` is drawn on top of the full-length `rest`, so it must be wider and
  // opaque to cover it.
  rest: { color: "#2557c7", weight: 4, opacity: 0.85, interactive: false },
  done: { color: "#e8590c", weight: 6, opacity: 1, interactive: false },
  start: { radius: 6, color: "#ffffff", weight: 2, fillColor: "#2f9e44", fillOpacity: 1 },
  finish: { radius: 6, color: "#ffffff", weight: 2, fillColor: "#c92a2a", fillOpacity: 1 },
  cell: { radius: 3.5, color: "#ffffff", weight: 1, fillColor: "#7048e8", fillOpacity: 0.95 },
  connector: { color: "#5f3dc4", weight: 1, opacity: 0.5, dashArray: "2 3", interactive: false },
};

// Simplification tolerance for the drawn route, in degrees (~1 m; under a
// pixel at OpenTopoMap's max zoom 17).
const SIMPLIFY_TOLERANCE_DEG = 1e-5;

const FIT_PADDING = [24, 24];
// Don't zoom past street level on a very short route.
const FIT_MAX_ZOOM = 15;

// Forecast cells get their own pane under the route (overlayPane is 400), so
// the route stays on top whatever order the layers are rebuilt in.
const CELL_PANE = "rwCells";
const CELL_PANE_Z = 350;

// Wind speed bands (km/h, upper bound exclusive) -> arrow class.
const WIND_BANDS = [
  { maxKmh: 10, name: "calm" },
  { maxKmh: 25, name: "breezy" },
  { maxKmh: 45, name: "strong" },
  { maxKmh: Infinity, name: "gale" },
];

// Only extremes get a marker here; start and finish have their own.
const EXTREME_KINDS = new Set(["peak", "valley"]);

const SVG_NS = "http://www.w3.org/2000/svg";
// Arrow pointing up (north) in a 40×40 box; rotated to where the wind goes.
const ARROW_PATH = "M20 2 L30 16 L23 16 L23 36 L17 36 L17 16 L10 16 Z";

// --- geometry ---

// `lon` shifted by whole turns to lie within 180° of `refLon` (exactly `lon`
// when it already does, so ordinary routes pass through bit-for-bit).
export const unwrapNear = (lon, refLon) => lon + 360 * Math.round((refLon - lon) / 360);

// [[lat, lon]] for `points`, with each longitude unwrapped against the one
// before so consecutive points never differ by more than 180°.
export function unwrapLatLngs(points) {
  let prevLon = null;
  return points.map((p) => {
    const lon = prevLon === null ? p.lon : unwrapNear(p.lon, prevLon);
    prevLon = lon;
    return [p.lat, lon];
  });
}

// Squared distance (degrees²) from `p` to the segment `a`–`b`; all [lat, lon].
function segmentDist2(p, a, b) {
  let [y, x] = a;
  const dy = b[0] - y;
  const dx = b[1] - x;
  if (dx !== 0 || dy !== 0) {
    const t = ((p[0] - y) * dy + (p[1] - x) * dx) / (dy * dy + dx * dx);
    if (t > 1) [y, x] = b;
    else if (t > 0) {
      y += dy * t;
      x += dx * t;
    }
  }
  return (p[0] - y) ** 2 + (p[1] - x) ** 2;
}

// Indices of the [lat, lon] `pairs` to draw: a radial pass dropping points
// within `tolerance` of the last kept one, then Douglas–Peucker (iterative, so
// a long track can't overflow the stack). First and last are always kept.
export function simplifyIndices(pairs, tolerance = SIMPLIFY_TOLERANCE_DEG) {
  const tol2 = tolerance * tolerance;
  const last = pairs.length - 1;
  const radial = [0];
  for (let i = 1; i < last; i++) {
    const prev = pairs[radial[radial.length - 1]];
    if ((pairs[i][0] - prev[0]) ** 2 + (pairs[i][1] - prev[1]) ** 2 > tol2) radial.push(i);
  }
  if (last > 0) radial.push(last);
  if (radial.length <= 2) return radial;

  const keep = new Uint8Array(radial.length);
  keep[0] = 1;
  keep[radial.length - 1] = 1;
  const stack = [[0, radial.length - 1]];
  while (stack.length > 0) {
    const [a, b] = stack.pop();
    let maxD2 = tol2;
    let split = -1;
    for (let k = a + 1; k < b; k++) {
      const d2 = segmentDist2(pairs[radial[k]], pairs[radial[a]], pairs[radial[b]]);
      if (d2 > maxD2) {
        maxD2 = d2;
        split = k;
      }
    }
    if (split !== -1) {
      keep[split] = 1;
      stack.push([a, split], [split, b]);
    }
  }
  return radial.filter((_, k) => keep[k]);
}

// Index of the last entry of the ascending `cumDist` at or before `distKm`
// (0 when before the start).
function splitIndex(cumDist, distKm) {
  let lo = 0;
  let hi = cumDist.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (cumDist[mid] <= distKm) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

// Index of the entry of the ascending `cumDist` nearest `distKm`.
function nearestIndex(cumDist, distKm) {
  const i = splitIndex(cumDist, distKm);
  const next = cumDist[i + 1];
  return next !== undefined && next - distKm < distKm - cumDist[i] ? i + 1 : i;
}

// --- wind ---

function windBand(kmh) {
  return WIND_BANDS.find((band) => kmh < band.maxKmh).name;
}

// Where the wind blows TO, in [0, 360): the arrow's rotation.
export const windArrowDeg = (windFromDeg) => (((windFromDeg + 180) % 360) + 360) % 360;

// --- factory ---

export function createMapUI(container, { L = leaflet } = {}) {
  const doc = container.ownerDocument;

  let map = null;
  let cellRenderer = null;
  let destroyed = false;
  let fitPending = false; // fitted while the map had no size
  let showCells = true;

  // Per route (null until drawn).
  let route = null;
  let latlngs = []; // L.LatLng, unwrapped, the simplified (drawn) vertices
  let cumDist = []; // route cumDistKm of each drawn vertex
  let routeLayer = null;
  let doneLine = null;
  let positionMarker = null;
  let positionKm = 0;
  let lastWeather = null; // explicit setPosition weather, see the header
  let lastBand = null; // wind band class currently on the arrow

  // Per samples/cells.
  let samples = null;
  let sampleDist = [];
  let cells = null;
  let cellLayer = null;
  let extremeLayer = null;
  let cellTips = []; // { i, node }
  let extremeTips = []; // { i, node }

  // Latest results, locations and units, for tooltips and the wind arrow.
  let results = null;
  let locations = null;
  let units = "metric";

  // --- DOM ---

  function el(tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const toolbar = el("div", "map-toolbar");
  const toggle = el("input");
  toggle.type = "checkbox";
  toggle.checked = showCells;
  const toggleLabel = el("label", "map-toggle");
  toggleLabel.append(toggle, doc.createTextNode(" Forecast cells"));
  toolbar.append(
    toggleLabel,
    el("span", "muted small", "Dots: the forecast model grid cell each weather point uses."),
  );
  const mapEl = el("div", "route-map");
  container.replaceChildren(toolbar, mapEl);

  toggle.addEventListener("change", () => {
    showCells = toggle.checked;
    if (!map || !cellLayer) return;
    if (showCells) map.addLayer(cellLayer);
    else map.removeLayer(cellLayer);
  });

  // The position marker's content: a dot with the wind arrow behind it. Built
  // once and passed to the divIcon as an element, so each frame only touches
  // its style instead of rebuilding the icon.
  const arrowSvg = doc.createElementNS(SVG_NS, "svg");
  arrowSvg.setAttribute("viewBox", "0 0 40 40");
  arrowSvg.setAttribute("aria-hidden", "true");
  const arrowPath = doc.createElementNS(SVG_NS, "path");
  arrowPath.setAttribute("d", ARROW_PATH);
  arrowSvg.append(arrowPath);
  const arrow = el("span", "rw-wind");
  arrow.append(arrowSvg);
  arrow.hidden = true;
  const positionInner = el("span", "rw-position-inner");
  positionInner.append(arrow, el("span", "rw-dot"));

  // --- map ---

  function ensureMap() {
    if (map) return;
    // An explicit SVG renderer: Leaflet's default picks one by feature
    // detection at import time, which finds none under jsdom.
    map = L.map(mapEl, { renderer: new L.SVG() });
    map.createPane(CELL_PANE).style.zIndex = String(CELL_PANE_Z);
    cellRenderer = new L.SVG({ pane: CELL_PANE });
    L.tileLayer(TILE_URL, {
      subdomains: TILE_SUBDOMAINS,
      maxZoom: TILE_MAX_ZOOM,
      attribution: TILE_ATTRIBUTION,
    }).addTo(map);
  }

  const hasSize = () => mapEl.clientWidth > 0 && mapEl.clientHeight > 0;

  function fitRoute() {
    map.fitBounds(L.latLngBounds(latlngs), { padding: FIT_PADDING, maxZoom: FIT_MAX_ZOOM });
    fitPending = !hasSize();
  }

  function onResize() {
    if (!map || !hasSize()) return;
    map.invalidateSize();
    if (fitPending && route) fitRoute();
  }

  const win = doc.defaultView;
  const resizeObserver = win?.ResizeObserver ? new win.ResizeObserver(onResize) : null;
  resizeObserver?.observe(mapEl);

  function removeLayer(layer) {
    if (layer && map) map.removeLayer(layer);
    return null;
  }

  // --- route ---

  function drawRoute(next) {
    routeLayer = removeLayer(routeLayer);
    route = next;
    const pairs = unwrapLatLngs(route.points);
    const drawn = simplifyIndices(pairs);
    latlngs = drawn.map((i) => L.latLng(pairs[i][0], pairs[i][1]));
    cumDist = drawn.map((i) => route.points[i].cumDistKm);
    positionKm = 0;
    lastWeather = null;

    const restLine = L.polyline(latlngs, STYLE.rest);
    doneLine = L.polyline([], STYLE.done);
    const start = L.circleMarker(latlngs[0], STYLE.start);
    const finish = L.circleMarker(latlngs[latlngs.length - 1], STYLE.finish);
    start.bindTooltip("Start");
    finish.bindTooltip("Finish");
    positionMarker = L.marker(latlngs[0], {
      icon: L.divIcon({ className: "rw-position", html: positionInner, iconSize: [44, 44] }),
      interactive: false,
      keyboard: false,
      zIndexOffset: 1000,
    });
    routeLayer = L.layerGroup([restLine, doneLine, start, finish, positionMarker]);
    map.addLayer(routeLayer);
    fitRoute();
  }

  // The route's unwrapped longitude at `distKm`, for shifting nearby points.
  const refLonAt = (distKm) => latlngs[splitIndex(cumDist, distKm)].lng;

  // --- samples and cells ---

  function drawPoints(state) {
    cellLayer = removeLayer(cellLayer);
    extremeLayer = removeLayer(extremeLayer);
    samples = state.samples;
    cells = state.cells;
    sampleDist = samples ? samples.map((s) => s.cumDistKm) : [];
    cellTips = [];
    extremeTips = [];
    if (!samples) return;

    const sampleLatLngs = samples.map(
      (s) => L.latLng(s.lat, unwrapNear(s.lon, refLonAt(s.cumDistKm))),
    );

    const extremes = [];
    samples.forEach((s, i) => {
      if (!EXTREME_KINDS.has(s.kind)) return;
      const marker = L.marker(sampleLatLngs[i], {
        icon: L.divIcon({ className: `rw-extreme rw-${s.kind}`, iconSize: [14, 12] }),
        keyboard: false,
      });
      const node = el("span");
      marker.bindTooltip(node, { direction: "top" });
      extremeTips.push({ i, node });
      extremes.push(marker);
    });
    extremeLayer = L.layerGroup(extremes);
    map.addLayer(extremeLayer);

    const cellLayers = [];
    (cells ?? []).forEach((cell, i) => {
      if (i >= sampleLatLngs.length) return;
      if (!cell || !Number.isFinite(cell.lat) || !Number.isFinite(cell.lon)) return;
      const from = sampleLatLngs[i];
      const at = L.latLng(cell.lat, unwrapNear(cell.lon, from.lng));
      const pane = { pane: CELL_PANE, renderer: cellRenderer };
      const dot = L.circleMarker(at, { ...STYLE.cell, ...pane });
      const node = el("span");
      dot.bindTooltip(node, { direction: "top" });
      cellTips.push({ i, node });
      cellLayers.push(L.polyline([from, at], { ...STYLE.connector, ...pane }), dot);
    });
    cellLayer = L.layerGroup(cellLayers);
    if (showCells) map.addLayer(cellLayer);
  }

  // Tooltip text is rewritten on every update that gets this far: it depends
  // on units and on the forecast (model cell elevation), not just geometry.
  function refreshTooltips() {
    for (const { i, node } of cellTips) {
      const cellElev = formatElevation(results?.[i]?.cellElev ?? locations?.[i]?.cellElev, units);
      node.textContent = `Forecast cell ${formatCell(cells[i], units)} · model elevation ${cellElev}`;
    }
    for (const { i, node } of extremeTips) {
      const s = samples[i];
      const ele = results?.[i]?.eleUsed ?? s.ele;
      node.textContent = `${KIND_LABELS[s.kind]} · ${formatElevation(ele, units)}`;
    }
  }

  // --- position ---

  function showWind(weather) {
    const kmh = weather?.windKmh;
    const fromDeg = weather?.windFromDeg;
    if (!Number.isFinite(kmh) || !Number.isFinite(fromDeg)) {
      arrow.hidden = true;
      return;
    }
    arrow.hidden = false;
    // Per frame: only the rotation, and the class when the band changes.
    const band = windBand(kmh);
    if (band !== lastBand) {
      arrow.className = `rw-wind wind-${band}`;
      lastBand = band;
    }
    arrow.style.transform = `rotate(${windArrowDeg(fromDeg)}deg)`;
  }

  // Moves the position marker to `distKm` along the route and redraws the
  // done line up to it. `weather` drives the wind arrow and is kept (see the
  // header); without it, the nearest sample's result.
  function setPosition(distKm, weather = null) {
    if (!route || destroyed) return;
    lastWeather = weather;
    positionKm = Math.min(Math.max(Number(distKm) || 0, 0), route.totalDistKm);
    const i = splitIndex(cumDist, positionKm);
    const pos = positionAtDistance(route, positionKm);
    const here = L.latLng(pos.lat, unwrapNear(pos.lon, latlngs[i].lng));

    const done = latlngs.slice(0, i + 1);
    done.push(here);
    doneLine.setLatLngs(done);
    positionMarker.setLatLng(here);

    const nearest = results && samples ? results[nearestIndex(sampleDist, positionKm)] : null;
    showWind(weather ?? nearest);
  }

  // --- update ---

  function clearRoute() {
    routeLayer = removeLayer(routeLayer);
    cellLayer = removeLayer(cellLayer);
    extremeLayer = removeLayer(extremeLayer);
    route = null;
    samples = null;
    sampleDist = [];
    cells = null;
    cellTips = [];
    extremeTips = [];
  }

  function update(state) {
    if (destroyed) return;
    const unchanged = state.route === route && state.samples === samples && state.cells === cells
      && state.results === results && (state.locations ?? null) === locations
      && state.units === units;
    if (unchanged) return;
    if (state.results !== results) lastWeather = null;
    units = state.units;
    results = state.results;
    locations = state.locations ?? null;
    if (!state.route) {
      clearRoute();
      return;
    }

    ensureMap();
    // Fallback for browsers without ResizeObserver: catch up on a render.
    if (fitPending && hasSize()) onResize();
    if (state.route !== route) {
      drawRoute(state.route);
      drawPoints(state);
    } else if (state.samples !== samples || state.cells !== cells) {
      drawPoints(state);
    }
    refreshTooltips();
    setPosition(positionKm, lastWeather);
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    resizeObserver?.disconnect();
    if (map) map.remove();
    map = null;
    route = null;
    container.replaceChildren();
  }

  return { update, setPosition, destroy };
}

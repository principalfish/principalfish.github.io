// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from "vitest";

import * as realLeaflet from "../../site/vendor/leaflet/leaflet.esm.js";
import {
  createMapUI, simplifyIndices, unwrapLatLngs, unwrapNear, windArrowDeg,
} from "../js/map-view.js";
import { buildRoute, positionAtDistance } from "../js/route.js";
import { TILE_ATTRIBUTION, TILE_MAX_ZOOM, TILE_SUBDOMAINS, TILE_URL } from "../js/config.js";
import { resolveUnits } from "../js/units.js";

const METRIC = resolveUnits("metric");
const IMPERIAL = resolveUnits("imperial");

// --- fake Leaflet ---

// Just enough of Leaflet's API for map-view.js, recording what it was asked to
// draw so the tests can assert on it directly.
function fakeLeaflet() {
  const made = { maps: [], tileLayers: [], polylines: [], circleMarkers: [], markers: [], groups: [] };
  const layer = (props) => ({
    ...props,
    tooltip: null,
    bindTooltip(content, options) {
      this.tooltip = { content, options };
      return this;
    },
  });
  const L = {
    latLng: (lat, lng) => ({ lat, lng }),
    latLngBounds: (latlngs) => ({ latlngs }),
    SVG: class {
      constructor(options) { this.options = options; }
    },
    map(el, options) {
      const map = {
        el,
        options,
        layers: new Set(),
        panes: {},
        fits: [],
        invalidated: 0,
        removed: false,
        addLayer(l) { this.layers.add(l); return this; },
        removeLayer(l) { this.layers.delete(l); return this; },
        hasLayer(l) { return this.layers.has(l); },
        createPane(name) {
          this.panes[name] = { style: {} };
          return this.panes[name];
        },
        fitBounds(bounds, opts) { this.fits.push({ bounds, opts }); return this; },
        invalidateSize() { this.invalidated++; return this; },
        remove() { this.removed = true; },
      };
      made.maps.push(map);
      return map;
    },
    tileLayer(url, options) {
      const t = { url, options, addTo(map) { map.addLayer(this); return this; } };
      made.tileLayers.push(t);
      return t;
    },
    polyline(latlngs, options) {
      const p = layer({ latlngs, options, setLatLngs(l) { this.latlngs = l; return this; } });
      made.polylines.push(p);
      return p;
    },
    circleMarker(latlng, options) {
      const c = layer({ latlng, options });
      made.circleMarkers.push(c);
      return c;
    },
    marker(latlng, options) {
      const m = layer({ latlng, options, setLatLng(l) { this.latlng = l; return this; } });
      made.markers.push(m);
      return m;
    },
    divIcon: (options) => ({ ...options }),
    layerGroup(layers) {
      const g = { layers };
      made.groups.push(g);
      return g;
    },
  };
  return { L, made };
}

// --- fixtures ---

// Four GPX vertices zig-zagging east, ~2.3 km apart, with a hump in the
// middle. Not collinear, so simplification keeps every one.
const POINTS = [
  { lat: 54.4, lon: -3.2, ele: 100 },
  { lat: 54.41, lon: -3.165, ele: 400 },
  { lat: 54.395, lon: -3.13, ele: 200 },
  { lat: 54.405, lon: -3.095, ele: 250 },
];

const sampleAt = (route, index, kind) => ({
  index, ...route.points[index], kind,
});

function makeState(route = buildRoute(POINTS), overrides = {}) {
  const samples = [
    sampleAt(route, 0, "start"),
    sampleAt(route, 1, "peak"),
    sampleAt(route, 2, "valley"),
    sampleAt(route, 3, "end"),
  ];
  const cells = [
    { lat: 54.448, lon: -3.211, offsetKm: 0.7, offsetBearingDeg: 225 },
    { lat: 54.401, lon: -3.16, offsetKm: 0.3, offsetBearingDeg: 80 },
    { lat: NaN, lon: NaN, offsetKm: NaN, offsetBearingDeg: NaN }, // no cell known
    { lat: 54.41, lon: -3.1, offsetKm: 1.2, offsetBearingDeg: 0 },
  ];
  const results = samples.map((s, i) => ({
    windKmh: [5, 20, 30, 60][i],
    windFromDeg: [270, 225, 90, 0][i],
    cellElev: 509 + i,
    eleUsed: s.ele + 7, // smoothed, so not the raw GPX value
  }));
  return { route, samples, cells, results, units: METRIC, ...overrides };
}

const lngs = (latlngs) => latlngs.map((ll) => ll.lng);

// --- pure helpers ---

describe("unwrapLatLngs", () => {
  it("keeps consecutive longitudes within 180° across the antimeridian", () => {
    const out = unwrapLatLngs([
      { lat: 0, lon: 179.9 }, { lat: 0, lon: -179.9 }, { lat: 0, lon: -179.8 },
    ]);
    expect(out.map(([, lon]) => lon)).toEqual([179.9, expect.closeTo(180.1, 9), expect.closeTo(180.2, 9)]);
    expect(out[0][0]).toBe(0);
  });

  it("unwraps westward crossings and leaves ordinary routes alone", () => {
    const west = unwrapLatLngs([{ lat: 1, lon: -179.95 }, { lat: 1, lon: 179.95 }]);
    expect(west[1][1]).toBeCloseTo(-180.05, 9);
    const plain = unwrapLatLngs(POINTS);
    expect(plain.map(([, lon]) => lon)).toEqual(POINTS.map((p) => p.lon));
  });

  it("unwrapNear shifts by whole turns only", () => {
    expect(unwrapNear(-179.9, 179.9)).toBeCloseTo(180.1, 9);
    expect(unwrapNear(10, 370)).toBe(370);
    expect(unwrapNear(-3.2, -3.1)).toBeCloseTo(-3.2, 9);
  });

  it("simplifyIndices drops collinear and near-duplicate vertices, keeping the ends", () => {
    const line = Array.from({ length: 101 }, (_, i) => [54, -3 + i * 0.001]);
    expect(simplifyIndices(line)).toEqual([0, 100]);
    const bend = [[0, 0], [0, 0.000001], [0, 1], [1, 1]];
    expect(simplifyIndices(bend)).toEqual([0, 2, 3]);
    expect(simplifyIndices([[0, 0]])).toEqual([0]);
    expect(simplifyIndices([[0, 0], [0, 0]])).toEqual([0, 1]);
  });

  it("windArrowDeg points where the wind goes", () => {
    expect(windArrowDeg(270)).toBe(90);
    expect(windArrowDeg(90)).toBe(270);
    expect(windArrowDeg(180)).toBe(0);
    expect(windArrowDeg(-45)).toBe(135);
  });
});

// --- with a fake Leaflet ---

describe("createMapUI (fake Leaflet)", () => {
  let container;
  let L;
  let made;
  let ui;

  beforeEach(() => {
    document.body.innerHTML = '<section id="mapPanel"></section>';
    container = document.getElementById("mapPanel");
    ({ L, made } = fakeLeaflet());
    ui = createMapUI(container, { L });
  });

  // The arrow lives in the position marker's divIcon element.
  const arrow = () => made.markers
    .find((m) => m.options.icon.className === "rw-position")
    .options.icon.html.querySelector(".rw-wind");
  const toggle = () => container.querySelector(".map-toggle input");
  const map = () => made.maps[0];
  const routeLines = () => made.polylines.filter((p) => p.options.pane === undefined);
  const lastLines = () => {
    const lines = routeLines();
    return { rest: lines[lines.length - 2], done: lines[lines.length - 1] };
  };

  it("builds its own toolbar and map element", () => {
    expect(container.querySelector(".map-toolbar")).not.toBeNull();
    expect(container.querySelector(".route-map")).not.toBeNull();
    expect(toggle().checked).toBe(true);
  });

  it("creates the map on the first route and draws it before any forecast", () => {
    ui.update({ route: null, samples: null, cells: null, results: null, units: METRIC });
    ui.setPosition(1);
    expect(made.maps).toHaveLength(0);

    // Forecast still loading: route, start/finish and peaks, no cells or arrow.
    const state = makeState();
    ui.update({ ...state, cells: null, results: null });
    expect(made.maps).toHaveLength(1);
    expect(map().el).toBe(container.querySelector(".route-map"));
    expect(routeLines()).toHaveLength(2);
    expect(made.markers.filter((m) => m.options.icon.className.startsWith("rw-extreme"))).toHaveLength(2);
    expect(made.circleMarkers.filter((c) => c.options.pane === "rwCells")).toHaveLength(0);
    expect(arrow().hidden).toBe(true);
    const peak = made.markers.find((m) => m.options.icon.className === "rw-extreme rw-peak");
    expect(peak.tooltip.content.textContent).toBe("Peak · 400 m"); // the sample's own elevation

    // Forecast arrives (same route): cells and the arrow appear, no new map.
    ui.update(state);
    expect(made.maps).toHaveLength(1);
    expect(routeLines()).toHaveLength(2);
    expect(made.circleMarkers.filter((c) => c.options.pane === "rwCells")).toHaveLength(3);
    expect(arrow().hidden).toBe(false);
    const peakNow = made.markers.findLast((m) => m.options.icon.className === "rw-extreme rw-peak");
    expect(peakNow.tooltip.content.textContent).toBe("Peak · 407 m"); // the elevation weather used

    // Results go away again (e.g. an invalid speed): the arrow hides.
    ui.update({ ...state, results: null });
    expect(arrow().hidden).toBe(true);
  });

  it("uses the location's model elevation in cell tooltips before results", () => {
    const state = makeState();
    const locations = state.samples.map((_, i) => ({ cellElev: 600 + i }));
    ui.update({ ...state, results: null, locations });
    const dot = made.circleMarkers.find((c) => c.options.pane === "rwCells");
    expect(dot.tooltip.content.textContent).toContain("model elevation 600 m");

    // Only `locations` changes (e.g. a retry with the same cells).
    ui.update({ ...state, results: null, locations: locations.map((l) => ({ cellElev: l.cellElev + 100 })) });
    expect(dot.tooltip.content.textContent).toContain("model elevation 700 m");
  });

  it("does nothing on a render where no input it draws from changed", () => {
    const state = makeState();
    ui.update(state);
    const dot = made.circleMarkers.find((c) => c.options.pane === "rwCells");
    dot.tooltip.content.textContent = "untouched";
    ui.update({ ...state }, "status");
    expect(dot.tooltip.content.textContent).toBe("untouched");
    ui.update({ ...state, units: IMPERIAL }, "units");
    expect(dot.tooltip.content.textContent).toContain("0.4 mi SW");
  });

  it("adds the OpenTopoMap tile layer from config", () => {
    ui.update(makeState());
    expect(made.tileLayers).toHaveLength(1);
    const [tiles] = made.tileLayers;
    expect(tiles.url).toBe(TILE_URL);
    expect(tiles.options).toEqual({
      subdomains: TILE_SUBDOMAINS, maxZoom: TILE_MAX_ZOOM, attribution: TILE_ATTRIBUTION,
    });
    expect(map().hasLayer(tiles)).toBe(true);
  });

  it("draws the route, start and finish, and fits it with padding", () => {
    const state = makeState();
    ui.update(state);
    const { rest, done } = lastLines();
    // At position 0: nothing done yet, the rest is the whole route.
    expect(lngs(rest.latlngs)).toEqual(POINTS.map((p) => p.lon));
    expect(lngs(done.latlngs)).toEqual([-3.2, -3.2]);
    const [start, finish] = made.circleMarkers.filter((c) => c.options.pane === undefined);
    expect(start.latlng).toEqual({ lat: 54.4, lng: -3.2 });
    expect(finish.latlng).toEqual({ lat: 54.405, lng: -3.095 });
    expect(map().fits).toHaveLength(1);
    expect(map().fits[0].opts).toEqual({ padding: [24, 24], maxZoom: 15 });
    expect(lngs(map().fits[0].bounds.latlngs)).toEqual(POINTS.map((p) => p.lon));
  });

  it("unwraps a route crossing the antimeridian, with its markers and cells", () => {
    const route = buildRoute([
      { lat: -16.8, lon: 179.9, ele: 10 },
      { lat: -16.75, lon: -179.9, ele: 50 },
      { lat: -16.8, lon: -179.8, ele: 20 },
    ]);
    const samples = [sampleAt(route, 0, "start"), sampleAt(route, 1, "peak"), sampleAt(route, 2, "end")];
    const cells = [179.95, -179.85, -179.75].map((lon) => (
      { lat: -16.8, lon, offsetKm: 5, offsetBearingDeg: 90 }
    ));
    const results = samples.map(() => ({ windKmh: 10, windFromDeg: 0, cellElev: 0, eleUsed: 0 }));
    ui.update({ route, samples, cells, results, units: METRIC });

    const fitted = lngs(map().fits[0].bounds.latlngs);
    expect(fitted[0]).toBe(179.9);
    expect(fitted[1]).toBeCloseTo(180.1, 9);
    expect(fitted[2]).toBeCloseTo(180.2, 9);
    const peak = made.markers.find((m) => m.options.icon.className === "rw-extreme rw-peak");
    expect(peak.latlng.lng).toBeCloseTo(180.1, 9);
    const connectors = made.polylines.filter((p) => p.options.pane === "rwCells");
    expect(lngs(connectors.map((c) => c.latlngs[0]))).toEqual([
      179.9, expect.closeTo(180.1, 9), expect.closeTo(180.2, 9),
    ]);
    const dots = made.circleMarkers.filter((c) => c.options.pane === "rwCells");
    expect(lngs(dots.map((d) => d.latlng))).toEqual([
      179.95, expect.closeTo(180.15, 9), expect.closeTo(180.25, 9),
    ]);

    // The position marker follows the unwrapped line too.
    ui.setPosition(route.totalDistKm * 0.75);
    const marker = made.markers.find((m) => m.options.icon.className === "rw-position");
    expect(marker.latlng.lng).toBeGreaterThan(180);
  });

  it("skips cells beyond the samples", () => {
    const state = makeState();
    ui.update({ ...state, cells: [...state.cells, { lat: 1, lon: 1, offsetKm: 0, offsetBearingDeg: 0 }] });
    expect(made.circleMarkers.filter((c) => c.options.pane === "rwCells")).toHaveLength(3);
  });

  it("draws one cell dot and one connector per sample with a known cell", () => {
    const state = makeState();
    ui.update(state);
    const dots = made.circleMarkers.filter((c) => c.options.pane === "rwCells");
    const connectors = made.polylines.filter((p) => p.options.pane === "rwCells");
    expect(dots).toHaveLength(3); // the NaN cell is skipped
    expect(connectors).toHaveLength(3);
    expect(dots[0].latlng).toEqual({ lat: 54.448, lng: -3.211 });
    expect(connectors[0].latlngs).toEqual([{ lat: 54.4, lng: -3.2 }, { lat: 54.448, lng: -3.211 }]);
    expect(connectors[0].options.interactive).toBe(false);
    // Rendered in their own pane under the route.
    expect(map().panes.rwCells.style.zIndex).toBe("350");
    expect(dots[0].options.renderer).toBeInstanceOf(L.SVG);
  });

  it("writes cell tooltips as text and rewrites them on a units change", () => {
    const state = makeState();
    ui.update(state);
    const dot = made.circleMarkers.find((c) => c.options.pane === "rwCells");
    const node = dot.tooltip.content;
    expect(node.nodeType).toBe(1); // a DOM node, not an HTML string
    expect(node.textContent).toBe("Forecast cell 54.448, −3.211 · 0.7 km SW · model elevation 509 m");

    ui.update({ ...state, units: IMPERIAL });
    expect(node.textContent).toBe("Forecast cell 54.448, −3.211 · 0.4 mi SW · model elevation 1670 ft");

    // Wind, rain and temperature overrides leave distance and elevation on the preset.
    ui.update({ ...state, units: resolveUnits("metric", { wind: "mph", rain: "in", temp: "f" }) });
    expect(node.textContent).toBe("Forecast cell 54.448, −3.211 · 0.7 km SW · model elevation 509 m");
  });

  it("toggles the forecast-cell layer from the checkbox", () => {
    const state = makeState();
    ui.update(state);
    const cellGroup = made.groups.find((g) => g.layers.some((l) => l.options?.pane === "rwCells"));
    expect(map().hasLayer(cellGroup)).toBe(true);
    toggle().checked = false;
    toggle().dispatchEvent(new Event("change"));
    expect(map().hasLayer(cellGroup)).toBe(false);

    // Stays off when the cells are redrawn (e.g. after a retry).
    ui.update({ ...state, cells: state.cells.map((c) => ({ ...c })) });
    const latest = made.groups[made.groups.length - 1];
    expect(map().hasLayer(latest)).toBe(false);

    toggle().checked = true;
    toggle().dispatchEvent(new Event("change"));
    expect(map().hasLayer(latest)).toBe(true);
  });

  it("honours a toggle made before any cells exist", () => {
    toggle().checked = false;
    toggle().dispatchEvent(new Event("change")); // no map yet: must not throw
    const state = makeState();
    ui.update({ ...state, cells: null });
    toggle().checked = true;
    toggle().dispatchEvent(new Event("change")); // map, but no cells yet
    toggle().checked = false;
    toggle().dispatchEvent(new Event("change"));
    ui.update(state);
    const cellGroup = made.groups.find((g) => g.layers.some((l) => l.options?.pane === "rwCells"));
    expect(map().hasLayer(cellGroup)).toBe(false);
  });

  it("marks peaks and valleys with triangle divIcons and elevation tooltips", () => {
    ui.update(makeState());
    const extremes = made.markers.filter((m) => m.options.icon.className.startsWith("rw-extreme"));
    expect(extremes.map((m) => m.options.icon.className)).toEqual([
      "rw-extreme rw-peak", "rw-extreme rw-valley",
    ]);
    expect(extremes[0].latlng).toEqual({ lat: 54.41, lng: -3.165 });
    expect(extremes[0].tooltip.content.textContent).toBe("Peak · 407 m");
    expect(extremes[1].tooltip.content.textContent).toBe("Valley · 207 m");
  });

  it("setPosition extends the done line to the marker over a static full route", () => {
    const state = makeState();
    ui.update(state);
    const { rest, done } = lastLines();
    const { points } = state.route;
    const marker = made.markers.find((m) => m.options.icon.className === "rw-position");
    const full = lngs(rest.latlngs);

    // Halfway along the second segment: vertices 0 and 1 are done.
    const d = (points[1].cumDistKm + points[2].cumDistKm) / 2;
    ui.setPosition(d);
    expect(done.latlngs).toHaveLength(3);
    expect(lngs(done.latlngs.slice(0, 2))).toEqual([-3.2, -3.165]);
    const pos = positionAtDistance(state.route, d);
    expect(done.latlngs[2]).toEqual({ lat: pos.lat, lng: pos.lon });
    expect(marker.latlng).toBe(done.latlngs[2]);
    expect(lngs(rest.latlngs)).toEqual(full); // never touched

    // Past the end clamps to the finish; the line objects are reused.
    const lineCount = made.polylines.length;
    ui.setPosition(1e6);
    expect(lngs(done.latlngs)).toEqual([...POINTS.map((p) => p.lon), -3.095]);
    expect(marker.latlng).toBe(done.latlngs[done.latlngs.length - 1]);
    expect(made.polylines).toHaveLength(lineCount);
  });

  it("draws a 60k-point track simplified, with the marker still on the full route", () => {
    // A 5 km-radius half circle sampled every ~0.26 m.
    const n = 60000;
    const points = Array.from({ length: n }, (_, i) => {
      const t = (Math.PI * i) / (n - 1);
      return { lat: 54 + 0.045 * Math.sin(t), lon: -3 + 0.077 * Math.cos(t) };
    });
    const route = buildRoute(points);
    const samples = [sampleAt(route, 0, "start"), sampleAt(route, n - 1, "end")];
    ui.update({ route, samples, cells: null, results: null, units: METRIC });

    const { rest, done } = lastLines();
    expect(rest.latlngs.length).toBeLessThan(1000);
    expect(rest.latlngs[0]).toEqual({ lat: points[0].lat, lng: points[0].lon });
    expect(rest.latlngs.at(-1)).toEqual({ lat: points[n - 1].lat, lng: points[n - 1].lon });

    const marker = made.markers.find((m) => m.options.icon.className === "rw-position");
    for (const frac of [0.1, 0.5, 0.93]) {
      const d = route.totalDistKm * frac;
      ui.setPosition(d);
      const pos = positionAtDistance(route, d);
      expect(marker.latlng).toEqual({ lat: pos.lat, lng: pos.lon });
      expect(done.latlngs.at(-1)).toBe(marker.latlng);
      expect(done.latlngs.length).toBeLessThan(rest.latlngs.length + 2);
    }
  });

  it("rotates the wind arrow to where the wind blows, from the nearest sample", () => {
    const state = makeState();
    ui.update(state);
    // Position 0: the start sample, wind from 270 at 5 km/h.
    expect(arrow().hidden).toBe(false);
    expect(arrow().style.transform).toBe("rotate(90deg)");
    expect(arrow().classList.contains("wind-calm")).toBe(true);

    // Nearer the valley sample (from 90 at 30 km/h).
    ui.setPosition(state.route.points[2].cumDistKm - 0.1);
    expect(arrow().style.transform).toBe("rotate(270deg)");
    expect(arrow().classList.contains("wind-strong")).toBe(true);

    // Explicit (interpolated) weather wins over the nearest sample.
    ui.setPosition(0, { windKmh: 50, windFromDeg: 45 });
    expect(arrow().style.transform).toBe("rotate(225deg)");
    expect(arrow().classList.contains("wind-gale")).toBe(true);

    // Unknown wind hides the arrow.
    ui.setPosition(0, { windKmh: NaN, windFromDeg: 45 });
    expect(arrow().hidden).toBe(true);
  });

  it("bands wind speed at the band edges and only rewrites the class on a band change", () => {
    ui.update(makeState());
    const a = arrow();
    const band = () => [...a.classList].find((c) => c.startsWith("wind-"));
    const native = Object.getOwnPropertyDescriptor(Element.prototype, "className");
    let writes = 0;
    Object.defineProperty(a, "className", {
      configurable: true,
      get() { return native.get.call(this); },
      set(v) { writes++; native.set.call(this, v); },
    });

    ui.setPosition(0, { windKmh: 9.9, windFromDeg: 0 });
    expect(band()).toBe("wind-calm");
    expect(writes).toBe(0); // already calm from the start sample
    ui.setPosition(0, { windKmh: 10, windFromDeg: 10 });
    expect(band()).toBe("wind-breezy");
    ui.setPosition(0, { windKmh: 24.9, windFromDeg: 20 });
    expect(writes).toBe(1);
    expect(a.style.transform).toBe("rotate(200deg)");
    ui.setPosition(0, { windKmh: 25, windFromDeg: 0 });
    expect(band()).toBe("wind-strong");
    ui.setPosition(0, { windKmh: 45, windFromDeg: 0 });
    expect(band()).toBe("wind-gale");
    expect(writes).toBe(3);
    expect(a.title).toBe("");
  });

  it("keeps explicit weather across renders until the results change", () => {
    const state = makeState();
    ui.update(state);
    ui.setPosition(1, { windKmh: 50, windFromDeg: 45 });
    ui.update({ ...state, units: IMPERIAL });
    expect(arrow().style.transform).toBe("rotate(225deg)"); // not snapped to a sample

    ui.update({ ...state, units: IMPERIAL, results: state.results.map((r) => ({ ...r })) });
    // New results: back to the nearest sample (the start, from 270).
    expect(arrow().style.transform).toBe("rotate(90deg)");

    ui.setPosition(1, { windKmh: 50, windFromDeg: 45 });
    ui.setPosition(1); // an explicit null drops it too
    expect(arrow().style.transform).toBe("rotate(90deg)");
  });

  it("keeps the map and layers on a same-route update, refreshing only text and the arrow", () => {
    const state = makeState();
    ui.update(state);
    const counts = [made.polylines.length, made.circleMarkers.length, made.markers.length];
    const results = state.results.map((r) => ({ ...r, windFromDeg: 180 }));
    ui.update({ ...state, results });
    expect(made.maps).toHaveLength(1);
    expect([made.polylines.length, made.circleMarkers.length, made.markers.length]).toEqual(counts);
    expect(map().fits).toHaveLength(1);
    expect(arrow().style.transform).toBe("rotate(0deg)");
  });

  it("redraws the overlays (not the map) for a new route, and resets the position", () => {
    const state = makeState();
    ui.update(state);
    ui.setPosition(3);
    const oldGroups = [...made.groups];
    const next = makeState(buildRoute(POINTS.map((p) => ({ ...p, lat: p.lat + 1 }))));
    ui.update(next);
    expect(made.maps).toHaveLength(1);
    expect(made.tileLayers).toHaveLength(1);
    for (const g of oldGroups) expect(map().hasLayer(g)).toBe(false);
    expect(map().fits).toHaveLength(2);
    const { done } = lastLines();
    expect(done.latlngs.every((ll) => ll.lat === 55.4)).toBe(true);
    expect(done.latlngs).toHaveLength(2); // back at the start
  });

  it("redraws cells when they change for the same route (retry)", () => {
    const state = makeState();
    ui.update(state);
    const before = made.circleMarkers.length;
    ui.update({ ...state, cells: state.cells.map((c) => ({ ...c })) });
    expect(made.circleMarkers.length).toBe(before + 3);
    expect(made.maps).toHaveLength(1);
  });

  it("re-measures on resize and re-fits a route fitted while the map had no size", () => {
    const observers = [];
    window.ResizeObserver = class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe(el) { this.el = el; }
      disconnect() { this.disconnected = true; }
    };
    try {
      container.replaceChildren();
      ui = createMapUI(container, { L });
      const [observer] = observers;
      const mapEl = container.querySelector(".route-map");
      expect(observer.el).toBe(mapEl);

      ui.update(makeState()); // panel hidden: jsdom elements measure 0×0
      expect(map().fits).toHaveLength(1);
      observer.callback([]);
      expect(map().invalidated).toBe(0); // still no size

      Object.defineProperty(mapEl, "clientWidth", { value: 800, configurable: true });
      Object.defineProperty(mapEl, "clientHeight", { value: 440, configurable: true });
      observer.callback([]); // shown
      expect(map().invalidated).toBe(1);
      expect(map().fits).toHaveLength(2);
      observer.callback([]); // a later resize keeps the user's view
      expect(map().invalidated).toBe(2);
      expect(map().fits).toHaveLength(2);

      ui.destroy();
      expect(observer.disconnected).toBe(true);
    } finally {
      delete window.ResizeObserver;
    }
  });

  it("without ResizeObserver, catches up on the next render once the map has a size", () => {
    const state = makeState();
    ui.update(state); // fitted at 0×0
    const mapEl = container.querySelector(".route-map");
    Object.defineProperty(mapEl, "clientWidth", { value: 800, configurable: true });
    Object.defineProperty(mapEl, "clientHeight", { value: 440, configurable: true });
    ui.update({ ...state, units: IMPERIAL });
    expect(map().invalidated).toBe(1);
    expect(map().fits).toHaveLength(2);
    ui.update({ ...state, units: METRIC });
    expect(map().fits).toHaveLength(2);
  });

  it("clears the overlays when the route goes away", () => {
    ui.update(makeState());
    ui.update({ route: null, samples: null, cells: null, results: null, units: METRIC });
    const overlays = [...map().layers].filter((l) => !made.tileLayers.includes(l));
    expect(overlays).toEqual([]);
  });

  it("destroy removes the map and empties the panel", () => {
    ui.update(makeState());
    ui.destroy();
    expect(map().removed).toBe(true);
    expect(container.children).toHaveLength(0);
    ui.update(makeState());
    ui.setPosition(1);
    expect(made.maps).toHaveLength(1);
  });
});

// --- with the real Leaflet ---

describe("createMapUI (real Leaflet under jsdom)", () => {
  it("renders the route, cells and wind arrow, and tears down cleanly", () => {
    document.body.innerHTML = '<section id="mapPanel"></section>';
    const container = document.getElementById("mapPanel");
    // No createSVGRect stub: map-view.js passes explicit SVG renderers, so it
    // works even where Leaflet's import-time SVG detection fails.
    const ui = createMapUI(container, { L: realLeaflet });
    const state = makeState();
    ui.update(state);

    const mapEl = container.querySelector(".route-map");
    expect(mapEl.classList.contains("leaflet-container")).toBe(true);
    // Route rest + done, start + finish, 3 connectors + 3 cell dots.
    expect(mapEl.querySelectorAll(".leaflet-overlay-pane path")).toHaveLength(4);
    expect(mapEl.querySelectorAll(".leaflet-rwCells-pane path")).toHaveLength(6);
    expect(mapEl.querySelectorAll(".rw-extreme")).toHaveLength(2);
    const attribution = mapEl.querySelector(".leaflet-control-attribution");
    expect(attribution.textContent).toContain("OpenTopoMap");

    ui.setPosition(state.route.totalDistKm / 2, { windKmh: 12, windFromDeg: 200 });
    const position = mapEl.querySelector(".rw-position .rw-wind");
    expect(position.style.transform).toBe("rotate(20deg)");
    expect(position.classList.contains("wind-breezy")).toBe(true);

    // Same route, imperial: no rebuild, same arrow, explicit wind kept.
    ui.update({ ...state, units: IMPERIAL });
    expect(mapEl.querySelector(".rw-position .rw-wind")).toBe(position);
    expect(position.style.transform).toBe("rotate(20deg)");

    ui.destroy();
    expect(container.children).toHaveLength(0);
  });
});

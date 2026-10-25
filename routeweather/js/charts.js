// Charts along the route: one SVG of four stacked strips sharing a distance x
// axis, with a cursor line at the playback position.
//
//   1. Elevation       the route's profile (area), summits ▲ and valleys ▼ at
//                      the sampled extremes, labelled with their elevation
//                      where the label fits; a note when there is no profile
//   2. Temperature     at your altitude (line), and the model's own 2 m value
//                      at its grid cell's terrain height as a faint dashed
//                      line, so the altitude correction is visible
//   3. Head/tailwind   the wind along your direction of travel (+ in your
//                      face, − behind you) with a zero line, and the gust as
//                      a dashed line (crosswind is in the readout and table)
//   4. Rain            precipitation per hour as one bar per sample, over a
//                      faint step area of the chance of rain (0–100 % of the
//                      strip's height, marked "100%" at the right edge)
//
// Rain and its chance are per-hour buckets taken from the NEARER sample (see
// along-route.js), so both draw as steps that change halfway between samples:
// the bar or step under the cursor is the value in the readout.
//
// Stretches outside the forecast window (values clamped to its nearest hour)
// are greyed across every strip. The rule matches along-route.js: the whole
// span next to an outside sample, up to its neighbouring samples.
//
// `createChartsUI(container, { onSeek, onScrubStart, onScrubEnd })` owns
// `container` (#chartsPanel) and returns:
//
//   update(state)       called on every render; rebuilds only when
//                       state.route, samples, results or units change identity
//                       (or the width changes), and clears without results
//   setCursor(distKm)   moves the one cursor line; cheap, for every frame
//   destroy()
//
// Pointer seeking calls onSeek(distKm). A mouse or pen seeks from pointerdown
// and follows the drag (primary button only). A touch only seeks once the
// finger moves mostly sideways (a vertical swipe is left to scroll the page),
// or on a tap. onScrubStart / onScrubEnd bracket a drag, so the controller can
// pause playback while you scrub.
//
// Width follows the container (ResizeObserver, coalesced to one rebuild per
// animation frame, or window resize where it is missing). Text is never
// measured (no getBBox: jsdom lacks it), so the legend is an HTML list under
// the SVG that wraps on a phone.

import * as d3 from "../../site/vendor/d3.v7.esm.js";
import {
  asUnits, formatElevation, KM_TO_MI, M_TO_FT, MM_TO_IN, UNIT_LABELS,
} from "./units.js";
import { isFiniteNumber as isNum } from "./display.js";
import { timeAtDistance } from "./along-route.js";

const DEFAULT_WIDTH = 640;
const MIN_WIDTH = 260;

// The right margin fits the rain strip's "100%" chance-of-rain mark.
const MARGIN = { top: 4, right: 30, bottom: 28, left: 46 };
// Each strip has a title line above its plot, then a gap below.
const TITLE_H = 16;
const STRIP_GAP = 10;

// Room above the highest point of the profile for a summit's ▲ and its
// label, and below the lowest for a valley's ▼.
const ELEV_HEADROOM_PX = 14;
const ELEV_FOOTROOM_PX = 12;

// Extreme labels: estimated width per character (10 px text; no getBBox),
// and the least gap kept between neighbouring labels.
const LABEL_CHAR_PX = 6;
const LABEL_GAP_PX = 4;

// Profile points drawn at most: the route is binned by distance and each bin
// keeps its lowest and highest point, so peaks survive the thinning.
const PROFILE_BINS = 400;

// Rain axis floor (per rain unit) so a dry forecast doesn't blow drizzle up
// to full height (and the axis still has clean ticks when it is dry
// throughout), and the tick count the strip's height has room for.
const MIN_RAIN_TOP = { mm: 0.5, in: 0.02 };
const RAIN_TICKS = 2;

// Per-instance ids for the outside-forecast hatch pattern.
let instanceCount = 0;

// A touch must travel this far (px) before it counts as a scrub or a scroll.
const TOUCH_SLOP_PX = 8;

// Axis and title unit labels for a units set: distance and elevation follow
// the preset, temperature, wind and rain their own settings.
function unitLabels(units) {
  const imperial = units.system === "imperial";
  return {
    dist: imperial ? "mi" : "km",
    ele: imperial ? "ft" : "m",
    temp: UNIT_LABELS.temp[units.temp],
    wind: UNIT_LABELS.wind[units.wind],
    rain: `${UNIT_LABELS.rain[units.rain]}/h`,
  };
}

// Metric value -> display value, per quantity, for a units set. Anything but
// a finite number (a gap: null, NaN) comes back NaN in either unit, so lines
// and bars skip it rather than plotting null as 0.
function converters(units) {
  const conv = (imperial, toImperial) => (v) => {
    if (!isNum(v)) return NaN;
    return imperial ? toImperial(v) : v;
  };
  const imperial = units.system === "imperial";
  return {
    dist: conv(imperial, (km) => km * KM_TO_MI),
    ele: conv(imperial, (m) => m * M_TO_FT),
    temp: conv(units.temp === "f", (c) => c * 9 / 5 + 32),
    wind: conv(units.wind === "mph", (kmh) => kmh * KM_TO_MI),
    rain: conv(units.rain === "in", (mm) => mm * MM_TO_IN),
  };
}

// The outside-forecast band's legend group (strip legends are in STRIPS).
const OUTSIDE_LEGEND = {
  name: "Grey",
  items: [{ cls: "outside", label: "outside the forecast: nearest hour shown", swatch: "band" }],
};

// --- data ---

// The elevation profile to draw, as [{ km, ele }] in metres: the route's own
// points when the GPX has elevation, else the samples' (filled from the
// elevation API), else none.
export function profilePoints(route, samples) {
  if (route?.hasElevation) {
    const { points, totalDistKm } = route;
    const asProfile = (p) => ({ km: p.cumDistKm, ele: p.ele });
    if (points.length <= PROFILE_BINS * 2) return points.map(asProfile);
    const out = [];
    let bin = -1;
    let lo = null;
    let hi = null;
    const flush = () => {
      if (!lo) return;
      if (lo === hi) out.push(lo);
      else if (lo.km <= hi.km) out.push(lo, hi);
      else out.push(hi, lo);
    };
    for (const p of points) {
      const b = totalDistKm > 0
        ? Math.min(PROFILE_BINS - 1, Math.floor((p.cumDistKm / totalDistKm) * PROFILE_BINS))
        : 0;
      if (b !== bin) {
        flush();
        bin = b;
        lo = null;
        hi = null;
      }
      const pt = asProfile(p);
      if (!lo || pt.ele < lo.ele) lo = pt;
      if (!hi || pt.ele > hi.ele) hi = pt;
    }
    flush();
    return out;
  }
  const withEle = (samples ?? []).filter((s) => isNum(s.ele));
  return withEle.length >= 2 ? withEle.map((s) => ({ km: s.cumDistKm, ele: s.ele })) : [];
}

// The stretches to grey, as [fromKm, toKm]: each run of outside samples out
// to the neighbouring samples either side (the route's ends past the first or
// last sample), where along-route.js interpolates with an outside sample in
// play. Runs that touch are merged.
export function outsideSpans(samples, results, totalKm) {
  const spans = [];
  results.forEach((r, i) => {
    if (!r.outsideForecast) return;
    const from = i === 0 ? 0 : samples[i - 1].cumDistKm;
    const to = i === samples.length - 1 ? totalKm : samples[i + 1].cumDistKm;
    const last = spans[spans.length - 1];
    if (last && from <= last[1]) last[1] = Math.max(last[1], to);
    else spans.push([from, to]);
  });
  return spans;
}

// The stretch each sample's per-hour values stand for: halfway to the
// previous sample to halfway to the next (the route's ends at either end).
function nearestSpan(samples, i, totalKm) {
  const mid = (j) => (samples[i].cumDistKm + samples[j].cumDistKm) / 2;
  return [i === 0 ? 0 : mid(i - 1), i === samples.length - 1 ? totalKm : mid(i + 1)];
}

// A per-sample value as steps, for d3.area/line with a linear curve and
// `defined: isNum(v)`: each sample holds its value over its nearestSpan, so
// the step changes halfway between samples, the nearer-sample rule the
// readout uses. A missing value leaves a gap over exactly its own span
// (curveStep over the raw samples would end the step AT the sample before a
// gap instead). Returns [{ km, v }].
export function stepPoints(samples, values, totalKm) {
  const points = [];
  samples.forEach((_, i) => {
    const [from, to] = nearestSpan(samples, i, totalKm);
    const v = isNum(values[i]) ? values[i] : NaN;
    // A NaN point between two runs breaks the path there.
    points.push({ km: from, v }, { km: to, v });
  });
  return points;
}

// A y domain over `values`, always including `include`, at least `minSpan`
// tall (centred on the data when it is flatter).
function yDomain(values, include = [], minSpan = 1) {
  const finite = [...values, ...include].filter(isNum);
  if (finite.length === 0) return [0, minSpan];
  let [lo, hi] = d3.extent(finite);
  if (hi - lo < minSpan) {
    const mid = (lo + hi) / 2;
    lo = mid - minSpan / 2;
    hi = mid + minSpan / 2;
  }
  return [lo, hi];
}

// --- strips ---
//
// Each strip is { key, name, height, legend, draw(ctx, h) }, drawn top to bottom in
// STRIPS order. draw() gets the shared context below and the strip's plot
// height, and calls ctx.frame(title, y[, axis]) for its group (title, y axis,
// translated to the strip's top). `legend` is the strip's legend group, or
// null when one series is named by the title. Adding a strip is one more
// function and entry.
//
// ctx: { route, samples, rows ([{ km, s, r }] per sample), units (a units
// set), unit (labels), cv (converters), xk (km -> x px), left, right, totalKm,
// line(y, get) (a d3 line skipping gaps), stepArea(y) (a d3 area over
// stepPoints, down to the strip's bottom), timeAt(km) (arrival epoch ms) }.

function drawElevation(ctx, h) {
  const { route, samples, units, unit, cv, xk, left, right } = ctx;
  const profile = profilePoints(route, samples);
  const y = d3.scaleLinear().domain(yDomain(profile.map((p) => cv.ele(p.ele)), [], 20))
    .nice().range([h - ELEV_FOOTROOM_PX, ELEV_HEADROOM_PX]);
  const g = ctx.frame(`Elevation (${unit.ele})`, y);
  if (profile.length === 0) {
    g.append("text").attr("class", "chart-note")
      .attr("x", (left + right) / 2).attr("y", h / 2)
      .attr("text-anchor", "middle").text("No elevation data for this route");
    return;
  }
  const yAt = (p) => y(cv.ele(p.ele));
  g.append("path").attr("class", "elev-area")
    .attr("d", d3.area().x((p) => xk(p.km)).y0(h).y1(yAt)(profile));
  g.append("path").attr("class", "elev-line")
    .attr("d", d3.line().x((p) => xk(p.km)).y(yAt)(profile));

  const extremes = samples.filter(
    (s) => (s.kind === "peak" || s.kind === "valley") && isNum(s.ele),
  );
  const at = (s) => [xk(s.cumDistKm), y(cv.ele(s.ele))];
  g.append("g").attr("class", "extremes").selectAll("path").data(extremes).join("path")
    .attr("class", (s) => `extreme ${s.kind}`)
    .attr("transform", (s) => `translate(${at(s).join(",")})`)
    // Summit ▲ sits above the point, valley ▼ below.
    .attr("d", (s) => (s.kind === "peak" ? "M0,-3 L4,-10 L-4,-10 Z" : "M0,3 L4,10 L-4,10 Z"));
  g.append("g").attr("class", "extreme-labels").selectAll("text")
    .data(extremeLabels(extremes, at, units, right)).join("text")
    .attr("class", (d) => `extreme-label ${d.kind}`)
    .attr("x", (d) => d.x).attr("y", (d) => d.y).text((d) => d.text);
}

// Labels for the extremes ("978 m"), just right of each marker, level with
// it. Walking along the route, a label is skipped when it would overlap the
// previous one kept or run past the plot's right edge; widths are estimated
// from the character count.
export function extremeLabels(extremes, at, units, right) {
  const labels = [];
  let lastEnd = -Infinity;
  for (const s of extremes) {
    const [px, py] = at(s);
    const text = formatElevation(s.ele, units);
    const x = px + 6;
    const end = x + text.length * LABEL_CHAR_PX;
    if (x < lastEnd + LABEL_GAP_PX || end > right) continue;
    lastEnd = end;
    labels.push({ kind: s.kind, text, x, y: s.kind === "peak" ? py - 3 : py + 11 });
  }
  return labels;
}

function drawTemperature(ctx, h) {
  const { rows, unit, cv, line } = ctx;
  const at = (d) => cv.temp(d.r.tempC);
  const model = (d) => cv.temp(d.r.model2mTempC);
  const y = d3.scaleLinear().domain(yDomain([...rows.map(at), ...rows.map(model)], [], 4))
    .nice().range([h, 0]);
  const g = ctx.frame(`Temperature (${unit.temp})`, y);
  g.append("path").attr("class", "series s-temp-model").attr("d", line(y, model)(rows));
  g.append("path").attr("class", "series s-temp").attr("d", line(y, at)(rows));
}

function drawWind(ctx, h) {
  const { rows, unit, cv, line, left, right } = ctx;
  const head = (d) => cv.wind(d.r.headKmh);
  const gust = (d) => cv.wind(d.r.gustKmh);
  const y = d3.scaleLinear()
    .domain(yDomain([...rows.map(head), ...rows.map(gust)], [0], 10))
    .nice().range([h, 0]);
  const g = ctx.frame(`Head/tailwind (${unit.wind})`, y);
  g.append("line").attr("class", "zero-line").attr("x1", left)
    .attr("x2", right).attr("y1", y(0)).attr("y2", y(0));
  g.append("path").attr("class", "series s-gust").attr("d", line(y, gust)(rows));
  g.append("path").attr("class", "series s-head").attr("d", line(y, head)(rows));
}

// Rain: the amount per hour as one bar per sample (left axis), over the
// stretch that sample is the nearer one for, with a 2 px gap between
// neighbours; nothing where it is 0 or missing. Behind the bars, the chance
// of rain as a faint step area (stepPoints: it changes halfway between
// samples, the nearer-sample rule again) scaled 0–100 % to the strip's full
// height, with a single "100%" mark at the right edge rather than a second
// axis. The user asked for both in one strip; the shaded area and the solid
// bars are deliberately different encodings so the two don't read as one
// scale.
function drawRain(ctx, h) {
  const { rows, samples, units, unit, cv, xk, totalKm, right } = ctx;
  const rain = (d) => cv.rain(d.r.precipMm);
  const minTop = MIN_RAIN_TOP[units.rain];
  const y = d3.scaleLinear().domain([0, Math.max(minTop, d3.max(rows, rain) ?? 0)]).nice()
    .range([h, 0]);
  const axis = d3.axisLeft(y).ticks(RAIN_TICKS).tickSizeOuter(0);
  const g = ctx.frame(`Rain (${unit.rain})`, y, axis);

  const yProb = d3.scaleLinear().domain([0, 100]).range([h, 0]);
  const steps = stepPoints(samples, rows.map((d) => d.r.precipProb), totalKm);
  g.append("path").attr("class", "prob-area").attr("d", ctx.stepArea(yProb)(steps));
  g.append("text").attr("class", "prob-max").attr("x", right + 3).attr("y", 4)
    .attr("dominant-baseline", "middle").text("100%");

  const bars = rows.map((d, i) => {
    const [from, to] = nearestSpan(samples, i, totalKm);
    return { d, x0: xk(from), x1: xk(to) };
  }).filter((b) => isNum(rain(b.d)) && rain(b.d) > 0);
  g.append("g").attr("class", "rain-bars").selectAll("rect").data(bars).join("rect")
    .attr("class", "s-rain")
    .attr("x", (b) => b.x0 + 1).attr("width", (b) => Math.max(1, b.x1 - b.x0 - 2))
    .attr("y", (b) => y(rain(b.d))).attr("height", (b) => h - y(rain(b.d)));
}

// Legend groups are named by their strip, so a colour never means two things
// in one group.
const STRIPS = [
  {
    key: "elev",
    name: "elevation",
    height: 84,
    draw: drawElevation,
    legend: {
      name: "Elevation",
      items: [
        { cls: "peak", label: "summit", swatch: "peak" },
        { cls: "valley", label: "valley", swatch: "valley" },
      ],
    },
  },
  {
    key: "temp",
    name: "temperature",
    height: 70,
    draw: drawTemperature,
    legend: {
      name: "Temperature",
      items: [
        { cls: "temp", label: "at your altitude" },
        { cls: "temp-model", label: "model 2 m (grid cell)" },
      ],
    },
  },
  {
    key: "wind",
    name: "head/tailwind",
    height: 80,
    draw: drawWind,
    legend: {
      name: "Head/tailwind",
      items: [
        { cls: "head", label: "headwind + / tailwind −" },
        { cls: "gust", label: "gust" },
      ],
    },
  },
  {
    key: "rain",
    name: "rain (amount and chance)",
    height: 76,
    draw: drawRain,
    legend: {
      name: "Rain",
      items: [
        { cls: "rain", label: "rain per hour", swatch: "bar" },
        { cls: "prob", label: "chance of rain", swatch: "area" },
      ],
    },
  },
];

// "Elevation, temperature, head/tailwind and rain (amount and chance)": the
// start of the SVG's accessible name, from STRIPS.
const CHART_LABEL = (() => {
  const names = STRIPS.map((strip) => strip.name);
  const text = names.length > 1
    ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
    : names[0];
  return text.charAt(0).toUpperCase() + text.slice(1);
})();

// --- factory ---

export function createChartsUI(container, {
  onSeek = () => {},
  onScrubStart = () => {},
  onScrubEnd = () => {},
  raf,
  caf,
} = {}) {
  const doc = container.ownerDocument;
  const win = doc.defaultView;
  const hatchId = `rwOutsideHatch${++instanceCount}`;
  // Resize rebuilds wait for the next frame; synchronous where there are none.
  const requestFrame = raf ?? (win?.requestAnimationFrame
    ? (cb) => win.requestAnimationFrame(cb)
    : (cb) => { cb(); return null; });
  const cancelFrame = caf ?? ((id) => win?.cancelAnimationFrame?.(id));

  const svgNode = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
  svgNode.setAttribute("class", "route-charts");
  svgNode.setAttribute("role", "img");
  svgNode.setAttribute(
    "aria-label",
    `${CHART_LABEL} along the route. The weather points table has the values.`,
  );
  const legend = doc.createElement("ul");
  legend.className = "chart-legend";
  container.replaceChildren(svgNode, legend);
  const svg = d3.select(svgNode);

  // Last drawn inputs (identity), the width, and the scale for seeking.
  let drawn = { route: null, samples: null, results: null, units: null };
  let drawnState = null;
  let width = 0;
  let built = false;
  let x = null; // display-distance scale
  let toDisp = null; // km -> display distance
  let totalKm = 0;
  let cursorKm = 0;
  let cursorNode = null;
  let hitNode = null;
  let cursorX = null;
  let destroyed = false;
  let resizeFrame = null;

  // The container's content width (clientWidth includes its padding).
  function measure() {
    const style = win?.getComputedStyle?.(container);
    const px = (v) => parseFloat(v) || 0;
    const padding = style ? px(style.paddingLeft) + px(style.paddingRight) : 0;
    const inner = Math.round(container.clientWidth - padding);
    return inner > 0 ? Math.max(MIN_WIDTH, inner) : DEFAULT_WIDTH;
  }

  // --- build ---

  function clear() {
    svg.selectAll("*").remove();
    legend.replaceChildren();
    built = false;
    cursorNode = null;
    hitNode = null;
    cursorX = null;
  }

  function buildLegend(hasOutside) {
    const groups = STRIPS.map((strip) => strip.legend).filter(Boolean);
    if (hasOutside) groups.push(OUTSIDE_LEGEND);
    legend.replaceChildren(...groups.map(({ name, items }) => {
      const li = doc.createElement("li");
      const title = doc.createElement("span");
      title.className = "legend-strip";
      title.textContent = name;
      li.append(title);
      for (const { cls, label, swatch = "line" } of items) {
        const item = doc.createElement("span");
        item.className = "legend-item";
        const mark = doc.createElement("span");
        mark.className = `swatch swatch-${swatch} s-${cls}`;
        mark.setAttribute("aria-hidden", "true");
        item.append(mark, doc.createTextNode(label));
        li.append(item);
      }
      return li;
    }));
  }

  // Strip frame: title and y axis in a group translated to the strip's top.
  function frame(key, top, title, y, axis) {
    const g = svg.append("g").attr("class", `strip strip-${key}`)
      .attr("transform", `translate(0,${top})`);
    g.append("text").attr("class", "strip-title").attr("x", MARGIN.left - 40).attr("y", -5)
      .text(title);
    g.append("g").attr("class", "axis axis-y").attr("transform", `translate(${MARGIN.left},0)`)
      .call(axis ?? d3.axisLeft(y).ticks(3).tickSizeOuter(0));
    return g;
  }

  function build(state) {
    clear();
    const { route, samples, results } = state;
    // A units set or, from older callers, a system string.
    const units = asUnits(state.units);
    const unit = unitLabels(units);
    const cv = converters(units);
    width = measure();
    totalKm = route.totalDistKm;
    toDisp = cv.dist;
    const right = width - MARGIN.right;
    x = d3.scaleLinear().domain([0, toDisp(totalKm)]).range([MARGIN.left, right]);
    const xk = (km) => x(toDisp(km));

    let top = MARGIN.top;
    const tops = STRIPS.map((s) => {
      const t = top + TITLE_H;
      top = t + s.height + STRIP_GAP;
      return t;
    });
    const plotTop = tops[0];
    const plotBottom = tops[tops.length - 1] + STRIPS[STRIPS.length - 1].height;
    const height = plotBottom + MARGIN.bottom;
    svg.attr("width", width).attr("height", height).attr("viewBox", `0 0 ${width} ${height}`);

    // Distance gridlines behind everything, then the outside-forecast bands.
    const ticks = x.ticks(Math.max(2, Math.floor((right - MARGIN.left) / 70)));
    svg.append("g").attr("class", "grid").selectAll("line").data(ticks).join("line")
      .attr("x1", (d) => x(d)).attr("x2", (d) => x(d)).attr("y1", plotTop).attr("y2", plotBottom);

    // Hatched, so the band reads as a region even over the grey profile.
    const hatch = svg.append("defs").append("pattern").attr("id", hatchId)
      .attr("class", "outside-hatch").attr("patternUnits", "userSpaceOnUse")
      .attr("width", 6).attr("height", 6).attr("patternTransform", "rotate(45)");
    hatch.append("rect").attr("width", 6).attr("height", 6);
    hatch.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6);
    const spans = outsideSpans(samples, results, totalKm);
    svg.append("g").attr("class", "outside-bands").selectAll("rect").data(spans).join("rect")
      .attr("x", (d) => xk(d[0])).attr("width", (d) => Math.max(1, xk(d[1]) - xk(d[0])))
      .attr("y", plotTop).attr("height", plotBottom - plotTop).attr("fill", `url(#${hatchId})`);

    const rows = samples.map((s, i) => ({ km: s.cumDistKm, s, r: results[i] }));
    const line = (y, get) => d3.line().defined((d) => isNum(get(d)))
      .x((d) => xk(d.km)).y((d) => y(get(d)));
    const stepArea = (y) => d3.area().defined((d) => isNum(d.v))
      .x((d) => xk(d.km)).y0((d) => y.range()[0]).y1((d) => y(d.v));
    const ctx = {
      route, samples, rows, units, unit, cv, xk, left: MARGIN.left, right, totalKm, line,
      stepArea, timeAt: (km) => timeAtDistance(state, km),
    };
    STRIPS.forEach((strip, i) => {
      strip.draw({ ...ctx, frame: (...args) => frame(strip.key, tops[i], ...args) }, strip.height);
    });

    // Shared distance axis under the last strip.
    svg.append("g").attr("class", "axis axis-x").attr("transform", `translate(0,${plotBottom})`)
      .call(d3.axisBottom(x).tickValues(ticks).tickSizeOuter(0));
    svg.append("text").attr("class", "axis-label").attr("x", right)
      .attr("y", plotBottom + MARGIN.bottom - 2).attr("text-anchor", "end")
      .text(`Distance (${unit.dist})`);

    // Cursor on top, then the hit area for pointer seeking.
    cursorNode = svg.append("line").attr("class", "chart-cursor")
      .attr("y1", plotTop).attr("y2", plotBottom).node();
    hitNode = svg.append("rect").attr("class", "chart-hit").attr("x", MARGIN.left)
      .attr("y", plotTop).attr("width", right - MARGIN.left).attr("height", plotBottom - plotTop)
      .node();

    buildLegend(spans.length > 0);
    built = true;
    setCursor(cursorKm);
  }

  // --- cursor ---

  function setCursor(distKm) {
    cursorKm = Math.min(Math.max(Number(distKm) || 0, 0), totalKm || Infinity);
    if (!built || !cursorNode) return;
    const cx = String(Math.round(x(toDisp(Math.min(cursorKm, totalKm))) * 10) / 10);
    if (cx === cursorX) return;
    cursorX = cx;
    cursorNode.setAttribute("x1", cx);
    cursorNode.setAttribute("x2", cx);
  }

  // --- pointer ---

  // Pointer x -> km along the route. The SVG is drawn at its measured width,
  // but scale by the rendered box anyway in case CSS squeezes it.
  function kmAt(event) {
    const box = svgNode.getBoundingClientRect();
    const scale = box.width > 0 ? width / box.width : 1;
    const px = (event.clientX - box.left) * scale;
    const disp = x.invert(Math.min(Math.max(px, MARGIN.left), width - MARGIN.right));
    return Math.min(Math.max(disp / toDisp(1), 0), totalKm);
  }

  // The pointer gesture in progress: { id, touch, x0, y0, scrubbing }.
  let gesture = null;
  const ours = (event) => gesture && event.pointerId === gesture.id;

  function startScrub(event) {
    gesture.scrubbing = true;
    if (event.pointerId !== undefined) svgNode.setPointerCapture?.(event.pointerId);
    onScrubStart();
    onSeek(kmAt(event));
  }

  function endGesture(event, cancelled) {
    if (!ours(event)) return;
    const { scrubbing, touch } = gesture;
    gesture = null;
    if (scrubbing) onScrubEnd();
    // A tap: a touch that neither scrubbed nor turned into a scroll.
    else if (touch && !cancelled && built) onSeek(kmAt(event));
  }

  svgNode.addEventListener("pointerdown", (event) => {
    // Only from the plot area (the hit rect over it), not titles or axes.
    if (!built || gesture || event.target !== hitNode) return;
    const touch = event.pointerType === "touch";
    // Primary mouse button / pen contact only.
    if (!touch && event.button > 0) return;
    gesture = {
      id: event.pointerId, touch, x0: event.clientX, y0: event.clientY, scrubbing: false,
    };
    if (!touch) startScrub(event);
  });
  svgNode.addEventListener("pointermove", (event) => {
    if (!ours(event) || !built) return;
    if (gesture.scrubbing) {
      onSeek(kmAt(event));
      return;
    }
    const dx = Math.abs(event.clientX - gesture.x0);
    const dy = Math.abs(event.clientY - gesture.y0);
    if (dx >= TOUCH_SLOP_PX && dx > dy) startScrub(event);
    else if (dy >= TOUCH_SLOP_PX) gesture = null; // a scroll, not ours
  });
  svgNode.addEventListener("pointerup", (event) => endGesture(event, false));
  svgNode.addEventListener("pointercancel", (event) => endGesture(event, true));

  // --- update / resize ---

  function update(state) {
    if (destroyed) return;
    const same = state.route === drawn.route && state.samples === drawn.samples
      && state.results === drawn.results && state.units === drawn.units;
    if (same) return;
    if (state.route !== drawn.route) cursorKm = 0;
    const { route, samples, results, units } = state;
    drawn = { route, samples, results, units };
    // The whole state, for rebuilding on resize (timeAt needs start/speed).
    drawnState = state;
    if (!route || !samples || !results) {
      clear();
      return;
    }
    build(state);
  }

  function onResize() {
    if (destroyed || resizeFrame !== null) return;
    resizeFrame = requestFrame(() => {
      resizeFrame = null;
      if (destroyed || !built || measure() === width) return;
      build(drawnState);
    });
  }

  const resizeObserver = win?.ResizeObserver ? new win.ResizeObserver(onResize) : null;
  if (resizeObserver) resizeObserver.observe(container);
  else win?.addEventListener("resize", onResize);

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    if (resizeFrame !== null) cancelFrame(resizeFrame);
    resizeObserver?.disconnect();
    if (!resizeObserver) win?.removeEventListener("resize", onResize);
    container.replaceChildren();
  }

  return { update, setCursor, destroy };
}

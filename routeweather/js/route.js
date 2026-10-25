// Route geometry and weather-sample selection. Pure maths, no DOM: takes the
// `{ lat, lon, ele }` points from gpx.js (or anywhere else) and works out
// distances, bearings, climb, a smoothed elevation profile, which points get a
// weather lookup, arrival times, and interpolated positions for playback.
//
// Conventions: distances are km, elevations metres, bearings degrees clockwise
// from true north in [0, 360). A raw point's `ele` is `undefined` when the GPX
// had none; values this module COMPUTES that are unknown (ascent, smoothed,
// interpolated and sample elevation) are `null`.

// Mean Earth radius (IUGG), the usual value for haversine.
export const EARTH_RADIUS_KM = 6371;

// Weather samples closer together than this along the route are merged.
export const MERGE_DISTANCE_KM = 0.2;

// Default elevation smoothing window (m), also used by sampleRoute.
export const SMOOTHING_WINDOW_M = 100;

// Slack (1 µm) on inclusive along-route distance edges, so a point exactly at
// the edge isn't in on one side and out on the other through float error in
// cumDistKm, which would skew a symmetric window by a vertex.
const EDGE_EPSILON_KM = 1e-9;

// Merge priority when two samples are within MERGE_DISTANCE_KM: lower wins.
const KIND_PRIORITY = { start: 0, end: 0, peak: 1, valley: 1, filler: 2 };

// Tie-break for samples at the same distance (e.g. a zero-length route): start
// sorts first, end last.
const KIND_ORDER = { start: 0, peak: 1, valley: 1, filler: 1, end: 2 };

const byDistance = (a, b) => a.cumDistKm - b.cumDistKm || KIND_ORDER[a.kind] - KIND_ORDER[b.kind];

// Wraps a longitude (or longitude difference) into [-180, 180).
const wrapLon = (lon) => ((((lon + 180) % 360) + 360) % 360) - 180;

const toRad = (deg) => (deg * Math.PI) / 180;
const toDeg = (rad) => (rad * 180) / Math.PI;

// --- geometry ---

// Great-circle distance in km between two `{ lat, lon }` points (haversine).
export function haversineKm(a, b) {
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  // min() guards against h creeping past 1 through rounding (antipodal points).
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

// Initial compass bearing (forward azimuth) from `a` to `b`, in [0, 360).
// Identical points give 0.
export function bearingDeg(a, b) {
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const dLon = toRad(b.lon - a.lon);
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

// True only when EVERY point has a finite elevation. Partial elevation data
// counts as none: gaps would make ascent/descent and the elevation profile
// unreliable, and guessing across them is worse than showing nothing. gpx.js
// uses this too, so both modules agree on what "has elevation" means.
export function hasFullElevation(points) {
  return points.length > 0 && points.every((p) => Number.isFinite(p.ele));
}

// --- route building ---

// Builds a route object from `{ lat, lon, ele }` points:
//
//   points        copies of the inputs plus `cumDistKm` (distance from the
//                 start) and `bearingDeg` (heading towards the next point)
//   totalDistKm   route length
//   hasElevation  see hasFullElevation (recomputed, never trusted from input)
//   ascentM       total climb, summed over raw consecutive deltas; `null`
//   descentM      when the route has no elevation (positive number, or `null`)
//
// Bearing rules: a point's bearing points at the next point at a DIFFERENT
// position, so duplicate consecutive points (common in GPX) don't flash a
// meaningless 0. The last point (and any trailing duplicates) repeat the final
// segment's bearing. A route with no distinct positions has `null` bearings.
//
// Throws on an empty point list.
export function buildRoute(points) {
  if (!Array.isArray(points) || points.length === 0) {
    throw new Error("Route has no points");
  }
  const n = points.length;

  let cumDistKm = 0;
  const enriched = points.map((p, i) => {
    if (i > 0) cumDistKm += haversineKm(points[i - 1], p);
    return { ...p, cumDistKm, bearingDeg: null };
  });

  // Each segment's own bearing, null where it has zero length...
  for (let i = 0; i < n - 1; i++) {
    if (enriched[i + 1].cumDistKm > enriched[i].cumDistKm) {
      enriched[i].bearingDeg = bearingDeg(points[i], points[i + 1]);
    }
  }
  // ...then fill zero-length gaps from the next real segment (the duplicate is
  // at the same spot, so it heads the same way), and the tail from behind.
  for (let i = n - 2; i >= 0; i--) {
    if (enriched[i].bearingDeg === null) enriched[i].bearingDeg = enriched[i + 1].bearingDeg;
  }
  for (let i = 1; i < n; i++) {
    if (enriched[i].bearingDeg === null) enriched[i].bearingDeg = enriched[i - 1].bearingDeg;
  }

  const hasElevation = hasFullElevation(points);
  let ascentM = null;
  let descentM = null;
  if (hasElevation) {
    ascentM = 0;
    descentM = 0;
    for (let i = 1; i < n; i++) {
      const delta = points[i].ele - points[i - 1].ele;
      if (delta > 0) ascentM += delta;
      else descentM -= delta;
    }
  }

  return { points: enriched, totalDistKm: cumDistKm, hasElevation, ascentM, descentM };
}

// --- elevation ---

// Elevation profile smoothed over a sliding DISTANCE window (GPX spacing
// varies, so a fixed point count would smooth unevenly). Each point becomes the
// distance-weighted mean of every point whose `cumDistKm` is within
// `windowM / 2` metres of its own (inclusive).
//
// Weights are trapezoid-style: each point stands for half the distance to each
// neighbour. A cluster of stationary duplicates (a café stop) therefore weighs
// the same as one point instead of swamping the window. The window always
// contains the point itself, so where points are sparser than `windowM / 2`
// the raw value passes through unchanged. If a window's weights sum to zero
// (every point at one spot), it falls back to the plain mean.
//
// Returns an array parallel to `route.points`, or all `null`s when the route
// has no elevation.
export function smoothElevation(route, windowM = SMOOTHING_WINDOW_M) {
  const { points } = route;
  if (!route.hasElevation) return points.map(() => null);
  const n = points.length;

  const halfKm = windowM / 2000 + EDGE_EPSILON_KM;

  // Prefix sums make each window's mean O(1).
  const sumW = [0];
  const sumWE = [0];
  const sumE = [0];
  points.forEach((p, i) => {
    const prevKm = points[Math.max(0, i - 1)].cumDistKm;
    const nextKm = points[Math.min(n - 1, i + 1)].cumDistKm;
    const w = (nextKm - prevKm) / 2;
    sumW.push(sumW[i] + w);
    sumWE.push(sumWE[i] + w * p.ele);
    sumE.push(sumE[i] + p.ele);
  });

  // Two pointers track the window's edges; cumDistKm is non-decreasing, so
  // both only ever move forward.
  let lo = 0;
  let hi = 0;
  return points.map((p) => {
    while (points[lo].cumDistKm < p.cumDistKm - halfKm) lo++;
    while (hi + 1 < n && points[hi + 1].cumDistKm <= p.cumDistKm + halfKm) hi++;
    const w = sumW[hi + 1] - sumW[lo];
    if (w > 0) return (sumWE[hi + 1] - sumWE[lo]) / w;
    return (sumE[hi + 1] - sumE[lo]) / (hi - lo + 1);
  });
}

// --- sampling ---

// Picks the places along the route that get a weather lookup. Returns
// samples sorted by distance, each:
//
//   { index, lat, lon, ele, cumDistKm, bearingDeg, kind }
//
//   kind        "start" | "end" | "peak" | "valley" | "filler"
//   index       the GPX vertex in `route.points` for start/end/peak/valley;
//               `null` for fillers, which sit at an interpolated position
//               (see positionAtDistance) so that widely spaced points (route
//               planners export <rtept>s tens of km apart) still get coverage
//   ele         metres (interpolated for fillers), or `null` without elevation
//   bearingDeg  direction of travel there, so consumers never need `index`
//
// Selection:
//   1. Start and end, always.
//   2. Peaks/valleys of the smoothed profile with prominence >= prominenceM
//      (see isProminent), each snapped to the highest/lowest RAW point within
//      half a smoothing window — smoothing drifts an extreme towards its
//      gentler side, and the real summit is the point worth sampling.
//   3. Fillers spaced evenly by distance between consecutive points from 1-2,
//      no closer than `minSpacingKm`. On a long route the spacing widens until
//      the fillers roughly fill the `maxSamples` budget (see fillerSpacingKm).
//   4. Samples within MERGE_DISTANCE_KM (along the route) of a higher-priority
//      one are dropped: start/end beat extremes, extremes beat fillers. A loop's
//      start and end are both kept — same place, different times.
//   5. If over `maxSamples`, start/end stay, then the route's highest peak and
//      lowest valley, then the other extremes and finally the fillers, each
//      group thinned evenly (never truncated, so the end survives).
//      `maxSamples` below 2 is treated as 2.
export function sampleRoute(route, options = {}) {
  const { maxSamples = 150, minSpacingKm = 1, prominenceM = 30 } = options;
  const { points } = route;
  const last = points.length - 1;
  const budget = Math.max(2, maxSamples);
  const vertexSample = (index, kind) => ({
    ...positionOf(route, points[index]),
    index,
    cumDistKm: points[index].cumDistKm,
    kind,
  });

  const snapKm = SMOOTHING_WINDOW_M / 2000 + EDGE_EPSILON_KM;
  const extremes = findExtremes(smoothElevation(route), prominenceM)
    .map((extreme) => snapToRawExtreme(points, extreme, snapKm));
  const anchors = [
    vertexSample(0, "start"),
    ...extremes.map(({ index, kind }) => vertexSample(index, kind)),
  ];
  if (last > 0) anchors.push(vertexSample(last, "end"));
  anchors.sort(byDistance);

  // Each gap between consecutive anchors is split into equal segments.
  const gaps = anchors.slice(1).map((anchor, k) => ({
    fromKm: anchors[k].cumDistKm,
    gapKm: anchor.cumDistKm - anchors[k].cumDistKm,
  }));
  const gapsKm = gaps.map((g) => g.gapKm);
  const spacingKm = fillerSpacingKm(gapsKm, minSpacingKm, budget - anchors.length);
  const fillers = [];
  for (const { fromKm, gapKm } of gaps) {
    const segments = segmentCount(gapKm, spacingKm);
    for (let s = 1; s < segments; s++) {
      const cumDistKm = fromKm + (gapKm * s) / segments;
      const position = positionAtDistance(route, cumDistKm);
      fillers.push({ ...position, index: null, cumDistKm, kind: "filler" });
    }
  }

  // The final map just fixes every sample's key order.
  return capSamples(mergeClose([...anchors, ...fillers]), budget)
    .sort(byDistance)
    .map(({ index, lat, lon, ele, cumDistKm, bearingDeg, kind }) => ({
      index, lat, lon, ele, cumDistKm, bearingDeg, kind,
    }));
}

// Equal segments, none shorter than `spacingKm`, that a gap splits into. The
// epsilon stops float error (a 10 km gap measuring 9.9999...) costing one.
const segmentCount = (gapKm, spacingKm) => Math.floor(gapKm / spacingKm + 1e-9);

const fillerCount = (gapsKm, spacingKm) =>
  gapsKm.reduce((sum, gapKm) => sum + Math.max(0, segmentCount(gapKm, spacingKm) - 1), 0);

// Filler spacing for a budget of `room` fillers. If minSpacingKm fits, use it.
// Otherwise binary-search (filler count only falls as spacing grows) for the
// WIDEST spacing that still yields at least `room`, and let capSamples thin the
// small excess evenly. Aiming to fit exactly instead would waste budget: with
// many short gaps the count jumps in big steps (one filler per gap -> none),
// and a one-shot total/room estimate loses up to a spacing per gap to rounding.
function fillerSpacingKm(gapsKm, minSpacingKm, room) {
  if (room <= 0) return Infinity;
  if (fillerCount(gapsKm, minSpacingKm) <= room) return minSpacingKm;
  let lo = minSpacingKm; // yields more than `room`
  let hi = Math.max(...gapsKm); // yields none
  for (let i = 0; i < 50; i++) {
    const mid = (lo + hi) / 2;
    if (fillerCount(gapsKm, mid) >= room) lo = mid;
    else hi = mid;
  }
  return lo;
}

// Interior local maxima ("peak") and minima ("valley") of the smoothed profile
// that pass isProminent. Plateaus count once, at their middle point. Returns
// `{ index, kind }`; empty when the profile is all null (no elevation).
function findExtremes(smoothed, prominenceM) {
  const extremes = [];
  if (smoothed.length < 3 || smoothed[0] === null) return extremes;
  // Quantise to 1 µm: the smoothed means come from prefix-sum differences, so
  // equal raw elevations at uneven spacing differ by ~1e-11 and a flat top
  // would otherwise split into noise extremes instead of one plateau.
  const profile = smoothed.map((v) => Math.round(v * 1e6) / 1e6);
  const negated = profile.map((v) => -v);

  let i = 1;
  while (i < profile.length - 1) {
    // [i, j] is a run of equal values; only interior runs can be extremes.
    let j = i;
    while (j + 1 < profile.length && profile[j + 1] === profile[i]) j++;
    if (j + 1 < profile.length) {
      const before = profile[i - 1];
      const after = profile[j + 1];
      const value = profile[i];
      const mid = Math.floor((i + j) / 2);
      if (value > before && value > after && isProminent(profile, i, j, prominenceM)) {
        extremes.push({ index: mid, kind: "peak" });
      } else if (value < before && value < after && isProminent(negated, i, j, prominenceM)) {
        extremes.push({ index: mid, kind: "valley" });
      }
    }
    i = j + 1;
  }
  return extremes;
}

// Moves a smoothed extreme to the highest (peak) or lowest (valley) raw point
// within `halfKm` of it along the route (inclusive).
function snapToRawExtreme(points, extreme, halfKm) {
  const sign = extreme.kind === "peak" ? 1 : -1;
  const centreKm = points[extreme.index].cumDistKm;
  let best = extreme.index;
  const consider = (k) => {
    if (sign * points[k].ele > sign * points[best].ele) best = k;
  };
  const inReach = (k) => k >= 0 && k < points.length
    && Math.abs(points[k].cumDistKm - centreKm) <= halfKm;
  for (let k = extreme.index - 1; inReach(k); k--) consider(k);
  for (let k = extreme.index + 1; inReach(k); k++) consider(k);
  return { ...extreme, index: best };
}

// Simplified prominence along the 1-D profile, as a yes/no test for the peak
// occupying [i, j] (valleys are tested on the negated profile).
//
// Walk outwards on each side until the profile has dropped `prominenceM` below
// the peak (the side PASSES), reaches strictly higher ground, or reaches the
// route's end (either way it FAILS). The peak counts only when both sides pass,
// i.e. it stands at least `prominenceM` above the lowest ground between it and
// anything higher (or the route's end) on each side. A summit that the route
// leaves by less than `prominenceM` before finishing is therefore not a "peak"
// sample; the end sample, within `prominenceM` of it, covers it. (Treating the
// route's end as unconstraining instead made every float-level dip in a flat
// run beside a big climb a "valley".) Walks stop early, so cost stays
// near-linear on real profiles.
function isProminent(profile, i, j, prominenceM) {
  const peak = profile[i];
  const floor = peak - prominenceM;
  const passes = (start, step) => {
    for (let k = start; k >= 0 && k < profile.length; k += step) {
      if (profile[k] <= floor) return true;
      if (profile[k] > peak) return false;
    }
    return false;
  };
  return passes(i - 1, -1) && passes(j + 1, 1);
}

// Keeps candidates in priority order, dropping any within MERGE_DISTANCE_KM of
// one already kept (which also removes an extreme snapped onto another).
// Start and end are always kept, even on a route shorter than the merge
// distance.
function mergeClose(candidates) {
  const ordered = [...candidates].sort(
    (a, b) => KIND_PRIORITY[a.kind] - KIND_PRIORITY[b.kind] || a.cumDistKm - b.cumDistKm,
  );
  const kept = [];
  for (const candidate of ordered) {
    const isEndpoint = KIND_PRIORITY[candidate.kind] === 0;
    const tooClose = kept.some(
      (k) => Math.abs(k.cumDistKm - candidate.cumDistKm) < MERGE_DISTANCE_KM,
    );
    if (isEndpoint || !tooClose) kept.push(candidate);
  }
  return kept;
}

// Fits samples to `budget`: start/end always, then the highest peak and lowest
// valley (even thinning alone could drop the route's summit), then as many
// other extremes as fit, then fillers — each group thinned evenly along the
// route if it overflows.
function capSamples(samples, budget) {
  if (samples.length <= budget) return samples;
  const endpoints = samples.filter((s) => KIND_PRIORITY[s.kind] === 0);
  const extremes = samples.filter((s) => KIND_PRIORITY[s.kind] === 1).sort(byDistance);
  const fillers = samples.filter((s) => KIND_PRIORITY[s.kind] === 2).sort(byDistance);

  const room = budget - endpoints.length;
  const key = keyExtremes(extremes).slice(0, Math.max(0, room));
  const others = extremes.filter((e) => !key.includes(e));
  const keptExtremes = [...key, ...thinEvenly(others, room - key.length)];
  const keptFillers = thinEvenly(fillers, room - keptExtremes.length);
  return [...endpoints, ...keptExtremes, ...keptFillers];
}

// The highest peak and the lowest valley (first on ties), where present.
function keyExtremes(extremes) {
  const pick = (kind, better) => extremes
    .filter((e) => e.kind === kind)
    .reduce((best, e) => (best === null || better(e.ele, best.ele) ? e : best), null);
  return [pick("peak", (x, y) => x > y), pick("valley", (x, y) => x < y)]
    .filter((e) => e !== null);
}

// `count` items spread evenly across `items` (all of them if they fit).
function thinEvenly(items, count) {
  if (count <= 0) return [];
  if (items.length <= count) return items;
  return Array.from(
    { length: count },
    (_, k) => items[Math.floor(((k + 0.5) * items.length) / count)],
  );
}

// --- timing ---

// Arrival time (epoch ms) at each cumulative distance, at a constant speed.
// Throws on a non-positive or non-finite speed, which has no meaningful answer.
export function arrivalTimes(cumDistKmArray, speedKmh, startMs) {
  if (!(speedKmh > 0) || !Number.isFinite(speedKmh)) {
    throw new Error(`Invalid speed: ${speedKmh}`);
  }
  return cumDistKmArray.map((distKm) => startMs + (distKm / speedKmh) * 3600000);
}

// --- playback ---

// Position `distKm` along the route: `{ lat, lon, ele, bearingDeg }`, linearly
// interpolated between the two bracketing points (plain lat/lon lerp is fine at
// GPX point spacing). Longitude interpolates the short way round, so a segment
// crossing the antimeridian stays near ±180; interpolated longitudes are
// wrapped into [-180, 180). Clamps to the first point at or below 0 (and for
// NaN) and to the last point at or beyond the end. `ele` is `null` without
// elevation data; `bearingDeg` is the bracketing segment's heading.
export function positionAtDistance(route, distKm) {
  const { points } = route;
  const lastPoint = points[points.length - 1];
  if (!(distKm > 0)) return positionOf(route, points[0]);
  if (distKm >= route.totalDistKm) return positionOf(route, lastPoint);

  const i = segmentIndex(points, distKm);
  const a = points[i];
  const b = points[i + 1];
  // segmentIndex returns the LAST point at or before distKm, so b is strictly
  // further along and the denominator is never zero.
  const t = (distKm - a.cumDistKm) / (b.cumDistKm - a.cumDistKm);
  const lerp = (x, y) => x + (y - x) * t;
  return {
    lat: lerp(a.lat, b.lat),
    lon: wrapLon(a.lon + wrapLon(b.lon - a.lon) * t),
    ele: route.hasElevation ? lerp(a.ele, b.ele) : null,
    bearingDeg: a.bearingDeg,
  };
}

function positionOf(route, p) {
  return {
    lat: p.lat,
    lon: p.lon,
    ele: route.hasElevation ? p.ele : null,
    bearingDeg: p.bearingDeg,
  };
}

// Index of the last point with cumDistKm <= distKm (0 if distKm is before the
// start). Binary search: routes can have tens of thousands of points.
function segmentIndex(points, distKm) {
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (points[mid].cumDistKm <= distKm) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

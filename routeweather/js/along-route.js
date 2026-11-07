// Weather and time at any distance along the route, between the sampled
// weather points. Pure functions over the page state (see main.js): the
// playback clock calls them every frame, so they are a binary search and a
// handful of arithmetic, nothing more.
//
// Interpolation between the two samples either side of `distKm` (by distance):
//
//   tempC, model2mTempC, gustKmh,      linear. Head/crosswind are interpolated
//   model10mWindKmh, model10mGustKmh,  rather than recomputed from the route's
//   headKmh, crossKmh, eleUsed,        bearing at the position: the raw GPX
//   cloudPct, feelsLikeC, rhPct        bearing flips at every switchback, and
//                                      the charts draw these as straight lines
//                                      between samples, so the readout matches
//                                      the cursor on the chart.
//   windKmh + windFromDeg              speed linear as a scalar, direction via
//                                      u/v (as weather.js blends in time and
//                                      height), so 350° and 10° meet at north.
//   precipMm, precipProb, code,        from the NEARER sample (a tie at
//   precipStepH, precipStepEndMs,      exactly halfway: the earlier). These are
//   cellElev, eleFromModel,            per-hour buckets / per-cell facts, not
//   aboveProfile, sunMin, sunEstimated quantities that vary smoothly along a
//                                      path; the charts draw each sample's
//                                      value over its nearestSpan, halfway to
//                                      its neighbours, so "nearer" is the bar
//                                      the cursor is over.
//   outsideForecast                    true if either neighbour is (the clamped
//                                      value is in play for the whole span);
//                                      exactly on a sample, that sample's own.
//                                      charts.js greys the same spans.
//
// A value missing (NaN/null) on one side takes the nearer sample's value as
// is, so a gap shows as a gap rather than poisoning half a span with NaN.

import { fromUV, toUV } from "./weather.js";
import { HOUR_MS } from "./schedule.js";
import { isFiniteNumber as isNum } from "./display.js";

const LINEAR_KEYS = [
  "tempC", "model2mTempC", "gustKmh", "model10mWindKmh", "model10mGustKmh",
  "headKmh", "crossKmh", "eleUsed", "cloudPct", "feelsLikeC", "rhPct",
];
const NEAREST_KEYS = [
  "precipMm", "precipProb", "precipStepH", "precipStepEndMs", "code", "cellElev",
  "aboveProfile", "sunMin", "sunEstimated",
];

// --- position ---

// `distKm` clamped to the route; 0 without a route or for a non-number.
export function clampDistance(state, distKm) {
  const total = state.route?.totalDistKm ?? 0;
  const d = Number(distKm);
  if (!Number.isFinite(d) || d <= 0) return 0;
  return Math.min(d, total);
}

// The samples either side of `distKm`: indices i0 <= i1 and the fraction
// between them (0 at i0). Before the first / after the last sample clamps to
// it; null without samples.
export function sampleBracket(samples, distKm) {
  const n = samples?.length ?? 0;
  if (n === 0) return null;
  if (!(distKm > samples[0].cumDistKm)) return { i0: 0, i1: 0, frac: 0 };
  if (distKm >= samples[n - 1].cumDistKm) return { i0: n - 1, i1: n - 1, frac: 0 };
  // Largest i with cumDistKm <= distKm.
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].cumDistKm <= distKm) lo = mid;
    else hi = mid;
  }
  const span = samples[lo + 1].cumDistKm - samples[lo].cumDistKm;
  return { i0: lo, i1: lo + 1, frac: span > 0 ? (distKm - samples[lo].cumDistKm) / span : 0 };
}

// --- weather ---

function mix(a, b, frac, nearerIsB) {
  if (isNum(a) && isNum(b)) return a + (b - a) * frac;
  return nearerIsB ? b : a;
}

function mixWind(a, b, frac, nearerIsB) {
  const ok = (w) => isNum(w.windKmh) && isNum(w.windFromDeg);
  if (!ok(a) || !ok(b)) {
    const w = nearerIsB ? b : a;
    return { windKmh: w.windKmh, windFromDeg: w.windFromDeg };
  }
  const ua = toUV(a.windKmh, a.windFromDeg);
  const ub = toUV(b.windKmh, b.windFromDeg);
  const { fromDeg } = fromUV(ua.u + (ub.u - ua.u) * frac, ua.v + (ub.v - ua.v) * frac);
  return { windKmh: a.windKmh + (b.windKmh - a.windKmh) * frac, windFromDeg: fromDeg };
}

// The weather at `distKm` along the route, in the shape of a per-sample
// result (weather.js weatherForSample) plus:
//
//   distKm          the clamped distance
//   nearestIndex    the sample the nearest-sample fields came from
//   eleFromModel    true when that sample had no elevation of its own (eleUsed
//                   is then the model cell's terrain height)
//
// null while there are no results.
export function weatherAtDistance(state, distKm) {
  const { samples, results } = state;
  if (!samples || !results || results.length !== samples.length) return null;
  const d = clampDistance(state, distKm);
  const { i0, i1, frac } = sampleBracket(samples, d);
  const a = results[i0];
  const b = results[i1];
  const nearerIsB = frac > 0.5;
  const nearestIndex = nearerIsB ? i1 : i0;
  const near = results[nearestIndex];

  const out = { distKm: d, nearestIndex };
  for (const key of LINEAR_KEYS) out[key] = mix(a[key], b[key], frac, nearerIsB);
  for (const key of NEAREST_KEYS) out[key] = near[key];
  Object.assign(out, mixWind(a, b, frac, nearerIsB));
  out.eleFromModel = !isNum(samples[nearestIndex].ele);
  out.outsideForecast = frac === 0 ? Boolean(a.outsideForecast)
    : Boolean(a.outsideForecast || b.outsideForecast);
  return out;
}

// --- time ---

// Arrival time (epoch ms) at `distKm`: the start plus distance over speed, the
// same constant-speed model as route.js arrivalTimes. NaN without a valid
// speed or start.
export function timeAtDistance(state, distKm) {
  const { startMs, speedKmh } = state;
  if (!isNum(startMs) || !isNum(speedKmh) || speedKmh <= 0) return NaN;
  return startMs + (clampDistance(state, distKm) / speedKmh) * HOUR_MS;
}

// --- per-sample spans ---

// The stretch [fromKm, toKm] sample `i`'s nearest-sample values stand for:
// halfway to the previous sample to halfway to the next (the route's ends,
// 0 and `totalKm`, at either end).
export function nearestSpan(samples, i, totalKm) {
  const mid = (j) => (samples[i].cumDistKm + samples[j].cumDistKm) / 2;
  return [i === 0 ? 0 : mid(i - 1), i === samples.length - 1 ? totalKm : mid(i + 1)];
}

// Time on the move (ms) over each sample's nearestSpan at the plan's constant
// speed, parallel to the samples; null without samples, results, a route or a
// valid speed.
export function nearestSpanTimes(state) {
  const { samples, results, route, speedKmh } = state;
  if (!samples || !results || !route || !isNum(speedKmh) || speedKmh <= 0) return null;
  return samples.map((_, i) => {
    const [from, to] = nearestSpan(samples, i, route.totalDistKm);
    return ((to - from) / speedKmh) * HOUR_MS;
  });
}

// The rain you would walk through (mm): each sample's precipitation per hour
// over the time spent nearer to it than to its neighbours. Time-weighted, so
// however many samples fall in one forecast hour (or 3-hour step), that hour
// counts once. Samples with no amount add nothing. null as nearestSpanTimes.
export function routeRain(state) {
  const spans = nearestSpanTimes(state);
  if (!spans) return null;
  return spans.reduce((mm, spanMs, i) => {
    const rate = state.results[i].precipMm;
    return isNum(rate) ? mm + rate * (spanMs / HOUR_MS) : mm;
  }, 0);
}

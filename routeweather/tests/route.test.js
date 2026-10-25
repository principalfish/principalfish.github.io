import { describe, it, expect } from "vitest";

import {
  haversineKm, bearingDeg, hasFullElevation, buildRoute, smoothElevation,
  sampleRoute, arrivalTimes, positionAtDistance, EARTH_RADIUS_KM, MERGE_DISTANCE_KM,
} from "../js/route.js";

// One degree of arc on a sphere of EARTH_RADIUS_KM — the exact great-circle
// length of 1° along a meridian or the equator, computed independently of
// haversine.
const KM_PER_DEG = (Math.PI * EARTH_RADIUS_KM) / 180;

// Points heading due north from (50, 0), one per elevation, `spacingM` apart.
// `eles` may be a number of points (no elevation) or an array of elevations.
// Like lineRoute, but the spacing wobbles deterministically between ~30 and
// ~70 m, as real GPX spacing does.
function jitteredRoute(eles) {
  let km = 0;
  return eles.map((ele, i) => {
    if (i > 0) km += (50 + 20 * Math.sin(i * 1.7)) / 1000;
    return { lat: 50 + km / KM_PER_DEG, lon: 0, ele };
  });
}

function lineRoute(eles, spacingM = 100) {
  const list = typeof eles === "number" ? Array(eles).fill(undefined) : eles;
  return list.map((ele, i) => ({ lat: 50 + (i * spacingM) / 1000 / KM_PER_DEG, lon: 0, ele }));
}

// Elevations for `n` points `spacingM` apart, from a function of distance (km).
const profile = (n, spacingM, fn) => Array.from({ length: n }, (_, i) => fn((i * spacingM) / 1000));

describe("haversineKm", () => {
  it("matches the exact arc length of 1° along a meridian", () => {
    expect(haversineKm({ lat: 10, lon: 5 }, { lat: 11, lon: 5 })).toBeCloseTo(KM_PER_DEG, 9);
  });

  it("matches the exact arc length of 1° along the equator", () => {
    expect(haversineKm({ lat: 0, lon: 0 }, { lat: 0, lon: 1 })).toBeCloseTo(KM_PER_DEG, 9);
  });

  it("is within 0.5% of the commonly quoted 1° ≈ 111.32 km", () => {
    const km = haversineKm({ lat: 0, lon: 0 }, { lat: 1, lon: 0 });
    expect(Math.abs(km - 111.32) / 111.32).toBeLessThan(0.005);
  });

  it("is within 0.5% of the published London–Paris great-circle distance (~343.5 km)", () => {
    const london = { lat: 51.5074, lon: -0.1278 };
    const paris = { lat: 48.8566, lon: 2.3522 };
    expect(Math.abs(haversineKm(london, paris) - 343.5) / 343.5).toBeLessThan(0.005);
  });

  it("is symmetric and zero for identical points", () => {
    const a = { lat: 51.5, lon: -0.1 };
    const b = { lat: 48.9, lon: 2.4 };
    expect(haversineKm(a, b)).toBeCloseTo(haversineKm(b, a), 12);
    expect(haversineKm(a, a)).toBe(0);
  });

  it("gives half the circumference for antipodal points", () => {
    const km = haversineKm({ lat: 0, lon: 0 }, { lat: 0, lon: 180 });
    expect(km).toBeCloseTo(Math.PI * EARTH_RADIUS_KM, 6);
  });
});

describe("bearingDeg", () => {
  it.each([
    ["due north", { lat: 0, lon: 0 }, { lat: 1, lon: 0 }, 0],
    ["due east", { lat: 0, lon: 0 }, { lat: 0, lon: 1 }, 90],
    ["due south", { lat: 1, lon: 0 }, { lat: 0, lon: 0 }, 180],
    ["due west", { lat: 0, lon: 1 }, { lat: 0, lon: 0 }, 270],
  ])("%s", (_, a, b, expected) => {
    expect(bearingDeg(a, b)).toBeCloseTo(expected, 9);
  });

  it("is ~45° for a small north-east step at the equator", () => {
    expect(bearingDeg({ lat: 0, lon: 0 }, { lat: 0.001, lon: 0.001 })).toBeCloseTo(45, 4);
  });

  it("stays in [0, 360) for westerly headings", () => {
    const deg = bearingDeg({ lat: 0, lon: 0 }, { lat: 0.001, lon: -0.001 });
    expect(deg).toBeCloseTo(315, 4);
  });

  it("returns 0 for identical points", () => {
    expect(bearingDeg({ lat: 3, lon: 4 }, { lat: 3, lon: 4 })).toBe(0);
  });
});

describe("hasFullElevation", () => {
  it("is true only when every point has a finite ele", () => {
    expect(hasFullElevation([{ ele: 0 }, { ele: 12.5 }])).toBe(true);
    expect(hasFullElevation([{ ele: 0 }, { ele: undefined }])).toBe(false);
    expect(hasFullElevation([{ ele: NaN }])).toBe(false);
    expect(hasFullElevation([{ ele: null }])).toBe(false);
    expect(hasFullElevation([])).toBe(false);
  });
});

describe("buildRoute", () => {
  // Four points along the equator, 0.01° apart, with an up-down-up profile.
  const POINTS = [
    { lat: 0, lon: 0, ele: 100 },
    { lat: 0, lon: 0.01, ele: 150 },
    { lat: 0, lon: 0.02, ele: 120 },
    { lat: 0, lon: 0.03, ele: 180 },
  ];
  const STEP_KM = 0.01 * KM_PER_DEG;

  it("adds cumulative distance matching a hand-computed sum", () => {
    const route = buildRoute(POINTS);
    route.points.forEach((p, i) => expect(p.cumDistKm).toBeCloseTo(i * STEP_KM, 9));
    for (let i = 1; i < route.points.length; i++) {
      expect(route.points[i].cumDistKm).toBeGreaterThan(route.points[i - 1].cumDistKm);
    }
  });

  it("sets totalDistKm to the last point's cumDistKm", () => {
    const route = buildRoute(POINTS);
    expect(route.totalDistKm).toBe(route.points[3].cumDistKm);
    expect(route.totalDistKm).toBeCloseTo(3 * STEP_KM, 9);
  });

  it("sums ascent and descent from consecutive deltas", () => {
    const route = buildRoute(POINTS);
    expect(route.hasElevation).toBe(true);
    expect(route.ascentM).toBe(50 + 60);
    expect(route.descentM).toBe(30);
  });

  it("reports 0 ascent/descent for a genuinely flat route", () => {
    const route = buildRoute(POINTS.map((p) => ({ ...p, ele: 0 })));
    expect(route.ascentM).toBe(0);
    expect(route.descentM).toBe(0);
  });

  it("uses null, not 0, for ascent/descent without elevation data", () => {
    const route = buildRoute(POINTS.map(({ lat, lon }) => ({ lat, lon, ele: undefined })));
    expect(route.hasElevation).toBe(false);
    expect(route.ascentM).toBeNull();
    expect(route.descentM).toBeNull();
  });

  it("treats partial elevation as no elevation", () => {
    const route = buildRoute([...POINTS.slice(0, 3), { lat: 0, lon: 0.03, ele: undefined }]);
    expect(route.hasElevation).toBe(false);
    expect(route.ascentM).toBeNull();
  });

  it("sets each point's bearing towards the next, repeating it on the last point", () => {
    const route = buildRoute([
      { lat: 0, lon: 0 },
      { lat: 0, lon: 0.01 },
      { lat: 0.01, lon: 0.01 },
    ]);
    expect(route.points.map((p) => p.bearingDeg)).toEqual([
      expect.closeTo(90, 6), expect.closeTo(0, 6), expect.closeTo(0, 6),
    ]);
  });

  it("gives duplicate consecutive points the heading of the next real segment", () => {
    const route = buildRoute([
      { lat: 0, lon: 0 },
      { lat: 0, lon: 0 },
      { lat: 0, lon: 0.01 },
      { lat: 0.01, lon: 0.01 },
      { lat: 0.01, lon: 0.01 },
    ]);
    expect(route.points.map((p) => p.bearingDeg)).toEqual([
      expect.closeTo(90, 6), expect.closeTo(90, 6), expect.closeTo(0, 6),
      expect.closeTo(0, 6), expect.closeTo(0, 6),
    ]);
    expect(route.points[1].cumDistKm).toBe(0);
  });

  it("gives null bearings when no two points differ", () => {
    expect(buildRoute([{ lat: 1, lon: 1 }]).points[0].bearingDeg).toBeNull();
    const route = buildRoute([{ lat: 1, lon: 1 }, { lat: 1, lon: 1 }]);
    expect(route.points.map((p) => p.bearingDeg)).toEqual([null, null]);
    expect(route.totalDistKm).toBe(0);
  });

  it("copies points rather than mutating the input", () => {
    const input = POINTS.map((p) => ({ ...p }));
    const route = buildRoute(input);
    expect(input[0]).not.toHaveProperty("cumDistKm");
    expect(route.points[0]).not.toBe(input[0]);
    expect(route.points[1]).toMatchObject(POINTS[1]);
  });

  it("throws on an empty or missing point list", () => {
    expect(() => buildRoute([])).toThrow(/no points/);
    expect(() => buildRoute(undefined)).toThrow(/no points/);
  });
});

describe("smoothElevation", () => {
  it("damps a single-point spike", () => {
    // 12 m spacing: a ±50 m window holds the spike plus 4 neighbours each side.
    const eles = Array(21).fill(100);
    eles[10] = 200;
    const smoothed = smoothElevation(buildRoute(lineRoute(eles, 12)));
    expect(smoothed[10]).toBeCloseTo((8 * 100 + 200) / 9, 6);
    expect(smoothed[10]).toBeLessThan(200);
    expect(smoothed[0]).toBeCloseTo(100, 9);
  });

  it("damps a single-point dip", () => {
    const eles = Array(21).fill(100);
    eles[10] = 0;
    const smoothed = smoothElevation(buildRoute(lineRoute(eles, 12)));
    expect(smoothed[10]).toBeGreaterThan(0);
    expect(smoothed[10]).toBeCloseTo(800 / 9, 6);
  });

  it("returns an array parallel to route.points", () => {
    const route = buildRoute(lineRoute([1, 2, 3, 4], 30));
    expect(smoothElevation(route)).toHaveLength(4);
  });

  it("widens with windowM", () => {
    const eles = Array(41).fill(100);
    eles[20] = 200;
    const route = buildRoute(lineRoute(eles, 12));
    expect(smoothElevation(route, 300)[20]).toBeLessThan(smoothElevation(route, 100)[20]);
  });

  it("passes raw values through where points are sparser than half the window", () => {
    const eles = [100, 400, 50, 300];
    const smoothed = smoothElevation(buildRoute(lineRoute(eles, 200)), 100);
    smoothed.forEach((value, i) => expect(value).toBeCloseTo(eles[i], 9));
  });

  // Spacings that put neighbours exactly windowM/2 away, where float error in
  // cumDistKm would otherwise drop one side of the window but not the other.
  it.each([50 / 3, 12.5, 25, 50 / 7, 10])("includes neighbours at the window edge (%s m)", (
    spacingM,
  ) => {
    const eles = Array(41).fill(100);
    eles[20] = 200;
    const smoothed = smoothElevation(buildRoute(lineRoute(eles, spacingM)), 100);
    const perSide = Math.round(50 / spacingM);
    expect(smoothed[20]).toBeCloseTo((2 * perSide * 100 + 200) / (2 * perSide + 1), 9);
    expect(smoothed[19]).toBeCloseTo(smoothed[21], 9);
  });

  it("weights by distance, so stationary duplicates count as one point", () => {
    // Flat 100 m at 20 m spacing, with one 130 m reading at vertex 10. The
    // "cafe stop" version repeats that reading 50 times at the same spot.
    const eles = Array(21).fill(100);
    eles[10] = 130;
    const single = lineRoute(eles, 20);
    const withStop = [...single.slice(0, 10), ...Array(50).fill(single[10]), ...single.slice(11)];
    const smoothedSingle = smoothElevation(buildRoute(single));
    const smoothedStop = smoothElevation(buildRoute(withStop));
    // Vertex 8 (40 m before the stop) sees it in both windows; an unweighted
    // mean would be ~(4 * 100 + 50 * 130) / 54 = 127.8 with the duplicates.
    expect(smoothedStop[8]).toBeCloseTo(smoothedSingle[8], 9);
    expect(smoothedStop[8]).toBeLessThan(110);
    // Points after the stop line up again, shifted by the 49 extra copies.
    expect(smoothedStop[12 + 49]).toBeCloseTo(smoothedSingle[12], 9);
  });

  it("falls back to the plain mean when every point is at one spot", () => {
    const route = buildRoute([{ lat: 1, lon: 1, ele: 10 }, { lat: 1, lon: 1, ele: 20 }]);
    expect(smoothElevation(route)).toEqual([15, 15]);
    expect(smoothElevation(buildRoute([{ lat: 1, lon: 1, ele: 7 }]))).toEqual([7]);
  });

  it("returns all nulls without elevation data", () => {
    expect(smoothElevation(buildRoute(lineRoute(3)))).toEqual([null, null, null]);
  });
});

describe("sampleRoute", () => {
  // 20 km at 50 m spacing, flat at 100 m except a triangular summit of 200 m
  // at vertex 201 (10.05 km), 0.5 km wide on each side.
  const SUMMIT_INDEX = 201;
  const summitEles = profile(401, 50, (km) => 100 + Math.max(0, 100 - Math.abs(km - 10.05) * 200));

  const kinds = (samples) => samples.map((s) => s.kind);
  const minGapKm = (samples) => Math.min(
    ...samples.slice(1).map((s, i) => s.cumDistKm - samples[i].cumDistKm),
  );

  it("always samples a prominent summit", () => {
    const route = buildRoute(lineRoute(summitEles, 50));
    const samples = sampleRoute(route);
    const peak = samples.find((s) => s.kind === "peak");
    expect(peak).toMatchObject({ index: SUMMIT_INDEX, ele: 200 });
    expect(kinds(samples).filter((k) => k === "peak")).toHaveLength(1);
  });

  it("samples a prominent valley", () => {
    const eles = summitEles.map((e) => 300 - e);
    const samples = sampleRoute(buildRoute(lineRoute(eles, 50)));
    const valley = samples.find((s) => s.kind === "valley");
    expect(valley).toMatchObject({ index: SUMMIT_INDEX, ele: 100 });
  });

  it("counts a drop of exactly prominenceM on both sides", () => {
    // 200 m spacing, so smoothing passes raw values straight through.
    const route = buildRoute(lineRoute([100, 100, 130, 100, 100], 200));
    expect(kinds(sampleRoute(route))).toEqual(["start", "peak", "end"]);
    const lower = buildRoute(lineRoute([100, 100, 129.99, 100, 100], 200));
    expect(kinds(sampleRoute(lower))).not.toContain("peak");
  });

  it("ignores a bump below the prominence threshold", () => {
    const eles = profile(401, 50, (km) => 100 + Math.max(0, 20 - Math.abs(km - 10.05) * 40));
    const samples = sampleRoute(buildRoute(lineRoute(eles, 50)));
    expect(kinds(samples)).not.toContain("peak");
  });

  it("respects a custom prominenceM", () => {
    const eles = profile(401, 50, (km) => 100 + Math.max(0, 20 - Math.abs(km - 10.05) * 40));
    const samples = sampleRoute(buildRoute(lineRoute(eles, 50)), { prominenceM: 10 });
    expect(kinds(samples)).toContain("peak");
  });

  it("snaps a smoothed summit back to the raw summit vertex", () => {
    // 0 -> 500 m over 9.5 km, then down to 300 m by 10 km. The smoothed
    // maximum drifts towards the gentler (left) side; the sample snaps back.
    const eles = profile(201, 50, (km) => (km <= 9.5 ? km * (500 / 9.5) : 500 - (km - 9.5) * 400));
    const peaks = sampleRoute(buildRoute(lineRoute(eles, 50))).filter((s) => s.kind === "peak");
    expect(peaks.map((s) => s.index)).toEqual([190]);
  });

  it("leaves a summit the route ends just below to the end sample", () => {
    // Climbs to 500 m at 9.5 km, then descends only 10 m to the finish.
    const eles = profile(201, 50, (km) => (km <= 9.5 ? km * (500 / 9.5) : 500 - (km - 9.5) * 20));
    const samples = sampleRoute(buildRoute(lineRoute(eles, 50)));
    expect(kinds(samples)).not.toContain("peak");
    expect(samples.at(-1)).toMatchObject({ kind: "end", ele: 490 });
  });

  it("does not turn flat ground beside a big climb into extremes", () => {
    const samples = sampleRoute(buildRoute(lineRoute(summitEles, 50)));
    expect(kinds(samples).filter((k) => k !== "filler")).toEqual(["start", "peak", "end"]);
  });

  it("samples a flat-topped summit mid-plateau despite uneven point spacing", () => {
    // Up 0 -> 300 m over vertices 0-200, flat 300 m over 200-400, down again
    // over 400-600, with jittered spacing. Float noise in the smoothed plateau
    // must not split it into noise extremes scattered along the top.
    const eles = Array.from({ length: 601 }, (_, i) => {
      if (i <= 200) return i * 1.5;
      if (i <= 400) return 300;
      return (600 - i) * 1.5;
    });
    const peaks = sampleRoute(buildRoute(jitteredRoute(eles))).filter((s) => s.kind === "peak");
    expect(peaks).toHaveLength(1);
    expect(Math.abs(peaks[0].index - 300)).toBeLessThanOrEqual(3);
  });

  it("samples a flat-topped summit once, mid-plateau", () => {
    // 200 m plateau from 4.8 to 5.2 km (vertices 96-104), 100 m elsewhere.
    const eles = profile(201, 50, (km) => (km >= 4.8 - 1e-9 && km <= 5.2 + 1e-9 ? 200 : 100));
    const peaks = sampleRoute(buildRoute(lineRoute(eles, 50))).filter((s) => s.kind === "peak");
    expect(peaks).toHaveLength(1);
    expect(peaks[0].index).toBe(100);
  });

  it("ignores a minor bump on the flank of a bigger summit", () => {
    // Climbs to a 300 m summit at 5 km; a 10 m bump on the way up at 2.5 km.
    const eles = profile(201, 50, (km) => {
      const base = 300 - Math.abs(km - 5) * 60;
      return base + Math.max(0, 10 - Math.abs(km - 2.5) * 40);
    });
    const samples = sampleRoute(buildRoute(lineRoute(eles, 50)));
    expect(samples.filter((s) => s.kind === "peak").map((s) => s.index)).toEqual([100]);
  });

  it("always includes the first and last points", () => {
    const route = buildRoute(lineRoute(summitEles, 50));
    const samples = sampleRoute(route);
    expect(samples[0]).toMatchObject({ index: 0, kind: "start", cumDistKm: 0 });
    expect(samples.at(-1)).toMatchObject({
      index: 400, kind: "end", cumDistKm: route.totalDistKm,
    });
  });

  it("returns vertex samples with their GPX index and the documented shape", () => {
    const route = buildRoute(lineRoute(summitEles, 50));
    const vertices = sampleRoute(route).filter((s) => s.kind !== "filler");
    expect(kinds(vertices)).toEqual(["start", "peak", "end"]);
    for (const s of vertices) {
      const p = route.points[s.index];
      expect(s).toEqual({
        index: s.index,
        lat: p.lat,
        lon: p.lon,
        ele: p.ele,
        cumDistKm: p.cumDistKm,
        bearingDeg: p.bearingDeg,
        kind: s.kind,
      });
    }
  });

  it("places fillers at interpolated positions with a null index", () => {
    const route = buildRoute(lineRoute(summitEles, 50));
    const fillers = sampleRoute(route).filter((s) => s.kind === "filler");
    expect(fillers.length).toBeGreaterThan(0);
    for (const s of fillers) {
      expect(s).toEqual({
        index: null,
        ...positionAtDistance(route, s.cumDistKm),
        cumDistKm: s.cumDistKm,
        kind: "filler",
      });
    }
  });

  it("returns samples sorted by distance", () => {
    const samples = sampleRoute(buildRoute(lineRoute(summitEles, 50)));
    const distances = samples.map((s) => s.cumDistKm);
    expect(distances).toEqual([...distances].sort((a, b) => a - b));
  });

  it("samples a sparse 2-point route along its length, not just at the ends", () => {
    // Planner-exported <rtept>s can be tens of km apart: here one 111 km leg.
    const route = buildRoute([{ lat: 0, lon: 0 }, { lat: 1, lon: 0 }]);
    const samples = sampleRoute(route);
    expect(samples).toHaveLength(112); // floor(111.19 / 1) segments + 1
    expect(samples.slice(1, -1).every((s) => s.index === null)).toBe(true);
    expect(samples.every((s) => s.bearingDeg === 0)).toBe(true);
    const gaps = samples.slice(1).map((s, i) => s.cumDistKm - samples[i].cumDistKm);
    // Even spacing, never under minSpacingKm (config: "no closer than").
    for (const gapKm of gaps) {
      expect(gapKm).toBeGreaterThanOrEqual(1 - 1e-9);
      expect(gapKm).toBeLessThan(1.01);
    }
    // Interpolated positions lie on the leg.
    expect(samples[56].lat).toBeCloseTo(samples[56].cumDistKm / KM_PER_DEG, 9);
    expect(samples[56].lon).toBe(0);
  });

  it("thins interpolated fillers on a sparse route to maxSamples", () => {
    const route = buildRoute([{ lat: 0, lon: 0 }, { lat: 3, lon: 0 }]); // ~334 km
    const samples = sampleRoute(route, { maxSamples: 150 });
    expect(samples.length).toBeLessThanOrEqual(150);
    expect(samples.length).toBeGreaterThanOrEqual(149);
    expect(minGapKm(samples)).toBeGreaterThan(2);
    expect(samples.at(-1).kind).toBe("end");
  });

  it("keeps both start and end of a loop that finishes where it began", () => {
    const loop = [
      { lat: 0, lon: 0 },
      { lat: 0, lon: 0.05 },
      { lat: 0.05, lon: 0.05 },
      { lat: 0.05, lon: 0 },
      { lat: 0, lon: 0 },
    ];
    const samples = sampleRoute(buildRoute(loop));
    expect(samples[0]).toMatchObject({ kind: "start", lat: 0, lon: 0, cumDistKm: 0 });
    expect(samples.at(-1)).toMatchObject({ kind: "end", lat: 0, lon: 0, index: 4 });
    expect(samples.at(-1).cumDistKm).toBeCloseTo(4 * 0.05 * KM_PER_DEG, 3);
  });

  it("fills gentle sections with evenly spaced samples no closer than minSpacingKm", () => {
    const route = buildRoute(lineRoute(Array(201).fill(100), 50)); // 10 km, flat
    const samples = sampleRoute(route);
    expect(kinds(samples)).toEqual(["start", ...Array(9).fill("filler"), "end"]);
    expect(minGapKm(samples)).toBeGreaterThanOrEqual(1 - 1e-9);
  });

  it("widens filler spacing with minSpacingKm", () => {
    const route = buildRoute(lineRoute(Array(201).fill(100), 50));
    const samples = sampleRoute(route, { minSpacingKm: 2.5 });
    expect(samples).toHaveLength(5);
    expect(minGapKm(samples)).toBeCloseTo(2.5, 6);
  });

  it("samples routes without elevation data with endpoints and fillers only", () => {
    const route = buildRoute(lineRoute(101, 50)); // 5 km
    const samples = sampleRoute(route);
    expect(kinds(samples)).toEqual(["start", "filler", "filler", "filler", "filler", "end"]);
    expect(samples.every((s) => s.ele === null)).toBe(true);
  });

  it("caps a ~300 km flat route at maxSamples, keeping both ends", () => {
    const route = buildRoute(lineRoute(Array(3001).fill(100), 100));
    const samples = sampleRoute(route, { maxSamples: 150 });
    expect(samples.length).toBeLessThanOrEqual(150);
    expect(samples.length).toBeGreaterThan(140);
    expect(samples[0].index).toBe(0);
    expect(samples.at(-1).index).toBe(3000);
    // Spacing widened evenly rather than bunching: ~2 km apart.
    expect(minGapKm(samples)).toBeGreaterThan(1.9);
  });

  it("spends nearly the whole budget on a long route with many gaps between extremes", () => {
    // 300 m hills every 7 km: ~43 peaks and ~43 valleys leave ~63 filler slots
    // spread over ~86 gaps, so per-gap rounding must not waste them.
    const eles = profile(3001, 100, (km) => 500 + 300 * Math.sin((km * 2 * Math.PI) / 7));
    const samples = sampleRoute(buildRoute(lineRoute(eles, 100)), { maxSamples: 150 });
    expect(samples.length).toBeLessThanOrEqual(150);
    expect(samples.length).toBeGreaterThanOrEqual(140);
    expect(kinds(samples).filter((k) => k === "filler").length).toBeGreaterThan(50);
  });

  it("caps a ~300 km hilly route whose extremes alone exceed maxSamples", () => {
    // 100 m hills every 2 km: ~150 peaks and ~150 valleys.
    const eles = profile(3001, 100, (km) => 100 + 100 * Math.sin((km * Math.PI) / 1));
    const route = buildRoute(lineRoute(eles, 100));
    const samples = sampleRoute(route, { maxSamples: 150 });
    expect(samples).toHaveLength(150);
    expect(samples[0].kind).toBe("start");
    expect(samples.at(-1).kind).toBe("end");
    expect(kinds(samples).filter((k) => k === "filler")).toHaveLength(0);
    // Thinned evenly: kept extremes still span the whole route.
    expect(samples[1].cumDistKm).toBeLessThan(10);
    expect(samples.at(-2).cumDistKm).toBeGreaterThan(290);
  });

  it("always keeps the highest summit and lowest valley when thinning extremes", () => {
    // ~150 ordinary 100 m hills (crests at x.5 km for even x, troughs at odd
    // x.5 km), with the crest at 100.5 km raised to 600 m and the trough at
    // 201.5 km sunk to -400 m. Only 8 extremes fit, so even thinning alone
    // would almost certainly skip both.
    const eles = profile(3001, 100, (km) => {
      if (Math.abs(km - 100.5) < 1e-9) return 600;
      if (Math.abs(km - 201.5) < 1e-9) return -400;
      return 100 + 100 * Math.sin(km * Math.PI);
    });
    const samples = sampleRoute(buildRoute(lineRoute(eles, 100)), { maxSamples: 10 });
    expect(samples).toHaveLength(10);
    expect(samples.find((s) => s.index === 1005)).toMatchObject({ kind: "peak", ele: 600 });
    expect(samples.find((s) => s.index === 2015)).toMatchObject({ kind: "valley", ele: -400 });
  });

  it("uses the widest spacing that exactly fills the budget", () => {
    // 10 km flat, 6 samples: 4 fillers fit exactly at a 2 km spacing. The
    // search must accept a spacing yielding exactly `room` fillers (>=), or it
    // would settle on 5 fillers and thin them unevenly.
    const route = buildRoute(lineRoute(Array(201).fill(100), 50));
    const samples = sampleRoute(route, { maxSamples: 6 });
    const expected = [0, 2, 4, 6, 8, 10].map((km) => expect.closeTo(km, 6));
    expect(samples.map((s) => s.cumDistKm)).toEqual(expected);
  });

  it("treats maxSamples below 2 as 2", () => {
    const route = buildRoute(lineRoute(summitEles, 50));
    expect(kinds(sampleRoute(route, { maxSamples: 1 }))).toEqual(["start", "end"]);
  });

  it("collapses candidates closer than the merge distance, keeping the extreme", () => {
    // Fillers every 50 m would all be within 200 m of a neighbour.
    const route = buildRoute(lineRoute(summitEles, 50));
    const samples = sampleRoute(route, { minSpacingKm: 0.05 });
    expect(minGapKm(samples)).toBeGreaterThanOrEqual(MERGE_DISTANCE_KM - 1e-9);
    expect(samples.find((s) => s.index === SUMMIT_INDEX)?.kind).toBe("peak");
  });

  it("collapses two prominent peaks closer than the merge distance into one", () => {
    // Steep 100 m peaks at 2.0 km and 2.0 + sepKm, flat ground between, at
    // 10 m spacing. Far apart both are sampled; within 200 m only one is.
    const twinPeaks = (sepKm) => {
      const eles = profile(401, 10, (km) => {
        const peak = (c) => Math.max(0, 100 - Math.abs(km - c) * 2000);
        return Math.max(peak(2.0), peak(2.0 + sepKm));
      });
      return sampleRoute(buildRoute(lineRoute(eles, 10))).filter((s) => s.kind === "peak");
    };
    expect(twinPeaks(0.3).map((s) => s.index)).toEqual([200, 230]);
    expect(twinPeaks(0.19)).toHaveLength(1);
  });

  it("keeps both of two equal-height summits", () => {
    // 300 m summits at 3 km and 7 km rising from flat 100 m ground: walking
    // from either, the other is not strictly higher, so neither blocks it.
    const hill = (km, centreKm) => Math.max(0, 200 - (Math.abs(km - centreKm) * 200) / 1.5);
    const eles = profile(201, 50, (km) => 100 + hill(km, 3) + hill(km, 7));
    const samples = sampleRoute(buildRoute(lineRoute(eles, 50)));
    const peaks = samples.filter((s) => s.kind === "peak");
    expect(peaks.map((s) => s.index)).toEqual([60, 140]);
    expect(peaks.map((s) => s.ele)).toEqual([300, 300]);
    // The flat ground between them is one valley, somewhere on the 4.5-5.5 km
    // plateau.
    const valleys = samples.filter((s) => s.kind === "valley");
    expect(valleys).toHaveLength(1);
    expect(valleys[0].index).toBeGreaterThanOrEqual(90);
    expect(valleys[0].index).toBeLessThanOrEqual(110);
  });

  it("keeps both equal summits either side of a col shallower than prominenceM", () => {
    // 300 m summits at 3 and 4 km, only a 20 m col between, flanks down to
    // 100 m. Walking from either summit, the other is equal — not strictly
    // higher — so the walk carries on over it to the far flank and passes.
    const eles = profile(121, 50, (km) => {
      if (km <= 2) return 100;
      if (km <= 3) return 100 + (km - 2) * 200;
      if (km <= 3.5) return 300 - (km - 3) * 40;
      if (km <= 4) return 280 + (km - 3.5) * 40;
      if (km <= 5) return 300 - (km - 4) * 200;
      return 100;
    });
    const samples = sampleRoute(buildRoute(lineRoute(eles, 50)));
    expect(samples.filter((s) => s.kind === "peak").map((s) => s.index)).toEqual([60, 80]);
    expect(kinds(samples)).not.toContain("valley");
  });

  it("merges an extreme just inside MERGE_DISTANCE_KM of the start, not just outside", () => {
    // Sparse points so smoothing is a pass-through; the dip sits 199 or 201 m
    // in. (Exactly 200 m can't be hit reliably through haversine float error.)
    const at = (km, ele) => ({ lat: 50 + km / KM_PER_DEG, lon: 0, ele });
    const dipAt = (metres) => sampleRoute(buildRoute([
      at(0, 300),
      at(metres / 1000, 200),
      at(1, 300),
      at(2, 300),
    ]));
    expect(kinds(dipAt(201))).toContain("valley");
    expect(kinds(dipAt(199))).not.toContain("valley");
  });

  it("keeps both start and end on a route shorter than the merge distance", () => {
    const samples = sampleRoute(buildRoute(lineRoute([100, 110, 105], 50)));
    expect(kinds(samples)).toEqual(["start", "end"]);
  });

  it("returns a single start sample for a one-point route", () => {
    const samples = sampleRoute(buildRoute([{ lat: 1, lon: 2, ele: 3 }]));
    expect(samples).toEqual([
      { index: 0, lat: 1, lon: 2, ele: 3, cumDistKm: 0, bearingDeg: null, kind: "start" },
    ]);
  });
});

describe("arrivalTimes", () => {
  it("converts cumulative distance to arrival time at a constant speed", () => {
    const start = Date.UTC(2026, 9, 26, 8, 0);
    expect(arrivalTimes([0, 10, 25], 20, start)).toEqual([
      start,
      start + 30 * 60000, // 10 km at 20 km/h = 30 min
      start + 75 * 60000, // 25 km at 20 km/h = 75 min
    ]);
  });

  it("returns an empty array for no distances", () => {
    expect(arrivalTimes([], 10, 0)).toEqual([]);
  });

  it.each([0, -5, NaN, Infinity, undefined])("throws on speed %s", (speed) => {
    expect(() => arrivalTimes([0, 1], speed, 0)).toThrow(/Invalid speed/);
  });
});

describe("positionAtDistance", () => {
  // Along the equator: east, then north (bearings 90 then 0).
  const route = buildRoute([
    { lat: 0, lon: 0, ele: 100 },
    { lat: 0, lon: 0.01, ele: 200 },
    { lat: 0.01, lon: 0.01, ele: 150 },
  ]);
  const STEP_KM = 0.01 * KM_PER_DEG;

  it("returns the first point exactly at distance 0", () => {
    expect(positionAtDistance(route, 0)).toEqual({
      lat: 0, lon: 0, ele: 100, bearingDeg: route.points[0].bearingDeg,
    });
  });

  it("returns the last point exactly at totalDistKm", () => {
    expect(positionAtDistance(route, route.totalDistKm)).toEqual({
      lat: 0.01, lon: 0.01, ele: 150, bearingDeg: route.points[2].bearingDeg,
    });
  });

  it("interpolates linearly at a segment's midpoint", () => {
    const pos = positionAtDistance(route, STEP_KM / 2);
    expect(pos.lat).toBeCloseTo(0, 12);
    expect(pos.lon).toBeCloseTo(0.005, 9);
    expect(pos.ele).toBeCloseTo(150, 6);
    expect(pos.bearingDeg).toBeCloseTo(90, 6);
  });

  it("interpolates within a later segment, using that segment's bearing", () => {
    const pos = positionAtDistance(route, STEP_KM * 1.25);
    expect(pos.lat).toBeCloseTo(0.0025, 6);
    expect(pos.lon).toBeCloseTo(0.01, 12);
    expect(pos.ele).toBeCloseTo(187.5, 3);
    expect(pos.bearingDeg).toBeCloseTo(0, 6);
  });

  it("returns an exact interior vertex at its own distance", () => {
    const pos = positionAtDistance(route, route.points[1].cumDistKm);
    expect(pos.lat).toBeCloseTo(0, 12);
    expect(pos.lon).toBeCloseTo(0.01, 12);
    expect(pos.ele).toBeCloseTo(200, 9);
  });

  it("clamps before the start and past the end", () => {
    expect(positionAtDistance(route, -5)).toEqual(positionAtDistance(route, 0));
    expect(positionAtDistance(route, NaN)).toEqual(positionAtDistance(route, 0));
    expect(positionAtDistance(route, route.totalDistKm + 10))
      .toEqual(positionAtDistance(route, route.totalDistKm));
  });

  it("returns null elevation for a route without elevation data", () => {
    const flat = buildRoute([{ lat: 0, lon: 0 }, { lat: 0, lon: 0.01 }]);
    expect(positionAtDistance(flat, 0).ele).toBeNull();
    expect(positionAtDistance(flat, STEP_KM / 2).ele).toBeNull();
    expect(positionAtDistance(flat, 99).ele).toBeNull();
  });

  it("steps over duplicate points without dividing by zero", () => {
    const dup = buildRoute([
      { lat: 0, lon: 0 },
      { lat: 0, lon: 0.01 },
      { lat: 0, lon: 0.01 },
      { lat: 0, lon: 0.02 },
    ]);
    const pos = positionAtDistance(dup, STEP_KM * 1.5);
    expect(pos.lon).toBeCloseTo(0.015, 9);
    expect(pos.bearingDeg).toBeCloseTo(90, 6);
  });

  it.each([
    ["eastbound", 179.99, -179.99, 90, 179.995, -179.995],
    ["westbound", -179.99, 179.99, 270, -179.995, 179.995],
  ])("interpolates the short way across the antimeridian (%s)", (
    _, fromLon, toLon, heading, quarterLon, threeQuarterLon,
  ) => {
    const crossing = buildRoute([{ lat: 10, lon: fromLon }, { lat: 10, lon: toLon }]);
    expect(crossing.totalDistKm).toBeLessThan(3);
    const at = (fraction) => positionAtDistance(crossing, crossing.totalDistKm * fraction);
    expect(Math.abs(at(0.5).lon)).toBeCloseTo(180, 9);
    expect(at(0.25).lon).toBeCloseTo(quarterLon, 9);
    expect(at(0.75).lon).toBeCloseTo(threeQuarterLon, 9);
    // A great circle along a parallel isn't quite due east/west.
    expect(at(0.5).bearingDeg).toBeCloseTo(heading, 2);
    for (const fraction of [0.1, 0.5, 0.9]) {
      expect(at(fraction).lon).toBeGreaterThanOrEqual(-180);
      expect(at(fraction).lon).toBeLessThan(180);
    }
  });

  it("handles a one-point route", () => {
    const single = buildRoute([{ lat: 1, lon: 2, ele: 3 }]);
    expect(positionAtDistance(single, 5)).toEqual({ lat: 1, lon: 2, ele: 3, bearingDeg: null });
  });
});

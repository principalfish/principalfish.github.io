import { readFileSync } from "node:fs";

import { describe, it, expect } from "vitest";

import {
  toUV, fromUV, interpHourly, bucketHourly, profileAt, temperatureAt, windAt, headCross,
  weatherForSample, describeWeather, WMO_DESCRIPTIONS, LAPSE_RATE_K_PER_M, isAboveProfile,
  sunshineAt, apparentTemperatureC,
} from "../js/weather.js";
import { daylightMinutes } from "../js/sun.js";
import { normaliseLocation } from "../js/openmeteo.js";
import { HOURLY_VARS, PRESSURE_LEVELS } from "../js/config.js";
import { MISSING } from "../js/units.js";

const HOUR = 3600 * 1000;
const T0 = Date.UTC(2026, 9, 26, 9);

// Real Open-Meteo response: [upland cell at 509 m, lowland cell at 18 m],
// 2026-10-26 09:00–14:00 UTC. cloud_cover, sunshine_duration and
// relative_humidity_2m were added by hand afterwards (plausible for that wet
// day), not fetched.
const FIXTURE = JSON.parse(
  readFileSync(new URL("./fixtures/forecast-2loc.json", import.meta.url), "utf8"),
);

const toRad = (deg) => (deg * Math.PI) / 180;
const toDeg = (rad) => (rad * 180) / Math.PI;

// Angular distance between two directions, so 0° and 359.9999° count as equal.
const angleDiff = (a, b) => {
  const d = Math.abs(a - b) % 360;
  return Math.min(d, 360 - d);
};

// A normalised location (as openmeteo.js returns) with `hours` hourly steps
// from T0. Each `series` entry is a constant or a per-hour array. Calm, dry
// 10 °C by default, with every pressure level underground (so unused) unless a
// test gives it a height.
function makeLoc({ cellElev = 0, hours = 3, ...series } = {}) {
  const defaults = {
    temperature_2m: 10,
    precipitation: 0,
    precipitation_probability: 0,
    weather_code: 0,
    wind_speed_10m: 0,
    wind_direction_10m: 0,
    wind_gusts_10m: 0,
    cloud_cover: 0,
    sunshine_duration: 0,
    relative_humidity_2m: 50,
  };
  for (const level of PRESSURE_LEVELS) {
    defaults[`temperature_${level}hPa`] = 0;
    defaults[`wind_speed_${level}hPa`] = 0;
    defaults[`wind_direction_${level}hPa`] = 0;
    defaults[`geopotential_height_${level}hPa`] = -1000;
  }
  const vars = {};
  for (const name of HOURLY_VARS) {
    const value = name in series ? series[name] : defaults[name];
    const at = (i) => (Array.isArray(value) ? value[i] : value);
    vars[name] = Float64Array.from({ length: hours }, (_, i) => at(i));
  }
  const timesMs = Float64Array.from({ length: hours }, (_, i) => T0 + i * HOUR);
  return { cellLat: 54, cellLon: -3, cellElev, timesMs, vars };
}

describe("toUV / fromUV", () => {
  it("points the vector where the air is going", () => {
    // From the north -> blowing south (v negative); from the west -> east (u positive).
    const north = toUV(10, 0);
    expect(north.u).toBeCloseTo(0, 10);
    expect(north.v).toBeCloseTo(-10, 10);
    const west = toUV(10, 270);
    expect(west.u).toBeCloseTo(10, 10);
    expect(west.v).toBeCloseTo(0, 10);
  });

  it("round-trips speed and direction", () => {
    for (const dir of [0, 10, 45, 90, 179, 180, 181, 270, 350, 359.5]) {
      const { u, v } = toUV(17, dir);
      const back = fromUV(u, v);
      expect(back.speed).toBeCloseTo(17, 10);
      expect(angleDiff(back.fromDeg, dir)).toBeLessThan(1e-9);
      expect(back.fromDeg).toBeGreaterThanOrEqual(0);
      expect(back.fromDeg).toBeLessThan(360);
    }
  });

  it("reports calm air as speed 0 from 0°", () => {
    expect(fromUV(0, 0)).toEqual({ speed: 0, fromDeg: 0 });
  });
});

describe("interpHourly", () => {
  const times = [T0, T0 + HOUR, T0 + 2 * HOUR];
  const values = [0, 10, 20];

  it("interpolates linearly between steps", () => {
    expect(interpHourly(times, values, T0 + HOUR / 2)).toBeCloseTo(5, 10);
    expect(interpHourly(times, values, T0 + 1.75 * HOUR)).toBeCloseTo(17.5, 10);
  });

  it("returns a step's own value exactly on it", () => {
    expect(interpHourly(times, values, T0 + HOUR)).toBe(10);
  });

  it("clamps outside the series", () => {
    expect(interpHourly(times, values, T0 - 5 * HOUR)).toBe(0);
    expect(interpHourly(times, values, T0 + 9 * HOUR)).toBe(20);
  });

  it("does not let a gap in the next hour poison an exact step", () => {
    expect(interpHourly(times, [0, 10, NaN], T0 + HOUR)).toBe(10);
    expect(interpHourly(times, [0, 10, NaN], T0 + 1.5 * HOUR)).toBeNaN();
  });

  it("returns NaN for an empty series or a NaN time", () => {
    expect(interpHourly([], [], T0)).toBeNaN();
    expect(interpHourly(times, values, NaN)).toBeNaN();
  });
});

describe("bucketHourly", () => {
  // Value at hh:00 is the sum over the hour before it.
  const times = [T0, T0 + HOUR, T0 + 2 * HOUR]; // 09:00, 10:00, 11:00
  const precip = [1, 2, 3];

  it("reads the hour just finished when arriving exactly on the hour", () => {
    expect(bucketHourly(times, precip, T0 + HOUR)).toBe(2); // 10:00 -> 09–10 bucket
  });

  it("reads the hour in progress otherwise", () => {
    expect(bucketHourly(times, precip, T0 + HOUR + 20 * 60 * 1000)).toBe(3); // 10:20 -> 10–11
  });

  it("clamps outside the series", () => {
    expect(bucketHourly(times, precip, T0 - HOUR)).toBe(1);
    expect(bucketHourly(times, precip, T0 + 5 * HOUR)).toBe(3);
  });

  it("returns NaN for an empty series", () => {
    expect(bucketHourly([], [], T0)).toBeNaN();
  });
});

describe("profileAt", () => {
  it("drops pressure levels at or below the terrain (negative 1000 hPa height)", () => {
    const loc = makeLoc({
      cellElev: 500,
      geopotential_height_1000hPa: -40,
      temperature_1000hPa: 30,
      geopotential_height_925hPa: 510, // exactly cellElev + 10: still dropped
      temperature_925hPa: 30,
      geopotential_height_850hPa: 1300,
      temperature_850hPa: 2,
    });
    const profile = profileAt(loc, T0);
    expect(profile.temps.map((p) => p.heightM)).toEqual([502, 1300]);
    expect(profile.winds.map((p) => p.heightM)).toEqual([510, 1300]);
    expect(temperatureAt(profile, 500)).toBe(10);
  });

  it("interpolates level heights in time before using them", () => {
    const loc = makeLoc({ geopotential_height_925hPa: [600, 800], hours: 2 });
    const profile = profileAt(loc, T0 + HOUR / 2);
    expect(profile.temps[1].heightM).toBeCloseTo(700, 10);
  });

  it("interpolates wind across the 350°↔10° wrap as vectors, not angles", () => {
    const loc = makeLoc({ wind_speed_10m: 20, wind_direction_10m: [350, 10], hours: 2 });
    const profile = profileAt(loc, T0 + HOUR / 2);
    expect(angleDiff(profile.model10mFromDeg, 0)).toBeLessThan(1e-9);
    // Speed is interpolated as a scalar, so the turn doesn't shrink it.
    expect(profile.model10mWindKmh).toBeCloseTo(20, 9);
  });

  it("reads a step's own wind exactly on it, despite a gap in the next hour", () => {
    const loc = makeLoc({ wind_speed_10m: [20, NaN], wind_direction_10m: [250, 90], hours: 2 });
    const profile = profileAt(loc, T0);
    expect(profile.model10mWindKmh).toBe(20);
    expect(profile.model10mFromDeg).toBe(250);
    expect(profileAt(loc, T0 + HOUR / 2).model10mWindKmh).toBeNaN();
  });

  it("interpolates speed linearly through a 90° turn, a quarter of the way", () => {
    const loc = makeLoc({ wind_speed_10m: [20, 40], wind_direction_10m: [0, 90], hours: 2 });
    const profile = profileAt(loc, T0 + HOUR / 4);
    expect(profile.model10mWindKmh).toBeCloseTo(25, 9);
    // u/v a quarter of the way from (0, -20) to (-40, 0) is (-10, -15).
    expect(profile.model10mFromDeg).toBeCloseTo(toDeg(Math.atan2(10, 15)), 9);
  });

  it("leaves out levels with gaps", () => {
    const loc = makeLoc({ geopotential_height_925hPa: 800, temperature_925hPa: NaN });
    const profile = profileAt(loc, T0);
    expect(profile.temps).toHaveLength(1);
    expect(profile.winds).toHaveLength(2);
  });
});

describe("temperatureAt", () => {
  it("warms at the standard lapse rate below the lowest point", () => {
    const profile = profileAt(makeLoc({ cellElev: 500, temperature_2m: 10 }), T0);
    // Lowest point is 502 m; 2 m above a 0 m route is 500 m lower.
    expect(LAPSE_RATE_K_PER_M).toBe(0.0065);
    expect(temperatureAt(profile, 0)).toBeCloseTo(10 + 500 * 0.0065, 10);
    expect(temperatureAt(profile, 300)).toBeCloseTo(10 + 200 * 0.0065, 10);
  });

  it("holds the top level's value above it", () => {
    const loc = makeLoc({
      geopotential_height_925hPa: 800,
      temperature_925hPa: 4,
      geopotential_height_700hPa: 3000,
      temperature_700hPa: -10,
    });
    expect(temperatureAt(profileAt(loc, T0), 4000)).toBe(-10);
  });

  it("keeps an inversion: a warm level above a cold surface interpolates between them", () => {
    const loc = makeLoc({
      cellElev: 100,
      temperature_2m: 0,
      geopotential_height_925hPa: 702,
      temperature_925hPa: 6,
    });
    // 402 m is halfway between 102 m and 702 m.
    const t = temperatureAt(profileAt(loc, T0), 400);
    expect(t).toBeCloseTo(3, 10);
  });

  it("weights the nearer point more (a quarter of the way up)", () => {
    const loc = makeLoc({
      cellElev: 100,
      temperature_2m: 0,
      geopotential_height_925hPa: 702,
      temperature_925hPa: 6,
    });
    // 252 m is a quarter of the way from 102 m to 702 m.
    expect(temperatureAt(profileAt(loc, T0), 250)).toBeCloseTo(1.5, 10);
  });

  it("interpolates in time", () => {
    const loc = makeLoc({ temperature_2m: [0, 10], hours: 2 });
    expect(temperatureAt(profileAt(loc, T0 + HOUR / 2), 0)).toBeCloseTo(5, 10);
    expect(temperatureAt(profileAt(loc, T0 + HOUR / 4), 0)).toBeCloseTo(2.5, 10);
  });

  it("returns NaN for a missing elevation or an empty profile", () => {
    const profile = profileAt(makeLoc(), T0);
    expect(temperatureAt(profile, NaN)).toBeNaN();
    expect(temperatureAt({ temps: [], winds: [] }, 100)).toBeNaN();
  });
});

describe("windAt", () => {
  const loc = makeLoc({
    cellElev: 100,
    wind_speed_10m: 20,
    wind_direction_10m: 350,
    geopotential_height_925hPa: 710,
    wind_speed_925hPa: 20,
    wind_direction_925hPa: 10,
    geopotential_height_700hPa: 3000,
    wind_speed_700hPa: 90,
    wind_direction_700hPa: 270,
  });
  const profile = profileAt(loc, T0);

  it("interpolates across the 350°↔10° wrap in height to ~north", () => {
    // 10 m above 400 m is 410 m: halfway between 110 m and 710 m.
    const wind = windAt(profile, 400);
    expect(angleDiff(wind.fromDeg, 0)).toBeLessThan(1e-9);
    expect(wind.speed).toBeCloseTo(20, 9);
  });

  it("interpolates speed linearly through a 90° turn, a quarter of the way up", () => {
    const turning = profileAt(makeLoc({
      cellElev: 100,
      wind_speed_10m: 20,
      wind_direction_10m: 0,
      geopotential_height_925hPa: 710,
      wind_speed_925hPa: 60,
      wind_direction_925hPa: 90,
    }), T0);
    // 260 m is a quarter of the way from 110 m to 710 m; u/v a quarter of the
    // way from (0, -20) to (-60, 0) is (-15, -15).
    const wind = windAt(turning, 250);
    expect(wind.speed).toBeCloseTo(30, 9);
    expect(wind.fromDeg).toBeCloseTo(45, 9);
  });

  it("uses the 10 m wind below the profile", () => {
    const wind = windAt(profile, 0);
    expect(wind.speed).toBeCloseTo(20, 10);
    expect(wind.fromDeg).toBeCloseTo(350, 9);
  });

  it("holds the top level's wind above it", () => {
    const wind = windAt(profile, 5000);
    expect(wind.speed).toBeCloseTo(90, 9);
    expect(wind.fromDeg).toBeCloseTo(270, 9);
  });
});

describe("headCross", () => {
  it("gives a positive headwind for wind from ahead", () => {
    const { headKmh, crossKmh } = headCross(0, 20, 0);
    expect(headKmh).toBeCloseTo(20, 10);
    expect(crossKmh).toBeCloseTo(0, 10);
  });

  it("gives a negative headwind (tailwind) for wind from behind", () => {
    expect(headCross(0, 20, 180).headKmh).toBeCloseTo(-20, 10);
  });

  it("gives a positive crosswind for wind from the right", () => {
    // Travelling north, east is on the right.
    const { headKmh, crossKmh } = headCross(90, 20, 0);
    expect(headKmh).toBeCloseTo(0, 10);
    expect(crossKmh).toBeCloseTo(20, 10);
    expect(headCross(270, 20, 0).crossKmh).toBeCloseTo(-20, 10);
  });

  it("handles bearings either side of north", () => {
    // Wind from 10°, heading 350°: 20° to the right of dead ahead.
    const { headKmh, crossKmh } = headCross(10, 10, 350);
    expect(headKmh).toBeCloseTo(10 * Math.cos(toRad(20)), 10);
    expect(crossKmh).toBeCloseTo(10 * Math.sin(toRad(20)), 10);
  });

  it("returns nulls without a bearing", () => {
    expect(headCross(90, 20, null)).toEqual({ headKmh: null, crossKmh: null });
    expect(headCross(90, 20, undefined)).toEqual({ headKmh: null, crossKmh: null });
  });
});

describe("weatherForSample", () => {
  const windy = {
    cellElev: 0,
    wind_speed_10m: 20,
    wind_direction_10m: 270,
    wind_gusts_10m: 30,
    geopotential_height_925hPa: 700,
    wind_speed_925hPa: 40,
    wind_direction_925hPa: 270,
  };

  it("adds the 10 m gust excess to the wind at altitude", () => {
    const loc = makeLoc(windy);
    // Excess 30 - 20 = 10; 690 m + 10 m sits on the 925 hPa level (40 km/h).
    const summit = weatherForSample(loc, { ele: 690 }, T0, 0);
    expect(summit.gustKmh).toBeCloseTo(50, 9);
    expect(summit.model10mGustKmh).toBe(30);
    expect(weatherForSample(loc, { ele: 0 }, T0, 0).gustKmh).toBeCloseTo(30, 9);
  });

  it("adds the whole 10 m gust in calm 10 m air", () => {
    const loc = makeLoc({ ...windy, wind_speed_10m: 0, wind_gusts_10m: 5 });
    expect(weatherForSample(loc, { ele: 0 }, T0, 0).gustKmh).toBe(5);
    expect(weatherForSample(loc, { ele: 690 }, T0, 0).gustKmh).toBeCloseTo(45, 9);
  });

  it("never gusts below the mean wind", () => {
    const odd = makeLoc({ ...windy, wind_gusts_10m: 10 });
    expect(weatherForSample(odd, { ele: 690 }, T0, 0).gustKmh).toBeCloseTo(40, 9);
  });

  it("takes the gust from the hour bucket, against that hour's mean 10 m wind", () => {
    const loc = makeLoc({ wind_speed_10m: [10, 20, 40], wind_gusts_10m: [30, 30, 60] });
    // 10:20 is in the 10–11 bucket: gust 60 (interpolating would give 40), mean
    // wind (20 + 40) / 2 = 30, so the excess is 30. The 10 m wind at 10:20 is
    // 20 + 20 / 3.
    const w = weatherForSample(loc, { ele: 0 }, T0 + HOUR + HOUR / 3, 0);
    expect(w.model10mGustKmh).toBe(60);
    expect(w.windKmh).toBeCloseTo(20 + 20 / 3, 9);
    expect(w.gustKmh).toBeCloseTo(20 + 20 / 3 + 30, 9);
  });

  it("uses the first step alone for the gust mean before the series", () => {
    const loc = makeLoc({ wind_speed_10m: [10, 20, 40], wind_gusts_10m: [25, 30, 60] });
    expect(weatherForSample(loc, { ele: 0 }, T0, 0).gustKmh).toBeCloseTo(25, 9);
  });

  it("flags times outside the forecast", () => {
    const loc = makeLoc();
    const outside = (tMs) => weatherForSample(loc, { ele: 0 }, tMs, 0).outsideForecast;
    expect(outside(T0)).toBe(false);
    expect(outside(T0 + 2 * HOUR)).toBe(false);
    expect(outside(T0 - 1)).toBe(true);
    expect(outside(T0 + 2 * HOUR + 1)).toBe(true);
    expect(outside(NaN)).toBe(true);
  });

  it("falls back to the cell's terrain height without an elevation", () => {
    const loc = makeLoc({
      ...windy,
      cellElev: 300,
      temperature_2m: 7,
      geopotential_height_925hPa: 1000,
      temperature_925hPa: 1,
    });
    for (const ele of [undefined, null, NaN]) {
      const w = weatherForSample(loc, { index: null, lat: 54, lon: -3, ele }, T0, 0);
      expect(w.eleUsed).toBe(300);
      expect(w.tempC).toBeCloseTo(w.model2mTempC, 10);
      expect(w.tempC).toBeCloseTo(7, 10);
      expect(w.windKmh).toBeCloseTo(w.model10mWindKmh, 10);
    }
  });

  it("reports wind relative to the bearing, and nulls without one", () => {
    const loc = makeLoc(windy); // from the west
    const east = weatherForSample(loc, { ele: 0 }, T0, 90);
    expect(east.headKmh).toBeCloseTo(-20, 9);
    expect(east.crossKmh).toBeCloseTo(0, 9);
    const none = weatherForSample(loc, { ele: 0 }, T0, null);
    expect(none.headKmh).toBeNull();
    expect(none.crossKmh).toBeNull();
  });

  it("takes precipitation, probability and code from the hour bucket", () => {
    const loc = makeLoc({
      precipitation: [0, 0.4, 2.5],
      precipitation_probability: [10, 40, 90],
      weather_code: [3, 61, 63],
    });
    const atTen = weatherForSample(loc, { ele: 0 }, T0 + HOUR, 0);
    expect([atTen.precipMm, atTen.precipProb, atTen.code]).toEqual([0.4, 40, 61]);
    const atTenTwenty = weatherForSample(loc, { ele: 0 }, T0 + HOUR + 20 * 60 * 1000, 0);
    expect([atTenTwenty.precipMm, atTenTwenty.precipProb, atTenTwenty.code]).toEqual([2.5, 90, 63]);
  });

  describe("on the real fixture", () => {
    const [upland, lowland] = FIXTURE.map(normaliseLocation);
    const t = Date.UTC(2026, 9, 26, 11, 30);

    it("returns finite numbers for every field", () => {
      const cases = [
        [upland, { ele: 950 }, 90],
        [upland, { ele: 300 }, 200],
        [lowland, { ele: 20 }, 0],
        [lowland, {}, 45],
      ];
      for (const [loc, sample, bearing] of cases) {
        const { outsideForecast, aboveProfile, sunEstimated, ...numbers } = weatherForSample(
          loc, sample, t, bearing,
        );
        expect(outsideForecast).toBe(false);
        expect(aboveProfile).toBe(false);
        expect(sunEstimated).toBe(false);
        for (const [key, value] of Object.entries(numbers)) {
          expect(Number.isFinite(value), `${key} = ${value}`).toBe(true);
        }
      }
    });

    it("keeps gusts at altitude plausible", () => {
      // 11:30 is in the 11–12 bucket: gust 69.8, mean 10 m wind
      // (35.6 + 33.8) / 2 = 34.7, so the excess is 35.1.
      const summit = weatherForSample(upland, { ele: 950 }, t, 90);
      expect(summit.gustKmh).toBeCloseTo(summit.windKmh + 35.1, 9);
      // A gust RATIO would have given ~124 km/h here.
      expect(summit.gustKmh).toBeLessThan(110);
      expect(weatherForSample(lowland, { ele: 300 }, t, 90).gustKmh).toBeLessThan(75);
    });

    it("drops the upland cell's underground 1000 hPa level", () => {
      // 1000 hPa sits at -13/-26 m at 11:00/12:00, under a 509 m cell.
      const profile = profileAt(upland, t);
      expect(profile.temps[0].heightM).toBe(511);
      expect(profile.temps.every((p, i) => i === 0 || p.heightM > 519)).toBe(true);
      expect(profile.temps).toHaveLength(4);
    });

    it("keeps the lowland cell's 1000 hPa level above its 18 m terrain", () => {
      expect(profileAt(lowland, t).temps).toHaveLength(5);
    });

    it("lands a summit between the surface and the levels above it", () => {
      const profile = profileAt(upland, t);
      const w = weatherForSample(upland, { ele: 950 }, t, 90);
      const [low, high] = [profile.temps[1].tempC, profile.temps[2].tempC]
        .sort((a, b) => a - b);
      // 952 m sits between the 925 hPa (~636 m) and 850 hPa (~1333 m) levels.
      expect(w.tempC).toBeGreaterThanOrEqual(low);
      expect(w.tempC).toBeLessThanOrEqual(high);
      expect(w.eleUsed).toBe(950);
      expect(w.cellElev).toBe(509);
    });
  });
});

describe("isAboveProfile", () => {
  // Only 700 hPa above ground, at 3000 m.
  const loc = makeLoc({ geopotential_height_700hPa: 3000 });
  const profile = profileAt(loc, T0);

  it("is false below the top level", () => {
    expect(isAboveProfile(profile, 2000)).toBe(false);
    expect(isAboveProfile(profile, 2990)).toBe(false);
  });

  it("is true once the 10 m wind height passes the top level", () => {
    expect(isAboveProfile(profile, 2991)).toBe(true);
    expect(isAboveProfile(profile, 4000)).toBe(true);
  });

  it("is false without an elevation or any pressure level", () => {
    expect(isAboveProfile(profile, NaN)).toBe(false);
    expect(isAboveProfile(profileAt(makeLoc(), T0), 4000)).toBe(false);
  });

  it("is reported per sample", () => {
    expect(weatherForSample(loc, { ele: 3500 }, T0, 0).aboveProfile).toBe(true);
    expect(weatherForSample(loc, { ele: 500 }, T0, 0).aboveProfile).toBe(false);
  });
});

describe("apparentTemperatureC", () => {
  it("follows the BoM formula", () => {
    // e = 0.5 × 6.105 × exp(17.27 × 20 / 257.7) = 11.66 hPa; calm.
    expect(apparentTemperatureC(20, 50, 0)).toBeCloseTo(20 + 0.33 * 11.661 - 4, 2);
    // e = 0.8 × 6.105 = 4.884 hPa at 0 °C; 36 km/h is 10 m/s.
    expect(apparentTemperatureC(0, 80, 36)).toBeCloseTo(0.33 * 4.884 - 7 - 4, 2);
  });

  it("reads 4 °C below the air in calm, dry air", () => {
    expect(apparentTemperatureC(10, 0, 0)).toBeCloseTo(6, 9);
  });

  it("clamps humidity and is NaN for missing input", () => {
    expect(apparentTemperatureC(10, 120, 0)).toBe(apparentTemperatureC(10, 100, 0));
    expect(apparentTemperatureC(NaN, 50, 0)).toBeNaN();
    expect(apparentTemperatureC(10, NaN, 0)).toBeNaN();
    expect(apparentTemperatureC(10, 50, NaN)).toBeNaN();
  });

  it("uses the altitude-corrected temperature and wind per sample", () => {
    const loc = makeLoc({ temperature_2m: 5, wind_speed_10m: 18, relative_humidity_2m: [60, 80] });
    const w = weatherForSample(loc, { ele: 0 }, T0 + HOUR / 2, 0);
    expect(w.rhPct).toBe(70);
    expect(w.feelsLikeC).toBeCloseTo(apparentTemperatureC(w.tempC, 70, w.windKmh), 9);
    expect(w.feelsLikeC).toBeLessThan(w.tempC);
  });
});

describe("sunshineAt", () => {
  // T0 is 09:00 UTC on 26 Oct; at 54°N, 3°W the sun is well up by then.
  const LAT = 54;
  const LON = -3;

  it("uses the forecast's own sunshine, in minutes", () => {
    const loc = makeLoc({ sunshine_duration: [0, 1800, 3600] });
    // 09:20 reads the 10:00 bucket (09–10).
    expect(sunshineAt(loc, T0 + HOUR / 3, LAT, LON)).toEqual({ sunMin: 30, sunEstimated: false });
    expect(sunshineAt(loc, T0 + 2 * HOUR, LAT, LON).sunMin).toBe(60);
  });

  it("estimates from cloud and daylight where the forecast has none", () => {
    const loc = makeLoc({ sunshine_duration: NaN, cloud_cover: [40, 60, 80] });
    // 09–10 bucket: fully light, mean cloud 50% -> half the hour.
    const day = sunshineAt(loc, T0 + HOUR / 3, LAT, LON);
    expect(day.sunEstimated).toBe(true);
    expect(day.sunMin).toBeCloseTo(daylightMinutes(T0, T0 + HOUR, LAT, LON) * 0.5, 9);
    expect(day.sunMin).toBeCloseTo(30, 9);
  });

  it("estimates no sun at night and nothing without cloud cover", () => {
    const night = makeLoc({ sunshine_duration: NaN, cloud_cover: 0 });
    const midnight = { ...night, timesMs: night.timesMs.map((t) => t - 9 * HOUR) };
    expect(sunshineAt(midnight, T0 - 9 * HOUR + HOUR / 2, LAT, LON).sunMin).toBe(0);
    const gap = makeLoc({ sunshine_duration: NaN, cloud_cover: NaN });
    expect(sunshineAt(gap, T0 + HOUR / 2, LAT, LON)).toEqual({ sunMin: NaN, sunEstimated: false });
  });

  it("is reported per sample with cloud cover", () => {
    const loc = makeLoc({ sunshine_duration: 600, cloud_cover: [20, 40, 60] });
    const w = weatherForSample(loc, { ele: 10, lat: LAT, lon: LON }, T0 + HOUR / 2, 0);
    expect(w.cloudPct).toBe(30);
    expect(w.sunMin).toBe(10);
    expect(w.sunEstimated).toBe(false);
  });
});

describe("describeWeather", () => {
  it("labels known codes", () => {
    expect(describeWeather(0)).toBe("Clear sky");
    expect(describeWeather(61)).toBe("Light rain");
    expect(describeWeather(95)).toBe(WMO_DESCRIPTIONS[95]);
  });

  it("returns Unknown for codes outside the table", () => {
    expect(describeWeather(42)).toBe("Unknown");
  });

  it("returns the missing placeholder for gaps", () => {
    expect(describeWeather(NaN)).toBe(MISSING);
    expect(describeWeather(null)).toBe(MISSING);
    expect(describeWeather(undefined)).toBe(MISSING);
  });
});

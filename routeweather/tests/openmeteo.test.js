import { readFileSync } from "node:fs";

import { describe, it, expect, beforeAll, afterAll } from "vitest";

import {
  buildForecastUrl, buildElevationUrl, fetchForecast, fetchElevations, normaliseLocation,
  CHUNK_SIZE, ELEVATION_CHUNK_SIZE, FORECAST_URL, ELEVATION_URL, REQUEST_TIMEOUT_MS,
} from "../js/openmeteo.js";
import { HOURLY_VARS } from "../js/config.js";

// Real Open-Meteo response for an upland (Lake District) and a lowland
// (London) point, 2026-10-26 09:00–14:00 UTC, every HOURLY_VARS variable.
const FIXTURE = JSON.parse(
  readFileSync(new URL("./fixtures/forecast-2loc.json", import.meta.url), "utf8"),
);

const RANGE = {
  startHour: Date.UTC(2026, 9, 26, 9, 30),
  endHour: Date.UTC(2026, 9, 26, 13, 10),
};

// Run in a zone that is never UTC (and has no DST), so parsing the API's
// zone-less times as local time instead of UTC fails on any machine, including
// a UTC one. Node applies TZ changes at runtime.
let savedTz;
beforeAll(() => {
  savedTz = process.env.TZ;
  process.env.TZ = "Asia/Kolkata";
});
afterAll(() => {
  if (savedTz === undefined) delete process.env.TZ;
  else process.env.TZ = savedTz;
});

const listParam = (url, name) => new URL(url).searchParams.get(name).split(",");

// A minimal valid location object for (lat, lon): one hourly step, every
// variable present. `id` is its index in the request; like the real API, the
// first location has no `location_id`.
function fakeLocation(lat, lon, id = 0) {
  const hourly = { time: ["2026-10-26T09:00"] };
  for (const name of HOURLY_VARS) hourly[name] = [1];
  const loc = { latitude: lat, longitude: lon, elevation: 100, hourly };
  return id === 0 ? loc : { ...loc, location_id: id };
}

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), { status });

// Mock fetch that answers each forecast URL with one fake location per
// requested coordinate — a bare object for a single location, as the real API
// does — and records the URLs it saw and the peak number of requests in flight.
function forecastFetch() {
  const calls = [];
  let inFlight = 0;
  let maxInFlight = 0;
  const impl = async (url, options) => {
    calls.push({ url, signal: options?.signal });
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 0));
    inFlight--;
    const lats = listParam(url, "latitude").map(Number);
    const lons = listParam(url, "longitude").map(Number);
    const locations = lats.map((lat, i) => fakeLocation(lat, lons[i], i));
    return jsonResponse(locations.length === 1 ? locations[0] : locations);
  };
  return { impl, calls, maxInFlight: () => maxInFlight };
}

const samplesN = (n) => Array.from({ length: n }, (_, i) => ({ lat: 50 + i * 0.01, lon: -3 }));

describe("buildForecastUrl", () => {
  const samples = [{ lat: 54.454, lon: -3.211 }, { lat: 51.5, lon: -0.12 }];
  const url = buildForecastUrl(samples, RANGE);
  const params = new URL(url).searchParams;

  it("targets the forecast endpoint", () => {
    expect(url.startsWith(`${FORECAST_URL}?`)).toBe(true);
  });

  it("lists latitudes and longitudes in sample order", () => {
    expect(params.get("latitude")).toBe("54.454,51.5");
    expect(params.get("longitude")).toBe("-3.211,-0.12");
  });

  it("rounds coordinates to 5 decimals", () => {
    const u = buildForecastUrl([{ lat: 54.4541234567, lon: -3.2119999999 }], RANGE);
    expect(new URL(u).searchParams.get("latitude")).toBe("54.45412");
    expect(new URL(u).searchParams.get("longitude")).toBe("-3.212");
  });

  it("asks for elevation=nan once per location", () => {
    expect(params.get("elevation")).toBe("nan,nan");
    const three = buildForecastUrl(samplesN(3), RANGE);
    expect(new URL(three).searchParams.get("elevation")).toBe("nan,nan,nan");
  });

  it("sets land cells, the UKMO model and GMT", () => {
    expect(params.get("cell_selection")).toBe("land");
    expect(params.get("models")).toBe("ukmo_seamless");
    expect(params.get("timezone")).toBe("GMT");
  });

  it("requests every hourly variable", () => {
    expect(params.get("hourly")).toBe(HOURLY_VARS.join(","));
  });

  it("floors the start and ceils the end to whole UTC hours", () => {
    expect(params.get("start_hour")).toBe("2026-10-26T09:00");
    expect(params.get("end_hour")).toBe("2026-10-26T14:00");
  });

  it("keeps times already on the hour", () => {
    const u = buildForecastUrl(samples, {
      startHour: Date.UTC(2026, 9, 26, 9),
      endHour: Date.UTC(2026, 9, 26, 14),
    });
    expect(new URL(u).searchParams.get("start_hour")).toBe("2026-10-26T09:00");
    expect(new URL(u).searchParams.get("end_hour")).toBe("2026-10-26T14:00");
  });

  it("does not use forecast_days (start/end hour replace it)", () => {
    expect(params.has("forecast_days")).toBe(false);
  });

  it("throws on an invalid window", () => {
    expect(() => buildForecastUrl(samples, { startHour: NaN, endHour: 0 })).toThrow(/window/);
    expect(() => buildForecastUrl(samples, { startHour: 2, endHour: 1 })).toThrow(/window/);
  });
});

describe("buildElevationUrl", () => {
  it("lists coordinates against the elevation endpoint", () => {
    const url = buildElevationUrl([{ lat: 54.454, lon: -3.211 }, { lat: 51.5, lon: -0.12 }]);
    expect(url).toBe(`${ELEVATION_URL}?latitude=54.454,51.5&longitude=-3.211,-0.12`);
  });
});

describe("fetchForecast", () => {
  it(`splits ${CHUNK_SIZE + 1} samples into 2 sequential requests, results in order`, async () => {
    const samples = samplesN(CHUNK_SIZE + 1);
    const mock = forecastFetch();
    const locations = await fetchForecast(samples, RANGE, mock.impl);

    expect(mock.calls).toHaveLength(2);
    expect(listParam(mock.calls[0].url, "latitude")).toHaveLength(CHUNK_SIZE);
    expect(listParam(mock.calls[1].url, "latitude")).toHaveLength(1);
    expect(mock.maxInFlight()).toBe(1);
    expect(locations).toHaveLength(samples.length);
    locations.forEach((loc, i) => expect(loc.cellLat).toBeCloseTo(samples[i].lat, 5));
  });

  it("normalises a single-object response into a one-element array", async () => {
    const mock = forecastFetch();
    const locations = await fetchForecast([{ lat: 51.5, lon: -0.12 }], RANGE, mock.impl);
    expect(locations).toHaveLength(1);
    expect(locations[0].cellLat).toBe(51.5);
    expect(locations[0].vars.temperature_2m[0]).toBe(1);
  });

  it("makes no requests for no samples", async () => {
    const mock = forecastFetch();
    expect(await fetchForecast([], RANGE, mock.impl)).toEqual([]);
    expect(mock.calls).toHaveLength(0);
  });

  it("passes a timeout signal with every request", async () => {
    const mock = forecastFetch();
    await fetchForecast(samplesN(CHUNK_SIZE + 1), RANGE, mock.impl);
    for (const call of mock.calls) expect(call.signal).toBeInstanceOf(AbortSignal);
  });

  it("throws a timed-out message when the request times out or aborts", async () => {
    for (const name of ["TimeoutError", "AbortError"]) {
      const impl = async () => {
        throw new DOMException("The operation was aborted", name);
      };
      await expect(fetchForecast(samplesN(1), RANGE, impl))
        .rejects.toThrow(`timed out after ${REQUEST_TIMEOUT_MS / 1000} s`);
    }
  });

  it("throws a timed-out message when the body read times out", async () => {
    // The timeout signal also aborts reading the body after the headers arrive.
    const impl = async () => new Response(new ReadableStream({
      start(controller) {
        controller.error(new DOMException("The operation timed out", "TimeoutError"));
      },
    }));
    await expect(fetchForecast(samplesN(1), RANGE, impl))
      .rejects.toThrow(`forecast request timed out after ${REQUEST_TIMEOUT_MS / 1000} s`);
  });

  it("throws when locations come back out of order", async () => {
    const impl = async () => jsonResponse([fakeLocation(50, -3, 1), fakeLocation(50.01, -3, 0)]);
    await expect(fetchForecast(samplesN(2), RANGE, impl)).rejects.toThrow(/out of order/);
  });

  it("throws the API's reason on an error response", async () => {
    const impl = async () => (
      jsonResponse({ error: true, reason: "Latitude must be in range" }, 400)
    );
    await expect(fetchForecast(samplesN(1), RANGE, impl))
      .rejects.toThrow(/HTTP 400.*Latitude must be in range/);
  });

  it("throws the API's reason even on an HTTP 200 error body", async () => {
    const impl = async () => jsonResponse({ error: true, reason: "Bad hourly" });
    await expect(fetchForecast(samplesN(1), RANGE, impl)).rejects.toThrow(/Bad hourly/);
  });

  it("throws a rate-limit message on HTTP 429", async () => {
    const impl = async () => new Response("Too many requests", { status: 429 });
    await expect(fetchForecast(samplesN(1), RANGE, impl))
      .rejects.toThrow(/rate limited.*try again in a minute/);
  });

  it("throws on a non-JSON error page", async () => {
    const impl = async () => new Response("<html>Bad gateway</html>", { status: 502 });
    await expect(fetchForecast(samplesN(1), RANGE, impl)).rejects.toThrow(/HTTP 502/);
  });

  it("wraps network failures", async () => {
    const impl = async () => {
      throw new TypeError("fetch failed");
    };
    await expect(fetchForecast(samplesN(1), RANGE, impl))
      .rejects.toThrow(/Could not reach Open-Meteo.*fetch failed/);
  });

  it("throws when the location count does not match the request", async () => {
    const impl = async () => jsonResponse([fakeLocation(50, -3)]);
    await expect(fetchForecast(samplesN(2), RANGE, impl))
      .rejects.toThrow(/returned 1 forecast locations, expected 2/);
  });
});

describe("normaliseLocation", () => {
  const [upland, lowland] = FIXTURE.map(normaliseLocation);

  it("keeps the grid cell's centre and terrain height", () => {
    expect(upland.cellLat).toBeCloseTo(54.448, 3);
    expect(upland.cellLon).toBeCloseTo(-3.211, 3);
    expect(upland.cellElev).toBe(509);
    expect(lowland.cellElev).toBe(18);
  });

  it("parses times as UTC", () => {
    expect(upland.timesMs).toHaveLength(6);
    expect(upland.timesMs[0]).toBe(Date.UTC(2026, 9, 26, 9));
    expect(upland.timesMs[5]).toBe(Date.UTC(2026, 9, 26, 14));
  });

  it("parses summer times as UTC too, not local (BST) time", () => {
    // The fixture's October date is GMT in Europe/London, so it can't tell UTC
    // from local parsing on a UK machine; a July date can (and the file-wide
    // Asia/Kolkata zone covers machines set to UTC).
    const json = structuredClone(FIXTURE[0]);
    json.hourly.time = ["2026-07-15T12:00", "2026-07-15T13:00", "2026-07-15T14:00",
      "2026-07-15T15:00", "2026-07-15T16:00", "2026-07-15T17:00"];
    const loc = normaliseLocation(json);
    expect(loc.timesMs[0]).toBe(Date.UTC(2026, 6, 15, 12));
    expect(loc.timesMs[5]).toBe(Date.UTC(2026, 6, 15, 17));
  });

  it("returns a Float64Array per requested variable, parallel to the times", () => {
    for (const name of HOURLY_VARS) {
      expect(upland.vars[name]).toBeInstanceOf(Float64Array);
      expect(upland.vars[name]).toHaveLength(6);
    }
    expect(upland.vars.temperature_2m[0]).toBe(FIXTURE[0].hourly.temperature_2m[0]);
    expect(upland.vars.geopotential_height_1000hPa[2]).toBe(-13);
  });

  it("turns nulls into NaN, not 0", () => {
    const json = structuredClone(FIXTURE[1]);
    json.hourly.temperature_2m[3] = null;
    json.hourly.precipitation[0] = null;
    const loc = normaliseLocation(json);
    expect(loc.vars.temperature_2m[3]).toBeNaN();
    expect(loc.vars.precipitation[0]).toBeNaN();
    expect(loc.vars.temperature_2m[2]).toBe(json.hourly.temperature_2m[2]);
  });

  it("throws on a missing or mis-sized variable", () => {
    const missing = structuredClone(FIXTURE[0]);
    delete missing.hourly.wind_speed_850hPa;
    expect(() => normaliseLocation(missing)).toThrow(/wind_speed_850hPa/);
    const short = structuredClone(FIXTURE[0]);
    short.hourly.temperature_2m.pop();
    expect(() => normaliseLocation(short)).toThrow(/temperature_2m/);
  });

  it("throws without hourly times or with unparseable ones", () => {
    expect(() => normaliseLocation({ latitude: 1 })).toThrow(/hourly times/);
    const bad = structuredClone(FIXTURE[0]);
    bad.hourly.time[1] = "not a time";
    expect(() => normaliseLocation(bad)).toThrow(/unparseable/);
  });
});

describe("fetchElevations", () => {
  it(`splits ${ELEVATION_CHUNK_SIZE + 1} points into 2 requests, results in order`, async () => {
    const points = samplesN(ELEVATION_CHUNK_SIZE + 1);
    const calls = [];
    const impl = async (url) => {
      calls.push(url);
      // Elevation = latitude * 10, so order is checkable.
      const lats = listParam(url, "latitude");
      return jsonResponse({ elevation: lats.map((lat) => Number(lat) * 10) });
    };
    const elevations = await fetchElevations(points, impl);

    expect(calls).toHaveLength(2);
    expect(calls[0].startsWith(`${ELEVATION_URL}?`)).toBe(true);
    expect(listParam(calls[0], "latitude")).toHaveLength(ELEVATION_CHUNK_SIZE);
    expect(listParam(calls[1], "latitude")).toHaveLength(1);
    expect(elevations).toHaveLength(points.length);
    elevations.forEach((ele, i) => expect(ele).toBeCloseTo(points[i].lat * 10, 6));
  });

  it("turns null elevations into NaN", async () => {
    const impl = async () => jsonResponse({ elevation: [12, null] });
    const elevations = await fetchElevations(samplesN(2), impl);
    expect(elevations[0]).toBe(12);
    expect(elevations[1]).toBeNaN();
  });

  it("throws on a count mismatch", async () => {
    const impl = async () => jsonResponse({ elevation: [12] });
    await expect(fetchElevations(samplesN(2), impl))
      .rejects.toThrow(/returned 1 elevations, expected 2/);
  });

  it("throws the API's reason and the rate-limit message", async () => {
    const bad = async () => jsonResponse({ error: true, reason: "Too many coordinates" }, 400);
    await expect(fetchElevations(samplesN(1), bad)).rejects.toThrow(/Too many coordinates/);
    const limited = async () => new Response("", { status: 429 });
    await expect(fetchElevations(samplesN(1), limited)).rejects.toThrow(/try again in a minute/);
  });
});

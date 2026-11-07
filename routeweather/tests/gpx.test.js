// @vitest-environment jsdom
import { describe, it, expect } from "vitest";

import { parseGpx } from "../js/gpx.js";

// Wraps a GPX body in a GPX 1.1 root (default namespace).
const gpx11 = (body) => `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
${body}
</gpx>`;

const pt = (tag, lat, lon, ele) =>
  `<${tag} lat="${lat}" lon="${lon}">${ele === undefined ? "" : `<ele>${ele}</ele>`}</${tag}>`;

// A one-segment track holding the given point (or other) markup, in GPX 1.1.
const track = (...parts) => gpx11(`<trk><trkseg>${parts.join("")}</trkseg></trk>`);

describe("parseGpx", () => {
  describe("tracks", () => {
    it("concatenates points from multiple segments in order", () => {
      const result = parseGpx(gpx11(`
        <trk><name>Two Segments</name>
          <trkseg>${pt("trkpt", 51.0, -1.0, 10)}${pt("trkpt", 51.1, -1.1, 20)}</trkseg>
          <trkseg>${pt("trkpt", 51.2, -1.2, 30)}</trkseg>
        </trk>`));
      expect(result).toEqual({
        name: "Two Segments",
        points: [
          { lat: 51.0, lon: -1.0, ele: 10 },
          { lat: 51.1, lon: -1.1, ele: 20 },
          { lat: 51.2, lon: -1.2, ele: 30 },
        ],
        hasElevation: true,
      });
    });

    it("concatenates points from multiple tracks in order, taking the first track's name", () => {
      const result = parseGpx(gpx11(`
        <trk><name>First</name><trkseg>${pt("trkpt", 1, 1, 1)}</trkseg></trk>
        <trk><name>Second</name><trkseg>${pt("trkpt", 2, 2, 2)}</trkseg></trk>`));
      expect(result.points.map((p) => p.lat)).toEqual([1, 2]);
      expect(result.name).toBe("First");
    });

    it("prefers track points over route points when both exist", () => {
      const result = parseGpx(gpx11(`
        <rte><name>Route</name>${pt("rtept", 9, 9)}</rte>
        <trk><name>Track</name><trkseg>${pt("trkpt", 1, 1)}</trkseg></trk>`));
      expect(result.points).toEqual([{ lat: 1, lon: 1, ele: undefined }]);
      expect(result.name).toBe("Track");
    });
  });

  describe("routes", () => {
    it("falls back to route points when there is no track", () => {
      const result = parseGpx(gpx11(`
        <rte><name>A Route</name>${pt("rtept", 50.5, 0.5, 5)}${pt("rtept", 50.6, 0.6, 6)}</rte>`));
      expect(result).toEqual({
        name: "A Route",
        points: [
          { lat: 50.5, lon: 0.5, ele: 5 },
          { lat: 50.6, lon: 0.6, ele: 6 },
        ],
        hasElevation: true,
      });
    });

    it("falls back to route points when tracks exist but are empty", () => {
      const result = parseGpx(gpx11(`
        <trk><name>Empty</name><trkseg></trkseg></trk>
        <rte><name>Real</name>${pt("rtept", 1, 2)}</rte>`));
      expect(result.points).toEqual([{ lat: 1, lon: 2, ele: undefined }]);
      expect(result.name).toBe("Real");
    });
  });

  describe("names", () => {
    it("returns null when the track has no name", () => {
      expect(parseGpx(track(pt("trkpt", 1, 1))).name).toBeNull();
    });

    it("returns null for a blank name", () => {
      const body = `<trk><name>  </name><trkseg>${pt("trkpt", 1, 1)}</trkseg></trk>`;
      expect(parseGpx(gpx11(body)).name).toBeNull();
    });

    it("trims whitespace around the name", () => {
      const body = `<trk><name>\n  Ridge Walk \n</name><trkseg>${pt("trkpt", 1, 1)}</trkseg></trk>`;
      expect(parseGpx(gpx11(body)).name).toBe("Ridge Walk");
    });

    it("ignores metadata and point names", () => {
      const result = parseGpx(gpx11(`
        <metadata><name>Metadata</name></metadata>
        <trk><trkseg><trkpt lat="1" lon="1"><name>Point</name></trkpt></trkseg></trk>`));
      expect(result.name).toBeNull();
    });

    it("skips an unnamed first track for a named later one", () => {
      const result = parseGpx(gpx11(`
        <trk><trkseg>${pt("trkpt", 1, 1)}</trkseg></trk>
        <trk><name>Later</name><trkseg>${pt("trkpt", 2, 2)}</trkseg></trk>`));
      expect(result.name).toBe("Later");
    });
  });

  describe("elevation", () => {
    it("leaves ele undefined and hasElevation false when no point has <ele>", () => {
      const result = parseGpx(track(pt("trkpt", 1, 1), pt("trkpt", 2, 2)));
      expect(result.hasElevation).toBe(false);
      expect(result.points).toEqual([
        { lat: 1, lon: 1, ele: undefined },
        { lat: 2, lon: 2, ele: undefined },
      ]);
      expect(result.points.every((p) => p.ele === undefined)).toBe(true);
    });

    it("treats partial elevation as no elevation but keeps the values it has", () => {
      const result = parseGpx(track(pt("trkpt", 1, 1, 100), pt("trkpt", 2, 2)));
      expect(result.hasElevation).toBe(false);
      expect(result.points.map((p) => p.ele)).toEqual([100, undefined]);
    });

    it("keeps an elevation of 0 distinct from a missing one", () => {
      const result = parseGpx(track(pt("trkpt", 1, 1, 0), pt("trkpt", 2, 2, -3.5)));
      expect(result.hasElevation).toBe(true);
      expect(result.points.map((p) => p.ele)).toEqual([0, -3.5]);
    });

    it.each([
      ["empty", ""],
      ["whitespace", "   "],
      ["non-numeric", "high"],
      ["trailing junk", "12m"],
    ])("treats an %s <ele> as missing", (_, text) => {
      const result = parseGpx(track(pt("trkpt", 1, 1, text)));
      expect(result.points[0].ele).toBeUndefined();
      expect(result.hasElevation).toBe(false);
    });

    it("trims whitespace around an elevation", () => {
      const result = parseGpx(track(pt("trkpt", 1, 1, " 42.5\n")));
      expect(result.points[0].ele).toBe(42.5);
    });
  });

  describe("namespaces", () => {
    it("parses GPX 1.0", () => {
      const result = parseGpx(`<gpx version="1.0" xmlns="http://www.topografix.com/GPX/1/0">
        <trk><trkseg>${pt("trkpt", 1, 2, 3)}</trkseg></trk></gpx>`);
      expect(result.points).toEqual([{ lat: 1, lon: 2, ele: 3 }]);
    });

    it("parses GPX with no namespace", () => {
      const result = parseGpx(`<gpx><trk><trkseg>${pt("trkpt", 1, 2)}</trkseg></trk></gpx>`);
      expect(result.points).toEqual([{ lat: 1, lon: 2, ele: undefined }]);
    });

    it("parses prefixed GPX elements", () => {
      const result = parseGpx(`<g:gpx xmlns:g="http://www.topografix.com/GPX/1/1">
        <g:trk><g:name>Prefixed</g:name><g:trkseg>
          <g:trkpt lat="1" lon="2"><g:ele>3</g:ele></g:trkpt>
        </g:trkseg></g:trk></g:gpx>`);
      expect(result).toEqual({
        name: "Prefixed",
        points: [{ lat: 1, lon: 2, ele: 3 }],
        hasElevation: true,
      });
    });

    it("ignores extension elements alongside the points", () => {
      const result = parseGpx(gpx11(`<trk><trkseg>
        <trkpt lat="1" lon="2"><ele>3</ele><time>2026-10-25T08:00:00Z</time>
          <extensions><hr xmlns="urn:example">140</hr></extensions></trkpt>
      </trkseg></trk>`));
      expect(result.points).toEqual([{ lat: 1, lon: 2, ele: 3 }]);
    });
  });

  describe("errors", () => {
    it.each([
      ["unclosed tags", `<gpx><trk><trkseg><trkpt lat="1" lon="1">`],
      ["not XML at all", "this is not a gpx file"],
      ["an empty string", ""],
    ])("throws on malformed XML (%s)", (_, text) => {
      expect(() => parseGpx(text)).toThrow(/not valid XML/);
    });

    it("reports the message inside a Chromium-style <parsererror>, not its heading", () => {
      // Chromium nests its error in the partial document as an XHTML
      // <parsererror> with an <h3> heading and the detail in a <div>.
      const text = `<gpx><parsererror xmlns="http://www.w3.org/1999/xhtml">
        <h3>This page contains the following errors:</h3>
        <div>error on line 1 at column 23: Extra content at the end of the document</div>
        <h3>Below is a rendering of the page up to the first error.</h3>
      </parsererror></gpx>`;
      expect(() => parseGpx(text)).toThrow(
        "GPX file is not valid XML: error on line 1 at column 23: "
          + "Extra content at the end of the document",
      );
    });

    it("throws a descriptive error when there are no track or route points", () => {
      expect(() => parseGpx(gpx11(`<trk><name>Empty</name><trkseg></trkseg></trk>`)))
        .toThrow("GPX file has no track or route points");
      expect(() => parseGpx(gpx11(`<wpt lat="1" lon="1"/>`))).toThrow(/no track or route points/);
    });

    it.each([
      ["missing lat", `<trkpt lon="1"/>`, /point 1 .*lat/],
      ["missing lon", `<trkpt lat="1"/>`, /point 1 .*lon/],
      ["empty lat", `<trkpt lat="" lon="1"/>`, /lat/],
      ["non-numeric lon", `<trkpt lat="1" lon="east"/>`, /lon/],
      ["lat with trailing junk", `<trkpt lat="12abc" lon="1"/>`, /lat/],
      ["lat out of range", `<trkpt lat="91" lon="1"/>`, /lat/],
      ["lon out of range", `<trkpt lat="1" lon="-180.5"/>`, /lon/],
    ])("throws on a point with %s", (_, point, message) => {
      expect(() => parseGpx(track(point))).toThrow(message);
    });

    it("names the offending point by position", () => {
      const text = track(pt("trkpt", 1, 1), pt("trkpt", 2, 2), `<trkpt lat="3"/>`);
      expect(() => parseGpx(text)).toThrow(/point 3 /);
    });

    it("accepts the coordinate range bounds", () => {
      const result = parseGpx(track(pt("trkpt", -90, -180), pt("trkpt", 90, 180)));
      expect(result.points.map((p) => [p.lat, p.lon])).toEqual([[-90, -180], [90, 180]]);
    });
  });
});

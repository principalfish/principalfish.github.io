// GPX parsing: turns a GPX file's text into a flat list of route points.
//
// Elements are matched by LOCAL name in any namespace
// (`getElementsByTagNameNS("*", name)`), so GPX 1.0, 1.1, un-namespaced and
// prefixed (`<gpx:trkpt>`) files all parse the same way. Plain
// `getElementsByTagName` matches the qualified name and misses prefixed files.

import { hasFullElevation } from "./route.js";

// --- parsing ---

// Parses GPX text into `{ name, points, hasElevation }`.
//
// Points come from every `<trkpt>` in document order (all tracks, all
// segments, concatenated); if the file has none, from `<rtept>` instead. Each
// point is `{ lat, lon, ele }`, where `ele` is metres or `undefined` when the
// point has no usable `<ele>`. It is never defaulted to 0, so "no elevation"
// stays distinct from "sea level".
//
// Throws on malformed XML, on a file with no track or route points, and on any
// point with a missing, non-numeric or out-of-range lat/lon.
export function parseGpx(text) {
  const doc = new DOMParser().parseFromString(String(text ?? ""), "application/xml");
  // Firefox and jsdom make <parsererror> the root; Chromium nests it inside
  // whatever it managed to parse, so search the whole document.
  const parserError = doc.getElementsByTagNameNS("*", "parsererror")[0];
  if (parserError) {
    // Chromium wraps the actual message in a <div> after a "This page contains
    // the following errors:" heading; jsdom/Firefox give plain text.
    const message = parserError.querySelector("div")?.textContent ?? parserError.textContent;
    const detail = message.trim().split("\n")[0];
    throw new Error(`GPX file is not valid XML${detail ? `: ${detail}` : ""}`);
  }

  let pointEls = byLocalName(doc, "trkpt");
  let containerTag = "trk";
  if (pointEls.length === 0) {
    pointEls = byLocalName(doc, "rtept");
    containerTag = "rte";
  }
  if (pointEls.length === 0) {
    throw new Error("GPX file has no track or route points");
  }

  const points = pointEls.map((el, i) => readPoint(el, i));
  return {
    name: readName(doc, containerTag),
    points,
    hasElevation: hasFullElevation(points),
  };
}

// --- helpers ---

function byLocalName(root, name) {
  return Array.from(root.getElementsByTagNameNS("*", name));
}

function childByLocalName(el, name) {
  return Array.from(el.children).find((child) => child.localName === name) ?? null;
}

// Strict numeric parse: rejects empty strings and trailing junk ("12abc"),
// which Number("") / parseFloat would otherwise turn into 0 / 12.
function parseNumber(raw) {
  if (raw == null) return NaN;
  const trimmed = raw.trim();
  return trimmed === "" ? NaN : Number(trimmed);
}

function readPoint(el, i) {
  const lat = parseNumber(el.getAttribute("lat"));
  const lon = parseNumber(el.getAttribute("lon"));
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new Error(`GPX point ${i + 1} has a missing or invalid lat`);
  }
  if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
    throw new Error(`GPX point ${i + 1} has a missing or invalid lon`);
  }
  const eleEl = childByLocalName(el, "ele");
  const ele = eleEl ? parseNumber(eleEl.textContent) : NaN;
  return { lat, lon, ele: Number.isFinite(ele) ? ele : undefined };
}

// The first non-empty direct `<name>` of a `<trk>` (or `<rte>`, matching where
// the points came from). Only direct children count, so a point's or the
// metadata's `<name>` is never picked up by mistake.
function readName(doc, containerTag) {
  for (const container of byLocalName(doc, containerTag)) {
    const nameEl = childByLocalName(container, "name");
    const name = nameEl?.textContent.trim();
    if (name) return name;
  }
  return null;
}

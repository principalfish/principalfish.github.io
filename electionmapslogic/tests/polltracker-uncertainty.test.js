import { describe, expect, it } from "vitest";
import * as d3 from "../../site/vendor/d3.v7.esm.js";
import {
  chanceSeries, drawPollTrackerBands, pollTrackerCapabilities, pollTrackerTooltipValues,
  pollTrackerUncertaintyNote, pollTrackerBandRibbons, smoothPollTrackerBand,
} from "../features/polltracker-uncertainty.js";

const series = {
  partyKey: "1", partyName: "Labour", colour: "#f00",
  seats: [300, null, 0], votePct: [40.12345, null, 0], voteLo: [35.12345, null, 0],
  voteHi: [45.98765, null, 2], pMaj: [0.123456, null, 0], pLargest: [1, null, 0],
};

describe("poll tracker uncertainty display", () => {
  it("finds optional capabilities while retaining valid zero probabilities", () => {
    expect(pollTrackerCapabilities({ seriesByParty: new Map([["1", series]]), hung: [0, null] }))
      .toEqual({ bands: true, seatBands: false, voteBands: true, chances: true });
    expect(pollTrackerCapabilities({ seriesByParty: new Map([["1", { seats: [2], votePct: [3] }]]), hung: [] }))
      .toEqual({ bands: false, seatBands: false, voteBands: false, chances: false });
  });

  it("converts odds once to percentages, switches measure and preserves missing days", () => {
    const majority = chanceSeries([series], "pMaj", [0.876544, null, 1]);
    expect(majority[0].votePct).toEqual([12.3456, null, 0]);
    expect(majority[1].votePct).toEqual([87.6544, null, 100]);
    expect(majority[1].isHung).toBe(true);
    expect(chanceSeries([series], "pLargest", [0])[0].votePct).toEqual([100, null, 0]);
    expect(series.votePct).toEqual([40.12345, null, 0]);
  });

  it("shows vote intervals and chance tooltips at two decimals without inventing missing values", () => {
    expect(pollTrackerTooltipValues(series, 0)).toEqual([
      ["Seats", "300"], ["Vote share", "40.12%"], ["90% vote range", "35.12–45.99%"],
    ]);
    expect(pollTrackerTooltipValues(series, 1)).toEqual([["Seats", "Unavailable"], ["Vote share", "Unavailable"]]);
    expect(pollTrackerTooltipValues(chanceSeries([series], "pMaj", [0])[0], 0))
      .toEqual([["Majority chance", "12.35%"]]);
    expect(pollTrackerTooltipValues(chanceSeries([series], "pLargest", [0])[0], 2))
      .toEqual([["Largest-party chance", "0.00%"]]);
  });

  it("draws separated band paths and visible isolated ranges without joining missing days", () => {
    const drawn = [];
    const plot = { append(type) {
      const item = { type, attrs: {}, values: null };
      drawn.push(item);
      const node = {
        datum(values) { item.values = values; return node; },
        attr(key, value) { item.attrs[key] = typeof value === "function" ? value(item.values) : value; return node; },
      };
      return node;
    } };
    drawPollTrackerBands(plot, [series], (index) => index * 10, (value) => 100 - value, d3.area, "votes");
    const outline = drawn.find((item) => item.attrs.class.includes("maps-polltracker-band--votes"));
    const markers = drawn.filter((item) => item.type === "line");
    expect(markers).toHaveLength(2);
    expect(outline.attrs.d.match(/M/g)).toHaveLength(2);
    expect(outline.attrs.fill).toBe(series.colour);
    expect(outline.attrs.stroke).toBeUndefined();
    expect(markers[0].attrs.y1).toBeCloseTo(64.87655);
    expect(markers[0].attrs.y2).toBeCloseTo(54.01235);
    const ribbons = drawn.filter((item) => item.attrs.class === "maps-polltracker-band-shade");
    expect(ribbons.length).toBeGreaterThan(1);
    expect(ribbons.every((item) => item.attrs.d.match(/M/g).length === 2)).toBe(true);
    expect(ribbons.every((item) => item.attrs.stroke === undefined)).toBe(true);
    drawn.length = 0;
    drawPollTrackerBands(plot, [{ ...series, seatLo: [220, null, 0], seatHi: [376, null, 2] }],
      (index) => index * 10, (value) => 500 - value, d3.area, "seats");
    expect(drawn.some((item) => item.attrs.class.includes("maps-polltracker-band--seats"))).toBe(true);
    const seatMarker = drawn.find((item) => item.type === "line");
    expect(seatMarker.attrs.y1).toBe(280);
    expect(seatMarker.attrs.y2).toBe(124);
  });

  it("smooths bands over at most five past and current steps without bridging gaps", () => {
    const band = [[10, 20], [12, 22], [20, 40], [12, 22], [10, 20], [11, 21], [null, null], [30, 40], [50, 10]];
    const smoothed = smoothPollTrackerBand(band);
    expect(smoothed[2]).toEqual([14, 82 / 3]);
    expect(smoothed[0]).toEqual([10, 20]);
    expect(smoothed[5][0]).toBeCloseTo((12 + 20 + 12 + 10 + 11) / 5);
    expect(smoothed.slice(6)).toEqual([[null, null], [30, 40], [50, 10]]);
    smoothed.slice(0, 6).forEach(([lo, hi]) => expect(lo).toBeLessThanOrEqual(hi));
  });

  it("averages over model updates, holding each carried-forward step flat", () => {
    const band = [[10, 20], [10, 20], [10, 20], [20, 30], [20, 30], [30, 40], [null, null], [null, null], [5, 6], [5, 6]];
    expect(smoothPollTrackerBand(band, 3)).toEqual([
      [10, 20], [10, 20], [10, 20], [15, 25], [15, 25], [20, 30], [null, null], [null, null], [5, 6], [5, 6],
    ]);
  });

  it("never lets appended future updates change earlier bands or the exact tooltip range", () => {
    const band = [[0, 10], [10, 20], [20, 30], [30, 40], [40, 50], [50, 60]];
    const original = structuredClone(band);
    for (let length = 1; length <= band.length; length += 1) {
      expect(smoothPollTrackerBand(band).slice(0, length))
        .toEqual(smoothPollTrackerBand(band.slice(0, length)));
    }
    expect(smoothPollTrackerBand(band).at(-1)).toEqual([30, 40]);
    const row = { ...series, seatLo: band.map(([lo]) => lo), seatHi: band.map(([, hi]) => hi) };
    expect(pollTrackerTooltipValues(row, 1)).toContainEqual(["90% seat range", "10–20"]);
    expect(band).toEqual(original);
  });

  it("fades towards a varying central curve rather than the midpoint of asymmetric bounds", () => {
    const band = [[10, 50], [100, 160]];
    const central = [20, 145];
    const ribbons = pollTrackerBandRibbons(band, central);
    expect(ribbons[0][0][0]).toBeGreaterThan(band[0][0]);
    expect(ribbons[0][0][1]).toBeLessThan(band[0][1]);
    const inner = ribbons.at(-1);
    expect(inner[0][0]).toBeGreaterThan(10);
    expect(inner[0][1]).toBeLessThan(30);
    expect(inner[1][0]).toBeGreaterThan(130);
    ribbons.forEach((ribbon) => ribbon.forEach(([lo, hi], index) => {
      expect(lo).toBeGreaterThanOrEqual(band[index][0]);
      expect(hi).toBeLessThanOrEqual(band[index][1]);
      expect(lo).toBeLessThanOrEqual(central[index]);
      expect(hi).toBeGreaterThanOrEqual(central[index]);
    }));
  });

  it("clamps outside central projections and preserves missing or malformed geometry", () => {
    const band = [[10, 50], [10, 50], [null, null], [10, 50], [50, 10], [0, Infinity]];
    const ribbons = pollTrackerBandRibbons(band, [0, 80, 30, NaN, 30, 0]);
    ribbons.forEach((ribbon) => {
      expect(ribbon[0][0]).toBe(10);
      expect(ribbon[1][1]).toBe(50);
      expect(ribbon[0][1]).toBeLessThanOrEqual(50);
      expect(ribbon[1][0]).toBeGreaterThanOrEqual(10);
      expect(ribbon.slice(2)).toEqual([[null, null], [null, null], [null, null], [null, null]]);
    });
  });

  it("formats seat ranges as counts and explains only enabled band metrics", () => {
    const row = { ...series, seatLo: [220], seatHi: [376] };
    expect(pollTrackerTooltipValues(row, 0)).toContainEqual(["90% seat range", "220–376"]);
    const capabilities = { seatBands: true, voteBands: true };
    expect(pollTrackerUncertaintyNote(capabilities, { seats: true, votes: true })).toContain("90% seat and vote-share ranges");
    expect(pollTrackerUncertaintyNote(capabilities, { seats: true, votes: true }))
      .toContain("trailing average over up to 5 model updates");
    expect(pollTrackerUncertaintyNote(capabilities, { seats: true, votes: false })).toContain("90% seat ranges");
    expect(pollTrackerUncertaintyNote(capabilities, { seats: false, votes: true })).toContain("90% vote-share ranges");
    expect(pollTrackerUncertaintyNote(capabilities, { chances: true })).not.toContain("Shaded bands");
    expect(pollTrackerTooltipValues(chanceSeries([row], "pMaj", [0])[0], 0)).toHaveLength(1);
  });

  it("leaves 90% ranges out of tooltips and the note when uncertainty is hidden", () => {
    const row = { ...series, seatLo: [220], seatHi: [376] };
    expect(pollTrackerTooltipValues(row, 0, { ranges: false })).toEqual([["Seats", "300"], ["Vote share", "40.12%"]]);
    expect(pollTrackerTooltipValues(row, 0, { ranges: true })).toContainEqual(["90% seat range", "220–376"]);
    const capabilities = { seatBands: true, voteBands: true };
    const hidden = pollTrackerUncertaintyNote(capabilities, { seats: true, votes: true, bands: false });
    expect(hidden).toContain("Uncertainty hidden");
    expect(hidden).not.toContain("Shaded bands");
    // Chances never draw bands, so hiding uncertainty doesn't change their note.
    expect(pollTrackerUncertaintyNote(capabilities, { chances: true, bands: false })).toContain("Chances");
  });
});

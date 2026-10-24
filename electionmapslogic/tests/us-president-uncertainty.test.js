import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it } from "vitest";
import { decodeUncertainty, uncertaintyForTotals, uncertaintyHeadline } from "../uncertainty.js";
import { manifest } from "../state.js";
import { parsePollTrackerData } from "../features/polltracker.js";
import { chanceSeries, pollTrackerCapabilities, pollTrackerTooltipValues, smoothPollTrackerBand } from "../features/polltracker-uncertainty.js";

const fixture = JSON.parse(readFileSync(new URL("../../data/tests/fixtures/us_uncertainty/president-public.json", import.meta.url)));
function payload() {
  return { ...structuredClone(fixture), uncertaintyId: "saved-president",
    display: { target_cycle: 2028, matchup: "Frozen Democrat vs Republican" } };
}
const decode = (value) => decodeUncertainty(value, "saved-president", String, new Map(), { model: "president", mapId: 23 });
function point(day, { known = true, summary = true } = {}) {
  const source = payload();
  const date = `2026-10-${String(day).padStart(2, "0")}`;
  source.context.as_of_date = date;
  return { as_of_date: date, context: known ? source.context : null,
    segment_id: known ? "frozen-president" : null, display: known ? source.display : null,
    metrics: Object.fromEntries(Object.entries(source.summary.metrics).map(([key, metric]) =>
      [key, Object.fromEntries(Object.entries(metric).filter(([field]) => field !== "ranges"))])),
    summary: summary ? source.summary : null,
    parties: { 1: { e: 280, s: 27, v: 50.8 }, 2: { e: 258, s: 29, v: 49.2 } } };
}
const tracker = (points) => parsePollTrackerData({ schema: "pf-us-model-trends-v1", model: "president", points });

beforeEach(() => manifest.init({ parties: [
  { id: 1, key: "1", name: "Democratic", colour: "#1375E1" },
  { id: 2, key: "2", name: "Republican", colour: "#E81B23" },
], parliamentFeatures: {}, mapModes: {}, files: {} }));

describe("President public electoral domain", () => {
  it("decodes 538 EVs and electoral majority rather than unit counts or popular-vote victory", () => {
    const summary = decode(payload());
    expect(summary.national.parties[1]).toEqual({ s: [200, 279, 360], v: [47.8, 53.8], pm: .58, pl: null });
    expect(summary.descriptor.metric).toBe("electoral_votes");
    expect(summary.display).toEqual({ targetCycle: 2028, matchup: "Frozen Democrat vs Republican" });
    expect(summary.basis).toContain("2028 · Frozen Democrat vs Republican");
    const headline = uncertaintyHeadline(summary, (id) => id === "1" ? "Democratic" : "Republican");
    expect(headline).toContain("Democratic electoral majority 58%");
    expect(headline).toContain("No electoral majority 2%");
    expect(headline).not.toMatch(/Hung|largest|popular.*majority/);
    const state = { view: "election", currentElection: { model: true }, electionData: { uncertainty: summary },
      voteTotals: { mode: "all" }, mapFilters: { party: "all", secondParty: "all", majorityMin: 0,
        majorityMax: 100, gainsOnly: false, upcoming: "all", region: "all" } };
    expect(uncertaintyForTotals(state).parties[1].s).toEqual([200, 279, 360]);
    state.mapFilters.region = "Maine";
    expect(uncertaintyForTotals(state)).toBeNull();
  });

  it.each(["metric", "total", "sum", "probability", "outcome", "allocation", "cycle", "display", "matchup", "identity"])("rejects malformed %s", (kind) => {
    const value = payload();
    if (kind === "metric") value.summary.metrics.electoral_votes.unit = "seats";
    if (kind === "total") value.summary.metrics.electoral_votes.total = 56;
    if (kind === "sum") value.summary.metrics.electoral_votes.central[1] = 27;
    if (kind === "probability") value.summary.outcomes.no_electoral_majority = .5;
    if (kind === "outcome") value.summary.outcomes.majority = value.summary.outcomes.electoral_majority;
    if (kind === "allocation") value.context.electoral_allocation_id = null;
    if (kind === "cycle") value.display.target_cycle = 2024;
    if (kind === "display") delete value.display;
    if (kind === "matchup") value.display.matchup = " ";
    if (kind === "identity") value.uncertaintyId = "different-run";
    expect(decode(value)).toBeNull();
  });
});

describe("President tracker EV boundary", () => {
  it("plots EV central and bounds, correct tooltip units, and only electoral-majority chances", () => {
    const data = tracker([point(1)]);
    const row = data.seriesByParty.get("1");
    expect(row.seats).toEqual([280]);
    expect(row.seatLo).toEqual([200]);
    expect(row.seatHi).toEqual([360]);
    expect(pollTrackerTooltipValues(row, 0)).toContainEqual(["Electoral votes", "280"]);
    expect(pollTrackerTooltipValues(row, 0)).toContainEqual(["90% electoral-vote range", "200–360"]);
    const odds = chanceSeries([row], "pMaj", data.residual, data.descriptor);
    expect(odds[0].chanceLabel).toBe("Electoral majority chance");
    expect(odds[1].chanceLabel).toBe("No electoral majority chance");
    expect(odds[0].votePct[0]).toBeCloseTo(58);
    expect(odds[1].votePct[0]).toBe(2);
    expect(row.pLargest).toEqual([null]);
  });

  it("retains a 269–269 central tally without turning it into a majority probability", () => {
    const source = point(1, { summary: false });
    source.metrics.electoral_votes.central = { 1: 269, 2: 269 };
    const data = tracker([source]);
    expect(data.seriesByParty.get("1").seats).toEqual([269]);
    expect(pollTrackerCapabilities(data).chances).toBe(false);
  });

  it("keeps legacy e as isolated central-only points without fabricating matchup or uncertainty", () => {
    const first = point(1, { known: false });
    const last = point(3, { known: false });
    delete first.metrics;
    delete last.metrics;
    const data = parsePollTrackerData([first, last], "president");
    expect(data.seriesByParty.get("1").seats).toEqual([280, null, null, 280]);
    expect(pollTrackerCapabilities(data)).toMatchObject({ bands: false, chances: false });
    expect(data.timeline.every((entry) => !entry.display)).toBe(true);
    expect(data.display).toBeNull();
    delete first.parties[1].e;
    expect(parsePollTrackerData([first], "president").seriesByParty.size).toBe(0);
  });

  it.each(["matchup_id", "electoral_allocation_id", "display"])("cuts all ranges, totals and chances when %s changes", (key) => {
    const next = point(2);
    if (key === "display") next.display.matchup = "Another saved matchup";
    else next.context[key] = "different-frozen-context";
    const data = tracker([point(1), next]);
    const row = data.seriesByParty.get("1");
    expect(row.seats).toEqual([280, null, 280]);
    expect(row.pMaj).toEqual([.58, null, .58]);
    expect(data.residual).toEqual([.02, null, .02]);
    const bands = row.seatLo.map((lo, index) => [lo, row.seatHi[index]]);
    expect(smoothPollTrackerBand(bands)).toEqual(bands);
  });

  it("clears unknown and missing evidence, including invalid explicit metrics despite legacy e", () => {
    const unknown = point(3, { known: false });
    const data = tracker([point(1), unknown, point(6, { summary: false })]);
    expect(data.seriesByParty.get("1").pMaj).toEqual([.58, .58, null, null, null, null, null, null]);
    const invalid = point(1);
    invalid.metrics.electoral_votes.total = 56;
    expect(tracker([invalid]).seriesByParty.size).toBe(0);
    const mismatch = point(1);
    mismatch.summary.metrics.electoral_votes.central[1]--;
    expect(pollTrackerCapabilities(tracker([mismatch])).chances).toBe(false);
  });
});

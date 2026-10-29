import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it } from "vitest";
import { decodeUncertainty, uncertaintyForTotals, uncertaintyHeadline } from "../uncertainty.js";
import { manifest } from "../state.js";
import { parsePollTrackerData } from "../features/polltracker.js";
import { chanceSeries, pollTrackerTooltipValues, smoothPollTrackerBand } from "../features/polltracker-uncertainty.js";

const fixture = JSON.parse(readFileSync(new URL("../../data/tests/fixtures/us_uncertainty/house-public.json", import.meta.url)));

function payload() {
  return { ...structuredClone(fixture), uncertaintyId: "current-artifact" };
}

function decode(value) {
  return decodeUncertainty(value, "current-artifact", String, new Map([[1, "region"]]),
    { model: "house", mapId: 21, seats: [{ seat: "TX-01", region: "region" }] });
}

function point(day, { segment = "meaning-a", context = true, summary = true, central = 220 } = {}) {
  const source = structuredClone(fixture);
  const date = `2026-10-${String(day).padStart(2, "0")}`;
  source.context.as_of_date = date;
  const metrics = source.summary.metrics;
  metrics.house_seats.central = { 1: central, 2: 435 - central };
  return {
    election_id: day, as_of_date: date, election_name: `US House UNS ${date}`,
    context: context ? source.context : null, segment_id: context ? segment : null,
    metrics: Object.fromEntries(Object.entries(metrics).map(([key, metric]) => [key,
      Object.fromEntries(Object.entries(metric).filter(([field]) => field !== "ranges"))])),
    summary: summary ? source.summary : null,
  };
}

function tracker(points) {
  return parsePollTrackerData({ schema: "pf-us-model-trends-v1", model: "house", points });
}

beforeEach(() => manifest.init({ parties: [
  { id: 1, key: "democratic", name: "Democratic" },
  { id: 2, key: "republican", name: "Republican" },
], parliamentFeatures: {}, mapModes: {}, files: {} }));

describe("explicit House public adapter", () => {
  it("decodes seats, projected vote and majority without importing largest-party semantics", () => {
    const result = decode(payload());
    expect(result.national.parties[1]).toEqual({ s: [190, 221, 250], v: [47.5, 52.6], pm: 0.6, pl: null });
    expect(result.national.residual).toBe(0);
    const headline = uncertaintyHeadline(result, (party) => party === "1" ? "Democratic" : "Republican");
    expect(headline).toContain("Democratic majority 60%");
    expect(headline).toContain("No majority 0%");
    expect(headline).not.toMatch(/Hung|largest|λ|Northern Ireland/);
  });

  it.each(["model", "map", "identity", "metric", "conservation", "date"])("rejects a malformed %s", (kind) => {
    const value = payload();
    if (kind === "model") value.model = "senate";
    if (kind === "map") value.context.map_id = 22;
    if (kind === "identity") value.uncertaintyId = "older-artifact";
    if (kind === "metric") value.summary.metrics.house_seats.unit = "electoral_votes";
    if (kind === "conservation") value.summary.outcomes.no_majority = 0.5;
    if (kind === "date") value.context.as_of_date = "2026-99-99";
    expect(decode(value)).toBeNull();
  });

  it("uses regional ranges only for exactly matching frozen membership and supported filters", () => {
    const value = payload();
    value.regional_bands = { schema: "pf-us-public-regions-v1", regions: {
      1: { members: ["TX-01"], parties: { 1: { s: [0, 1, 1], v: [45, 60] } } },
    } };
    const summary = decode(value);
    const state = { view: "election", currentElection: { model: true },
      electionData: { uncertainty: summary }, voteTotals: { mode: "all" },
      mapFilters: { party: "all", secondParty: "all", majorityMin: 0, majorityMax: 100,
        gainsOnly: false, upcoming: "all", region: "region" } };
    expect(uncertaintyForTotals(state).parties[1].v).toEqual([45, 60]);
    state.mapFilters.party = "democratic";
    expect(uncertaintyForTotals(state)).toBeNull();
    state.mapFilters.party = "all";
    state.mapFilters.majorityMin = 1;
    expect(uncertaintyForTotals(state)).toBeNull();
    value.regional_bands.regions[1].members = ["different-seat"];
    expect(decode(value).regions).toEqual({});
  });
  it.each(["schema", "unknown region", "empty members", "duplicate member", "non-string member", "party", "seat range", "vote range", "empty parties"])("rejects malformed regional %s", (kind) => {
    const value = payload();
    const block = { members: ["TX-01"], parties: { 1: { s: [0, 1, 1], v: [45, 60] } } };
    value.regional_bands = { schema: "pf-us-public-regions-v1", regions: { 1: block } };
    if (kind === "schema") value.regional_bands.schema = "unknown";
    if (kind === "unknown region") value.regional_bands.regions = { 99: block };
    if (kind === "empty members") block.members = [];
    if (kind === "duplicate member") block.members = ["TX-01", "TX-01"];
    if (kind === "non-string member") block.members = [1];
    if (kind === "party") block.parties = { private: block.parties[1] };
    if (kind === "seat range") block.parties[1].s = [0, 1, 2];
    if (kind === "vote range") block.parties[1].v = [60, 45];
    if (kind === "empty parties") block.parties = {};
    expect(decode(value)).toBeNull();
  });
});

describe("House trend meaning and display", () => {
  it.each(["seats", "votes"])("withholds ranges and odds when %s central values differ from the summary", (kind) => {
    const entry = point(1);
    if (kind === "seats") {
      entry.metrics.house_seats.central = { 1: 219, 2: 216 };
    } else {
      entry.metrics.national_vote_share.central = { 1: 49, 2: 51 };
    }
    const data = tracker([entry]);
    const row = data.seriesByParty.get("1");
    expect(row.seats).toEqual([entry.metrics.house_seats.central[1]]);
    expect(row.votePct).toEqual([entry.metrics.national_vote_share.central[1]]);
    expect(row.seatLo).toEqual([null]);
    expect(row.pMaj).toEqual([null]);
    expect(data.residual).toEqual([null]);
  });

  it("carries within known context, clears explicit missing summaries, and splits changed context", () => {
    const data = tracker([point(1), point(3, { summary: false }), point(5, { segment: "meaning-b" })]);
    const row = data.seriesByParty.get("1");
    expect(data.descriptor.model).toBe("house");
    expect(row.pMaj).toEqual([0.6, 0.6, null, null, null, 0.6]);
    expect(row.seats).toEqual([220, 220, 220, 220, null, 220]);
    expect(row.pLargest.every((value) => value === null)).toBe(true);
    const odds = chanceSeries([row], "pMaj", data.residual, data.descriptor);
    expect(odds[1].partyName).toBe("No majority");
    expect(odds[1].chanceLabel).toBe("No majority chance");
    expect(pollTrackerTooltipValues(row, 0)).toContainEqual(["90% seat range", "190–250"]);
  });

  it("never invents context for legacy points or bridges unknown calendar gaps", () => {
    const data = tracker([point(1, { context: false }), point(3, { context: false })]);
    expect(data.seriesByParty.get("1").seats).toEqual([220, null, null, 220]);
    expect(data.seriesByParty.get("1").pMaj.every((value) => value === null)).toBe(true);
  });

  it("keeps real context boundaries even if a malformed producer reuses a segment id", () => {
    const next = point(2);
    next.context.target_cycle = 2028;
    const data = tracker([point(1), next]);
    expect(data.seriesByParty.get("1").seats).toEqual([220, null, 220]);
    const band = data.seriesByParty.get("1").seatLo.map((lo, i) => [lo, data.seriesByParty.get("1").seatHi[i]]);
    expect(smoothPollTrackerBand(band)).toEqual(band);
  });
});

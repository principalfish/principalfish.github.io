import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it } from "vitest";
import { ElectionData, manifest } from "../state.js";
import { activeUncertainty, decodeUncertainty, uncertaintyForTotals, uncertaintyHeadline } from "../uncertainty.js";
import { parsePollTrackerData } from "../features/polltracker.js";
import { chanceSeries, pollTrackerCapabilities, pollTrackerTooltipValues } from "../features/polltracker-uncertainty.js";

const fixture = JSON.parse(readFileSync(new URL("../../data/tests/fixtures/us_uncertainty/senate-public.json", import.meta.url)));
const payload = () => ({ ...structuredClone(fixture), uncertaintyId: "saved-senate" });
const decode = (value) => decodeUncertainty(value, "saved-senate", String, new Map(), { model: "senate", mapId: 22 });
function point(day, { known = true, full = true, summary = true } = {}) {
  const doc = payload();
  const date = `2026-10-${String(day).padStart(2, "0")}`;
  doc.context.as_of_date = date;
  const metrics = Object.fromEntries(Object.entries(doc.summary.metrics).filter(([key]) => full || key !== "senate_full_seats")
    .map(([key, metric]) => [key, Object.fromEntries(Object.entries(metric).filter(([field]) => field !== "ranges"))]));
  return { as_of_date: date, context: known ? doc.context : null, segment_id: known ? "context-a" : null,
    metrics, summary: summary ? doc.summary : null, parties: { 1: { s: 16, v: 49 }, 2: { s: 19, v: 51 } } };
}
const tracker = (points) => parsePollTrackerData({ schema: "pf-us-model-trends-v1", model: "senate", points });

beforeEach(() => manifest.init({ parties: [
  { id: 1, key: "1", name: "Democratic", colour: "#1375E1" },
  { id: 2, key: "2", name: "Republican", colour: "#E81B23" },
  { id: 3, key: "3", name: "Independent" },
], parliamentFeatures: {}, mapModes: {}, files: {} }));

describe("Senate public metric domains", () => {
  it("keeps seats-up and full ranges separate without adding chamber vote shares", () => {
    const summary = decode(payload());
    expect(summary.national.parties[1].s).toEqual([12, 16, 20]);
    expect(summary.full.parties[1].s).toEqual([44, 48, 52]);
    expect(summary.full.parties[1].v).toBeNull();
    expect(summary.full.parties[1].pm).toBeNull();
    expect(summary.outcomes.control.republican).toBe(.7);
    expect(summary.outcomes.majority.republican).toBe(.5);
    const text = uncertaintyHeadline(summary, String);
    expect(text).toContain("Republican caucus control 70%");
    expect(text).toContain("50–50 caucuses 20%");
    expect(text).not.toMatch(/Hung|largest/);
  });

  it.each(["domain", "probability", "votes", "roster", "policy", "total", "model", "identity"])("rejects malformed %s", (kind) => {
    const value = payload();
    if (kind === "domain") value.summary.outcomes.control.domain = "party";
    if (kind === "probability") value.summary.outcomes.unresolved_control = .2;
    if (kind === "votes") value.summary.metrics.national_vote_share = {};
    if (kind === "roster") value.context.roster_id = null;
    if (kind === "policy") value.context.control_policy_id = null;
    if (kind === "total") value.summary.metrics.senate_full_seats.total = 35;
    if (kind === "model") value.context.model = "house";
    if (kind === "identity") value.uncertaintyId = "another-run";
    expect(decode(value)).toBeNull();
  });

  it("binds full totals only to a matching derived chamber and hides unsupported filtered ranges", () => {
    const source = new ElectionData({ seats: [], uncertaintyId: "saved-senate" });
    source.uncertainty = decode(payload());
    const derived = ElectionData.fromSeats([], "Saved Senate", source);
    derived.uncertaintyScope = "senate_full_seats";
    derived.summary.data.parties = [{ party: "1", seats: 48 }, { party: "2", seats: 51 }, { party: "3", seats: 1 }];
    const state = { view: "election", currentElection: { model: true }, electionData: derived,
      voteTotals: { mode: "all" }, mapFilters: { party: "all", secondParty: "all",
        majorityMin: 0, majorityMax: 100, gainsOnly: false, upcoming: "all", region: "all" } };
    expect(uncertaintyForTotals(state).parties[1].s).toEqual([44, 48, 52]);
    state.mapFilters.region = "region";
    expect(uncertaintyForTotals(state)).toBeNull();
    derived.summary.data.parties[0].seats = 47;
    expect(activeUncertainty(state)).toBeNull();
    expect(ElectionData.fromSeats([], "User scenario").uncertaintyId).toBeNull();
  });
});

describe("Senate full-chamber tracker", () => {
  it("shows saved-roster central points without carrying totals or inventing odds", () => {
    const partial = point(3, { known: false, summary: false });
    partial.central_basis = { schema: "pf-us-senate-central-roster-v1",
      map_id: 22, as_of_date: partial.as_of_date, target_cycle: 2026,
      roster_id: "saved-roster", field_id: "saved-field" };
    const data = tracker([point(1), partial, point(6)]);
    expect(data.hasRosterOnly).toBe(true);
    const row = data.seriesByParty.get("1");
    expect(row.seats).toEqual([48, 48, null, 48, null, null, null, 48]);
    expect(row.seatLo).toEqual([44, 44, null, null, null, null, null, 44]);
    expect(data.senateChances.control.democratic).toEqual([.3, .3, null, null, null, null, null, .3]);
    partial.central_basis.roster_id = "";
    expect(tracker([partial]).seriesByParty.size).toBe(0);
  });

  it("plots full100 raw-party counts, not legacy contested s, and no chamber popular vote", () => {
    const data = tracker([point(1)]);
    const row = data.seriesByParty.get("1");
    expect(row.seats).toEqual([48]);
    expect(row.seatLo).toEqual([44]);
    expect(row.votePct).toEqual([null]);
    expect(row.pMaj).toEqual([null]);
    expect(pollTrackerTooltipValues(row, 0)).not.toContainEqual(["Vote share", "Unavailable"]);
    expect(pollTrackerCapabilities(data).chances).toBe(true);
    const chances = chanceSeries([], "pMaj", data.residual, data.descriptor, data.senateChances);
    expect(chances.map((row) => row.partyKey)).toEqual(["democratic", "republican", "tied_caucuses", "unresolved_control"]);
    expect(chances.map((row) => row.votePct[0])).toEqual([30, 70, 20, 0]);
    expect(pollTrackerTooltipValues(chances[1], 0)).toContainEqual(["Caucus majority chance (51+ seats)", "50.00%"]);
  });

  it("clears missing summaries, omits unsupported legacy full totals and never carries across unknown contexts", () => {
    const data = tracker([point(1), point(3, { summary: false }), point(4, { known: false, full: false }), point(6)]);
    expect(data.seriesByParty.get("1").seats).toEqual([48, 48, 48, null, null, null, null, 48]);
    expect(data.senateChances.control.democratic).toEqual([.3, .3, null, null, null, null, null, .3]);
    expect(tracker([point(1, { known: false, full: false })]).seriesByParty.size).toBe(0);
  });

  it("splits frozen roster and control-policy changes even with a reused segment id", () => {
    const next = point(2);
    next.context.roster_id = "other-roster";
    expect(tracker([point(1), next]).seriesByParty.get("1").seats).toEqual([48, null, 48]);
    next.context = { ...point(2).context, control_policy_id: "other-policy" };
    expect(tracker([point(1), next]).senateChances.tied).toEqual([.2, null, .2]);
  });
});

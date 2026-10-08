import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../dom.js", () => ({
  renderHeader: vi.fn(),
  renderMap: vi.fn(),
  renderMapControlOptions: vi.fn(),
  syncRightPanelHeight: vi.fn(),
  initRegionTable: vi.fn(),
  refreshOpenSeatPopup: vi.fn(),
}));
vi.mock("../files.js", () => ({ fetchJson: vi.fn() }));
vi.mock("../features/senate-forecast-view.js", () => ({ renderSenateForecastTabs: vi.fn() }));

import { activateSenateForecastView } from "../features/senate-forecast-controller.js";
import { renderSenateForecastTabs } from "../features/senate-forecast-view.js";
import { renderMap, refreshOpenSeatPopup } from "../dom.js";
import { fetchJson } from "../files.js";
import { ElectionData, manifest, state } from "../state.js";

const readData = (file) => JSON.parse(readFileSync(new URL(`../../uselectionmaps/data/${file}`, import.meta.url), "utf8"));
const manifestData = readData("map-modes.json");
const chamberData = readData("results/senate-current.json");
const forecastData = readData("results/us-senate-forecast.json");
const baselineData = readData("results/us-senate-2020.json");
const latestTabs = () => renderSenateForecastTabs.mock.calls.at(-1)[0];
const tallies = (summary) => Object.fromEntries(summary.parties.filter((party) => party.seats).map((party) => [party.party, party.seats]));

describe("activateSenateForecastView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchJson.mockReset();
    fetchJson.mockResolvedValue(chamberData);
    manifest.init(structuredClone(manifestData));
    state.currentParliament = "us_senate";
    state.currentElection = { ...manifest.getElectionFromId("current-us-senate") };
    state.view = "election";
    state.isReferendumType = false;
    state.predictModel = null;
    state.predictionSnippet = "";
    state.voteTotals.mode = "all";
    state.mapFilters = {
      party: "all", region: "all", secondParty: "all", majorityMin: 0,
      majorityMax: 100, gainsOnly: false, upcoming: "all",
    };
    state.mapChoropleths = { type: "none", party: "all" };
    state.electionData = new ElectionData(forecastData, "2026 Prediction");
    state.comparisonElectionData = new ElectionData(baselineData);
    state.setupMapData();
  });

  afterEach(() => vi.restoreAllMocks());

  it("switches the saved forecast to 100 members with current-chamber comparisons and no vote columns", async () => {
    await activateSenateForecastView();
    expect(fetchJson).toHaveBeenCalledWith("data/results/senate-current.json");
    expect(latestTabs().activeTab).toBe("seatsup");
    expect(latestTabs().tabs.map((tab) => tab.key)).toEqual(["seatsup", "chamber"]);

    latestTabs().onTabChange("chamber");
    expect(state.currentElection.multiMember).toBe(true);
    expect(state.electionData.currentSeats).toHaveLength(50);
    expect(state.filteredSeatsSummary.totalSeats).toBe(100);
    expect(tallies(state.filteredSeatsSummary)).toEqual({ democrat: 51, republican: 47, independent: 2 });
    expect(tallies(state.filteredSeatsComparisonSummary)).toEqual({ republican: 53, democrat: 45, independent: 2 });
    expect(state.filteredSeatsComparisonSummary.totalSeats).toBe(100);
    expect(state.filteredSeatsSummary.totalVotes).toBe(0);
    expect(state.voteTotals.columns).toMatchObject({ votes: false, votePct: false });
    expect(state.electionData.electionName).toBe("2026 Prediction");
    expect(refreshOpenSeatPopup).toHaveBeenCalledOnce();

    const ohio = state.electionData.currentSeats.find((seat) => seat.seat === "Ohio");
    expect(ohio.members.find((member) => member.class === 3)).toMatchObject({ party: "democrat", name: "Democratic (projected)" });
    expect(ohio.members.find((member) => member.class === 1)).toMatchObject({ party: "republican", name: "Bernie Moreno" });
    const florida = state.electionData.currentSeats.find((seat) => seat.seat === "Florida");
    expect(florida.members.find((member) => member.class === 3).name).toBe("Republican (projected)");
    expect(florida.members.find((member) => member.class === 1).name).toBe("Rick Scott");
    expect(manifest.getElectionFromId("current-us-senate").multiMember).toBeUndefined();
  });

  it("repeatedly restores the original 35-seat forecast, vote shares and comparison objects", async () => {
    const forecast = state.electionData;
    const comparison = state.comparisonElectionData;
    const originalForecast = JSON.stringify(forecast.currentSeats);
    const originalComparison = JSON.stringify(comparison.currentSeats);
    const votes = state.filteredSeatsSummary.totalVotes;
    await activateSenateForecastView();

    for (let index = 0; index < 3; index += 1) {
      latestTabs().onTabChange("chamber");
      expect(latestTabs().activeTab).toBe("chamber");
      expect(state.filteredSeatsSummary.totalSeats).toBe(100);
      latestTabs().onTabChange("seatsup");
      expect(latestTabs().activeTab).toBe("seatsup");
      expect(state.currentElection.multiMember).toBe(false);
      expect(state.electionData).toBe(forecast);
      expect(state.comparisonElectionData).toBe(comparison);
      expect(state.filteredSeatsSummary.totalSeats).toBe(35);
      expect(state.filteredSeatsSummary.totalVotes).toBe(votes);
      expect(state.voteTotals.columns.votePct).toBe(true);
    }
    expect(JSON.stringify(forecast.currentSeats)).toBe(originalForecast);
    expect(JSON.stringify(comparison.currentSeats)).toBe(originalComparison);
    expect(fetchJson).toHaveBeenCalledOnce();
    expect(renderMap).toHaveBeenCalledTimes(6);
    latestTabs().onTabChange("seatsup");
    latestTabs().onTabChange("unknown");
    expect(renderMap).toHaveBeenCalledTimes(6);
  });

  it("retains region and party filters while switching views", async () => {
    state.mapFilters.region = "newengland";
    state.setupMapData();
    const seatsUpCount = state.filteredSeatsSummary.totalSeats;
    await activateSenateForecastView();
    latestTabs().onTabChange("chamber");
    expect(state.mapFilters.region).toBe("newengland");
    expect(state.filteredSeatsSummary.totalSeats).toBe(12);
    expect(state.mapSeatsVisible.seats).toHaveLength(6);
    state.mapFilters.party = "independent";
    state.setupMapData();
    expect(state.filteredSeatsSummary.totalSeats).toBe(2);
    expect(tallies(state.filteredSeatsSummary)).toEqual({ independent: 2 });
    latestTabs().onTabChange("seatsup");
    expect(state.mapFilters.party).toBe("independent");
    expect(state.filteredSeatsSummary.totalSeats).toBe(0);
    state.mapFilters.party = "all";
    state.setupMapData();
    expect(state.filteredSeatsSummary.totalSeats).toBe(seatsUpCount);
  });

  it.each([
    ["historical Senate", "us_senate", "2022-us-senate", "election"],
    ["Current Senate", "us_senate", "current-senate", "election"],
    ["House forecast", "us_house", "current-us-house", "election"],
    ["interactive prediction", "us_senate", "current-us-senate", "predict"],
  ])("hides the switch on %s without loading or replacing election data", async (_, parliament, electionId, view) => {
    state.currentParliament = parliament;
    state.currentElection = { ...manifest.getElectionFromId(electionId) };
    state.view = view;
    const forecast = state.electionData;
    await activateSenateForecastView();
    expect(renderSenateForecastTabs).toHaveBeenCalledExactlyOnceWith();
    expect(fetchJson).not.toHaveBeenCalled();
    expect(state.electionData).toBe(forecast);
    expect(renderMap).not.toHaveBeenCalled();
  });

  it.each(["missing", "empty", "failed"])("keeps the forecast usable and the switch hidden for a %s chamber snapshot", async (failure) => {
    if (failure === "missing") {
      manifest.elections = manifest.elections.filter((election) => election.id !== "current-senate");
    } else if (failure === "empty") {
      fetchJson.mockResolvedValue({ seats: [] });
    } else {
      fetchJson.mockRejectedValue(new Error("Snapshot unavailable"));
      vi.spyOn(console, "error").mockImplementation(() => {});
    }
    const forecast = state.electionData;
    const comparison = state.comparisonElectionData;
    await activateSenateForecastView();
    expect(renderSenateForecastTabs).toHaveBeenCalledExactlyOnceWith();
    expect(state.electionData).toBe(forecast);
    expect(state.comparisonElectionData).toBe(comparison);
    expect(state.filteredSeatsSummary.totalSeats).toBe(35);
    expect(state.currentElection.multiMember).toBeFalsy();
    expect(renderMap).not.toHaveBeenCalled();
  });
});

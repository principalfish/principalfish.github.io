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
vi.mock("../features/senate-forecast-view.js", () => ({
  renderSenateForecastTabs: vi.fn(),
  renderSenateForecastComparison: vi.fn(),
}));

import { activateSenateForecastView } from "../features/senate-forecast-controller.js";
import { renderSenateForecastComparison, renderSenateForecastTabs } from "../features/senate-forecast-view.js";
import { renderMap, refreshOpenSeatPopup } from "../dom.js";
import { fetchJson } from "../files.js";
import { ElectionData, manifest, page, Seat, state } from "../state.js";
import { seatLookupKey } from "../utils.js";

const readData = (file) => JSON.parse(readFileSync(new URL(`../../uselectionmaps/data/${file}`, import.meta.url), "utf8"));
const manifestData = readData("map-modes.json");
const chamberData = readData("results/senate-current.json");
const forecastData = readData("results/us-senate-forecast.json");
const baselineData = readData("results/us-senate-2020.json");
const specialBaselineData = readData("results/us-senate-2022.json");
function fetchResults(url) {
  const file = url.split("/results/")[1];
  const results = {
    "senate-current.json": chamberData,
    "us-senate-2020.json": baselineData,
    "us-senate-2022.json": specialBaselineData,
  }[file];
  if (!results) throw new Error(`Unexpected results URL: ${url}`);
  return Promise.resolve(results);
}
const latestTabs = () => renderSenateForecastTabs.mock.calls.at(-1)[0];
const latestComparison = () => renderSenateForecastComparison.mock.calls.at(-1)[0];
const tallies = (summary) => Object.fromEntries(summary.parties.filter((party) => party.seats).map((party) => [party.party, party.seats]));

describe("activateSenateForecastView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchJson.mockReset();
    fetchJson.mockImplementation(fetchResults);
    page.dataBase = "";
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
    refreshOpenSeatPopup.mockClear();
    expect(fetchJson).toHaveBeenCalledWith("data/results/senate-current.json");
    expect(latestTabs().activeTab).toBe("seatsup");
    expect(latestTabs().tabs.map((tab) => tab.key)).toEqual(["seatsup", "chamber"]);
    expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: true });

    latestTabs().onTabChange("chamber");
    expect(latestComparison()).toEqual({ activeTab: "chamber", comparisonAvailable: true });
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

  it("repeatedly restores the original 35-seat forecast, vote shares and completed comparison", async () => {
    const forecast = state.electionData;
    const originalForecast = JSON.stringify(forecast.currentSeats);
    const votes = state.filteredSeatsSummary.totalVotes;
    await activateSenateForecastView();
    const comparison = state.comparisonElectionData;
    const originalComparison = JSON.stringify(comparison.currentSeats);
    renderMap.mockClear();

    for (let index = 0; index < 3; index += 1) {
      latestTabs().onTabChange("chamber");
      expect(latestTabs().activeTab).toBe("chamber");
      expect(latestComparison()).toEqual({ activeTab: "chamber", comparisonAvailable: true });
      expect(state.filteredSeatsSummary.totalSeats).toBe(100);
      latestTabs().onTabChange("seatsup");
      expect(latestTabs().activeTab).toBe("seatsup");
      expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: true });
      expect(state.currentElection.multiMember).toBe(false);
      expect(state.electionData).toBe(forecast);
      expect(state.comparisonElectionData).toBe(comparison);
      expect(state.filteredSeatsSummary.totalSeats).toBe(35);
      expect(state.filteredSeatsSummary.totalVotes).toBe(votes);
      expect(state.voteTotals.columns.votePct).toBe(true);
    }
    expect(JSON.stringify(forecast.currentSeats)).toBe(originalForecast);
    expect(JSON.stringify(comparison.currentSeats)).toBe(originalComparison);
    expect(fetchJson).toHaveBeenCalledTimes(2);
    expect(fetchJson.mock.calls.map(([url]) => url).sort()).toEqual([
      "data/results/senate-current.json", "data/results/us-senate-2022.json",
    ]);
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
    expect(renderSenateForecastComparison).toHaveBeenCalledExactlyOnceWith();
    expect(fetchJson).not.toHaveBeenCalled();
    expect(state.electionData).toBe(forecast);
    expect(renderMap).not.toHaveBeenCalled();
  });

  it.each(["missing", "empty", "failed"])("keeps the forecast usable and the switch hidden for a %s chamber snapshot", async (failure) => {
    if (failure === "missing") {
      manifest.elections = manifest.elections.filter((election) => election.id !== "current-senate");
    } else if (failure === "empty") {
      fetchJson.mockImplementation((url) => url.endsWith("senate-current.json")
        ? Promise.resolve({ seats: [] }) : fetchResults(url));
    } else {
      fetchJson.mockImplementation((url) => url.endsWith("senate-current.json")
        ? Promise.reject(new Error("Snapshot unavailable")) : fetchResults(url));
      vi.spyOn(console, "error").mockImplementation(() => {});
    }
    const forecast = state.electionData;
    await activateSenateForecastView();
    expect(renderSenateForecastTabs).toHaveBeenCalledExactlyOnceWith();
    expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: true });
    expect(state.electionData).toBe(forecast);
    expect(state.comparisonElectionData.currentSeats).toHaveLength(35);
    expect(state.filteredSeatsComparisonSummary.totalSeats).toBe(35);
    expect(state.filteredSeatsSummary.totalSeats).toBe(35);
    expect(state.currentElection.multiMember).toBeFalsy();
    expect(renderMap).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["historical Senate", "us_senate", "2022-us-senate", "election"],
    ["House forecast", "us_house", "current-us-house", "election"],
    ["interactive prediction", "us_senate", "current-us-senate", "predict"],
  ])("clears saved chamber controls when activating %s", async (_, parliament, electionId, view) => {
    await activateSenateForecastView();
    latestTabs().onTabChange("chamber");
    state.currentParliament = parliament;
    state.currentElection = { ...manifest.getElectionFromId(electionId) };
    state.view = view;
    const destination = state.electionData;
    await activateSenateForecastView();
    expect(latestTabs()).toBeUndefined();
    expect(latestComparison()).toBeUndefined();
    expect(state.electionData).toBe(destination);
  });

  it.each(["missing", "empty", "failed"])("explains unavailable baselines without a %s chamber snapshot", async (failure) => {
    const mode = manifest.mapModes[String(state.currentElection.mapId)];
    delete mode.senateSpecialElections[0].baselineElectionId;
    if (failure === "missing") {
      manifest.elections = manifest.elections.filter((election) => election.id !== "current-senate");
    } else {
      fetchJson.mockImplementation((url) => {
        if (!url.endsWith("senate-current.json")) return fetchResults(url);
        return failure === "empty" ? Promise.resolve({ seats: [] }) : Promise.reject(new Error("Snapshot unavailable"));
      });
    }
    vi.spyOn(console, "error").mockImplementation(() => {});
    const forecast = state.electionData;
    await activateSenateForecastView();
    expect(latestTabs()).toBeUndefined();
    expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: false });
    expect(state.comparisonElectionData).toBeNull();
    expect(state.electionData).toBe(forecast);
  });

  describe("per-seat comparison", () => {
    const result = (name, winner, democrat = winner === "democrat" ? 600 : 400) => ({
      n: name, r: "test", w: winner, p: [["democrat", democrat], ["republican", 1000 - democrat]],
    });
    const regularRows = Array.from({ length: 33 }, (_, index) =>
      result(`Regular ${index}`, index < 13 ? "democrat" : "republican"));
    const savedRows = Array.from({ length: 33 }, (_, index) =>
      result(`Regular ${index}`, index < 18 ? "democrat" : "republican"));
    const specialRows = [result("Special A", "republican", 300), result("Special B", "republican", 450)];
    const specials = [
      { seat: " special a ", class: 3, year: 2026, baselineElectionId: "2022-us-senate" },
      { seat: "SPECIAL B", class: 3, year: 2026, baselineElectionId: "2022-us-senate" },
    ];
    let regularResults;
    let specialResults;

    beforeEach(() => {
      regularResults = { seats: [...regularRows, regularRows[0], result("Unrelated regular", "democrat")] };
      specialResults = { seats: [...specialRows, specialRows[0], result("Unrelated special", "democrat")] };
      manifest.mapModes[String(state.currentElection.mapId)].senateSpecialElections = [
        ...structuredClone(specials),
        { seat: "Regular 0", year: 2028, baselineElectionId: "missing" },
        { seat: "Absent forecast seat", year: 2026, baselineElectionId: "missing" },
      ];
      state.electionData = new ElectionData({ seats: [
        ...savedRows, result("Special A", "democrat"), result("Special B", "republican"),
      ] }, "Synthetic forecast");
      state.comparisonElectionData = new ElectionData(regularResults);
      state.setupMapData();
      fetchJson.mockImplementation((url) => {
        if (url.endsWith("us-senate-2022.json")) return Promise.resolve(specialResults);
        if (url.endsWith("us-senate-2020.json")) return Promise.resolve(regularResults);
        return fetchResults(url);
      });
    });

    it("compares the same 35 keys, uses special votes and gains, and counts no unrelated or duplicate rows", async () => {
      const forecast = state.electionData;
      const savedOutput = JSON.stringify(forecast.currentSeats);
      const regular = state.comparisonElectionData;
      await activateSenateForecastView();
      const comparison = state.comparisonElectionData;
      expect(state.electionData).toBe(forecast);
      expect(JSON.stringify(forecast.currentSeats)).toBe(savedOutput);
      expect([...comparison.seatsByKey.keys()].sort()).toEqual([...forecast.seatsByKey.keys()].sort());
      expect(comparison.currentSeats).toHaveLength(35);
      expect(tallies(state.filteredSeatsSummary)).toEqual({ democrat: 19, republican: 16 });
      expect(tallies(state.filteredSeatsComparisonSummary)).toEqual({ democrat: 13, republican: 22 });
      const changes = Object.fromEntries(state.filteredSeatsSummary.parties.map((party) => [
        party.party, party.seats - state.filteredSeatsComparisonSummary.parties.find((row) => row.party === party.party).seats,
      ]));
      expect(changes).toEqual({ democrat: 6, republican: -6 });
      for (const row of regularRows) {
        expect(comparison.seatsByKey.get(seatLookupKey(row.n))).toBe(regular.seatsByKey.get(seatLookupKey(row.n)));
      }
      const specialA = forecast.seatsByKey.get("special a");
      const baselineA = comparison.seatsByKey.get("special a");
      expect(baselineA.votes).toEqual({ democrat: 300, republican: 700 });
      expect(specialA.gainFromParty(baselineA.winner)).toBe("republican");
      expect(Seat.choroplethValue(specialA, baselineA, true, "democrat")).toBe(30);
      expect(forecast.seatsByKey.get("special b").gainFromParty(comparison.seatsByKey.get("special b").winner)).toBeNull();
      expect(comparison.seatsByKey.get("special b").votes).toEqual({ democrat: 450, republican: 550 });
      expect(fetchJson.mock.calls.filter(([url]) => url.endsWith("us-senate-2022.json"))).toHaveLength(1);
    });

    it("gives special baselines priority over regular rows with the same key", async () => {
      regularResults.seats.push(result("Special A", "democrat"), result("Special B", "democrat"));
      state.comparisonElectionData = new ElectionData(regularResults);
      await activateSenateForecastView();
      expect(tallies(state.filteredSeatsComparisonSummary)).toEqual({ democrat: 13, republican: 22 });
      expect(state.comparisonElectionData.seatsByKey.get("special a").votes).toEqual({ democrat: 300, republican: 700 });
    });

    it.each(["absent id", "unresolved id", "missing file", "failed", "empty", "missing seat", "no votes", "no winner", "invalid votes"])(
      "suppresses comparison for a %s special baseline, preserves output and retries on activation",
      async (failure) => {
        const forecast = state.electionData;
        const output = JSON.stringify(forecast.currentSeats);
        regularResults.seats.push(...specialRows.map((row) => result(row.n, "democrat")));
        state.comparisonElectionData = new ElectionData(regularResults);
        vi.spyOn(console, "error").mockImplementation(() => {});
        if (failure === "absent id") {
          delete manifest.mapModes[String(state.currentElection.mapId)].senateSpecialElections[0].baselineElectionId;
        } else if (failure === "unresolved id") {
          manifest.elections = manifest.elections.filter((entry) => entry.id !== "2022-us-senate");
        } else if (failure === "missing file") {
          delete manifest.files.elections.electionsById["2022-us-senate"];
        } else if (failure === "failed") {
          const successfulFetch = fetchJson.getMockImplementation();
          fetchJson.mockImplementation((url) => url.endsWith("us-senate-2022.json")
            ? Promise.reject(new Error("Unavailable baseline")) : successfulFetch(url));
        } else if (failure === "empty") {
          specialResults = { seats: [] };
        } else if (failure === "missing seat") {
          specialResults = { seats: [specialRows[1]] };
        } else if (failure === "no votes") {
          specialResults = { seats: [{ ...specialRows[0], p: [] }, specialRows[1]] };
        } else if (failure === "no winner") {
          specialResults = { seats: [{ ...specialRows[0], w: undefined }, specialRows[1]] };
        } else {
          specialResults = { seats: [{ ...specialRows[0], p: [["republican", "bad"]] }, specialRows[1]] };
        }
        await activateSenateForecastView();
        expect(state.comparisonElectionData).toBeNull();
        expect(state.filteredSeatsComparisonSummary).toBeNull();
        expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: false });
        expect(state.electionData).toBe(forecast);
        expect(JSON.stringify(forecast.currentSeats)).toBe(output);
        const tabs = latestTabs();
        tabs.onTabChange("chamber");
        expect(latestComparison()).toEqual({ activeTab: "chamber", comparisonAvailable: true });
        latestTabs().onTabChange("seatsup");
        expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: false });
        expect(state.comparisonElectionData).toBeNull();
        expect(state.electionData).toBe(forecast);

        manifest.init(structuredClone(manifestData));
        manifest.mapModes[String(state.currentElection.mapId)].senateSpecialElections = structuredClone(specials);
        fetchJson.mockImplementation((url) => {
          if (url.endsWith("us-senate-2022.json")) return Promise.resolve({ seats: specialRows });
          if (url.endsWith("us-senate-2020.json")) return Promise.resolve(regularResults);
          return fetchResults(url);
        });
        await activateSenateForecastView();
        expect(state.comparisonElectionData.currentSeats).toHaveLength(35);
        expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: true });
        expect(tallies(state.filteredSeatsComparisonSummary)).toEqual({ democrat: 13, republican: 22 });
        expect(state.electionData).toBe(forecast);
        expect(JSON.stringify(forecast.currentSeats)).toBe(output);
      },
    );

    it.each(["missing seat", "no votes", "no winner"])("suppresses a %s regular baseline", async (failure) => {
      const rows = regularRows.slice(1);
      if (failure !== "missing seat") rows.push({ ...regularRows[0], ...(failure === "no votes" ? { p: [] } : { w: undefined }) });
      state.comparisonElectionData = new ElectionData({ seats: rows });
      const output = JSON.stringify(state.electionData.currentSeats);
      await activateSenateForecastView();
      expect(state.comparisonElectionData).toBeNull();
      expect(JSON.stringify(state.electionData.currentSeats)).toBe(output);
    });

    it("uses only regular forecast rows when no specials are configured", async () => {
      manifest.mapModes[String(state.currentElection.mapId)].senateSpecialElections = [];
      state.electionData = new ElectionData({ seats: savedRows });
      state.setupMapData();
      await activateSenateForecastView();
      expect(state.comparisonElectionData.currentSeats).toHaveLength(33);
      expect(tallies(state.filteredSeatsComparisonSummary)).toEqual({ democrat: 13, republican: 20 });
      expect(fetchJson).toHaveBeenCalledExactlyOnceWith("data/results/senate-current.json");
    });

    it("clears the incomplete comparison and refreshes before awaiting special results", async () => {
      let resolveBaseline;
      fetchJson.mockImplementation((url) => url.endsWith("us-senate-2022.json")
        ? new Promise((resolve) => { resolveBaseline = resolve; }) : fetchResults(url));
      const activation = activateSenateForecastView();
      expect(state.comparisonElectionData).toBeNull();
      expect(state.filteredSeatsComparisonSummary).toBeNull();
      expect(renderMap).toHaveBeenCalledExactlyOnceWith(true);
      expect(latestTabs()).toBeUndefined();
      expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: false, loading: true });
      resolveBaseline(specialResults);
      await activation;
      expect(state.filteredSeatsComparisonSummary.totalSeats).toBe(35);
      expect(latestComparison()).toEqual({ activeTab: "seatsup", comparisonAvailable: true });
    });

    it.each([
      ["special", "election object"], ["special", "view"],
      ["chamber", "election object"], ["chamber", "view"],
      ["regular", "election object"], ["regular", "view"],
    ])("ignores an awaited %s response after the %s changes", async (load, destination) => {
      let resolveLoad;
      const deferredFile = { special: "us-senate-2022.json", chamber: "senate-current.json", regular: "us-senate-2020.json" }[load];
      if (load === "regular") state.comparisonElectionData = null;
      const successfulFetch = fetchJson.getMockImplementation();
      fetchJson.mockImplementation((url) => url.endsWith(deferredFile)
        ? new Promise((resolve) => { resolveLoad = resolve; }) : successfulFetch(url));
      const activation = activateSenateForecastView();
      await vi.waitFor(() => expect(resolveLoad).toBeTypeOf("function"));
      if (destination === "election object") state.currentElection = { ...state.currentElection };
      else state.view = "predict";
      const destinationData = new ElectionData({ seats: [result("Destination", "democrat")] });
      state.electionData = destinationData;
      state.comparisonElectionData = destinationData;
      const renderCount = renderMap.mock.calls.length;
      const noteRenderCount = renderSenateForecastComparison.mock.calls.length;
      resolveLoad(load === "chamber" ? chamberData : load === "regular" ? regularResults : specialResults);
      await activation;
      expect(state.electionData).toBe(destinationData);
      expect(state.comparisonElectionData).toBe(destinationData);
      expect(renderMap).toHaveBeenCalledTimes(renderCount);
      expect(renderSenateForecastComparison).toHaveBeenCalledTimes(noteRenderCount);
      expect(renderSenateForecastTabs).toHaveBeenCalledExactlyOnceWith();
    });

    it("keeps cleared destination controls hidden after a pending baseline resolves", async () => {
      let resolveBaseline;
      fetchJson.mockImplementation((url) => url.endsWith("us-senate-2022.json")
        ? new Promise((resolve) => { resolveBaseline = resolve; }) : fetchResults(url));
      const activation = activateSenateForecastView();
      expect(latestComparison().loading).toBe(true);
      state.currentElection = { ...manifest.getElectionFromId("2022-us-senate") };
      const destinationData = new ElectionData({ seats: [result("Destination", "democrat")] });
      state.electionData = destinationData;
      state.comparisonElectionData = destinationData;
      await activateSenateForecastView();
      expect(latestComparison()).toBeUndefined();
      expect(latestTabs()).toBeUndefined();
      const noteRenderCount = renderSenateForecastComparison.mock.calls.length;
      resolveBaseline(specialResults);
      await activation;
      expect(renderSenateForecastComparison).toHaveBeenCalledTimes(noteRenderCount);
      expect(latestTabs()).toBeUndefined();
      expect(state.electionData).toBe(destinationData);
      expect(state.comparisonElectionData).toBe(destinationData);
    });

    it.each(["election object", "view"])("ignores a retained tab callback after the %s changes", async (destination) => {
      await activateSenateForecastView();
      const callback = latestTabs().onTabChange;
      const forecast = state.electionData;
      const comparison = state.comparisonElectionData;
      const renderCount = renderMap.mock.calls.length;
      const noteRenderCount = renderSenateForecastComparison.mock.calls.length;
      if (destination === "election object") state.currentElection = { ...state.currentElection };
      else state.view = "predict";
      callback("chamber");
      expect(state.electionData).toBe(forecast);
      expect(state.comparisonElectionData).toBe(comparison);
      expect(state.currentElection.multiMember).toBeFalsy();
      expect(renderMap).toHaveBeenCalledTimes(renderCount);
      expect(renderSenateForecastComparison).toHaveBeenCalledTimes(noteRenderCount);
    });
  });
});

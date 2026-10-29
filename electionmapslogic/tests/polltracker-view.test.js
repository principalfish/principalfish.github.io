import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchJson = vi.hoisted(() => vi.fn());
vi.mock("../files.js", () => ({ fetchJson }));

// The browser harness is optional locally; CI can supply a jsdom 24 installation
// without making it a production dependency of the static website.
let JSDOM;
try {
  ({ JSDOM } = createRequire(import.meta.url)(process.env.PF_TEST_JSDOM || "jsdom"));
} catch { /* Actual-module browser checks run when the optional harness is supplied. */ }

const fixture = JSON.parse(readFileSync(new URL("../../data/tests/fixtures/us_uncertainty/house-trend.json", import.meta.url), "utf8")
  .replaceAll('"1":', '"20":').replaceAll('"2":', '"21":'));
const senateFixture = JSON.parse(readFileSync(new URL("../../data/tests/fixtures/us_uncertainty/senate-public.json", import.meta.url), "utf8")
  .replaceAll('"1":', '"20":').replaceAll('"2":', '"21":'));
const presidentFixture = JSON.parse(readFileSync(new URL("../../data/tests/fixtures/us_uncertainty/president-trend.json", import.meta.url), "utf8")
  .replaceAll('"1":', '"20":').replaceAll('"2":', '"21":'));
function senateHistory({ known = true } = {}) {
  const context = senateFixture.context;
  const summary = senateFixture.summary;
  return { schema: "pf-us-model-trends-v1", model: "senate", points: [{
    as_of_date: context.as_of_date, context: known ? context : null,
    segment_id: known ? "frozen-senate" : null,
    metrics: summary.metrics, summary: known ? summary : null,
  }] };
}
const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");
const fragment = readFileSync(new URL("../fragments/polltracker.html", import.meta.url), "utf8");

function history() {
  return { ...fixture, points: Array.from({ length: 40 }, (_, index) => {
    const point = structuredClone(fixture.points[0]);
    const date = new Date(Date.UTC(2026, 8, 1 + index)).toISOString().slice(0, 10);
    point.as_of_date = date;
    point.context.as_of_date = date;
    point.segment_id = "stable-meaning";
    return point;
  }) };
}

describe.skipIf(!JSDOM)("actual poll tracker view", () => {
  let dom;
  let state;
  let view;
  beforeEach(async () => {
    vi.resetModules();
    fetchJson.mockReset();
    dom = new JSDOM(shell + fragment, { url: "http://localhost/uselectionmaps/" });
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("navigator", dom.window.navigator);
    const module = await import("../state.js");
    state = module.state;
    module.manifest.init({ parties: [
      { id: 20, key: "democratic", name: "Democratic", colour: "#0066cc" },
      { id: 21, key: "republican", name: "Republican", colour: "#cc0000" },
    ], mapModes: {}, files: {}, parliamentFeatures: { us_house: {
      polltrackerDataPath: "results/us-house-trends.json", polltrackerDefaultParties: [20, 21],
    }, us_senate: {
      uncertaintyModel: "senate",
      polltrackerDataPath: "results/us-senate-trends.json", polltrackerDefaultParties: [20, 21],
    }, us_presidential: {
      polltrackerDataPath: "results/us-president-trends.json", polltrackerDefaultParties: [20, 21],
      uncertaintyModel: "president",
    }, westminster: {
      polltrackerDataPath: "results/model_output_trends.json", polltrackerDefaultParties: [20, 21],
    } } });
    state.view = "polltracker";
    state.currentParliament = "us_house";
    state.currentElection = { model: true, mapId: 21 };
    view = await import("../features/polltracker-view.js");
    view.wirePollTrackerControls();
    fetchJson.mockResolvedValue(history());
  });
  afterEach(() => {
    dom?.window.close();
    vi.unstubAllGlobals();
  });

  it("defaults to seats and bands, selects both parties, and draws the real chart", async () => {
    await view.activatePollTrackerMode();
    expect(document.getElementById("mapsPollTrackerMetricSeats").checked).toBe(true);
    expect(document.getElementById("mapsPollTrackerMetricVotes").checked).toBe(false);
    expect(document.getElementById("mapsPollTrackerShowUncertainty").checked).toBe(true);
    expect([...document.querySelectorAll(".maps-polltracker-party-toggle input")].map((node) => [node.value, node.checked]))
      .toEqual([["20", true], ["21", true]]);
    expect(document.querySelectorAll("#mapsPollTrackerChartWrap svg")).toHaveLength(1);
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(2);
    expect(document.querySelectorAll(".maps-polltracker-band-shade")).toHaveLength(32);
  });

  it("draws separate Westminster segments and restarts trailing smoothing at each boundary", async () => {
    state.currentParliament = "westminster";
    fetchJson.mockResolvedValue(Array.from({ length: 6 }, (_, index) => ({
      as_of_date: `2026-09-0${index + 1}`, segment_id: index < 3 ? "old-config" : "new-config",
      hung: 0.5, parties: { 20: {
        s: 300, v: 40, sl: index < 3 ? 200 - index * 100 : 400,
        sh: index < 3 ? 400 - index * 100 : 600, pm: 0.5,
      } },
    })));
    await view.activatePollTrackerMode();
    const band = document.querySelector(".maps-polltracker-band--seats");
    expect(band.getAttribute("d").match(/M/g)).toHaveLength(2);
    expect(band.__data__).toEqual([
      [200, 400], [150, 350], [100, 300], [null, null], [400, 600], [400, 600], [400, 600],
    ]);
    expect(state.pollTrackerData.seriesByParty.get("20").seatLo)
      .toEqual([200, 100, 0, null, 400, 400, 400]);
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").textContent)
      .toContain("trailing average over up to 5 model updates");
    state.currentParliament = "us_house";
    fetchJson.mockResolvedValue(history());
    await view.activatePollTrackerMode();
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(2);
    expect(state.pollTrackerData.descriptor.model).toBe("house");
  });

  it("switches to majority / no-majority chances without exposing a largest-party choice", async () => {
    await view.activatePollTrackerMode();
    const chance = document.querySelector('[data-polltracker-view="chances"]');
    chance.checked = true;
    chance.dispatchEvent(new window.Event("change"));
    expect(document.querySelector('[data-polltracker-chance="pLargest"]').closest("label").hidden).toBe(true);
    expect(document.getElementById("mapsPollTrackerMetrics").hidden).toBe(true);
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").textContent).toContain("No majority");
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("Dotted = No majority");
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).not.toContain("Hung");
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(0);
  });

  it("applies All / 30 / 90 windows by dates and retains independent metric controls", async () => {
    await view.activatePollTrackerMode();
    document.querySelector('[data-polltracker-range="30"]').click();
    const band = document.querySelector(".maps-polltracker-band--seats");
    expect(band.__data__).toHaveLength(30);
    document.querySelector('[data-polltracker-range="90"]').click();
    expect(document.querySelector(".maps-polltracker-band--seats").__data__).toHaveLength(40);
    const votes = document.getElementById("mapsPollTrackerMetricVotes");
    votes.checked = true;
    votes.dispatchEvent(new window.Event("change"));
    expect(document.querySelectorAll(".maps-polltracker-band--votes")).toHaveLength(2);
  });

  it("ignores delayed data after the active parliament or view changes", async () => {
    let resolve;
    fetchJson.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const loading = view.activatePollTrackerMode();
    state.currentParliament = "westminster";
    state.view = "election";
    resolve(history());
    await loading;
    expect(state.pollTrackerData.timeline).toEqual([]);
    expect(document.querySelectorAll("#mapsPollTrackerChartWrap svg")).toHaveLength(0);
  });

  it("shows full Senate ranges and distinct caucus outcomes, with no chamber vote metric", async () => {
    state.currentParliament = "us_senate";
    fetchJson.mockResolvedValue(senateHistory());
    await view.activatePollTrackerMode();
    expect(state.pollTrackerData.seriesByParty.get("20").seats).toEqual([48]);
    expect(document.getElementById("mapsPollTrackerMetricVotes").closest("label").hidden).toBe(true);
    expect(document.getElementById("mapsPollTrackerMetricVotes").checked).toBe(false);
    expect(document.getElementById("mapsPollTrackerMetricSeats").checked).toBe(true);
    expect(document.getElementById("mapsPollTrackerShowUncertainty").checked).toBe(true);
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(2);
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("Full Senate seats (100)");
    const chances = document.querySelector('[data-polltracker-view="chances"]');
    chances.checked = true;
    chances.dispatchEvent(new window.Event("change"));
    expect(document.querySelector('[data-polltracker-chance="pMaj"]').parentElement.textContent).toContain("Caucus control");
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").textContent).toContain("overlaps control chances");
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("50–50 / unresolved");
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).not.toContain("Hung");
    state.currentParliament = "us_house";
    fetchJson.mockResolvedValue(history());
    await view.activatePollTrackerMode();
    expect(document.getElementById("mapsPollTrackerMetricVotes").closest("label").hidden).toBe(false);
    expect(document.querySelector('[data-polltracker-chance="pMaj"]').parentElement.textContent).toContain("Majority");
    expect(document.querySelector('[data-polltracker-view="totals"]').parentElement.textContent).toContain("Seats & votes");
  });

  it("explains unavailable legacy full Senate history without plotting contested seats", async () => {
    state.currentParliament = "us_senate";
    fetchJson.mockResolvedValue(JSON.parse(readFileSync(
      new URL("../../data/tests/fixtures/us_uncertainty/senate-legacy-trend.json", import.meta.url), "utf8",
    )));
    await view.activatePollTrackerMode();
    expect(state.pollTrackerData.seriesByParty.size).toBe(0);
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").hidden).toBe(false);
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").textContent).toContain("Legacy seats-up totals are not full-chamber totals");
    expect(document.getElementById("mapsPollTrackerViewControls").hidden).toBe(true);
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(0);
  });

  it("draws isolated saved-roster Senate points without lines, ranges or chances", async () => {
    state.currentParliament = "us_senate";
    const data = senateHistory({ known: false });
    const first = data.points[0];
    first.metrics = { senate_full_seats: {
      ...first.metrics.senate_full_seats,
      central: first.metrics.senate_full_seats.central,
    } };
    delete first.metrics.senate_full_seats.ranges;
    first.central_basis = { schema: "pf-us-senate-central-roster-v1",
      map_id: 22, target_cycle: 2026, as_of_date: first.as_of_date,
      roster_id: "saved-roster", field_id: "saved-field" };
    const next = structuredClone(first);
    next.as_of_date = "2026-10-21";
    next.central_basis.as_of_date = next.as_of_date;
    data.points.push(next);
    fetchJson.mockResolvedValue(data);
    await view.activatePollTrackerMode();
    expect(document.querySelectorAll(".maps-polltracker-isolated-point")).toHaveLength(4);
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(0);
    expect(document.getElementById("mapsPollTrackerViewControls").hidden).toBe(true);
    for (const line of document.querySelectorAll('#mapsPollTrackerChartWrap path[stroke="#1375E1"], #mapsPollTrackerChartWrap path[stroke="#E81B23"]')) {
      expect(line.getAttribute("d")).not.toContain("L");
    }
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").textContent).toContain("isolated points");
  });

  it("restores House defaults when reopening after changed toggles and dates", async () => {
    await view.activatePollTrackerMode();
    document.getElementById("mapsPollTrackerMetricSeats").checked = false;
    document.getElementById("mapsPollTrackerMetricVotes").checked = true;
    document.getElementById("mapsPollTrackerShowUncertainty").checked = false;
    document.querySelector('[data-polltracker-range="30"]').click();
    await view.activatePollTrackerMode();
    expect(document.getElementById("mapsPollTrackerMetricSeats").checked).toBe(true);
    expect(document.getElementById("mapsPollTrackerMetricVotes").checked).toBe(false);
    expect(document.getElementById("mapsPollTrackerShowUncertainty").checked).toBe(true);
    expect(document.querySelector('[data-polltracker-range="all"]').classList.contains("is-active")).toBe(true);
  });

  it("uses EVs, safe frozen context and electoral chances with popular vote off by default", async () => {
    state.currentParliament = "us_presidential";
    const data = structuredClone(presidentFixture);
    data.points[0].segment_id = "saved-president";
    data.points[0].display = { target_cycle: 2028, matchup: "<img src=x onerror=bad()> vs Republican" };
    fetchJson.mockResolvedValue(data);
    await view.activatePollTrackerMode();
    expect(state.pollTrackerData.seriesByParty.get("20").seats).toEqual([280]);
    expect(document.getElementById("mapsPollTrackerMetricVotes").checked).toBe(false);
    expect(document.getElementById("mapsPollTrackerMetricSeats").parentElement.textContent).toContain("Electoral votes");
    expect(document.getElementById("mapsPollTrackerContext").textContent).toContain("2028 · <img");
    expect(document.querySelector("#mapsPollTrackerContext img")).toBeNull();
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(2);
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("Electoral votes (538)");
    const chances = document.querySelector('[data-polltracker-view="chances"]');
    chances.checked = true;
    chances.dispatchEvent(new window.Event("change"));
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").textContent).toContain("270 electoral votes");
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("Electoral majority chance");
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("No electoral majority");
    expect(document.querySelector('[data-polltracker-chance="pLargest"]').closest("label").hidden).toBe(true);
  });

  it("plots historical President EV points with uncertainty controls and unknown matchup hidden", async () => {
    state.currentParliament = "us_presidential";
    const data = structuredClone(presidentFixture.points[0]);
    delete data.context;
    delete data.summary;
    delete data.metrics;
    fetchJson.mockResolvedValue([data]);
    await view.activatePollTrackerMode();
    expect(state.pollTrackerData.seriesByParty.get("20").seats).toEqual([280]);
    expect(document.getElementById("mapsPollTrackerUncertaintyToggle").hidden).toBe(true);
    expect(document.getElementById("mapsPollTrackerViewControls").hidden).toBe(true);
    expect(document.getElementById("mapsPollTrackerContext").hidden).toBe(true);
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(0);
    expect(document.querySelectorAll(".maps-polltracker-isolated-point")).toHaveLength(2);
  });
});

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
});

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchJson = vi.hoisted(() => vi.fn());
vi.mock("../files.js", () => ({ fetchJson }));
// The view's d3 import is written relative to the built bundle, not this source tree.
vi.mock("../site/vendor/d3.v7.esm.js", () => import("../../site/vendor/d3.v7.esm.js"));

// The browser harness is optional locally; CI can supply a jsdom 24 installation
// without making it a production dependency of the static website.
let JSDOM;
try {
  ({ JSDOM } = createRequire(import.meta.url)(process.env.PF_TEST_JSDOM || "jsdom"));
} catch { /* Actual-module browser checks run when the optional harness is supplied. */ }

const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");
const fragment = readFileSync(new URL("../fragments/polltracker.html", import.meta.url), "utf8");

/** Forty daily Westminster runs in one segment, each with seat/vote bands and chances. */
function history() {
  return Array.from({ length: 40 }, (_, index) => ({
    as_of_date: new Date(Date.UTC(2026, 8, 1 + index)).toISOString().slice(0, 10),
    segment_id: "stable-meaning", hung: 0.4,
    parties: {
      20: { s: 320, v: 38, sl: 280, sh: 360, vl: 35, vh: 41, pm: 0.55, pl: 0.8 },
      21: { s: 180, v: 27, sl: 140, sh: 220, vl: 24, vh: 30, pm: 0.05, pl: 0.2 },
    },
  }));
}

describe.skipIf(!JSDOM)("actual poll tracker view", () => {
  let dom;
  let state;
  let view;
  beforeEach(async () => {
    vi.resetModules();
    fetchJson.mockReset();
    dom = new JSDOM(shell + fragment, { url: "http://localhost/electionmaps/" });
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("navigator", dom.window.navigator);
    const module = await import("../state.js");
    state = module.state;
    module.manifest.init({ parties: [
      { id: 20, key: "labour", name: "Labour", colour: "#e4003b" },
      { id: 21, key: "conservative", name: "Conservative", colour: "#0087dc" },
    ], mapModes: {}, files: {}, parliamentFeatures: { westminster: {
      polltrackerDataPath: "results/model_output_trends.json", polltrackerDefaultParties: [20, 21],
    } } });
    state.view = "polltracker";
    state.currentParliament = "westminster";
    state.currentElection = { model: true, mapId: 1 };
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
    expect(document.getElementById("mapsPollTrackerUncertaintyToggle").hidden).toBe(false);
    expect([...document.querySelectorAll(".maps-polltracker-party-toggle input")].map((node) => [node.value, node.checked]))
      .toEqual([["20", true], ["21", true]]);
    expect(document.querySelectorAll("#mapsPollTrackerChartWrap svg")).toHaveLength(1);
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(2);
    expect(document.querySelectorAll(".maps-polltracker-band-shade")).toHaveLength(32);
  });

  it("switches to majority / hung chances with the largest-party measure available", async () => {
    await view.activatePollTrackerMode();
    expect(document.getElementById("mapsPollTrackerViewControls").hidden).toBe(false);
    const chance = document.querySelector('[data-polltracker-view="chances"]');
    chance.checked = true;
    chance.dispatchEvent(new window.Event("change"));
    expect(document.getElementById("mapsPollTrackerChanceControls").hidden).toBe(false);
    expect(document.querySelector('[data-polltracker-chance="pLargest"]').closest("label").hidden).toBe(false);
    expect(document.getElementById("mapsPollTrackerMetrics").hidden).toBe(true);
    expect(document.getElementById("mapsPollTrackerUncertaintyNote").textContent)
      .toContain("Hung means no party wins a majority");
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("Solid = Majority chance, Dotted = Hung");
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(0);
    const largest = document.querySelector('[data-polltracker-chance="pLargest"]');
    largest.checked = true;
    largest.dispatchEvent(new window.Event("change"));
    expect(document.getElementById("mapsPollTrackerChartWrap").textContent).toContain("Solid = Largest-party chance");
  });

  it("applies All / 30 / 90 windows by dates and retains independent metric controls", async () => {
    await view.activatePollTrackerMode();
    expect(document.querySelector(".maps-polltracker-band--seats").__data__).toHaveLength(40);
    document.querySelector('[data-polltracker-range="30"]').click();
    const band = document.querySelector(".maps-polltracker-band--seats");
    expect(band.__data__).toHaveLength(30);
    document.querySelector('[data-polltracker-range="90"]').click();
    expect(document.querySelector(".maps-polltracker-band--seats").__data__).toHaveLength(40);
    const votes = document.getElementById("mapsPollTrackerMetricVotes");
    votes.checked = true;
    votes.dispatchEvent(new window.Event("change"));
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(2);
    expect(document.querySelectorAll(".maps-polltracker-band--votes")).toHaveLength(2);
  });

  it.each(["parliament", "view", "newer activation"])("ignores delayed data after the %s changes", async (change) => {
    const pending = [];
    fetchJson.mockImplementation(() => new Promise((done) => { pending.push(done); }));
    const loading = view.activatePollTrackerMode();
    if (change === "parliament") state.currentParliament = "scottish_parliament";
    if (change === "view") state.view = "election";
    if (change === "newer activation") view.activatePollTrackerMode();
    pending[0](history());
    await loading;
    expect(state.pollTrackerData.timeline).toEqual([]);
    expect(document.querySelectorAll("#mapsPollTrackerChartWrap svg")).toHaveLength(0);
  });

  it("draws separate Westminster segments and restarts trailing smoothing at each boundary", async () => {
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
  });
});

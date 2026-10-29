import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

let JSDOM;
try {
  ({ JSDOM } = createRequire(import.meta.url)(process.env.PF_TEST_JSDOM || "jsdom"));
} catch { /* Use the project's optional actual-module browser harness. */ }

const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");

describe.skipIf(!JSDOM)("open race popup uncertainty interaction", () => {
  let browser;
  afterEach(() => {
    browser?.window.close();
    vi.unstubAllGlobals();
  });

  it("shows and hides bands in the existing popup when the shared toggle is clicked", async () => {
    vi.resetModules();
    browser = new JSDOM(shell, { url: "http://localhost/uselectionmaps/", pretendToBeVisual: true });
    for (const key of ["window", "document", "navigator", "Node", "SVGElement", "Element", "HTMLElement"])
      vi.stubGlobal(key, browser.window[key]);
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
    // D3's browser SVG extent is not implemented by jsdom.
    const svg = document.querySelector(".maps-svg");
    Object.defineProperty(svg, "viewBox", { value: { baseVal: { x: 0, y: 0, width: 1200, height: 900 } } });
    Object.defineProperty(svg, "width", { value: { baseVal: { value: 1200 } } });
    Object.defineProperty(svg, "height", { value: { baseVal: { value: 900 } } });
    const { manifest, state, ElectionData, Seat } = await import("../state.js");
    manifest.init({ parties: [
      { id: 20, key: "democratic", name: "Democratic", colour: "#0066cc" },
      { id: 21, key: "republican", name: "Republican", colour: "#cc0000" },
    ], mapModes: {}, files: {}, parliamentFeatures: { us_house: { uncertaintyModel: "house" } } });
    state.view = "election";
    state.currentParliament = "us_house";
    state.currentElection = { id: "current-us-house", name: "Synthetic interaction fixture", model: true, mapId: 21 };
    state.electionData = ElectionData.fromSeats([new Seat({
      seat: "Synthetic race", region: "synthetic", winner: "democratic",
      votes: { democratic: 55, republican: 45 },
      u: { democratic: [45, 65, 0.7], republican: [35, 55, 0.3] },
    })]);
    state.electionData.uncertainty = {
      model: "house", interval: 0.9,
      national: { parties: {
        democratic: { s: [200, 220, 240], v: [45, 65] },
        republican: { s: [195, 215, 235], v: [35, 55] },
      } },
    };
    state.voteTotals.showUncertainty = false;
    state.voteTotals.mode = "all";
    state.comparisonElectionData = null;
    state.mapFilters = { party: "all", secondParty: "all", region: "all",
      majorityMin: 0, majorityMax: 100, gainsOnly: false, upcoming: "all" };
    state.mapChoropleths = { type: "none", party: "all" };
    state.mapData = { type: "Topology", arcs: [], objects: { races: {
      type: "GeometryCollection", geometries: [{ type: "Point", coordinates: [-100, 40],
        properties: { name: "Synthetic race" } }],
    } } };
    state.setupMapData();
    const view = await import("../dom.js");
    view.wireInit();
    view.renderMap();
    document.querySelector(".maps-seat-row").click();
    const popup = document.getElementById("mapsSeatPopup");
    const toggle = document.getElementById("mapsUncertaintyToggle");
    expect(popup.hidden).toBe(false);
    expect(popup.querySelectorAll(".maps-popup-uncertainty")).toHaveLength(0);
    toggle.click();
    expect(document.getElementById("mapsSeatPopup")).toBe(popup);
    expect(popup.hidden).toBe(false);
    expect(popup.querySelectorAll(".maps-popup-uncertainty")).toHaveLength(2);
    expect(popup.textContent).toContain("win 70%");
    expect(document.getElementById("mapsVoteTotalsTable").classList.contains("hide-uncertainty-cols")).toBe(false);
    toggle.click();
    expect(popup.hidden).toBe(false);
    expect(popup.querySelectorAll(".maps-popup-uncertainty")).toHaveLength(0);
    expect(document.getElementById("mapsVoteTotalsTable").classList.contains("hide-uncertainty-cols")).toBe(true);
  });
});

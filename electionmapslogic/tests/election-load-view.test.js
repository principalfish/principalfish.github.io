import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchJson = vi.hoisted(() => vi.fn());
vi.mock("../files.js", () => ({ fetchJson }));
// The vendored topojson build points at a missing source map; one square seat is enough here.
vi.mock("../../site/vendor/topojson-client.v3.esm.js", () => ({
  feature: () => ({ type: "FeatureCollection", features: [{
    type: "Feature", properties: { name: "Testtown", region: "london" },
    geometry: { type: "Polygon", coordinates: [[[0, 51], [0, 52], [1, 52], [1, 51], [0, 51]]] },
  }] }),
  mesh: () => ({ type: "MultiLineString", coordinates: [] }),
  merge: () => null,
}));

// jsdom 24 is a devDependency (AGENTS.md pins 24: later majors fail to load on Node 21).
// PF_TEST_JSDOM overrides it; the suites skip if the chosen jsdom fails to load.
let JSDOM;
try {
  ({ JSDOM } = createRequire(import.meta.url)(process.env.PF_TEST_JSDOM || "jsdom"));
} catch { /* Without jsdom the browser suites below are skipped. */ }

// The page's own header (electionmaps/index.html) holds the title, subtitle and countdown the
// engine renders into; the shell holds the rest.
const header = `<header class="maps-header"><h1 class="maps-title"></h1>
  <p id="mapsSubtitle" class="maps-subtitle"></p><p id="mapsElectionCountdown" hidden></p></header>`;
const shell = header + readFileSync(new URL("../shell.html", import.meta.url), "utf8");

const HEADLINE = "Labour 282 seats (199–359) · majority 19% (if an election were held today)";
const SIMULATED = "Current prediction · Hung parliament likely (81%) · Labour largest party (94%)";
// The one-seat uniform-swing projection the main line shows without uncertainty.
const UNIFORM_SWING = "Current prediction · Labour majority: 1";

function mapModes() {
  return {
    misc: { title: "UK Election Maps" },
    defaultElection: "current-prediction",
    parties: [
      { id: 1, key: "labour", name: "Labour", colour: "#e4003b" },
      { id: 2, key: "reform", name: "Reform UK", colour: "#12b6cf" },
    ],
    elections: [
      { id: "current-prediction", name: "Current prediction", mapId: 2, parliament: "westminster", model: true },
      { id: "2024-general", name: "2024 General Election", mapId: 2, parliament: "westminster" },
    ],
    mapModes: { 2: { name: "westminster-2024", regions: [{ id: 17, name: "London" }] } },
    files: {
      elections: {
        mapsById: { 2: "maps/map-2.topo.json" },
        electionsById: { "current-prediction": "results/prediction.json", "2024-general": "results/2024.json" },
      },
      meta: { westminster: "results/meta.json" },
    },
    parliamentFeatures: { westminster: {
      predictAnchorElectionId: "current-prediction", uncertaintyDataPath: "results/uncertainty.json",
    } },
  };
}

/** One seat whose saved run carries a per-party band: Reform's win chance rounds to "<1%". */
function results(u = [[1, 48.5, 61.25, 0.87], [2, 30, 41, 0]]) {
  return {
    uncertaintyId: "saved-run",
    seats: [{ n: "Testtown", r: 17, w: 1, p: [[1, 600], [2, 400]], u }],
  };
}

function uncertainty() {
  return {
    schema: "pf-uncertainty-v1", uncertaintyId: "saved-run", interval: 0.9, simulations: 10000,
    national: {
      parties: {
        1: { v: [21.7, 29.3], s: [199, 282, 359], pm: 0.19, pl: 0.94 },
        2: { v: [19.2, 27.1], s: [114, 120, 130], pm: 0, pl: 0.06 },
      },
      hung: 0.81,
    },
    regions: {},
  };
}

/** Serve each data path; a function value is called per request (e.g. to hold it pending). */
function serve(overrides = {}) {
  const files = {
    "data/map-modes.json": mapModes(),
    "data/results/meta.json": { latest_poll_snippet: "" },
    "data/maps/map-2.topo.json": { type: "Topology", objects: { seats: { type: "GeometryCollection", geometries: [] } }, arcs: [] },
    "data/results/prediction.json": results(),
    "data/results/2024.json": results(),
    "data/results/uncertainty.json": uncertainty(),
    ...overrides,
  };
  fetchJson.mockImplementation(async (url) => {
    if (!(url in files)) throw new Error(`unexpected fetch ${url}`);
    const value = files[url];
    return typeof value === "function" ? value() : structuredClone(value);
  });
}

/** A promise held open until the test resolves it. */
function pending() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

describe.skipIf(!JSDOM)("actual election load", () => {
  let dom;
  let state;
  let startApp;
  let consoleError;
  let d3;

  async function boot(search = "") {
    dom = new JSDOM(shell, { url: `http://localhost/electionmaps/${search}` });
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("navigator", dom.window.navigator);
    // d3-zoom sizes its extent from the svg's viewBox via the global SVGElement; jsdom has
    // the constructor but no SVGAnimatedRect, so serve the declared viewBox attribute.
    vi.stubGlobal("SVGElement", dom.window.SVGElement);
    // Zooming to a selected seat raises its path with d3's Node-based ordering.
    vi.stubGlobal("Node", dom.window.Node);
    Object.defineProperty(dom.window.SVGSVGElement.prototype, "viewBox", {
      get() {
        const [x, y, width, height] = this.getAttribute("viewBox").split(/\s+/).map(Number);
        return { baseVal: { x, y, width, height } };
      },
    });
    // startApp reports load failures to the console; any here is a broken harness or a bug.
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    // dom.js observes panel sizes at module load; jsdom has no layout to observe.
    const Observer = class { observe() {} unobserve() {} disconnect() {} };
    vi.stubGlobal("ResizeObserver", Observer);
    vi.stubGlobal("IntersectionObserver", Observer);
    ({ state } = await import("../state.js"));
    ({ startApp } = await import("../app.js"));
    // The same d3 instance dom.js loaded, so afterEach can stop its zoom transitions.
    d3 = await import("../../site/vendor/d3.v7.esm.js");
  }

  beforeEach(() => {
    vi.resetModules();
    fetchJson.mockReset();
    serve();
  });
  afterEach(() => {
    const errors = consoleError?.mock.calls ?? [];
    consoleError?.mockRestore();
    // Clicking a seat starts a 1.5 s zoom; left running, it ticks after the globals it reads
    // (SVGElement) are unstubbed and fails the run with an unhandled error.
    if (dom) d3?.selectAll(dom.window.document.querySelectorAll("svg")).interrupt();
    d3 = null;
    dom?.window.close();
    vi.unstubAllGlobals();
    vi.doUnmock("../uncertainty.js");
    // Last, so a failure here cannot skip the cleanup above.
    expect(errors).toEqual([]);
  });

  const headline = () => document.querySelector("#mapsSubtitle .maps-subtitle-uncertainty");
  const mainLine = () => document.querySelector("#mapsSubtitle .maps-subtitle-main");
  const popupBands = () => [...document.querySelectorAll("#mapsSeatPopupList .maps-popup-uncertainty")];
  const openSeatPopup = () => document.querySelector("#mapsSeatList .maps-seat-row").click();

  describe("Monte Carlo headline", () => {
    it("renders after a model election loads and records the route", async () => {
      await boot();
      await startApp();
      expect(state.electionData.uncertainty.national.hung).toBe(0.81);
      expect(headline().textContent).toBe(HEADLINE);
      expect(mainLine().textContent).toBe(SIMULATED);
      expect(document.querySelectorAll("#mapContent .maps-region-path")).toHaveLength(1);
      expect(dom.window.location.search).toBe("?view=election&election=current-prediction");
    });

    it("ignores results that arrive after the election changes", async () => {
      const held = pending();
      serve({ "data/results/prediction.json": () => held.promise });
      await boot();
      const loading = startApp();
      await vi.waitFor(() => expect(fetchJson).toHaveBeenCalledWith("data/results/prediction.json"));
      const original = state.currentElection;
      state.currentElection = { ...original, id: "2024-general", model: false };
      held.resolve(results());
      await loading;
      expect(state.electionData).toBeNull();
      expect(fetchJson).not.toHaveBeenCalledWith("data/results/uncertainty.json");
      expect(headline()).toBeNull();
      expect(dom.window.location.search).toBe("");
    });

    it("ignores an uncertainty summary that arrives after the election changes", async () => {
      const held = pending();
      serve({ "data/results/uncertainty.json": () => held.promise });
      await boot();
      const loading = startApp();
      await vi.waitFor(() => expect(fetchJson).toHaveBeenCalledWith("data/results/uncertainty.json"));
      const loaded = state.electionData;
      state.currentElection = { ...state.currentElection };
      held.resolve(uncertainty());
      await loading;
      expect(loaded.uncertainty).toBeNull();
      expect(headline()).toBeNull();
      expect(mainLine().textContent).toBe(UNIFORM_SWING);
      expect(dom.window.location.search).toBe("");
    });
  });

  describe("main subtitle line", () => {
    it("keeps the uniform-swing line for a saved model without uncertainty", async () => {
      serve({ "data/results/uncertainty.json": () => Promise.reject(new Error("missing")) });
      await boot();
      await startApp();
      expect(state.electionData.uncertainty).toBeNull();
      expect(mainLine().textContent).toBe(UNIFORM_SWING);
      expect(headline()).toBeNull();
    });

    it("keeps the uniform-swing line for a historical election", async () => {
      await boot("?parliament=westminster&election=2024-general");
      await startApp();
      expect(mainLine().textContent).toBe("2024 General Election · Labour majority: 1");
      expect(headline()).toBeNull();
    });

    it("returns to the uniform-swing line in predict view", async () => {
      await boot();
      await startApp();
      expect(mainLine().textContent).toBe(SIMULATED);
      state.view = "predict";
      const { renderHeader } = await import("../dom.js");
      renderHeader(state.electionData.summary.text);
      expect(mainLine().textContent).toBe(UNIFORM_SWING);
      expect(headline()).toBeNull();
    });

    it("keeps the summary line on a US-style model election with no uncertainty path", async () => {
      serve({
        "data/map-modes.json": {
          misc: { title: "US Elections" },
          defaultElection: "current-us-house",
          parties: [
            { id: 1, key: "democrat", name: "Democratic", colour: "#3333ff" },
            { id: 2, key: "republican", name: "Republican", colour: "#e81b23" },
          ],
          elections: [{
            id: "current-us-house", name: "2026 Prediction", mapId: 21, parliament: "us_house", model: true,
          }],
          mapModes: { 21: { name: "us-house-2024", regions: [{ id: 17, name: "California" }] } },
          files: { elections: {
            mapsById: { 21: "maps/map-21.topo.json" },
            electionsById: { "current-us-house": "results/us-house.json" },
          } },
          parliamentFeatures: { us_house: { predictAnchorElectionId: "current-us-house" } },
        },
        "data/maps/map-21.topo.json": { type: "Topology", objects: { seats: { type: "GeometryCollection", geometries: [] } }, arcs: [] },
        "data/results/us-house.json": results(),
      });
      await boot();
      await startApp();
      expect(state.electionData.uncertainty).toBeFalsy();
      expect(fetchJson).not.toHaveBeenCalledWith(expect.stringContaining("uncertainty"));
      expect(mainLine().textContent).toBe(state.electionData.summary.text);
      expect(mainLine().textContent).toBe("2026 Prediction · Democratic majority: 1");
      expect(headline()).toBeNull();
    });

    it("inserts party names as text, not markup", async () => {
      const modes = mapModes();
      modes.parties[0].name = "<b>Labour</b> & co";
      serve({ "data/map-modes.json": modes });
      await boot();
      await startApp();
      expect(mainLine().textContent).toBe(
        "Current prediction · Hung parliament likely (81%) · <b>Labour</b> & co largest party (94%)",
      );
      expect(mainLine().children).toHaveLength(0);
      expect(headline().textContent.startsWith("<b>Labour</b> & co 282 seats")).toBe(true);
      expect(headline().children).toHaveLength(0);
    });
  });

  describe("seat popup band", () => {
    it("shows each party's band and win chance on a saved model", async () => {
      await boot();
      await startApp();
      openSeatPopup();
      expect(document.getElementById("mapsSeatPopup").hidden).toBe(false);
      expect(popupBands().map((node) => node.textContent))
        .toEqual(["(48.5–61.25%) · win 87%", "(30–41%) · win <1%"]);
    });

    it("escapes the band text as text, not markup", async () => {
      vi.doMock("../uncertainty.js", async (importOriginal) => ({
        ...(await importOriginal()),
        seatUncertaintyText: () => "win <1% & <b>bold</b>",
      }));
      await boot();
      await startApp();
      openSeatPopup();
      const [band] = popupBands();
      expect(band.textContent).toBe("win <1% & <b>bold</b>");
      expect(band.children).toHaveLength(0);
      expect(band.innerHTML).toBe("win &lt;1% &amp; &lt;b&gt;bold&lt;/b&gt;");
    });

    it("leaves the band out in predict view", async () => {
      await boot();
      await startApp();
      openSeatPopup();
      expect(popupBands()).toHaveLength(2);
      // A predict projection refreshes the open popup in place.
      state.view = "predict";
      const { refreshOpenSeatPopup } = await import("../dom.js");
      refreshOpenSeatPopup();
      expect(document.getElementById("mapsSeatPopup").hidden).toBe(false);
      expect(popupBands()).toHaveLength(0);
    });

    it("leaves the band out when the election is not a model", async () => {
      await boot("?parliament=westminster&election=2024-general");
      await startApp();
      expect(state.currentElection.model).toBeUndefined();
      openSeatPopup();
      expect(document.querySelectorAll("#mapsSeatPopupList .maps-popup-row")).toHaveLength(2);
      expect(popupBands()).toHaveLength(0);
    });

    it("leaves the band out when the seat carries none", async () => {
      serve({ "data/results/prediction.json": results([]) });
      await boot();
      await startApp();
      openSeatPopup();
      expect(document.querySelectorAll("#mapsSeatPopupList .maps-popup-row")).toHaveLength(2);
      expect(popupBands()).toHaveLength(0);
    });
  });
});

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d3 from "../../site/vendor/d3.v7.esm.js";

const fetchJson = vi.hoisted(() => vi.fn());
vi.mock("../files.js", () => ({ fetchJson }));
// The view's d3 import is written relative to the built bundle, not this source tree.
vi.mock("../site/vendor/d3.v7.esm.js", () => import("../../site/vendor/d3.v7.esm.js"));
// dom.js (imported for the header test) loads topojson, whose vendored build points at a missing
// source map; no map is drawn here.
vi.mock("../../site/vendor/topojson-client.v3.esm.js", () => ({ feature: vi.fn(), mesh: vi.fn(), merge: vi.fn() }));

// jsdom 24 is a devDependency (AGENTS.md pins 24: later majors fail to load on Node 21).
// PF_TEST_JSDOM overrides it; the suites skip if the chosen jsdom fails to load.
let JSDOM;
try {
  ({ JSDOM } = createRequire(import.meta.url)(process.env.PF_TEST_JSDOM || "jsdom"));
} catch { /* Without jsdom the browser suites below are skipped. */ }

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

/** Forty simulated daily runs from Saturday 2026-08-29, so the first weekly tick is a day in. */
function historyFromSaturday() {
  return history().map((run, index) => ({
    ...run, as_of_date: new Date(Date.UTC(2026, 7, 29 + index)).toISOString().slice(0, 10),
  }));
}

/** A central-only election-day run a month before the simulated history begins on 09-01. */
function historyWithElectionDay() {
  return [
    { as_of_date: "2026-08-01", parties: { 20: { s: 411, v: 34 }, 21: { s: 121, v: 24 } } },
    ...history(),
  ];
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

  /** Tick or untick the tracker's Uncertainty box as a reader would. */
  const tickUncertainty = (checked = true) => {
    const input = document.getElementById("mapsPollTrackerShowUncertainty");
    input.checked = checked;
    input.dispatchEvent(new window.Event("change"));
  };

  it("defaults to seats without uncertainty, selects both parties, and draws the real chart", async () => {
    await view.activatePollTrackerMode();
    expect(document.getElementById("mapsPollTrackerMetricSeats").checked).toBe(true);
    expect(document.getElementById("mapsPollTrackerMetricVotes").checked).toBe(false);
    expect(document.getElementById("mapsPollTrackerShowUncertainty").checked).toBe(false);
    expect(document.getElementById("mapsPollTrackerUncertaintyToggle").hidden).toBe(false);
    expect([...document.querySelectorAll(".maps-polltracker-party-toggle input")].map((node) => [node.value, node.checked]))
      .toEqual([["20", true], ["21", true]]);
    expect(document.querySelectorAll("#mapsPollTrackerChartWrap svg")).toHaveLength(1);
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(0);
    tickUncertainty();
    expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(2);
    expect(document.querySelectorAll(".maps-polltracker-band-shade")).toHaveLength(32);
  });

  it("puts the latest poll below the chart, outside the tracker card and the subtitle", async () => {
    const footer = document.getElementById("mapsPollTrackerFooter");
    expect(footer.parentElement.id).toBe("mapsPollTrackerView");
    expect(footer.previousElementSibling.classList.contains("maps-polltracker-card")).toBe(true);
    // dom.js observes panel sizes at module load; jsdom has no layout to observe.
    const Observer = class { observe() {} unobserve() {} disconnect() {} };
    vi.stubGlobal("ResizeObserver", Observer);
    vi.stubGlobal("IntersectionObserver", Observer);
    // The page's own header, as in electionmaps/index.html.
    const header = document.createElement("header");
    header.innerHTML = '<h1 class="maps-title"></h1><p id="mapsSubtitle"></p><p id="mapsElectionCountdown" hidden></p>';
    document.body.prepend(header);
    const subtitle = document.getElementById("mapsSubtitle");
    state.predictionSnippet = "Latest poll used: Opinium (2026-10-28 to 2026-10-30)";
    const { renderHeader } = await import("../dom.js");
    renderHeader();
    expect(subtitle.textContent).toBe("Poll tracker · model output trends");
    expect(footer.hidden).toBe(false);
    expect([...footer.children].map((node) => [node.className, node.textContent]))
      .toEqual([["maps-map-footer-latest", state.predictionSnippet]]);
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
    tickUncertainty();
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

  describe("chart layout", () => {
    // jsdom has no layout, so the chart is drawn at its 760px floor with an 84px right margin.
    const WIDTH = 760;
    const RIGHT = 84;
    const plot = () => document.querySelector("#mapsPollTrackerChartWrap svg > g");
    const change = (selector, checked = true) => {
      const input = document.querySelector(selector);
      input.checked = checked;
      input.dispatchEvent(new window.Event("change"));
    };
    const MODES = {
      "Seats only": () => {},
      "Seats and Vote %": () => change("#mapsPollTrackerMetricVotes"),
      "Vote % only": () => {
        change("#mapsPollTrackerMetricVotes");
        change("#mapsPollTrackerMetricSeats", false);
      },
      "Chances, majority": () => change('[data-polltracker-view="chances"]'),
      "Chances, largest party": () => {
        change('[data-polltracker-view="chances"]');
        change('[data-polltracker-chance="pLargest"]');
      },
    };
    const LEFT = {
      "Seats only": 70, "Seats and Vote %": 70, "Vote % only": 16,
      "Chances, majority": 16, "Chances, largest party": 16,
    };

    it.each(Object.keys(MODES))("keeps the gridlines inside the plot and the gutter to its axes: %s", async (mode) => {
      await view.activatePollTrackerMode();
      MODES[mode]();
      const left = LEFT[mode];
      const innerWidth = WIDTH - left - RIGHT;
      expect(plot().getAttribute("transform")).toBe(`translate(${left},14)`);
      const grid = document.querySelector(".maps-polltracker-grid-line").closest("g.maps-polltracker-axis");
      expect(grid.getAttribute("transform")).toBeNull();
      const lines = [...grid.querySelectorAll(".maps-polltracker-grid-line")];
      expect(lines.length).toBeGreaterThan(2);
      for (const line of lines) {
        expect(Number(line.getAttribute("x1") ?? 0)).toBe(0);
        expect(Number(line.getAttribute("x2"))).toBe(innerWidth);
      }
      // A left axis label is drawn only with the Seats axis that the wide gutter holds.
      const labels = [...document.querySelectorAll(".maps-polltracker-axis-label")].map((node) => node.textContent);
      expect(labels.includes("Seats")).toBe(left === 70);
    });

    const timelineStart = () => state.pollTrackerData.timeline[0].dateKey;
    const hungPath = () => document.querySelector('#mapsPollTrackerChartWrap path[stroke="#475569"]');
    const labourPath = () => document.querySelector('#mapsPollTrackerChartWrap path[stroke="#e4003b"]');

    it.each(["Chances, majority", "Chances, largest party"])(
      "starts the %s timeline on the first date with chances",
      async (mode) => {
        fetchJson.mockResolvedValue(historyWithElectionDay());
        await view.activatePollTrackerMode();
        expect(timelineStart()).toBe("2026-08-01");
        MODES[mode]();
        // The 40 simulated days fill the plot from its left edge.
        expect(hungPath().__data__).toHaveLength(40);
        expect(hungPath().getAttribute("d")).toMatch(/^M0,/);
        expect(labourPath().__data__).toHaveLength(40);
        expect(labourPath().getAttribute("d")).toMatch(/^M0,/);
        const ticks = [...document.querySelectorAll(".maps-polltracker-axis .tick text")].map((node) => node.textContent);
        expect(ticks.filter((tick) => /^\d{4}-/.test(tick)).every((tick) => tick >= "2026-09-01")).toBe(true);
      },
    );

    it("leaves a range window that starts after the first chance unchanged", async () => {
      fetchJson.mockResolvedValue(historyWithElectionDay());
      await view.activatePollTrackerMode();
      MODES["Chances, majority"]();
      document.querySelector('[data-polltracker-range="30"]').click();
      expect(hungPath().__data__).toHaveLength(30);
      expect(hungPath().__data__).toEqual(state.pollTrackerData.hung.slice(-30).map((value) => value * 100));
    });

    const xTicks = () => [...document.querySelectorAll(".maps-polltracker-axis .tick")]
      .filter((tick) => /^\d{4}-/.test(tick.textContent))
      .map((tick) => [tick.textContent, Number(tick.getAttribute("transform").match(/translate\(([-\d.]+)/)[1])]);

    it("keeps the natural date ticks under the wide Seats gutter, even one a day in", async () => {
      fetchJson.mockResolvedValue(historyFromSaturday());
      await view.activatePollTrackerMode();
      const innerWidth = WIDTH - 70 - RIGHT;
      const natural = d3.scaleTime()
        .domain(d3.extent(state.pollTrackerData.timeline, (entry) => entry.dateValue)).range([0, innerWidth])
        .ticks(Math.floor(innerWidth / 105)).map((tick) => d3.timeFormat("%Y-%m-%d")(tick));
      expect(xTicks().map(([date]) => date)).toEqual(natural);
      expect(xTicks()[0][0]).toBe("2026-08-30");
      expect(xTicks()[0][1]).toBeLessThan(40);
    });

    it.each(["Vote % only", "Chances, majority", "Chances, largest party"])(
      "drops a date tick whose label would run off the narrow gutter: %s",
      async (mode) => {
        fetchJson.mockResolvedValue(historyFromSaturday());
        await view.activatePollTrackerMode();
        MODES[mode]();
        const ticks = xTicks();
        expect(ticks.map(([date]) => date)).not.toContain("2026-08-30");
        expect(ticks.length).toBeGreaterThan(2);
        // A rotated label reaches about 56px left of its tick; the gutter is 16px.
        for (const [, x] of ticks) expect(16 + x).toBeGreaterThanOrEqual(56);
      },
    );

    it("starts the chances timeline at the first largest-party chance when no hung odds exist", async () => {
      await view.activatePollTrackerMode();
      const data = state.pollTrackerData;
      data.hung = data.hung.map(() => null);
      for (const series of data.seriesByParty.values()) {
        series.pMaj = series.pMaj.map(() => null);
        series.pLargest = series.pLargest.map((value, index) => (index < 15 ? null : value));
      }
      MODES["Chances, largest party"]();
      expect(labourPath().__data__).toHaveLength(data.timeline.length - 15);
      expect(labourPath().getAttribute("d")).toMatch(/^M0,/);
      expect(xTicks().every(([date]) => date >= data.timeline[15].dateKey)).toBe(true);
    });

    it("keeps the Seats & votes timeline from the election-day point", async () => {
      fetchJson.mockResolvedValue(historyWithElectionDay());
      await view.activatePollTrackerMode();
      MODES["Seats and Vote %"]();
      const length = state.pollTrackerData.timeline.length;
      expect(length).toBeGreaterThan(40);
      expect(labourPath().__data__).toHaveLength(length);
      expect(labourPath().__data__[0]).toBe(411);
      expect(labourPath().getAttribute("d")).toMatch(/^M0,/);
    });

    it("draws the uniform-swing seats unticked, and each run's mean with the bands ticked", async () => {
      const [electionDay, ...runs] = historyWithElectionDay();
      fetchJson.mockResolvedValue([electionDay, ...runs.map((run) => ({
        ...run, parties: { ...run.parties, 20: { ...run.parties[20], sm: 317.4 } },
      }))]);
      await view.activatePollTrackerMode();
      MODES["Seats only"]();
      const note = () => document.getElementById("mapsPollTrackerUncertaintyNote").textContent;
      // Unticked (the default): the uniform-swing `s` line, no bands.
      let seats = labourPath().__data__;
      expect(seats[0]).toBe(411);
      expect(seats.at(-1)).toBe(320);
      expect(seats).not.toContain(317.4);
      expect(document.querySelectorAll(".maps-polltracker-band--seats")).toHaveLength(0);
      expect(note()).toBe("Seat lines show the uniform-swing count. Tick Uncertainty for the simulated mean seats and 90% ranges.");
      // Ticked: the mean `sm` line, the election-day result kept, and the bands.
      tickUncertainty();
      seats = labourPath().__data__;
      expect(seats[0]).toBe(411);
      expect(seats.at(-1)).toBe(317.4);
      expect(seats).not.toContain(320);
      const band = document.querySelector(".maps-polltracker-band--seats").__data__;
      expect(band.at(-1)).toEqual([280, 360]);
      expect(note()).toMatch(/^Seat lines show the simulated mean seats\. Shaded bands show 90% seat ranges/);
      // Unticking returns to the uniform-swing line.
      tickUncertainty(false);
      expect(labourPath().__data__.at(-1)).toBe(320);
    });
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
    tickUncertainty();
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

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../files.js", () => ({ fetchJson: vi.fn() }));
// The table never draws the map, and the vendored topojson build points at a missing source map.
vi.mock("../../site/vendor/topojson-client.v3.esm.js", () => ({ feature: vi.fn(), mesh: vi.fn(), merge: vi.fn() }));

// jsdom 24 is a devDependency (AGENTS.md pins 24: later majors fail to load on Node 21).
// PF_TEST_JSDOM overrides it; the suites skip if the chosen jsdom fails to load.
let JSDOM;
try {
  ({ JSDOM } = createRequire(import.meta.url)(process.env.PF_TEST_JSDOM || "jsdom"));
} catch { /* Without jsdom the browser suites below are skipped. */ }

const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");

/**
 * Stored simulations for Labour and Reform nationally and in London. Each party's uniform-swing
 * count (summary below) differs from its median, so a stray count shows up in the table text.
 */
function payload() {
  return {
    schema: "pf-uncertainty-v1", uncertaintyId: "saved-run", interval: 0.9, simulations: 10000,
    basis: "Nowcast: if an election were held today.",
    national: {
      parties: {
        1: { v: [21.7, 29.3], s: [199, 282, 359], pm: 0.19, pl: 0.94 },
        2: { v: [19.2, 27.1], s: [114, 120, 130], pm: 0, pl: 0.06 },
      },
      hung: 0.81,
    },
    regions: {
      17: { parties: { 1: { v: [30, 40], s: [40, 48, 55] }, 2: { v: [10, 20.5], s: [2, 5, 9] } }, hung: null },
    },
  };
}

/** Uniform-swing totals: Labour 300, Reform 109, Green 3 (Green has no stored simulations). */
const summary = {
  totalVotes: 1000,
  totalSeats: 650,
  parties: [
    { party: "labour", seats: 300, votes: 600 },
    { party: "reform", seats: 109, votes: 380 },
    { party: "green", seats: 3, votes: 20 },
  ],
};

/** The same seats at the 2024 election. */
const comparison = {
  totalVotes: 1000,
  parties: [
    { party: "labour", seats: 411, votes: 500 },
    { party: "reform", seats: 5, votes: 200 },
    { party: "green", seats: 4, votes: 100 },
  ],
};

/** The plain table body, as rendered before simulated medians existed. */
const PLAIN_BODY = [
  ["labour", "Labour", "#e4003b", "300", "maps-delta-positive", "+300", "600", "60.00", "maps-delta-positive", "+60.00"],
  ["reform", "Reform UK", "#12b6cf", "109", "maps-delta-positive", "+109", "380", "38.00", "maps-delta-positive", "+38.00"],
  ["green", "Green", "#02a95b", "3", "maps-delta-positive", "+3", "20", "2.00", "maps-delta-positive", "+2.00"],
].map(([, name, colour, seats, seatClass, seatDelta, votes, pct, pctClass, pctDelta]) => `<tr>
      <td><span class="maps-party-cell"><span class="maps-party-swatch" style="background:${colour}"></span>${name}</span></td>
      <td>${seats}</td>
      <td class="comparison-col ${seatClass}">${seatDelta}</td>
      <td class="vote-total-col">${votes}</td>
      <td class="vote-pct-col">${pct}</td>
      <td class="comparison-col vote-pct-comparison-col ${pctClass}">${pctDelta}</td>
    </tr>`).join("");

describe.skipIf(!JSDOM)("actual vote totals table", () => {
  let dom;
  let module;
  let state;
  let renderVoteTotals;
  beforeEach(async () => {
    vi.resetModules();
    dom = new JSDOM(shell, { url: "http://localhost/electionmaps/" });
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("navigator", dom.window.navigator);
    // dom.js observes panel sizes at module load; jsdom has no layout to observe.
    const Observer = class { observe() {} unobserve() {} disconnect() {} };
    vi.stubGlobal("ResizeObserver", Observer);
    vi.stubGlobal("IntersectionObserver", Observer);
    module = await import("../state.js");
    const { decodeUncertainty } = await import("../uncertainty.js");
    state = module.state;
    module.manifest.init({ parties: [
      { id: 1, key: "labour", name: "Labour", colour: "#e4003b" },
      { id: 2, key: "reform", name: "Reform UK", colour: "#12b6cf" },
      { id: 6, key: "green", name: "Green", colour: "#02a95b" },
    ], mapModes: { 2: { regions: [{ id: 17, name: "London" }] } }, files: {} });
    state.view = "election";
    state.currentParliament = "westminster";
    state.currentElection = { model: true, mapId: 2 };
    state.electionData = new module.ElectionData({ seats: [], uncertaintyId: "saved-run" });
    state.electionData.uncertainty = decodeUncertainty(
      payload(), "saved-run", (party) => module.manifest.resolvePartyRef(party), module.manifest.regionsById,
    );
    ({ renderVoteTotals } = await import("../dom.js"));
  });
  afterEach(() => {
    dom?.window.close();
    vi.unstubAllGlobals();
  });

  const header = () => [...document.querySelectorAll("#mapsVoteTotalsTable thead th")].map((th) => th.textContent);
  const table = () => [...document.querySelectorAll("#mapsVoteTotalsBody tr")]
    .map((tr) => [...tr.cells].map((td) => td.textContent));
  const note = () => document.getElementById("mapsSeatMedianNote");
  const toggle = () => document.getElementById("mapsUncertaintyToggle");
  const describedBy = () => document.getElementById("mapsVoteTotalsTable").getAttribute("aria-describedby");
  const PLAIN_HEADER = ["Party", "Seats", "±", "Votes", "Vote %", "±"];

  it("shows each simulated median under Seats, with ± from the median and a dash without one", () => {
    renderVoteTotals(summary, comparison);
    expect(header()).toEqual(PLAIN_HEADER);
    expect(table()).toEqual([
      ["Labour", "282", "-129", "600", "60.00", "+10.00"],
      ["Reform UK", "120", "+115", "380", "38.00", "+18.00"],
      ["Green", "—", "", "20", "2.00", "-8.00"],
    ]);
    // No uniform-swing count anywhere: not in a cell and not in a hover title.
    const html = document.getElementById("mapsVoteTotalsTable").outerHTML;
    expect(html).not.toMatch(/>300<|>109<|title="[^"]*(300|109|[Uu]niform)/);
    expect(toggle().hidden).toBe(false);
    expect(toggle().textContent).toBe("Show uncertainty");
  });

  it("adds only the range text when uncertainty is shown", () => {
    renderVoteTotals(summary, comparison);
    const hidden = table();
    state.voteTotals.showUncertainty = true;
    renderVoteTotals(summary, comparison);
    expect(header()).toEqual(PLAIN_HEADER);
    expect(table()).toEqual([
      ["Labour", "282 (199–359)", "-129", "600", "60.00 (21.7–29.3%)", "+10.00"],
      ["Reform UK", "120 (114–130)", "+115", "380", "38.00 (19.2–27.1%)", "+18.00"],
      ["Green", "—", "", "20", "2.00", "-8.00"],
    ]);
    // Same rows, same columns; only the Seats and Vote % texts gain their ranges.
    table().forEach((row, r) => row.forEach((text, c) => {
      expect(text.startsWith(hidden[r][c])).toBe(true);
      if (c !== 1 && c !== 4) expect(text).toBe(hidden[r][c]);
    }));
    expect(toggle().textContent).toBe("Hide uncertainty");
  });

  it("explains the medians beside the table and names the note to assistive technology", () => {
    renderVoteTotals(summary, comparison);
    expect(note().hidden).toBe(false);
    expect(note().textContent).toBe(
      "Seats are simulated medians, so they needn't add up to 650. Map colours show each seat's uniform-swing winner.",
    );
    expect(describedBy().split(" ")).toContain("mapsSeatMedianNote");
  });

  it("hides the median note with the table when the card collapses", () => {
    const css = readFileSync(new URL("../maps.css", import.meta.url), "utf8");
    const collapsedRule = css.match(/#mapsVoteTotalsCard\.maps-vote-totals--collapsed[^{}]+\{[^}]+\}/)?.[0];
    expect(note().classList.contains("maps-seat-median-note")).toBe(true);
    expect(collapsedRule).toContain(".maps-seat-median-note");
    expect(collapsedRule).toContain("display: none;");
  });

  it("sorts Seats by the median, breaking ties by name", () => {
    state.voteTotals.sort = { key: "seats", direction: "desc" };
    // Reform's uniform-swing count leads, but Labour's median does.
    const swapped = { ...summary, parties: [
      { party: "labour", seats: 100, votes: 300 }, { party: "reform", seats: 400, votes: 700 },
    ] };
    renderVoteTotals(swapped, null);
    expect(table().map((row) => row[0])).toEqual(["Labour", "Reform UK"]);
    // Equal medians: Reform's larger vote share does not put it first.
    state.electionData.uncertainty.national.parties.reform.s = [100, 282, 400];
    renderVoteTotals(swapped, null);
    expect(table().map((row) => row[0])).toEqual(["Labour", "Reform UK"]);
    state.voteTotals.sort = { key: "seats", direction: "asc" };
    renderVoteTotals(swapped, null);
    expect(table().map((row) => row[0])).toEqual(["Labour", "Reform UK"]);
  });

  it("uses one region's medians, with ± against that region's 2024 seats", () => {
    state.mapFilters.region = "london";
    state.voteTotals.showUncertainty = true;
    const london = { totalVotes: 100, totalSeats: 75, parties: [
      { party: "labour", seats: 60, votes: 70 }, { party: "reform", seats: 1, votes: 30 },
    ] };
    const london2024 = { totalVotes: 100, parties: [
      { party: "labour", seats: 59, votes: 60 }, { party: "reform", seats: 0, votes: 10 },
    ] };
    renderVoteTotals(london, london2024);
    expect(table()).toEqual([
      ["Labour", "48 (40–55)", "-11", "70", "70.00 (30–40%)", "+10.00"],
      ["Reform UK", "5 (2–9)", "+5", "30", "30.00 (10–20.5%)", "+20.00"],
    ]);
    expect(note().textContent).toContain("needn't add up to 75.");
  });

  it.each([
    ["party", "labour"], ["majorityMin", 1], ["gainsOnly", true], ["upcoming", "2026"],
  ])("keeps the uniform-swing count under plain Seats in the %s-filtered view", (filter, value) => {
    state.mapFilters[filter] = value;
    state.voteTotals.showUncertainty = true;
    renderVoteTotals(summary, comparison);
    expect(header()).toEqual(PLAIN_HEADER);
    expect(table().map((row) => row.slice(1, 3))).toEqual([["300", "-111"], ["109", "+104"], ["3", "-1"]]);
    expect(table().flat().join(" ")).not.toMatch(/[–—]/);
    expect(toggle().hidden).toBe(true);
    expect(note().hidden).toBe(true);
    expect(note().textContent).toBe("");
    expect(describedBy()).toBe("mapsSenateForecastComparison mapsUncertaintyBasis");
  });

  describe("without stored simulations the table is unchanged", () => {
    function expectPlain() {
      expect(header()).toEqual(PLAIN_HEADER);
      expect(document.getElementById("mapsVoteTotalsBody").innerHTML).toBe(PLAIN_BODY);
      expect(note().hidden).toBe(true);
      expect(toggle().hidden).toBe(true);
      expect(describedBy()).toBe("mapsSenateForecastComparison");
    }
    const empty = { totalVotes: 0, parties: [] };

    it("on a real election", () => {
      state.currentElection.model = false;
      state.voteTotals.showUncertainty = true;
      renderVoteTotals(summary, empty);
      expectPlain();
    });

    it("in predict view", () => {
      state.view = "predict";
      state.voteTotals.showUncertainty = true;
      renderVoteTotals(summary, empty);
      expectPlain();
    });

    it("on a user scenario", () => {
      state.setElectionDataFromSeats([]);
      state.voteTotals.showUncertainty = true;
      renderVoteTotals(summary, empty);
      expectPlain();
    });

    it("on a saved model without uncertainty", () => {
      state.electionData.uncertainty = null;
      renderVoteTotals(summary, empty);
      expectPlain();
    });

    it("on a US electoral-vote map, which keeps its EV header", () => {
      state.electionData.uncertainty = null;
      state.mapConfig = { tally: "electoralVotes" };
      renderVoteTotals(summary, empty);
      expect(header()).toEqual(["Party", "EV", "±", "Votes", "Vote %", "±"]);
      expect(document.getElementById("mapsVoteTotalsBody").innerHTML).toBe(PLAIN_BODY);
      expect(note().hidden).toBe(true);
    });
  });
});

import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { state as defaultState } from "../state.js";
import {
  renderModelledUncertaintyToggle, renderUncertaintyBasis, renderUncertaintyHeader,
  wireModelledUncertaintyToggle,
} from "../uncertainty-view.js";

function element() {
  return {
    hidden: true, checked: false, textContent: "", children: [], attributes: {}, listeners: {},
    appendChild(child) { this.children.push(child); },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
}

const summary = {
  basis: "Nowcast: if an election were held today. 90% ranges and probabilities from 10,000 simulations, conditional on current polls and the model's error assumptions.",
  national: { parties: { labour: { v: [20, 30], s: [200, 300, 400], m: 297, pl: 0.9, pm: 0.3 } }, hung: 0.7 },
  regions: { scotland: { parties: { labour: { v: [10, 20], s: [2, 4, 6], m: 4 } } } },
};

describe("uncertainty display controls", () => {
  let state;
  let control;
  let input;

  beforeEach(() => {
    vi.stubGlobal("document", { createElement: element });
    state = {
      view: "election", currentElection: { model: true },
      electionData: { uncertainty: summary }, showModelledUncertainty: false,
      voteTotals: { mode: "all" },
      mapFilters: { party: "all", secondParty: "all", region: "all", majorityMin: 0, majorityMax: 100, gainsOnly: false, upcoming: "all" },
    };
    control = element();
    input = element();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("offers the checkbox unticked by default and records the reader's choice", () => {
    expect(defaultState.showModelledUncertainty).toBe(false);
    expect(defaultState.voteTotals).not.toHaveProperty("showUncertainty");
    const render = vi.fn(() => renderModelledUncertaintyToggle(control, input, state));
    wireModelledUncertaintyToggle(input, state, render);
    renderModelledUncertaintyToggle(control, input, state);
    expect(control.hidden).toBe(false);
    expect(input.checked).toBe(false);
    input.checked = true;
    input.listeners.change();
    expect(state.showModelledUncertainty).toBe(true);
    expect(render).toHaveBeenCalledTimes(1);
    expect(input.checked).toBe(true);
    input.checked = false;
    input.listeners.change();
    expect(state.showModelledUncertainty).toBe(false);
  });

  it.each([
    ["a historical election", (s) => { s.currentElection.model = false; }],
    ["predict", (s) => { s.view = "predict"; }],
    ["the poll tracker", (s) => { s.view = "polltracker"; }],
    ["a saved model without uncertainty", (s) => { s.electionData.uncertainty = null; }],
  ])("hides the checkbox on %s, keeping the reader's choice", (_label, change) => {
    state.showModelledUncertainty = true;
    change(state);
    renderModelledUncertaintyToggle(control, input, state);
    expect(control.hidden).toBe(true);
    expect(state.showModelledUncertainty).toBe(true);
  });

  it("keeps the checkbox under filters, which only switch the table to the plain count", () => {
    state.mapFilters.party = "labour";
    renderModelledUncertaintyToggle(control, input, state);
    expect(control.hidden).toBe(false);
  });

  it("renders the seats line in the header and the full reading note for the footer", () => {
    const header = element();
    renderUncertaintyHeader(header, summary, () => "Labour");
    expect(header.children.map((child) => child.className)).toEqual(["maps-subtitle-uncertainty"]);
    expect(header.children[0].textContent).toBe("Labour 297 seats (200–400) · majority 30%");
    const footer = element();
    renderUncertaintyBasis(footer, summary);
    // The note carries the nowcast sentence in full; the odds line no longer repeats it.
    expect(footer.children.map((child) => child.className)).toEqual(["maps-map-footer-basis"]);
    expect(footer.children[0].textContent).toBe(summary.basis);
    expect(footer.children[0].id).toBe("mapsUncertaintyBasis");
  });

  it("renders no explanation for elections without uncertainty", () => {
    const header = element();
    renderUncertaintyHeader(header, null, () => "Labour");
    renderUncertaintyBasis(header, null);
    expect(header.children).toEqual([]);
  });

  it("retains the full basis when no outcome odds are available", () => {
    const header = element();
    const noOdds = { basis: summary.basis, national: { parties: {} } };
    renderUncertaintyHeader(header, noOdds, () => "Labour");
    renderUncertaintyBasis(header, noOdds);
    expect(header.children).toHaveLength(1);
    expect(header.children[0].id).toBe("mapsUncertaintyBasis");
    expect(header.children[0].textContent).toBe(summary.basis);
  });

  it("has no Show uncertainty button, a footer below the map and no range columns", () => {
    const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");
    expect(shell).not.toContain("mapsUncertaintyToggle");
    expect(shell).not.toMatch(/Show uncertainty|Hide uncertainty/);
    expect(shell).toMatch(/id="mapsVoteTotalsToggle"[^>]*>Show all<\/button>\s*<button[^>]*id="mapsVoteTotalsCollapse"/);
    // The footer follows the map viewport, inside the map stage.
    expect(shell).toMatch(/<\/svg>\s*<\/div>\s*(<!--[\s\S]*?-->\s*)?<p id="mapsMapFooter" class="maps-map-footer" hidden><\/p>/);
    expect(shell).toMatch(/data-sort-key="seats"[^>]*>Seats<\/th>\s*<th class="comparison-col">±<\/th>\s*<th class="vote-total-col"/);
    expect(shell).not.toMatch(/uncertainty-col|data-uncertainty-label/);
    expect(shell).not.toContain("mapsSeatMedianNote");
    expect(shell).toMatch(/<p id="mapsMapColourNote"[^>]*hidden><\/p>/);
    expect(shell).not.toContain('id="mapsUncertaintyBasis"');
  });

  it("puts the unticked checkbox directly below the Westminster subtitle only", () => {
    const page = (name) => readFileSync(new URL(`../../${name}/index.html`, import.meta.url), "utf8");
    expect(page("electionmaps")).toMatch(/<p id="mapsSubtitle"[^>]*>[^<]*<\/p>\s*(<!--[\s\S]*?-->\s*)?<label id="mapsModelledUncertainty"[^>]*hidden>\s*<input id="mapsModelledUncertaintyInput" type="checkbox" \/>\s*Show with modelled uncertainty\s*<\/label>/);
    expect(page("electionmaps")).not.toMatch(/mapsModelledUncertaintyInput"[^>]*checked/);
    expect(page("uselectionmaps")).not.toContain("mapsModelledUncertainty");
  });
});

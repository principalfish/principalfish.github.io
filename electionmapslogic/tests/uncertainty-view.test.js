import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { state as defaultState } from "../state.js";
import {
  renderUncertaintyHeader, renderUncertaintyToggle, wireUncertaintyToggle,
} from "../uncertainty-view.js";

function element() {
  return {
    hidden: true, textContent: "", children: [], attributes: {}, listeners: {},
    appendChild(child) { this.children.push(child); },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
}

const summary = {
  basis: "Nowcast: if an election were held today. 90% ranges and probabilities from 10,000 simulations, conditional on current polls and the model's error assumptions.",
  national: { parties: { labour: { v: [20, 30], s: [200, 300, 400], pl: 0.9, pm: 0.3 } }, hung: 0.7 },
  regions: { scotland: { parties: { labour: { v: [10, 20], s: [2, 4, 6] } } } },
};

describe("uncertainty display controls", () => {
  let state;
  let button;

  beforeEach(() => {
    vi.stubGlobal("document", { createElement: element });
    state = {
      view: "election", currentElection: { model: true },
      electionData: { uncertainty: summary },
      voteTotals: { mode: "all", showUncertainty: false },
      mapFilters: { party: "all", secondParty: "all", region: "all", majorityMin: 0, majorityMax: 100, gainsOnly: false, upcoming: "all" },
    };
    button = element();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("defaults off and toggles the displayed columns with an accessible pressed state", () => {
    expect(defaultState.voteTotals.showUncertainty).toBe(false);
    const render = vi.fn(() => renderUncertaintyToggle(button, state));
    wireUncertaintyToggle(button, state, render);
    expect(renderUncertaintyToggle(button, state)).toBe(false);
    expect(button.hidden).toBe(false);
    expect(button.textContent).toBe("Show uncertainty");
    expect(button.attributes["aria-pressed"]).toBe("false");
    button.listeners.click();
    expect(render).toHaveLastReturnedWith(true);
    expect(button.textContent).toBe("Hide uncertainty");
    expect(button.attributes["aria-pressed"]).toBe("true");
    button.listeners.click();
    expect(render).toHaveLastReturnedWith(false);
    expect(button.textContent).toBe("Show uncertainty");
  });

  it("hides unsupported map selections without losing the user's toggle preference", () => {
    state.voteTotals.showUncertainty = true;
    state.mapFilters.region = "scotland";
    expect(renderUncertaintyToggle(button, state)).toBe(true);
    state.mapFilters.party = "labour";
    expect(renderUncertaintyToggle(button, state)).toBe(false);
    expect(button.hidden).toBe(true);
    expect(button.attributes["aria-pressed"]).toBe("false");
    state.mapFilters.party = "all";
    expect(renderUncertaintyToggle(button, state)).toBe(true);
    state.currentElection.model = false;
    expect(renderUncertaintyToggle(button, state)).toBe(false);
    expect(button.hidden).toBe(true);
  });

  it("does not change popup data or header odds when hiding the totals columns", () => {
    const original = structuredClone(summary);
    wireUncertaintyToggle(button, state, () => {});
    button.listeners.click();
    button.listeners.click();
    expect(state.electionData.uncertainty).toEqual(original);
    const header = element();
    renderUncertaintyHeader(header, summary, () => "Labour");
    expect(header.children.map((child) => child.className))
      .toEqual(["maps-subtitle-uncertainty", "maps-subtitle-basis"]);
    const text = header.children.map((child) => child.textContent).join(" ");
    expect(text.match(/if an election were held today/g)).toHaveLength(1);
    expect(text).toContain("90% ranges and probabilities from 10,000 simulations");
    expect(header.children[1].id).toBe("mapsUncertaintyBasis");
  });

  it("renders no explanation for elections without uncertainty", () => {
    const header = element();
    renderUncertaintyHeader(header, null, () => "Labour");
    expect(header.children).toEqual([]);
  });

  it("places the toggle beside Show all and the seat range after the seat change", () => {
    const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");
    expect(shell).toMatch(/id="mapsVoteTotalsToggle"[^>]*>Show all<\/button>\s*<button[^>]*id="mapsUncertaintyToggle"[^>]*aria-pressed="false"/);
    expect(shell).toMatch(/data-sort-key="seats"[^>]*>Seats<\/th>\s*<th class="comparison-col">±<\/th>\s*<th[^>]*data-uncertainty-label="seats"/);
    expect(shell).not.toContain('id="mapsUncertaintyBasis"');
  });
});

import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  renderSenateForecastComparison,
  renderSenateForecastTabs,
} from "../features/senate-forecast-view.js";

function createElement() {
  return {
    hidden: true,
    textContent: "",
    children: [],
    attributes: {},
    listeners: {},
    replaceChildren() { this.children = []; },
    appendChild(child) { this.children.push(child); },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
}

describe("saved Senate forecast view", () => {
  let elements;

  beforeEach(() => {
    elements = {
      mapsSenateForecastTabNav: createElement(),
      mapsSenateForecastComparison: createElement(),
    };
    vi.stubGlobal("document", {
      getElementById: (id) => elements[id] || null,
      createElement,
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it.each([
    [{ activeTab: "seatsup", comparisonAvailable: true }, "Changes vs previous election for each seat"],
    [{ activeTab: "chamber", comparisonAvailable: true }, "Changes vs Current Senate"],
    [{ activeTab: "seatsup", comparisonAvailable: false }, "Comparison unavailable"],
    [{ activeTab: "seatsup", comparisonAvailable: false, loading: true }, "Loading comparison…"],
  ])("renders the comparison context %j independently of tabs", (options, text) => {
    renderSenateForecastComparison(options);
    expect(elements.mapsSenateForecastComparison.hidden).toBe(false);
    expect(elements.mapsSenateForecastComparison.textContent).toBe(text);
    expect(elements.mapsSenateForecastTabNav.hidden).toBe(true);
    expect(elements.mapsSenateForecastComparison.innerHTML).toBeUndefined();
  });

  it("clears the note and tab buttons when leaving the saved forecast", () => {
    renderSenateForecastComparison({ activeTab: "chamber", comparisonAvailable: true });
    renderSenateForecastTabs({
      tabs: [{ key: "chamber", label: "Full Senate" }],
      activeTab: "chamber",
      onTabChange: vi.fn(),
    });
    renderSenateForecastComparison();
    renderSenateForecastTabs();
    expect(elements.mapsSenateForecastComparison.hidden).toBe(true);
    expect(elements.mapsSenateForecastComparison.textContent).toBe("");
    expect(elements.mapsSenateForecastTabNav.hidden).toBe(true);
    expect(elements.mapsSenateForecastTabNav.children).toEqual([]);
  });

  it("renders labelled tab buttons with selection and callbacks, replacing stale buttons", () => {
    const onTabChange = vi.fn();
    const tabs = [{ key: "seatsup", label: "Seats up (2026)" }, { key: "chamber", label: "Full Senate" }];
    renderSenateForecastTabs({ tabs, activeTab: "seatsup", onTabChange });
    const nav = elements.mapsSenateForecastTabNav;
    expect(nav.hidden).toBe(false);
    expect(nav.children.map((button) => button.textContent)).toEqual(["Seats up (2026)", "Full Senate"]);
    expect(nav.children.map((button) => button.type)).toEqual(["button", "button"]);
    expect(nav.children.map((button) => button.attributes["aria-pressed"])).toEqual(["true", "false"]);
    expect(nav.children[0].className).toBe("maps-predict-tab-btn active");
    nav.children[1].listeners.click();
    expect(onTabChange).toHaveBeenCalledExactlyOnceWith("chamber");
    renderSenateForecastTabs({ tabs, activeTab: "chamber", onTabChange });
    expect(nav.children).toHaveLength(2);
    expect(nav.children.map((button) => button.attributes["aria-pressed"])).toEqual(["false", "true"]);
  });

  it("tolerates a shell without the forecast elements", () => {
    elements = {};
    expect(() => renderSenateForecastComparison({ activeTab: "seatsup", comparisonAvailable: true })).not.toThrow();
    expect(() => renderSenateForecastTabs()).not.toThrow();
  });

  it("starts hidden in the shared shell, describes the table and collapses with the vote card", () => {
    const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");
    const css = readFileSync(new URL("../maps.css", import.meta.url), "utf8");
    expect(shell).toMatch(/<p\b[^>]*id="mapsSenateForecastComparison"[^>]*\bhidden[^>]*><\/p>/);
    expect(shell).toMatch(/<nav\b[^>]*id="mapsSenateForecastTabNav"[^>]*\bhidden/);
    expect(shell).toMatch(/<table\b[^>]*id="mapsVoteTotalsTable"[^>]*aria-describedby="mapsSenateForecastComparison"/);
    const collapsedRule = css.match(/#mapsVoteTotalsCard\.maps-vote-totals--collapsed[^{}]+\{[^}]+\}/)?.[0];
    expect(collapsedRule).toContain(".maps-senate-forecast-comparison");
    expect(collapsedRule).toContain(".maps-predict-tabs");
    expect(collapsedRule).toContain("display: none;");
  });
});

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../files.js", () => ({ fetchJson: vi.fn() }));
// The table never draws the map, and the vendored topojson build points at a missing source map.
vi.mock("../../site/vendor/topojson-client.v3.esm.js", () => ({ feature: vi.fn(), mesh: vi.fn(), merge: vi.fn() }));

// The browser harness is optional locally; CI can supply a jsdom 24 installation
// without making it a production dependency of the static website.
let JSDOM;
try {
  ({ JSDOM } = createRequire(import.meta.url)(process.env.PF_TEST_JSDOM || "jsdom"));
} catch { /* Actual-module browser checks run when the optional harness is supplied. */ }

const shell = readFileSync(new URL("../shell.html", import.meta.url), "utf8");

/** Labour's uniform-swing 300 sits inside 199–359; Reform's 109 falls below 114–130. */
function payload() {
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

const summary = {
  totalVotes: 1000,
  parties: [{ party: "labour", seats: 300, votes: 600 }, { party: "reform", seats: 109, votes: 400 }],
};

describe.skipIf(!JSDOM)("actual vote totals table", () => {
  let dom;
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
    const module = await import("../state.js");
    const { decodeUncertainty } = await import("../uncertainty.js");
    state = module.state;
    module.manifest.init({ parties: [
      { id: 1, key: "labour", name: "Labour", colour: "#e4003b" },
      { id: 2, key: "reform", name: "Reform UK", colour: "#12b6cf" },
    ], mapModes: { 2: {} }, files: {} });
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
  const rows = () => [...document.querySelectorAll("#mapsVoteTotalsBody tr")];

  it("labels the uniform-swing count, shows the simulated median and marks only the outside count", () => {
    state.voteTotals.showUncertainty = true;
    renderVoteTotals(summary, null);
    expect(header()).toContain("Uniform swing");
    expect(header()).toContain("Simulated (90%)");
    expect(header()).toContain("90% vote range");
    const [labour, reform] = rows();
    expect(labour.cells[1].textContent).toBe("300");
    expect(labour.cells[3].textContent).toBe("282 (199–359)");
    expect(reform.cells[1].querySelector(".maps-outside-range").textContent).toBe("†");
    expect(reform.cells[1].textContent).toBe("109† (outside its 90% simulated range)");
    expect(reform.cells[3].textContent).toBe("120 (114–130)");
    expect(document.querySelectorAll("#mapsVoteTotalsBody .maps-outside-range")).toHaveLength(1);
  });

  it("drops the dagger and the range labels when uncertainty is hidden", () => {
    state.voteTotals.showUncertainty = false;
    renderVoteTotals(summary, null);
    expect(header()).toContain("Uniform swing");
    expect([...document.querySelectorAll("[data-uncertainty-label]")].map((th) => th.textContent)).toEqual(["", ""]);
    expect(document.querySelector("#mapsVoteTotalsBody .maps-outside-range")).toBeNull();
    expect(rows()[1].cells[1].textContent).toBe("109");
  });

  it("keeps the plain Seats label without uncertainty", () => {
    state.electionData.uncertainty = null;
    renderVoteTotals(summary, null);
    expect(header()).toContain("Seats");
    expect(header()).not.toContain("Uniform swing");
  });
});

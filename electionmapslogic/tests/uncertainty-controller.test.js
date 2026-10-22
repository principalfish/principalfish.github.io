import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../dom.js", () => ({
  renderHeader: vi.fn(), renderVoteTotals: vi.fn(), refreshOpenSeatPopup: vi.fn(),
}));
vi.mock("../files.js", () => ({ fetchJson: vi.fn() }));

import { fetchJson } from "../files.js";
import { renderHeader, renderVoteTotals, refreshOpenSeatPopup } from "../dom.js";
import { ElectionData, manifest, page, state } from "../state.js";
import { activateUncertaintyView } from "../uncertainty-controller.js";

function payload() {
  return {
    schema: "pf-uncertainty-v1", uncertaintyId: "saved-run", interval: 0.9,
    simulations: 10000, national: { parties: { 1: { v: [20, 30], s: [200, 300, 400], pm: 0.3, pl: 0.8 } }, hung: 0.7 },
    regions: {},
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchJson.mockReset();
  manifest.init({
    parties: [{ id: 1, key: "labour", name: "Labour" }],
    parliamentFeatures: { westminster: { uncertaintyDataPath: "results/uncertainty.json" } },
    mapModes: { 1: {} },
  });
  page.dataBase = "scratch/data";
  state.currentParliament = "westminster";
  state.currentElection = { model: true, id: "prediction", mapId: 1 };
  state.view = "election";
  state.electionData = new ElectionData({ seats: [], uncertaintyId: "saved-run" });
  fetchJson.mockResolvedValue(payload());
});

describe("optional uncertainty loading", () => {
  it("loads a matching summary and refreshes the visible central result", async () => {
    await activateUncertaintyView();
    expect(fetchJson).toHaveBeenCalledWith("scratch/data/results/uncertainty.json");
    expect(state.electionData.uncertainty.national.parties.labour.pm).toBe(0.3);
    expect(renderHeader).toHaveBeenCalledOnce();
    expect(renderVoteTotals).toHaveBeenCalledOnce();
    expect(refreshOpenSeatPopup).toHaveBeenCalledOnce();
  });

  it.each(["view", "election", "parliament", "results"])("ignores a delayed summary after %s changes", async (change) => {
    let resolve;
    fetchJson.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const loading = activateUncertaintyView();
    const original = state.electionData;
    if (change === "view") state.view = "predict";
    if (change === "election") state.currentElection = { ...state.currentElection };
    if (change === "parliament") state.currentParliament = "us_house";
    if (change === "results") state.electionData = new ElectionData({ seats: [] });
    resolve(payload());
    await loading;
    expect(original.uncertainty).toBeNull();
    expect(state.electionData.uncertainty).toBeNull();
    expect(renderVoteTotals).not.toHaveBeenCalled();
  });

  it.each([null, { ...payload(), uncertaintyId: "different-same-day-run" }, { schema: "unknown" }])(
    "ignores malformed or mismatched data: %j", async (data) => {
      fetchJson.mockResolvedValue(data);
      await activateUncertaintyView();
      expect(state.electionData.uncertainty).toBeNull();
    },
  );

  it("keeps central results usable when the optional file is missing", async () => {
    const original = state.electionData;
    fetchJson.mockRejectedValue(new Error("404"));
    await activateUncertaintyView();
    expect(state.electionData).toBe(original);
    expect(original.uncertainty).toBeNull();
    expect(renderVoteTotals).toHaveBeenCalledOnce();
  });

  it.each(["real", "predict", "us", "legacy"])("does not fetch for %s results", async (kind) => {
    if (kind === "real") state.currentElection.model = false;
    if (kind === "predict") state.view = "predict";
    if (kind === "us") state.currentParliament = "us_house";
    if (kind === "legacy") state.electionData.uncertaintyId = null;
    await activateUncertaintyView();
    expect(fetchJson).not.toHaveBeenCalled();
  });
});

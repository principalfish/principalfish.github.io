import { beforeEach, describe, expect, it } from "vitest";
import { ElectionData, manifest, Seat, state } from "../state.js";
import {
  activeUncertainty, decodeUncertainty, seatUncertaintyText, uncertaintyForTotals,
  uncertaintyHeadline, uncertaintyRange,
} from "../uncertainty.js";

export function publicSummary() {
  return {
    schema: "pf-uncertainty-v1", uncertaintyId: "matching-run", interval: 0.9,
    simulations: 10000, basis: "90% ranges from 10,000 simulations.",
    national: {
      parties: {
        1: { v: [22.8, 30.6], s: [220, 301, 376], pm: 0.306, pl: 0.97 },
        4: { v: [18.1234, 27.5678], s: [80, 120, 150], pm: 0, pl: 0.03 },
      },
      hung: 0.694,
    },
    regions: { 12: { parties: { 1: { v: [30, 40], s: [20, 30, 40] } } } },
  };
}

function decoded(payload = publicSummary()) {
  return decodeUncertainty(payload, "matching-run", (party) => manifest.resolvePartyRef(party), manifest.regionsById);
}

beforeEach(() => {
  manifest.init({
    parties: [{ id: 1, key: "labour", name: "Labour" }, { id: 4, key: "conservative", name: "Conservative" }],
    mapModes: { 1: { regions: [{ id: 12, name: "North East" }] } },
  });
  state.currentElection = { model: true, mapId: 1 };
  state.view = "election";
  state.voteTotals.mode = "all";
  state.mapFilters = {
    party: "all", secondParty: "all", region: "all", gainsOnly: false,
    majorityMin: 0, majorityMax: 100, upcoming: "all",
  };
  state.electionData = new ElectionData({
    uncertaintyId: "matching-run",
    seats: [{ n: "Test", r: 12, w: 1, p: [[1, 60], [4, 40]], u: [[1, 50, 70, 0.87654]] }],
  });
  state.electionData.uncertainty = decoded();
});

describe("seat uncertainty", () => {
  it("keeps party-ID-aligned bands and isolates raw, base and cloned seat arrays", () => {
    const raw = { n: "Test", p: [[4, 40], [1, 60]], u: [[1, 50, 70, 0.9]] };
    const seat = Seat.fromRaw(raw);
    const copy = new Seat(seat);
    raw.u[0][1] = 1;
    copy.u.labour[0] = 2;
    expect(seat.u).toEqual({ labour: [50, 70, 0.9] });
    expect(copy.u.conservative).toBeUndefined();
    expect(state.electionData.baseSeats[0].u).toEqual(state.electionData.currentSeats[0].u);
    expect(state.electionData.baseSeats[0].u.labour).not.toBe(state.electionData.currentSeats[0].u.labour);
  });

  it("rejects malformed ranges and odds and leaves ordinary seats unchanged", () => {
    expect(Seat.fromRaw({ u: [[1, 70, 50, 0.2], [4, 10, 30, 12], null] }).u).toEqual({});
    expect(new Seat({}).u).toEqual({});
  });

  it("formats ranges and converts 0–1 odds to percentages with at most two decimals", () => {
    expect(seatUncertaintyText([23.123456, 38.987654, 0.87654])).toBe("(23.12–38.99%) · win 87.65%");
    expect(seatUncertaintyText([0, 100, 0])).toBe("(0–100%) · win 0%");
    expect(seatUncertaintyText(undefined)).toBe("");
    expect(uncertaintyRange([220, 301, 376])).toBe("220–376");
    expect(uncertaintyRange(undefined)).toBe("—");
  });
});

describe("uncertainty totals and headline", () => {
  it("maps identifiers to canonical keys and uses national or one-region ranges", () => {
    expect(uncertaintyForTotals(state).parties.labour.s).toEqual([220, 301, 376]);
    state.mapFilters.region = "northeast";
    expect(uncertaintyForTotals(state).parties.labour.s).toEqual([20, 30, 40]);
    expect(uncertaintyForTotals(state).parties.conservative).toBeUndefined();
  });

  it.each([
    ["party", "labour"], ["secondParty", "conservative"], ["gainsOnly", true],
    ["majorityMin", 1], ["majorityMax", 99], ["upcoming", "2026"],
    ["region", ["northeast", "london"]], ["region", "northeast,london"],
  ])("hides ranges for the unsupported %s filter", (filter, value) => {
    state.mapFilters[filter] = value;
    expect(uncertaintyForTotals(state)).toBeNull();
  });

  it("keeps national odds under geographic filters and chooses the most likely largest party", () => {
    state.mapFilters.region = "northeast";
    expect(uncertaintyHeadline(activeUncertainty(state), (party) => manifest.labelParty(party)))
      .toBe("Labour largest party 97% · Labour majority 30.6% · Hung 69.4% (if an election were held today)");
  });

  it("suppresses stored uncertainty for real elections and user predictions", () => {
    state.currentElection.model = false;
    expect(activeUncertainty(state)).toBeNull();
    state.currentElection.model = true;
    state.view = "predict";
    expect(uncertaintyForTotals(state)).toBeNull();
    state.view = "election";
    state.setElectionDataFromSeats(state.electionData.currentSeats);
    expect(activeUncertainty(state)).toBeNull();
  });

  it("rejects a mismatched artifact and does not fabricate invalid or missing party ranges", () => {
    const payload = publicSummary();
    payload.uncertaintyId = "another-run";
    expect(decoded(payload)).toBeNull();
    payload.uncertaintyId = "matching-run";
    payload.national.parties[4].v = [27, 18];
    expect(decoded(payload).national.parties.conservative).toBeUndefined();
    expect(decoded(null)).toBeNull();
  });
});

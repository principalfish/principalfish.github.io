import { beforeEach, describe, expect, it } from "vitest";
import { ElectionData, manifest, Seat, state } from "../state.js";
import {
  activeUncertainty, decodeUncertainty, formatProbability, outsideSeatRange, seatMedianRange,
  seatUncertaintyText, uncertaintyForTotals, uncertaintyHeadline, uncertaintyLabel, uncertaintyRange,
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

  it("formats share ranges at two decimals and the win chance as a whole percentage", () => {
    expect(seatUncertaintyText([23.123456, 38.987654, 0.87654])).toBe("(23.12–38.99%) · win 88%");
    expect(seatUncertaintyText([0, 100, 0])).toBe("(0–100%) · win <1%");
    expect(seatUncertaintyText([40, 60, 1])).toBe("(40–60%) · win >99%");
    expect(seatUncertaintyText(undefined)).toBe("");
    expect(uncertaintyRange([220, 301, 376])).toBe("220–376");
    expect(uncertaintyRange(undefined)).toBe("—");
  });
});

describe("published probabilities", () => {
  it.each([
    [0, "<1%"], [0.004, "<1%"], [0.005, "1%"], [0.5, "50%"],
    [0.994, "99%"], [0.995, ">99%"], [1, ">99%"],
    // Exact half-percents that value * 100 puts just below the half.
    [0.145, "15%"], [0.285, "29%"], [0.565, "57%"],
    // Four-place inputs (older published files) round once, not via tenths.
    [0.0049, "<1%"], [0.9949, "99%"], [0.1449, "14%"],
  ])("shows %s as %s", (value, text) => {
    expect(formatProbability(value)).toBe(text);
  });
});

describe("seat ranges", () => {
  it("shows the simulated median before its range", () => {
    expect(seatMedianRange([199, 282, 359])).toBe("282 (199–359)");
    expect(seatMedianRange([0, 0, 2])).toBe("0 (0–2)");
    expect(seatMedianRange(undefined)).toBe("—");
    expect(seatMedianRange([20, 30])).toBe("—");
  });

  it.each([
    [282, false], [199, false], [359, false], [198, true], [360, true], [300, false],
  ])("flags a uniform-swing count of %s outside 199–359: %s", (seats, outside) => {
    expect(outsideSeatRange(seats, [199, 282, 359])).toBe(outside);
  });

  it("never flags a count without a band", () => {
    expect(outsideSeatRange(300, undefined)).toBe(false);
    expect(outsideSeatRange(300, null)).toBe(false);
  });

  it("fills the interval into a column label", () => {
    expect(uncertaintyLabel("Simulated ({interval})", 0.9)).toBe("Simulated (90%)");
    expect(uncertaintyLabel("{interval} vote range", 0.8)).toBe("80% vote range");
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
      .toBe("Labour 301 seats (220–376) · largest party 97% · majority 31% · hung 69% (if an election were held today)");
  });

  it("bounds the headline odds at <1% and >99%", () => {
    const summary = activeUncertainty(state);
    summary.national.parties.labour = { ...summary.national.parties.labour, s: [199, 282, 359], pl: 1, pm: 0.19 };
    summary.national.parties.conservative = { ...summary.national.parties.conservative, pl: 0 };
    summary.national.hung = 0.81;
    expect(uncertaintyHeadline(summary, (party) => manifest.labelParty(party))).toBe(
      "Labour 282 seats (199–359) · largest party >99% · majority 19% · hung 81% (if an election were held today)",
    );
    summary.national.parties.labour.pm = 0.004;
    expect(uncertaintyHeadline(summary, (party) => manifest.labelParty(party))).toContain("majority <1% ·");
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

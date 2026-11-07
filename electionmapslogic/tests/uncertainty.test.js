import { beforeEach, describe, expect, it } from "vitest";
import { ElectionData, manifest, Seat, state } from "../state.js";
import {
  activeUncertainty, decodeUncertainty, formatProbability, outsideSeatRange, probabilityPercent,
  seatMedianRange, seatUncertaintyText, simulatedSubtitle, uncertaintyForTotals, uncertaintyHeadline,
  uncertaintyLabel, uncertaintyOutcomeLine, uncertaintyRange,
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
      .toBe("Labour 301 seats (220–376) · majority 31%");
    expect(uncertaintyOutcomeLine(activeUncertainty(state), (party) => manifest.labelParty(party)))
      .toBe("Hung parliament likely (69%) · Labour largest party (97%)");
  });

  it("bounds the headline odds at <1% and >99%", () => {
    const summary = activeUncertainty(state);
    summary.national.parties.labour = { ...summary.national.parties.labour, s: [199, 282, 359], pl: 1, pm: 0.19 };
    summary.national.parties.conservative = { ...summary.national.parties.conservative, pl: 0 };
    summary.national.hung = 0.81;
    const label = (party) => manifest.labelParty(party);
    expect(uncertaintyOutcomeLine(summary, label)).toBe("Hung parliament likely (81%) · Labour largest party (>99%)");
    expect(uncertaintyHeadline(summary, label)).toBe(
      "Labour 282 seats (199–359) · majority 19%",
    );
    summary.national.parties.labour.pm = 0.004;
    summary.national.hung = 0.996;
    expect(uncertaintyOutcomeLine(summary, label)).toBe("Hung parliament likely (>99%) · Labour largest party (>99%)");
    expect(uncertaintyHeadline(summary, label)).toBe(
      "Labour 282 seats (199–359) · majority <1%",
    );
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

describe("simulated headline lines", () => {
  const NAMES = { labour: "Labour", reform: "Reform UK", conservative: "Conservative" };
  const label = (party) => NAMES[party];

  /** A decoded national summary; each party row is [lo, median, hi, pm, pl]. */
  function national(rows, hung) {
    const parties = {};
    for (const [party, [lo, median, hi, pm, pl]] of Object.entries(rows)) {
      parties[party] = { v: [20, 30], s: [lo, median, hi], pm, pl };
    }
    return { national: { parties, hung } };
  }

  function lines(summary) {
    return [simulatedSubtitle("Current prediction", summary, label), uncertaintyHeadline(summary, label)];
  }

  it("leads with a likely hung parliament and drops hung from the second line", () => {
    const summary = national({ labour: [238, 319, 389, 0.44, 0.99], reform: [100, 150, 200, 0, 0.01] }, 0.56);
    expect(lines(summary)).toEqual([
      "Current prediction · Hung parliament likely (56%) · Labour largest party (99%)",
      `Labour 319 seats (238–389) · majority 44%`,
    ]);
  });

  it("calls an outcome below a displayed 50% only most likely", () => {
    const summary = national({
      labour: [250, 300, 340, 0.3, 0.7], reform: [200, 250, 300, 0.25, 0.3],
    }, 0.45);
    expect(lines(summary)).toEqual([
      "Current prediction · Hung parliament most likely (45%) · Labour largest party (70%)",
      `Labour 300 seats (250–340) · majority 30%`,
    ]);
  });

  it("names a likely majority and adds the hung odds to the second line", () => {
    const summary = national({ labour: [300, 340, 370, 0.72, 0.99], reform: [80, 120, 160, 0, 0.01] }, 0.28);
    expect(lines(summary)).toEqual([
      "Current prediction · Labour majority likely (72%) · Labour largest party (99%)",
      `Labour 340 seats (300–370) · majority 72% · hung 28%`,
    ]);
  });

  it("names every outcome tied on the displayed percentage, hung first then by name", () => {
    const summary = national({ labour: [280, 326, 360, 0.502, 0.9], reform: [100, 150, 200, 0, 0.1] }, 0.498);
    expect(lines(summary)).toEqual([
      "Current prediction · Hung parliament or Labour majority equally likely (50%) · Labour largest party (90%)",
      `Labour 326 seats (280–360) · majority 50%`,
    ]);
    const three = national({
      reform: [250, 300, 340, 0.333, 0.5], labour: [250, 300, 340, 0.334, 0.5],
    }, 0.333);
    expect(uncertaintyOutcomeLine(three, label)).toBe(
      "Hung parliament, Labour majority or Reform UK majority equally likely (33%) · "
      + "Labour and Reform UK level as largest party (50%)",
    );
  });

  it("names a majority tie without hung and keeps hung on the second line", () => {
    const summary = national({
      reform: [250, 300, 340, 0.4, 0.5], labour: [250, 300, 340, 0.404, 0.5],
    }, 0.196);
    expect(lines(summary)[0]).toBe(
      "Current prediction · Labour majority or Reform UK majority equally likely (40%) · "
      + "Labour and Reform UK level as largest party (50%)",
    );
    expect(lines(summary)[1]).toMatch(/· hung 20%$/);
  });

  it("shows both medians when the largest party is tied on the displayed percentage", () => {
    const summary = national({
      reform: [240, 288, 330, 0.18, 0.498], labour: [250, 290, 330, 0.2, 0.502],
    }, 0.62);
    expect(lines(summary)).toEqual([
      "Current prediction · Hung parliament likely (62%) · Labour and Reform UK level as largest party (50%)",
      `Labour 290 seats (250–330) · majority 20% · Reform UK 288 seats (240–330) · majority 18%`,
    ]);
    // The alphabetically later party ahead on the hidden decimal still reads second.
    summary.national.parties.reform.pl = 0.502;
    summary.national.parties.labour.pl = 0.498;
    expect(lines(summary)).toEqual([
      "Current prediction · Hung parliament likely (62%) · Labour and Reform UK level as largest party (50%)",
      `Labour 290 seats (250–330) · majority 20% · Reform UK 288 seats (240–330) · majority 18%`,
    ]);
    summary.national.hung = 0.38;
    summary.national.parties.labour.pm = 0.42;
    expect(lines(summary)[1]).toBe(
      `Labour 290 seats (250–330) · majority 42% · Reform UK 288 seats (240–330) · majority 18% · hung 38%`,
    );
  });

  it("judges likely on the displayed percentage, so 49.6% reads as likely (50%)", () => {
    expect(probabilityPercent(0.496)).toBe(50);
    expect(probabilityPercent(0.494)).toBe(49);
    const summary = national({ labour: [200, 300, 340, 0.3, 0.8], reform: [100, 200, 300, 0.204, 0.2] }, 0.496);
    expect(uncertaintyOutcomeLine(summary, label)).toBe("Hung parliament likely (50%) · Labour largest party (80%)");
    summary.national.hung = 0.494;
    summary.national.parties.reform.pm = 0.206;
    expect(uncertaintyOutcomeLine(summary, label)).toBe("Hung parliament most likely (49%) · Labour largest party (80%)");
  });

  it("bounds both lines at <1% and >99%", () => {
    const summary = national({ labour: [330, 380, 420, 0.997, 0.999], reform: [50, 80, 110, 0, 0.001] }, 0.003);
    expect(lines(summary)).toEqual([
      "Current prediction · Labour majority likely (>99%) · Labour largest party (>99%)",
      `Labour 380 seats (330–420) · majority >99% · hung <1%`,
    ]);
  });

  it("gives no line without national odds, so the uniform-swing line stays", () => {
    expect(simulatedSubtitle("Current prediction", null, label)).toBe("");
    expect(simulatedSubtitle("Current prediction", national({ labour: [1, 2, 3, 0.1, 0.9] }, null), label)).toBe("");
    expect(simulatedSubtitle("Current prediction", national({ labour: [1, 2, 3, null, 0.9] }, 0.5), label)).toBe("");
    expect(uncertaintyHeadline(national({}, 0.5), label)).toBe("");
  });
});

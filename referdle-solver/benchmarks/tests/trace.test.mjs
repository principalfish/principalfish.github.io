import { describe, expect, it } from "vitest";
import { gzipSync, gunzipSync } from "node:zlib";
import { compareTraces, decodeTrace, encodeTrace, normalizeTrace } from "../trace.mjs";

describe("lossless semantic trace protocol", () => {
  it("round trips compression with special numbers, undefined, holes and precision", () => {
    const value = { undefined, null: null, numbers: [-0, NaN, Infinity, -Infinity, 1.0000000000000002], holes: [, undefined], text: "AAAAA" };
    const decoded = decodeTrace(gunzipSync(gzipSync(encodeTrace(value))).toString());
    expect(compareTraces(value, decoded)).toEqual({ equal: true });
    expect(Object.hasOwn(decoded, "undefined")).toBe(true);
    expect(Object.hasOwn(decoded.holes, 0)).toBe(false);
    expect(Object.is(decoded.numbers[0], -0)).toBe(true);
  });

  it("ignores object insertion order while producing canonical bytes", () => {
    expect(encodeTrace({ z: 2, a: 1 })).toBe(encodeTrace({ a: 1, z: 2 }));
    expect(compareTraces({ z: 2, a: 1 }, { a: 1, z: 2 })).toEqual({ equal: true });
  });

  const move = { word: "AAAAA", board: 1, colors: "21000", before: { cands: [["AAAAA", "BBBBB"]] }, suggest: { ranked: [{ word: "AAAAA", score: 1.0000000000000002 }, { word: "BBBBB", score: 2 }] } };
  it.each([
    ["word", (m) => { m.word = "BBBBB"; }],
    ["board", (m) => { m.board = 2; }],
    ["colors", (m) => { m.colors = "21001"; }],
    ["candidates", (m) => { m.before.cands[0][0] = "CCCCC"; }],
    ["candidate order", (m) => { m.before.cands[0].reverse(); }],
    ["ranked word", (m) => { m.suggest.ranked[0].word = "CCCCC"; }],
    ["ranking order", (m) => { m.suggest.ranked.reverse(); }],
    ["score precision", (m) => { m.suggest.ranked[0].score = 1; }],
  ])("reports exact differences in %s with case/move context", (_, edit) => {
    const expected = { day: 1000, expanded: false, moves: [move] };
    const actual = structuredClone(expected);
    edit(actual.moves[0]);
    expect(compareTraces(expected, actual)).toMatchObject({ equal: false, day: 1000, expanded: false, moveIndex: 0 });
  });

  it.each([[{}, { x: undefined }], [{ x: undefined }, { x: null }], [{ x: -0 }, { x: 0 }], [["A", "B"], ["B", "A"]], [undefined, null]])("rejects field/numeric/order differences", (a, b) => {
    expect(compareTraces(a, b).equal).toBe(false);
  });

  it("bounds large diagnostics and rejects corrupt or unsupported encodings", () => {
    const result = compareTraces({ x: "A".repeat(1000) }, { x: "B" });
    expect(result.expected.length).toBeLessThanOrEqual(180);
    expect(() => decodeTrace('["number","bad"]')).toThrow("Invalid trace tag");
    expect(() => decodeTrace('["object",[["x",["null"]],["x",["null"]]]]')).toThrow("Invalid trace object field");
    expect(() => encodeTrace(new Map())).toThrow("plain objects");
    const cyclic = {}; cyclic.self = cyclic;
    expect(() => encodeTrace(cyclic)).toThrow("Cyclic");
  });

  it("preserves an inconsistent controller summary as an incomplete terminal", () => {
    const result = normalizeTrace({
      day: 1000, answers: ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"], expanded: false, clueGrid: [],
      publication: { game: { solved: true }, moves: [], steps: [] },
      finalSlots: Array.from({ length: 5 }, () => ({ guesses: [] })), finalResult: { solvable: false, reason: "no candidates" },
    });
    expect(result.final.game.solved).toBe(true);
    expect(result.final.status).toBe("incomplete");
    expect(result.final.result.reason).toBe("no candidates");
  });

  it("fails when a move lacks its before observation", () => {
    expect(() => normalizeTrace({ publication: { moves: [move], steps: [{ suggest: {}, after: {} }] } })).toThrow("Missing before-state");
  });
});

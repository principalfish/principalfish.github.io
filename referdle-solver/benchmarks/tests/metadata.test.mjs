import { describe, expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { COMPONENT_DIR } from "../harness.mjs";
import { assertCompatible, collectProvenance, collectRuntimeFootprint, createContract, sha256, timingNotes } from "../metadata.mjs";
import { caseRow, summarize } from "../reporting.mjs";

function inputs(matrix = new Int16Array([0, 1, 242, 3])) {
  return {
    sample: { schemaVersion: 1, games: [{ day: 1000, answers: ["AAAAA"] }] }, strategy: { probe_cap: 220 },
    state: { N: 2, PM: matrix, POOL: ["AAAAA", "BBBBB"], EXPANDED: ["CCCCC"], ALL_GUESSES: ["AAAAA", "BBBBB", "CCCCC"], PLURALS: new Set(["BBBBB"]) },
  };
}

describe("logical comparison contract", () => {
  it("accepts byte/int16 matrix formats and ignores storage/provenance metadata", () => {
    const original = inputs(); original.state.manifest = { gzip_bytes: 999 };
    const repacked = inputs(new Uint8Array([0, 1, 242, 3])); repacked.state.manifest = { gzip_bytes: 100 };
    expect(createContract(original)).toEqual(createContract(repacked));
    expect(() => assertCompatible(createContract(original), createContract(repacked))).not.toThrow();
  });
  it.each([
    ["word order", (data) => { data.state.POOL.reverse(); }],
    ["matrix pattern", (data) => { data.state.PM[1] = 2; }],
    ["strategy", (data) => { data.strategy.probe_cap = 221; }],
    ["answer", (data) => { data.sample.games[0].answers[0] = "BBBBB"; }],
    ["expanded list", (data) => { data.state.EXPANDED.push("DDDDD"); }],
  ])("rejects changed %s inputs", (_, edit) => {
    const current = inputs(); edit(current);
    expect(() => assertCompatible(createContract(inputs()), createContract(current))).toThrow("Reference contract changed");
  });
  it.each([new Int16Array([0, 1]), [0, 1, 243, 3], [0, 1, -1, 3], [0, 1, 1.5, 3]])("rejects invalid matrix shape/codes", (matrix) => {
    expect(() => createContract(inputs(matrix))).toThrow(/Invalid (logical matrix|matrix pattern)/);
  });
  it("notes environment changes without rejecting source changes", () => {
    const before = { node: "v22", platform: "linux", architecture: "x64", release: "old", cpu: { model: "x" }, sourceHashes: { x: "old" } };
    expect(timingNotes(before, { ...before, sourceHashes: { x: "new" } })).toEqual([]);
    expect(timingNotes(before, { ...before, node: "v24", cpu: { model: "y" } })[0]).toContain("node, cpu");
  });
  it("records the shared feedback implementation as source provenance", async () => {
    const provenance = await collectProvenance();
    expect(provenance.sourceHashes["referdle-solver/js/manual.js"])
      .toBe(sha256(await readFile(path.join(COMPONENT_DIR, "js/manual.js"))));
    expect(Object.keys(provenance.sourceHashes).every((filename) => !path.isAbsolute(filename))).toBe(true);
  });
});

describe("timing reporting", () => {
  it("computes per-case deltas, per-mode totals, medians and slowest cases", () => {
    const rows = [
      caseRow({ day: 1000, expanded: false, computeMs: 20, baselineMs: 40, status: "matched", moves: 5 }),
      caseRow({ day: 1004, expanded: false, computeMs: 10, baselineMs: 20, status: "matched", moves: 6 }),
      caseRow({ day: 1000, expanded: true, computeMs: 50, baselineMs: 100, status: "matched", moves: 5 }),
    ];
    expect(rows[0]).toMatchObject({ deltaMs: -20, deltaPercent: -50 });
    const summary = summarize(rows);
    expect(summary.pool).toMatchObject({ computeMs: 30, baselineComputeMs: 60, medianMs: 15 });
    expect(summary.expanded.computeMs).toBe(50);
    expect(summary.overall).toMatchObject({ computeMs: 80, baselineComputeMs: 160, medianMs: 20, deltaPercent: -50 });
    expect(summary.overall.slowest[0]).toEqual({ day: 1000, mode: "expanded", computeMs: 50 });
  });
  it("keeps absent/zero baselines and untimed failures explicit", () => {
    const untimed = caseRow({ day: 1000, expanded: false, status: "error" });
    expect(untimed.currentComputeMs).toBeNull();
    expect(summarize([untimed]).overall).toMatchObject({ cases: 1, measuredCases: 0, medianMs: null, baselineComputeMs: null });
    expect(caseRow({ day: 1000, expanded: false, computeMs: 1, baselineMs: 0, status: "matched" }).deltaPercent).toBeNull();
  });
});


it("records shipped file bytes separately from decoded PM and excludes references", async () => {
  const footprint = await collectRuntimeFootprint();
  expect(footprint.matrixGzipBytes).toBe(7491562);
  expect(footprint.decodedMatrixBytes).toBe(16378209);
  expect(footprint.totalBytes).toBe(footprint.dataBytes + footprint.javascriptBytes + footprint.htmlCssBytes);
  expect(footprint.totalBytes).toBeLessThan(9000000);
  expect(footprint.scope).toContain("excludes benchmarks/references");
});

it("keeps unavailable roundtrips distinct and computes overhead on paired measurements only", () => {
  const rows = [
    caseRow({ day: 1000, expanded: false, computeMs: 10, roundtripMs: 14, status: "matched" }),
    caseRow({ day: 1004, expanded: false, computeMs: 20, status: "matched" }),
    caseRow({ day: 1000, expanded: true, roundtripMs: 9, status: "error" }),
  ];
  expect(rows[1].roundtripMs).toBeNull();
  expect(rows[1].transportOverheadMs).toBeNull();
  expect(summarize(rows).overall).toMatchObject({ computeMs: 30, roundtripMs: 23, roundtripMeanMs: 11.5, roundtripMedianMs: 11.5,
    roundtripMeasuredCases: 2, overheadMeasuredCases: 1, transportOverheadMs: 4, transportOverheadMeanMs: 4 });
  expect(summarize([rows[1]]).overall).toMatchObject({ roundtripMs: null, roundtripMeanMs: null, transportOverheadMs: null });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { link, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import * as harness from "../harness.mjs";
import { buildReportModel, createDurationBins, histogramCounts, main, renderReportHTML, writeHTMLReport } from "../report-html.mjs";

function row(day = 1000, expanded = false, overrides = {}) {
  return { day, expanded, status: "matched", currentComputeMs: 1000, baselineComputeMs: null, moves: 12, ...overrides };
}
function report(cases = [], overrides = {}) {
  const days = [...new Set(cases.map((row) => row.day))].sort();
  return { schemaVersion: 1, action: "verify", valid: true, cases, notes: [], errors: [],
    selection: days.length ? { days, games: days.length, cases: days.length * 2, fullSample: false } : null,
    timingBoundaries: "Compute timing only", order: "Pool then expanded", setupMs: 100, elapsedMs: 2000, referenceBytes: 1000,
    ...overrides,
  };
}

describe("shared duration histogram bands", () => {
  it("uses readable bands through the actual maximum and includes the final boundary", () => {
    const bins = createDurationBins([0, 2, 20, 34.4]);
    expect(bins).toHaveLength(9);
    expect(bins[0]).toMatchObject({ lower: 0, upper: 4, label: "0–4" });
    expect(bins.at(-1)).toMatchObject({ lower: 32, upper: 36, inclusiveUpper: true });
    expect(histogramCounts([0, 4, 7.99, 8, 34.4, 36], bins)).toEqual([1, 2, 1, 0, 0, 0, 0, 0, 2]);
    const exact = createDurationBins([20]);
    expect(exact).toHaveLength(10);
    expect(histogramCounts([0, 2, 19.99, 20], exact)).toEqual([1, 1, 0, 0, 0, 0, 0, 0, 0, 2]);
  });
  it("handles fractional boundary values, zero and empty timings", () => {
    const bins = createDurationBins([0.3]);
    expect(histogramCounts([0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3], bins)).toEqual([1, 1, 1, 1, 1, 2]);
    expect(histogramCounts([0, 0], createDurationBins([0, 0]))).toEqual([2]);
    expect(histogramCounts([], createDurationBins([]))).toEqual([0]);
    expect(() => histogramCounts([2], createDurationBins([0]))).toThrow("outside histogram");
  });
  it("shares bins and count scales across modes and baseline/current", () => {
    const model = buildReportModel(report([
      row(1000, false, { currentComputeMs: 2000, baselineComputeMs: 34400 }),
      row(1004, false, { currentComputeMs: 2000, baselineComputeMs: 34400 }),
      row(1000, true, { currentComputeMs: 7000, baselineComputeMs: 34400 }),
      row(1004, true, { currentComputeMs: 8000, baselineComputeMs: 30000 }),
    ]));
    expect(model.durationBins).toHaveLength(9);
    expect(model.durationCountMax).toBe(2);
    expect(model.modes.pool.currentCounts[0]).toBe(2);
    expect(model.modes.expanded.currentCounts.slice(1, 3)).toEqual([1, 1]);
    expect(model.modes.pool.baselineCounts.at(-1)).toBe(2);
    for (const mode of Object.values(model.modes)) {
      expect(mode.currentCounts.reduce((sum, count) => sum + count, 0)).toBe(mode.currentCount);
      expect(mode.baselineCounts.reduce((sum, count) => sum + count, 0)).toBe(mode.baselineCount);
    }
  });
});

describe("means, cohorts and scope", () => {
  it("compares only the same paired finite cohort without treating missing values as zero", () => {
    const model = buildReportModel(report([
      row(1000, false, { currentComputeMs: 4000, baselineComputeMs: 2000, moves: 10 }),
      row(1004, false, { currentComputeMs: 100000, baselineComputeMs: null, moves: null }),
      row(1000, true, { currentComputeMs: 0, baselineComputeMs: 1000, moves: 14 }),
      row(1004, true, { status: "error", currentComputeMs: null, baselineComputeMs: 50000, moves: undefined, error: "failed" }),
    ], { valid: false }));
    expect(model.aggregate).toMatchObject({ currentCount: 3, baselineCount: 3, pairedCount: 2,
      pairedCurrentMean: 2, pairedBaselineMean: 1.5, deltaSeconds: 0.5,
      movesMean: 12, movesCount: 2, missingCurrent: 1, missingBaseline: 1, missingMoves: 2,
    });
    expect(model.aggregate.currentMean).toBeCloseTo(104 / 3);
    expect(model.aggregate.deltaPercent).toBeCloseTo(100 / 3);
    expect(model.modes.pool.pairedCurrentMean).toBe(4);
    expect(model.modes.expanded.pairedCurrentMean).toBe(0);
    expect(model.moveBins).toEqual([10, 11, 12, 13, 14]);
    expect(model.modes.pool.moveCounts).toEqual([1, 0, 0, 0, 0]);
    expect(model.modes.expanded.moveCounts).toEqual([0, 0, 0, 0, 1]);
    expect(model.successful).toBe(false);
  });
  it("omits baseline series from record reports and leaves empty means unavailable", () => {
    const model = buildReportModel(report([row(1000, false, { status: "recorded" }), row(1000, true, { status: "recorded" })], { action: "record" }));
    expect(model.hasBaseline).toBe(false);
    expect(renderReportHTML(model)).not.toContain("Baseline (0)");
    const empty = buildReportModel(report([], { valid: false, errors: ["Setup failed"] }));
    expect(empty.aggregate.currentMean).toBeNull();
    expect(empty.aggregate.movesMean).toBeNull();
    expect(renderReportHTML(empty)).toContain("Unavailable");
  });
  it("labels partial and failed runs prominently even if a report claims valid", () => {
    const partial = buildReportModel(report([row(), row(1000, true)]));
    expect(partial.successful).toBe(true);
    expect(partial.fullSample).toBe(false);
    expect(renderReportHTML(partial)).toContain("Partial sample · 2/2 cases");
    const failed = buildReportModel(report([row(1000, false, { status: "mismatch", difference: { path: "$[\"moves\"][0]" } }), row(1000, true)]));
    expect(failed.successful).toBe(false);
    const html = renderReportHTML(failed);
    expect(html).toContain("Failed or incomplete run");
    expect(html).toContain("cannot support performance acceptance");
    expect(html).toContain("first difference");
  });
});

describe("saved report validation and escaping", () => {
  it.each([
    (value) => { value.schemaVersion = 2; },
    (value) => { value.cases[0].currentComputeMs = -1; },
    (value) => { value.cases[0].baselineComputeMs = Infinity; },
    (value) => { value.cases[0].moves = 2.5; },
    (value) => { value.cases[0].expanded = "false"; },
    (value) => { value.cases[0].status = "unknown"; },
    (value) => { value.cases.push(value.cases[0]); },
    (value) => { value.notes = [{}]; },
    (value) => { value.selection.games = 100; },
  ])("rejects malformed schema or rows", (edit) => {
    const value = report([row(), row(1000, true)]); edit(value);
    expect(() => buildReportModel(value)).toThrow();
  });
  it("escapes every report-derived text field and requires no scripts or external assets", () => {
    const attack = '<script>alert("x")</script><img src="https://invalid.example/x" onerror="bad">&';
    const html = renderReportHTML(buildReportModel(report([row(1000, false, { status: "error", currentComputeMs: null, error: attack }), row(1000, true)], {
      valid: false, notes: [attack], errors: [attack], order: attack, timingBoundaries: attack,
    })));
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<link");
    expect(html).not.toMatch(/<[^>]+\bsrc=/);
    expect(html).toContain("&lt;script&gt;alert(&quot;x&quot;)");
    expect(html).toContain("<svg");
  });
});

describe("offline report command", () => {
  const temporary = [];
  afterEach(async () => { vi.restoreAllMocks(); await Promise.all(temporary.splice(0).map((dir) => rm(dir, { recursive: true, force: true }))); });
  async function fixture() {
    const dir = await mkdtemp(path.join(os.tmpdir(), "referdle-html-test-")); temporary.push(dir);
    const input = path.join(dir, "report.json");
    await writeFile(input, JSON.stringify(report([row(), row(1000, true)])));
    return { dir, input, output: path.join(dir, "report.html") };
  }
  it("postprocesses saved JSON without rerunning or rewriting the solver/report", async () => {
    const { input, output } = await fixture();
    const before = await readFile(input);
    const solver = vi.spyOn(harness, "createHarness").mockImplementation(() => { throw new Error("Solver must not run"); });
    expect(await main([input, "--output", output], { stdout() {}, stderr() {} })).toBe(0);
    expect(solver).not.toHaveBeenCalled();
    expect(await readFile(input)).toEqual(before);
    expect(await readFile(output, "utf8")).toContain("Computation time per game");
  });
  it("refuses repository/reference outputs and same-file, symlink and hard-link aliases", async () => {
    const { input, dir } = await fixture();
    await expect(writeHTMLReport(input, input)).rejects.toThrow("different files");
    await symlink(input, path.join(dir, "alias.html"));
    await expect(writeHTMLReport(input, path.join(dir, "alias.html"))).rejects.toThrow("different files");
    await link(input, path.join(dir, "hard-link.html"));
    await expect(writeHTMLReport(input, path.join(dir, "hard-link.html"))).rejects.toThrow("different files");
    await expect(writeHTMLReport(input, path.join(harness.COMPONENT_DIR, "index.html"))).rejects.toThrow("Report file");
    await expect(writeHTMLReport(input, path.join(harness.COMPONENT_DIR, "benchmarks/references/report.html"))).rejects.toThrow("Report file");
    expect(JSON.parse(await readFile(input, "utf8")).schemaVersion).toBe(1);
  });
  it("returns nonzero on malformed CLI arguments or corrupt input", async () => {
    const { input, output } = await fixture();
    const io = { stdout() {}, stderr() {} };
    expect(await main([input], io)).toBe(1);
    await writeFile(input, "corrupt JSON");
    expect(await main([input, "--output", output], io)).toBe(1);
  });
});

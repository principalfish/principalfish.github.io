import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { mkdtemp, readFile, writeFile, rm, stat, rename, mkdir, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { COMPONENT_DIR, loadSample } from "../harness.mjs";
import { main, parseArgs, runBenchmark, selectDays } from "../runner.mjs";
import { assertSafePaths, beginRecording, DEFAULT_BASELINE_DIR, readBaseline, readCase, writeCase } from "../references.mjs";

let sample;
beforeAll(async () => { sample = await loadSample(); });
const temporary = [];
afterEach(async () => { await Promise.all(temporary.splice(0).map((dir) => rm(dir, { recursive: true, force: true }))); });
async function directory() {
  const dir = await mkdtemp(path.join(os.tmpdir(), "referdle-cli-test-"));
  temporary.push(dir); return dir;
}
function trace(day, expanded) {
  const answers = sample.games.find((game) => game.day === day).answers;
  return { schemaVersion: 1, day, expanded, answers, clueGrid: Array(5).fill("22222"),
    moves: [{ word: answers[0], board: 0, colors: "22222", before: { cands: [answers] }, suggest: { ranked: [{ word: answers[0], score: 1.0000000000000002 }] }, after: { solvable: true } }],
    final: { status: "solved", game: { solved: true }, slots: answers.map((word) => ({ guesses: [{ word, colors: "22222" }] })), result: { solvable: true } },
  };
}
function dependencies({ edit, failure, provenance = {} } = {}) {
  const calls = [];
  return {
    calls, progress() {}, loadSample: async () => sample,
    collectProvenance: async () => ({ node: "v22", platform: "linux", architecture: "x64", release: "test", cpu: { model: "test", logicalCount: 1 }, ...provenance }),
    createHarness: async () => ({
      sample, strategy: { probe_cap: 220 },
      state: { N: 2, PM: new Int16Array([0, 1, 2, 242]), POOL: ["AAAAA", "BBBBB"], EXPANDED: [], ALL_GUESSES: ["AAAAA", "BBBBB"], PLURALS: new Set() },
      async runGame(day, expanded) {
        calls.push([day, expanded]);
        if (failure && expanded) throw new Error("injected turn failure");
        const value = trace(day, expanded); if (edit) edit(value);
        return { trace: value, computeMs: expanded ? 20 : 10, turnMs: [expanded ? 20 : 10] };
      },
    }),
  };
}
function options(dir, extra = {}) {
  return { ...parseArgs([]), baselineDir: path.join(dir, "baseline"), days: [1000], ...extra };
}
async function record(dir, extra = {}) {
  const report = await runBenchmark(options(dir, { record: true, ...extra }), dependencies());
  expect(report.valid).toBe(true); return report;
}

describe("CLI selection and paths", () => {
  it("defaults to verification and rejects ambiguous or invalid options", () => {
    expect(parseArgs([])).toMatchObject({ record: false, overwrite: false, days: null, baselineDir: DEFAULT_BASELINE_DIR });
    for (const args of [["--overwrite"], ["--days"], ["--days", "1000,1000"], ["--days", "no"], ["--record", "--record"], ["--unknown"]]) {
      expect(() => parseArgs(args)).toThrow();
    }
    expect(selectDays(sample, [1414, 1000])).toEqual([1000, 1414]);
    expect(() => selectDays(sample, [1001])).toThrow("outside the frozen sample");
  });
  it("protects source, repository ancestors, baseline artifacts and symlinked reports", async () => {
    const dir = await directory(), baseline = path.join(dir, "baseline");
    await expect(assertSafePaths(COMPONENT_DIR)).rejects.toThrow("Baseline directory");
    await expect(assertSafePaths(path.dirname(path.dirname(COMPONENT_DIR)))).rejects.toThrow("Baseline directory");
    await expect(assertSafePaths(baseline, path.join(baseline, "timing-baseline.json"))).rejects.toThrow("Report file");
    await expect(assertSafePaths(baseline, path.join(COMPONENT_DIR, "index.html"))).rejects.toThrow("Report file");
    await symlink(path.join(COMPONENT_DIR, "index.html"), path.join(dir, "report.json"));
    await expect(assertSafePaths(baseline, path.join(dir, "report.json"))).rejects.toThrow("Report file");
    await symlink(path.join(COMPONENT_DIR, "benchmarks"), path.join(dir, "benchmark-alias"));
    await expect(assertSafePaths(path.join(dir, "benchmark-alias/references"))).rejects.toThrow("Baseline directory");
    await expect(assertSafePaths(baseline, path.join(dir, "safe.json"))).resolves.toBeUndefined();
  });
  it("refuses partial recordings into the default baseline", async () => {
    const report = await runBenchmark({ ...parseArgs([]), record: true, days: [1000] }, dependencies());
    expect(report.valid).toBe(false);
    expect(report.errors[0]).toContain("Partial recording");
  });
});

describe("explicit reference lifecycle", () => {
  it("missing default verification never creates a baseline", async () => {
    const dir = await directory(), deps = dependencies();
    const report = await runBenchmark(options(dir), deps);
    expect(report.valid).toBe(false);
    expect(report.errors[0]).toContain("Missing or corrupt");
    await expect(stat(path.join(dir, "baseline"))).rejects.toMatchObject({ code: "ENOENT" });
    expect(deps.calls).toEqual([]);
  });
  it("records both modes in fixed order, verifies without changing artifacts, and reports size/timings", async () => {
    const dir = await directory(), deps = dependencies();
    const recorded = await runBenchmark(options(dir, { record: true, days: [1414, 1000] }), deps);
    expect(recorded.valid).toBe(true);
    expect(deps.calls).toEqual([[1000, false], [1414, false], [1000, true], [1414, true]]);
    expect(recorded.selection).toMatchObject({ fullSample: false, games: 2, cases: 4 });
    expect(recorded.referenceBytes).toBeGreaterThan(0);
    const manifestBefore = await readFile(path.join(dir, "baseline/reference-manifest.json"));
    const verified = await runBenchmark(options(dir, { days: [1414, 1000] }), dependencies({ provenance: { sourceHashes: { source: "new" }, node: "v24" } }));
    expect(verified.valid).toBe(true);
    expect(verified.cases.every((row) => row.status === "matched" && row.deltaMs === 0)).toBe(true);
    expect(verified.summary.overall.computeMs).toBe(60);
    expect(verified.notes[0]).toContain("Timing environment differs");
    expect(await readFile(path.join(dir, "baseline/reference-manifest.json"))).toEqual(manifestBefore);
  });
  it("refuses accidental overwrite and failed replacement preserves the original", async () => {
    const dir = await directory(); await record(dir);
    const before = await readFile(path.join(dir, "baseline/reference-manifest.json"));
    const refused = await runBenchmark(options(dir, { record: true }), dependencies());
    expect(refused.errors[0]).toContain("--record --overwrite");
    const failed = await runBenchmark(options(dir, { record: true, overwrite: true }), dependencies({ failure: true }));
    expect(failed.valid).toBe(false);
    expect(failed.cases[1].error).toContain("injected turn failure");
    expect(await readFile(path.join(dir, "baseline/reference-manifest.json"))).toEqual(before);
    expect((await runBenchmark(options(dir), dependencies())).valid).toBe(true);
  });
  it("refuses unrelated directories even with overwrite, including extra files beside valid references", async () => {
    const dir = await directory(); await mkdir(path.join(dir, "baseline"));
    await writeFile(path.join(dir, "baseline/keep.txt"), "keep");
    const refused = await runBenchmark(options(dir, { record: true, overwrite: true }), dependencies());
    expect(refused.valid).toBe(false);
    expect(await readFile(path.join(dir, "baseline/keep.txt"), "utf8")).toBe("keep");
    await rm(path.join(dir, "baseline"), { recursive: true }); await record(dir);
    await writeFile(path.join(dir, "baseline/keep.txt"), "keep");
    const extra = await runBenchmark(options(dir, { record: true, overwrite: true }), dependencies());
    expect(extra.errors[0]).toContain("unrelated files");
  });
  it("rejects concurrent recording and rolls back a failed final rename", async () => {
    const dir = await directory(), baselineDir = path.join(dir, "baseline");
    const owner = await beginRecording(baselineDir);
    await expect(beginRecording(baselineDir)).rejects.toThrow("Another reference writer");
    await owner.abort(); await record(dir);
    const before = await readFile(path.join(baselineDir, "reference-manifest.json"));
    const { manifest, timings } = await readBaseline(baselineDir);
    const transaction = await beginRecording(baselineDir, { overwrite: true });
    for (const entry of manifest.cases) await writeCase(transaction.staging, await readCase(baselineDir, entry));
    let calls = 0;
    await expect(transaction.publish(manifest, timings, { renameDirectory: async (from, to) => {
      calls++; if (calls === 2) throw new Error("injected publish failure");
      await rename(from, to);
    } })).rejects.toThrow("injected publish failure");
    await transaction.abort();
    expect(await readFile(path.join(baselineDir, "reference-manifest.json"))).toEqual(before);
    expect((await runBenchmark(options(dir), dependencies())).valid).toBe(true);
  });
});

describe("correctness failures and CLI exits", () => {
  it("reports the first semantic divergence and exits nonzero with a complete JSON report", async () => {
    const dir = await directory(); await record(dir);
    const deps = dependencies({ edit(value) { value.moves[0].suggest.ranked[0].score = 1; } });
    let output = "";
    const code = await main(["--days", "1000", "--baseline-dir", path.join(dir, "baseline"), "--report", path.join(dir, "report.json")], { ...deps, stdout(text) { output += text; }, stderr() {} });
    expect(code).toBe(1);
    const report = JSON.parse(output);
    expect(report.valid).toBe(false);
    expect(report.cases[0].difference).toMatchObject({ day: 1000, expanded: false, moveIndex: 0 });
    expect(report.cases[0].difference.path).toContain("score");
    expect(report.notes[0]).toContain("invalid for performance acceptance");
    expect(JSON.parse(await readFile(path.join(dir, "report.json"), "utf8"))).toEqual(report);
  });
  it("fails changed contracts before solving and rejects corrupt or missing case files", async () => {
    const dir = await directory(); await record(dir);
    const file = path.join(dir, "baseline/reference-manifest.json");
    const manifest = JSON.parse(await readFile(file, "utf8"));
    manifest.contract.strategy.probe_cap++;
    await writeFile(file, JSON.stringify(manifest));
    const deps = dependencies(), changed = await runBenchmark(options(dir), deps);
    expect(changed.errors[0]).toContain("Reference contract changed");
    expect(deps.calls).toEqual([]);
    manifest.contract.strategy.probe_cap--; await writeFile(file, JSON.stringify(manifest));
    await writeFile(path.join(dir, "baseline/pool/1000.json.gz"), "corrupt");
    const corrupt = await runBenchmark(options(dir), dependencies());
    expect(corrupt.valid).toBe(false);
    expect(corrupt.cases[0].error).toContain("Missing or corrupt reference pool/1000");
    expect(corrupt.cases[1].status).toBe("matched");
    await rm(path.join(dir, "baseline/expanded/1000.json.gz"));
    expect((await runBenchmark(options(dir), dependencies())).cases[1].status).toBe("error");
  });
  it("never publishes incomplete games and preserves their compressed evidence", async () => {
    const dir = await directory();
    const incomplete = await runBenchmark(options(dir, { record: true }), dependencies({ edit(value) { value.final.status = "incomplete"; value.final.result = { solvable: false, reason: "pre-existing" }; } }));
    expect(incomplete.valid).toBe(false);
    expect(incomplete.cases.every((row) => row.status === "incomplete")).toBe(true);
    await expect(stat(path.join(dir, "baseline"))).rejects.toMatchObject({ code: "ENOENT" });
    for (const row of incomplete.cases) {
      const evidence = row.error.split("compressed trace evidence: ")[1];
      expect((await stat(evidence)).size).toBeGreaterThan(0);
      temporary.push(path.dirname(path.dirname(evidence)));
    }
  });
});

import { describe, expect, it } from "vitest";
import { existsSync } from "node:fs";
import { createHarness, loadSample } from "../harness.mjs";
import { createNativeWorkerFactory } from "../worker-bridge.mjs";
import { workerModules } from "./worker-support.mjs";
import { compareTraces } from "../trace.mjs";
import { assertCompatible } from "../metadata.mjs";
import { DEFAULT_BASELINE_DIR, readBaseline, readCase } from "../references.mjs";

const words = ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"];
const position = { words, slots: words.map(() => ({ guesses: [] })), grid: Array(5).fill("22222"), done: false };
const result = { solvable: true, cands: [["CCCCC", "AAAAA"], ["BBBBB"]],
  precise: 1.0000000000000002, present: undefined, negativeZero: -0,
  nested: [undefined, null, -0, 1.0000000000000002], sparse: ["AAAAA", , "BBBBB"] };
const expected = { continuation: position, result, before: result, after: result,
  move: { word: words[0], board: 0, colors: "22222" },
  suggest: { ranked: [{ board: 0, word: words[0], score: -0 }], perBoard: [{ board: 0, top: [{ word: words[0], score: 1.0000000000000002 }] }] } };
const replacements = {
  "data.js": `export async function loadAssets() { return {}; }
export async function dailyGame() { return ${JSON.stringify(words)}; }
export async function testableDays() { return [1000]; }`,
  "compute-service.js": `export function createComputeService() {
return { request(message) {
  if (message.type === "catalogue") return { pool: ${JSON.stringify(words)}, expanded: ${JSON.stringify(words)} };
  if (message.type === "load-day") return { status: "loaded", continuation: ${JSON.stringify(position)} };
  if (message.type !== "advance-daily") throw new Error("Unexpected fixture request");
  const start = performance.now(); while (performance.now() - start < 150) {}
  const result = ${JSON.stringify(result)};
  result.present = undefined; result.negativeZero = -0;
  result.nested = [undefined, null, -0, 1.0000000000000002]; result.sparse = ["AAAAA", , "BBBBB"];
  const reply = ${JSON.stringify(expected)};
  reply.before = reply.after = reply.result = result;
  reply.suggest.ranked[0].score = -0;
  return reply;
} }; }`,
};

describe("native benchmark adapter", () => {
  it("uses the real client/handler for bounded slow jobs and preserves all structured-clone distinctions", async () => {
    const { createWorkerClient } = await workerModules();
    const bridge = await createNativeWorkerFactory({ replacements });
    const client = createWorkerClient({ workerFactory: bridge.factory,
      directFactory: async () => { throw new Error("No direct fallback in native validation"); } });
    try {
      await client.request({ type: "initialize" });
      const loaded = await client.request({ type: "load-day", day: 1000 });
      client.acceptDaily({ day: 1000, continuation: loaded.continuation });
      let ticks = 0;
      const timer = setInterval(() => { ticks++; }, 5);
      let actual;
      try { actual = await client.request({ type: "advance-daily", expanded: false }); }
      finally { clearInterval(timer); }
      expect(ticks).toBeGreaterThan(5);
      expect(compareTraces(expected, actual)).toEqual({ equal: true });
      expect(Object.hasOwn(actual.result, "absent")).toBe(false);
      expect(Object.hasOwn(actual.result, "present")).toBe(true);
      expect(Object.hasOwn(actual.result.sparse, 1)).toBe(false);
      expect(Object.is(actual.result.negativeZero, -0)).toBe(true);
      const timing = client.getTiming();
      expect(timing.computeMs).toBeGreaterThanOrEqual(140);
      expect(timing.roundtripMs).toBeGreaterThanOrEqual(timing.computeMs);
    } finally { client.dispose(); await bridge.dispose(); }
    expect(bridge.workers[0].native.threadId).toBe(-1);
    expect(bridge.workers[0].native.listenerCount("message")).toBe(0);
    expect(bridge.workers[0].native.listenerCount("error")).toBe(0);
  }, 10000);

  it.skipIf(!existsSync(DEFAULT_BASELINE_DIR))("computes compatible metadata in the single Worker-owned matrix and drives a complete live-controller trace (local baseline)", async () => {
    const sample = await loadSample();
    const harness = await createHarness({ sample, backend: "worker" });
    try {
      expect(harness.state).not.toHaveProperty("PM");
      expect(harness.ownership).toEqual({ assetLoads: 1, decodedMatrixBytes: 16378209, matrixStorage: "Uint8Array" });
      const baseline = await readBaseline(DEFAULT_BASELINE_DIR);
      expect(() => assertCompatible(baseline.manifest.contract, harness.contract)).not.toThrow();
      const actual = await harness.runGame(1000, false);
      const reference = await readCase(DEFAULT_BASELINE_DIR, baseline.manifest.cases.find(row => row.day === 1000 && !row.expanded));
      expect(compareTraces(reference, actual.trace)).toEqual({ equal: true });
      expect(actual.roundtripMs).toBeGreaterThanOrEqual(actual.computeMs);
      expect(actual.turnRoundtripMs).toHaveLength(actual.turnMs.length);
      expect(actual.roundtripMs).toBe(actual.turnRoundtripMs.reduce((sum, value) => sum + value, 0));
    } finally { await harness.dispose(); }
    await expect(harness.runGame(1000, false)).rejects.toThrow("disposed");
  }, 20000);

  it("fails unavailable or changed metadata and detaches control listeners", async () => {
    const sample = await loadSample();
    const bridge = await createNativeWorkerFactory();
    const worker = bridge.factory();
    try {
      await expect(bridge.contract(worker, sample)).rejects.toThrow("initialize first");
      expect(worker.native.listenerCount("message")).toBe(0);
    } finally { await bridge.dispose(); }
    const changed = structuredClone(sample); changed.games[0].answers[0] = "ZZZZZ";
    await expect(createHarness({ sample: changed, backend: "worker" })).rejects.toThrow("Frozen answers differ");
  }, 10000);
});

import { beforeAll, describe, expect, it } from "vitest";
import { existsSync } from "node:fs";
import { DEFAULT_BASELINE_DIR, readBaseline, readCase } from "../references.mjs";
import { createNativeWorkerFactory, workerModules } from "./worker-support.mjs";

let modules;
beforeAll(async () => { modules = await workerModules(); });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

describe("production Worker handler", () => {
  it("owns assets once, reports precise structured-clone values and measures synchronous computation only", async () => {
    let loads = 0;
    let time = 10;
    const messages = [];
    const value = { precise: 1.0000000000000002, missing: undefined, zero: -0, infinite: Infinity };
    const runtime = modules.createWorkerRuntime(m => messages.push(structuredClone(m)), {
      load: async progress => { loads++; progress("loading"); return {}; }, listDays: async () => [1000],
      createService: () => ({ request: message => {
        if (message.type === "catalogue") return { pool: ["AAAAA"], expanded: ["AAAAA"] };
        time += 7;
        return value;
      } }), now: () => time,
    });
    await runtime.receive({ type: "request", id: 1, generation: 2, message: { type: "initialize" } });
    await runtime.receive({ type: "request", id: 2, generation: 2, message: { type: "test" } });
    expect(loads).toBe(1);
    expect(messages.at(-1)).toMatchObject({ id: 2, generation: 2, computeMs: 7, result: value });
    expect(Object.hasOwn(messages.at(-1).result, "missing")).toBe(true);
    expect(Object.is(messages.at(-1).result.zero, -0)).toBe(true);
    expect(messages[0].type).toBe("progress");
  });

  it("rejects concurrent jobs and distinguishes asset and computation failures", async () => {
    const held = deferred();
    const messages = [];
    const runtime = modules.createWorkerRuntime(m => messages.push(m), {
      load: () => held.promise, listDays: async () => [], createService: () => ({ request: () => { throw new Error("compute failed"); } }),
    });
    const first = runtime.receive({ type: "request", id: 1, generation: 0, message: { type: "initialize" } });
    await runtime.receive({ type: "request", id: 2, generation: 0, message: { type: "test" } });
    expect(messages[0]).toMatchObject({ id: 2, type: "failure", kind: "compute" });
    held.resolve({}); await first;
    expect(messages.at(-1)).toMatchObject({ kind: "asset", error: "compute failed" });
    const healthy = modules.createWorkerRuntime(m => messages.push(m), {
      load: async () => ({}), listDays: async () => [], createService: () => ({ request: m => {
        if (m.type === "catalogue") return { pool: [], expanded: [] };
        throw new Error("bad request");
      } }),
    });
    await healthy.receive({ type: "request", id: 3, generation: 0, message: { type: "initialize" } });
    await healthy.receive({ type: "request", id: 4, generation: 0, message: { type: "bad" } });
    expect(messages.at(-1)).toMatchObject({ kind: "compute", error: "bad request" });
  });

  it("restores manual replay metadata without solving, and sends only a compact final-step delta", async () => {
    const { createControllerBundle } = await import("../harness.mjs");
    const words = ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"];
    const module = await createControllerBundle({ replacements: {
      "solver.js": `import { capture } from "benchmark:capture";
export function solveRelaxed() { capture.count = (capture.count || 0)+1; return { solvable:true, perSlotFeasible:${JSON.stringify(words)}.map(w=>[w]) }; }
export const solve = solveRelaxed;`,
      "suggest.js": `export function buildSuggestions() { return { ranked:[], perBoard:[] }; }`,
    } });
    const service = module.createComputeService({ POOL: words, ALL_GUESSES: words, PLURALS: new Set() });
    const slots = words.map(word => ({ guesses: [{ word, colors: "22222" }] }));
    service.request({ type: "restore-manual", slots, clueGrid: null });
    expect(module.capture.count).toBeUndefined();
    const reply = service.request({ type: "analyse-manual", slots, clueGrid: null, expanded: false, compact: true });
    expect(module.capture.count).toBe(1);
    expect(reply).not.toHaveProperty("steps");
    expect(reply.moves).toHaveLength(5);
    expect(reply.finalStep.after.solvable).toBe(true);
    expect(reply.reuse).toBe(5);
  });
});

describe.skipIf(!existsSync(DEFAULT_BASELINE_DIR))("bounded native Worker transport (local baseline)", () => {
  it("runs the real engine in both probe modes with exact frozen opening state and host progress", async () => {
    const bridge = await createNativeWorkerFactory();
    const client = modules.createWorkerClient({ workerFactory: bridge.factory,
      directFactory: async () => { throw new Error("Native test must not fall back"); } });
    try {
      const catalogue = await client.request({ type: "initialize" });
      expect(catalogue.pool).toHaveLength(4047);
      expect(catalogue).not.toHaveProperty("PM");
      const { manifest } = await readBaseline(DEFAULT_BASELINE_DIR);
      for (const expanded of [false, true]) {
        const loaded = await client.request({ type: "load-day", day: 1000 });
        client.acceptDaily({ day: 1000, continuation: loaded.continuation });
        let ticks = 0;
        const timer = setInterval(() => { ticks++; }, 10);
        let reply;
        try { reply = await client.request({ type: "advance-daily", expanded }); }
        finally { clearInterval(timer); }
        const reference = await readCase(DEFAULT_BASELINE_DIR, manifest.cases.find(c => c.day === 1000 && c.expanded === expanded));
        expect(reply.before).toEqual(reference.moves[0].before);
        expect(reply.after).toEqual(reference.moves[0].after);
        expect(reply.suggest).toEqual(reference.moves[0].suggest);
        const { before, after, suggest, ...move } = reference.moves[0];
        expect(reply.move).toEqual(move);
        expect(ticks).toBeGreaterThan(2);
        expect(client.getTiming().computeMs).toBeGreaterThan(100);
        expect(client.getTiming().roundtripMs).toBeGreaterThanOrEqual(client.getTiming().computeMs);
      }
      const reference = await readCase(DEFAULT_BASELINE_DIR, manifest.cases.find(c => c.day === 1000 && !c.expanded));
      const input = { slots: reference.final.slots, clueGrid: reference.clueGrid, expanded: false };
      const manual = await client.request({ type: "analyse-manual", ...input, compact: true });
      client.acceptManual(input);
      expect(manual.result.solvable).toBe(true);
      expect(manual).not.toHaveProperty("steps");
      expect(manual.moves).toHaveLength(reference.moves.length);
      const replay = await client.request({ type: "replay-manual", index: manual.moves.length - 1,
        clueGrid: input.clueGrid, expanded: false });
      expect(replay.slots).toEqual(input.slots);
      expect(replay.step.after).toEqual(manual.result);
      expect(replay.step.suggest.ranked).toEqual([]);
      expect(bridge.workers).toHaveLength(1);
    } finally { client.dispose(); await bridge.dispose(); }
  }, 30000);

  it("physically cancels an active real solve and restarts from the committed continuation", async () => {
    const bridge = await createNativeWorkerFactory();
    const client = modules.createWorkerClient({ workerFactory: bridge.factory,
      directFactory: async () => { throw new Error("Native test must not fall back"); } });
    try {
      await client.request({ type: "initialize" });
      const loaded = await client.request({ type: "load-day", day: 1000 });
      client.acceptDaily({ day: 1000, continuation: loaded.continuation });
      const pending = client.request({ type: "advance-daily", expanded: true });
      const rejected = expect(pending).rejects.toMatchObject({ name: "CancelledError" });
      await new Promise(resolve => setTimeout(resolve, 30));
      client.cancel();
      await rejected;
      const retried = await client.request({ type: "advance-daily", expanded: false });
      const { manifest } = await readBaseline(DEFAULT_BASELINE_DIR);
      const reference = await readCase(DEFAULT_BASELINE_DIR, manifest.cases.find(c => c.day === 1000 && !c.expanded));
      expect(retried.suggest).toEqual(reference.moves[0].suggest);
      expect(retried.continuation.slots.reduce((n,s)=>n+s.guesses.length,0)).toBe(1);
      expect(bridge.workers).toHaveLength(2);
    } finally { client.dispose(); await bridge.dispose(); }
  }, 30000);
});

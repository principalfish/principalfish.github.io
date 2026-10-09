import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { workerModules } from "./worker-support.mjs";

let createWorkerClient;
beforeAll(async () => { ({ createWorkerClient } = await workerModules()); });
const clients = [];
afterEach(() => { clients.splice(0).forEach(c => c.dispose()); vi.useRealTimers(); });
const words = ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"];
const catalogue = () => ({ pool: words, expanded: [...words, "ZZZZZ"], days: [1000] });
const position = () => ({ words, slots: words.map(() => ({ guesses: [] })), grid: Array(5).fill("22222"), done: false });
const result = () => ({ continuation: position(), result: { solvable: true, precise: 1.0000000000000002, missing: undefined, zero: -0 } });
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function factory() {
  const workers = [];
  const make = () => {
    const listeners = new Map();
    const worker = {
      messages: [], terminated: false,
      addEventListener(type, listener) { listeners.set(type, listener); },
      removeEventListener(type, listener) { if (listeners.get(type) === listener) listeners.delete(type); },
      terminate() { this.terminated = true; },
      postMessage(message) {
        this.messages.push(structuredClone(message));
        if (message.message.type === "initialize") queueMicrotask(() => this.reply(message, catalogue()));
        if (["restore-daily", "restore-manual", "reset-manual"].includes(message.message.type)) {
          queueMicrotask(() => this.reply(message, message.message.type === "restore-daily" ? message.message.continuation : null));
        }
      },
      emit(type, data) { listeners.get(type)?.(type === "message" ? { data } : data); },
      reply(message, value, fields = {}) { this.emit("message", { type: "result", id: message.id, generation: message.generation, result: value, computeMs: 2, ...fields }); },
      listeners,
    };
    workers.push(worker);
    queueMicrotask(() => worker.emit("message", { type: "ready" }));
    return worker;
  };
  return { workers, make };
}
async function setup(options = {}) {
  const f = factory();
  const fallback = vi.fn();
  const client = createWorkerClient({ workerFactory: f.make, directFactory: fallback, ...options });
  clients.push(client);
  await client.request({ type: "initialize" });
  return { ...f, client, fallback };
}

describe("single Worker lifecycle", () => {
  it("owns assets in the Worker and clones the lightweight catalogue", async () => {
    const { client, workers, fallback } = await setup();
    const first = await client.request({ type: "catalogue" });
    expect(first).not.toHaveProperty("PM");
    first.pool.pop();
    expect((await client.request({ type: "catalogue" })).pool).toEqual(words);
    expect(workers).toHaveLength(1);
    expect(fallback).not.toHaveBeenCalled();
  });

  it("terminates obsolete CPU work, rejects cancellation and restores accepted continuation only", async () => {
    const { client, workers } = await setup();
    const accepted = position();
    client.acceptDaily({ day: 1000, continuation: accepted });
    accepted.slots[0].guesses.push({ word: "ZZZZZ", colors: "00000" });
    const obsolete = client.request({ type: "advance-daily", expanded: false });
    const rejected = expect(obsolete).rejects.toMatchObject({ name: "CancelledError" });
    await flush();
    await expect(client.request({ type: "advance-daily" })).rejects.toThrow("already active");
    client.cancel();
    await rejected;
    expect(workers[0].terminated).toBe(true);
    expect(workers[0].listeners.size).toBe(0);
    const next = client.request({ type: "advance-daily", expanded: true });
    await flush();
    const restarted = workers[1];
    expect(restarted.messages.map(m => m.message.type)).toEqual(["initialize", "restore-daily", "advance-daily"]);
    expect(restarted.messages[1].message.continuation.slots[0].guesses).toEqual([]);
    restarted.reply(restarted.messages.at(-1), result());
    const acceptedReply = await next;
    expect(acceptedReply).toEqual(result());
    expect(Object.hasOwn(acceptedReply.result, "missing")).toBe(true);
    expect(Object.is(acceptedReply.result.zero, -0)).toBe(true);
    expect(client.getTiming()).toMatchObject({ computeMs: 2 });
  });

  it("suppresses old/out-of-order replies and old callbacks without disturbing a newer job", async () => {
    const { client, workers } = await setup();
    client.acceptDaily({ day: 1000, continuation: position() });
    const first = client.request({ type: "advance-daily" });
    const cancelled = expect(first).rejects.toMatchObject({ name: "CancelledError" });
    await flush();
    const oldMessage = workers[0].messages.at(-1);
    const oldCallback = workers[0].listeners.get("message");
    const oldError = workers[0].listeners.get("error");
    client.cancel();
    await cancelled;
    const second = client.request({ type: "advance-daily" });
    await flush();
    const current = workers[1].messages.at(-1);
    oldCallback({ data: { ...oldMessage, type: "result", result: result(), computeMs: 1 } });
    oldError({ message: "obsolete crash" });
    workers[1].reply({ ...current, id: current.id - 1 }, result());
    let done = false;
    second.then(() => { done = true; });
    await flush();
    expect(done).toBe(false);
    expect(workers[1].terminated).toBe(false);
    workers[1].reply(current, result());
    await second;
  });

  it.each(["error", "messageerror"])("preserves accepted state on runtime %s and permits explicit retry", async (type) => {
    const { client, workers, fallback } = await setup();
    client.acceptDaily({ day: 1000, continuation: position() });
    const job = client.request({ type: "advance-daily" });
    const rejected = expect(job).rejects.toThrow(type === "error" ? "crashed" : "could not be read");
    await flush();
    workers[0].emit(type, { message: "crashed" });
    await rejected;
    expect(workers[0].terminated).toBe(true);
    expect(fallback).not.toHaveBeenCalled();
    const retry = client.request({ type: "advance-daily" });
    await flush();
    expect(workers[1].messages[1].message.type).toBe("restore-daily");
    workers[1].reply(workers[1].messages.at(-1), result());
    await retry;
  });

  it.each([null, {}, { type: "result", result: {} }])("rejects malformed replies %j", async (bad) => {
    const { client, workers, fallback } = await setup();
    client.acceptDaily({ day: 1000, continuation: position() });
    const job = client.request({ type: "advance-daily" });
    const rejected = expect(job).rejects.toThrow("Malformed");
    await flush();
    workers[0].emit("message", bad);
    await rejected;
    expect(workers[0].terminated).toBe(true);
    expect(fallback).not.toHaveBeenCalled();
  });

  it("restores manual entered input without replaying final or prefix solves", async () => {
    const { client, workers } = await setup();
    const input = { slots: position().slots, clueGrid: null, expanded: false };
    client.acceptManual(input);
    const first = client.request({ type: "replay-manual", index: 0 });
    const rejected = expect(first).rejects.toMatchObject({ name: "CancelledError" });
    await flush();
    client.cancel();
    await rejected;
    const retry = client.request({ type: "replay-manual", index: 0 });
    await flush();
    expect(workers[1].messages.map(m => m.message.type)).toEqual(["initialize", "restore-manual", "replay-manual"]);
    workers[1].reply(workers[1].messages.at(-1), { slots: input.slots, move: { word: "AAAAA" }, step: { after: { solvable: true } } });
    await retry;
  });

  it("invalidates cached manual probe choices in the Worker before analysing the changed set", async () => {
    const { client, workers } = await setup();
    client.resetManual();
    const job = client.request({ type: "analyse-manual", slots: position().slots, clueGrid: null, expanded: true, compact: true });
    await flush();
    expect(workers[0].messages.map(m => m.message.type)).toEqual(["initialize", "reset-manual", "analyse-manual"]);
    workers[0].reply(workers[0].messages.at(-1), { result: { solvable: true }, moves: [], startSlots: position().slots, reuse: 0 });
    await job;
  });

  it("keeps an idle Worker alive and releases listeners on disposal", async () => {
    const { client, workers } = await setup();
    client.cancel();
    await client.request({ type: "catalogue" });
    expect(workers).toHaveLength(1);
    client.dispose();
    expect(workers[0].listeners.size).toBe(0);
    expect(workers[0].terminated).toBe(true);
    await expect(client.request({ type: "initialize" })).rejects.toMatchObject({ name: "CancelledError" });
  });
});

describe("startup and asset errors", () => {
  it("cancels startup and clears its timeout without falling back", async () => {
    vi.useFakeTimers();
    const f = factory();
    const fallback = vi.fn();
    const client = createWorkerClient({ workerFactory: f.make, directFactory: fallback });
    clients.push(client);
    const pending = client.request({ type: "initialize" });
    const rejected = expect(pending).rejects.toMatchObject({ name: "CancelledError" });
    client.cancel();
    await rejected;
    await vi.runAllTimersAsync();
    expect(f.workers[0].terminated).toBe(true);
    expect(f.workers[0].listeners.size).toBe(0);
    expect(fallback).not.toHaveBeenCalled();
  });

  it("terminates a Worker that never becomes ready before compatibility fallback", async () => {
    vi.useFakeTimers();
    const f = factory();
    const fallback = vi.fn(async () => {
      expect(f.workers[0].terminated).toBe(true);
      return { service: { request: () => catalogue() }, days: [1000] };
    });
    const client = createWorkerClient({ workerFactory: () => {
      const worker = f.make();
      worker.emit = () => {};
      return worker;
    }, startupTimeout: 100, directFactory: fallback });
    clients.push(client);
    const pending = client.request({ type: "initialize" });
    await vi.advanceTimersByTimeAsync(100);
    await expect(pending).resolves.toEqual(catalogue());
    expect(fallback).toHaveBeenCalledTimes(1);
  });

  it("visibly uses direct compatibility mode only after a failed Worker is terminated", async () => {
    const compatibility = vi.fn();
    const f = factory();
    const fallback = vi.fn(async () => {
      expect(f.workers[0].terminated).toBe(true);
      expect(f.workers[0].listeners.size).toBe(0);
      return { service: { request: () => catalogue() }, days: [1000] };
    });
    const client = createWorkerClient({ workerFactory: () => {
      const worker = f.make();
      queueMicrotask(() => worker.emit("error", { message: "cannot start" }));
      // Suppress the ordinary ready delivery to simulate a module setup failure.
      worker.emit = (type, data) => { if (type !== "message") worker.listeners.get(type)?.(data); };
      return worker;
    }, directFactory: fallback, onCompatibility: compatibility });
    clients.push(client);
    expect(await client.request({ type: "initialize" })).toEqual(catalogue());
    expect(compatibility).toHaveBeenCalledWith(expect.stringContaining("Compatibility mode"));
    expect(fallback).toHaveBeenCalledTimes(1);
  });

  it("supports browsers without Worker while surfacing fallback asset failure", async () => {
    const client = createWorkerClient({ workerFactory() { throw new Error("unsupported"); },
      directFactory: async () => { throw new Error("corrupt matrix"); }, onCompatibility: vi.fn() });
    clients.push(client);
    await expect(client.request({ type: "initialize" })).rejects.toThrow("corrupt matrix");
  });

  it("does not silently fall back on an asset failure and retries with a fresh Worker", async () => {
    const f = factory();
    const fallback = vi.fn();
    const client = createWorkerClient({ workerFactory: () => {
      const worker = f.make();
      const post = worker.postMessage;
      worker.postMessage = function (message) {
        if (message.message.type === "initialize" && f.workers.length === 1) {
          this.messages.push(message);
          queueMicrotask(() => this.emit("message", { id: message.id, generation: message.generation, type: "failure", kind: "asset", error: "invalid matrix" }));
        } else post.call(this, message);
      };
      return worker;
    }, directFactory: fallback });
    clients.push(client);
    await expect(client.request({ type: "initialize" })).rejects.toThrow("invalid matrix");
    expect(fallback).not.toHaveBeenCalled();
    expect(f.workers[0].terminated).toBe(true);
    expect(await client.request({ type: "initialize" })).toEqual(catalogue());
    expect(f.workers).toHaveLength(2);
  });
});

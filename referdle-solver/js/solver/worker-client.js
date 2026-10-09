// One computation worker. Only accepted inputs survive cancellation/restart.
export class CancelledError extends Error {
  constructor() { super("Computation cancelled"); this.name = "CancelledError"; }
}
export const isCancelled = (error) => error?.name === "CancelledError";

const positionIsValid = (value) => value && Array.isArray(value.words) && value.words.length === 5
  && Array.isArray(value.slots) && value.slots.length === 5 && Array.isArray(value.grid)
  && typeof value.done === "boolean";
function replyIsValid(type, value) {
  switch (type) {
    case "initialize": return value && Array.isArray(value.pool) && Array.isArray(value.expanded) && Array.isArray(value.days);
    case "load-day": return value?.status === "unavailable-day" || value?.status === "loaded" && positionIsValid(value.continuation);
    case "advance-daily": return positionIsValid(value?.continuation) && typeof value?.result?.solvable === "boolean"
      && (!value.move || value.after && value.suggest);
    case "restore-daily": return positionIsValid(value);
    case "analyse-manual": return typeof value?.result?.solvable === "boolean" && Array.isArray(value.moves)
      && Array.isArray(value.startSlots) && (Array.isArray(value.steps) || Number.isInteger(value.reuse));
    case "replay-manual": return Array.isArray(value?.slots) && value.move && value.step
      && typeof value.step.after?.solvable === "boolean";
    case "restore-manual":
    case "reset-manual": return value === null;
    case "reset-daily": return value === null || positionIsValid(value?.continuation);
    default: return false;
  }
}

export function createWorkerClient({ workerFactory = () => new Worker(new URL("./solver-worker.js", import.meta.url), { type: "module" }),
  onProgress = () => {}, onCompatibility = () => {}, startupTimeout = 10000,
  directFactory = async () => {
    const [{ loadAssets, testableDays }, { createComputeService }] = await Promise.all([
      import("./data.js"), import("./compute-service.js"),
    ]);
    const assets = await loadAssets(onProgress);
    return { service: createComputeService(assets), days: await testableDays(assets) };
  } } = {}) {
  let worker = null;
  let generation = 0;
  let serial = 0;
  let pending = null;
  let starting = null;
  let rejectStartup = null;
  let listeners = null;
  let startupTimer = null;
  let direct = null;
  let disposed = false;
  let daily = null;
  let manual = null;
  let restoredDaily = false;
  let restoredManual = false;
  let manualNeedsReset = false;
  let catalogue = null;
  let lastTiming = null;
  let active = false;

  function detach() {
    clearTimeout(startupTimer);
    startupTimer = null;
    if (worker && listeners) {
      for (const [name, handler] of Object.entries(listeners)) worker.removeEventListener(name, handler);
    }
    listeners = null;
    worker?.terminate();
    worker = null;
    restoredDaily = restoredManual = false;
  }

  function invalidate(error = new CancelledError()) {
    generation++;
    const job = pending;
    pending = null;
    job?.reject(error);
    rejectStartup?.(error);
    rejectStartup = null;
    starting = null;
    detach();
    active = false;
  }

  function send(message) {
    const id = ++serial;
    const currentGeneration = generation;
    const start = performance.now();
    return new Promise((resolve, reject) => {
      pending = { id, generation: currentGeneration, resolve, reject, start, type: message.type };
      try { worker.postMessage({ type: "request", id, generation: currentGeneration, message }); }
      catch (error) { invalidate(error); }
    });
  }

  async function start() {
    if (disposed) throw new CancelledError();
    if (direct || worker && catalogue && !starting) return;
    if (starting) return starting;
    const startGeneration = generation;
    starting = (async () => {
      let setupFailure = false;
      try {
        await new Promise((resolve, reject) => {
          rejectStartup = reject;
          let ready = false;
          let ownedWorker = null;
          const fail = (error) => {
            if (worker !== ownedWorker) return;
            if (!ready) { setupFailure = true; clearTimeout(startupTimer); reject(error); }
            else invalidate(error);
          };
          try { worker = workerFactory(); }
          catch (error) { setupFailure = true; reject(error); return; }
          ownedWorker = worker;
          listeners = {
            message(event) {
              if (worker !== ownedWorker) return;
              const data = event.data;
              if (!ready) {
                if (data?.type !== "ready") { fail(new Error("Invalid Worker startup message")); return; }
                ready = true;
                clearTimeout(startupTimer);
                rejectStartup = null;
                resolve();
                return;
              }
              const job = pending;
              if (!job) return;
              if (!Number.isSafeInteger(data?.generation) || !Number.isSafeInteger(data?.id)) {
                pending = null;
                job.reject(new Error("Malformed Worker reply"));
                detach();
                return;
              }
              if (data.generation !== generation || data.id !== job.id) return;
              if (data.type === "progress" && typeof data.text === "string") { onProgress(data.text); return; }
              pending = null;
              if (data.type === "failure" && typeof data.error === "string") {
                const error = new Error(data.error);
                error.kind = data.kind;
                job.reject(error);
                detach();
              } else if (data?.type === "result" && Object.hasOwn(data, "result")
                  && Number.isFinite(data.computeMs) && data.computeMs >= 0 && replyIsValid(job.type, data.result)) {
                lastTiming = { computeMs: data.computeMs, roundtripMs: performance.now() - job.start };
                job.resolve(data.result);
              } else {
                job.reject(new Error("Malformed Worker reply"));
                detach();
              }
            },
            error(event) { event.preventDefault?.(); fail(new Error(event.message || "Computation Worker failed. Retry the action.")); },
            messageerror() { fail(new Error("Computation Worker message could not be read. Retry the action.")); },
          };
          for (const [name, handler] of Object.entries(listeners)) worker.addEventListener(name, handler);
          startupTimer = setTimeout(() => fail(new Error("Worker startup timed out")), startupTimeout);
        });
        if (startGeneration !== generation) throw new CancelledError();
        catalogue = await send({ type: "initialize" });
      } catch (error) {
        if (isCancelled(error)) throw error;
        if (startGeneration !== generation) throw error;
        detach();
        if (!setupFailure) throw error;
        onCompatibility("Compatibility mode: calculations run on this page because a Worker could not start.");
        const fallback = await directFactory();
        if (startGeneration !== generation) throw new CancelledError();
        direct = fallback.service;
        catalogue = { ...await direct.request({ type: "catalogue" }), days: fallback.days };
      } finally {
        if (startGeneration === generation) { starting = null; rejectStartup = null; }
      }
    })();
    return starting;
  }

  async function call(message) {
    if (direct) return direct.request(message);
    return send(message);
  }

  async function request(message) {
    if (active) throw new Error("Computation already active");
    const ownedGeneration = generation;
    active = true;
    try {
      await start();
      if (ownedGeneration !== generation) throw new CancelledError();
      if (message.type === "initialize" || message.type === "catalogue") return structuredClone(catalogue);
      if (message.type === "analyse-manual" && manualNeedsReset) {
        await call({ type: "reset-manual" });
        if (ownedGeneration !== generation) throw new CancelledError();
        manualNeedsReset = false;
      }
      if (message.type === "advance-daily" && !restoredDaily) {
        if (!daily) throw new Error("No daily puzzle loaded");
        await call(daily.continuation ? { type: "restore-daily", ...daily }
          : { type: "load-day", day: daily.day });
        if (ownedGeneration !== generation) throw new CancelledError();
        restoredDaily = true;
      }
      if (message.type === "replay-manual" && !restoredManual) {
        if (!manual) throw new Error("No accepted manual input");
        await call({ type: "restore-manual", ...manual });
        if (ownedGeneration !== generation) throw new CancelledError();
        restoredManual = true;
        manualNeedsReset = false;
      }
      if (ownedGeneration !== generation) throw new CancelledError();
      return await call(message);
    } finally {
      if (ownedGeneration === generation) active = false;
    }
  }

  return {
    request,
    cancel() {
      if (active || starting) invalidate();
      else generation++;
    },
    acceptDaily(position) { daily = structuredClone(position); restoredDaily = !!position?.continuation; },
    acceptManual(input) { manual = structuredClone(input); restoredManual = true; },
    resetDaily(day) { daily = day == null ? null : { day, continuation: null }; restoredDaily = false; },
    resetManual() { manual = null; restoredManual = false; manualNeedsReset = true; },
    getTiming: () => direct?.getTiming?.() ?? lastTiming,
    dispose() { disposed = true; invalidate(); direct = null; daily = manual = catalogue = null; },
  };
}

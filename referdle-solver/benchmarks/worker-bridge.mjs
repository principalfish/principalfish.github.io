import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { Worker } from "node:worker_threads";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";

const componentDir = fileURLToPath(new URL("../", import.meta.url));
const solverDir = path.join(componentDir, "js/solver");

// Node transport only; the browser client and shared handler remain production code.
export async function createNativeWorkerFactory({ dataDir = path.join(componentDir, "data"), replacements = {} } = {}) {
  const output = await build({ stdin: { contents: `
import { parentPort } from "node:worker_threads";
import { createLocalFetch } from ${JSON.stringify(path.join(componentDir, "benchmarks/local-fetch.mjs"))};
import { createContract } from ${JSON.stringify(path.join(componentDir, "benchmarks/contract.mjs"))};
import { validateSampleAnswers } from ${JSON.stringify(path.join(componentDir, "benchmarks/sample.mjs"))};
import { loadAssets, dailyGame } from ${JSON.stringify(path.join(solverDir, "data.js"))};
import { STRATEGY } from ${JSON.stringify(path.join(solverDir, "strategy.js"))};
import { createWorkerRuntime } from ${JSON.stringify(path.join(solverDir, "worker-runtime.js"))};
globalThis.fetch = createLocalFetch(${JSON.stringify(dataDir)}, globalThis.fetch);
let assets = null;
let assetLoads = 0;
const runtime = createWorkerRuntime(message => parentPort.postMessage(message), {
  load: async progress => { assetLoads++; assets = await loadAssets(progress); return assets; },
});
parentPort.on("message", async envelope => {
  if (envelope?.type !== "benchmark:contract") { runtime.receive(envelope); return; }
  try {
    if (!assets) throw new Error("Worker-owned assets are unavailable; initialize first");
    await validateSampleAnswers(envelope.sample, day => dailyGame(assets, day));
    for (const game of envelope.sample.games) {
      if (!game.answers.every(word => assets.poolIndex.has(word))) throw new Error("Daily #" + game.day + " has an answer outside the pool");
    }
    const contract = createContract({ sample: envelope.sample, state: assets, strategy: STRATEGY });
    parentPort.postMessage({ type: "benchmark:contract-result", serial: envelope.serial, contract,
      ownership: { assetLoads, decodedMatrixBytes: assets.PM.byteLength, matrixStorage: assets.PM.constructor.name } });
  } catch (error) {
    parentPort.postMessage({ type: "benchmark:contract-result", serial: envelope.serial, error: error.message });
  }
});
parentPort.postMessage({ type: "ready" });`, resolveDir: solverDir },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    define: { "import.meta.url": JSON.stringify(pathToFileURL(path.join(solverDir, "data.js")).href) },
    plugins: [{ name: "bounded-worker-fixtures", setup(builder) {
      builder.onLoad({ filter: /[/\\][^/\\]+\.js$/ }, async args => {
        const contents = replacements[path.basename(args.path)];
        return contents === undefined ? undefined : { contents, resolveDir: solverDir };
      });
    } }],
  });
  const url = new URL(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}`);
  const workers = [];
  let serial = 0;
  function factory() {
    const native = new Worker(url, { execArgv: [] });
    const listeners = new Map();
    let termination = null;
    const adapter = {
      native,
      postMessage: message => native.postMessage(message),
      addEventListener(name, handler) {
        const wrapped = name === "message" ? data => {
          if (data?.type !== "benchmark:contract-result") handler({ data });
        } : name === "error" ? error => handler({ message: error.message }) : handler;
        const exited = name === "error" ? code => {
          if (!termination) handler({ message: `Native computation Worker exited unexpectedly (${code})` });
        } : null;
        listeners.set(handler, { name, wrapped, exited });
        native.on(name, wrapped);
        if (exited) native.on("exit", exited);
      },
      removeEventListener(name, handler) {
        const entry = listeners.get(handler);
        if (entry) {
          native.off(name, entry.wrapped);
          if (entry.exited) native.off("exit", entry.exited);
        }
        listeners.delete(handler);
      },
      terminate() {
        for (const [handler, entry] of listeners) {
          native.off(entry.name, entry.wrapped);
          if (entry.exited) native.off("exit", entry.exited);
          listeners.delete(handler);
        }
        termination ??= native.terminate();
        return termination;
      },
    };
    workers.push(adapter);
    return adapter;
  }
  async function contract(worker, sample) {
    const requestSerial = ++serial;
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timer);
        worker.native.off("message", receive);
        worker.native.off("error", fail);
        worker.native.off("exit", exited);
      };
      const fail = error => { cleanup(); reject(error); };
      const exited = () => fail(new Error("Worker exited before returning benchmark metadata"));
      const receive = message => {
        if (message?.type !== "benchmark:contract-result" || message.serial !== requestSerial) return;
        cleanup();
        if (message.error) reject(new Error(message.error));
        else resolve({ contract: message.contract, ownership: message.ownership });
      };
      const timer = setTimeout(() => fail(new Error("Worker benchmark metadata timed out")), 10000);
      worker.native.on("message", receive);
      worker.native.on("error", fail);
      worker.native.on("exit", exited);
      try { worker.native.postMessage({ type: "benchmark:contract", serial: requestSerial, sample }); }
      catch (error) { fail(error); }
    });
  }
  return { factory, workers, contract, dispose: () => Promise.all(workers.map(worker => worker.terminate())) };
}

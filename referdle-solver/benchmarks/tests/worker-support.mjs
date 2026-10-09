import path from "node:path";
import { pathToFileURL } from "node:url";
import { Worker } from "node:worker_threads";
import { build } from "esbuild";
import { COMPONENT_DIR, DATA_DIR } from "../harness.mjs";

const solverDir = path.join(COMPONENT_DIR, "js/solver");
export async function workerModules() {
  const output = await build({ stdin: { contents: `
export { createWorkerClient, CancelledError } from ${JSON.stringify(path.join(solverDir, "worker-client.js"))};
export { createWorkerRuntime } from ${JSON.stringify(path.join(solverDir, "worker-runtime.js"))};`, resolveDir: solverDir },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    define: { "import.meta.url": JSON.stringify(pathToFileURL(path.join(solverDir, "data.js")).href) } });
  return import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}#${crypto.randomUUID()}`);
}

// A bounded-test bridge to the production handler, with fetch confined to data/.
export async function createNativeWorkerFactory({ dataDir = DATA_DIR } = {}) {
  const output = await build({ stdin: { contents: `
import { parentPort } from "node:worker_threads";
import { createLocalFetch } from ${JSON.stringify(path.join(COMPONENT_DIR, "benchmarks/local-fetch.mjs"))};
import { createWorkerRuntime } from ${JSON.stringify(path.join(solverDir, "worker-runtime.js"))};
globalThis.fetch = createLocalFetch(${JSON.stringify(dataDir)}, globalThis.fetch);
const runtime = createWorkerRuntime(message => parentPort.postMessage(message));
parentPort.on("message", message => runtime.receive(message));
parentPort.postMessage({ type: "ready" });`, resolveDir: solverDir },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    define: { "import.meta.url": JSON.stringify(pathToFileURL(path.join(solverDir, "data.js")).href) } });
  const url = new URL(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}`);
  const workers = [];
  function factory() {
    const native = new Worker(url, { execArgv: [] });
    const listeners = new Map();
    const adapter = {
      native,
      postMessage: (message) => native.postMessage(message),
      addEventListener(name, handler) {
        const wrapped = name === "message" ? (data) => handler({ data })
          : name === "error" ? (error) => handler({ message: error.message }) : handler;
        listeners.set(handler, wrapped);
        native.on(name, wrapped);
      },
      removeEventListener(name, handler) { native.off(name, listeners.get(handler)); listeners.delete(handler); },
      terminate() { return native.terminate(); },
    };
    workers.push(adapter);
    return adapter;
  }
  return { factory, workers, dispose: () => Promise.all(workers.map((worker) => worker.terminate())) };
}

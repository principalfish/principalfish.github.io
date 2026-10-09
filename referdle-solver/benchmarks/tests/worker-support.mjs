import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { COMPONENT_DIR } from "../harness.mjs";

const solverDir = path.join(COMPONENT_DIR, "js/solver");
export async function workerModules() {
  const output = await build({ stdin: { contents: `
export { createWorkerClient, CancelledError } from ${JSON.stringify(path.join(solverDir, "worker-client.js"))};
export { createWorkerRuntime } from ${JSON.stringify(path.join(solverDir, "worker-runtime.js"))};`, resolveDir: solverDir },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    define: { "import.meta.url": JSON.stringify(pathToFileURL(path.join(solverDir, "data.js")).href) } });
  return import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}#${crypto.randomUUID()}`);
}

export { createNativeWorkerFactory } from "../worker-bridge.mjs";

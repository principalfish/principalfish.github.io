import { createWorkerRuntime } from "./worker-runtime.js";

const runtime = createWorkerRuntime((message) => postMessage(message));
addEventListener("message", (event) => runtime.receive(event.data));
postMessage({ type: "ready" });

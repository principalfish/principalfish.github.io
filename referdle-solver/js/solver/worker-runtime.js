// Transport-independent worker handler. Assets and computation stay in its scope.
import { loadAssets, testableDays } from "./data.js";
import { createComputeService } from "./compute-service.js";

export function createWorkerRuntime(send, { load = loadAssets, createService = createComputeService,
  listDays = testableDays, now = () => performance.now() } = {}) {
  let service = null;
  let active = false;
  let initialized = false;

  async function receive(envelope) {
    const { id, generation, message } = envelope || {};
    if (envelope?.type !== "request" || !Number.isSafeInteger(id) || !Number.isSafeInteger(generation)
        || typeof message?.type !== "string") return;
    const reply = (fields) => send({ id, generation, ...fields });
    if (active) { reply({ type: "failure", kind: "compute", error: "Computation already active" }); return; }
    active = true;
    try {
      if (!service) {
        try {
          const assets = await load((text) => reply({ type: "progress", text }));
          service = createService(assets);
          const catalogue = service.request({ type: "catalogue" });
          catalogue.days = await listDays(assets);
          initialized = true;
          if (message.type === "initialize") {
            reply({ type: "result", result: catalogue, computeMs: 0 });
            return;
          }
        } catch (error) {
          reply({ type: "failure", kind: "asset", error: error.message });
          return;
        }
      }
      const start = now();
      const value = service.request(message);
      const computeMs = now() - start;
      const result = await value;
      reply({ type: "result", result, computeMs });
    } catch (error) {
      reply({ type: "failure", kind: initialized ? "compute" : "asset", error: error.message });
    } finally {
      active = false;
    }
  }

  return { receive };
}

import { createHash } from "node:crypto";
import { encodeTrace, TRACE_SCHEMA_VERSION } from "./trace.mjs";

export const BASELINE_SCHEMA_VERSION = 1;

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function createContract({ sample, state, strategy }) {
  const { N, PM } = state;
  if (!Number.isInteger(N) || N < 1 || !PM || PM.length !== N * N || state.POOL.length !== N) {
    throw new Error("Invalid logical matrix dimensions");
  }
  const canonicalMatrix = PM instanceof Uint8Array ? PM : Buffer.allocUnsafe(PM.length);
  for (let i = 0; i < PM.length; i++) {
    if (!Number.isInteger(PM[i]) || PM[i] < 0 || PM[i] > 242) throw new Error(`Invalid matrix pattern code at index ${i}`);
    if (canonicalMatrix !== PM) canonicalMatrix[i] = PM[i];
  }
  return {
    schemaVersion: BASELINE_SCHEMA_VERSION,
    traceSchemaVersion: TRACE_SCHEMA_VERSION,
    sampleHash: sha256(encodeTrace(sample)),
    dayAnswersHash: sha256(encodeTrace(sample.games)),
    wordLists: Object.fromEntries(["POOL", "EXPANDED", "ALL_GUESSES", "PLURALS"].map((key) =>
      [key, sha256(encodeTrace(Array.from(state[key])))])),
    matrix: { dimension: N, patternsHash: sha256(canonicalMatrix) },
    strategy,
    probeSettings: [false, true],
  };
}

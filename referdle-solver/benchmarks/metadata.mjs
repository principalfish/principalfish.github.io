import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import os from "node:os";
import path from "node:path";
import { COMPONENT_DIR, DATA_DIR } from "./harness.mjs";
import { compareTraces, encodeTrace, TRACE_SCHEMA_VERSION } from "./trace.mjs";

const execFileAsync = promisify(execFile);
export const BASELINE_SCHEMA_VERSION = 1;

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function createContract({ sample, state, strategy }) {
  const { N, PM } = state;
  if (!Number.isInteger(N) || N < 1 || !PM || PM.length !== N * N || state.POOL.length !== N) {
    throw new Error("Invalid logical matrix dimensions");
  }
  const canonicalMatrix = Buffer.allocUnsafe(PM.length);
  for (let i = 0; i < PM.length; i++) {
    if (!Number.isInteger(PM[i]) || PM[i] < 0 || PM[i] > 242) throw new Error(`Invalid matrix pattern code at index ${i}`);
    canonicalMatrix[i] = PM[i];
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

export function assertCompatible(expected, current) {
  const result = compareTraces(expected, current);
  if (!result.equal) {
    throw new Error(`Reference contract changed at ${result.path}: expected ${result.expected}, actual ${result.actual}. Record a new reference intentionally.`);
  }
}

async function hashDirectory(directory, prefix, suffix) {
  const names = (await readdir(directory)).filter((name) => !suffix || name.endsWith(suffix)).sort();
  return Object.fromEntries(await Promise.all(names.map(async (name) =>
    [`${prefix}/${name}`, sha256(await readFile(path.join(directory, name)))])));
}

export async function collectProvenance() {
  const repoDir = path.dirname(COMPONENT_DIR);
  const [assets, solverSources, sharedSources, harnessSources, git] = await Promise.all([
    hashDirectory(DATA_DIR, "referdle-solver/data"),
    hashDirectory(path.join(COMPONENT_DIR, "js/solver"), "referdle-solver/js/solver", ".js"),
    hashDirectory(path.join(COMPONENT_DIR, "js"), "referdle-solver/js", ".js"),
    hashDirectory(path.join(COMPONENT_DIR, "benchmarks"), "referdle-solver/benchmarks", ".mjs"),
    Promise.all([
      execFileAsync("git", ["rev-parse", "HEAD"], { cwd: repoDir }),
      execFileAsync("git", ["status", "--porcelain"], { cwd: repoDir }),
    ]).then(([revision, status]) => ({ revision: revision.stdout.trim(), dirty: Boolean(status.stdout.trim()) }))
      .catch(() => ({ revision: null, dirty: null })),
  ]);
  const cpus = os.cpus();
  return {
    node: process.version,
    platform: os.platform(), architecture: os.arch(), release: os.release(),
    cpu: { model: cpus[0]?.model ?? null, logicalCount: cpus.length },
    git, rawAssetHashes: assets, sourceHashes: { ...sharedSources, ...solverSources, ...harnessSources },
  };
}

export function timingNotes(baseline, current) {
  const fields = ["node", "platform", "architecture", "release", "cpu"];
  const changed = fields.filter((field) => encodeTrace(baseline[field]) !== encodeTrace(current[field]));
  return changed.length ? [`Timing environment differs: ${changed.join(", ")}. Correctness comparison remains valid.`] : [];
}

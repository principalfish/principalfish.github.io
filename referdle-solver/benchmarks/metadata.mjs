import { readFile, readdir, stat } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import os from "node:os";
import path from "node:path";
import { COMPONENT_DIR, DATA_DIR } from "./harness.mjs";
import { compareTraces, encodeTrace } from "./trace.mjs";

const execFileAsync = promisify(execFile);
export { BASELINE_SCHEMA_VERSION, createContract, sha256 } from "./contract.mjs";
import { sha256 } from "./contract.mjs";

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

async function directoryBytes(directory, suffix) {
  const names = (await readdir(directory)).filter(name => !suffix || name.endsWith(suffix));
  const entries = await Promise.all(names.map(name => stat(path.join(directory, name))));
  return entries.filter(entry => entry.isFile()).reduce((sum, entry) => sum + entry.size, 0);
}

export async function collectRuntimeFootprint() {
  const [dataBytes, solverJS, sharedJS, cssBytes, indexHTML, manifest] = await Promise.all([
    directoryBytes(DATA_DIR), directoryBytes(path.join(COMPONENT_DIR, "js/solver"), ".js"),
    directoryBytes(path.join(COMPONENT_DIR, "js"), ".js"), directoryBytes(path.join(COMPONENT_DIR, "css"), ".css"),
    stat(path.join(COMPONENT_DIR, "index.html")), readFile(path.join(DATA_DIR, "manifest.json"), "utf8").then(JSON.parse),
  ]);
  const javascriptBytes = solverJS + sharedJS;
  const htmlCssBytes = indexHTML.size + cssBytes;
  const matrixGzipBytes = (await stat(path.join(DATA_DIR, manifest.matrix_file))).size;
  return { dataBytes, javascriptBytes, htmlCssBytes, totalBytes: dataBytes + javascriptBytes + htmlCssBytes,
    matrixGzipBytes, decodedMatrixBytes: manifest.decoded_bytes,
    scope: "Unminified component index HTML, CSS, JavaScript and all shipped data files; excludes benchmarks/references, stats page and shared site assets. File bytes, not an initial network-transfer measurement." };
}

export async function collectProvenance() {
  const repoDir = path.dirname(COMPONENT_DIR);
  const [assets, solverSources, sharedSources, harnessSources, git, runtimeFootprint] = await Promise.all([
    hashDirectory(DATA_DIR, "referdle-solver/data"),
    hashDirectory(path.join(COMPONENT_DIR, "js/solver"), "referdle-solver/js/solver", ".js"),
    hashDirectory(path.join(COMPONENT_DIR, "js"), "referdle-solver/js", ".js"),
    hashDirectory(path.join(COMPONENT_DIR, "benchmarks"), "referdle-solver/benchmarks", ".mjs"),
    Promise.all([
      execFileAsync("git", ["rev-parse", "HEAD"], { cwd: repoDir }),
      execFileAsync("git", ["status", "--porcelain"], { cwd: repoDir }),
    ]).then(([revision, status]) => ({ revision: revision.stdout.trim(), dirty: Boolean(status.stdout.trim()) }))
      .catch(() => ({ revision: null, dirty: null })),
    collectRuntimeFootprint(),
  ]);
  const cpus = os.cpus();
  return {
    node: process.version,
    platform: os.platform(), architecture: os.arch(), release: os.release(),
    cpu: { model: cpus[0]?.model ?? null, logicalCount: cpus.length },
    git, runtimeFootprint, rawAssetHashes: assets, sourceHashes: { ...sharedSources, ...solverSources, ...harnessSources },
  };
}

export function timingNotes(baseline, current) {
  const fields = ["node", "platform", "architecture", "release", "cpu"];
  const changed = fields.filter((field) => encodeTrace(baseline[field]) !== encodeTrace(current[field]));
  return changed.length ? [`Timing environment differs: ${changed.join(", ")}. Correctness comparison remains valid.`] : [];
}

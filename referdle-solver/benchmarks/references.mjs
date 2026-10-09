import { mkdir, mkdtemp, open, readFile, realpath, rename, rm, stat, lstat, writeFile, readdir } from "node:fs/promises";
import { gzipSync, gunzipSync } from "node:zlib";
import path from "node:path";
import { COMPONENT_DIR } from "./harness.mjs";
import { decodeTrace, encodeTrace, TRACE_SCHEMA_VERSION } from "./trace.mjs";
import { BASELINE_SCHEMA_VERSION, sha256 } from "./metadata.mjs";

export const DEFAULT_BASELINE_DIR = path.join(COMPONENT_DIR, "benchmarks/references");

export function caseKey(day, expanded) {
  return `${expanded ? "expanded" : "pool"}/${day}`;
}

function caseFile(day, expanded) {
  if (!Number.isInteger(day) || day < 1000 || day > 1414 || typeof expanded !== "boolean") throw new Error("Invalid reference case identity");
  return `${caseKey(day, expanded)}.json.gz`;
}

function lockPath(directory) {
  return path.join(path.dirname(directory), `.${path.basename(directory)}.lock`);
}

async function exists(filename) {
  try { await lstat(filename); return true; } catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

function within(candidate, directory) {
  const relative = path.relative(directory, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

async function canonicalPath(filename) {
  try { return await realpath(filename); } catch (error) {
    if (error.code !== "ENOENT") throw error;
    const parent = path.dirname(filename);
    if (parent === filename) throw error;
    return path.join(await canonicalPath(parent), path.basename(filename));
  }
}

export async function assertSafePaths(baselineDir, reportFile) {
  const repo = await realpath(path.dirname(COMPONENT_DIR));
  const baseline = await canonicalPath(path.resolve(baselineDir));
  const defaultBaseline = await canonicalPath(DEFAULT_BASELINE_DIR);
  const isDefault = path.resolve(baselineDir) === DEFAULT_BASELINE_DIR;
  if ((within(baseline, repo) && (!isDefault || baseline !== defaultBaseline)) || within(repo, baseline)) {
    throw new Error("Baseline directory must be the default references directory or outside the repository, and cannot contain the repository");
  }
  if (reportFile) {
    const report = await canonicalPath(path.resolve(reportFile));
    if (within(report, repo) || within(report, baseline) || within(baseline, report)) {
      throw new Error("Report file must be outside the repository and must not overlap the baseline");
    }
  }
}

export function validateInventory(manifest, timings) {
  if (manifest?.schemaVersion !== BASELINE_SCHEMA_VERSION || manifest.traceSchemaVersion !== TRACE_SCHEMA_VERSION
      || !manifest.contract || !manifest.provenance || !Array.isArray(manifest.days) || !manifest.days.length
      || !Array.isArray(manifest.cases) || timings?.schemaVersion !== BASELINE_SCHEMA_VERSION || !Array.isArray(timings.cases)) {
    throw new Error("Invalid reference manifest or timing baseline");
  }
  if (manifest.days.some((day, i) => !Number.isInteger(day) || day < 1000 || day > 1414 || (i && day <= manifest.days[i - 1]))) {
    throw new Error("Invalid reference day selection");
  }
  const expected = [false, true].flatMap((expanded) => manifest.days.map((day) => ({ day, expanded })));
  if (manifest.cases.length !== expected.length || timings.cases.length !== expected.length) throw new Error("Incomplete reference inventory");
  expected.forEach(({ day, expanded }, i) => {
    const entry = manifest.cases[i], timing = timings.cases[i];
    if (entry.day !== day || entry.expanded !== expanded || !/^[a-f0-9]{64}$/.test(entry.encodedHash)
        || !Number.isInteger(entry.bytes) || entry.bytes < 1 || timing.day !== day || timing.expanded !== expanded
        || !Number.isFinite(timing.computeMs) || timing.computeMs < 0) throw new Error(`Invalid reference inventory at case ${i}`);
  });
}

export async function readBaseline(directory, { checkLock = true } = {}) {
  if (checkLock && await exists(lockPath(directory))) throw new Error("Reference writer is active (or its lock needs manual removal after interruption)");
  try {
    const [manifestText, timingText] = await Promise.all([
      readFile(path.join(directory, "reference-manifest.json"), "utf8"),
      readFile(path.join(directory, "timing-baseline.json"), "utf8"),
    ]);
    const manifest = JSON.parse(manifestText), timings = JSON.parse(timingText);
    validateInventory(manifest, timings);
    return { manifest, timings };
  } catch (error) {
    throw new Error(`Missing or corrupt reference baseline: ${error.message}`, { cause: error });
  }
}

export async function readCase(directory, entry) {
  try {
    const encoded = gunzipSync(await readFile(path.join(directory, caseFile(entry.day, entry.expanded)))).toString("utf8");
    if (sha256(encoded) !== entry.encodedHash) throw new Error("Trace checksum differs from manifest");
    const trace = decodeTrace(encoded);
    if (trace.schemaVersion !== TRACE_SCHEMA_VERSION || trace.day !== entry.day || trace.expanded !== entry.expanded) throw new Error("Trace identity or schema differs");
    return trace;
  } catch (error) {
    throw new Error(`Missing or corrupt reference ${caseKey(entry.day, entry.expanded)}: ${error.message}`, { cause: error });
  }
}

export async function writeCase(directory, trace) {
  const filename = path.join(directory, caseFile(trace.day, trace.expanded));
  await mkdir(path.dirname(filename), { recursive: true });
  const encoded = encodeTrace(trace);
  const compressed = gzipSync(encoded);
  await writeFile(filename, compressed, { flag: "wx" });
  return { day: trace.day, expanded: trace.expanded, encodedHash: sha256(encoded), bytes: compressed.length };
}

export async function artifactBytes(directory) {
  let bytes = 0;
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) bytes += await artifactBytes(filename);
    else bytes += (await stat(filename)).size;
  }
  return bytes;
}

async function assertBaselineLayout(directory, manifest) {
  const names = (await readdir(directory)).sort();
  const allowed = ["expanded", "pool", "reference-manifest.json", "timing-baseline.json"];
  if (JSON.stringify(names) !== JSON.stringify(allowed)) throw new Error("Reference directory contains unrelated files; refusing replacement");
  for (const expanded of [false, true]) {
    const subdir = path.join(directory, expanded ? "expanded" : "pool");
    if (!(await lstat(subdir)).isDirectory()) throw new Error("Reference case directory is not a plain directory");
    const expected = manifest.cases.filter((entry) => entry.expanded === expanded).map((entry) => `${entry.day}.json.gz`).sort();
    if (JSON.stringify((await readdir(subdir)).sort()) !== JSON.stringify(expected)) throw new Error("Reference case inventory contains missing or unrelated files");
    for (const filename of expected) if (!(await lstat(path.join(subdir, filename))).isFile()) throw new Error("Reference case must be a plain file");
  }
}

export async function beginRecording(directory, { overwrite = false } = {}) {
  directory = path.resolve(directory);
  const parent = path.dirname(directory);
  await mkdir(parent, { recursive: true });
  let lock;
  try { lock = await open(lockPath(directory), "wx"); } catch (error) {
    if (error.code === "EEXIST") throw new Error("Another reference writer owns this directory (remove a stale lock manually after checking)");
    throw error;
  }
  let staging;
  let finished = false;
  try {
    await lock.writeFile(JSON.stringify({ pid: process.pid }));
    if (await exists(directory)) {
      if (!overwrite) throw new Error("Reference already exists; replacement requires --record --overwrite");
      if (!(await lstat(directory)).isDirectory()) throw new Error("Reference target must be a directory, not a symlink");
      // Even explicit overwrite cannot replace arbitrary unrelated files/directories.
      const { manifest } = await readBaseline(directory, { checkLock: false });
      await assertBaselineLayout(directory, manifest);
    }
    staging = await mkdtemp(path.join(parent, `.${path.basename(directory)}.staging-`));
  } catch (error) {
    await lock.close(); await rm(lockPath(directory), { force: true });
    throw error;
  }
  async function cleanup() {
    if (finished) return;
    finished = true;
    await rm(staging, { recursive: true, force: true });
    await lock.close();
    await rm(lockPath(directory), { force: true });
  }
  return {
    staging,
    async publish(manifest, timings, { renameDirectory = rename } = {}) {
      if (finished) throw new Error("Recording transaction already finished");
      validateInventory(manifest, timings);
      await writeFile(path.join(staging, "reference-manifest.json"), JSON.stringify(manifest, null, 2) + "\n", { flag: "wx" });
      await writeFile(path.join(staging, "timing-baseline.json"), JSON.stringify(timings, null, 2) + "\n", { flag: "wx" });
      const backup = `${staging}.previous`;
      let backedUp = false;
      try {
        if (await exists(directory)) { await renameDirectory(directory, backup); backedUp = true; }
        try { await renameDirectory(staging, directory); } catch (error) {
          if (backedUp) await renameDirectory(backup, directory);
          throw error;
        }
        if (backedUp) await rm(backup, { recursive: true });
      } finally {
        await cleanup();
      }
    },
    abort: cleanup,
  };
}

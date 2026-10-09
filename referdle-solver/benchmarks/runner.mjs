import { writeFile, mkdtemp } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";
import os from "node:os";
import path from "node:path";
import { createHarness, loadSample } from "./harness.mjs";
import { compareTraces, TRACE_SCHEMA_VERSION } from "./trace.mjs";
import { assertCompatible, BASELINE_SCHEMA_VERSION, collectProvenance, createContract, timingNotes } from "./metadata.mjs";
import { artifactBytes, assertSafePaths, beginRecording, caseKey, DEFAULT_BASELINE_DIR, readBaseline, readCase, writeCase } from "./references.mjs";
import { caseRow, RUN_ORDER, summarize, TIMING_BOUNDARIES } from "./reporting.mjs";

export const HELP = `Usage: npm run benchmark:referdle -- [options]
  --record                 Record explicitly; verification is the default
  --overwrite              Replace an existing valid reference (requires --record)
  --backend worker|direct  Native Worker is the default; direct is for focused compatibility
  --days 1000,1414          Select only days from the frozen sample; both modes run
  --baseline-dir DIRECTORY Isolate smoke references outside the repository
  --report FILE.json       Also write JSON outside the repository and baseline
  --help                   Show this help
Requires Node.js 22 or later. A partial recording requires an external baseline directory.
Progress goes to stderr; the JSON report goes to stdout.`;

export function parseArgs(args) {
  const options = { record: false, overwrite: false, days: null, baselineDir: DEFAULT_BASELINE_DIR, reportFile: null, backend: "worker", help: false };
  const seen = new Set();
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (seen.has(arg)) throw new Error(`Duplicate option: ${arg}`);
    seen.add(arg);
    if (arg === "--record") options.record = true;
    else if (arg === "--overwrite") options.overwrite = true;
    else if (arg === "--help") options.help = true;
    else if (["--days", "--baseline-dir", "--report", "--backend"].includes(arg)) {
      const value = args[++i];
      if (!value || value.startsWith("--")) throw new Error(`Missing value for ${arg}`);
      if (arg === "--backend") {
        if (!["worker", "direct"].includes(value)) throw new Error("--backend must be worker or direct");
        options.backend = value;
      } else if (arg === "--days") {
        if (!/^\d+(,\d+)*$/.test(value)) throw new Error("--days must contain comma-separated integers");
        options.days = value.split(",").map(Number);
        if (new Set(options.days).size !== options.days.length) throw new Error("--days must not contain duplicate days");
      } else if (arg === "--baseline-dir") options.baselineDir = path.resolve(value);
      else options.reportFile = path.resolve(value);
    } else throw new Error(`Unknown option: ${arg}`);
  }
  if (options.overwrite && !options.record) throw new Error("--overwrite requires --record");
  return options;
}

export function selectDays(sample, requested) {
  if (!requested) return sample.games.map((game) => game.day);
  for (const day of requested) {
    if (!sample.games.some((game) => game.day === day)) throw new Error(`Day ${day} is outside the frozen sample`);
  }
  return sample.games.filter((game) => requested.includes(game.day)).map((game) => game.day);
}

async function executeCase({ harness, day, expanded, options, baseline, transaction }) {
  const entry = baseline?.manifest.cases.find((entry) => entry.day === day && entry.expanded === expanded);
  const baselineMs = baseline?.timings.cases.find((entry) => entry.day === day && entry.expanded === expanded)?.computeMs ?? null;
  let result;
  try {
    // Expected decoding and all actual trace work happen outside the harness timers.
    const expected = options.record ? null : await readCase(options.baselineDir, entry);
    result = await harness.runGame(day, expanded);
    const incomplete = result.trace.final.status !== "solved";
    const difference = expected ? compareTraces(expected, result.trace) : { equal: true };
    let evidenceFile;
    if (incomplete) {
      const evidenceDir = await mkdtemp(path.join(os.tmpdir(), "referdle-incomplete-"));
      await writeCase(evidenceDir, result.trace);
      evidenceFile = path.join(evidenceDir, `${caseKey(day, expanded)}.json.gz`);
    }
    const status = incomplete ? "incomplete" : !difference.equal ? "mismatch" : options.record ? "recorded" : "matched";
    const inventory = options.record && !incomplete ? await writeCase(transaction.staging, result.trace) : null;
    return {
      inventory,
      row: caseRow({
        day, expanded, baselineMs, computeMs: result.computeMs, moves: result.trace.moves.length, turnMs: result.turnMs, roundtripMs: result.roundtripMs, turnRoundtripMs: result.turnRoundtripMs, status,
        ...(incomplete ? { error: `Actual terminal state is incomplete; compressed trace evidence: ${evidenceFile}` } : {}),
        ...(!difference.equal ? { difference } : {}),
      }),
    };
  } catch (error) {
    return { inventory: null, row: caseRow({ day, expanded, baselineMs, status: "error", error: error.message,
      ...(result ? { computeMs: result.computeMs, moves: result.trace.moves.length, turnMs: result.turnMs, roundtripMs: result.roundtripMs, turnRoundtripMs: result.turnRoundtripMs } : {}),
    }) };
  }
}

export async function runBenchmark(options, dependencies = {}) {
  const now = dependencies.now ?? (() => performance.now());
  const progress = dependencies.progress ?? ((message) => process.stderr.write(`${message}\n`));
  const started = now();
  const report = {
    schemaVersion: 1, backend: options.backend ?? "worker", action: options.record ? "record" : "verify", valid: false,
    timingBoundaries: TIMING_BOUNDARIES, order: RUN_ORDER,
    selection: null, setupMs: null, elapsedMs: null, referenceBytes: null,
    notes: [], errors: [], cases: [], summary: summarize([]),
  };
  let transaction;
  let harness;
  try {
    await assertSafePaths(options.baselineDir, options.reportFile);
    const sample = await (dependencies.loadSample ?? loadSample)();
    const days = selectDays(sample, options.days);
    report.selection = { days, games: days.length, cases: days.length * 2, fullSample: days.length === sample.games.length };
    if (options.record && !report.selection.fullSample && path.resolve(options.baselineDir) === DEFAULT_BASELINE_DIR) {
      throw new Error("Partial recording requires --baseline-dir outside the repository");
    }
    const baseline = options.record ? null : await readBaseline(options.baselineDir);
    if (baseline) {
      for (const expanded of [false, true]) for (const day of days) {
        if (!baseline.manifest.cases.some((entry) => entry.day === day && entry.expanded === expanded)) throw new Error(`Reference is missing requested case ${caseKey(day, expanded)}`);
      }
    } else transaction = await beginRecording(options.baselineDir, { overwrite: options.overwrite });
    harness = await (dependencies.createHarness ?? createHarness)({ sample, onProgress: progress, backend: options.backend ?? "worker" });
    const contract = harness.contract ?? createContract(harness);
    if (harness.ownership) report.assetOwnership = harness.ownership;
    const provenance = await (dependencies.collectProvenance ?? collectProvenance)();
    report.provenance = provenance;
    if (baseline) {
      assertCompatible(baseline.manifest.contract, contract);
      report.notes.push(...timingNotes(baseline.manifest.provenance, provenance));
    }
    report.setupMs = now() - started;
    const inventory = [];
    for (const expanded of [false, true]) {
      for (const day of days) {
        const { row, inventory: entry } = await executeCase({ harness, day, expanded, options, baseline, transaction });
        report.cases.push(row);
        if (entry) inventory.push(entry);
        const timing = row.currentComputeMs === null ? "not timed" : `${row.currentComputeMs.toFixed(1)} ms compute`;
        progress(`[${report.cases.length}/${report.selection.cases}] ${caseKey(day, expanded)}: ${row.status}; ${timing}${row.error ? `; ${row.error}` : ""}`);
        if (row.difference) progress(`  Move ${row.difference.moveIndex ?? "terminal"}, ${row.difference.path}: expected ${row.difference.expected}; actual ${row.difference.actual}`);
      }
    }
    report.valid = report.cases.every((row) => ["matched", "recorded"].includes(row.status));
    if (options.record && report.valid) {
      const manifest = {
        schemaVersion: BASELINE_SCHEMA_VERSION, traceSchemaVersion: TRACE_SCHEMA_VERSION,
        contract, provenance, days, cases: inventory,
      };
      const timings = {
        schemaVersion: BASELINE_SCHEMA_VERSION,
        cases: report.cases.map(({ day, expanded, currentComputeMs, moves, turnMs }) => ({ day, expanded, computeMs: currentComputeMs, moves, turnMs })),
      };
      await transaction.publish(manifest, timings);
    }
    if (!options.record || report.valid) report.referenceBytes = await artifactBytes(options.baselineDir);
    if (!report.valid) report.notes.push("Correctness failed; timings are invalid for performance acceptance.");
  } catch (error) {
    report.valid = false;
    report.errors.push(error.message);
    report.notes.push("The run did not complete successfully; timings are invalid for performance acceptance.");
  } finally {
    try { await harness?.dispose?.(); } catch (error) {
      report.valid = false;
      report.errors.push(`Benchmark cleanup failed: ${error.message}`);
    }
    if (transaction) await transaction.abort();
    report.setupMs ??= now() - started;
    report.elapsedMs = now() - started;
    report.summary = summarize(report.cases);
  }
  return report;
}

export async function main(args = process.argv.slice(2), dependencies = {}) {
  const stdout = dependencies.stdout ?? ((text) => process.stdout.write(text));
  const stderr = dependencies.stderr ?? ((text) => process.stderr.write(text));
  try {
    const options = parseArgs(args);
    if (options.help) { stdout(`${HELP}\n`); return 0; }
    if (Number(process.versions.node.split(".")[0]) < 22) throw new Error("Referdle benchmark requires Node.js 22 or later");
    await assertSafePaths(options.baselineDir, options.reportFile);
    const report = await runBenchmark(options, { ...dependencies, progress: dependencies.progress ?? ((message) => stderr(`${message}\n`)) });
    const json = JSON.stringify(report, null, 2) + "\n";
    if (options.reportFile) await writeFile(options.reportFile, json);
    stdout(json);
    return report.valid ? 0 : 1;
  } catch (error) {
    stderr(`${error.message}\n`);
    return 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}

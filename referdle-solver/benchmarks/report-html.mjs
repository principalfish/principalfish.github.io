import { readFile, realpath, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { assertSafePaths, DEFAULT_BASELINE_DIR } from "./references.mjs";

const CASE_STATUSES = new Set(["matched", "recorded", "error", "mismatch", "incomplete"]);
const SUCCESS_STATUSES = new Set(["matched", "recorded"]);
const HELP = "Usage: npm run report:referdle -- INPUT.json --output OUTPUT.html\nPostprocess a saved benchmark JSON report without running the solver. Output must be outside the repository and baseline.";

function optionalNumber(value, label, { integer = false, max = Infinity } = {}) {
  if (value === null || value === undefined) return;
  if (!Number.isFinite(value) || value < 0 || value > max || (integer && !Number.isInteger(value))) throw new Error(`Invalid ${label}`);
}

function validateReport(report) {
  if (!report || report.schemaVersion !== 1 || !["record", "verify"].includes(report.action)
      || typeof report.valid !== "boolean" || !Array.isArray(report.cases) || report.cases.length > 200) throw new Error("Invalid benchmark report schema");
  for (const field of ["notes", "errors"]) {
    if (!Array.isArray(report[field]) || report[field].some((value) => typeof value !== "string")) throw new Error(`Invalid report ${field}`);
  }
  for (const field of ["timingBoundaries", "order"]) {
    if (report[field] !== undefined && typeof report[field] !== "string") throw new Error(`Invalid report ${field}`);
  }
  for (const field of ["setupMs", "elapsedMs", "referenceBytes"]) optionalNumber(report[field], field);
  if (report.selection !== null && report.selection !== undefined) {
    const selection = report.selection;
    if (!Array.isArray(selection.days) || typeof selection.fullSample !== "boolean"
        || !Number.isInteger(selection.games) || selection.games !== selection.days.length
        || selection.games < 1 || selection.games > 100 || selection.cases !== selection.games * 2
        || new Set(selection.days).size !== selection.days.length
        || selection.days.some((day) => !Number.isInteger(day) || day < 1000 || day > 1414)) throw new Error("Invalid report selection");
  }
  const seen = new Set();
  for (const row of report.cases) {
    if (!row || !Number.isInteger(row.day) || row.day < 1000 || row.day > 1414
        || typeof row.expanded !== "boolean" || !CASE_STATUSES.has(row.status)
        || (row.mode !== undefined && row.mode !== (row.expanded ? "expanded" : "pool"))
        || (row.error !== undefined && typeof row.error !== "string")) throw new Error("Invalid benchmark case row");
    if (report.selection && !report.selection.days.includes(row.day)) throw new Error("Case is outside the report selection");
    const key = `${row.day}/${row.expanded}`;
    if (seen.has(key)) throw new Error(`Duplicate benchmark case ${key}`);
    seen.add(key);
    optionalNumber(row.currentComputeMs, "current case time");
    optionalNumber(row.baselineComputeMs, "baseline case time");
    optionalNumber(row.moves, "played moves", { integer: true, max: 80 });
    if (report.action === "record" && row.baselineComputeMs != null) throw new Error("Record reports must not contain baseline timings");
  }
}

function mean(values) {
  if (!values.length) return null;
  return values.reduce((average, value, i) => average + (value - average) / (i + 1), 0);
}

function readable(value) {
  return Number(value.toPrecision(10)).toString();
}

export function createDurationBins(values) {
  const maximum = values.length ? Math.max(...values) : 0;
  let width = 1;
  if (maximum >= 10) width = Math.ceil(maximum / 10);
  else if (maximum > 0) {
    const target = maximum / 10;
    const magnitude = 10 ** Math.floor(Math.log10(target));
    const factor = [1, 2, 2.5, 5, 10].find((factor) => factor >= target / magnitude);
    width = factor * magnitude;
  }
  const count = Math.max(1, Math.ceil(maximum / width - 1e-10));
  return Array.from({ length: count }, (_, i) => ({
    lower: Number((i * width).toPrecision(12)), upper: Number(((i + 1) * width).toPrecision(12)),
    label: `${readable(i * width)}–${readable((i + 1) * width)}`,
    inclusiveUpper: i === count - 1,
  }));
}

export function histogramCounts(values, bins) {
  const counts = bins.map(() => 0);
  for (const value of values) {
    const i = bins.findIndex((bin) => value >= bin.lower && (value < bin.upper || (bin.inclusiveUpper && value <= bin.upper + Number.EPSILON * Math.max(1, bin.upper))));
    if (i < 0) throw new Error("Value is outside histogram bands");
    counts[i]++;
  }
  return counts;
}

function series(rows, durationBins, moveBins) {
  const current = rows.filter((row) => row.currentComputeMs != null).map((row) => row.currentComputeMs / 1000);
  const baseline = rows.filter((row) => row.baselineComputeMs != null).map((row) => row.baselineComputeMs / 1000);
  const paired = rows.filter((row) => row.currentComputeMs != null && row.baselineComputeMs != null);
  const moves = rows.filter((row) => row.moves != null).map((row) => row.moves);
  const pairedCurrentMean = mean(paired.map((row) => row.currentComputeMs / 1000));
  const pairedBaselineMean = mean(paired.map((row) => row.baselineComputeMs / 1000));
  const deltaSeconds = paired.length ? pairedCurrentMean - pairedBaselineMean : null;
  return {
    cases: rows.length, currentCount: current.length, baselineCount: baseline.length, pairedCount: paired.length,
    currentMean: mean(current), baselineMean: mean(baseline),
    pairedCurrentMean, pairedBaselineMean, deltaSeconds,
    deltaPercent: deltaSeconds !== null && pairedBaselineMean !== 0 ? deltaSeconds / pairedBaselineMean * 100 : null,
    movesCount: moves.length, movesMean: mean(moves),
    currentCounts: histogramCounts(current, durationBins), baselineCounts: histogramCounts(baseline, durationBins),
    moveCounts: moveBins.map((value) => moves.filter((moves) => moves === value).length),
    missingCurrent: rows.length - current.length, missingBaseline: rows.length - baseline.length, missingMoves: rows.length - moves.length,
  };
}

export function buildReportModel(report) {
  validateReport(report);
  const times = report.cases.flatMap((row) => [row.currentComputeMs, row.baselineComputeMs])
    .filter((value) => value != null).map((value) => value / 1000);
  const durationBins = createDurationBins(times);
  const moves = report.cases.filter((row) => row.moves != null).map((row) => row.moves);
  const moveBins = moves.length ? Array.from({ length: Math.max(...moves) - Math.min(...moves) + 1 }, (_, i) => Math.min(...moves) + i) : [];
  const modes = {
    pool: series(report.cases.filter((row) => !row.expanded), durationBins, moveBins),
    expanded: series(report.cases.filter((row) => row.expanded), durationBins, moveBins),
  };
  const aggregate = series(report.cases, durationBins, moveBins);
  const selectionComplete = report.selection && report.cases.length === report.selection.cases;
  const successful = report.valid && Boolean(selectionComplete) && report.cases.every((row) => SUCCESS_STATUSES.has(row.status)) && !report.errors.length;
  return {
    action: report.action, successful, fullSample: Boolean(report.selection?.fullSample && report.selection.games === 100),
    requestedCases: report.selection?.cases ?? null, aggregate, modes, durationBins, moveBins,
    durationCountMax: Math.max(1, ...Object.values(modes).flatMap((mode) => [...mode.currentCounts, ...mode.baselineCounts])),
    moveCountMax: Math.max(1, ...Object.values(modes).flatMap((mode) => mode.moveCounts)),
    hasBaseline: report.action === "verify" && aggregate.baselineCount > 0,
    setupMs: report.setupMs ?? null, elapsedMs: report.elapsedMs ?? null, referenceBytes: report.referenceBytes ?? null,
    timingBoundaries: report.timingBoundaries ?? "Computation timing only; consult the benchmark documentation for timing boundaries.",
    order: report.order ?? "Fixed sequential benchmark selection.",
    notes: report.notes,
    diagnostics: [...report.errors, ...report.cases.filter((row) => !SUCCESS_STATUSES.has(row.status)).map((row) =>
      `Daily #${row.day}, ${row.expanded ? "expanded" : "pool"}: ${row.status}${row.error ? ` — ${row.error}` : ""}${typeof row.difference?.path === "string" ? ` — first difference ${row.difference.path}` : ""}`)],
  };
}

export function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[char]);
}

function metric(value, suffix = "") {
  return value === null ? "Unavailable" : `${value.toFixed(2)}${suffix}`;
}

function chart({ title, labels, current, baseline, scaleMax, unit }) {
  if (!labels.length || !current.some((value) => value) && !(baseline?.some((value) => value))) return `<p class="empty">No measured ${escapeHTML(unit)} values available.</p>`;
  const width = 720, height = 300, left = 40, right = 12, top = 26, bottom = 60;
  const plotHeight = height - top - bottom;
  const columnWidth = (width - left - right) / labels.length;
  const barWidth = Math.min(36, columnWidth * (baseline ? 0.32 : 0.58));
  const ticks = [...new Set([0, Math.ceil(scaleMax / 2), scaleMax])];
  const grid = ticks.map((value) => {
    const y = top + plotHeight * (1 - value / scaleMax);
    return `<line x1="${left}" x2="${width - right}" y1="${y}" y2="${y}" class="grid"/><text x="${left - 9}" y="${y + 4}" text-anchor="end" class="tick">${value}</text>`;
  }).join("");
  const bars = labels.map((label, i) => {
    const center = left + (i + 0.5) * columnWidth;
    function bar(count, isBaseline) {
      const x = baseline ? center + (isBaseline ? 2 : -barWidth - 2) : center - barWidth / 2;
      const barHeight = count / scaleMax * plotHeight;
      return `<rect x="${x}" y="${top + plotHeight - barHeight}" width="${barWidth}" height="${barHeight}" rx="3" class="${isBaseline ? "baseline-bar" : "current-bar"}"><title>${escapeHTML(`${label} ${unit}: ${count} ${isBaseline ? "baseline" : "current"} cases`)}</title></rect>`;
    }
    return `${bar(current[i], false)}${baseline ? bar(baseline[i], true) : ""}<text x="${center}" y="${height - bottom + 20}" text-anchor="middle" class="band">${escapeHTML(label)}</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHTML(title)}"><title>${escapeHTML(title)}</title><desc>Vertical axis: number of game cases. Horizontal axis: ${escapeHTML(unit)}. Counts use a shared scale across probe modes.</desc>${grid}${bars}<text x="${width / 2}" y="${height - 5}" text-anchor="middle" class="axis">${escapeHTML(unit)}</text></svg>`;
}

function comparison(mode) {
  if (!mode.pairedCount) return `<p class="comparison">No paired baseline/current timings are available.</p>`;
  const delta = mode.deltaPercent === null ? "unavailable (zero baseline mean)" : `${mode.deltaPercent >= 0 ? "+" : ""}${metric(mode.deltaPercent, "%")}`;
  return `<p class="comparison"><strong>Paired means (${mode.pairedCount} cases):</strong> baseline ${metric(mode.pairedBaselineMean, "s")} · current ${metric(mode.pairedCurrentMean, "s")} · change ${delta}.</p>`;
}

export function renderReportHTML(model) {
  const scope = `${model.fullSample ? "Full fixed sample" : "Partial sample"} · ${model.aggregate.cases}/${model.requestedCases ?? "unknown"} cases`;
  const outcome = model.successful ? model.action === "record" ? "Reference recording completed" : "Exact trace verification passed" : "Failed or incomplete run — diagnostic timings only";
  const panels = Object.entries(model.modes).map(([key, mode]) => {
    const name = key === "pool" ? "Pool probes" : "Expanded probes";
    return `<section class="panel"><h2>${name}</h2><div class="panel-stats"><strong>${metric(mode.currentMean, "s")}</strong> mean per measured game <span>${mode.currentCount}/${mode.cases} current timings</span></div>
<div class="legend"><span class="current-key">Current (${mode.currentCount})</span>${model.hasBaseline ? `<span class="baseline-key">Baseline (${mode.baselineCount})</span>` : ""}</div>
${chart({ title: `${name}: computation time per game`, labels: model.durationBins.map((bin) => bin.label), current: mode.currentCounts, baseline: model.hasBaseline ? mode.baselineCounts : null, scaleMax: model.durationCountMax, unit: "Compute seconds per game" })}
${model.hasBaseline ? comparison(mode) : ""}<p class="coverage">Missing current timings: ${mode.missingCurrent}${model.hasBaseline ? ` · missing baseline timings: ${mode.missingBaseline}` : ""}. Missing values are excluded.</p>
<details class="details"><summary>Duration band counts</summary><table class="table"><thead><tr><th>Seconds</th><th>Current cases</th>${model.hasBaseline ? "<th>Baseline cases</th>" : ""}</tr></thead><tbody>${model.durationBins.map((bin, i) => `<tr><td>${escapeHTML(bin.label)}</td><td>${mode.currentCounts[i]}</td>${model.hasBaseline ? `<td>${mode.baselineCounts[i]}</td>` : ""}</tr>`).join("")}</tbody></table></details>
<h3>Total played moves</h3><p class="moves-mean"><strong>${metric(mode.movesMean)}</strong> mean moves · ${mode.movesCount}/${mode.cases} cases with move counts</p>
${chart({ title: `${name}: total played moves`, labels: model.moveBins.map(String), current: mode.moveCounts, scaleMax: model.moveCountMax, unit: "Played moves, including closing moves" })}
<p class="coverage">Current move counts only; saved timing rows contain no baseline move counts. Missing move counts: ${mode.missingMoves}.</p></section>`;
  }).join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Referdle benchmark report</title><style>
:root {
  --text: #17283b;
  --muted: #4f6579;
  --border: #dce4eb;
  --background: #f5f7f9;
  --current: #2379aa;
  --baseline: #a8bccd;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font: 1rem/1.5 system-ui, sans-serif;
  color: var(--text);
  background: var(--background);
}

main {
  max-width: 93.75rem;
  margin-inline: auto;
  padding: 1.25rem 0.75rem;
}

h1 {
  margin-block: 0.5rem;
  font-size: clamp(1.6875rem, 4vw, 2.625rem);
}

h2 {
  margin: 0 0 0.5rem;
  font-size: 1.5rem;
}

h3 {
  margin: 1.75rem 0 0.5rem;
  font-size: 1.25rem;
}

.eyebrow {
  font-size: 0.8125rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
}

.scope,
.moves-mean {
  color: var(--muted);
}

.status {
  padding: 0.75rem 1rem;
  border-left: 4px solid #208b68;
  border-radius: 0.375rem;
  background: #e5f3ed;
}

.status.failed {
  border-color: #c55529;
  background: #fff0e8;
}

.overview {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.5rem;
  margin-block: 1.5rem;
}

.card,
.panel {
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: #fff;
}

.card {
  padding: 0.75rem;
}

.card strong {
  display: block;
  font-size: 1.375rem;
}

.card span,
.coverage,
.comparison,
.scope-note {
  font-size: 0.875rem;
  color: var(--muted);
}

.panels {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1.25rem;
}

.panel {
  padding: 1rem;
}

.panel-stats strong {
  font-size: 1.6875rem;
}

.panel-stats span {
  display: block;
  font-size: 0.875rem;
  color: var(--muted);
}

.legend {
  display: flex;
  gap: 1.125rem;
  margin-top: 1.25rem;
  font-size: 0.8125rem;
}

.legend span::before {
  display: inline-block;
  width: 0.75rem;
  height: 0.75rem;
  margin-right: 0.375rem;
  border-radius: 0.1875rem;
  content: "";
}

.current-key::before {
  background: var(--current);
}

.current-bar {
  fill: var(--current);
}

.baseline-key::before {
  background: var(--baseline);
}

.baseline-bar {
  fill: var(--baseline);
}

svg {
  display: block;
  width: 100%;
  height: auto;
}

.grid {
  stroke: #e3eaf0;
}

/* SVG typography follows the chart's viewBox coordinates. */
.tick,
.band,
.axis {
  font: 12px system-ui, sans-serif;
  fill: var(--muted);
}

.band {
  font-size: 11px;
}

.axis {
  font-size: 13px;
}

.empty {
  padding: 1.75rem;
  color: var(--muted);
}

.details {
  font-size: 0.875rem;
}

summary {
  color: #376c8d;
  cursor: pointer;
}

.table {
  width: 100%;
  margin-top: 0.625rem;
  border-collapse: collapse;
  text-align: left;
}

th,
td {
  padding: 0.375rem 0.625rem;
  border-bottom: 1px solid #e5ebf0;
}

footer {
  margin-top: 1.75rem;
  padding-block: 1.25rem;
  border-top: 1px solid var(--border);
  font-size: 0.875rem;
  color: var(--muted);
}

footer p,
.scope-note {
  max-width: 65.625rem;
}

.diagnostics {
  margin-top: 1.25rem;
  padding: 1.125rem;
  border-radius: 0.5rem;
  background: #fff0e8;
  overflow-wrap: anywhere;
}

.comparison {
  padding: 0.75rem;
  border-radius: 0.375rem;
  background: #f0f5f9;
}

.aggregate-comparison {
  margin-top: 1.125rem;
}

@media (min-width: 32rem) {
  main {
    padding: 2.25rem 1.5rem;
  }

  .panel {
    padding: 1.5rem;
  }

  .overview {
    gap: 0.875rem;
  }

  .card {
    padding: 1.125rem;
  }

  .card strong {
    font-size: 1.625rem;
  }
}

@media (min-width: 60rem) {
  .panels {
    grid-template-columns: 1fr 1fr;
  }

  .overview {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style></head><body><main><div class="eyebrow">Referdle solver · saved benchmark report</div><h1>Computation time per game</h1><p class="scope">${escapeHTML(scope)}</p><p class="status${model.successful ? "" : " failed"}">${escapeHTML(outcome)}</p>
<p class="scope-note">${model.fullSample ? "100 frozen games in both probe modes." : "This selection does not represent the full 200-case benchmark."} ${model.successful ? "Timing differences describe this single pass; they do not establish a performance improvement." : "Correctness acceptance failed or the run is incomplete. Timing charts are diagnostic and cannot support performance acceptance."}</p>
<div class="overview"><div class="card"><strong>${metric(model.aggregate.currentMean, "s")}</strong><span>Mean current compute time · ${model.aggregate.currentCount} measured cases</span></div><div class="card"><strong>${metric(model.aggregate.movesMean)}</strong><span>Mean total played moves · ${model.aggregate.movesCount} cases</span></div><div class="card"><strong>${metric(model.elapsedMs === null ? null : model.elapsedMs / 60000, " min")}</strong><span>Run elapsed wall time</span></div><div class="card"><strong>${metric(model.referenceBytes === null ? null : model.referenceBytes / 1000000, " MB")}</strong><span>Reference artifacts · decimal megabytes</span></div></div>
${model.hasBaseline ? `<div class="aggregate-comparison">${comparison(model.aggregate)}</div>` : ""}
<div class="panels">${panels}</div>
${model.diagnostics.length ? `<section class="diagnostics"><h2>Run diagnostics</h2><ul>${model.diagnostics.map((text) => `<li>${escapeHTML(text)}</li>`).join("")}</ul></section>` : ""}
<footer><p>${escapeHTML(model.timingBoundaries)}</p><p>${escapeHTML(model.order)}</p><p>Duration bands start at zero and share their boundaries and count scales across probe modes and timing series. Lower boundaries are inclusive; upper boundaries are exclusive, except the final band includes its maximum. Comparison means and changes use only the same cases with both finite timings (${model.aggregate.pairedCount} paired cases). Missing values are never counted as zero.</p><p>Setup time: ${metric(model.setupMs === null ? null : model.setupMs / 1000, "s")}. Total played moves include every closing move.</p>${model.notes.map((text) => `<p>${escapeHTML(text)}</p>`).join("")}</footer></main></body></html>\n`;
}

export async function writeHTMLReport(inputFile, outputFile) {
  await assertSafePaths(DEFAULT_BASELINE_DIR, outputFile);
  const input = await realpath(inputFile);
  let output;
  try { output = await realpath(outputFile); } catch (error) {
    if (error.code !== "ENOENT") throw error;
    output = path.join(await realpath(path.dirname(path.resolve(outputFile))), path.basename(outputFile));
  }
  if (input === output) throw new Error("Input and output must be different files");
  try {
    const [inputStat, outputStat] = await Promise.all([stat(input), stat(output)]);
    if (inputStat.dev === outputStat.dev && inputStat.ino === outputStat.ino) throw new Error("Input and output must be different files");
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  const model = buildReportModel(JSON.parse(await readFile(input, "utf8")));
  await writeFile(output, renderReportHTML(model), "utf8");
  return model;
}

export async function main(args = process.argv.slice(2), { stdout = (text) => process.stdout.write(text), stderr = (text) => process.stderr.write(text) } = {}) {
  try {
    if (args.length === 1 && args[0] === "--help") { stdout(`${HELP}\n`); return 0; }
    if (args.length !== 3 || args[1] !== "--output" || !args[0] || args[0].startsWith("--") || !args[2] || args[2].startsWith("--")) throw new Error(HELP);
    await writeHTMLReport(args[0], args[2]);
    stdout(`Saved HTML report: ${path.resolve(args[2])}\n`);
    return 0;
  } catch (error) { stderr(`${error.message}\n`); return 1; }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = await main();

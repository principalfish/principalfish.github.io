export const TIMING_BOUNDARIES = "Sum of synchronous computation-service intervals for daily advances; excludes Promise waiting, controller publication/rendering, asset loading/decoding, bundling, animation delays, trace normalization/encoding, compression and comparison. Original reference timings included synchronous nextTurn controller/publication work, so percentage comparisons are informative across this changed boundary. Render helpers are capture/no-op shims; this does not measure browser rendering.";
export const RUN_ORDER = "Pool probes first, then expanded probes; ascending fixed days within each mode. One process, one measured pass, no warmup; earliest cases include JIT startup and host load can affect timings.";

export function caseRow({ day, expanded, computeMs = null, baselineMs = null, moves = null, turnMs = [], status, error, difference }) {
  const deltaMs = computeMs !== null && baselineMs !== null ? computeMs - baselineMs : null;
  return {
    day, expanded, mode: expanded ? "expanded" : "pool", status, moves,
    baselineComputeMs: baselineMs, currentComputeMs: computeMs,
    deltaMs, deltaPercent: deltaMs !== null && baselineMs !== 0 ? deltaMs / baselineMs * 100 : null,
    turnMs,
    ...(error ? { error } : {}), ...(difference ? { difference } : {}),
  };
}

function aggregate(rows) {
  const measured = rows.filter((row) => row.currentComputeMs !== null);
  const times = measured.map((row) => row.currentComputeMs).sort((a, b) => a - b);
  const computeMs = times.reduce((sum, time) => sum + time, 0);
  const baselineMs = measured.length && measured.every((row) => row.baselineComputeMs !== null)
    ? measured.reduce((sum, row) => sum + row.baselineComputeMs, 0) : null;
  const middle = Math.floor(times.length / 2);
  const medianMs = !times.length ? null : times.length % 2 ? times[middle] : (times[middle - 1] + times[middle]) / 2;
  return {
    cases: rows.length, measuredCases: measured.length, computeMs, baselineComputeMs: baselineMs,
    deltaMs: baselineMs === null ? null : computeMs - baselineMs,
    deltaPercent: baselineMs === null || baselineMs === 0 ? null : (computeMs - baselineMs) / baselineMs * 100,
    medianMs,
    slowest: [...measured].sort((a, b) => b.currentComputeMs - a.currentComputeMs).slice(0, 5)
      .map(({ day, mode, currentComputeMs }) => ({ day, mode, computeMs: currentComputeMs })),
  };
}

export function summarize(rows) {
  return {
    pool: aggregate(rows.filter((row) => !row.expanded)),
    expanded: aggregate(rows.filter((row) => row.expanded)),
    overall: aggregate(rows),
  };
}

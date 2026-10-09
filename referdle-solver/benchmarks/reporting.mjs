export const TIMING_BOUNDARIES = "Sum of synchronous computation-service intervals for daily advances; excludes Promise waiting, controller publication/rendering, asset loading/decoding, bundling, animation delays, trace normalization/encoding, compression and comparison. Original reference timings included synchronous nextTurn controller/publication work, so percentage comparisons are informative across this changed boundary. Worker roundtrip time sums per-advance job durations from host send through accepted reply, including computation, queueing, cloning and message handling. Roundtrip minus compute is transport/job overhead (including benchmark trace evidence), not pure messaging latency. Day loading/initialization is setup or wall time, outside per-game advance totals. Render helpers are capture/no-op shims; this does not measure browser rendering.";
export const RUN_ORDER = "Pool probes first, then expanded probes; ascending fixed days within each mode. One host process and one reusable native Worker for the Worker backend; one measured pass, no warmup; earliest cases include JIT startup and host load can affect timings.";

export function caseRow({ day, expanded, computeMs = null, baselineMs = null, moves = null, turnMs = [], roundtripMs = null, turnRoundtripMs = [], status, error, difference }) {
  const deltaMs = computeMs !== null && baselineMs !== null ? computeMs - baselineMs : null;
  return {
    day, expanded, mode: expanded ? "expanded" : "pool", status, moves,
    baselineComputeMs: baselineMs, currentComputeMs: computeMs,
    deltaMs, deltaPercent: deltaMs !== null && baselineMs !== 0 ? deltaMs / baselineMs * 100 : null,
    turnMs, turnRoundtripMs, roundtripMs,
    transportOverheadMs: roundtripMs !== null && computeMs !== null ? roundtripMs - computeMs : null,
    ...(error ? { error } : {}), ...(difference ? { difference } : {}),
  };
}

function aggregate(rows) {
  const measured = rows.filter((row) => row.currentComputeMs !== null);
  const times = measured.map((row) => row.currentComputeMs).sort((a, b) => a - b);
  const computeMs = times.reduce((sum, time) => sum + time, 0);
  const baselineMs = measured.length && measured.every((row) => row.baselineComputeMs !== null)
    ? measured.reduce((sum, row) => sum + row.baselineComputeMs, 0) : null;
  const roundtripRows = rows.filter(row => row.roundtripMs != null);
  const roundtripTimes = roundtripRows.map(row => row.roundtripMs).sort((a, b) => a - b);
  const roundtripMs = roundtripTimes.length ? roundtripTimes.reduce((sum, time) => sum + time, 0) : null;
  const paired = roundtripRows.filter(row => row.currentComputeMs != null);
  const transportOverheadMs = paired.length ? paired.reduce((sum, row) => sum + row.roundtripMs - row.currentComputeMs, 0) : null;
  const roundtripMiddle = Math.floor(roundtripTimes.length / 2);
  const roundtripMedianMs = !roundtripTimes.length ? null : roundtripTimes.length % 2
    ? roundtripTimes[roundtripMiddle] : (roundtripTimes[roundtripMiddle - 1] + roundtripTimes[roundtripMiddle]) / 2;
  const middle = Math.floor(times.length / 2);
  const medianMs = !times.length ? null : times.length % 2 ? times[middle] : (times[middle - 1] + times[middle]) / 2;
  return {
    cases: rows.length, measuredCases: measured.length, computeMs, baselineComputeMs: baselineMs,
    deltaMs: baselineMs === null ? null : computeMs - baselineMs,
    deltaPercent: baselineMs === null || baselineMs === 0 ? null : (computeMs - baselineMs) / baselineMs * 100,
    medianMs, meanComputeMs: times.length ? computeMs / times.length : null,
    roundtripMeasuredCases: roundtripRows.length, roundtripMs, roundtripMedianMs,
    roundtripMeanMs: roundtripTimes.length ? roundtripMs / roundtripTimes.length : null,
    overheadMeasuredCases: paired.length, transportOverheadMs,
    transportOverheadMeanMs: paired.length ? transportOverheadMs / paired.length : null,
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

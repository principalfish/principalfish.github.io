import { formatInt, formatPct } from "../utils.js";

/** Model updates averaged per drawn band step; the tooltip keeps each day's own range. */
const BAND_SMOOTHING_POINTS = 5;

export function pollTrackerCapabilities(data) {
  const series = Array.from(data.seriesByParty.values());
  const hasBands = (loKey, hiKey) => series.some((row) => row[loKey]?.some((lo, index) => Number.isFinite(lo) && Number.isFinite(row[hiKey][index])));
  const seatBands = hasBands("seatLo", "seatHi");
  const voteBands = hasBands("voteLo", "voteHi");
  return {
    bands: seatBands || voteBands, seatBands, voteBands,
    chances: data.hung?.some(Number.isFinite) || series.some((row) => [...(row.pMaj || []), ...(row.pLargest || [])].some(Number.isFinite)),
  };
}

export function pollTrackerUncertaintyNote(capabilities, { chances, seats, votes, bands = true }) {
  const basis = "if an election were held today, conditional on current polls and the model's error assumptions.";
  if (chances) return `Chances ${basis} Hung means no party wins a majority.`;
  if (!bands) return "Uncertainty hidden. Tick Uncertainty to show 90% ranges.";
  const metrics = [];
  if (seats && capabilities.seatBands) metrics.push("seat");
  if (votes && capabilities.voteBands) metrics.push("vote-share");
  return metrics.length ? `Shaded bands show 90% ${metrics.join(" and ")} ranges (averaged over ${BAND_SMOOTHING_POINTS} model updates) ${basis}`
    : `Uncertainty describes an election held today, conditional on current polls and the model's error assumptions.`;
}

/** Reuse the percentage plotting axis for odds, while retaining the underlying series. */
export function chanceSeries(series, metric, hung) {
  const percent = (values) => values.map((value) => Number.isFinite(value) ? value * 100 : null);
  const label = metric === "pLargest" ? "Largest-party chance" : "Majority chance";
  return [
    ...series.map((row) => ({ ...row, votePct: percent(row[metric] || []), chanceLabel: label })),
    {
      partyKey: "hung", partyName: "Hung parliament", colour: "#475569", isHung: true,
      votePct: percent(hung), chanceLabel: "Hung-parliament chance",
    },
  ];
}

/**
 * Only finite values are formatted; missing optional observations never become zero.
 * With `ranges: false` (uncertainty hidden) the 90% seat and vote ranges are left out.
 */
export function pollTrackerTooltipValues(series, index, { ranges = true } = {}) {
  const percent = (value) => Number.isFinite(value) ? `${formatPct(value)}%` : "Unavailable";
  if (series.chanceLabel) return [[series.chanceLabel, percent(series.votePct[index])]];
  const values = [
    ["Seats", Number.isFinite(series.seats[index]) ? formatInt(series.seats[index]) : "Unavailable"],
    ["Vote share", percent(series.votePct[index])],
  ];
  if (!ranges) return values;
  if (Number.isFinite(series.seatLo?.[index]) && Number.isFinite(series.seatHi?.[index])) {
    values.push(["90% seat range", `${formatInt(series.seatLo[index])}–${formatInt(series.seatHi[index])}`]);
  }
  if (Number.isFinite(series.voteLo?.[index]) && Number.isFinite(series.voteHi?.[index])) {
    values.push(["90% vote range", `${formatPct(series.voteLo[index])}–${formatPct(series.voteHi[index])}%`]);
  }
  return values;
}

function validBand(values) {
  return values.every(Number.isFinite) && values[0] <= values[1];
}

/**
 * Centred moving average of both bounds over model updates, damping run-to-run simulation noise.
 * Days carried forward repeat their run's band, so consecutive equal bands form one step that
 * counts once and stays flat. The window stays inside each unbroken stretch of valid steps
 * (shrinking at its ends), so smoothing never bridges a missing day or drags an isolated range.
 */
export function smoothPollTrackerBand(band, points = BAND_SMOOTHING_POINTS) {
  const radius = Math.floor(points / 2);
  const steps = [];
  band.forEach((values, index) => {
    const previous = steps.at(-1);
    if (previous && previous.values[0] === values[0] && previous.values[1] === values[1]) previous.end = index;
    else steps.push({ values, start: index, end: index });
  });
  const isValidStep = (step) => step !== undefined && validBand(step.values);
  const smoothed = band.slice();
  steps.forEach((step, position) => {
    if (!isValidStep(step)) return;
    let first = position;
    let last = position;
    while (first > position - radius && isValidStep(steps[first - 1])) first -= 1;
    while (last < position + radius && isValidStep(steps[last + 1])) last += 1;
    const window = steps.slice(first, last + 1);
    const mean = [0, 1].map((side) => window.reduce((sum, { values }) => sum + values[side], 0) / window.length);
    for (let index = step.start; index <= step.end; index += 1) smoothed[index] = mean;
  });
  return smoothed;
}

/**
 * Nested ribbons follow the central curve and each side's distance to its interval edge.
 * This is a display fade, not an estimate of probability density within the interval.
 * A central projection outside its simulation interval anchors the fade at the nearest edge.
 */
export function pollTrackerBandRibbons(band, central) {
  return Array.from({ length: 16 }, (_, layer) => {
    const width = (16 - layer) / 17;
    return band.map((values, index) => {
      if (!validBand(values) || !Number.isFinite(central[index])) return [null, null];
      const [lo, hi] = values;
      const anchor = Math.max(lo, Math.min(hi, central[index]));
      return [anchor + (lo - anchor) * width, anchor + (hi - anchor) * width];
    });
  });
}

/** Bands are painted before every line, with gaps matching missing uncertainty runs. */
export function drawPollTrackerBands(plot, series, xAt, y, createArea, metric) {
  const [loKey, hiKey] = metric === "seats" ? ["seatLo", "seatHi"] : ["voteLo", "voteHi"];
  for (const row of series) {
    const band = smoothPollTrackerBand((row[loKey] || []).map((lo, index) => [lo, row[hiKey][index]]));
    const defined = validBand;
    const indices = band.map((values, index) => defined(values) ? index : -1).filter((index) => index >= 0);
    if (!indices.length) continue;
    const area = createArea().defined(defined)
      .x((_values, index) => xAt(index)).y0((values) => y(values[0])).y1((values) => y(values[1]));
    const central = metric === "seats" ? row.seats : row.votePct;
    for (const ribbon of pollTrackerBandRibbons(band, central)) {
      plot.append("path").datum(ribbon).attr("class", "maps-polltracker-band-shade")
        .attr("fill", row.colour).attr("d", area);
    }
    plot.append("path").datum(band).attr("class", `maps-polltracker-band maps-polltracker-band--${metric}`)
      .attr("fill", row.colour).attr("d", area);
    for (const index of indices) {
      if (defined(band[index - 1] || [null]) || defined(band[index + 1] || [null])) continue;
      plot.append("line").attr("class", "maps-polltracker-band-point")
        .attr("x1", xAt(index)).attr("x2", xAt(index))
        .attr("y1", y(band[index][0])).attr("y2", y(band[index][1]))
        .attr("stroke", row.colour);
    }
  }
}

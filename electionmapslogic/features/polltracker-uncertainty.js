import { formatProbability } from "../uncertainty.js";
import { formatInt, formatPct } from "../utils.js";

/** Model updates averaged per drawn band step; the tooltip keeps each day's own range. */
const BAND_SMOOTHING_POINTS = 5;

/**
 * Index of the first entry with a hung, majority or largest-party chance, or -1 when the data has
 * none: the one presence test behind both the Chances view's availability and its start date.
 * @param {{seriesByParty: Map, hung?: Array<number|null>}} data
 * @returns {number}
 */
export function firstChanceIndex(data) {
  const series = Array.from(data.seriesByParty.values());
  const length = Math.max(data.hung?.length ?? 0,
    ...series.flatMap((row) => [row.pMaj?.length ?? 0, row.pLargest?.length ?? 0]));
  for (let i = 0; i < length; i += 1) {
    if (Number.isFinite(data.hung?.[i])
      || series.some((row) => Number.isFinite(row.pMaj?.[i]) || Number.isFinite(row.pLargest?.[i]))) return i;
  }
  return -1;
}

export function pollTrackerCapabilities(data) {
  const series = Array.from(data.seriesByParty.values());
  const hasBands = (loKey, hiKey) => series.some((row) => row[loKey]?.some((lo, index) => Number.isFinite(lo) && Number.isFinite(row[hiKey][index])));
  const seatBands = hasBands("seatLo", "seatHi");
  const voteBands = hasBands("voteLo", "voteHi");
  return {
    bands: seatBands || voteBands, seatBands, voteBands,
    seatMeans: series.some((row) => row.seatMean?.some(Number.isFinite)),
    chances: firstChanceIndex(data) >= 0,
  };
}

/**
 * The note under the tracker controls, shown only while the Uncertainty box is ticked: the seat
 * lines are the simulated mean seats with 90% bands. The seat-line sentence appears only while
 * seats are drawn and the data has means.
 */
export function pollTrackerUncertaintyNote(capabilities, { chances, seats, votes }) {
  const basis = "if an election were held today, conditional on current polls and the model's error assumptions.";
  if (chances) return `Chances ${basis} Hung means no party wins a majority.`;
  const lead = seats && capabilities.seatMeans ? "Seat lines show the simulated mean seats. " : "";
  const metrics = [];
  if (seats && capabilities.seatBands) metrics.push("seat");
  if (votes && capabilities.voteBands) metrics.push("vote-share");
  return metrics.length ? `${lead}Shaded bands show 90% ${metrics.join(" and ")} ranges (trailing average over up to ${BAND_SMOOTHING_POINTS} model updates) ${basis}`
    : `${lead}Uncertainty describes an election held today, conditional on current polls and the model's error assumptions.`;
}

/**
 * Reuse the percentage plotting axis for odds, while retaining the underlying series. The 0–1
 * odds stay beside the plotted percentages so the tooltip formats the published value itself.
 */
export function chanceSeries(series, metric, hung) {
  const percent = (values) => values.map((value) => Number.isFinite(value) ? value * 100 : null);
  const label = metric === "pLargest" ? "Largest-party chance" : "Majority chance";
  return [
    ...series.map((row) => {
      const chance = row[metric] || [];
      return { ...row, votePct: percent(chance), chance, chanceLabel: label };
    }),
    {
      partyKey: "hung", partyName: "Hung parliament", colour: "#475569", isHung: true,
      votePct: percent(hung), chance: hung, chanceLabel: "Hung-parliament chance",
    },
  ];
}

/**
 * With `ranges: false` (uncertainty hidden) the tooltip is the plain whole seats and vote %, a
 * missing value reading as zero, with no 90% ranges.
 * With uncertainty shown, only finite values are formatted, so a missing optional observation
 * never becomes zero. A run with a simulated mean shows that mean, labelled so and kept to the
 * trend file's 1 dp; a central-only run such as the election result shows its whole seats.
 */
export function pollTrackerTooltipValues(series, index, { ranges = true } = {}) {
  const percent = (value) => Number.isFinite(value) ? `${formatPct(value)}%` : "Unavailable";
  if (series.chanceLabel) {
    const chance = series.chance[index];
    return [[series.chanceLabel, Number.isFinite(chance) ? formatProbability(chance) : "Unavailable"]];
  }
  if (!ranges) {
    return [
      ["Seats", formatInt(Number(series.seats[index] || 0))],
      ["Vote %", `${formatPct(Number(series.votePct[index] || 0))}%`],
    ];
  }
  const mean = series.seatMean?.[index];
  const values = [
    Number.isFinite(mean) ? ["Seats (simulated mean)", mean.toFixed(1)]
      : ["Seats", Number.isFinite(series.seats[index]) ? formatInt(series.seats[index]) : "Unavailable"],
    ["Vote %", percent(series.votePct[index])],
  ];
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
 * Trailing moving average of both bounds over model updates, damping run-to-run simulation noise.
 * Days carried forward repeat their run's band, so consecutive equal bands form one step that
 * counts once and stays flat. The window stays inside each unbroken stretch of valid steps
 * (including only past and current steps), so smoothing never bridges a missing day or
 * changes a historical band when later updates arrive.
 */
export function smoothPollTrackerBand(band, points = BAND_SMOOTHING_POINTS) {
  const windowSize = Math.max(1, Math.floor(points));
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
    while (first > position - windowSize + 1 && isValidStep(steps[first - 1])) first -= 1;
    const window = steps.slice(first, position + 1);
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

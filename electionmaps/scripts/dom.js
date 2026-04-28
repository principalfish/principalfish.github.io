import * as d3 from '../../site/vendor/d3.v7.esm.js';
import { manifest, state } from './state.js';
import { escapeHtml, formatInt, formatPct } from './utils.js';

// Left-rail nav container — populated with one anchor per election plus the
// Predict / Poll tracker links by renderElectionLinks.
const electionList = document.getElementById('mapsElectionList');
// Page H1 — set to "UK Election Maps · <Parliament>" by renderTitle.
const mapsTitle = document.querySelector('.maps-title');
// Countdown badge in the header — visible only for the Holyrood UNS prediction;
// ticks every second until the election date passes.
const electionCountdown = document.getElementById('mapsElectionCountdown');
// Subtitle line below the H1 — election name (or "Poll tracker..." in tracker view),
// optionally suffixed with the poll snippet for prediction elections.
const subtitle = document.getElementById('mapsSubtitle');

// ─── Page title ───────────────────────────────────────────────────────────────

const MAPS_PAGE_TITLE_SUFFIX = 'Election Maps | Principal Fish';

/**
 * Sets the browser tab title from the current view: poll tracker, predict (with next election year), or election name.
 * @returns {void}
 */
export function setPageTitle() {
  let label;
  if (state.view === 'polltracker') {
    label = 'Poll tracker';
  } else if (state.view === 'predict') {
    const nextElectionYear = manifest.parliamentFeatures[state.currentParliament]?.nextElectionYear;
    label = `Predict ${nextElectionYear ?? ''}`.trim();
  } else {
    label = state.currentElection.name;
  }
  const parliament = state.currentParliament;
  const parlLabel = parliament ? parliament[0].toUpperCase() + parliament.slice(1) : null;
  const suffix = parlLabel ? `${parlLabel} | ${MAPS_PAGE_TITLE_SUFFIX}` : MAPS_PAGE_TITLE_SUFFIX;
  document.title = label ? `${label} | ${suffix}` : suffix;
}

// ─── Header ─────────────────────────────────────────────────────────────────

/**
 * Updates the title area: the page h1, subtitle, and election countdown.
 * Called early in init (text omitted — subtitle falls back to election name) and again
 * after results load with the full summary string. Pass error=true on load failure.
 * @param {string} [text=''] - Full subtitle string (e.g. "2024 Election · Labour majority: 174").
 *   TODO: once election summary data is held in state, derive this internally and remove the param.
 * @param {boolean} [error=false] - When true, subtitle shows a load-failure message.
 * @returns {void}
 */
export function setHeader(text = '', error = false) {
  renderTitle();
  renderSubtitleText(text, error);
  renderCountdown();
}

/**
 * Renders the subtitle element. Derives snippet behaviour from current state:
 * poll tracker view uses a fixed label with snippet; election view uses the provided
 * text (falling back to the election name before results load) with snippet for model elections.
 * @param {string} [text=''] - Subtitle string; omit on early init to fall back to election name.
 * @param {boolean} [error=false] - When true, displays a load-failure message instead.
 * @returns {void}
 */
function renderSubtitleText(text = '', error = false) {
  if (!subtitle) return;

  let baseText;
  let includeSnippet;

  if (error) {
    baseText = 'Failed to load election data';
    includeSnippet = false;
  } else if (state.view === 'polltracker') {
    baseText = 'Poll tracker · model output trends';
    includeSnippet = true;
  } else {
    baseText = text || state.currentElection?.name || '';
    includeSnippet = Boolean(state.currentElection?.model);
  }

  subtitle.textContent = '';

  const mainSpan = document.createElement('span');
  mainSpan.className = 'maps-subtitle-main';
  mainSpan.textContent = String(baseText || '').trim();
  subtitle.appendChild(mainSpan);

  const latestPollSnippet = includeSnippet ? state.predictionSnippet : '';
  subtitle.classList.toggle('maps-subtitle-has-latest', Boolean(latestPollSnippet));
  if (!latestPollSnippet) return;

  const latestSpan = document.createElement('span');
  latestSpan.className = 'maps-subtitle-latest';
  latestSpan.textContent = latestPollSnippet;
  subtitle.appendChild(latestSpan);
}

/**
 * Updates the page h1 to suffix the current parliament name (e.g. "UK Election Maps · Westminster").
 * @returns {void}
 */
function renderTitle() {
  const label = state.currentParliament[0].toUpperCase() + state.currentParliament.slice(1);
  mapsTitle.textContent = `UK Election Maps · ${label}`;
}

// ─── Left Bar ─────────────────────────────────────────────────────────────────

/**
 * Updates the left panel: highlights the active parliament tab and rebuilds the election list nav.
 * @param {{ onPredict?: function, onPollTracker?: function }} callbacks - Click handlers for mode buttons.
 * @returns {void}
 */
export function setLeftBar() {
  renderParliamentTabs();
  renderElectionLinks();
}

/**
 * Highlights the active parliament tab by toggling the 'active' class on all [data-parliament] elements.
 * @returns {void}
 */
function renderParliamentTabs() {
  document.querySelectorAll('[data-parliament]').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.parliament === state.currentParliament);
  });
}

/**
 * Rebuilds the election list nav, inserting Predict and Poll tracker links after the
 * current-prediction entry.
 * @returns {void}
 */
function renderElectionLinks() {
  const activeId = state.view === 'election' ? state.currentElection.id : null;

  const features = manifest.parliamentFeatures[state.currentParliament]?.features ?? [];
  const hasPredictMode = features.includes('predict');
  const hasPollTracker = features.includes('pollTracker');
  const predictAnchorId = state.getPredictAnchorElectionId() ?? null;

  electionList.innerHTML = '';
  let insertedPredictLink = false;
  let insertedPollTrackerLink = false;
  state.parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = state.viewUrl('election', election.id);
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (hasPredictMode && !insertedPredictLink && election.id === predictAnchorId) {
      const predictLink = document.createElement('a');
      predictLink.href = state.viewUrl('predict');
      predictLink.className = `maps-election-item${state.view === 'predict' ? ' active' : ''}`;
      const nextElectionYear = manifest.parliamentFeatures[state.currentParliament]?.nextElectionYear;
      predictLink.textContent = `Predict ${nextElectionYear ?? ''}`;
      electionList.appendChild(predictLink);
      insertedPredictLink = true;

      if (hasPollTracker && !insertedPollTrackerLink) {
        const trackerLink = document.createElement('a');
        trackerLink.href = state.viewUrl('polltracker');
        trackerLink.className = `maps-election-item${state.view === 'polltracker' ? ' active' : ''}`;
        trackerLink.textContent = 'Poll tracker';
        electionList.appendChild(trackerLink);
        insertedPollTrackerLink = true;
      }
    }
  });
}

// ─── Countdown ────────────────────────────────────────────────────────────────

const countdown = {
  /** setInterval handle for the 1-second countdown tick, or null when not running. */
  intervalId: null,
};

/**
 * Formats a millisecond duration as "Xd Xh Xm Xs".
 * @param {number} ms - Milliseconds remaining (must be > 0).
 * @returns {string} Formatted countdown string.
 */
function formatCountdown(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${days}d ${hours}h ${minutes}m ${seconds}s`;
}

/**
 * Shows or hides the election countdown element based on the current election type and mode.
 * Starts a 1-second interval tick when visible; clears it when hidden or after election day.
 * Visible only when state.currentElection.type is 'holyrood_uns' and poll tracker is not active.
 * @returns {void}
 */
function renderCountdown() {
  if (!electionCountdown) return;

  const shouldShow = state.shouldShowCountdown();

  if (countdown.intervalId !== null) {
    clearInterval(countdown.intervalId);
    countdown.intervalId = null;
  }

  if (!shouldShow) {
    electionCountdown.hidden = true;
    return;
  }

  const tick = () => {
    const msLeft = new Date(manifest.misc?.holyroodElectionDate) - Date.now();
    if (msLeft <= 0) {
      electionCountdown.hidden = true;
      // Clear and null the handle so renderCountdown can safely restart if called again.
      clearInterval(countdown.intervalId);
      countdown.intervalId = null;
      return;
    }
    electionCountdown.textContent = `${formatCountdown(msLeft)} · Holyrood election · 7 May 2026`;
    electionCountdown.hidden = false;
  };

  tick();
  countdown.intervalId = setInterval(tick, 1000);
}

// ─── Poll tracker ─────────────────────────────────────────────────────────────

const POLLTRACKER_RANGE_ATTR = 'data-polltracker-range';
const POLLTRACKER_AXIS_CLASS = 'maps-polltracker-axis';
const POLLTRACKER_AXIS_LABEL_CLASS = 'maps-polltracker-axis-label';
const POLLTRACKER_PARTY_TOGGLE_CLASS = 'maps-polltracker-party-toggle';
const POLLTRACKER_LINE_STROKE_WIDTH = 2.1;
const POLLTRACKER_HIT_STROKE_WIDTH = 14;
const POLLTRACKER_EMPTY_CLASS = 'maps-polltracker-empty';
const POLLTRACKER_GRID_LINE_CLASS = 'maps-polltracker-grid-line';

// Wrapper element that the poll tracker SVG chart and tooltip overlay are mounted into.
const pollTrackerChartWrap = document.getElementById('mapsPollTrackerChartWrap');
// Container for the per-party checkbox toggles, built once per page load.
const pollTrackerPartyControls = document.getElementById('mapsPollTrackerPartyControls');
// Metric checkbox — when checked, the chart paints solid lines for seats on the left axis.
const pollTrackerMetricSeatsInput = document.getElementById('mapsPollTrackerMetricSeats');
// Metric checkbox — when checked, the chart paints dashed lines for vote % on the right axis.
const pollTrackerMetricVotesInput = document.getElementById('mapsPollTrackerMetricVotes');

/**
 * Attaches a change handler to a poll tracker metric radio/checkbox so the chart re-renders
 * when the metric (seats vs vote %) is switched. No-op if null or already wired.
 * @param {HTMLInputElement|null} inputEl - The input element to wire.
 * @returns {void}
 */
function wirePollTrackerMetricInput(inputEl) {
  if (!inputEl || inputEl.dataset.wired === 'true') return;
  inputEl.addEventListener('change', () => {
    if (state.view === 'polltracker') setPollTracker();
  });
  inputEl.dataset.wired = 'true';
}

/**
 * Wires the seats and vote-% metric inputs (via wirePollTrackerMetricInput) and all
 * [data-polltracker-range] buttons so clicking a range button updates the active button
 * and re-renders the chart. Guards individual buttons against double-wiring via dataset flag.
 * @returns {void}
 */
export function wirePollTrackerControls() {
  wirePollTrackerMetricInput(pollTrackerMetricSeatsInput);
  wirePollTrackerMetricInput(pollTrackerMetricVotesInput);

  document.querySelectorAll(`[${POLLTRACKER_RANGE_ATTR}]`).forEach((button) => {
    if (button.dataset.wired === 'true') return;
    button.addEventListener('click', () => {
      const nextRange = button.getAttribute(POLLTRACKER_RANGE_ATTR) || 'all';
      document.querySelectorAll(`[${POLLTRACKER_RANGE_ATTR}]`).forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate.getAttribute(POLLTRACKER_RANGE_ATTR) === nextRange);
      });
      if (state.view === 'polltracker') setPollTracker();
    });
    button.dataset.wired = 'true';
  });
}

let partyControlsRendered = false;

/**
 * Renders the poll tracker view: party toggles (once per page load) and the chart.
 * Subsequent calls only re-render the chart, since the toggle state is preserved on the DOM.
 * @returns {void}
 */
export function setPollTracker() {
  if (!partyControlsRendered) {
    renderPollTrackerPartyControls();
    partyControlsRendered = true;
  }
  renderPollTrackerChart();
}

/**
 * Returns the party key values of all checked party toggle checkboxes in the poll tracker controls.
 * @returns {string[]} Array of party key strings for all currently checked party toggle inputs.
 */
function polltrackerSelectedParties() {
  return Array.from(document.querySelectorAll(`.${POLLTRACKER_PARTY_TOGGLE_CLASS} input[type="checkbox"]`))
    .filter((input) => input.checked)
    .map((input) => input.value);
}

/**
 * Renders the poll tracker D3 SVG chart into pollTrackerChartWrap.
 * Draws solid lines for seats (left axis) and dashed lines for vote % (right axis), one pair per
 * selected party. Respects the current date-range selection and includes a crosshair tooltip on hover.
 * @returns {void}
 */
function renderPollTrackerChart() {
  // Reset the chart
  pollTrackerChartWrap.innerHTML = '';
  pollTrackerChartWrap.style.position = 'relative';

  // Read inputs.
  const selectedParties = polltrackerSelectedParties();
  const seatsEnabled = Boolean(pollTrackerMetricSeatsInput?.checked);
  const votePctEnabled = Boolean(pollTrackerMetricVotesInput?.checked);

  // Bail with a friendly message if there's no data, or if the user
  //    has deselected everything (no parties, or no metric).
  if (!state.pollTrackerData.timeline.length) {
    pollTrackerChartWrap.innerHTML = `<div class="${POLLTRACKER_EMPTY_CLASS}">No poll tracker data available.</div>`;
    return;
  }
  if (!selectedParties.length || !(seatsEnabled || votePctEnabled)) {
    pollTrackerChartWrap.innerHTML = `<div class="${POLLTRACKER_EMPTY_CLASS}">Select at least one party and one metric (Seats/Vote %).</div>`;
    return;
  }

  // Slice the visible window from the back of the full timeline based on the user range selection.
  const { visibleTimeline, windowStart } = pollTrackerWindow();

  // Resolve responsive SVG dimensions and reserved margins.
  const { width, height, margin, innerWidth, innerHeight } = pollTrackerDimensions();

  // Build the SVG canvas, plot group, tooltip overlay, and the crosshair line.
  const { svg, plot, tooltip, crosshairLine } = pollTrackerScaffold(width, height, margin, innerHeight);

  // Compute scales, per-party windowed series, and line generators.
  const { x, useTimeScale } = pollTrackerXScale(visibleTimeline, innerWidth);
  const selectedSeries = pollTrackerSelectedSeries(selectedParties, windowStart);
  const { ySeats, yVotePct } = pollTrackerYScales(selectedSeries, innerHeight);
  const { seatsLine, votePctLine } = pollTrackerLineGenerators({ useTimeScale, x, visibleTimeline, ySeats, yVotePct });

  // Paint background grid and axes.
  pollTrackerDrawGrid(plot, { seatsEnabled, ySeats, yVotePct, innerWidth });
  pollTrackerDrawXAxis(plot, { x, useTimeScale, visibleTimeline, innerHeight });
  pollTrackerDrawYAxes(plot, { seatsEnabled, votePctEnabled, ySeats, yVotePct, innerWidth, innerHeight });

  // Construct tooltip handlers closed over the current render's scales and DOM refs.
  const { showTrackerTooltip, hideTrackerTooltip } = pollTrackerTooltipHandlers({
    svg, tooltip, crosshairLine, margin, innerWidth, width, height, useTimeScale, x, visibleTimeline,
  });

  // Paint the lines and legend, then mount the SVG.
  pollTrackerDrawLines(plot, { selectedSeries, seatsEnabled, votePctEnabled, seatsLine, votePctLine, showTrackerTooltip, hideTrackerTooltip });
  pollTrackerDrawLegend(svg, { width, margin });
  pollTrackerChartWrap.appendChild(svg.node());
}

/**
 * Resolves the visible slice of the poll tracker timeline based on the user's range selection.
 * The active [data-polltracker-range] button's value is either 'all' (whole timeline) or a
 * numeric day count; Number('all') is NaN so the isFinite guard naturally falls through to the
 * full length.
 * @returns {{visibleTimeline: Array<{dateKey: string, dateValue: Date}>, windowStart: number}}
 */
function pollTrackerWindow() {
  const timeline = state.pollTrackerData.timeline;
  const activeBtn = document.querySelector(`[${POLLTRACKER_RANGE_ATTR}].is-active`);
  const requested = Number(activeBtn?.getAttribute(POLLTRACKER_RANGE_ATTR));
  const windowSize = Number.isFinite(requested) && requested > 0
    ? Math.min(requested, timeline.length)
    : timeline.length;
  const windowStart = timeline.length - windowSize;
  return { visibleTimeline: timeline.slice(windowStart), windowStart };
}

/**
 * Resolves chart dimensions: responsive width (760px floor, 8px gutter), fixed height,
 * and reserved margins for axes/labels.
 * @returns {{
 *   width: number,
 *   height: number,
 *   margin: {top: number, right: number, bottom: number, left: number},
 *   innerWidth: number,
 *   innerHeight: number
 * }}
 */
function pollTrackerDimensions() {
  const width = Math.max(760, pollTrackerChartWrap.clientWidth - 8);
  const height = 520;
  const margin = { top: 14, right: 84, bottom: 58, left: 70 };
  return {
    width,
    height,
    margin,
    innerWidth: width - margin.left - margin.right,
    innerHeight: height - margin.top - margin.bottom,
  };
}

/**
 * Builds the SVG canvas, the plot group (translated by margins), an HTML tooltip overlay, and
 * a vertical crosshair line. Mounts the tooltip into pollTrackerChartWrap; the SVG is mounted by the caller.
 * @param {number} width
 * @param {number} height
 * @param {{top:number,right:number,bottom:number,left:number}} margin
 * @param {number} innerHeight
 * @returns {{svg: object, plot: object, tooltip: HTMLDivElement, crosshairLine: object}}
 */
function pollTrackerScaffold(width, height, margin, innerHeight) {
  const svg = d3.create('svg').attr('viewBox', `0 0 ${width} ${height}`);
  const plot = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const tooltip = document.createElement('div');
  tooltip.className = 'maps-polltracker-tooltip';
  tooltip.hidden = true;
  pollTrackerChartWrap.appendChild(tooltip);

  const crosshairLine = plot.append('line')
    .attr('class', 'maps-polltracker-crosshair')
    .attr('y1', 0)
    .attr('y2', innerHeight)
    .attr('opacity', 0);

  return { svg, plot, tooltip, crosshairLine };
}

/**
 * Builds the X scale. With ≥2 entries we use a real time scale so points are placed at their
 * actual dates (gaps span proportionally). The 1-entry edge case can't form a time domain, so
 * fall back to a degenerate linear scale — only one point to plot, positioning is trivial.
 * @param {Array<{dateValue: Date}>} visibleTimeline
 * @param {number} innerWidth
 * @returns {{x: object, useTimeScale: boolean}}
 */
function pollTrackerXScale(visibleTimeline, innerWidth) {
  const useTimeScale = visibleTimeline.length > 1;
  const x = useTimeScale
    ? d3.scaleTime()
      .domain(d3.extent(visibleTimeline.map((entry) => entry.dateValue)))
      .range([0, innerWidth])
    : d3.scaleLinear()
      .domain([0, 0])
      .range([0, innerWidth]);
  return { x, useTimeScale };
}

/**
 * Reshapes the per-party series down to just the visible window. Skips parties with no data.
 * The seats/votePct arrays are sliced to align positionally with visibleTimeline.
 * @param {string[]} selectedParties - party keys checked in the toggle list
 * @param {number} windowStart - index in the full timeline where the visible window begins
 * @returns {Array<{
 *   partyKey: string,
 *   partyName: string,
 *   colour: string,
 *   seats: Array<number|null>,
 *   votePct: Array<number|null>,
 *   latestSeats: number
 * }>}
 */
function pollTrackerSelectedSeries(selectedParties, windowStart) {
  return selectedParties
    .map((partyKey) => state.pollTrackerData.seriesByParty.get(partyKey))
    .filter(Boolean)
    .map((series) => ({
      ...series,
      seats: series.seats.slice(windowStart),
      votePct: series.votePct.slice(windowStart),
    }));
}

/**
 * Builds the two Y scales. Each metric gets 8% headroom (`* 1.08`); vote-% is capped at 100.
 * `.nice()` rounds the domain to friendly tick boundaries.
 * @param {Array<{seats: Array<number|null>, votePct: Array<number|null>}>} selectedSeries
 * @param {number} innerHeight
 * @returns {{ySeats: object, yVotePct: object}}
 */
function pollTrackerYScales(selectedSeries, innerHeight) {
  const seatsMax = d3.max(selectedSeries.flatMap((series) => series.seats.filter((value) => Number.isFinite(value)))) || 1;
  const votePctMax = d3.max(selectedSeries.flatMap((series) => series.votePct.filter((value) => Number.isFinite(value)))) || 1;
  return {
    ySeats: d3.scaleLinear().domain([0, seatsMax * 1.08]).nice().range([innerHeight, 0]),
    yVotePct: d3.scaleLinear().domain([0, Math.min(100, votePctMax * 1.08)]).nice().range([innerHeight, 0]),
  };
}

/**
 * Builds d3.line generators for each metric. `.defined(...)` skips null gaps so lines break
 * (rather than connect through) on dates before the party first appears in the data.
 * @param {{useTimeScale: boolean, x: object, visibleTimeline: Array<{dateValue: Date}>, ySeats: object, yVotePct: object}} args
 * @returns {{seatsLine: object, votePctLine: object}}
 */
function pollTrackerLineGenerators({ useTimeScale, x, visibleTimeline, ySeats, yVotePct }) {
  const xAt = (_value, index) => (useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index));
  return {
    seatsLine: d3.line().defined((value) => Number.isFinite(value)).x(xAt).y((value) => ySeats(value)),
    votePctLine: d3.line().defined((value) => Number.isFinite(value)).x(xAt).y((value) => yVotePct(value)),
  };
}

/**
 * Paints the background grid. Uses whichever Y axis is currently enabled — extends its tick marks
 * across the full plot width as horizontal grid lines (no tick labels).
 * @param {object} plot - d3 selection for the plot group
 * @param {{seatsEnabled: boolean, ySeats: object, yVotePct: object, innerWidth: number}} args
 * @returns {void}
 */
function pollTrackerDrawGrid(plot, { seatsEnabled, ySeats, yVotePct, innerWidth }) {
  const gridAxis = seatsEnabled ? d3.axisLeft(ySeats).ticks(6) : d3.axisRight(yVotePct).ticks(6);
  plot.append('g')
    .attr('class', POLLTRACKER_AXIS_CLASS)
    .call(gridAxis.tickSize(-innerWidth).tickFormat(''))
    .selectAll('line')
    .attr('class', POLLTRACKER_GRID_LINE_CLASS);
}

/**
 * Paints the X axis with rotated date labels. Tick density caps at ~105px per tick.
 * The 1-entry fallback emits a single tick reading the only date.
 * @param {object} plot
 * @param {{x: object, useTimeScale: boolean, visibleTimeline: Array<{dateKey: string}>, innerHeight: number}} args
 * @returns {void}
 */
function pollTrackerDrawXAxis(plot, { x, useTimeScale, visibleTimeline, innerHeight }) {
  const innerWidth = x.range()[1];
  const maxTicksByWidth = Math.max(4, Math.floor(innerWidth / 105));
  const xAxisGroup = plot.append('g')
    .attr('class', POLLTRACKER_AXIS_CLASS)
    .attr('transform', `translate(0,${innerHeight})`)
    .call(useTimeScale
      ? d3.axisBottom(x)
        .ticks(maxTicksByWidth)
        .tickFormat((value) => d3.timeFormat('%Y-%m-%d')(value))
      : d3.axisBottom(x)
        .tickValues([0])
        .tickFormat(() => visibleTimeline[0]?.dateKey || '')
    );
  xAxisGroup.selectAll('text')
    .style('text-anchor', 'end')
    .attr('dx', '-0.38em')
    .attr('dy', '0.44em')
    .attr('transform', 'rotate(-32)');
}

/**
 * Paints the Y axes and their labels. Left = "Seats" (when seats enabled), right = "Vote %"
 * (when votes enabled), bottom = "Date" always. Vote % ticks formatted with a `%` suffix.
 * @param {object} plot
 * @param {{seatsEnabled: boolean, votePctEnabled: boolean, ySeats: object, yVotePct: object, innerWidth: number, innerHeight: number}} args
 * @returns {void}
 */
function pollTrackerDrawYAxes(plot, { seatsEnabled, votePctEnabled, ySeats, yVotePct, innerWidth, innerHeight }) {
  if (seatsEnabled) {
    plot.append('g')
      .attr('class', POLLTRACKER_AXIS_CLASS)
      .call(d3.axisLeft(ySeats).ticks(7));
    plot.append('text')
      .attr('class', POLLTRACKER_AXIS_LABEL_CLASS)
      .attr('transform', 'rotate(-90)')
      .attr('x', -innerHeight / 2)
      .attr('y', -52)
      .attr('text-anchor', 'middle')
      .text('Seats');
  }
  if (votePctEnabled) {
    plot.append('g')
      .attr('class', POLLTRACKER_AXIS_CLASS)
      .attr('transform', `translate(${innerWidth},0)`)
      .call(d3.axisRight(yVotePct).ticks(7).tickFormat((value) => `${Number(value).toFixed(1)}%`));
    plot.append('text')
      .attr('class', POLLTRACKER_AXIS_LABEL_CLASS)
      .attr('transform', 'rotate(90)')
      .attr('x', innerHeight / 2)
      .attr('y', -(innerWidth + 56))
      .attr('text-anchor', 'middle')
      .text('Vote %');
  }
  plot.append('text')
    .attr('class', POLLTRACKER_AXIS_LABEL_CLASS)
    .attr('x', innerWidth / 2)
    .attr('y', innerHeight + 48)
    .attr('text-anchor', 'middle')
    .text('Date');
}

/**
 * Returns mousemove/mouseleave handlers for the poll tracker tooltip.
 * @param {{svg: object, tooltip: HTMLElement, crosshairLine: object, margin: object, innerWidth: number, width: number, height: number, useTimeScale: boolean, x: function, visibleTimeline: Array}} args
 * @returns {{showTrackerTooltip: function, hideTrackerTooltip: function}}
 */
function pollTrackerTooltipHandlers({ svg, tooltip, crosshairLine, margin, innerWidth, width, height, useTimeScale, x, visibleTimeline }) {
  const showTrackerTooltip = (event, series) => {
    const [pointerX] = d3.pointer(event, svg.node());
    const plotX = pointerX - margin.left;
    if (plotX < 0 || plotX > innerWidth) {
      tooltip.hidden = true;
      return;
    }

    // Find nearest visible timeline entry to the cursor X.
    const index = useTimeScale
      ? (() => {
          const hoveredDate = x.invert(plotX);
          const bisectDate = d3.bisector((entry) => entry.dateValue.getTime()).left;
          const candidate = bisectDate(visibleTimeline, hoveredDate.getTime());
          const leftIndex = Math.max(0, candidate - 1);
          const rightIndex = Math.min(visibleTimeline.length - 1, candidate);
          const leftDistance = Math.abs(visibleTimeline[leftIndex].dateValue.getTime() - hoveredDate.getTime());
          const rightDistance = Math.abs(visibleTimeline[rightIndex].dateValue.getTime() - hoveredDate.getTime());
          return rightDistance < leftDistance ? rightIndex : leftIndex;
        })()
      : 0;
    const xPos = useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index);
    const timelinePoint = visibleTimeline[index];
    const seatsValue = Number(series.seats[index] || 0);
    const votePctValue = Number(series.votePct[index] || 0);
    const partyColour = series.colour || '#9CA3AF';

    crosshairLine
      .attr('x1', xPos)
      .attr('x2', xPos)
      .attr('opacity', 1)
      .raise();

    tooltip.innerHTML = `
      <div class="maps-polltracker-tooltip-party"><span class="maps-predict-grid-swatch" style="background:${partyColour}"></span>${escapeHtml(series.partyName)}</div>
      <div>${timelinePoint?.dateKey || ''}</div>
      <div>Seats: ${formatInt(seatsValue)}</div>
      <div>Vote %: ${formatPct(votePctValue)}%</div>
    `;
    const tooltipX = Math.min(width - 220, Math.max(8, pointerX + 14));
    const tooltipY = Math.min(height - 96, Math.max(8, event.offsetY + 10));
    tooltip.style.left = `${tooltipX}px`;
    tooltip.style.top = `${tooltipY}px`;
    tooltip.hidden = false;
  };
  const hideTrackerTooltip = () => {
    tooltip.hidden = true;
    crosshairLine.attr('opacity', 0);
  };
  return { showTrackerTooltip, hideTrackerTooltip };
}

/**
 * Draws the per-series lines. For each series + each enabled metric we paint TWO paths:
 *   - the visible coloured line (solid for seats, dashed for vote %)
 *   - a thicker invisible "hit area" line on top, which catches mousemove/mouseleave for the tooltip.
 * This keeps thin visible lines easy to hover without making the visible stroke fat.
 * @param {object} plot
 * @param {{selectedSeries: Array, seatsEnabled: boolean, votePctEnabled: boolean, seatsLine: object, votePctLine: object, showTrackerTooltip: function, hideTrackerTooltip: function}} args
 * @returns {void}
 */
function pollTrackerDrawLines(plot, { selectedSeries, seatsEnabled, votePctEnabled, seatsLine, votePctLine, showTrackerTooltip, hideTrackerTooltip }) {
  selectedSeries.forEach((series) => {
    if (seatsEnabled) {
      plot.append('path')
        .datum(series.seats)
        .attr('fill', 'none')
        .attr('stroke', series.colour)
        .attr('stroke-width', POLLTRACKER_LINE_STROKE_WIDTH)
        .attr('d', seatsLine);
      plot.append('path')
        .datum(series.seats)
        .attr('fill', 'none')
        .attr('stroke', 'transparent')
        .attr('stroke-width', POLLTRACKER_HIT_STROKE_WIDTH)
        .attr('d', seatsLine)
        .on('mousemove', (event) => showTrackerTooltip(event, series))
        .on('mouseleave', hideTrackerTooltip);
    }
    if (votePctEnabled) {
      plot.append('path')
        .datum(series.votePct)
        .attr('fill', 'none')
        .attr('stroke', series.colour)
        .attr('stroke-width', POLLTRACKER_LINE_STROKE_WIDTH)
        .attr('stroke-dasharray', '6 4')
        .attr('d', votePctLine);
      plot.append('path')
        .datum(series.votePct)
        .attr('fill', 'none')
        .attr('stroke', 'transparent')
        .attr('stroke-width', POLLTRACKER_HIT_STROKE_WIDTH)
        .attr('d', votePctLine)
        .on('mousemove', (event) => showTrackerTooltip(event, series))
        .on('mouseleave', hideTrackerTooltip);
    }
  });
}

/**
 * Paints the top-right legend explaining the line styles.
 * @param {object} svg
 * @param {{width: number, margin: {top: number, right: number}}} args
 * @returns {void}
 */
function pollTrackerDrawLegend(svg, { width, margin }) {
  const legend = svg.append('g').attr('transform', `translate(${width - margin.right},${margin.top - 2})`);
  legend.append('text')
    .text('Solid = Seats, Dashed = Vote %')
    .attr('fill', '#334155')
    .attr('text-anchor', 'end')
    .style('font', '700 11px "DM Sans", "Segoe UI", sans-serif');
}

/**
 * Renders the party toggle checkboxes for the poll tracker.
 * Pre-selects a fixed set of the main UK parties (Reform, Labour, Conservative, Lib Dems, Green, SNP).
 * Toggling a checkbox re-renders the chart.
 * @returns {void}
 */
function renderPollTrackerPartyControls() {
  // 1. Order parties so the largest (most recent projected seats) appears first;
  //    party name is used as a stable alphabetical tiebreaker on ties.
  const partyRows = Array.from(state.pollTrackerData.seriesByParty.values())
    .sort((a, b) => b.latestSeats - a.latestSeats || a.partyName.localeCompare(b.partyName));

  // 2. Decide which checkboxes start checked from the manifest's default party IDs.
  //    partyKey on each series is String(partyId), so coerce IDs to strings for the lookup.
  const defaultPartyIds = manifest.parliamentFeatures[state.currentParliament]?.polltrackerDefaultParties ?? [];
  const defaultSelectedPartySet = new Set(defaultPartyIds.map(String));

  // 3. Rebuild the toggle list. Each row is a <label> wrapping:
  //    [ <input type=checkbox> ] [ colour swatch ] [ party name text ]
  //    The change handler triggers a chart re-render.
  pollTrackerPartyControls.innerHTML = '';
  partyRows.forEach((row) => {
    const label = document.createElement('label');
    label.className = POLLTRACKER_PARTY_TOGGLE_CLASS;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = row.partyKey;
    checkbox.checked = defaultSelectedPartySet.has(row.partyKey);
    checkbox.addEventListener('change', () => renderPollTrackerChart());

    const swatch = document.createElement('span');
    swatch.className = 'maps-predict-grid-swatch';
    swatch.style.background = row.colour;

    const text = document.createElement('span');
    text.textContent = row.partyName;

    label.append(checkbox, swatch, text);
    pollTrackerPartyControls.appendChild(label);
  });
}

// ─── Pre-fetch ───────────────────────────────────────────────────────────────
//
// Election-type-specific UI hooks that run before results are fetched — sibling to
// the map controls below, but configured up-front rather than rebuilt per load.

// Info button shown only on referendum elections — opens a data-source / methodology explainer.
const dataInfoButton = document.getElementById('mapsDataInfoBtn');

/**
 * Configures election-type-specific UI before election data has loaded.
 * Sets the gains button label and toggles referendum-specific controls.
 * @returns {void}
 */
export function setElectionPreDataFetch() {
  filterGainsButton.textContent = state.getByElectionSeatsSet() ? 'By-elections' : 'Gains';
  filterGainsButton.hidden = state.isReferendumType;
  choroplethVoteShareChangeOption.hidden = state.isReferendumType;
  dataInfoButton.hidden = !state.isReferendumType;
}

// ─── Map control ─────────────────────────────────────────────────────────────
//
// This section holds the filter and choropleth controls — the dropdowns and toggle
// buttons users interact with to narrow visible seats or change the choropleth fill.

// Gains-only toggle button — labelled "Gains" by default, or "By-elections" when the current
// election declares byElectionSeats. Hidden for referendum elections.
const filterGainsButton = document.getElementById('mapsFilterGainsOnly');
// The "vote share change" <option> inside the choropleth-type select — hidden for referendums,
// which only support the simpler vote-share view.
const choroplethVoteShareChangeOption = document.getElementById('mapsChoroplethVoteShareChangeOption');

// Primary party filter — restricts visible seats to those won by the chosen party.
const filterPartySelect = document.getElementById('mapsFilterParty');
// Region filter — restricts visible seats to a single region (e.g. London, Scotland).
const filterRegionSelect = document.getElementById('mapsFilterRegion');
// Second-place filter — paired with filterPartySelect to restrict to seats where the chosen
// party finished second. The wrapping group is hidden when filterParty is 'all'.
const filterSecondPartySelect = document.getElementById('mapsFilterSecondParty');
// Choropleth target party — once a choropleth type is selected, this picks which party's
// vote share / vote share change drives the colour ramp on the map.
const choroplethPartySelect = document.getElementById('mapsChoroplethParty');

/**
 * Rebuilds the option lists for the four election filter/choropleth selects from the
 * currently loaded seat data: filterParty, filterSecondParty, choroplethParty (all sharing
 * the party row set) and filterRegion. Called once per election load, after state has
 * been initialised but before the controls are read back into _state.mapViewState.
 *
 * The party and region row sets come from AppState (mapControlParties /
 * mapControlRegions) — this function is purely the DOM-write side; option content
 * decisions (sorting, deduping, 'all parties...'/'all regions...' default rows) live there.
 *
 * Each select preserves its previously selected value when still available in the new
 * options, otherwise falls back to 'all' — this is the reconciliation step that handles
 * loading an election whose data lacks a party/region the previous selection referenced.
 *
 * @returns {void}
 */
export function setMapControlOptions() {
  /**
   * Replaces a select's options with the given rows, then sets its value back to the
   * previously selected option if it survived the rebuild, falling back to 'all' otherwise.
   * Mutates the select in place; does not fire any change events.
   * @param {HTMLSelectElement} selectEl - Target <select> (assumed to exist).
   * @param {Array<{value: string, label: string}>} rows - Option rows to render.
   * @returns {void}
   */
  const setOptions = (selectEl, rows) => {
    const previousValue = selectEl.value;
    selectEl.innerHTML = '';
    rows.forEach((row) => {
      const option = document.createElement('option');
      option.value = row.value;
      option.textContent = row.label;
      selectEl.appendChild(option);
    });
    const stillAvailable = rows.some((row) => row.value === previousValue);
    selectEl.value = stillAvailable ? previousValue : 'all';
  };

  const partyRows = state.mapControlParties();
  const regionRows = state.mapControlRegions();

  // The three party-keyed selects share one option list; filterRegion uses its own.
  setOptions(filterPartySelect, partyRows);       // primary winner filter
  setOptions(filterSecondPartySelect, partyRows); // second-place finisher filter
  setOptions(choroplethPartySelect, partyRows);   // choropleth target party
  setOptions(filterRegionSelect, regionRows);     // region filter
}

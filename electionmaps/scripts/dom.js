import * as d3 from '../../site/vendor/d3.v7.esm.js';
import {
  feature as topojsonFeature,
  mesh as topojsonMesh,
  merge as topojsonMerge,
} from '../../site/vendor/topojson-client.v3.esm.js';
import { manifest, state } from './state.js';
import { escapeHtml, formatInt, formatPct, formatSigned, deltaClass, getRegionLabel, seatLookupKey, normalizeRegionKey, clampNumber } from './utils.js';

// ─── Page title ───────────────────────────────────────────────────────────────

const MAPS_PAGE_TITLE_SUFFIX = 'Election Maps | Principal Fish';

/**
 * Sets the browser tab title from the current view: poll tracker or election name.
 * @returns {void}
 */
export function renderPageTitle() {
  let label;
  if (state.view === 'polltracker') {
    label = 'Poll tracker';
  } else {
    label = state.currentElection.name;
  }
  const parliament = state.currentParliament;
  const parlLabel = parliament ? parliament[0].toUpperCase() + parliament.slice(1) : null;
  const suffix = parlLabel ? `${parlLabel} | ${MAPS_PAGE_TITLE_SUFFIX}` : MAPS_PAGE_TITLE_SUFFIX;
  document.title = label ? `${label} | ${suffix}` : suffix;
}

// ─── Header ─────────────────────────────────────────────────────────────────

// Page H1 — set to "UK Election Maps · <Parliament>" by renderTitle.
const mapsTitle = document.querySelector('.maps-title');
// Subtitle line below the H1 — election name (or "Poll tracker..." in tracker view),
// optionally suffixed with the poll snippet for prediction elections.
const subtitle = document.getElementById('mapsSubtitle');

/**
 * Updates the title area: the page h1 and subtitle.
 * Called early in init (text omitted — subtitle falls back to election name) and again
 * after results load with the full summary string. Pass error=true on load failure.
 * @param {string} [text=''] - Full subtitle string (e.g. "2024 Election · Labour majority: 174").
 * @param {boolean} [error=false] - When true, subtitle shows a load-failure message.
 * @returns {void}
 */
export function renderHeader(text = '', error = false) {
  renderTitle();
  renderSubtitleText(text, error);
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

// Left-rail nav container — populated with one anchor per election plus the
// Poll tracker link by renderElectionLinks.
const electionList = document.getElementById('mapsElectionList');

/**
 * Updates the left panel: highlights the active parliament tab and rebuilds the election list nav.
 * @param {{ onPredict?: function, onPollTracker?: function }} callbacks - Click handlers for mode buttons.
 * @returns {void}
 */
export function renderLeftBar() {
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
 * Rebuilds the election list nav with one link per election, plus a Poll tracker link
 * when the feature is enabled for the current parliament.
 * @returns {void}
 */
function renderElectionLinks() {
  if (!electionList) return;
  const activeId = state.view === 'election' ? state.currentElection.id : null;

  const features = manifest.parliamentFeatures[state.currentParliament]?.features ?? [];
  const hasPollTracker = features.includes('pollTracker');
  const pollTrackerAnchorId = hasPollTracker ? (state.getPredictAnchorElectionId() ?? null) : null;

  electionList.innerHTML = '';
  let insertedPollTrackerLink = false;
  state.parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = state.viewUrl('election', election.id);
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (hasPollTracker && !insertedPollTrackerLink && election.id === pollTrackerAnchorId) {
      const trackerLink = document.createElement('a');
      trackerLink.href = state.viewUrl('polltracker');
      trackerLink.className = `maps-election-item${state.view === 'polltracker' ? ' active' : ''}`;
      trackerLink.textContent = 'Poll tracker';
      electionList.appendChild(trackerLink);
      insertedPollTrackerLink = true;
    }
  });

  if (hasPollTracker && !insertedPollTrackerLink) {
    const trackerLink = document.createElement('a');
    trackerLink.href = state.viewUrl('polltracker');
    trackerLink.className = `maps-election-item${state.view === 'polltracker' ? ' active' : ''}`;
    trackerLink.textContent = 'Poll tracker';
    electionList.appendChild(trackerLink);
  }
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
    if (state.view === 'polltracker') renderPollTracker();
  });
  inputEl.dataset.wired = 'true';
}

/**
 * Wires the seats and vote-% metric inputs (via wirePollTrackerMetricInput) and all
 * [data-polltracker-range] buttons so clicking a range button updates the active button
 * and re-renders the chart. Guards individual buttons against double-wiring via dataset flag.
 * @returns {void}
 */
function wirePollTrackerControls() {
  wirePollTrackerMetricInput(pollTrackerMetricSeatsInput);
  wirePollTrackerMetricInput(pollTrackerMetricVotesInput);

  document.querySelectorAll(`[${POLLTRACKER_RANGE_ATTR}]`).forEach((button) => {
    if (button.dataset.wired === 'true') return;
    button.addEventListener('click', () => {
      const nextRange = button.getAttribute(POLLTRACKER_RANGE_ATTR) || 'all';
      document.querySelectorAll(`[${POLLTRACKER_RANGE_ATTR}]`).forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate.getAttribute(POLLTRACKER_RANGE_ATTR) === nextRange);
      });
      if (state.view === 'polltracker') renderPollTracker();
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
export function renderPollTracker() {
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
      <div class="maps-polltracker-tooltip-party"><span class="maps-party-swatch" style="background:${partyColour}"></span>${escapeHtml(series.partyName)}</div>
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
    swatch.className = 'maps-party-swatch';
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
  filterGainsButton.textContent = state.currentElection.byElectionSeats ? 'By-elections' : 'Gains';
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
const choroplethLegend = document.getElementById('mapsChoroplethLegend');

// Primary party filter — restricts visible seats to those won by the chosen party.
const filterPartySelect = document.getElementById('mapsFilterParty');
// Region filter — restricts visible seats to a single region (e.g. London, Scotland).
const filterRegionSelect = document.getElementById('mapsFilterRegion');
// Second-place filter — paired with filterPartySelect to restrict to seats where the chosen
// party finished second. The wrapping group is hidden when state.mapFilters.party is 'all'.
const filterSecondPartySelect = document.getElementById('mapsFilterSecondParty');
// Wrapping group for the second-party filter — hidden when no primary party is selected.
const filterSecondPartyGroup = document.getElementById('mapsFilterSecondPartyGroup');
// Majority range filter inputs — restrict visible seats to those within the min/max % range.
const filterMajorityMinInput = document.getElementById('mapsFilterMajorityMin');
const filterMajorityMaxInput = document.getElementById('mapsFilterMajorityMax');
// Choropleth type select — 'none', 'vote-share', or 'vote-share-change'.
const choroplethTypeSelect = document.getElementById('mapsChoroplethType');
// Choropleth target party — once a choropleth type is selected, this picks which party's
// vote share / vote share change drives the colour ramp on the map.
const choroplethPartySelect = document.getElementById('mapsChoroplethParty');
// Reset buttons — restore all filters or choropleths to defaults.
const filtersResetButton = document.getElementById('mapsFiltersReset');
const choroplethsResetButton = document.getElementById('mapsChoroplethsReset');

/**
 * Rebuilds the option lists for the four election filter/choropleth selects from the
 * currently loaded seat data: filterParty, filterSecondParty, choroplethParty (all sharing
 * the party row set) and filterRegion. Called once per election load, after state has
 * been initialised but before the controls are read back into state.mapFilters / state.mapChoropleths.
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
export function renderMapControlOptions() {
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

/**
 * Pushes the current state.mapFilters and state.mapChoropleths values into the DOM
 * filter/choropleth inputs and toggles second-party group visibility.
 * @returns {void}
 */
export function syncMapControlInputsFromState() {
  filterPartySelect.value = state.mapFilters.party;
  filterRegionSelect.value = state.mapFilters.region;

  const showSecondPlaceFilter = state.mapFilters.party !== 'all';
  filterSecondPartyGroup.hidden = !showSecondPlaceFilter;
  if (!showSecondPlaceFilter) {
    state.mapFilters.secondParty = 'all';
  }
  filterSecondPartySelect.value = state.mapFilters.secondParty;

  filterMajorityMinInput.value = String(state.mapFilters.majorityMin);
  filterMajorityMaxInput.value = String(state.mapFilters.majorityMax);
  filterGainsButton.classList.toggle('is-active', state.mapFilters.gainsOnly);

  choroplethTypeSelect.value = state.mapChoropleths.type;
  choroplethPartySelect.value = state.mapChoropleths.party;
}

/**
 * Reads the DOM filter/choropleth inputs into state.mapFilters and state.mapChoropleths,
 * normalizing and clamping values, then syncs the inputs back.
 * @returns {void}
 */
export function syncMapControlStateFromInputs() {
  state.mapFilters.party = filterPartySelect.value || 'all';
  state.mapFilters.region = filterRegionSelect.value || 'all';
  if (state.mapFilters.party === 'all') {
    state.mapFilters.secondParty = 'all';
  } else {
    state.mapFilters.secondParty = filterSecondPartySelect.value || 'all';
  }
  state.mapFilters.majorityMin = clampNumber(filterMajorityMinInput.value, 0, 100);
  state.mapFilters.majorityMax = clampNumber(filterMajorityMaxInput.value, 0, 100);
  if (state.mapFilters.majorityMin > state.mapFilters.majorityMax) {
    const swap = state.mapFilters.majorityMin;
    state.mapFilters.majorityMin = state.mapFilters.majorityMax;
    state.mapFilters.majorityMax = swap;
  }

  state.mapChoropleths.type = choroplethTypeSelect.value || 'none';
  state.mapChoropleths.party = choroplethPartySelect.value || 'all';

  syncMapControlInputsFromState();
}

/**
 * Attaches change handlers to all filter and choropleth selects/inputs so any change reads
 * state and re-renders the map. Also wires the gains toggle, reset-filters, and
 * reset-choropleths buttons. Guards against double-wiring via dataset flag.
 * @returns {void}
 */
function wireMapViewControls() {
  if (filterPartySelect?.dataset.wired === 'true') return;

  /** Reads all filter/choropleth input values into state and re-renders the map. */
  const applyFromInputs = () => {
    syncMapControlStateFromInputs();
    drawMap(true);
  };

  [
    filterPartySelect,
    filterRegionSelect,
    filterSecondPartySelect,
    filterMajorityMinInput,
    filterMajorityMaxInput,
    choroplethTypeSelect,
    choroplethPartySelect,
  ].forEach((input) => {
    if (!input) return;
    input.addEventListener('change', applyFromInputs);
  });

  if (filterGainsButton) {
    filterGainsButton.addEventListener('click', () => {
      state.mapFilters.gainsOnly = !state.mapFilters.gainsOnly;
      syncMapControlInputsFromState();
      drawMap(true);
    });
  }

  if (filtersResetButton) {
    filtersResetButton.addEventListener('click', () => {
      state.resetFilters();
      syncMapControlInputsFromState();
      closeAllPopups();
      drawMap(true);
    });
  }

  if (choroplethsResetButton) {
    choroplethsResetButton.addEventListener('click', () => {
      state.resetChoropleths();
      syncMapControlInputsFromState();
      closeAllPopups();
      drawMap(true);
    });
  }

  if (filterPartySelect) filterPartySelect.dataset.wired = 'true';
}

// ─── Vote totals ─────────────────────────────────────────────────────────────

const voteTotalsTabNav = document.getElementById('mapsVoteTotalsTabNav');
const voteTotalsBody = document.getElementById('mapsVoteTotalsBody');
const voteTotalsTable = document.getElementById('mapsVoteTotalsTable');
const voteTotalsToggle = document.getElementById('mapsVoteTotalsToggle');

/**
 * Syncs the three column-visibility CSS classes on the vote-totals table to state.voteTotals.columns.
 * Adds `hide-vote-total-col`, `hide-vote-pct-col`, or `hide-comparison-cols` when the corresponding
 * column type is toggled off, so CSS hides the relevant <td> elements without re-rendering rows.
 */
/**
 * Returns a sorted copy of party rows according to state.voteTotals.sort (party name alpha, or numeric column with label tiebreak).
 * @param {Array<object>} rows - Party summary rows with a `party` key and numeric fields matching sort key names.
 * @returns {Array<object>} New sorted array of party rows.
 */
function sortPartyRows(rows) {
  const multiplier = state.voteTotals.sort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (state.voteTotals.sort.key === 'party') {
      return multiplier * manifest.labelParty(a.party).localeCompare(manifest.labelParty(b.party));
    }

    const av = Number(a[state.voteTotals.sort.key] || 0);
    const bv = Number(b[state.voteTotals.sort.key] || 0);
    if (av !== bv) return multiplier * (av - bv);
    const voteDiff = Number(b.votePct || 0) - Number(a.votePct || 0);
    if (voteDiff !== 0) return voteDiff;
    return manifest.labelParty(a.party).localeCompare(manifest.labelParty(b.party));
  });
}

/**
 * Renders the vote totals panel: first syncs tab active classes to state.voteTotals.mode, then
 * rebuilds the table body with one row per party showing seats, seat delta, vote count, vote pct,
 * and vote-pct delta. Truncates to 7 rows unless state.voteTotals.expanded is true.
 *
 * Defaults to state.filteredSeatsSummary / state.filteredSeatsComparisonSummary so callers in the
 * normal render path need no arguments. Predict mode passes explicit summaries (projectedSummary /
 * baselineSummary) which differ from the filtered state values.
 *
 * Hidden parties (e.g. Alba on the Holyrood map) are read from state.mapConfig.hiddenVoteTotalsParties.
 *
 * @param {{parties: Array<object>, totalVotes: number}} [summary] - Summary to render; defaults to state.filteredSeatsSummary.
 * @param {{parties: Array<object>, totalVotes: number}|null} [comparisonSummary] - Comparison for delta columns; defaults to state.filteredSeatsComparisonSummary.
 * @returns {void}
 */
export function renderVoteTotals(
  summary = state.filteredSeatsSummary,
  comparisonSummary = state.filteredSeatsComparisonSummary
) {
  voteTotalsTabNav.querySelectorAll('[data-vote-tab]').forEach((b) => {
    b.classList.toggle('active', b.dataset.voteTab === state.voteTotals.mode);
  });

  voteTotalsBody.innerHTML = '';
  voteTotalsTable.classList.toggle('hide-vote-total-col', !state.voteTotalsColumnVisible('votes'));
  voteTotalsTable.classList.toggle('hide-vote-pct-col', !state.voteTotalsColumnVisible('votePct'));

  const hiddenParties = new Set(state.mapConfig?.hiddenVoteTotalsParties ?? []);
  const showComparison = Boolean(comparisonSummary);
  voteTotalsTable.classList.toggle('hide-comparison-cols', !showComparison);
  const comparisonByParty = new Map();
  if (comparisonSummary) {
    comparisonSummary.parties.forEach((partyRow) => {
      const votePct = comparisonSummary.totalVotes > 0 ? (partyRow.votes / comparisonSummary.totalVotes) * 100 : 0;
      comparisonByParty.set(partyRow.party, {
        seats: Number(partyRow.seats || 0),
        votePct,
      });
    });
  }

  const rows = summary.parties.map((partyRow) => {
    const votePct = summary.totalVotes > 0 ? (partyRow.votes / summary.totalVotes) * 100 : 0;
    const comparison = comparisonByParty.get(partyRow.party) || { seats: 0, votePct: 0 };
    return {
      ...partyRow,
      votePct,
      seatsDelta: Number(partyRow.seats || 0) - comparison.seats,
      votePctDelta: votePct - comparison.votePct,
    };
  });

  const sortedRows = sortPartyRows(rows).filter((r) => !hiddenParties.has(r.party));
  const visibleRows = state.voteTotals.expanded ? sortedRows : sortedRows.slice(0, 7);

  if (voteTotalsToggle) {
    const canExpand = sortedRows.length > 7;
    voteTotalsToggle.hidden = !canExpand;
    if (canExpand) {
      voteTotalsToggle.textContent = state.voteTotals.expanded ? 'Show fewer' : 'Show all';
    }
  }

  visibleRows.forEach((partyRow) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="maps-party-cell"><span class="maps-party-swatch" style="background:${manifest.colourParty(partyRow.party)}"></span>${manifest.labelParty(partyRow.party)}</span></td>
      <td>${formatInt(partyRow.seats)}</td>
      <td class="comparison-col ${showComparison ? deltaClass(partyRow.seatsDelta) : ''}">${showComparison ? formatSigned(partyRow.seatsDelta, 0) : ''}</td>
      <td class="vote-total-col">${formatInt(partyRow.votes)}</td>
      <td class="vote-pct-col">${formatPct(partyRow.votePct)}</td>
      <td class="comparison-col vote-pct-comparison-col ${showComparison ? deltaClass(partyRow.votePctDelta) : ''}">${showComparison ? formatSigned(partyRow.votePctDelta, 2) : ''}</td>
    `;
    voteTotalsBody.appendChild(tr);
  });
}

/**
 * Rebuilds the vote-totals tab nav from state.mapConfig.voteTotalsViews. Hides the nav entirely
 * when only one view exists (Westminster elections have no constituency/list split). Each button
 * sets state.voteTotals.mode on click, recomputes the filtered totals via
 * state.recomputeVoteTotalsForMode(), then re-renders the whole panel via renderVoteTotals().
 * Called once per election load from renderMapInit.
 * @returns {void}
 */
function initVoteTotalsTabs() {
  voteTotalsTabNav.innerHTML = '';
  const views = state.mapConfig.voteTotalsViews;
  voteTotalsTabNav.hidden = views.length <= 1;
  views.forEach((view) => {
    const btn = document.createElement('button');
    btn.className = `maps-vote-tab${view.id === state.voteTotals.mode ? ' active' : ''}`;
    btn.dataset.voteTab = view.id;
    btn.textContent = view.label;
    btn.addEventListener('click', () => {
      state.voteTotals.mode = view.id;
      state.recomputeVoteTotalsForMode();
      renderVoteTotals();
    });
    voteTotalsTabNav.appendChild(btn);
  });
}

/**
 * Wires the expand/collapse button that toggles the vote-totals table between the top-7 truncation
 * and the full party list. Flips state.voteTotals.expanded then re-renders via renderVoteTotals().
 * @returns {void}
 */
export function wireVoteTotalsToggle() {
  voteTotalsToggle.addEventListener('click', () => {
    state.voteTotals.expanded = !state.voteTotals.expanded;
    if (!state.filteredSeatsSummary) return;
    renderVoteTotals();
  });
}

// ─── Seat search ─────────────────────────────────────────────────────────────

// ── Shared ───────────────────────────────────────────────────────────────────

/**
 * Resolves a search query to a seat name (exact → starts-with → contains), zooms the map,
 * selects the list row, and opens the popup.
 * @param {string} query - Raw search string as entered by the user.
 * @returns {void}
 */
function selectSeatBySearchQuery(query) {
  const rawQuery = String(query || '').trim();
  if (!rawQuery) return;

  // 1. Exact normalised-key match — handles case and punctuation differences.
  const directKey = seatLookupKey(rawQuery);
  let seatName = state.currentSeatNameByKey.get(directKey) || null;

  // 2. Fall back to starts-with then contains, both case-insensitive.
  if (!seatName) {
    const queryLower = rawQuery.toLowerCase();
    seatName = Array.from(state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().startsWith(queryLower))
      || Array.from(state.currentSeatNameByKey.values()).find((name) => name.toLowerCase().includes(queryLower))
      || null;
  }

  if (!seatName) {
    return;
  }

  // Re-derive the key from the resolved seat name (not the raw query) for a correct map lookup.
  const seatKey = seatLookupKey(seatName);
  const zoomed = mapInteraction.zoomToSeat(seatName);
  // Only update the list row and open the popup if the zoom succeeded —
  // the seat may be filtered out or the map not yet ready.
  if (zoomed) {
    setSelectedSeatRowByKey(seatKey);
    renderSeatPopup(seatName);
    seatSearchInput.value = seatName;
    return;
  }
}

// ── Postcode search ───────────────────────────────────────────────────────────

const postcodeSearchInput = document.getElementById('maps-postcode-search');
const postcodeSearchGroup = postcodeSearchInput?.closest('.maps-toolbar-group-postcode') ?? null;
const postcodeWarningBtn = document.getElementById('mapsPostcodeWarningBtn');
const postcodeWarningPanel = document.getElementById('mapsPostcodeWarningPanel');
let postcodeErrorTimeout = null;

// Maps old 2021 Holyrood constituency names (as returned by postcodes.io) to their
// 2026 boundary equivalents. Used as a fallback when a returned name has no match
// in the current seat data. Where two old seats merged into one, both map to the new
// combined name — best-guess only, since the boundary changed at the postcode level.
const HOLYROOD_2021_TO_2026_NAME = {
  'Aberdeen South and North Kincardine': 'Aberdeen Deeside and North Kincardine',
  'Airdrie and Shotts': 'Airdrie',
  'East Lothian': 'East Lothian Coast and Lammermuirs',
  'Edinburgh Eastern': 'Edinburgh Eastern, Musselburgh and Tranent',
  'Edinburgh Northern and Leith': 'Edinburgh North Eastern and Leith',
  'Edinburgh Pentlands': 'Edinburgh South Western',
  'Edinburgh Western': 'Edinburgh North Western',
  'Falkirk East': 'Falkirk East and Linlithgow',
  'Glasgow Cathcart': 'Glasgow Cathcart and Pollok',
  'Glasgow Kelvin': 'Glasgow Kelvin and Maryhill',
  'Glasgow Maryhill and Springburn': 'Glasgow Kelvin and Maryhill',
  'Glasgow Pollok': 'Glasgow Cathcart and Pollok',
  'Glasgow Provan': 'Glasgow Easterhouse and Springburn',
  'Glasgow Shettleston': 'Glasgow Baillieston and Shettleston',
  'Greenock and Inverclyde': 'Inverclyde',
  'Linlithgow': 'Falkirk East and Linlithgow',
  'Midlothian North and Musselburgh': 'Midlothian North',
  'North East Fife': 'Fife North East',
  'Renfrewshire North and West': 'Renfrewshire North and Cardonald',
  'Renfrewshire South': 'Renfrewshire West and Levern Valley',
  'Rutherglen': 'Rutherglen and Cambuslang',
};

/**
 * Shows or hides the postcode search group based on whether the current election supports
 * postcode lookup. Clears the input and any error state when hiding.
 */
function initPostcodeSearch() {
  postcodeSearchGroup.hidden = !state.mapConfig?.postcodeSupported;
  // postcodes.io returns 2021 Scottish Parliament constituencies, but the Holyrood map uses
  // 2026 boundaries — the warning icon toggles a panel listing the affected seats.
  const isHolyrood = state.mapConfig?.name?.startsWith('holyrood') ?? false;
  postcodeWarningBtn.hidden = !isHolyrood;
  if (!isHolyrood) postcodeWarningPanel.hidden = true;
}

/**
 * Flashes an error message inside the postcode input for 2 seconds, then clears the
 * input so the placeholder is shown again. The input is made readonly during the flash
 * to prevent accidental edits. Cancels any in-flight error flash before starting a new one.
 * @param {string} msg - The error text to display in the input.
 * @returns {void}
 */
function showPostcodeError(msg) {
  // Cancel any in-flight flash before starting a new one to avoid overlapping timers.
  clearPostcodeError();
  // Display the error text in the input and lock it readonly so the user can't type over it.
  postcodeSearchInput.value = msg;
  postcodeSearchInput.readOnly = true;
  postcodeSearchInput.classList.add('is-postcode-error');
  // Store the timer ID so clearPostcodeError can cancel it if the user focuses before 2 s.
  postcodeErrorTimeout = window.setTimeout(() => {
    postcodeSearchInput.readOnly = false;
    postcodeSearchInput.value = '';
    postcodeSearchInput.classList.remove('is-postcode-error');
    postcodeErrorTimeout = null;
  }, 2000);
}

/**
 * Cancels any active postcode error flash and removes the error style.
 * Does not restore the input value — caller is responsible for that if needed.
 * @returns {void}
 */
function clearPostcodeError() {
  // Cancel the pending auto-clear timer.
  if (postcodeErrorTimeout) {
    clearTimeout(postcodeErrorTimeout);
    postcodeErrorTimeout = null;
  }
  // Restore the input to its normal editable state.
  if (postcodeSearchInput) {
    postcodeSearchInput.readOnly = false;
    postcodeSearchInput.classList.remove('is-postcode-error');
  }
}

/**
 * Looks up a postcode via the postcodes.io API and returns the constituency name,
 * or null if the postcode is not found or the current map does not support lookup.
 * Selects the Westminster or Scottish endpoint based on the current election's mapId.
 * @param {string} postcode - The raw postcode string entered by the user.
 * @returns {Promise<string|null>} The constituency name, or null on failure.
 */
async function lookupPostcode(postcode) {
  if (!state.mapConfig?.postcodeSupported) return null;

  // Strip all whitespace then re-insert the canonical space before the inward code
  // (always the last 3 characters). Both endpoints require this format.
  const stripped = postcode.trim().toUpperCase().replace(/\s+/g, '');
  const normalised = stripped.length >= 5 ? `${stripped.slice(0, -3)} ${stripped.slice(-3)}` : stripped;

  const mapName = state.mapConfig?.name ?? null;
  let url = '';
  let resultProperty = '';

  switch (mapName) {
    case 'holyrood-2026':
      url = `https://api.postcodes.io/scotland/postcodes/${encodeURIComponent(normalised)}`;
      resultProperty = 'scottish_parliamentary_constituency';
      break;
    case 'westminster-2024':
      url = `https://api.postcodes.io/postcodes/${encodeURIComponent(normalised)}`;
      resultProperty = 'parliamentary_constituency_2024';
      break;
    default:
      return null;
  }

  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json();
    const rawName = data?.result?.[resultProperty] ?? null;

    if (!rawName) return null;

    // Normalise accented characters to ASCII so names like "Ynys Môn" match
    // our seat data which stores the unaccented form "Ynys Mon".
    const constituencyName = rawName.normalize('NFD').replace(/[\u0300-\u036f]/g, '');

    // If the returned name has no match in the current seat index, try the
    // Holyrood 2021→2026 boundary mapping as a best-guess fallback.
    // Only applied on Holyrood to avoid false rewrites on Westminster lookups.
    const seatKey = seatLookupKey(constituencyName);
    if (!state.currentSeatNameByKey.has(seatKey) && mapName === 'holyrood-2026') {
      const mapped = HOLYROOD_2021_TO_2026_NAME[constituencyName] ?? null;
      if (mapped) return mapped;
    }

    return constituencyName;
  } catch {
    return null;
  }
}

/**
 * Attaches event listeners to the postcode search input. On Enter or blur, looks up the
 * postcode and zooms to the matched constituency. Disables the input during the fetch,
 * shows an inline error on failure, and deduplicates blur-after-Enter submissions.
 * Guards against double-wiring via dataset flag.
 * @returns {void}
 */
function wirePostcodeSearch() {
  if (postcodeSearchInput.dataset.wired === 'true') return;

  let lastSubmittedPostcode = '';

  /**
   * Reads the postcode input, runs the lookup, and selects the resolved seat.
   * Deduplicates against the last submitted value to avoid double-fetching on blur after Enter.
   * @returns {void}
   */
  const submitPostcode = async () => {
    const query = postcodeSearchInput.value.trim();
    if (!query || query === lastSubmittedPostcode) return;
    lastSubmittedPostcode = query;
    postcodeSearchInput.disabled = true;
    clearPostcodeError();
    const constituencyName = await lookupPostcode(query);
    postcodeSearchInput.disabled = false;
    if (constituencyName) {
      selectSeatBySearchQuery(constituencyName);
    } else {
      showPostcodeError('Postcode not found');
    }
  };

  postcodeSearchInput.addEventListener('focus', () => {
    // If the error flash is showing, dismiss it and restore the original value
    // so the user can immediately retype without clearing "Postcode not found" manually.
    if (postcodeSearchInput.readOnly) {
      clearPostcodeError();
      postcodeSearchInput.value = '';
      lastSubmittedPostcode = '';
    }
  });
  postcodeSearchInput.addEventListener('input', () => {
    // Reset the dedup guard so the same value can be re-submitted after editing.
    lastSubmittedPostcode = '';
    clearPostcodeError();
  });
  postcodeSearchInput.addEventListener('keydown', (event) => {
    // Enter submits immediately without waiting for blur.
    if (event.key === 'Enter') {
      event.preventDefault();
      submitPostcode();
    }
  });
  postcodeSearchInput.addEventListener('blur', () => {
    // Brief delay absorbs Enter-then-blur so submitPostcode isn't called twice in quick succession.
    window.setTimeout(submitPostcode, 120);
  });

  postcodeSearchInput.dataset.wired = 'true';
}

// ── Seat name search ──────────────────────────────────────────────────────────

const seatSearchInput = document.getElementById('maps-seat-search');
let seatSearchMenuEl = null;
let seatSearchSuggestionIndex = -1;
let seatSearchSuggestions = [];

const MAX_SEAT_SEARCH_SUGGESTIONS = 10;

/**
 * Creates the autocomplete dropdown menu element adjacent to the seat search input if it
 * doesn't exist yet. Returns the element or null if the input is absent.
 * @returns {HTMLElement|null} The autocomplete menu element, or null if the seat search input is not in the DOM.
 */
function ensureSeatSearchMenu() {
  // Return the cached element if it already exists, or bail if the input isn't in the DOM.
  if (seatSearchMenuEl || !seatSearchInput) return seatSearchMenuEl;
  // Mount inside the toolbar group so CSS can position the dropdown relative to it.
  const searchGroup = seatSearchInput.closest('.maps-toolbar-group-search') || seatSearchInput.parentElement;

  const menu = document.createElement('div');
  menu.className = 'maps-seat-search-menu';
  menu.hidden = true;
  // ARIA: role=listbox pairs with role=option on each item for keyboard accessibility.
  menu.setAttribute('role', 'listbox');
  menu.id = 'mapsSeatSearchMenu';
  searchGroup.appendChild(menu);
  seatSearchMenuEl = menu;
  return seatSearchMenuEl;
}

/**
 * Hides the autocomplete dropdown and clears the keyboard suggestion index.
 * @returns {void}
 */
function hideSeatSearchSuggestions() {
  // Reset the keyboard cursor so the next open starts with no item highlighted.
  seatSearchSuggestionIndex = -1;
  if (!seatSearchMenuEl) return;
  seatSearchMenuEl.hidden = true;
  // Wipe content so a stale list is never shown if the menu is re-opened immediately.
  seatSearchMenuEl.innerHTML = '';
}

/**
 * Populates the autocomplete dropdown with up to MAX_SEAT_SEARCH_SUGGESTIONS seat names
 * matching query (starts-with first, then contains).
 * @param {string} [query=''] - Search string to match against; empty string shows all names up to the limit.
 * @returns {void}
 */
function showSeatSearchSuggestions(query = '') {
  const menu = ensureSeatSearchMenu();
  if (!menu) return;

  // An empty query shows all names up to the limit — supports the focus-opens-list UX.
  const queryText = String(query || '').trim().toLowerCase();
  const startsWithMatches = [];
  const includesMatches = [];
  // Starts-with matches rank above contains matches for more intuitive ordering.
  state.seatSearchNames.forEach((name) => {
    const lowerName = name.toLowerCase();
    if (!queryText || lowerName.startsWith(queryText)) {
      startsWithMatches.push(name);
      return;
    }
    if (lowerName.includes(queryText)) includesMatches.push(name);
  });

  // Merge the two tiers and truncate — starts-with results always appear before contains.
  seatSearchSuggestions = [...startsWithMatches, ...includesMatches].slice(0, MAX_SEAT_SEARCH_SUGGESTIONS);
  seatSearchSuggestionIndex = -1;
  menu.innerHTML = '';

  if (!seatSearchSuggestions.length) {
    menu.hidden = true;
    return;
  }

  seatSearchSuggestions.forEach((name, index) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'maps-seat-search-item';
    item.textContent = name;
    item.setAttribute('role', 'option');
    item.dataset.index = String(index);
    // Prevent mousedown from blurring the input before the click handler fires.
    item.addEventListener('mousedown', (event) => {
      event.preventDefault();
    });
    // Commit the selection: fill the input, close the menu, and run the search.
    item.addEventListener('click', () => {
      seatSearchInput.value = name;
      hideSeatSearchSuggestions();
      selectSeatBySearchQuery(name);
    });
    menu.appendChild(item);
  });

  menu.hidden = false;
}

/**
 * Updates the keyboard-active (is-active) class on suggestion items to reflect
 * seatSearchSuggestionIndex.
 * @returns {void}
 */
function updateSeatSearchHighlight() {
  if (!seatSearchMenuEl) return;
  // Toggle is-active on each item to reflect the current keyboard cursor position.
  const options = seatSearchMenuEl.querySelectorAll('.maps-seat-search-item');
  options.forEach((option) => {
    const index = Number(option.dataset.index);
    option.classList.toggle('is-active', index === seatSearchSuggestionIndex);
  });
}

/**
 * Attaches all seat search event listeners: focus/input show the autocomplete dropdown,
 * change/blur submit the query, arrow keys navigate suggestions, Enter selects, Escape closes,
 * and an outside click dismisses the menu. Guards against double-wiring via dataset flag.
 * @returns {void}
 */
function wireSeatSearch() {
  if (seatSearchInput.dataset.wired === 'true') return;

  let lastSubmittedQuery = '';
  /**
   * Reads the current search input value and calls selectSeatBySearchQuery, deduplicating
   * against the last submitted query.
   * @returns {void}
   */
  const submitSearch = () => {
    const query = String(seatSearchInput.value || '').trim();
    if (!query || query === lastSubmittedQuery) return;
    lastSubmittedQuery = query;
    selectSeatBySearchQuery(query);
  };

  seatSearchInput.addEventListener('focus', () => {
    showSeatSearchSuggestions(seatSearchInput.value);
  });
  seatSearchInput.addEventListener('input', () => {
    showSeatSearchSuggestions(seatSearchInput.value);
  });
  seatSearchInput.addEventListener('change', submitSearch);
  seatSearchInput.addEventListener('blur', () => {
    window.setTimeout(() => {
      hideSeatSearchSuggestions();
      submitSearch();
    }, 120);
  });
  seatSearchInput.addEventListener('keydown', (event) => {
    // ArrowDown: open the list if empty, then advance the cursor.
    if (event.key === 'ArrowDown') {
      if (!seatSearchSuggestions.length) {
        showSeatSearchSuggestions(seatSearchInput.value);
      }
      if (!seatSearchSuggestions.length) return;
      event.preventDefault();
      seatSearchSuggestionIndex = Math.min(seatSearchSuggestionIndex + 1, seatSearchSuggestions.length - 1);
      updateSeatSearchHighlight();
      return;
    }

    // ArrowUp: move the cursor back, clamped to 0.
    if (event.key === 'ArrowUp') {
      if (!seatSearchSuggestions.length) return;
      event.preventDefault();
      seatSearchSuggestionIndex = Math.max(seatSearchSuggestionIndex - 1, 0);
      updateSeatSearchHighlight();
      return;
    }

    // Enter: commit the highlighted suggestion (if any) then run the search.
    if (event.key === 'Enter') {
      event.preventDefault();
      if (seatSearchSuggestionIndex >= 0 && seatSearchSuggestionIndex < seatSearchSuggestions.length) {
        const selectedName = seatSearchSuggestions[seatSearchSuggestionIndex];
        seatSearchInput.value = selectedName;
      }
      hideSeatSearchSuggestions();
      submitSearch();
      return;
    }

    // Escape: close the list without submitting.
    if (event.key === 'Escape') {
      hideSeatSearchSuggestions();
    }
  });
  // Dismiss the menu when the user clicks outside both the input and the dropdown.
  document.addEventListener('click', (event) => {
    if (!(event.target instanceof Node)) return;
    if (seatSearchInput.contains(event.target)) return;
    if (seatSearchMenuEl?.contains(event.target)) return;
    hideSeatSearchSuggestions();
  });

  seatSearchInput.dataset.wired = 'true';
}

// ─── Map ─────────────────────────────────────────────────────────────────────

const regionCard = document.getElementById('mapsRegionCard');
const regionTableBody = document.getElementById('mapsRegionTableBody');
const popupOverlay = document.getElementById('mapsPopupOverlay');

/** Closes all popup panels and hides the backdrop overlay. */
function closeAllPopups() {
  document.querySelectorAll('.maps-control-popup').forEach((p) => { p.hidden = true; });
  if (popupOverlay) popupOverlay.hidden = true;
}

/**
 * Attaches click handlers to all [data-popup-action] buttons. 'toggle' opens the target panel
 * and closes all others (plus backdrop); 'close' closes all panels. On mobile the backdrop
 * overlay is shown/hidden alongside the panel. Guards against double-wiring via dataset flag.
 *
 * Covers the four map control popups: Filters, Choropleths, Data info (referendum only),
 * and Postcode accuracy warning.
 * @returns {void}
 */
function wirePopupPanels() {
  if (popupOverlay && popupOverlay.dataset.wired !== 'true') {
    popupOverlay.addEventListener('click', closeAllPopups);
    popupOverlay.dataset.wired = 'true';
  }

  document.querySelectorAll('[data-popup-action]').forEach((button) => {
    if (button.dataset.wired === 'true') return;

    button.addEventListener('click', () => {
      const action = button.getAttribute('data-popup-action');
      const targetId = button.getAttribute('data-popup-target');
      const panel = targetId ? document.getElementById(targetId) : null;
      if (!panel) return;

      if (action === 'close') {
        closeAllPopups();
        return;
      }

      if (action === 'toggle') {
        const willShow = panel.hidden;
        closeAllPopups();
        panel.hidden = !willShow;
        if (popupOverlay) popupOverlay.hidden = !willShow;
      }
    });

    button.dataset.wired = 'true';
  });
}

/**
 * Attaches click handlers to all [data-map-action] buttons: zoom-in (×1.2), zoom-out (×0.83),
 * reset-zoom (restore default transform), and reset-view (zoom reset + clear all filters/choropleths).
 * @returns {void}
 */
function wireMapInteractions() {
  document.querySelectorAll('[data-map-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.getAttribute('data-map-action');
      if (action === 'zoom-in') mapInteraction.zoomBy(1.2);
      if (action === 'zoom-out') mapInteraction.zoomBy(0.83);
      if (action === 'reset-zoom') mapInteraction.reset();
      if (action === 'reset-view') {
        mapInteraction.reset();
        state.resetFilters();
        state.resetChoropleths();
        syncMapControlInputsFromState();
        closeAllPopups();
        drawMap();
      }
    });
  });
}

/**
 * Builds the list-region table overlay from `state.listRegionSummary`.
 *
 * Hides the region card and returns early if there is no summary or it is empty
 * (non-list elections have no regions).
 *
 * For each region in the summary, renders a table row containing:
 *  - A name cell with the human-readable region label.
 *  - A seats cell with a proportional colour bar — one segment per party that
 *    won at least one seat, widths scaled to that party's share of total seats,
 *    labelled with the count when ≥ 2. Rows with no data are skipped entirely.
 *
 * Clicking a row flashes the region boundary on the map and opens the region popup.
 *
 * After all rows are appended, unhides the card and wires the collapse toggle on
 * the table header, resetting to expanded state on each call.
 *
 * @returns {void}
 */
function initRegionTable() {
  const regionSummary = state.listRegionSummary;
  // Non-list elections have no region summary — hide the card and bail.
  if (!regionSummary || regionSummary.size === 0) {
    regionCard.hidden = true;
    return;
  }

  // Clear any previously rendered rows before rebuilding.
  regionTableBody.innerHTML = '';

  regionSummary.forEach((data, regionKey) => {
    // Skip regions with no data (shouldn't happen, but guard against malformed summaries).
    if (!data) return;

    // Sort parties by seat count descending and compute total for bar width scaling.
    const entries = Object.entries(data.seatsByParty)
      .filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1]);
    const total = entries.reduce((s, [, n]) => s + n, 0);

    // Build the row — clicking flashes the region on the map and opens the popup.
    const tr = document.createElement('tr');
    tr.className = 'maps-region-table-row';
    tr.addEventListener('click', () => {
      mapInteraction.flashRegion(regionKey);
      renderRegionPopup(regionKey, data);
    });

    // First cell: human-readable region name.
    const tdName = document.createElement('td');
    tdName.className = 'maps-region-table-name';
    tdName.textContent = getRegionLabel(regionKey, state.currentRegionLabelsByKey);
    tr.appendChild(tdName);

    // Second cell: proportional colour bar, one segment per party.
    const tdSeats = document.createElement('td');
    tdSeats.className = 'maps-region-table-seats';

    if (total > 0) {
      const barEl = document.createElement('div');
      barEl.className = 'maps-region-table-bar';
      entries.forEach(([party, count]) => {
        const seg = document.createElement('div');
        seg.className = 'maps-region-table-bar-seg';
        seg.style.width = `${(count / total) * 100}%`;
        seg.style.background = manifest.colourParty(party);
        // Only label the segment if it's wide enough to fit the number.
        if (count >= 2) seg.textContent = count;
        barEl.appendChild(seg);
      });
      tdSeats.appendChild(barEl);
    }

    tr.appendChild(tdSeats);
    regionTableBody.appendChild(tr);
  });

  // All rows built — show the card.
  regionCard.hidden = false;

  // Reset to expanded and wire the header toggle button (mobile-only — toggle and collapsed styles are gated behind max-width: 640px in styles.css).
  const toggleBtn = document.getElementById('mapsRegionCardToggle');
  regionCard.classList.remove('maps-region-card--collapsed');
  if (toggleBtn) {
    toggleBtn.textContent = '▼';
    const onToggle = () => {
      const collapsed = regionCard.classList.toggle('maps-region-card--collapsed');
      toggleBtn.textContent = collapsed ? '▶' : '▼';
    };
    const thead = regionCard.querySelector('thead');
    if (thead) thead.onclick = onToggle;
  }
}

// ─── Seat popup ──────────────────────────────────────────────────────────────

const seatPopup = document.getElementById('mapsSeatPopup');
const seatPopupTitle = document.getElementById('mapsSeatPopupTitle');
const seatPopupMeta = document.getElementById('mapsSeatPopupMeta');
const seatPopupList = document.getElementById('mapsSeatPopupList');

/**
 * Creates a .maps-popup-row element with the party colour bar, label, and injected values HTML.
 * CSS custom properties --maps-popup-bar-width and --maps-popup-bar-colour drive the bar.
 * @param {string} party - Party key for colour lookup.
 * @param {number} barWidth - Bar width percentage (0–75), scaled relative to the leading row.
 * @param {string} valuesHtml - Inner HTML for the .maps-popup-values div.
 * @returns {HTMLDivElement}
 */
function buildPopupRow(party, barWidth, valuesHtml) {
  const item = document.createElement('div');
  item.className = 'maps-popup-row';
  item.style.setProperty('--maps-popup-bar-width', `${barWidth}%`);
  item.style.setProperty('--maps-popup-bar-colour', manifest.colourParty(party));
  item.innerHTML = `
    <div class="maps-popup-party"><span class="maps-seat-icon" style="background:${manifest.colourParty(party)}"></span>${escapeHtml(manifest.labelParty(party))}</div>
    <div class="maps-popup-values">${valuesHtml}</div>
  `;
  return item;
}

/**
 * Clears seatPopupList and renders a scaled bar-chart row for each entry in rows.
 * Computes maxPct internally so callers don't need to manage bar-width scaling.
 * @param {Array<{party: string, pct: number}>} rows - Sorted rows; each must have party and pct.
 * @param {function({party: string, pct: number}): string} getValuesHtml - Returns right-side values HTML for a row.
 * @returns {void}
 */
function renderPopupRows(rows, getValuesHtml) {
  // Scale all bars relative to the leading row so the top party always fills 75%.
  const maxPct = rows.reduce((max, row) => Math.max(max, row.pct), 0);
  seatPopupList.innerHTML = '';
  rows.forEach((row) => {
    // Bar width is proportional to pct / maxPct, capped at 75 to leave room for labels.
    const barWidth = maxPct > 0 ? Math.max(0, Math.min(75, (row.pct / maxPct) * 75)) : 0;
    // getValuesHtml supplies the right-side content (vote share, delta, seat count, etc.)
    // which differs between the region popup and the constituency popup.
    seatPopupList.appendChild(buildPopupRow(row.party, barWidth, getValuesHtml(row)));
  });
}

/**
 * Populates the seat popup with a list-region summary.
 *
 * Clears the current seat selection so no constituency is shown as active.
 *
 * Sets the popup title to "<Region> List Vote" using the human-readable region label.
 *
 * Renders the meta bar with the total number of list seats won in this region.
 *
 * Builds a vote-share row for each party that received list votes, sorted descending
 * by vote count, capped at 8 rows. Each row contains:
 *  - A colour bar whose width is proportional to the party's share relative to the
 *    leading party, capped at 75% of the column width.
 *  - A party swatch and label.
 *  - The party's list vote percentage and seat count.
 *
 * Unhides the popup.
 *
 * @param {string} regionKey - Normalised region identifier (used for the title label).
 * @param {{seatsByParty: Object<string,number>, votesByParty: Object<string,number>}} data - Pre-fetched summary entry for this region.
 * @returns {void}
 */
function renderRegionPopup(regionKey, data) {
  seatPopupTitle.textContent = `${getRegionLabel(regionKey, state.currentRegionLabelsByKey)} List Vote`;

  // Meta bar: total list seats won across all parties in this region.
  const totalSeats = Object.values(data.seatsByParty).reduce((a, b) => a + b, 0);
  seatPopupMeta.innerHTML = `<span class="maps-popup-meta-item">Total seats: ${totalSeats}</span>`;

  // Build a sorted rows array with pre-computed vote share percentages, capped at 8.
  const totalVotes = Object.values(data.votesByParty).reduce((a, b) => a + b, 0);
  const rows = Object.entries(data.votesByParty)
    .map(([party, votes]) => ({ party, votes, pct: totalVotes > 0 ? (votes / totalVotes) * 100 : 0 }))
    .sort((a, b) => b.votes - a.votes)
    .slice(0, 8);

  // Each row shows the party's list vote share (derived from votesByParty / totalVotes above)
  // and the raw seat count for that party in this region (from seatsByParty).
  renderPopupRows(rows, (row) => {
    const seats = data.seatsByParty[row.party] || 0;
    return `<span>${formatPct(row.pct)}%</span><span style="color:#6b7280">${seats} seat${seats !== 1 ? 's' : ''}</span>`;
  });

  seatPopup.hidden = false;
}

// TODO: make private once electionmaps.js callers (wireSeatPopup, resetZoom) migrate to dom.js
/**
 * Hides the seat detail popup and clears the tracked open seat name.
 * @returns {void}
 */
export function hideSeatPopup() {
  if (!seatPopup) return;
  seatPopup.hidden = true;
}

// TODO: make private once electionmaps.js caller (selectSeatBySearchQuery) migrates to dom.js
/**
 * Renders the seat detail popup for seatName, showing majority, gain indicator, and a ranked
 * vote share bar chart with comparison deltas. Hides the popup if the seat is not found.
 * @param {string} seatName - Display name of the seat to show.
 * @returns {void}
 */
export function renderSeatPopup(seatName) {
  // Resolve the seat object; hide the popup and bail if not found.
  const seatKey = seatLookupKey(seatName);
  const seat = state.electionData.seatsByKey.get(seatKey);
  if (!seat) {
    hideSeatPopup();
    return;
  }

  // Resolve gain indicator and majority stats via Seat instance methods so the
  // logic stays in one place and this function has no arithmetic of its own.
  const comparisonSeat = state.comparisonElectionData?.seatsByKey.get(seatKey) || null;
  const gainFrom = seat.gainFromParty(comparisonSeat?.winner || null);
  const majority = seat.majorityStats();

  // Model and referendum elections have no meaningful turnout or raw majority to display.
  const showCounts = !state.currentElection.model && !state.isReferendumType;

  // Populate title and meta line (gain indicator, region, majority, turnout).
  seatPopupTitle.textContent = seat.seat;
  seatPopupMeta.innerHTML = `
    ${gainFrom ? `<span class="maps-popup-meta-item">FROM ${manifest.labelParty(gainFrom)} <span class="maps-seat-icon" style="background:${manifest.colourParty(gainFrom)}"></span></span>` : ''}
    <span class="maps-popup-meta-item">${getRegionLabel(seat.region, state.currentRegionLabelsByKey)}</span>
    <span class="maps-popup-meta-item">Majority: ${formatPct(majority.pct)}%${showCounts ? ` = ${formatInt(majority.raw)}` : ''}</span>
    ${showCounts ? `<span class="maps-popup-meta-item">Turnout: ${formatInt(seat.turnout)}</span>` : ''}
  `;

  // Build vote-share rows with comparison deltas. prevPct is null before the
  // comparison election's first data point, which suppresses the delta span.
  const currentTurnout = seat.turnout;
  const comparisonTurnout = comparisonSeat?.turnout ?? 0;
  const comparisonVotes = comparisonSeat?.votes || {};

  const rows = Object.entries(seat.votes || {})
    .map(([party, votes]) => {
      const voteTotal = Number(votes || 0);
      const pct = currentTurnout > 0 ? (voteTotal / currentTurnout) * 100 : 0;
      const prevPct = comparisonTurnout > 0 ? ((Number(comparisonVotes[party] || 0) / comparisonTurnout) * 100) : null;
      const delta = prevPct == null ? null : pct - prevPct;
      return { party, pct, delta };
    })
    .sort((a, b) => b.pct - a.pct)
    .slice(0, 8);

  // Each row shows the party's vote share (votes / seat turnout) and, when comparison
  // data is available, the signed delta vs the comparison election. delta is null when
  // the party had no comparison data point, which suppresses the delta span entirely.
  renderPopupRows(rows, (row) => {
    const deltaHtml = row.delta == null ? '' : `<span class="${deltaClass(row.delta)}">${formatSigned(row.delta, 2)}</span>`;
    return `<span>${formatPct(row.pct)}%</span>${deltaHtml}`;
  });

  seatPopup.hidden = false;
}

// ─── Seat list ───────────────────────────────────────────────────────────────

// Seat list panel elements — populated by renderSeatList.
const seatList = document.getElementById('mapsSeatList');
const seatListTitle = document.querySelector('#mapsSeatCard .maps-panel-title');

/**
 * Marks the seat list row for seatKey as selected (is-selected class) and deselects the previously selected row.
 * @param {string} seatKey - Normalized seat lookup key identifying which row to select.
 * @returns {void}
 */
function setSelectedSeatRowByKey(seatKey) {
  const nextRow = state.seatList.rowByKey.get(seatKey);
  // No row found — seat may not be visible under current filters; do nothing.
  if (!nextRow) return;

  // Deselect the previously highlighted row before selecting the new one.
  if (state.seatList.selected && state.seatList.selected !== nextRow) {
    state.seatList.selected.classList.remove('is-selected');
  }
  nextRow.classList.add('is-selected');
  state.seatList.selected = nextRow;
}

/**
 * Renders up to 300 seat rows sorted alphabetically into the seat list panel. Each row shows
 * the winner colour, name, and gain-from indicator. Click zooms and opens the seat popup.
 * Reads seats and comparison data directly from state.
 * @returns {void}
 */
function renderSeatList() {
  const seats = state.listFilteredSeats;
  const comparisonSeats = state.comparisonSeats;
  seatListTitle.textContent = `Seats (${seats.length})`;
  // Wipe the previous render and clear the stale selection reference before rebuilding.
  seatList.innerHTML = '';
  state.seatList.selected = null;

  const comparisonSeatsByKey = state.comparisonElectionData?.seatsByKey ?? null;

  const ordered = [...seats].sort((a, b) => a.seat.localeCompare(b.seat));
  // Build rowByKey locally so it can be atomically written to state at the end,
  // avoiding a partially-populated map being read by setSelectedSeatRowByKey mid-render.
  const rowByKey = new Map();

  const renderSeatRow = (seat) => {
    const seatName = seat.seat || 'Unknown seat';
    const seatKey = seatLookupKey(seatName);
    const winnerKey = seat.winner || 'others';
    const comparisonWinnerKey = comparisonSeatsByKey?.get(seatKey)?.winner ?? null;
    // Only show a gain indicator when the winner changed from the comparison election.
    const gainedFrom = comparisonWinnerKey && comparisonWinnerKey !== winnerKey ? comparisonWinnerKey : null;
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'maps-seat-row';
    item.dataset.seatKey = seatKey;
    item.setAttribute('aria-label', `Zoom to ${seatName}`);
    item.innerHTML = `
      <span class="maps-seat-main">
        <span class="maps-seat-icon maps-seat-owner-icon" style="background:${manifest.colourParty(winnerKey)}" title="${manifest.labelParty(winnerKey)}"></span>
        <span class="maps-seat-name">${seatName}</span>
      </span>
      <span class="maps-seat-meta">
        ${gainedFrom ? `<span class="maps-seat-gain"><span class="maps-seat-gain-label">GAIN FROM</span><span class="maps-seat-icon" style="background:${manifest.colourParty(gainedFrom)}" title="${manifest.labelParty(gainedFrom)}"></span></span>` : '<span class="maps-seat-gain-placeholder"></span>'}
      </span>
    `;

    item.addEventListener('click', () => {
      setSelectedSeatRowByKey(seatKey);
      mapInteraction.zoomToSeat(seatName);
      renderSeatPopup(seatName);
    });

    rowByKey.set(seatKey, item);
    seatList.appendChild(item);
  };

  // Render the rows
  ordered.forEach(renderSeatRow);
  state.seatList.rowByKey = rowByKey;
}

// ─── Right panel ─────────────────────────────────────────────────────────────

const mapsStage = document.querySelector('.maps-stage');
const mapsPanelRight = document.querySelector('.maps-panel-right');

/**
 * Syncs the right panel's height to the map stage height so the two columns stay aligned.
 * On mobile (≤980px) the panel stacks below the map, so height constraints are cleared instead.
 * No-ops silently when either element is absent from the DOM.
 * @returns {void}
 */
export function renderRightPanel() {
  if (!mapsStage || !mapsPanelRight) return;

  if (window.innerWidth <= 980) {
    mapsPanelRight.style.height = '';
    mapsPanelRight.style.maxHeight = '';
    return;
  }

  const stageHeight = mapsStage.getBoundingClientRect().height;
  if (!Number.isFinite(stageHeight) || stageHeight <= 0) return;

  mapsPanelRight.style.height = `${Math.round(stageHeight)}px`;
  mapsPanelRight.style.maxHeight = `${Math.round(stageHeight)}px`;
}

// ─── TopoJSON map ────────────────────────────────────────────────────────────

const mapSvg = document.querySelector('.maps-svg');
const mapContent = document.getElementById('mapContent');
const zoomValue = document.getElementById('mapsZoomValue');

// Exported map interaction handle. Replaced on every renderTopoMap call;
// external callers (toolbar buttons, seat list, postcode search) use it to drive
// the map without holding references to internal D3 selections.
// Stub methods are in effect until the first renderTopoMap call.
export let mapInteraction = {
  zoomBy: () => {},
  reset: () => {},
  clearSelection: () => {},
  highlightSeat: () => {},
  zoomToSeat: () => false,
  flashRegion: () => {},
};

const INITIAL_MAP_SCALE = 1.0;
const INITIAL_MAP_SCALE_MOBILE = 1.26;
const ZOOM_MIN_SCALE = 1;
const ZOOM_MAX_SCALE = 17.5;
const SEAT_ZOOM_BASE = 0.05;
const CLICK_ZOOM_DURATION_MS = 1500;
const RESET_ZOOM_DURATION_MS = 500;

/**
 * Owns all zoom, pan, highlight, and flash interactions for the rendered TopoJSON map.
 * Constructed by renderTopoMap on each render with fresh D3 selections and lookup maps;
 * assigned to the exported mapInteraction binding for external callers.
 *
 * Public API:
 *   zoomBy(factor)                  — scale by factor in a short transition
 *   reset()                         — return to initial zoom and clear selection
 *   clearSelection()                — remove the active seat highlight
 *   highlightSeat(name)             — highlight without zooming
 *   selectFeature(datum, pathNode)  — highlight pathNode and zoom to its feature
 *   zoomToSeat(name)                — highlight and zoom by seat name; returns false if seat not on map
 *   flashRegion(regionKey)          — flash a region; no-op until setFlashLayer() is called
 *   registerSeatPath(key, node)     — record a seat's path node (called once per seat at render time)
 *   setFlashLayer(layer, geoms)     — install the flash layer (list-seat elections only)
 */
class MapInteraction {
  /**
   * @param {object} svg - d3 selection of the root SVG element.
   * @param {object} zoomBehavior - d3 zoom behaviour attached to svg.
   * @param {object} path - d3 geo path generator for the current projection.
   * @param {object} initialTransform - d3 zoom transform to return to on reset.
   * @param {number} width - SVG viewBox width in pixels.
   * @param {number} height - SVG viewBox height in pixels.
   * @param {Map} featureBySeat - normalised seat key → GeoJSON feature.
   */
  constructor(svg, zoomBehavior, path, initialTransform, width, height, featureBySeat) {
    this._svg = svg;
    this._zoomBehavior = zoomBehavior;
    this._path = path;
    this._initialTransform = initialTransform;
    this._width = width;
    this._height = height;
    this._featureBySeat = featureBySeat;
    this._seatPathByKey = new Map();
    this._activeSeatPathNode = null;
    this._flashLayer = null;
    this._geometriesByRegion = null;
  }

  /**
   * Registers a rendered SVG path node for a seat so highlightSeat and zoomToSeat can
   * locate it by name. Called once per seat path during the initial render pass.
   * @param {string} seatKey - Normalised seat lookup key.
   * @param {Element} node - SVG path DOM node for the seat.
   */
  registerSeatPath(seatKey, node) {
    this._seatPathByKey.set(seatKey, node);
  }

  // ── Static utilities ─────────────────────────────────────────────────────

  /** Extracts the seat name from a TopoJSON feature. All current map files use `name`. */
  static seatNameFromFeature(featureDatum) {
    return featureDatum?.properties?.name || null;
  }

  /**
   * Converts a d3 zoom scale value to a human-readable percentage string relative to
   * INITIAL_MAP_SCALE (e.g. scale 2.0 → '200%').
   */
  static formatZoomPct(scaleValue) {
    const baselineScale = Math.max(1, Number(INITIAL_MAP_SCALE) || 1);
    const ratio = Number(scaleValue) / baselineScale;
    if (!Number.isFinite(ratio) || ratio <= 0) return '100%';
    return `${Math.round(ratio * 100)}%`;
  }

  /**
   * Returns the d3 zoom transform that centres the map at the initial scale.
   * Uses INITIAL_MAP_SCALE_MOBILE on narrow screens (≤ 980px), INITIAL_MAP_SCALE otherwise.
   */
  static getInitialZoomTransform(width, height) {
    const isMobile = window.innerWidth <= 980;
    const scale = Math.max(1, Number(isMobile ? INITIAL_MAP_SCALE_MOBILE : INITIAL_MAP_SCALE) || 1);
    const tx = width / 2 - scale * (width / 2);
    const ty = height / 2 - scale * (height / 2);
    return d3.zoomIdentity.translate(tx, ty).scale(scale);
  }

  /**
   * Computes a d3 zoom transform centred on featureDatum, scaling by the square-root of
   * its bounding box dimensions so large seats zoom less than small ones.
   */
  static getSeatZoomTransform(path, featureDatum, width, height) {
    const bounds = path.bounds(featureDatum);
    const dx = Math.max(0, bounds[1][0] - bounds[0][0]);
    const dy = Math.max(0, bounds[1][1] - bounds[0][1]);
    const cx = (bounds[0][0] + bounds[1][0]) / 2;
    const cy = (bounds[0][1] + bounds[1][1]) / 2;
    const denom = Math.max(Math.sqrt(dx) / width, Math.sqrt(dy) / height, 1e-9);
    const scale = Math.max(ZOOM_MIN_SCALE, Math.min(ZOOM_MAX_SCALE, SEAT_ZOOM_BASE / denom));
    return d3.zoomIdentity.translate(width / 2 - scale * cx, height / 2 - scale * cy).scale(scale);
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  /** Animates zoom to centre on featureDatum using the seat zoom transform. */
  _zoomToFeature(featureDatum) {
    const targetTransform = MapInteraction.getSeatZoomTransform(this._path, featureDatum, this._width, this._height);
    this._svg.transition().duration(CLICK_ZOOM_DURATION_MS).call(this._zoomBehavior.transform, targetTransform);
  }

  /**
   * Marks pathNode as the active seat: removes highlight from the previous path,
   * applies the active class, and raises it above neighbouring seats in the SVG stack.
   * Safe to call with null — no-op.
   * @param {SVGPathElement|null} pathNode
   */
  _setActivePath(pathNode) {
    if (!pathNode) return;
    if (this._activeSeatPathNode && this._activeSeatPathNode !== pathNode) {
      d3.select(this._activeSeatPathNode).classed('maps-region-path-active', false);
    }
    this._activeSeatPathNode = pathNode;
    d3.select(pathNode).classed('maps-region-path-active', true).raise();
  }

  /** Removes the active highlight and clears the reference. Safe to call when nothing is active. */
  _clearActivePath() {
    if (!this._activeSeatPathNode) return;
    d3.select(this._activeSeatPathNode).classed('maps-region-path-active', false);
    this._activeSeatPathNode = null;
  }

  // ── Public interface ──────────────────────────────────────────────────────

  /** Scales the map by factor in a short transition. factor > 1 zooms in, < 1 zooms out. */
  zoomBy(factor) {
    this._svg.transition().duration(180).call(this._zoomBehavior.scaleBy, factor);
  }

  /** Hides the seat popup, clears the active highlight, and returns to the initial zoom. */
  reset() {
    hideSeatPopup();
    this._clearActivePath();
    this._svg.transition().duration(RESET_ZOOM_DURATION_MS).call(this._zoomBehavior.transform, this._initialTransform);
  }

  /** Removes the active seat highlight. */
  clearSelection() {
    this._clearActivePath();
  }

  /** Highlights the path for seatName without zooming — syncs map to an external trigger. */
  highlightSeat(seatName) {
    const seatPathNode = this._seatPathByKey.get(seatLookupKey(seatName));
    this._setActivePath(seatPathNode);
  }

  /**
   * Highlights pathNode and animates zoom to featureDatum. Used by the seat-path click
   * handler in renderTopoMap, which already has both values in hand.
   * @param {object} featureDatum - GeoJSON feature for the seat.
   * @param {SVGPathElement|null} pathNode - SVG path to highlight; null skips highlight.
   */
  selectFeature(featureDatum, pathNode) {
    this._setActivePath(pathNode);
    this._zoomToFeature(featureDatum);
  }

  /**
   * Highlights and zooms to seatName.
   * @returns {boolean} false if the seat has no matching feature (e.g. filtered out).
   */
  zoomToSeat(seatName) {
    const seatKey = seatLookupKey(seatName);
    const featureDatum = this._featureBySeat.get(seatKey);
    if (!featureDatum) return false;
    this.selectFeature(featureDatum, this._seatPathByKey.get(seatKey));
    return true;
  }

  /**
   * Dissolves the region's seat geometries into a single merged polygon, appends a
   * temporary path to the flash layer, and removes it after the CSS animation.
   * No-op until setFlashLayer() has installed a layer (list-seat elections only).
   * @param {string} regionKey - normalised region key.
   */
  flashRegion(regionKey) {
    if (!this._flashLayer || !this._geometriesByRegion) return;
    const geoms = this._geometriesByRegion.get(regionKey);
    if (!geoms) return;
    const merged = topojsonMerge(state.mapData, geoms);
    if (!merged) return;
    const flashPath = this._flashLayer.append('path')
      .attr('class', 'maps-region-flash-path')
      .attr('d', this._path(merged));
    flashPath.node().addEventListener('animationend', () => flashPath.remove(), { once: true });
  }

  /**
   * Installs the flash layer and region geometry index. Called after construction
   * once the layer exists. Until called, flashRegion is a no-op.
   * @param {object} flashLayer - d3 selection of the flash g element inside zoomLayer.
   * @param {Map} geometriesByRegion - normalised region key → TopoJSON geometry array.
   */
  setFlashLayer(flashLayer, geometriesByRegion) {
    this._flashLayer = flashLayer;
    this._geometriesByRegion = geometriesByRegion;
  }
}

/**
 * Renders the full TopoJSON map into mapSvg using D3.
 * Creates seat path elements coloured by winner or choropleth metric, wires click-to-zoom
 * and hover handlers, draws region boundary overlays, and assigns a fresh MapInteraction
 * instance to the exported mapInteraction binding for external callers.
 * Reads map data, seats, filters, choropleth config, and region summary directly from state.
 * @param {boolean} [preserveZoom=false] - When true, keep the current d3 pan/zoom transform.
 * @returns {void}
 */
function renderTopoMap(preserveZoom = false) {
  // ── Snapshot state ────────────────────────────────────────────────────────
  // Read everything from state up-front so the render is a pure function of a
  // single consistent snapshot. Nothing here should be read lazily mid-render.

  const mapData = state.mapData;
  // null means "all seats visible"; a Set means only those keys are unfiltered.
  const visibleSeatKeys = state.mapSeatsVisible.seatKeys || null;
  const choroplethConfig = state.choroplethConfig || { enabled: false };
  // regionSummary is non-null only for list-seat elections (e.g. Holyrood) that
  // have regional seat totals — it gates the flash layer setup below.
  const regionSummary = state.listRegionSummary;

  // ── TopoJSON → GeoJSON ────────────────────────────────────────────────────
  // TopoJSON files contain exactly one named object (the map layer). Take the
  // first (only) key rather than hard-coding a name, so the function works
  // across all map files regardless of what they name their object.

  const objectName = Object.keys(mapData?.objects || {})[0];
  if (!objectName) throw new Error('TopoJSON missing objects');
  
  const object = mapData.objects[objectName];
  // Convert the TopoJSON arc topology to a standard GeoJSON FeatureCollection
  // so D3's geo functions can work with it.
  const featureCollection = topojsonFeature(mapData, object);
  const features = featureCollection?.features || [];

  if (!features.length) throw new Error('No map features available');

  // ── SVG dimensions ────────────────────────────────────────────────────────
  // Read dimensions from the SVG's declared viewBox rather than its rendered
  // size — the viewBox is the coordinate space that the projection and all path
  // data are computed in.

  const vb = mapSvg.viewBox?.baseVal;
  const width = vb?.width || 1200;
  const height = vb?.height || 900;

  // ── Projection and path generator ─────────────────────────────────────────
  // fitSize scales and centres the Mercator projection so the full feature
  // collection fills the viewBox. path converts GeoJSON geometries to SVG path
  // data strings using that projection.

  const projection = d3.geoMercator().fitSize([width, height], featureCollection);
  const path = d3.geoPath(projection);

  // ── DOM teardown and rebuild ───────────────────────────────────────────────
  // Remove all existing children of mapContent before rebuilding, so that
  // re-renders (filter change, election switch) start from a clean slate.

  const svg = d3.select(mapSvg);
  const content = d3.select(mapContent);
  content.selectAll('*').remove();

  // ── Lookup tables ─────────────────────────────────────────────────────────
  // featureBySeat: normalised seat key → GeoJSON feature, for zoom-to-seat.
  // Fill colour uses state.electionData.seatsByKey directly — no extra map needed.

  const featureBySeat = new Map();
  features.forEach((featureDatum) => {
    const seatName = MapInteraction.seatNameFromFeature(featureDatum);
    if (!seatName) return;
    featureBySeat.set(seatLookupKey(seatName), featureDatum);
  });

  // ── SVG layer structure ───────────────────────────────────────────────────
  // zoomRoot  — receives no transform; wraps everything inside mapContent.
  //   maps-map-bg rect  — full-size transparent hit area so clicks on empty
  //                       space outside all seat paths still reach the svg
  //                       background click handler.
  //   zoomLayer  — receives the d3 zoom transform on every zoom/pan event.
  //     seatLayer     — constituency fill paths.
  //     boundaryLayer — region boundary overlay mesh (drawn above seat fills).
  //     flashLayer    — temporary region-flash paths (added below if needed).

  const zoomRoot = content.append('g').attr('class', 'maps-geo-root');
  zoomRoot.append('rect').attr('class', 'maps-map-bg').attr('x', 0).attr('y', 0).attr('width', width).attr('height', height);
  const zoomLayer = zoomRoot.append('g').attr('class', 'maps-geo-layer');
  const seatLayer = zoomLayer.append('g').attr('class', 'maps-seat-layer');
  const boundaryLayer = zoomLayer.append('g').attr('class', 'maps-boundary-layer');

  // Zoom behaviour: pan and pinch-zoom within the scale bounds. On each zoom event, apply
  // the new transform to zoomLayer (which contains all map geometry) and update the readout.
  const zoomBehavior = d3
    .zoom()
    .scaleExtent([ZOOM_MIN_SCALE, ZOOM_MAX_SCALE])
    .on('zoom', (event) => {
      zoomLayer.attr('transform', event.transform.toString());
      zoomValue.textContent = MapInteraction.formatZoomPct(event.transform.k);
    });

  svg.call(zoomBehavior);
  const initialTransform = MapInteraction.getInitialZoomTransform(width, height);

  // ── Interaction controller ─────────────────────────────────────────────────

  const interaction = new MapInteraction(svg, zoomBehavior, path, initialTransform, width, height, featureBySeat);
  mapInteraction = interaction;

  // ── Region boundary mesh ──────────────────────────────────────────────────
  // topojsonMesh extracts shared arc segments matching the filter predicate.
  // The filter keeps only interior edges shared by two different-region features
  // (a !== b rules out the outer coastline; the region check rules out edges
  // between two seats in the same region). The result is a single MultiLineString
  // drawn as one path element, styled to show region divisions over the seat fills.

  const regionBoundaryMesh = topojsonMesh(
    mapData, object,
    (a, b) => a && b && a !== b && a.properties?.region !== b.properties?.region
  );
  if (regionBoundaryMesh?.coordinates?.length) {
    boundaryLayer.append('path')
      .datum(regionBoundaryMesh)
      .attr('class', 'maps-region-boundary')
      .attr('d', path);
  }

  // ── Constituency seat paths ───────────────────────────────────────────────

  const seatPaths = seatLayer
    .selectAll('path')
    .data(features)
    .join('path')
    .attr('class', 'maps-region-path')
    .attr('d', path)
    .attr('fill', (datum) => {
      const seatKey = seatLookupKey(MapInteraction.seatNameFromFeature(datum));
      const seat = state.electionData.seatsByKey.get(seatKey);
      // Feature has no name, or no matching seat in the election data — render as others.
      if (!seat) return manifest.colourParty('others');

      // Active filter excludes this seat — render as greyed-out slate rather than hiding,
      // so the map shape remains intact and the filter effect is clear.
      if (visibleSeatKeys && !visibleSeatKeys.has(seatKey)) return '#cbd5e1';

      // Choropleth mode overrides winner colouring with a continuous metric scale.
      if (choroplethConfig.enabled && choroplethConfig.valueBySeatKey?.has(seatKey)) {
        return choroplethConfig.toColour(choroplethConfig.valueBySeatKey.get(seatKey));
      }

      // Default: colour by winning party.
      return manifest.colourParty(seat.winner || 'others');
    })
    .attr('stroke', null)
    .on('mouseenter', null)
    .on('click', (event, datum) => {
      // stopPropagation prevents the svg background click handler from firing and
      // immediately resetting the zoom we are about to trigger.
      event.stopPropagation();
      const seatName = MapInteraction.seatNameFromFeature(datum);
      if (!seatName) return;
      setSelectedSeatRowByKey(seatLookupKey(seatName));
      renderSeatPopup(seatName);
      interaction.selectFeature(datum, event.currentTarget);
    });

  // Build the seatKey → SVG path node index so zoomToSeat and highlightSeat can find
  // the path element for a given seat name without scanning all features on every call.
  seatPaths.each(function assignSeatPath(datum) {
    const seatName = MapInteraction.seatNameFromFeature(datum);
    if (!seatName) return;
    interaction.registerSeatPath(seatLookupKey(seatName), this);
  });

  // ── Region flash layer (list-seat elections only) ─────────────────────────
  // regionSummary is non-null only for Holyrood-style elections that have a
  // regional seat total panel. The flash layer sits above all seat paths inside
  // zoomLayer so it zooms and pans with the map.
  //
  // geometriesByRegion maps each normalised region key to the raw TopoJSON
  // geometry objects for all seats in that region. setFlashLayer wires the real
  // flashRegion implementation on the interaction instance once the layer exists.

  if (regionSummary) {
    const geometriesByRegion = new Map();
    (object.geometries || []).forEach((geom) => {
      const region = geom.properties?.region;
      if (!region) return;
      const regionKey = normalizeRegionKey(region);
      if (!geometriesByRegion.has(regionKey)) geometriesByRegion.set(regionKey, []);
      geometriesByRegion.get(regionKey).push(geom);
    });

    const flashLayer = zoomLayer.append('g').attr('class', 'maps-region-flash-layer');
    interaction.setFlashLayer(flashLayer, geometriesByRegion);
  }

  // ── Background click → reset ───────────────────────────────────────────────
  // Clicking the SVG element itself or the background rect resets the map.
  // Clicks on seat paths do not reach here because they call stopPropagation.

  svg.on('click', (event) => {
    const target = event.target;
    if (target === mapSvg || target?.classList?.contains('maps-map-bg')) {
      interaction.reset();
    }
  });

  // ── Initial transform ──────────────────────────────────────────────────────
  // Apply the starting zoom state immediately (no transition). When preserveZoom
  // is true, read the current live transform from the SVG node so pan/zoom
  // position survives a filter or choropleth re-render. Otherwise reset to the
  // computed initial transform.

  svg.call(zoomBehavior.transform, preserveZoom ? d3.zoomTransform(mapSvg) : initialTransform);
}

// ─── Choropleth legend ────────────────────────────────────────────────────────────────

/**
 * Renders the choropleth legend panel from state.choroplethConfig.
 * Hides and clears the panel when choropleth is disabled.
 * Shows a plain-text label when the config has no structured legend object.
 * Builds a CSS gradient bar with min/mid/max labels when a full legend is present;
 * the mid stop is included only for delta (symmetric) colour ramps.
 * @returns {void}
 */
function renderChoroplethLegend() {
  const choroplethConfig = state.choroplethConfig;
  if (!choroplethConfig?.enabled) {
    choroplethLegend.hidden = true;
    choroplethLegend.innerHTML = '';
    return;
  }

  const legend = choroplethConfig.legend;
  if (!legend) {
    choroplethLegend.textContent = `Choropleth: ${choroplethConfig.legendText}`;
    choroplethLegend.hidden = false;
    return;
  }

  const gradient = legend.isDelta
    ? `linear-gradient(90deg, ${legend.startColour} 0%, ${legend.midColour} 50%, ${legend.endColour} 100%)`
    : `linear-gradient(90deg, ${legend.startColour} 0%, ${legend.endColour} 100%)`;

  choroplethLegend.innerHTML = `
    <div class="maps-choropleth-legend-title">${legend.title}</div>
    <div class="maps-choropleth-legend-bar" style="background:${gradient}"></div>
    <div class="maps-choropleth-legend-labels">
      <span>${legend.minLabel}</span>
      ${legend.isDelta ? `<span>${legend.midLabel}</span>` : ''}
      <span>${legend.maxLabel}</span>
    </div>
  `;
  choroplethLegend.hidden = false;
}

// ─── Map init ────────────────────────────────────────────────────────────────

/**
 * Wires all DOM-owned controls. Call once during page boot alongside wireInit.
 * @returns {void}
 */
export function domWireInit() {
  wirePollTrackerControls();
  wirePopupPanels();
  wireMapInteractions();
  wireMapViewControls();
  wireSeatSearch();
  wirePostcodeSearch();
  wireVoteTotalsToggle();
}

/**
 * Runs all once-per-election DOM initialisations. Must be called after state.setupMapData() so
 * mapConfig and listRegionSummary are already set.
 *
 * Rebuilds the vote-totals tab nav, shows/hides the postcode search group based
 * on state.mapConfig.postcodeSupported, and populates the region-table overlay (hidden for non-list elections).
 *
 * @returns {void}
 */
export function renderMapInit() {
  initVoteTotalsTabs();
  initPostcodeSearch();
  initRegionTable();
}

// ─── Draw ────────────────────────────────────────────────────────────────────

/**
 * Renders the vote totals, seat list, topo map, and choropleth legend.
 * @param {boolean} [preserveZoom=false] - When true, keep the current pan/zoom transform.
 * @returns {void}
 */
export function renderMap(preserveZoom = false) {
  renderVoteTotals();
  renderSeatList();
  renderTopoMap(preserveZoom);
  renderChoroplethLegend();
}

/**
 * Runs the per-render data setup then re-renders map, seat list, vote totals, and legend.
 * @param {boolean} [preserveZoom=false] - When true, keep the current pan/zoom transform.
 * @returns {void}
 */
function drawMap(preserveZoom = false) {
  state.setupMapData();
  renderMap(preserveZoom);
}

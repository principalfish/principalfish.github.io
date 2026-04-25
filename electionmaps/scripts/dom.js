import * as d3 from '../../site/vendor/d3.v7.esm.js';
import { manifest, state, getPredictAnchorElectionId, HOLYROOD_ELECTION_DATE, shouldShowCountdown } from './state.js';
import { escapeHtml, formatInt, formatPct, viewUrl } from './utils.js';

const electionList = document.getElementById('mapsElectionList');
const mapsTitle = document.querySelector('.maps-title');
const electionCountdown = document.getElementById('mapsElectionCountdown');
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
  const predictAnchorId = getPredictAnchorElectionId() ?? null;

  electionList.innerHTML = '';
  let insertedPredictLink = false;
  let insertedPollTrackerLink = false;
  state.parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = viewUrl('election', election.id);
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (hasPredictMode && !insertedPredictLink && election.id === predictAnchorId) {
      const predictLink = document.createElement('a');
      predictLink.href = viewUrl('predict');
      predictLink.className = `maps-election-item${state.view === 'predict' ? ' active' : ''}`;
      const nextElectionYear = manifest.parliamentFeatures[state.currentParliament]?.nextElectionYear;
      predictLink.textContent = `Predict ${nextElectionYear ?? ''}`;
      electionList.appendChild(predictLink);
      insertedPredictLink = true;

      if (hasPollTracker && !insertedPollTrackerLink) {
        const trackerLink = document.createElement('a');
        trackerLink.href = viewUrl('polltracker');
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

  const shouldShow = shouldShowCountdown();

  if (countdown.intervalId !== null) {
    clearInterval(countdown.intervalId);
    countdown.intervalId = null;
  }

  if (!shouldShow) {
    electionCountdown.hidden = true;
    return;
  }

  const tick = () => {
    const msLeft = HOLYROOD_ELECTION_DATE - Date.now();
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

const pollTrackerChartWrap = document.getElementById('mapsPollTrackerChartWrap');
const pollTrackerPartyControls = document.getElementById('mapsPollTrackerPartyControls');
const pollTrackerMetricSeatsInput = document.getElementById('mapsPollTrackerMetricSeats');
const pollTrackerMetricVotesInput = document.getElementById('mapsPollTrackerMetricVotes');

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
  return Array.from(document.querySelectorAll('.maps-polltracker-party-toggle input[type="checkbox"]'))
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
  // 1. Read inputs and reset the chart container.
  const selectedParties = polltrackerSelectedParties();
  const seatsEnabled = Boolean(pollTrackerMetricSeatsInput?.checked);
  const votePctEnabled = Boolean(pollTrackerMetricVotesInput?.checked);
  pollTrackerChartWrap.innerHTML = '';
  pollTrackerChartWrap.style.position = 'relative';

  // 2. Empty states. Bail with a friendly message if there's no data, or if the user
  //    has deselected everything (no parties, or no metric).
  if (!state.pollTrackerData.timeline.length) {
    pollTrackerChartWrap.innerHTML = '<div class="maps-polltracker-empty">No poll tracker data available.</div>';
    return;
  }
  if (!selectedParties.length || !(seatsEnabled || votePctEnabled)) {
    pollTrackerChartWrap.innerHTML = '<div class="maps-polltracker-empty">Select at least one party and one metric (Seats/Vote %).</div>';
    return;
  }

  // 3. Slice the visible window from the back of the full timeline based on the user range selection.
  const { visibleTimeline, windowStart } = pollTrackerWindow();

  // 4. Resolve responsive SVG dimensions and reserved margins.
  const { width, height, margin, innerWidth, innerHeight } = pollTrackerDimensions();

  // 5. Build the SVG canvas, plot group, tooltip overlay, and the crosshair line.
  const { svg, plot, tooltip, crosshairLine } = pollTrackerScaffold(width, height, margin, innerHeight);

  // 6. X scale. With ≥2 entries we use a real time scale (placement matches actual dates, so big gaps
  //    span proportionally). The 1-entry edge case can't form a time domain, so fall back to a
  //    linear scale over indices — there's only one point to plot, so positioning is trivial anyway.
  const useTimeScale = visibleTimeline.length > 1;
  const x = useTimeScale
    ? d3.scaleTime()
      .domain(d3.extent(visibleTimeline.map((entry) => entry.dateValue)))
      .range([0, innerWidth])
    : d3.scaleLinear()
      .domain([0, 0])
      .range([0, innerWidth]);

  // 7. Reshape per-party series down to just the visible window. The seats/votePct arrays
  //    are sliced to align positionally with visibleTimeline.
  const selectedSeries = selectedParties
    .map((partyKey) => state.pollTrackerData.seriesByParty.get(partyKey))
    .filter(Boolean)
    .map((series) => ({
      ...series,
      seats: series.seats.slice(windowStart),
      votePct: series.votePct.slice(windowStart),
    }));

  // 8. Y scales. Each metric gets its own with a 8% headroom (`* 1.08`); the vote-% scale is
  //    capped at 100. `.nice()` rounds the domain to friendly tick boundaries.
  const seatsMax = d3.max(selectedSeries.flatMap((series) => series.seats.filter((value) => Number.isFinite(value)))) || 1;
  const votePctMax = d3.max(selectedSeries.flatMap((series) => series.votePct.filter((value) => Number.isFinite(value)))) || 1;
  const ySeats = d3.scaleLinear().domain([0, seatsMax * 1.08]).nice().range([innerHeight, 0]);
  const yVotePct = d3.scaleLinear().domain([0, Math.min(100, votePctMax * 1.08)]).nice().range([innerHeight, 0]);

  // 9. Background grid. Uses whichever Y axis is currently enabled — extends its tick marks
  //    across the full plot width as horizontal grid lines (no tick labels).
  const gridAxis = seatsEnabled ? d3.axisLeft(ySeats).ticks(6) : d3.axisRight(yVotePct).ticks(6);
  plot.append('g')
    .attr('class', 'maps-polltracker-axis')
    .call(gridAxis.tickSize(-innerWidth).tickFormat(''))
    .selectAll('line')
    .attr('class', 'maps-polltracker-grid-line');

  // 10. X axis with rotated date labels. Tick density is capped by available width
  //     (~105px per tick). The non-time fallback is for the 1-entry edge case.
  const maxTicksByWidth = Math.max(4, Math.floor(innerWidth / 105));
  const xAxisGroup = plot.append('g')
    .attr('class', 'maps-polltracker-axis')
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

  // 11. Y axes + labels. Left is "Seats" (when seats enabled), right is "Vote %" (when votes enabled),
  //     bottom is "Date" always. Vote % ticks are formatted with a `%` suffix.
  if (seatsEnabled) {
    plot.append('g')
      .attr('class', 'maps-polltracker-axis')
      .call(d3.axisLeft(ySeats).ticks(7));
    plot.append('text')
      .attr('class', 'maps-polltracker-axis-label')
      .attr('transform', 'rotate(-90)')
      .attr('x', -innerHeight / 2)
      .attr('y', -52)
      .attr('text-anchor', 'middle')
      .text('Seats');
  }
  if (votePctEnabled) {
    plot.append('g')
      .attr('class', 'maps-polltracker-axis')
      .attr('transform', `translate(${innerWidth},0)`)
      .call(d3.axisRight(yVotePct).ticks(7).tickFormat((value) => `${Number(value).toFixed(1)}%`));
    plot.append('text')
      .attr('class', 'maps-polltracker-axis-label')
      .attr('transform', 'rotate(90)')
      .attr('x', innerHeight / 2)
      .attr('y', -(innerWidth + 56))
      .attr('text-anchor', 'middle')
      .text('Vote %');
  }
  plot.append('text')
    .attr('class', 'maps-polltracker-axis-label')
    .attr('x', innerWidth / 2)
    .attr('y', innerHeight + 48)
    .attr('text-anchor', 'middle')
    .text('Date');

  // 12. Line generators. `.defined(...)` skips null gaps (party not yet in data), so lines break
  //     rather than connect through nulls.
  const seatsLine = d3.line()
    .defined((value) => Number.isFinite(value))
    .x((_value, index) => (useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index)))
    .y((value) => ySeats(value));
  const votePctLine = d3.line()
    .defined((value) => Number.isFinite(value))
    .x((_value, index) => (useTimeScale ? x(visibleTimeline[index]?.dateValue) : x(index)))
    .y((value) => yVotePct(value));

  // 13. Tooltip handlers. showTrackerTooltip locates the nearest data point by bisecting the
  //     timeline against the cursor's projected date, positions the crosshair, and renders the
  //     party/date/seats/vote-% panel. hideTrackerTooltip is the symmetric mouseleave hook.
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

  // 14. Draw the lines. For each series + each enabled metric we paint TWO paths:
  //     - the visible coloured line (solid for seats, dashed for vote %)
  //     - a thicker invisible "hit area" line on top, which is what catches mousemove/mouseleave.
  //     This makes thin lines easy to hover without making the visible stroke fat.
  selectedSeries.forEach((series) => {
    if (seatsEnabled) {
      plot.append('path')
        .datum(series.seats)
        .attr('fill', 'none')
        .attr('stroke', series.colour)
        .attr('stroke-width', 2.1)
        .attr('d', seatsLine);
      plot.append('path')
        .datum(series.seats)
        .attr('fill', 'none')
        .attr('stroke', 'transparent')
        .attr('stroke-width', 14)
        .attr('d', seatsLine)
        .on('mousemove', (event) => showTrackerTooltip(event, series))
        .on('mouseleave', hideTrackerTooltip);
    }
    if (votePctEnabled) {
      plot.append('path')
        .datum(series.votePct)
        .attr('fill', 'none')
        .attr('stroke', series.colour)
        .attr('stroke-width', 2.1)
        .attr('stroke-dasharray', '6 4')
        .attr('d', votePctLine);
      plot.append('path')
        .datum(series.votePct)
        .attr('fill', 'none')
        .attr('stroke', 'transparent')
        .attr('stroke-width', 14)
        .attr('d', votePctLine)
        .on('mousemove', (event) => showTrackerTooltip(event, series))
        .on('mouseleave', hideTrackerTooltip);
    }
  });

  // 15. Legend in the top-right corner explaining the line styles, then mount the SVG.
  const legend = svg.append('g').attr('transform', `translate(${width - margin.right},${margin.top - 2})`);
  legend.append('text')
    .text('Solid = Seats, Dashed = Vote %')
    .attr('fill', '#334155')
    .attr('text-anchor', 'end')
    .style('font', '700 11px "DM Sans", "Segoe UI", sans-serif');

  pollTrackerChartWrap.appendChild(svg.node());
}

/**
 * Resolves the visible slice of the poll tracker timeline based on the user's range selection.
 * pollTrackerRangeSelection is either 'all' (whole timeline) or a numeric day count;
 * Number('all') is NaN so the isFinite guard naturally falls through to the full length.
 * @returns {{visibleTimeline: Array<{dateKey: string, dateValue: Date}>, windowStart: number}}
 */
function pollTrackerWindow() {
  const timeline = state.pollTrackerData.timeline;
  const requested = Number(state.pollTrackerRangeSelection);
  const windowSize = Number.isFinite(requested) && requested > 0
    ? Math.min(requested, timeline.length)
    : timeline.length;
  const windowStart = timeline.length - windowSize;
  return { visibleTimeline: timeline.slice(windowStart), windowStart };
}

/**
 * Resolves chart dimensions: responsive width (760px floor, 8px gutter), fixed height,
 * and reserved margins for axes/labels.
 * @returns {{width: number, height: number, margin: {top:number,right:number,bottom:number,left:number}, innerWidth: number, innerHeight: number}}
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
    label.className = 'maps-polltracker-party-toggle';

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

// ───────────────────────────────────────────────────────────────────────────

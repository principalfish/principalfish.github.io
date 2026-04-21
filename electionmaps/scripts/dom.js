import { manifest, state, getPredictAnchorElectionId, HOLYROOD_ELECTION_DATE, shouldShowCountdown, electionUrl, predictUrl, pollTrackerUrl } from './state.js';

const electionList = document.getElementById('mapsElectionList');
const mapsTitle = document.querySelector('.maps-title');
const electionCountdown = document.getElementById('mapsElectionCountdown');
const subtitle = document.getElementById('mapsSubtitle');

// ─── Subtitle ─────────────────────────────────────────────────────────────────

/** Full subtitle text set by updateTopSummary once election results have loaded. Empty until then. */
let subtitleText = '';

/**
 * Stores the full subtitle string computed from election results. Call this before updateTitle
 * so that renderSubtitleText picks up the latest value. Not needed for poll tracker or error
 * states, which derive their text from state.view / the error flag directly.
 * @param {string} text - Full subtitle string (e.g. "2024 Election · Labour majority: 174").
 * @returns {void}
 */
export function setSubtitleText(text) {
  subtitleText = text;
}

/**
 * Renders the subtitle element. Derives text and snippet behaviour from current state:
 * poll tracker view uses a fixed label with snippet; election view uses subtitleText
 * (falling back to the election name before results load) with snippet for model elections.
 * @param {boolean} [error=false] - When true, displays a load-failure message instead.
 * @returns {void}
 */
function renderSubtitleText(error = false) {
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
    baseText = subtitleText || state.currentElection?.name || '';
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
 * Updates the title area: the page h1, subtitle, and election countdown.
 * Called early in init (subtitle shows election name only) and again after results load
 * (subtitle shows the full summary set by setSubtitleText). Pass error=true on load failure.
 * @param {boolean} [error=false] - When true, subtitle shows a load-failure message.
 * @returns {void}
 */
export function updateTitle(error = false) {
  renderTitle();
  renderSubtitleText(error);
  renderCountdown();
}

/**
 * Updates the page h1 to suffix the current parliament name (e.g. "UK Election Maps · Westminster").
 * @returns {void}
 */
function renderTitle() {
  const label = state.currentParliament[0].toUpperCase() + state.currentParliament.slice(1);
  mapsTitle.textContent = `UK Election Maps · ${label}`;
}

/**
 * Updates the left panel: highlights the active parliament tab and rebuilds the election list nav.
 * @param {{ onPredict?: function, onPollTracker?: function }} callbacks - Click handlers for mode buttons.
 * @returns {void}
 */
export function updateLeftBar() {
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
  const activeId = (state.pollTrackerModeActive || state.predictModeActive) ? null : state.currentElection.id;

  const features = manifest.parliamentFeatures[state.currentParliament]?.features ?? [];
  const hasPredictMode = features.includes('predict');
  const hasPollTracker = features.includes('pollTracker');
  const predictAnchorId = getPredictAnchorElectionId() ?? null;

  electionList.innerHTML = '';
  let insertedPredictLink = false;
  let insertedPollTrackerLink = false;
  state.parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = electionUrl(election.id);
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (hasPredictMode && !insertedPredictLink && election.id === predictAnchorId) {
      const predictLink = document.createElement('a');
      predictLink.href = predictUrl();
      predictLink.className = `maps-election-item${state.predictModeActive ? ' active' : ''}`;
      const nextElectionYear = manifest.parliamentFeatures[state.currentParliament]?.nextElectionYear;
      predictLink.textContent = `Predict ${nextElectionYear ?? ''}`;
      electionList.appendChild(predictLink);
      insertedPredictLink = true;

      if (hasPollTracker && !insertedPollTrackerLink) {
        const trackerLink = document.createElement('a');
        trackerLink.href = pollTrackerUrl();
        trackerLink.className = `maps-election-item${state.pollTrackerModeActive ? ' active' : ''}`;
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

// ───────────────────────────────────────────────────────────────────────────

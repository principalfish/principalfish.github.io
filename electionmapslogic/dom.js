import * as d3 from '../site/vendor/d3.v7.esm.js';
import {
  feature as topojsonFeature,
  mesh as topojsonMesh,
  merge as topojsonMerge,
} from '../site/vendor/topojson-client.v3.esm.js';
import { manifest, state, page, seatComparisonHidden } from './state.js';
import { escapeHtml, formatInt, formatPct, formatSigned, deltaClass, getRegionLabel, seatLookupKey, normalizeRegionKey, clampNumber, DEFAULT_PARTY_COLOUR, buildStateTrendSeries } from './utils.js';
import { fetchJson } from './files.js';

// ─── Page title ───────────────────────────────────────────────────────────────

// Brand shown in the H1 and browser-tab title. The US page overrides it via page.title
// ("US Elections"); the UK page keeps the default.
const PAGE_BRAND = page.title || 'Election Maps';
const MAPS_PAGE_TITLE_SUFFIX = `${PAGE_BRAND} | Principal Fish`;

/**
 * Sets the browser tab title from the current view: poll tracker, predict, or election name.
 * @returns {void}
 */
export function renderPageTitle() {
  let label;
  if (state.view === 'polltracker') {
    label = 'Poll tracker';
  } else if (state.view === 'predict') {
    const nextYear = manifest.parliamentFeatures[state.currentParliament]?.nextElectionYear;
    label = `Predict ${nextYear ?? ''}`.trim();
  } else {
    label = state.currentElection.name;
  }
  const parliament = state.currentParliament;
  // Use the manifest's display label (e.g. "US House"), not the raw key ("us_house"),
  // so the browser-tab title matches the on-page H1.
  const parlLabel = parliament ? manifest.parliamentLabel(parliament) : null;
  const suffix = parlLabel ? `${parlLabel} | ${MAPS_PAGE_TITLE_SUFFIX}` : MAPS_PAGE_TITLE_SUFFIX;
  document.title = label ? `${label} | ${suffix}` : suffix;
}

// ─── Header ─────────────────────────────────────────────────────────────────

// Page H1 — set to "UK Election Maps · <Parliament>" by renderTitle.
const mapsTitle = document.querySelector('.maps-title');
// Subtitle line below the H1 — election name (or "Poll tracker..." in tracker view),
// optionally suffixed with the poll snippet for prediction elections.
const subtitle = document.getElementById('mapsSubtitle');
// Countdown badge in the header — visible only on a parliament's prediction / predict view
// when that parliament has an upcoming election date; ticks every second until it passes.
const electionCountdown = document.getElementById('mapsElectionCountdown');

/**
 * Updates the title area: the page h1, subtitle, and election countdown.
 * Called early in init (text omitted — subtitle falls back to election name) and again
 * after results load with the full summary string. Pass error=true on load failure.
 * @param {string} [text=''] - Full subtitle string (e.g. "2024 Election · Labour majority: 174").
 * @param {boolean} [error=false] - When true, subtitle shows a load-failure message.
 * @returns {void}
 */
export function renderHeader(text = '', error = false) {
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
  const base = page.title || manifest.misc?.title || 'UK Election Maps';
  const label = manifest.parliamentLabel(state.currentParliament);
  mapsTitle.textContent = label ? `${base} · ${label}` : base;
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

// Parliament selector container — rebuilt from manifest.parliamentTabs() each render.
const parliamentTabsContainer = document.querySelector('.maps-parliament-tabs');

/**
 * Rebuilds the parliament selector from manifest.parliamentTabs(), one anchor per tab, and
 * marks the current parliament active. Tabs are plain `?parliament=…` links (navigation is
 * handled on load), so rebuilding them dynamically is safe — nothing binds click handlers here.
 * @returns {void}
 */
function renderParliamentTabs() {
  if (!parliamentTabsContainer) return;
  parliamentTabsContainer.innerHTML = '';
  manifest.parliamentTabs().forEach((tab) => {
    const link = document.createElement('a');
    link.className = `maps-parliament-tab${tab.parliament === state.currentParliament ? ' active' : ''}`;
    link.dataset.parliament = tab.parliament;
    link.href = `?parliament=${tab.parliament}`;
    link.textContent = tab.label;
    parliamentTabsContainer.appendChild(link);
  });
}

/**
 * Rebuilds the election list nav with one link per election, plus a Predict link (when the
 * feature is enabled for the current parliament) and a Poll tracker link (likewise). Both
 * extras are inserted after the predict-anchor election so they sit alongside the most
 * recent / live result for that parliament.
 * @returns {void}
 */
function renderElectionLinks() {
  if (!electionList) return;
  const activeId = state.view === 'election' ? state.currentElection.id : null;

  const features = manifest.parliamentFeatures[state.currentParliament]?.features ?? [];
  const hasPredict = features.includes('predict');
  const hasPollTracker = features.includes('pollTracker');
  const anchorId = state.getPredictAnchorElectionId() ?? null;

  const appendPredictLink = () => {
    const nextYear = manifest.parliamentFeatures[state.currentParliament]?.nextElectionYear;
    const link = document.createElement('a');
    link.href = state.viewUrl('predict');
    link.className = `maps-election-item${state.view === 'predict' ? ' active' : ''}`;
    link.textContent = `Predict ${nextYear ?? ''}`.trim();
    electionList.appendChild(link);
  };
  const appendPollTrackerLink = () => {
    const link = document.createElement('a');
    link.href = state.viewUrl('polltracker');
    link.className = `maps-election-item${state.view === 'polltracker' ? ' active' : ''}`;
    link.textContent = 'Poll tracker';
    electionList.appendChild(link);
  };

  electionList.innerHTML = '';
  let extrasInserted = false;
  state.parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = state.viewUrl('election', election.id);
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (!extrasInserted && election.id === anchorId) {
      if (hasPredict) appendPredictLink();
      if (hasPollTracker) appendPollTrackerLink();
      extrasInserted = true;
    }
  });

  if (!extrasInserted) {
    if (hasPredict) appendPredictLink();
    if (hasPollTracker) appendPollTrackerLink();
  }
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
 * The date and label come from the current parliament's config (nextElectionDate /
 * nextElectionLabel), so each parliament drives its own countdown — see
 * `state.shouldShowCountdown`.
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

  const cfg = manifest.parliamentConfig(state.currentParliament);
  const electionDate = new Date(cfg.nextElectionDate);
  const label = cfg.nextElectionLabel ?? '';
  const dateText = electionDate.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });

  const tick = () => {
    const msLeft = electionDate - Date.now();
    if (msLeft <= 0) {
      electionCountdown.hidden = true;
      // Clear and null the handle so renderCountdown can safely restart if called again.
      clearInterval(countdown.intervalId);
      countdown.intervalId = null;
      return;
    }
    electionCountdown.textContent = `${formatCountdown(msLeft)} · ${label} · ${dateText}`;
    electionCountdown.hidden = false;
  };

  tick();
  countdown.intervalId = setInterval(tick, 1000);
}

// ─── Pre-fetch ───────────────────────────────────────────────────────────────
//
// Election-type-specific UI hooks that run before results are fetched — sibling to
// the map controls below, but configured up-front rather than rebuilt per load.

// Info button shown only on referendum elections — opens a data-source / methodology explainer.
// Lives in the referendum-info shell fragment, so it's null on pages that don't load it (US).
const dataInfoButton = document.getElementById('mapsDataInfoBtn');

/**
 * Configures election-type-specific UI before election data has loaded.
 * Sets the gains button label and toggles referendum-specific controls.
 * @returns {void}
 */
export function setElectionPreDataFetch() {
  // The Gains filter and "vote share change" choropleth both need matching seat keys across
  // cycles, so they are hidden for referendums and for aggregate-only comparison maps
  // (mapMode.seatComparison === false). The referendum-only data-info button stays gated on
  // isReferendumType alone.
  const hideSeatComparison = seatComparisonHidden();
  filterGainsButton.textContent = state.currentElection.byElectionSeats ? 'By-elections' : 'Gains';
  filterGainsButton.hidden = hideSeatComparison;
  choroplethVoteShareChangeOption.hidden = hideSeatComparison;
  if (dataInfoButton) dataInfoButton.hidden = !state.isReferendumType;
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
// "Next up for election" cycle filter (multi-member chambers whose election carries
// `upcomingElections`, e.g. the Current Senate). The wrapping group is shown only then.
const filterUpcomingSelect = document.getElementById('mapsFilterUpcoming');
const filterUpcomingGroup = document.getElementById('mapsFilterUpcomingGroup');
// Second-place filter — paired with filterPartySelect to restrict to seats where the chosen
// party finished second. The wrapping group is hidden when state.mapFilters.party is 'all'.
const filterSecondPartySelect = document.getElementById('mapsFilterSecondParty');
// Wrapping group for the second-party filter — hidden when no primary party is selected.
const filterSecondPartyGroup = document.getElementById('mapsFilterSecondPartyGroup');
// Majority range filter inputs — restrict visible seats to those within the min/max % range.
// The wrapping row is hidden for multi-member chambers (no vote margins, e.g. Current Senate).
const filterMajorityGroup = document.getElementById('mapsFilterMajorityGroup');
const filterMajorityMinInput = document.getElementById('mapsFilterMajorityMin');
const filterMajorityMaxInput = document.getElementById('mapsFilterMajorityMax');
// Choropleth type select — 'none', 'vote-share', or 'vote-share-change'.
const choroplethTypeSelect = document.getElementById('mapsChoroplethType');
// Choropleth target party — once a choropleth type is selected, this picks which party's
// vote share / vote share change drives the colour ramp on the map.
const choroplethPartySelect = document.getElementById('mapsChoroplethParty');
// Choropleths toolbar button — opens the choropleths panel; hidden for multi-member chambers.
const choroplethsButton = document.getElementById('mapsChoroplethsBtn');
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

  // The "next up for election" filter is gated by the election's `upcomingElections` flag.
  // When off, hide the control and force the filter to 'all' so it never narrows other
  // elections; when on, populate the cycle years from the data.
  const upcomingEnabled = Boolean(state.currentElection?.upcomingElections);
  if (filterUpcomingGroup) filterUpcomingGroup.hidden = !upcomingEnabled;
  if (upcomingEnabled) {
    setOptions(filterUpcomingSelect, state.mapControlUpcomingYears());
  } else {
    state.mapFilters.upcoming = 'all';
    if (filterUpcomingSelect) filterUpcomingSelect.innerHTML = '';
  }

  // Vote/comparison-based controls are meaningless for multi-member chambers (composition
  // snapshots with no votes and no comparison, e.g. Current Senate): hide the majority range,
  // the gains toggle, and the choropleths button, and force them off so none can narrow the
  // map. (The party filter still works — it matches per member; see matchesPrimaryFilters.)
  // buildChoroplethConfig + matchesPrimaryFilters also ignore these for such elections.
  const isMultiMember = Boolean(state.currentElection?.multiMember);
  if (filterMajorityGroup) filterMajorityGroup.hidden = isMultiMember;
  // This is the authoritative writer of the gains button's visibility (it runs after
  // setElectionPreDataFetch), so the per-seat comparison gate must be honoured here too:
  // Gains needs matching seat keys across cycles, absent for referendums and aggregate-only
  // comparison maps (mapMode.seatComparison === false).
  if (filterGainsButton) filterGainsButton.hidden = isMultiMember || seatComparisonHidden();
  if (choroplethsButton) choroplethsButton.hidden = isMultiMember;
  if (isMultiMember) {
    state.mapFilters.majorityMin = 0;
    state.mapFilters.majorityMax = 100;
    state.mapFilters.gainsOnly = false;
    filterMajorityMinInput.value = '0';
    filterMajorityMaxInput.value = '100';
    filterGainsButton.classList.remove('is-active');
  }
}

/**
 * Pushes the current state.mapFilters and state.mapChoropleths values into the DOM
 * filter/choropleth inputs and toggles second-party group visibility.
 * @returns {void}
 */
function syncMapControlInputsFromState() {
  filterPartySelect.value = state.mapFilters.party;
  filterRegionSelect.value = state.mapFilters.region;
  if (filterUpcomingSelect) filterUpcomingSelect.value = state.mapFilters.upcoming;

  // Second-place needs vote data, so it's never offered for multi-member chambers (no votes).
  const showSecondPlaceFilter = state.mapFilters.party !== 'all' && !state.currentElection?.multiMember;
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
function syncMapControlStateFromInputs() {
  state.mapFilters.party = filterPartySelect.value || 'all';
  state.mapFilters.region = filterRegionSelect.value || 'all';
  if (filterUpcomingSelect) state.mapFilters.upcoming = filterUpcomingSelect.value || 'all';
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
    filterUpcomingSelect,
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
const voteTotalsCollapse = document.getElementById('mapsVoteTotalsCollapse');

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
function renderVoteTotals(
  summary = state.filteredSeatsSummary,
  comparisonSummary = state.filteredSeatsComparisonSummary
) {
  voteTotalsTabNav.querySelectorAll('[data-vote-tab]').forEach((b) => {
    b.classList.toggle('active', b.dataset.voteTab === state.voteTotals.mode);
  });

  voteTotalsBody.innerHTML = '';
  // The "Seats" column counts electoral votes on EV-tally maps (presidential), so relabel it.
  const seatsHeader = voteTotalsTable.querySelector('th[data-sort-key="seats"]');
  if (seatsHeader) seatsHeader.textContent = state.mapConfig?.tally === 'electoralVotes' ? 'EV' : 'Seats';
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
      <td><span class="maps-party-cell"><span class="maps-party-swatch" style="background:${manifest.colourParty(partyRow.party)}"></span>${escapeHtml(manifest.labelParty(partyRow.party))}</span></td>
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
function wireVoteTotalsToggle() {
  voteTotalsToggle.addEventListener('click', () => {
    state.voteTotals.expanded = !state.voteTotals.expanded;
    if (!state.filteredSeatsSummary) return;
    renderVoteTotals();
  });
}

/**
 * Wires the vote-totals card collapse button. Mirrors the predict-window collapse pattern:
 * toggles `maps-vote-totals--collapsed` on the card (CSS hides the tabs, table, and Show all
 * button) and flips the glyph between ▲ (expanded) and ▼ (collapsed). The button itself is
 * hidden on mobile via a media query in mobile-sidebar.css.
 * @returns {void}
 */
function wireVoteTotalsCollapse() {
  voteTotalsCollapse.addEventListener('click', () => {
    const collapsed = voteTotalsCard.classList.toggle('maps-vote-totals--collapsed');
    voteTotalsCollapse.textContent = collapsed ? '▼' : '▲';
  });
}

/**
 * Attaches click and keyboard (Enter/Space) handlers to all [data-sort-key] table headers
 * to trigger sort changes and re-render the vote totals and right panel.
 * @returns {void}
 */
function wireVoteTotalsSorting() {
  document.querySelectorAll('th[data-sort-key]').forEach((header) => {
    const sortKey = header.getAttribute('data-sort-key');
    if (!sortKey) return;

    const trigger = () => {
      state.setSortDirection(sortKey);
      renderVoteTotals();
      syncRightPanelHeight();
    };

    header.addEventListener('click', trigger);
    header.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        trigger();
      }
    });
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
export function selectSeatBySearchQuery(query) {
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
  seatSearchInput.addEventListener('blur', () => {
    window.setTimeout(hideSeatSearchSuggestions, 120);
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

/** Closes all popup panels. */
function closeAllPopups() {
  document.querySelectorAll('.maps-control-popup').forEach((p) => { p.hidden = true; });
}

/**
 * Attaches click handlers to all [data-popup-action] buttons. 'toggle' opens the target panel
 * and closes all others; 'close' closes all panels. Guards against double-wiring via dataset flag.
 *
 * Covers the four map control popups: Filters, Choropleths, Data info (referendum only),
 * and Postcode accuracy warning.
 * @returns {void}
 */
function wirePopupPanels() {
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
export function initRegionTable() {
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

// Name of the seat whose detail popup is currently open, or null when nothing is open (or a
// region list-vote popup is showing instead). Lets refreshOpenSeatPopup re-render it in place
// after a predict projection swaps in new seat data.
let openSeatName = null;

// One-shot cache for a per-unit two-party trend bundle (the seat-trends artifact). Keyed by
// path so it stays correct if a future parliament ships its own trend file. The fetch fires
// lazily on the first seat-popup open for a trends-enabled parliament, never at initial load.
let stateTrendsPath = null;
let stateTrendsPromise = null;

/**
 * Lazily fetches (and memoises) the seat-trends bundle for `path`, resolving to its
 * `{ unitName: [{year, dem, rep}, ...] }` map. Returns `{}` when there is no path or the
 * fetch fails, so a missing artifact simply yields no chart rather than throwing.
 * @param {string|undefined} path - Relative data path from the manifest, or nullish.
 * @returns {Promise<Record<string, Array<{year:number, dem:number, rep:number}>>>}
 */
function loadStateTrends(path) {
  if (stateTrendsPromise && stateTrendsPath === path) return stateTrendsPromise;
  stateTrendsPath = path;
  if (!path) {
    stateTrendsPromise = Promise.resolve({});
    return stateTrendsPromise;
  }
  const base = page.dataBase || 'data';
  stateTrendsPromise = fetchJson(`${base}/${path}`)
    .then((payload) => payload?.units ?? payload ?? {})
    .catch(() => ({}));
  return stateTrendsPromise;
}

/**
 * Creates a .maps-popup-row element with the party colour bar, label, and injected values HTML.
 * CSS custom properties --maps-popup-bar-width and --maps-popup-bar-colour drive the bar.
 * @param {string} party - Party key for colour lookup.
 * @param {number} barWidth - Bar width percentage (0–75), scaled relative to the leading row.
 * @param {string} valuesHtml - Inner HTML for the .maps-popup-values div.
 * @param {string} [label] - Row label; defaults to the party name when omitted (e.g. a
 *   candidate name is passed for constituency popups, party labels for region summaries).
 * @returns {HTMLDivElement}
 */
function buildPopupRow(party, barWidth, valuesHtml, label) {
  const item = document.createElement('div');
  item.className = 'maps-popup-row';
  item.style.setProperty('--maps-popup-bar-width', `${barWidth}%`);
  item.style.setProperty('--maps-popup-bar-colour', manifest.colourParty(party));
  item.innerHTML = `
    <div class="maps-popup-party"><span class="maps-seat-icon" style="background:${manifest.colourParty(party)}"></span><span class="maps-popup-name">${escapeHtml(label ?? manifest.labelParty(party))}</span></div>
    <div class="maps-popup-values">${valuesHtml}</div>
  `;
  return item;
}

/**
 * Clears seatPopupList and renders a scaled bar-chart row for each entry in rows.
 * Computes maxPct internally so callers don't need to manage bar-width scaling.
 * @param {Array<{party: string, pct: number}>} rows - Sorted rows; each must have party and pct.
 * @param {function({party: string, pct: number}): string} getValuesHtml - Returns right-side values HTML for a row.
 * @param {number} [barCap=75] - Max bar width (%); lower it when the row's value text is wide
 *   (e.g. the member popup) so the bar doesn't run under the value.
 * @returns {void}
 */
function renderPopupRows(rows, getValuesHtml, barCap = 75) {
  // Scale all bars relative to the leading row so the top party always fills barCap.
  const maxPct = rows.reduce((max, row) => Math.max(max, row.pct), 0);
  seatPopupList.innerHTML = '';
  rows.forEach((row) => {
    // Bar width is proportional to pct / maxPct, capped to leave room for labels.
    const barWidth = maxPct > 0 ? Math.max(0, Math.min(barCap, (row.pct / maxPct) * barCap)) : 0;
    // getValuesHtml supplies the right-side content (vote share, delta, seat count, etc.)
    // which differs between the region popup and the constituency popup. row.label, when
    // present, overrides the party label (e.g. a candidate name in the seat popup).
    seatPopupList.appendChild(buildPopupRow(row.party, barWidth, getValuesHtml(row), row.label));
  });
}

/**
 * Appends a compact two-line (Dem/Rep) two-party-share chart to seatPopupList. The x-axis is
 * data-driven from the years present in `series`, so extending the artifact to earlier elections
 * needs no change here. Idempotent: removes any prior chart first, so re-renders (e.g. a predict
 * refresh) never stack duplicates. No-op for an empty series.
 * @param {Array<{year:number, dem:number, rep:number}>} series - Ascending by year.
 * @returns {void}
 */
function renderStateTrendChart(series) {
  seatPopupList.querySelector('.maps-state-trend-wrap')?.remove();
  if (!series?.length) return;

  const width = 428;
  const height = 132;
  const margin = { top: 22, right: 10, bottom: 20, left: 30 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const x = d3.scalePoint().domain(series.map((d) => d.year)).range([0, innerW]).padding(0.5);
  const y = d3.scaleLinear().domain([0, 100]).range([innerH, 0]);

  const svg = d3.create('svg')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('class', 'maps-state-trend-svg');
  const plot = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  // Reference gridlines + y labels at 0 / 50 (crossover) / 100.
  [0, 50, 100].forEach((tick) => {
    plot.append('line').attr('class', 'maps-state-trend-grid')
      .attr('x1', 0).attr('x2', innerW).attr('y1', y(tick)).attr('y2', y(tick));
    plot.append('text').attr('class', 'maps-state-trend-axis')
      .attr('x', -6).attr('y', y(tick)).attr('dy', '0.32em').attr('text-anchor', 'end')
      .text(tick);
  });
  // X-axis year labels, abbreviated ('00', '04', … '24').
  series.forEach((d) => {
    plot.append('text').attr('class', 'maps-state-trend-axis')
      .attr('x', x(d.year)).attr('y', innerH + 14).attr('text-anchor', 'middle')
      .text(String(d.year).slice(-2));
  });

  const drawLine = (key, colour) => {
    plot.append('path')
      .attr('d', d3.line().x((d) => x(d.year)).y((d) => y(d[key]))(series))
      .attr('fill', 'none').attr('stroke', colour).attr('stroke-width', 2);
    plot.selectAll(null).data(series).enter().append('circle')
      .attr('cx', (d) => x(d.year)).attr('cy', (d) => y(d[key]))
      .attr('r', 2.2).attr('fill', colour);
  };
  // Colours come from the manifest (single source of truth) — series keys are dem/rep,
  // but the manifest party keys are democrat/republican.
  drawLine('dem', manifest.colourParty('democrat'));
  drawLine('rep', manifest.colourParty('republican'));

  const wrap = document.createElement('div');
  wrap.className = 'maps-state-trend-wrap';
  wrap.innerHTML = '<div class="maps-state-trend-title">Two-party vote share</div>';
  wrap.appendChild(svg.node());
  seatPopupList.appendChild(wrap);
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
  // A region list-vote popup reuses the shared seatPopup element; clear the seat tracker so a
  // later refreshOpenSeatPopup doesn't draw a stale seat over this region view.
  openSeatName = null;
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

/**
 * Hides the seat detail popup and clears the tracked open seat name.
 * @returns {void}
 */
function hideSeatPopup() {
  if (!seatPopup) return;
  seatPopup.hidden = true;
  openSeatName = null;
}

/**
 * Re-renders the currently-open seat popup in place, if one is showing. Used after a predict
 * projection swaps in new seat data so the open popup reflects the projected votes/majority and
 * the (now non-zero) swing column, without re-zooming. No-op when no seat popup is open.
 * @returns {void}
 */
export function refreshOpenSeatPopup() {
  if (openSeatName && seatPopup && !seatPopup.hidden) renderSeatPopup(openSeatName);
}

/**
 * Renders the seat detail popup for seatName, showing majority, gain indicator, and a ranked
 * vote share bar chart with comparison deltas. Hides the popup if the seat is not found.
 * @param {string} seatName - Display name of the seat to show.
 * @returns {void}
 */
function renderSeatPopup(seatName) {
  // Resolve the seat object; hide the popup and bail if not found.
  const seatKey = seatLookupKey(seatName);
  const seat = state.electionData.seatsByKey.get(seatKey);
  if (!seat) {
    hideSeatPopup();
    return;
  }
  // Record the open seat so refreshOpenSeatPopup can re-render it after a predict projection.
  openSeatName = seatName;

  // Multi-member seat (several members, not a single winner): show each member with their
  // party colour, name and the years their seat was last and is next contested.
  if (seat.members?.length) {
    seatPopupTitle.textContent = seat.seat;
    seatPopupMeta.innerHTML =
      `<span class="maps-popup-meta-item">${escapeHtml(getRegionLabel(seat.region, state.currentRegionLabelsByKey))}</span>`;
    const rows = seat.members.map((member) => ({
      party: member.party,
      pct: 100,
      label: member.name,
      up: member.up,
    }));
    // Terms are 6 years, so a seat was last contested six years before it is next up. The
    // bar is just a colour accent here, so cap it short to clear the wide "last/up" value.
    // Guard an unresolved cycle year (member missing `class`, or no senateClassNextElection)
    // so the row shows just the name rather than "last NaN · up undefined".
    renderPopupRows(rows, (row) => (row.up ? `<span>last ${row.up - 6} · up ${row.up}</span>` : ''), 70);
    seatPopup.hidden = false;
    return;
  }

  // Resolve gain indicator and majority stats via Seat instance methods so the
  // logic stays in one place and this function has no arithmetic of its own.
  const comparisonSeat = state.comparisonElectionData?.seatsByKey.get(seatKey) || null;
  const gainFrom = seat.gainFromParty(comparisonSeat?.winner || null);
  const majority = seat.majorityStats();

  // Model and referendum elections have no meaningful turnout or raw majority to display.
  const showCounts = !state.currentElection.model && !state.isReferendumType;

  // Populate title (with electoral votes for presidential states) and meta line.
  seatPopupTitle.textContent = seat.ev ? `${seat.seat} - ${formatInt(seat.ev)} EV` : seat.seat;
  seatPopupMeta.innerHTML = `
    ${gainFrom ? `<span class="maps-popup-meta-item">FROM ${escapeHtml(manifest.labelParty(gainFrom))} <span class="maps-seat-icon" style="background:${manifest.colourParty(gainFrom)}"></span></span>` : ''}
    <span class="maps-popup-meta-item">${escapeHtml(getRegionLabel(seat.region, state.currentRegionLabelsByKey))}</span>
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
      // Label by candidate name where available (name mode); party label otherwise.
      return { party, pct, delta, label: seat.candidates?.[party] };
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

  // When this parliament ships a seat-trends bundle, lazily fetch it and append the trend chart.
  // Gated purely by the manifest flag (no parliament literal), so any parliament can opt in.
  const trendConfig = manifest.parliamentConfig(state.currentParliament);
  if (trendConfig.seatTrendsAvailable) {
    const requestedSeat = seatName;
    loadStateTrends(trendConfig.seatTrendsDataPath).then((trends) => {
      // Bail if the popup was closed or switched to another seat while the fetch was in flight.
      if (openSeatName !== requestedSeat || seatPopup.hidden) return;
      renderStateTrendChart(buildStateTrendSeries(trends?.[seat.seat] ?? trends?.[seatName]));
    });
  }
}

const seatPopupClose = document.getElementById('mapsSeatPopupClose');

/**
 * Closes the seat detail popup and deselects the active map path when the close button is clicked.
 * @returns {void}
 */
function wireSeatPopup() {
  seatPopupClose.addEventListener('click', () => {
    hideSeatPopup();
    mapInteraction.clearSelection?.();
  });
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
 * Renders the filtered seats, sorted alphabetically, into the seat list panel. Each row shows
 * the winner colour, name, and gain-from indicator. Click zooms and opens the seat popup.
 * Reads seats and comparison data directly from state.
 * @returns {void}
 */
function renderSeatList() {
  const seats = state.listFilteredSeats;
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
        <span class="maps-seat-icon maps-seat-owner-icon" style="background:${manifest.colourParty(winnerKey)}" title="${escapeHtml(manifest.labelParty(winnerKey))}"></span>
        <span class="maps-seat-name">${escapeHtml(seatName)}</span>
      </span>
      <span class="maps-seat-meta">
        ${gainedFrom ? `<span class="maps-seat-gain"><span class="maps-seat-gain-label">GAIN FROM</span><span class="maps-seat-icon" style="background:${manifest.colourParty(gainedFrom)}" title="${escapeHtml(manifest.labelParty(gainedFrom))}"></span></span>` : '<span class="maps-seat-gain-placeholder"></span>'}
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
const seatCard = document.getElementById('mapsSeatCard');
const voteTotalsCard = document.getElementById('mapsVoteTotalsCard');

/**
 * Syncs the right panel's height to the map stage height so the two columns stay aligned.
 * On mobile (≤980px) the panel stacks below the map, so height constraints are cleared instead.
 * No-ops silently when either element is absent from the DOM.
 * @returns {void}
 */
export function syncRightPanelHeight() {
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

// Minimum allocated height (in px) needed to render the seat card. Roughly the
// card head + one seat row; below this the card looks cramped, so we hide it
// entirely rather than render a stub.
const SEAT_CARD_MIN_HEIGHT = 6 * 16;

/**
 * Hides the seat card when the rail's leftover height (after vote-totals and
 * predict claim their content sizes) is too small to render the card head plus
 * one seat row. Predict can still shrink to fit the rail (its inner grid then
 * scrolls), but never to free space for the seat list — that's handled by the
 * vote-totals/predict flex configuration in styles.css.
 *
 * On mobile (≤980px) the rail is unbounded and stacks vertically, so the seat
 * card always renders.
 * @returns {void}
 */
function updateSeatCardVisibility() {
  if (window.innerWidth <= 980) {
    seatCard.hidden = false;
    return;
  }
  // Show the card to measure what flex would allocate, then hide if too small.
  // Hiding seatCard is loop-safe because the ResizeObserver only watches the
  // rail, predict, and vote-totals — none of which change size when the seat's
  // hidden attribute flips (predict has flex-shrink:1 but its rendered height
  // doesn't depend on seat's basis 0).
  seatCard.hidden = false;
  const allocated = seatCard.getBoundingClientRect().height;
  if (allocated < SEAT_CARD_MIN_HEIGHT) seatCard.hidden = true;
}

const seatCardObserver = new ResizeObserver(() => updateSeatCardVisibility());
seatCardObserver.observe(mapsPanelRight);
// The predict window is a predict-feature element (predict-view.js owns it) and is absent on
// pages without the feature. Observe it by id when present so the seat card reflows as the
// predict window resizes — without coupling core dom.js to the predict module.
const predictWindowEl = document.getElementById('mapsPredictWindow');
if (predictWindowEl) seatCardObserver.observe(predictWindowEl);
seatCardObserver.observe(voteTotalsCard);

// ─── TopoJSON map ────────────────────────────────────────────────────────────

const mapSvg = document.querySelector('.maps-svg');
const mapContent = document.getElementById('mapContent');
const zoomValue = document.getElementById('mapsZoomValue');

// Module-level map interaction handle. Replaced on every renderTopoMap call;
// callers within this module (toolbar buttons, seat list, postcode search) use it to drive
// the map without holding references to internal D3 selections.
// Stub methods are in effect until the first renderTopoMap call.
let mapInteraction = {
  zoomBy: () => {},
  reset: () => {},
  clearSelection: () => {},
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
 * assigned to the module-level mapInteraction binding used within this module.
 *
 * Public API:
 *   zoomBy(factor)                  — scale by factor in a short transition
 *   reset()                         — return to initial zoom and clear selection
 *   clearSelection()                — remove the active seat highlight
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
   * Registers a rendered SVG path node for a seat so zoomToSeat can
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
 * instance to the module-level mapInteraction binding used within this module.
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
  // The projection is selected per map via mapMode.projection ("albersUsa" for
  // US maps, default "mercator" for UK maps). fitSize scales and centres it so
  // the full feature collection fills the viewBox; path converts GeoJSON
  // geometries to SVG path data strings using that projection.

  const projectionFactory =
    state.mapConfig?.projection === 'albersUsa' ? d3.geoAlbersUsa : d3.geoMercator;
  const projection = projectionFactory().fitSize([width, height], featureCollection);
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
      // Feature has no matching seat in the election data — e.g. an area not contested this
      // cycle. Use the map's neutralFill when configured (so "not contested" reads as
      // neutral, not an Others win), otherwise the Others colour.
      if (!seat) return state.mapConfig?.neutralFill || manifest.colourParty('others');

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

  // Build the seatKey → SVG path node index so zoomToSeat can find
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
    <div class="maps-choropleth-legend-title">${escapeHtml(legend.title)}</div>
    <div class="maps-choropleth-legend-bar" style="background:${gradient}"></div>
    <div class="maps-choropleth-legend-labels">
      <span>${escapeHtml(legend.minLabel)}</span>
      ${legend.isDelta ? `<span>${escapeHtml(legend.midLabel)}</span>` : ''}
      <span>${escapeHtml(legend.maxLabel)}</span>
    </div>
  `;
  choroplethLegend.hidden = false;
}

// ─── Map init ────────────────────────────────────────────────────────────────

/**
 * Wires all DOM-owned controls. Call once during page boot alongside wireInit.
 * @returns {void}
 */
export function wireInit() {
  wirePopupPanels();
  wireMapInteractions();
  wireMapViewControls();
  wireSeatSearch();
  wireVoteTotalsToggle();
  wireVoteTotalsCollapse();
  wireVoteTotalsSorting();
  wireWindowResize();
  wireSeatPopup();
}

/**
 * Runs all once-per-election DOM initialisations. Must be called after state.setupMapData() so
 * mapConfig and listRegionSummary are already set.
 *
 * Rebuilds the vote-totals tab nav, populates the region-table overlay (hidden for non-list
 * elections), then runs any feature-registered map-init hooks (e.g. postcode search).
 *
 * @returns {void}
 */
export function renderMapInit() {
  initVoteTotalsTabs();
  initRegionTable();
  for (const hook of mapInitHooks) hook();
}

// Per-map-init hooks registered by opt-in feature modules (e.g. postcode search). Invoked by
// renderMapInit after the core inits, so a feature can react to each election load without
// core dom.js importing it.
const mapInitHooks = [];

/**
 * Registers a callback to run on every map (re)initialisation. Used by feature modules the
 * page entry opts into.
 * @param {() => void} hook
 * @returns {void}
 */
export function registerMapInitHook(hook) {
  mapInitHooks.push(hook);
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

/**
 * Syncs the right panel height to the map on window resize. Feature views that need their
 * own resize handling (e.g. the poll tracker re-rendering its chart) wire it themselves in
 * their view module so core stays free of feature code.
 * @returns {void}
 */
function wireWindowResize() {
  window.addEventListener('resize', () => {
    syncRightPanelHeight();
  });
}

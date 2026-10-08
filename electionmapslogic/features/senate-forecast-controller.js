import { activeMapMode, ElectionData, manifest, page, state } from "../state.js";
import { fetchJson } from "../files.js";
import { seatLookupKey } from "../utils.js";
import {
  initRegionTable,
  refreshOpenSeatPopup,
  renderHeader,
  renderMap,
  renderMapControlOptions,
  syncRightPanelHeight,
} from "../dom.js";
import { buildSenateChamber } from "./senate-chamber.js";
import { loadSenateSpecialBaseline } from "./senate-baseline.js";
import { renderSenateForecastTabs } from "./senate-forecast-view.js";

/**
 * Adds the seats-up / full-chamber output switch to the current saved Senate forecast.
 * Its input is the loaded forecast, so switching never recalculates the model's winners.
 * @returns {Promise<void>}
 */
export async function activateSenateForecastView() {
  renderSenateForecastTabs();
  const config = manifest.parliamentConfig(state.currentParliament);
  if (state.view !== "election" || config.predict?.model !== "senate"
      || !state.currentElection?.model
      || state.currentElection.id !== config.predictAnchorElectionId) return;

  const forecastData = state.electionData;
  let regularComparison = state.comparisonElectionData;
  const election = state.currentElection;
  const parliament = state.currentParliament;
  let displayedData = forecastData;
  const isCurrent = () => state.currentElection === election && state.view === "election"
    && state.currentParliament === parliament && state.electionData === displayedData;
  const forecastKeys = new Set(forecastData.currentSeats.map((seat) => seatLookupKey(seat.seat)));
  const specials = (activeMapMode().senateSpecialElections || [])
    .filter((special) => Number(special.year) === Number(config.nextElectionYear)
      && forecastKeys.has(seatLookupKey(special.seat)));

  // Suppress partial deltas while additional per-seat results are still loading.
  state.comparisonElectionData = null;
  refreshForecast();
  if (!regularComparison) {
    const regularElection = manifest.getElectionFromId(election.comparisonElectionId);
    if (regularElection) {
      try {
        const { dataFile } = manifest.resolveElectionFiles(regularElection);
        const regularData = await fetchJson(`${page.dataBase || "data"}/${dataFile}`);
        if (!isCurrent()) return;
        regularComparison = new ElectionData(regularData);
      } catch (error) {
        console.error("Senate forecast regular baseline load failed", error);
      }
      if (!isCurrent()) return;
    }
  }
  const specialSeats = await loadSenateSpecialBaseline(specials);
  if (!isCurrent()) return;
  const comparisonData = completeForecastComparison(forecastKeys, regularComparison, specials, specialSeats);
  state.comparisonElectionData = comparisonData;
  refreshForecast();

  const chamberElection = manifest.getElectionFromId(config.predict.chamberElectionId);
  if (!chamberElection) return;
  let chamberSeats;
  try {
    const { dataFile } = manifest.resolveElectionFiles(chamberElection);
    const chamberData = await fetchJson(`${page.dataBase || "data"}/${dataFile}`);
    if (!isCurrent()) return;
    chamberSeats = new ElectionData(chamberData).currentSeats;
  } catch (error) {
    console.error("Senate forecast chamber snapshot load failed", error);
    return;
  }
  // An absent snapshot cannot produce a full chamber; keep the loaded seats-up map usable.
  if (!chamberSeats.length) return;

  const specialClassBySeat = new Map(
    specials
      .map((special) => [seatLookupKey(special.seat), Number(special.class) || 2]),
  );
  const tabs = config.predict.tabs || [
    { key: "seatsup", label: `Seats up (${config.nextElectionYear})` },
    { key: "chamber", label: "Full Senate" },
  ];
  let activeTab = "seatsup";

  function renderTabs() {
    renderSenateForecastTabs({ tabs, activeTab, onTabChange: changeTab });
  }

  function changeTab(key) {
    if (!isCurrent() || key === activeTab || !tabs.some((tab) => tab.key === key)) return;
    activeTab = key;
    const fullChamber = key === "chamber";
    election.multiMember = fullChamber;
    if (fullChamber) {
      const seats = buildSenateChamber(chamberSeats, forecastData.currentSeats, specialClassBySeat);
      state.setElectionDataFromSeats(seats, forecastData.electionName);
      state.comparisonElectionData = ElectionData.fromSeats(chamberSeats);
    } else {
      state.electionData = forecastData;
      state.comparisonElectionData = comparisonData;
    }
    displayedData = state.electionData;
    refreshForecast();
    renderTabs();
  }

  function refreshForecast() {
    state.setupMapData();
    renderHeader(state.electionData.summary.text);
    renderMapControlOptions();
    renderMap(true);
    initRegionTable();
    syncRightPanelHeight();
    refreshOpenSeatPopup();
  }

  renderTabs();
}

/** Builds exactly one usable result for each forecast key; required overrides cannot fall back. */
function completeForecastComparison(forecastKeys, regularComparison, specials, specialSeats) {
  const specialKeys = new Set(specials.map((special) => seatLookupKey(special.seat)));
  const specialByKey = ElectionData.buildSeatIndex(specialSeats);
  const seats = [];
  for (const key of forecastKeys) {
    const seat = specialKeys.has(key)
      ? specialByKey.get(key)
      : regularComparison?.seatsByKey.get(key);
    if (!seat?.winner || !Number.isFinite(seat.turnout) || seat.turnout <= 0
        || !(seat.votes[seat.winner] > 0)
        || Object.values(seat.votes).some((votes) => !Number.isFinite(votes))) return null;
    seats.push(seat);
  }
  return ElectionData.fromSeats(seats);
}

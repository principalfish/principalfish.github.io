import { ElectionData, manifest, page, state } from "../state.js";
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

  const chamberElection = manifest.getElectionFromId(config.predict.chamberElectionId);
  if (!chamberElection) return;
  const forecastData = state.electionData;
  const comparisonData = state.comparisonElectionData;
  const election = state.currentElection;
  let chamberSeats;
  try {
    const { dataFile } = manifest.resolveElectionFiles(chamberElection);
    const chamberData = await fetchJson(`${page.dataBase || "data"}/${dataFile}`);
    chamberSeats = new ElectionData(chamberData).currentSeats;
  } catch (error) {
    console.error("Senate forecast chamber snapshot load failed", error);
    return;
  }
  // An absent snapshot cannot produce a full chamber; keep the loaded seats-up map usable.
  if (!chamberSeats.length) return;

  const specialClassBySeat = new Map(
    (state.mapConfig.senateSpecialElections || [])
      .filter((special) => Number(special.year) === Number(config.nextElectionYear))
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
    if (key === activeTab || !tabs.some((tab) => tab.key === key)) return;
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

    state.setupMapData();
    renderHeader(state.electionData.summary.text);
    renderMapControlOptions();
    renderMap(true);
    initRegionTable();
    syncRightPanelHeight();
    refreshOpenSeatPopup();
    renderTabs();
  }

  renderTabs();
}

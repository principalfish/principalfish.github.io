import { manifest, page, state } from "./state.js";
import { fetchJson } from "./files.js";
import { renderHeader, renderVoteTotals, refreshOpenSeatPopup } from "./dom.js";
import { decodeUncertainty } from "./uncertainty.js";

/** Load optional saved-run uncertainty after the ordinary election is already usable. */
export async function activateUncertaintyView() {
  const election = state.currentElection;
  const parliament = state.currentParliament;
  const config = manifest.parliamentConfig(parliament);
  const electionData = config.uncertaintyModel === "senate"
    ? state.electionData?.uncertaintySource ?? state.electionData : state.electionData;
  const identity = electionData?.uncertaintyId;
  const path = config.uncertaintyDataPath;
  if (state.view !== "election" || !election?.model || !path || !electionData?.uncertaintyId) return;
  const isCurrent = () => state.view === "election" && state.currentElection === election
    && state.currentParliament === parliament && electionData.uncertaintyId === identity
    && (state.electionData === electionData
      || (manifest.parliamentConfig(parliament).uncertaintyModel === "senate"
        && state.electionData?.uncertaintySource === electionData
        && state.electionData.uncertaintyId === identity));
  try {
    const payload = await fetchJson(`${page.dataBase || "data"}/${path}`);
    if (!isCurrent()) return;
    electionData.uncertainty = decodeUncertainty(
      payload, electionData.uncertaintyId,
      (party) => manifest.resolvePartyRef(party), manifest.regionsById,
      { model: manifest.parliamentConfig(parliament).uncertaintyModel,
        mapId: election.mapId, seats: electionData.currentSeats },
    );
  } catch {
    if (!isCurrent()) return;
    electionData.uncertainty = null;
  }
  renderHeader(state.electionData.summary.text);
  renderVoteTotals();
  refreshOpenSeatPopup();
}

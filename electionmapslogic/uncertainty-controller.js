import { manifest, page, state } from "./state.js";
import { fetchJson } from "./files.js";
import { renderHeader, renderVoteTotals, refreshOpenSeatPopup } from "./dom.js";
import { decodeUncertainty } from "./uncertainty.js";

/** Load optional saved-run uncertainty after the ordinary election is already usable. */
export async function activateUncertaintyView() {
  const election = state.currentElection;
  const parliament = state.currentParliament;
  const electionData = state.electionData;
  const path = manifest.parliamentConfig(parliament).uncertaintyDataPath;
  if (state.view !== "election" || !election?.model || !path || !electionData?.uncertaintyId) return;
  const isCurrent = () => state.view === "election" && state.currentElection === election
    && state.currentParliament === parliament && state.electionData === electionData;
  try {
    const payload = await fetchJson(`${page.dataBase || "data"}/${path}`);
    if (!isCurrent()) return;
    electionData.uncertainty = decodeUncertainty(
      payload, electionData.uncertaintyId,
      (party) => manifest.resolvePartyRef(party), manifest.regionsById,
    );
  } catch {
    if (!isCurrent()) return;
    electionData.uncertainty = null;
  }
  renderHeader(electionData.summary.text);
  renderVoteTotals();
  refreshOpenSeatPopup();
}

import {
  activeUncertainty, intervalPercent, outsideSeatRange, uncertaintyForTotals, uncertaintyHeadline,
} from "./uncertainty.js";

/** Append the simulated seats and odds line, in the headline area. */
export function renderUncertaintyHeader(container, summary, labelParty) {
  const odds = uncertaintyHeadline(summary, labelParty);
  if (!odds) return;
  const span = document.createElement("span");
  span.className = "maps-subtitle-uncertainty";
  span.textContent = odds;
  container.appendChild(span);
}

/**
 * Append the exported reading note in full, nowcast sentence included: it is the one place the
 * header says the odds are "if an election were held today". The totals table names it in
 * aria-describedby by its id.
 */
export function renderUncertaintyBasis(container, summary) {
  if (typeof summary?.basis !== "string" || !summary.basis) return;
  const basis = document.createElement("span");
  basis.id = "mapsUncertaintyBasis";
  basis.className = "maps-subtitle-basis";
  basis.textContent = summary.basis;
  container.appendChild(basis);
}

export function renderUncertaintyToggle(button, state) {
  const uncertainty = uncertaintyForTotals(state);
  const available = !!uncertainty && Object.keys(uncertainty.parties).length > 0;
  const visible = available && state.voteTotals.showUncertainty;
  if (button) {
    button.hidden = !available;
    button.setAttribute("aria-pressed", String(visible));
    button.textContent = visible ? "Hide uncertainty" : "Show uncertainty";
  }
  return visible;
}

export function wireUncertaintyToggle(button, state, onChange) {
  button?.addEventListener("click", () => {
    if (!activeUncertainty(state) || !uncertaintyForTotals(state)) return;
    state.voteTotals.showUncertainty = !state.voteTotals.showUncertainty;
    onChange();
  });
}

/**
 * A saved run with uncertainty shows two seat figures, so the deterministic one is named by its
 * method; everywhere else the column keeps its plain label ("EV" on electoral-vote tallies).
 */
export function renderSeatsHeader(header, state) {
  if (!header) return;
  const uniformSwing = !!activeUncertainty(state);
  header.textContent = uniformSwing ? "Uniform swing"
    : state.mapConfig?.tally === "electoralVotes" ? "EV" : "Seats";
  header.setAttribute("title", uniformSwing ? "Uniform-swing projection. Sort by seats" : "Sort by seats");
}

/** HTML: a dagger, explained to pointer and screen-reader users, when the count leaves its range. */
export function outsideRangeMarker(seats, values, interval) {
  if (!outsideSeatRange(seats, values)) return "";
  const note = `outside its ${intervalPercent(interval)} simulated range`;
  return `<span class="maps-outside-range" title="Uniform-swing count ${note}" aria-hidden="true">†</span>`
    + `<span class="maps-visually-hidden"> (${note})</span>`;
}

import {
  activeUncertainty, uncertaintyForTotals, uncertaintyHeadline,
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
  const available = !!uncertaintyForTotals(state);
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

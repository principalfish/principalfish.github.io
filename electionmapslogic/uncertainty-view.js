import { activeUncertainty, uncertaintyForTotals, uncertaintyHeadline } from "./uncertainty.js";

/** Append the odds and their explanation once, in the headline area. */
export function renderUncertaintyHeader(container, summary, labelParty) {
  if (!summary) return;
  const odds = uncertaintyHeadline(summary, labelParty);
  if (odds) {
    const span = document.createElement("span");
    span.className = "maps-subtitle-uncertainty";
    span.textContent = odds;
    container.appendChild(span);
  }
  if (typeof summary.basis === "string" && summary.basis) {
    const basis = document.createElement("span");
    basis.id = "mapsUncertaintyBasis";
    basis.className = "maps-subtitle-basis";
    basis.textContent = odds
      ? summary.basis.replace(/^Nowcast: if an election were held today\.\s*/, "")
      : summary.basis;
    container.appendChild(basis);
  }
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

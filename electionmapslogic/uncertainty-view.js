import { activeUncertainty, uncertaintyHeadline } from "./uncertainty.js";

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
 * page says the odds are "if an election were held today". It sits in the footer below the map;
 * the totals table names it in aria-describedby by its id.
 */
export function renderUncertaintyBasis(container, summary) {
  if (typeof summary?.basis !== "string" || !summary.basis) return;
  const basis = document.createElement("span");
  basis.id = "mapsUncertaintyBasis";
  basis.className = "maps-map-footer-basis";
  basis.textContent = summary.basis;
  container.appendChild(basis);
}

/**
 * Show the "Show with modelled uncertainty" checkbox only when the view has a stored simulation
 * (a saved run's summary), ticked from the reader's choice.
 */
export function renderModelledUncertaintyToggle(control, input, state) {
  if (control) control.hidden = !activeUncertainty(state);
  if (input) input.checked = Boolean(state.showModelledUncertainty);
}

/** Record the reader's choice for the rest of the page's life, then redraw what it switches. */
export function wireModelledUncertaintyToggle(input, state, onChange) {
  input?.addEventListener("change", () => {
    state.showModelledUncertainty = input.checked;
    onChange();
  });
}

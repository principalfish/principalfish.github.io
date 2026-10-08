/**
 * Shows the saved Senate forecast's output switch above the vote totals. Passing no
 * options hides the switch on other elections; the controller owns the active view.
 * @param {{tabs: Array<{key: string, label: string}>, activeTab: string,
 *   onTabChange: (key: string) => void}|null} [options]
 * @returns {void}
 */
export function renderSenateForecastTabs(options = null) {
  const nav = document.getElementById("mapsSenateForecastTabNav");
  if (!nav) return;
  nav.hidden = !options;
  nav.replaceChildren();
  if (!options) return;

  options.tabs.forEach(({ key, label }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `maps-predict-tab-btn${options.activeTab === key ? " active" : ""}`;
    button.textContent = label;
    button.setAttribute("aria-pressed", String(options.activeTab === key));
    button.addEventListener("click", () => options.onTabChange(key));
    nav.appendChild(button);
  });
}

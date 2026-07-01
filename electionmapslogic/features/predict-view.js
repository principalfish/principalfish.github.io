// ─── Predict (view) ───────────────────────────────────────────────────────────
//
// Renders the predict input window (regional uniform-swing grid), its tab strip, and
// wires the action buttons. Extracted from dom.js so it ships only in the bundle of a
// page whose manifest enables the `predict` feature. The action handlers themselves live
// in predict-controller.js and are registered here via setPredictActionHandlers.

import { state, manifest } from '../state.js';
import { escapeHtml, clampNumber } from '../utils.js';

const predictWindow = document.getElementById('mapsPredictWindow');
const predictGrid = document.getElementById('mapsPredictGrid');
const predictTabNav = document.getElementById('mapsPredictTabNav');
const predictTitle = document.getElementById('mapsPredictTitle');

// Action handlers wired by the predict controller (predict-controller.js) so predict-mode
// actions can run their projection / URL-share / forecast-load logic without dom.js needing to
// import that logic. Keys: 'apply' | 'submit' | 'share' | 'reset'.
let predictActionHandlers = {};

/**
 * Registers the orchestrator-supplied callbacks that fire on predict-action button
 * clicks. Keys must match the `[data-predict-action]` attributes on the action buttons
 * in `index.html`; missing keys cause the matching button click to no-op.
 *
 * Called once per predict-mode entry from `activatePredictView` in `predict-controller.js`.
 * Subsequent calls overwrite the registration wholesale (a `null` argument resets to
 * an empty object, disabling all action buttons until re-registered).
 *
 * The action buttons themselves are wired by `wirePredictControls` exactly once per
 * page lifetime (called from `wireInit`); only the handler bag is re-registered.
 *
 * @param {{apply?: () => (void | Promise<void>),
 *          submit?: () => void,
 *          share?: () => Promise<void>,
 *          reset?: () => void} | null | undefined} handlers
 *   Map keyed by `data-predict-action` value. Returned promises are awaited implicitly
 *   by the click delegator (errors are surfaced to the console by the browser).
 * @returns {void}
 */
export function setPredictActionHandlers(handlers) {
  predictActionHandlers = handlers || {};
}

/**
 * Shows or hides the predict input window. Drives layout swaps between predict mode and
 * the regular election / poll-tracker views.
 *
 * When `visible` is true:
 * - Unhides `mapsPredictWindow` so the input grid + action buttons render.
 * - Adds `maps-predict-window-fill` so the predict window sizes to its content at the top
 *   of the right rail; the seat card sits below and grows into the leftover space.
 * - Adds `maps-predict-mode` to the body for top-level CSS hooks.
 *
 * When `visible` is false: reverses all three. The seat card is never hidden here — its
 * visibility is managed reactively by `seatCardObserver`, which hides it only when the
 * remaining height falls below `SEAT_CARD_MIN_HEIGHT`. The predict-window collapse button
 * (▲ / ▼ inside the window's header) toggles the same fill class so the user can shrink
 * the predict window and read the seat list without leaving predict mode entirely.
 *
 * @param {boolean} visible
 * @returns {void}
 */
export function setPredictWindowVisible(visible) {
  predictWindow.hidden = !visible;
  predictWindow.classList.toggle('maps-predict-window-fill', visible);
  document.body.classList.toggle('maps-predict-mode', visible);
}

/**
 * Renders the predict window contents from `state.predictModel`. Driven from three
 * places: the predict-mode entry (`activatePredictView`), each action
 * handler in predict-controller.js after it mutates the model, and the inline event handlers
 * in this file (tab click, aggregate toggle, input change) when they need to repaint
 * after a model state change.
 *
 * Renders in two passes:
 * 1. Tab strip — hidden when `model.tabs` is empty/missing (Westminster), populated
 *    from the manifest tabs list otherwise (Holyrood: Constituency / List).
 * 2. Grid — one table per `model.gridSections()` entry, with rows from `model.regions()`
 *    and columns from each section's `columnKeys`.
 *
 * No-ops silently when no model is set (e.g. user is on the election or poll-tracker
 * view) or when the predict window markup isn't in the DOM.
 *
 * @returns {void}
 */
export function renderPredict() {
  const model = state.predictModel;
  if (!model || !predictWindow) return;
  if (predictTitle) predictTitle.textContent = model.title;
  if (predictTabNav) {
    const hasTabs = (model.tabs?.length || 0) > 0;
    predictTabNav.hidden = !hasTabs;
    if (hasTabs) renderPredictTabs(model);
    else predictTabNav.innerHTML = '';
  }
  renderPredictGrid(model);
}

/**
 * Renders the ballot tab strip from `model.tabs` (e.g. Constituency / List for Holyrood
 * predict). Empties the tab nav and rebuilds it from scratch each call — cheaper than
 * diffing for the typical 2-tab case and lets `renderPredict` be a pure repaint.
 *
 * Each tab button carries an inline click handler that:
 * 1. Calls `model.setActiveTab(key)` to swap which input map is read/written by
 *    `currentInputMap` / `currentBaselineMap`.
 * 2. Re-invokes `renderPredict` so the grid surfaces the active tab's inputs.
 *
 * @param {object} model - The predict model (must expose `tabs`, `activeTab`,
 *   `setActiveTab`).
 * @returns {void}
 */
function renderPredictTabs(model) {
  predictTabNav.innerHTML = '';
  model.tabs.forEach(({ key, label }) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `maps-predict-tab-btn${model.activeTab === key ? ' active' : ''}`;
    btn.textContent = label;
    btn.addEventListener('click', () => {
      // Switching ballot re-routes share reads/writes via currentInputMap; re-render so
      // the grid surfaces the active tab's inputs instead of the previous tab's.
      model.setActiveTab(key);
      renderPredict();
      // Some models (Senate) use tabs to switch the OUTPUT view — seats-up vs full-chamber —
      // rather than which ballot is edited, so a tab click must re-run the projection to repaint
      // the map, not just the grid. AMS models leave the flag unset and keep the grid-only swap.
      if (model.reprojectOnTabChange) predictActionHandlers.tabChange?.();
    });
    predictTabNav.appendChild(btn);
  });
}

/**
 * Renders the predict input grid into `predictGrid`. Builds one labelled table per
 * `model.gridSections()` entry; each table has a header row of party swatches, one body
 * row per region from the section's `regions` list, and an 'other' total cell at the
 * end of every body row showing the implied share of unmodelled parties.
 *
 * Three live event handlers are wired per render pass:
 * - **Aggregate toggle** (only on `row.isAggregate` rows when an aggregate is configured).
 *   Clicking the "Show regions" / "Hide regions" button delegates to
 *   `model.setAggregateExpanded`, which propagates the aggregate's user inputs onto
 *   each empty sub-region (on expand) or drops sub-region inputs (on collapse), then
 *   re-renders the grid so the new row layout appears.
 * - **Numeric input change** (every editable share cell). Fires on blur / Enter, clamps
 *   the entered value into `[0, 100]`, rounds to integer, persists via `model.setShare`,
 *   and updates the row's 'other' cell so the displayed residual stays consistent.
 *   Aggregate-row inputs are `disabled` while expanded — sub-rows are the source of
 *   truth in that state.
 * - **Closure-scoped `refreshOther`** — invoked by each input's change handler to
 *   surface the negative-residual styling when the user's entered shares sum above 100.
 *
 * The closure-scoped `otherCellByRegion` map keeps a reference to each row's 'other'
 * cell so input handlers can repaint it directly without re-rendering the entire grid.
 *
 * @param {object} model - The predict model (must expose `gridSections`, `regions`,
 *   `getShare`, `getOtherShare`, `setShare`, `resolveColumnPartyKey`, plus
 *   `aggregateExpanded` / `setAggregateExpanded` if `aggregateConfig` is set).
 * @returns {void}
 */
function renderPredictGrid(model) {
  predictGrid.innerHTML = '';
  const otherCellByRegion = new Map();

  /** Repaints a region's 'other' cell from `model.getOtherShare`, toggling the
   * negative-residual style when entered shares sum above 100 (other < 0). */
  const refreshOther = (regionKey) => {
    const cell = otherCellByRegion.get(regionKey);
    if (!cell) return;
    const other = model.getOtherShare(regionKey);
    cell.textContent = String(other);
    cell.classList.toggle('maps-predict-grid-total-over', other < 0);
  };

  model.gridSections().forEach((section) => {
    if (!section.regions.length) return;

    const wrap = document.createElement('section');
    wrap.className = `maps-predict-grid-section maps-predict-grid-section-${section.id}`;
    if (section.title) {
      const heading = document.createElement('h4');
      heading.className = 'maps-predict-grid-section-title';
      heading.textContent = section.title;
      wrap.appendChild(heading);
    }

    const table = document.createElement('table');
    table.className = 'maps-predict-grid-table';

    // Header row.
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    const regionTh = document.createElement('th');
    regionTh.textContent = section.blankRegionHeader ? '' : 'Region';
    headRow.appendChild(regionTh);
    section.columnKeys.forEach((columnKey) => {
      const th = document.createElement('th');
      // Virtual columns (e.g. 'nat') carry their own title + swatch class in the manifest;
      // plain party columns fall back to the manifest party label + colour.
      const meta = model.columnMeta(columnKey);
      if (meta) {
        if (meta.title) th.title = meta.title;
        th.innerHTML = `<span class="maps-party-swatch ${meta.swatchClass || ''}" aria-hidden="true"></span>`;
      } else {
        th.title = manifest.labelParty(columnKey);
        th.innerHTML = `<span class="maps-party-swatch" style="background:${manifest.colourParty(columnKey)}" aria-hidden="true"></span>`;
      }
      headRow.appendChild(th);
    });
    const otherTh = document.createElement('th');
    otherTh.title = 'Other';
    otherTh.innerHTML = '<span class="maps-party-swatch maps-party-swatch-other" aria-hidden="true"></span>';
    headRow.appendChild(otherTh);
    thead.appendChild(headRow);
    table.appendChild(thead);

    // Body rows.
    const tbody = document.createElement('tbody');
    section.regions.forEach((row) => {
      const tr = document.createElement('tr');

      const labelTd = document.createElement('td');
      labelTd.className = 'maps-predict-grid-region';
      if (row.isAggregate) {
        const labelWrap = document.createElement('div');
        labelWrap.className = 'maps-predict-region-label-wrap';
        labelWrap.innerHTML = `<span>${escapeHtml(row.regionLabel)}</span>`;
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'maps-predict-expand-btn';
        toggle.textContent = model.aggregateExpanded ? 'Hide regions' : 'Show regions';
        toggle.addEventListener('click', () => {
          // Toggle drives input-map propagation inside the model (aggregate→subs on
          // expand, drop subs on collapse); re-render rebuilds rows from model.regions()
          // so the new layout is reflected in the DOM.
          model.setAggregateExpanded(!model.aggregateExpanded);
          renderPredict();
        });
        labelWrap.appendChild(toggle);
        labelTd.appendChild(labelWrap);
      } else {
        labelTd.textContent = row.predictLabel;
        if (row.isAggregateChild) labelTd.classList.add('maps-predict-grid-region-child');
      }
      tr.appendChild(labelTd);

      section.columnKeys.forEach((columnKey) => {
        const partyKey = model.resolveColumnPartyKey(row.regionKey, columnKey);
        const td = document.createElement('td');
        if (!partyKey) {
          td.className = 'maps-predict-grid-total';
          td.textContent = '—';
          tr.appendChild(td);
          return;
        }
        const input = document.createElement('input');
        input.type = 'number';
        input.step = '1';
        input.min = '0';
        input.max = '100';
        input.className = 'maps-predict-grid-input';
        input.dataset.regionKey = row.regionKey;
        input.dataset.partyKey = partyKey;
        input.value = String(model.getShare(row.regionKey, partyKey));
        if (row.isAggregate && model.aggregateExpanded) {
          input.disabled = true;
        } else {
          input.addEventListener('change', () => {
            // 'change' fires on blur / Enter. An empty cell means "revert to baseline": clear
            // the override and redisplay the baseline value, rather than coercing the blank to
            // 0% (clampNumber('') is 0, which would silently pin the party to zero). Otherwise
            // clamp + round before persisting so the model only ever holds [0, 100] integer
            // shares. Either way refresh the row's 'other' cell so the implied total stays
            // consistent.
            if (input.value.trim() === '') {
              model.clearShare(row.regionKey, partyKey);
              input.value = String(model.getShare(row.regionKey, partyKey));
              refreshOther(row.regionKey);
              return;
            }
            const next = clampNumber(input.value, 0, 100);
            input.value = String(Math.round(next));
            model.setShare(row.regionKey, partyKey, next);
            refreshOther(row.regionKey);
          });
        }
        td.appendChild(input);
        tr.appendChild(td);
      });

      const otherTd = document.createElement('td');
      otherTd.className = 'maps-predict-grid-total';
      const initialOther = model.getOtherShare(row.regionKey);
      otherTd.textContent = String(initialOther);
      otherTd.classList.toggle('maps-predict-grid-total-over', initialOther < 0);
      otherCellByRegion.set(row.regionKey, otherTd);
      tr.appendChild(otherTd);
      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    wrap.appendChild(table);
    predictGrid.appendChild(wrap);
  });
}

/**
 * Wires the predict-window action buttons via a single click delegator per button. Called
 * exactly once from `wireInit` at page init, so no re-wiring guard is needed — there is no
 * second predict-mode entry that would re-run it and stack duplicate listeners.
 *
 * Two action classes share the dispatcher:
 *
 * 1. **Collapse** (`data-predict-action="collapse"`) — purely layout-local, handled
 *    inline. Toggles the `maps-predict-window--collapsed` class (which hides the body
 *    via CSS), flips the button glyph between ▲ (expanded) and ▼ (collapsed), and strips
 *    `maps-predict-window-fill` so the predict window shrinks to header height instead of
 *    sizing to its content. The seat card below then grows into the recovered space, and
 *    the `seatCardObserver` ResizeObserver re-shows it if it had been hidden for lack of
 *    room — the collapse handler never touches the seat card directly. Both class flips
 *    reverse on re-expand. None of this is shared with the orchestrator — keeping it
 *    inline avoids a round-trip through `predict-controller.js`.
 *
 * 2. **Apply / Submit / Share / Reset** — looked up in the `predictActionHandlers` bag
 *    populated by `setPredictActionHandlers`. Each handler lives in `predict-controller.js`
 *    where it can mutate the model and trigger a re-projection. Missing handlers
 *    silently no-op (defensive against partial registration).
 *
 * @returns {void}
 */
export function wirePredictControls() {
  document.querySelectorAll('[data-predict-action]').forEach((button) => {
    button.addEventListener('click', () => {
      // Collapse is layout-only and stays in dom.js; the rest (apply/submit/share/reset)
      // routes to the orchestrator-supplied handler so this module doesn't need to
      // import predict-mode logic from predict-controller.js.
      const action = button.getAttribute('data-predict-action');
      if (action === 'collapse') {
        // Dropping maps-predict-window-fill makes the window shrink to header-only
        // height (its body is also display:none via the --collapsed class), so the
        // seat card's flex:1 claims the recovered space. Reverse on re-expand.
        const collapsed = predictWindow.classList.toggle('maps-predict-window--collapsed');
        button.textContent = collapsed ? '▼' : '▲';
        predictWindow.classList.toggle('maps-predict-window-fill', !collapsed);
        return;
      }
      const handler = predictActionHandlers[action];
      if (handler) handler();
    });
  });
}

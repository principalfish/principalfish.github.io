// ─── Predict controller ──────────────────────────────────────────────────────
//
// The predict-view glue: action handlers, the simulation cache, and view activation.
// Extracted from electionmaps.js so the orchestrator stays slim. Depends on the DOM
// render layer (dom.js) and the prediction engine (predict.js); kept separate from the
// engine so the engine stays DOM-free and unit-testable.
//
// Predict mode shares the same data-load path as election mode: activateElection fetches
// the manifest's baseline election (e.g. 2024-general) and assigns it to both
// state.electionData and state.comparisonElectionData, so the user lands on the baseline
// without any projection. activatePredictView then instantiates a parliament-specific
// PredictModel and shows the input grid; if the URL carries a shared `?predict=` scenario
// it deserialises and runs the projection so the shared map renders. Subsequent Submit /
// Apply / Reset clicks call runPredictProjection, which re-projects from the model and
// re-renders.

import { state, manifest, ElectionData, buildRouteSearchParams } from './state.js';
import { predictModelClassFor } from './predict.js';
import { fetchJson } from './files.js';
import {
  renderHeader,
  renderMap,
  renderMapControlOptions,
  renderPredict,
  syncRightPanelHeight,
  setPredictActionHandlers,
  setPredictWindowVisible,
  initRegionTable,
} from './dom.js';

// Cached current-model simulation seats (for the "Use current forecast" button), one entry
// per parliament. Prefetched in the background from activatePredictView so the first Apply
// click resolves instantly; ensurePredictSimulation also falls back to fetching on demand
// if the prefetch is still in flight (or failed silently).
const predictSimulationCache = new Map();

/**
 * Resolves the election that activateElection should fetch when the page is in
 * predict mode. Three outcomes:
 *   - Parliament's manifest doesn't list `predict` in `features`: demotes
 *     `state.view` to 'election' and returns `state.currentElection` so the caller
 *     proceeds as a regular election view.
 *   - Predict configured and `predictBaselineElectionId` resolves: returns the
 *     baseline election (e.g. 2024-general for Westminster).
 *   - Predict configured but baseline missing or unresolvable: returns null;
 *     caller is expected to render an error and bail.
 * @returns {object|null}
 */
export function getPredictBaseElection() {
  const parliament = state.currentParliament;
  const config = manifest.parliamentConfig(parliament);
  const features = config.features ?? [];
  if (!features.includes('predict')) {
    state.view = 'election';
    return state.currentElection;
  }
  const baselineId = config.predictBaselineElectionId;
  return baselineId ? manifest.getElectionFromId(baselineId) : null;
}

/**
 * Predict-only setup that runs after `activateElection` has fetched the baseline and
 * rendered it. Constructs the parliament-specific predict model, wires the action
 * buttons, shows the predict window, and (only if the URL carries a shared scenario)
 * deserialises it and re-projects so the user lands on the shared map.
 * @returns {Promise<void>}
 */
export async function activatePredictView() {
  const parliament = state.currentParliament;
  const parliamentConfig = manifest.parliamentConfig(parliament);
  const PredictModelClass = predictModelClassFor(parliament);
  // Reachable only on a malformed manifest (parliament lists `predict` as a feature but has
  // no/invalid `predict.model`). Fail with a clear message rather than `new null(...)`.
  if (!PredictModelClass) {
    throw new Error(`No predict model configured for parliament '${parliament}'`);
  }
  state.predictModel = new PredictModelClass(parliamentConfig.nextElectionYear, parliamentConfig.predict);

  setPredictActionHandlers({
    apply: handlePredictApply,
    submit: handlePredictSubmit,
    share: handlePredictShare,
    reset: handlePredictReset,
  });
  setPredictWindowVisible(true);
  renderPredict();

  // Warm the simulation cache in the background so the "Use current forecast" button
  // doesn't pay the (possibly multi-MB) fetch latency on its first click. Fire-and-forget:
  // ensurePredictSimulation catches its own errors and returns null on failure, and the
  // Apply handler still calls ensurePredictSimulation defensively.
  ensurePredictSimulation(parliament);

  // If a shared scenario URL is present, hydrate the model, repaint the grid so the
  // deserialised inputs show up in the cells, and re-project so the map reflects them.
  // Without a payload, the baseline already shown by activateElection is the correct
  // initial display.
  const sharedPayload = new URLSearchParams(window.location.search).get('predict');
  if (sharedPayload) {
    state.predictModel.deserialize(sharedPayload);
    renderPredict();
    runPredictProjection();
  }
}

/**
 * Projects the predict model and pushes the result through the regular render pipeline.
 * Reads `state.predictModel`; no-op when null.
 * @returns {void}
 */
function runPredictProjection() {
  const model = state.predictModel;
  if (!model) return;

  const nextYear = model.nextElectionYear;
  const predictLabel = `Predict ${nextYear ?? ''}`.trim();

  // Only state.electionData (the projection output) changes between projections — the
  // baseline / map / region-labels were set once in activateElection and the model
  // now reads them straight from state via getters.
  const projectedSeats = model.project();
  state.setElectionDataFromSeats(projectedSeats, predictLabel);

  state.setupMapData();
  renderHeader(state.electionData.summary.text);
  renderMapControlOptions();
  renderMap(true);
  // setupMapData recomputed listRegionSummary from the projected seats, so rebuild the
  // region-table overlay (renderMap doesn't touch it). preserveZoom keeps the user's pan/zoom.
  initRegionTable();
  syncRightPanelHeight();

  const params = buildRouteSearchParams('predict');
  window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
}

/**
 * Submit handler. Wired to the `[data-predict-action="submit"]` button via
 * `setPredictActionHandlers` in `activatePredictView`.
 *
 * Runs `model.validate()` to find any region whose entered party shares sum above 100%
 * (which would imply a negative residual for "other parties" and produce undefined
 * behaviour in `projectSeatUniformSwing`). On invalid input, surfaces a blocking
 * `window.alert` listing up to four offending regions with their totals — the cap keeps
 * the dialog readable when many rows are out of bounds — and bails without projecting.
 *
 * On valid input, delegates to `runPredictProjection` which pulls a fresh `Seat[]` out
 * of the model, swaps it into `state.electionData`, and re-renders the map / right panel
 * / header. URL is also synced via `buildRouteSearchParams('predict')` as a side effect.
 *
 * No-ops silently if `state.predictModel` is null (e.g. the user navigated away from
 * predict mode mid-click).
 *
 * @returns {void}
 */
function handlePredictSubmit() {
  // Validate before projecting — a row whose entered shares exceed 100 produces a
  // negative residual for "other parties", which projectSeatUniformSwing has no defined
  // behaviour for. Cap the alert at the first 4 offenders so the dialog stays readable.
  const model = state.predictModel;
  if (!model) return;
  const invalid = model.validate();
  if (invalid.length) {
    const summary = invalid.slice(0, 4).map((r) => `${r.regionLabel} (${r.total}%)`).join(', ');
    window.alert(`Entered percentages exceed 100% for: ${summary}${invalid.length > 4 ? ', ...' : ''}. Please reduce inputs before submitting.`);
    return;
  }
  runPredictProjection();
}

/**
 * Reset handler. Wired to the `[data-predict-action="reset"]` button.
 *
 * Calls `model.reset()`, which clears every input map, drops the aggregate-expanded
 * flag, and (Holyrood only) restores `activeTab` to the first tab. The render pass then
 * runs *before* re-projection: `renderPredict` rebuilds the grid from the cleared model
 * so the user sees baseline values immediately, even if `runPredictProjection`'s
 * zero-swing short-circuit returns the unaltered baseline seats milliseconds later.
 *
 * No-ops silently if `state.predictModel` is null.
 *
 * @returns {void}
 */
function handlePredictReset() {
  // Reset clears every input map (and aggregateExpanded / activeTab on Holyrood); the
  // re-render is needed before re-projection so the grid reflects the cleared inputs
  // even if projection short-circuits to baseline.
  const model = state.predictModel;
  if (!model) return;
  model.reset();
  renderPredict();
  runPredictProjection();
}

/**
 * Apply handler ("Use current forecast" button). Wired to `[data-predict-action="apply"]`.
 *
 * Reads the per-parliament prediction-model output via `ensurePredictSimulation` — the
 * file is prefetched into `predictSimulationCache` from `activatePredictView`, so this
 * usually resolves synchronously from cache; if the prefetch is still in flight (or it
 * never ran because predict view loaded mid-fetch) the call awaits the same fetch. If
 * the load fails — missing anchor election, network error, or empty seat array —
 * surfaces a blocking `window.alert` and bails.
 *
 * On success, calls `model.loadSimulationShares(simulationSeats)` which writes the
 * forecast's per-region shares into the model's input map(s), honouring the current
 * aggregate-expanded state (`PredictModel.loadSharesFromSeats` skips the synthetic
 * aggregate when expanded, sub-regions when collapsed). Then re-renders the grid so
 * forecast values become editable, and re-projects.
 *
 * No-ops silently if `state.predictModel` is null.
 *
 * @returns {Promise<void>}
 */
async function handlePredictApply() {
  // The simulation file is prefetched from activatePredictView so this normally resolves
  // from cache; on cache miss (slow prefetch / previous fetch failure) ensurePredictSimulation
  // falls back to fetching synchronously. loadSimulationShares writes the forecast into the
  // model's input maps, then re-render makes those values appear in the grid before projection.
  const model = state.predictModel;
  if (!model) return;
  const simulationSeats = await ensurePredictSimulation(state.currentParliament);
  if (!simulationSeats) {
    window.alert('Current prediction data is not available.');
    return;
  }
  model.loadSimulationShares(simulationSeats);
  renderPredict();
  runPredictProjection();
}

/**
 * Loads and caches the current model-output seats for the current parliament. Returns null
 * on failure. The cache stores the in-flight promise (not the resolved seats) so concurrent
 * callers — typically the activatePredictView prefetch and a fast Apply click — share one
 * fetch. On failure the entry is evicted so the next call retries.
 * Exported for unit testing of the cache/eviction semantics.
 * @param {string} parliament
 * @returns {Promise<Seat[] | null>}
 */
export async function ensurePredictSimulation(parliament) {
  if (predictSimulationCache.has(parliament)) return predictSimulationCache.get(parliament);

  const anchorId = manifest.parliamentConfig(parliament).predictAnchorElectionId;
  const anchorElection = anchorId ? manifest.getElectionFromId(anchorId) : null;
  if (!anchorElection) return null;

  const promise = (async () => {
    try {
      const { dataFile } = manifest.resolveElectionFiles(anchorElection);
      const resultsData = await fetchJson(`data/${dataFile}`);
      const electionData = new ElectionData(resultsData);
      if (!electionData.baseSeats.length) return null;
      return electionData.baseSeats;
    } catch (error) {
      console.error('Predict simulation load failed', error);
      return null;
    }
  })();
  predictSimulationCache.set(parliament, promise);
  const seats = await promise;
  if (!seats) predictSimulationCache.delete(parliament);
  return seats;
}

/**
 * Share handler. Wired to `[data-predict-action="share"]`.
 *
 * Composes a fully-qualified URL containing the current predict scenario via
 * `buildRouteSearchParams('predict')` (which embeds `model.serialize()` as the
 * `?predict=` payload alongside `?view=predict` and `?election=...`). Then walks a
 * three-tier delivery fallback so the URL is always accessible to the user:
 *
 * 1. `navigator.share` — opens the OS-native share sheet on mobile / supported desktops.
 *    Both the API call and the user's accept/dismiss can throw; either case falls
 *    through to step 2.
 * 2. `navigator.clipboard.writeText` — silent copy + a confirmation alert. Requires a
 *    secure context and a user gesture (the click satisfies the gesture). On failure
 *    (permissions denied, missing clipboard API), falls through to step 3.
 * 3. `window.prompt` — final fallback. Shows a pre-populated dialog the user can copy
 *    from manually. Always succeeds at delivering the URL.
 *
 * Each layer's errors are deliberately swallowed because the next fallback handles the
 * user-visible delivery; logging would just be noise.
 *
 * @returns {Promise<void>}
 */
async function handlePredictShare() {
  // Try Web Share first (mobile-native sheet), then async clipboard, then fall back to
  // a prompt() so the URL is always copyable. Both Web Share and Clipboard need a user
  // gesture and a secure context; this fn is invoked from a click so the gesture is
  // satisfied. Errors from either path are swallowed because the next fallback handles
  // the user-visible delivery.
  const params = buildRouteSearchParams('predict');
  const query = params.toString();
  const shareUrl = `${window.location.origin}${window.location.pathname}${query ? `?${query}` : ''}`;
  try {
    if (navigator.share) {
      await navigator.share({ title: 'UK Election Maps prediction', url: shareUrl });
      return;
    }
  } catch { /* fall through */ }
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(shareUrl);
      window.alert('Prediction link copied to clipboard.');
      return;
    } catch { /* fall through */ }
  }
  window.prompt('Copy your prediction link:', shareUrl);
}

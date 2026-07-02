import { describe, it, expect, vi, beforeEach } from 'vitest';

// predict-controller imports the DOM render layer (dom.js) and the predict view module
// (predict-view.js) at module load, both of which touch document/observers. Stub them so the
// module imports cleanly in Node. files.js is stubbed so the simulation fetch is controllable.
vi.mock('../dom.js', () => ({
  renderHeader: vi.fn(),
  renderMap: vi.fn(),
  renderMapControlOptions: vi.fn(),
  syncRightPanelHeight: vi.fn(),
  initRegionTable: vi.fn(),
  refreshOpenSeatPopup: vi.fn(),
}));
vi.mock('../features/predict-view.js', () => ({
  renderPredict: vi.fn(),
  setApplyActionVisible: vi.fn(),
  setPredictActionHandlers: vi.fn(),
  setPredictWindowVisible: vi.fn(),
}));
vi.mock('../files.js', () => ({ fetchJson: vi.fn() }));

import {
  activatePredictView,
  getPredictBaseElection,
  ensurePredictSimulation,
  parliamentHasForecast,
} from '../features/predict-controller.js';
import { setApplyActionVisible, setPredictActionHandlers } from '../features/predict-view.js';
import { manifest, state, ElectionData } from '../state.js';
import { fetchJson } from '../files.js';

describe('getPredictBaseElection', () => {
  beforeEach(() => {
    state.currentParliament = 'westminster';
    state.currentElection = { id: 'current' };
    state.view = 'predict';
  });

  it('demotes the view to election when the parliament has no predict feature', () => {
    manifest.init({ parliamentFeatures: { westminster: { features: [] } }, elections: [] });
    const result = getPredictBaseElection();
    expect(state.view).toBe('election');
    expect(result).toBe(state.currentElection);
  });

  it('returns the configured baseline election when predict is enabled and resolvable', () => {
    manifest.init({
      parliamentFeatures: { westminster: { features: ['predict'], predictBaselineElectionId: 'base' } },
      elections: [{ id: 'base', name: 'Baseline' }],
    });
    const result = getPredictBaseElection();
    expect(state.view).toBe('predict');
    expect(result).toMatchObject({ id: 'base' });
  });

  it('returns null when predict is enabled but no baseline id is configured', () => {
    manifest.init({ parliamentFeatures: { westminster: { features: ['predict'] } }, elections: [] });
    expect(getPredictBaseElection()).toBe(null);
  });

  it('returns a falsy value when the configured baseline id does not resolve to an election', () => {
    manifest.init({
      parliamentFeatures: { westminster: { features: ['predict'], predictBaselineElectionId: 'missing' } },
      elections: [],
    });
    expect(getPredictBaseElection()).toBeFalsy();
  });
});

describe('parliamentHasForecast', () => {
  it('is true when the anchor resolves to a model/nowcast election', () => {
    manifest.init({
      parliamentFeatures: { westminster: { predictAnchorElectionId: 'nowcast' } },
      elections: [{ id: 'nowcast', model: true }],
    });
    expect(parliamentHasForecast('westminster')).toBe(true);
  });

  it('is false when the anchor is a plain past election (US-style)', () => {
    manifest.init({
      parliamentFeatures: { us_presidential: { predictAnchorElectionId: '2024-us-president' } },
      elections: [{ id: '2024-us-president' }],
    });
    expect(parliamentHasForecast('us_presidential')).toBe(false);
  });

  it('is false when no anchor election id is configured', () => {
    manifest.init({ parliamentFeatures: { us_house: {} }, elections: [] });
    expect(parliamentHasForecast('us_house')).toBe(false);
  });
});

describe('activatePredictView forecast gating', () => {
  // Minimal single-seat baseline: FPTPPredict's constructor builds its ballot baseline from
  // state.comparisonElectionData (set by activateElection in the real flow).
  const baselineResults = { seats: [{ n: 'A', r: 'glasgow', w: 'snp', p: [['snp', 600], ['labour', 400]] }] };
  const predictConfig = {
    model: 'fptp',
    modelledPartyKeys: ['snp', 'labour'],
    gridSections: [{ id: 's', columnKeys: ['snp', 'labour'] }],
  };

  /** Manifest with a predict-enabled parliament whose anchor is / isn't a model election.
   * Distinct parliament keys per test keep the module-level simulation cache from leaking. */
  function configurePredict(parliament, { modelAnchor }) {
    manifest.init({
      parliamentFeatures: {
        [parliament]: { predictAnchorElectionId: 'anchor', nextElectionYear: 2030, predict: predictConfig },
      },
      elections: [{ id: 'anchor', mapId: 1, ...(modelAnchor ? { model: true } : {}) }],
      files: { elections: { mapsById: { 1: 'maps/m.topo.json' }, electionsById: { anchor: 'results/r.json' } } },
    });
    state.comparisonElectionData = new ElectionData(baselineResults);
  }

  /** Runs activatePredictView far enough to assert the gating. The action-handler wiring,
   * apply-button visibility, and prefetch all happen before the function reads
   * window.location (absent in the node test env), where it throws — swallow that. */
  async function runActivate(parliament) {
    state.currentParliament = parliament;
    try {
      await activatePredictView();
    } catch { /* expected: no window in node; gating already ran */ }
  }

  beforeEach(() => {
    fetchJson.mockReset();
    setApplyActionVisible.mockClear();
    setPredictActionHandlers.mockClear();
  });

  it('with a model anchor: registers the apply handler, shows the button, prefetches', async () => {
    configurePredict('p_forecast', { modelAnchor: true });
    fetchJson.mockResolvedValue(baselineResults);
    await runActivate('p_forecast');
    expect(setApplyActionVisible).toHaveBeenCalledWith(true);
    const handlers = setPredictActionHandlers.mock.calls[0][0];
    expect(handlers.apply).toBeTypeOf('function');
    expect(fetchJson).toHaveBeenCalledWith('data/results/r.json');
  });

  it('without a model anchor (US-style): no apply handler, hidden button, no prefetch', async () => {
    configurePredict('p_noforecast', { modelAnchor: false });
    await runActivate('p_noforecast');
    expect(setApplyActionVisible).toHaveBeenCalledWith(false);
    const handlers = setPredictActionHandlers.mock.calls[0][0];
    expect(handlers.apply).toBeUndefined();
    expect(handlers.submit).toBeTypeOf('function');
    expect(fetchJson).not.toHaveBeenCalled();
  });
});

describe('ensurePredictSimulation', () => {
  // One seat -> ElectionData.baseSeats has length 1 (success); empty seats -> length 0 (failure).
  const oneSeatResults = { seats: [{ n: 'A', r: 'glasgow', w: 'snp', p: [['snp', 600], ['labour', 400]] }] };
  const emptyResults = { seats: [] };

  // Wires up a parliament whose anchor election resolves to a data file. Distinct parliament
  // keys per test avoid the module-level cache leaking across tests.
  function configureAnchor(parliament, { withAnchor = true } = {}) {
    manifest.init({
      parliamentFeatures: { [parliament]: withAnchor ? { predictAnchorElectionId: 'anchor' } : {} },
      elections: [{ id: 'anchor', mapId: 1 }],
      files: { elections: { mapsById: { 1: 'maps/m.topo.json' }, electionsById: { anchor: 'results/r.json' } } },
    });
  }

  beforeEach(() => { fetchJson.mockReset(); });

  it('returns null without fetching when the anchor election is not configured', async () => {
    configureAnchor('p_noanchor', { withAnchor: false });
    expect(await ensurePredictSimulation('p_noanchor')).toBe(null);
    expect(fetchJson).not.toHaveBeenCalled();
  });

  it('fetches, parses, and returns the anchor election baseSeats', async () => {
    configureAnchor('p_ok');
    fetchJson.mockResolvedValue(oneSeatResults);
    const seats = await ensurePredictSimulation('p_ok');
    expect(seats).toHaveLength(1);
    expect(seats[0].winner).toBe('snp');
    expect(fetchJson).toHaveBeenCalledWith('data/results/r.json');
  });

  it('caches the in-flight promise so concurrent callers share one fetch', async () => {
    configureAnchor('p_cache');
    fetchJson.mockResolvedValue(oneSeatResults);
    const [a, b] = await Promise.all([
      ensurePredictSimulation('p_cache'),
      ensurePredictSimulation('p_cache'),
    ]);
    expect(a).toBe(b);
    expect(fetchJson).toHaveBeenCalledTimes(1);
  });

  it('evicts the cache entry on failure so the next call retries', async () => {
    configureAnchor('p_evict');
    fetchJson.mockResolvedValue(emptyResults); // empty baseSeats -> null result
    expect(await ensurePredictSimulation('p_evict')).toBe(null);
    expect(await ensurePredictSimulation('p_evict')).toBe(null);
    // Two separate fetches because the failed entry was evicted between calls.
    expect(fetchJson).toHaveBeenCalledTimes(2);
  });
});

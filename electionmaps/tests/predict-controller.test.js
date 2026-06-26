import { describe, it, expect, vi, beforeEach } from 'vitest';

// predict-controller imports the DOM render layer (dom.js) at module load, which touches
// document/observers. Stub it so the module imports cleanly in Node. files.js is stubbed so
// the simulation fetch is controllable.
vi.mock('../scripts/dom.js', () => ({
  renderHeader: vi.fn(),
  renderMap: vi.fn(),
  renderMapControlOptions: vi.fn(),
  renderPredict: vi.fn(),
  syncRightPanelHeight: vi.fn(),
  setPredictActionHandlers: vi.fn(),
  setPredictWindowVisible: vi.fn(),
  initRegionTable: vi.fn(),
}));
vi.mock('../scripts/files.js', () => ({ fetchJson: vi.fn() }));

import { getPredictBaseElection, ensurePredictSimulation } from '../scripts/predict-controller.js';
import { manifest, state } from '../scripts/state.js';
import { fetchJson } from '../scripts/files.js';

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

// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';

// The map drawing libraries are only used when a topology is rendered, which these tests do
// not do; stubbing them also avoids loading the vendored bundles.
vi.mock('../../site/vendor/d3.v7.esm.js', () => ({}));
vi.mock('../../site/vendor/topojson-client.v3.esm.js', () => ({
  feature: vi.fn(), mesh: vi.fn(), merge: vi.fn(),
}));

// dom.js binds its elements from the document at import time, so the real shell markup is
// injected first and the observers jsdom lacks are stubbed.
let dom;
let stateModule;

beforeAll(async () => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  // The page itself (not the shell) supplies the title and subtitle elements.
  document.body.innerHTML = '<h1 class="maps-title"></h1><p id="mapsSubtitle"></p>' + readFileSync(resolve(process.cwd(), 'electionmapslogic/shell.html'), 'utf8');
  stateModule = await import('../state.js');
  dom = await import('../dom.js');
});

function resetState() {
  const { manifest, state } = stateModule;
  manifest.init({
    parties: [], mapModes: {}, elections: [], defaultElection: null, files: {},
    parliamentFeatures: {}, partyKeyAliases: {}, misc: {},
  });
  state.view = 'election';
  state.currentElection = { id: 'e', name: 'E', mapId: 1, byElectionSeats: null };
  state.comparisonElectionData = null;
  state.currentRegionLabelsByKey = new Map();
}

beforeEach(resetState);
afterEach(() => {
  stateModule.state.electionData = null;
  stateModule.state.currentElection = null;
  stateModule.state.view = 'election';
});

const rawSeat = (n, w, votes, extra = {}) => ({
  n, r: 'region', w, p: Object.entries(votes).map(([party, total]) => [party, total]), ...extra,
});

describe('renderSourceNote', () => {
  const el = () => document.getElementById('mapsSourceNote');

  it('shows the election sourceNote as text', () => {
    stateModule.state.currentElection.sourceNote = 'Results: The New York Times / AP. Provisional until certified.';
    dom.renderSourceNote();
    expect(el().hidden).toBe(false);
    expect(el().textContent).toBe('Results: The New York Times / AP. Provisional until certified.');
  });

  it('is hidden when the election has no note', () => {
    dom.renderSourceNote();
    expect(el().hidden).toBe(true);
    expect(el().textContent).toBe('');
  });

  it('clears a previous note when the election changes to one without', () => {
    stateModule.state.currentElection.sourceNote = 'A note';
    dom.renderSourceNote();
    stateModule.state.currentElection = { id: 'f', name: 'F', mapId: 1 };
    dom.renderSourceNote();
    expect(el().hidden).toBe(true);
    expect(el().textContent).toBe('');
  });

  it('never renders the note as markup', () => {
    stateModule.state.currentElection.sourceNote = '<img src=x onerror=alert(1)><b>bold</b>';
    dom.renderSourceNote();
    expect(el().querySelector('img, b')).toBeNull();
    expect(el().textContent).toBe('<img src=x onerror=alert(1)><b>bold</b>');
  });

  it('is hidden outside the election view', () => {
    stateModule.state.currentElection.sourceNote = 'A note';
    stateModule.state.view = 'predict';
    dom.renderSourceNote();
    expect(el().hidden).toBe(true);
  });

  it('is refreshed by renderHeader', () => {
    stateModule.state.currentElection.sourceNote = 'Via header';
    dom.renderHeader('Subtitle');
    expect(el().textContent).toBe('Via header');
  });
});

describe('renderSeatPopup', () => {
  const load = (payload) => {
    const { state, ElectionData } = stateModule;
    state.electionData = new ElectionData(payload);
  };
  const meta = () => document.getElementById('mapsSeatPopupMeta');
  const list = () => document.getElementById('mapsSeatPopupList');

  it('shows UNCALLED with the counted share, the same-party note and the round label', () => {
    load({ seats: [rawSeat('Alaska', 'republican', { republican: 51, democrat: 49 }, { c: false, pc: 62.5, sp: true, rd: 'rcv-final' })] });
    dom.renderSeatPopup('Alaska');
    expect(meta().textContent).toContain('UNCALLED · 62.5% counted');
    expect(meta().querySelector('.maps-uncalled-tag')).not.toBeNull();
    expect(meta().textContent).toContain('Same-party contest: votes combined');
    expect(meta().textContent).toContain('Decided by ranked-choice final round');
  });

  it('shows just UNCALLED when the counted share is unknown, and no tags for a called seat', () => {
    load({ seats: [rawSeat('A', 'republican', { republican: 1 }, { c: false }), rawSeat('B', 'republican', { republican: 1 })] });
    dom.renderSeatPopup('A');
    expect(meta().textContent).toContain('UNCALLED');
    expect(meta().textContent).not.toContain('counted');
    dom.renderSeatPopup('B');
    expect(meta().textContent).not.toContain('UNCALLED');
    expect(meta().textContent).not.toContain('Same-party');
    expect(meta().textContent).not.toContain('Decided');
  });

  it('escapes candidate names', () => {
    load({ seats: [{ n: 'A', r: 'r', w: 'republican', p: [['republican', 5, '<img src=x onerror=1>']] }] });
    dom.renderSeatPopup('A');
    expect(list().querySelector('img')).toBeNull();
    expect(list().textContent).toContain('<img src=x onerror=1>');
  });

  it('adds a Senate special block with its class, leader, uncalled tag and vote rows', () => {
    load({
      seats: [rawSeat('Florida', 'republican', { republican: 55, democrat: 45 })],
      specials: [{ n: 'Florida', cl: 3, w: 'democrat', c: false, pc: 40, p: [['democrat', 51, 'Dem <b>X</b>'], ['republican', 49, 'Rep']] }],
    });
    dom.renderSeatPopup('Florida');
    const block = list().querySelector('.maps-popup-special');
    expect(block).not.toBeNull();
    expect(block.querySelector('.maps-popup-special-title').textContent).toBe('Special election (Class 3)');
    expect(block.textContent).toContain('UNCALLED · 40% counted');
    expect(block.textContent).toContain('Leading:');
    expect(block.querySelectorAll('.maps-popup-row')).toHaveLength(2);
    expect(block.querySelector('b')).toBeNull();
    // The regular result's rows are still there above it.
    expect(list().querySelectorAll('.maps-popup-row').length).toBe(4);
  });

  it('shows no special block for a state without one', () => {
    load({ seats: [rawSeat('Texas', 'republican', { republican: 1 })], specials: [{ n: 'Ohio', cl: 3, w: 'democrat', p: [['democrat', 1]] }] });
    dom.renderSeatPopup('Texas');
    expect(list().querySelector('.maps-popup-special')).toBeNull();
  });
});

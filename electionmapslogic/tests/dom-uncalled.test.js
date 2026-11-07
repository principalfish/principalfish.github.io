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

describe('renderSeatPopup: individual candidate rows', () => {
  const load = (payload, comparison = null) => {
    const { state, ElectionData } = stateModule;
    state.electionData = new ElectionData(payload);
    state.comparisonElectionData = comparison ? new ElectionData(comparison) : null;
  };
  const meta = () => document.getElementById('mapsSeatPopupMeta');
  const list = () => document.getElementById('mapsSeatPopupList');
  const rowTexts = () => [...list().querySelectorAll('.maps-popup-row')].map((row) => row.textContent.replace(/\s+/g, ' ').trim());
  const alaska = (extra = {}) => ({
    n: 'Alaska', r: 'region', w: 'democrat', c: false, pc: 74.1,
    p: [['democrat', 102962, 'Mary Peltola'], ['republican', 102325, 'Dan Sullivan'],
      ['republican', 4846, 'Gerald Heikes'], ['others', 3600, 'Daniel <b>Sullivan</b>']],
    ...extra,
  });

  it('lists the four candidates with percentages, sorted by votes, and no deltas', () => {
    // A comparison seat would give every party a delta; candidate rows carry none.
    load({ seats: [alaska()] }, { seats: [rawSeat('Alaska', 'republican', { republican: 60, democrat: 40 })] });
    dom.renderSeatPopup('Alaska');
    expect(rowTexts()).toEqual([
      expect.stringMatching(/^Mary Peltola\s*48\.17%$/),
      expect.stringMatching(/^Dan Sullivan\s*47\.88%$/),
      expect.stringMatching(/^Gerald Heikes\s*2\.27%$/),
      expect.stringMatching(/^Daniel <b>Sullivan<\/b>\s*1\.68%$/),
    ]);
    expect(list().querySelector('b')).toBeNull();
    expect(list().querySelector('.maps-popup-values span:nth-child(2)')).toBeNull();
    expect(meta().textContent).toContain('UNCALLED · 74.1% counted');
    // Peltola's lead over Sullivan (637 votes), not the Republican party total's lead of 4,209.
    expect(meta().textContent).toContain('Majority: 0.30% = 637');
    expect(meta().textContent).not.toContain('4,209');
  });

  it('colours each row by its own party', () => {
    stateModule.manifest.init({
      parties: [
        { id: 20, key: 'democrat', name: 'Democrat', colour: '#0000ff' },
        { id: 21, key: 'republican', name: 'Republican', colour: '#ff0000' },
        { id: 15, key: 'others', name: 'Others', colour: '#888888' },
      ],
      mapModes: {}, elections: [], defaultElection: null, files: {}, parliamentFeatures: {}, partyKeyAliases: {}, misc: {},
    });
    load({ seats: [alaska()] });
    dom.renderSeatPopup('Alaska');
    const rows = [...list().querySelectorAll('.maps-popup-row')];
    const colour = (row) => row.style.getPropertyValue('--maps-popup-bar-colour');
    expect(colour(rows[1])).toBe(colour(rows[2]));
    expect(colour(rows[1])).toBe('#ff0000');
    expect(colour(rows[0])).toBe('#0000ff');
    expect(colour(rows[3])).toBe('#888888');
  });

  it('a seat without a repeated party keeps its per-party rows and deltas', () => {
    load(
      { seats: [rawSeat('Texas', 'republican', { republican: 60, democrat: 40 })] },
      { seats: [rawSeat('Texas', 'republican', { republican: 50, democrat: 50 })] },
    );
    dom.renderSeatPopup('Texas');
    const texts = rowTexts();
    expect(texts).toHaveLength(2);
    expect(texts[0]).toContain('+10.00');
    expect(texts[1]).toContain('-10.00');
  });
});

describe('renderSeatPopup: previous same-class result (pv)', () => {
  const load = (payload, comparison = null) => {
    const { state, ElectionData } = stateModule;
    state.electionData = new ElectionData(payload);
    state.comparisonElectionData = comparison ? new ElectionData(comparison) : null;
  };
  const meta = () => document.getElementById('mapsSeatPopupMeta');
  const list = () => document.getElementById('mapsSeatPopupList');
  const rowText = (container, party) => [...container.querySelectorAll('.maps-popup-row')]
    .map((row) => row.textContent.replace(/\s+/g, ' ').trim()).find((text) => text.startsWith(party)) ?? '';
  // The comparison election's Florida seat is a different class: wildly different shares.
  const comparison = { seats: [rawSeat('Florida', 'republican', { republican: 90, democrat: 10 })] };

  it('parses pv into previous, with party keys; null when absent; survives a clone', () => {
    const { Seat } = stateModule;
    const seat = Seat.fromRaw(rawSeat('Florida', 'republican', { republican: 60 }, { pv: { w: 'democrat', p: [['democrat', 55], ['republican', 45]] } }));
    expect(seat.previous).toEqual({ winner: 'democrat', votes: { democrat: 55, republican: 45 } });
    expect(new Seat(seat).previous).toEqual(seat.previous);
    expect(Seat.fromRaw(rawSeat('A', 'republican', { republican: 1 })).previous).toBeNull();
    expect(Seat.fromRaw(rawSeat('A', 'republican', { republican: 1 }, { pv: { p: [['democrat', 5]] } })).previous)
      .toEqual({ winner: null, votes: { democrat: 5 } });
  });

  it('a standalone special seat takes its deltas and FROM tag from previous, not the comparison seat', () => {
    load({ seats: [rawSeat('Florida', 'republican', { republican: 60, democrat: 40 }, { pv: { w: 'democrat', p: [['democrat', 55], ['republican', 45]] } })] }, comparison);
    dom.renderSeatPopup('Florida');
    expect(meta().textContent).toContain('FROM');
    expect(rowText(list(), 'republican')).toContain('60.00%');
    expect(rowText(list(), 'republican')).toContain('+15.00');
    expect(rowText(list(), 'democrat')).toContain('-15.00');
  });

  it('a pv without w shows deltas but no FROM tag', () => {
    load({ seats: [rawSeat('Florida', 'republican', { republican: 60, democrat: 40 }, { pv: { p: [['democrat', 55], ['republican', 45]] } })] }, comparison);
    dom.renderSeatPopup('Florida');
    expect(meta().textContent).not.toContain('FROM');
    expect(rowText(list(), 'republican')).toContain('+15.00');
  });

  it('a seat without pv still uses the comparison seat', () => {
    load({ seats: [rawSeat('Florida', 'republican', { republican: 60, democrat: 40 })] }, comparison);
    dom.renderSeatPopup('Florida');
    expect(rowText(list(), 'republican')).toContain('-30.00');
    expect(rowText(list(), 'democrat')).toContain('+30.00');
    expect(meta().textContent).not.toContain('FROM');
  });

  it('the folded special block shows deltas and a FROM tag when the winner changed', () => {
    load({
      seats: [rawSeat('Ohio', 'republican', { republican: 55, democrat: 45 })],
      specials: [rawSeat('Ohio', 'democrat', { democrat: 52, republican: 48 }, { cl: 3, pv: { w: 'republican', p: [['republican', 60], ['democrat', 40]] } })],
    });
    dom.renderSeatPopup('Ohio');
    const block = list().querySelector('.maps-popup-special');
    expect(block.textContent).toContain('FROM');
    expect(rowText(block, 'democrat')).toContain('+12.00');
    expect(rowText(block, 'republican')).toContain('-12.00');
    // The regular block is unchanged: no comparison, no deltas.
    expect(meta().textContent).not.toContain('FROM');
  });

  it('a special held by the same party has deltas but no FROM tag, and one without pv has neither', () => {
    load({
      seats: [rawSeat('Ohio', 'republican', { republican: 55, democrat: 45 })],
      specials: [rawSeat('Ohio', 'republican', { republican: 52, democrat: 48 }, { cl: 3, pv: { w: 'republican', p: [['republican', 60], ['democrat', 40]] } })],
    });
    dom.renderSeatPopup('Ohio');
    const block = list().querySelector('.maps-popup-special');
    expect(block.textContent).not.toContain('FROM');
    expect(rowText(block, 'republican')).toContain('-8.00');

    load({
      seats: [rawSeat('Ohio', 'republican', { republican: 55 })],
      specials: [rawSeat('Ohio', 'democrat', { democrat: 52, republican: 48 }, { cl: 3 })],
    });
    dom.renderSeatPopup('Ohio');
    const plain = list().querySelector('.maps-popup-special');
    expect(plain.textContent).not.toContain('FROM');
    expect(plain.textContent).not.toMatch(/[+-]\d+\.\d\d/);
  });
});

describe('renderSeatPopup: Current Senate uncalled members', () => {
  const list = () => document.getElementById('mapsSeatPopupList');

  it('shows an uncalled member as a neutral "Uncalled" row beside the called senator', () => {
    const { state, ElectionData } = stateModule;
    state.electionData = new ElectionData({
      seats: [{
        n: 'Texas',
        r: 'region',
        w: 'republican',
        members: [
          { party: 'republican', name: 'Ted <b>Cruz</b>', class: 1 },
          { party: null, name: null, class: 2, c: false, pc: 62.5 },
        ],
      }],
    });
    dom.renderSeatPopup('Texas');
    const rows = list().querySelectorAll('.maps-popup-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('Ted <b>Cruz</b>');
    expect(rows[0].querySelector('b')).toBeNull();
    expect(rows[1].querySelector('.maps-popup-name').textContent).toBe('Uncalled');
    expect(rows[1].textContent).toContain('62.5% counted');
    // Neutral grey, never a party colour or the Others colour.
    expect(rows[1].querySelector('.maps-seat-icon').style.background).toMatch(/#d1d5db|rgb\(209, 213, 219\)/);
  });

  it('says just "Uncalled" when the counted share is unknown', () => {
    const { state, ElectionData } = stateModule;
    state.electionData = new ElectionData({
      seats: [{ n: 'Texas', r: 'region', w: 'republican', members: [{ party: null, name: null, class: 2, c: false }, { party: 'republican', name: 'A', class: 1 }] }],
    });
    dom.renderSeatPopup('Texas');
    const row = list().querySelectorAll('.maps-popup-row')[0];
    expect(row.querySelector('.maps-popup-name').textContent).toBe('Uncalled');
    expect(row.textContent).not.toContain('counted');
  });
});

// renderMap draws the vote totals and the seat list before the topology; with no topology
// object it then throws, which lets these tests exercise the two panels without a map renderer.
const renderPanels = () => {
  expect(() => dom.renderMap()).toThrow('TopoJSON missing objects');
};

describe('vote totals uncalled line', () => {
  const line = () => document.getElementById('mapsVoteTotalsUncalled');
  const render = (uncalled, mapConfig = {}) => {
    const { state } = stateModule;
    state.mapConfig = mapConfig;
    state.mapData = { objects: {} };
    state.filteredSeatsSummary = { parties: [], totalVotes: 0, totalSeats: 10, uncalled };
    state.filteredSeatsComparisonSummary = null;
    state.listFilteredSeats = [];
    renderPanels();
  };

  it('is hidden and empty when nothing is uncalled', () => {
    render(0);
    expect(line().hidden).toBe(true);
    expect(line().textContent).toBe('');
  });

  it('says "1 seat uncalled" in the singular', () => {
    render(1);
    expect(line().hidden).toBe(false);
    expect(line().textContent).toBe('1 seat uncalled');
  });

  it('says "3 seats uncalled" in the plural', () => {
    render(3);
    expect(line().hidden).toBe(false);
    expect(line().textContent).toBe('3 seats uncalled');
  });

  it('counts electoral votes on an electoral-votes map, whatever the number', () => {
    render(1, { tally: 'electoralVotes' });
    expect(line().textContent).toBe('1 EV uncalled');
    render(38, { tally: 'electoralVotes' });
    expect(line().hidden).toBe(false);
    expect(line().textContent).toBe('38 EV uncalled');
  });

  it('hides again when a later render has nothing uncalled', () => {
    render(3);
    render(0);
    expect(line().hidden).toBe(true);
    expect(line().textContent).toBe('');
  });
});

describe('seat list swatches', () => {
  const render = (current, comparison) => {
    const { state, ElectionData } = stateModule;
    state.mapConfig = {};
    state.mapData = { objects: {} };
    state.filteredSeatsSummary = { parties: [], totalVotes: 0, totalSeats: 0, uncalled: 0 };
    state.filteredSeatsComparisonSummary = null;
    state.electionData = new ElectionData({ seats: current });
    state.comparisonElectionData = new ElectionData({ seats: comparison });
    state.listFilteredSeats = state.electionData.currentSeats;
    renderPanels();
  };
  const row = (name) => document.querySelector(`#mapsSeatList .maps-seat-row[data-seat-key="${name.toLowerCase()}"]`);
  const icon = (name) => row(name).querySelector('.maps-seat-owner-icon');

  beforeEach(() => {
    render(
      [
        // Called, and changed hands from the comparison election.
        rawSeat('Held', 'republican', { republican: 60, democrat: 40 }),
        rawSeat('Flipped', 'republican', { republican: 60, democrat: 40 }),
        // Uncalled with a leader: its leader differs from the comparison winner, yet no gain.
        rawSeat('Leading', 'republican', { republican: 51, democrat: 49 }, { c: false, pc: 50 }),
        // Uncalled and nobody leads (a tied count has no winner).
        { n: 'Tied', r: 'region', c: false, p: [['republican', 5], ['democrat', 5]] },
      ],
      [
        rawSeat('Held', 'republican', { republican: 55, democrat: 45 }),
        rawSeat('Flipped', 'democrat', { republican: 45, democrat: 55 }),
        rawSeat('Leading', 'democrat', { republican: 45, democrat: 55 }),
        rawSeat('Tied', 'democrat', { republican: 45, democrat: 55 }),
      ],
    );
  });

  it('draws a called seat solid, labelled with its winner', () => {
    expect(row('Held')).not.toBeNull();
    expect(icon('Held').classList.contains('maps-seat-icon--provisional')).toBe(false);
    expect(icon('Held').title).toBe('republican');
    expect(row('Held').querySelector('.maps-uncalled-tag')).toBeNull();
  });

  it('draws an uncalled seat with a leader in the leader\'s colour as provisional', () => {
    expect(icon('Leading').classList.contains('maps-seat-icon--provisional')).toBe(true);
    expect(icon('Leading').title).toBe('republican');
    expect(row('Leading').querySelector('.maps-uncalled-tag').textContent).toBe('UNCALLED');
  });

  it('draws an uncalled seat with no leader neutral, labelled "No leader", never Others', () => {
    expect(icon('Tied').classList.contains('maps-seat-icon--provisional')).toBe(true);
    expect(icon('Tied').title).toBe('No leader');
    expect(icon('Tied').style.background).toMatch(/#d1d5db|rgb\(209, 213, 219\)/);
    expect(row('Tied').querySelector('.maps-uncalled-tag')).not.toBeNull();
  });

  it('shows a gain indicator only for a called seat that changed hands', () => {
    const gain = (name) => row(name).querySelector('.maps-seat-gain');
    expect(gain('Held')).toBeNull();
    expect(gain('Flipped')).not.toBeNull();
    expect(gain('Flipped').textContent).toContain('GAIN FROM');
    // Uncalled seats differ from the comparison winner but have no settled winner to gain.
    expect(gain('Leading')).toBeNull();
    expect(gain('Tied')).toBeNull();
    expect(row('Leading').querySelector('.maps-seat-gain-placeholder')).not.toBeNull();
  });
});

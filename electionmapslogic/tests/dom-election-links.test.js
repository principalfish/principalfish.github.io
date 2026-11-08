// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, beforeAll, vi } from 'vitest';

// The map drawing libraries are only used when a topology is rendered, which these tests do
// not do; stubbing them also avoids loading the vendored bundles.
vi.mock('../../site/vendor/d3.v7.esm.js', () => ({}));
vi.mock('../../site/vendor/topojson-client.v3.esm.js', () => ({
  feature: vi.fn(), mesh: vi.fn(), merge: vi.fn(),
}));

let dom;
let stateModule;

beforeAll(async () => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  document.body.innerHTML = '<h1 class="maps-title"></h1><p id="mapsSubtitle"></p>' + readFileSync(resolve(process.cwd(), 'electionmapslogic/shell.html'), 'utf8');
  stateModule = await import('../state.js');
  dom = await import('../dom.js');
});

const election = (id, name, parliament) => ({ id, name, parliament, type: 'x', mapId: 1 });

/** Renders the left bar for a parliament and returns the link texts in order. */
function leftBar({ parliament, elections, features, view = 'election', current = elections[0].id }) {
  const { manifest, state } = stateModule;
  manifest.init({
    parties: [], mapModes: {}, elections, defaultElection: null, files: {},
    parliamentFeatures: { [parliament]: features }, partyKeyAliases: {}, misc: {},
  });
  state.currentParliament = parliament;
  state.parliamentElections = elections;
  state.view = view;
  state.currentElection = elections.find((e) => e.id === current);
  dom.renderElectionLinks();
  return [...document.querySelectorAll('#mapsElectionList a')].map((a) => a.textContent);
}

describe('renderElectionLinks: Predict and Poll tracker placement', () => {
  it('puts Predict right after the anchor in the House list (2026, Predict, 2024 …)', () => {
    const elections = ['2026', '2024', '2022', '2020', '2018'].map((y) => election(`${y}-us-house`, `${y} Election`, 'us_house'));
    expect(leftBar({
      parliament: 'us_house', elections,
      features: { features: ['predict'], predictAnchorElectionId: '2026-us-house', nextElectionYear: 2028 },
    })).toEqual(['2026 Election', 'Predict 2028', '2024 Election', '2022 Election', '2020 Election', '2018 Election']);
  });

  const senateElections = () => [
    election('2026-us-senate', '2026 Election', 'us_senate'),
    election('current-senate', 'Current Senate', 'us_senate'),
    election('2024-us-senate', '2024 Election', 'us_senate'),
    election('2022-us-senate', '2022 Election', 'us_senate'),
  ];
  const senateFeatures = (extra = {}) => ({
    features: ['predict'], predictAnchorElectionId: '2026-us-senate', nextElectionYear: 2028,
    predict: { chamberElectionId: 'current-senate' }, ...extra,
  });

  it('puts Predict after Current Senate when the chamber election follows the anchor', () => {
    expect(leftBar({ parliament: 'us_senate', elections: senateElections(), features: senateFeatures() }))
      .toEqual(['2026 Election', 'Current Senate', 'Predict 2028', '2024 Election', '2022 Election']);
  });

  it('puts Predict and Poll tracker together after Current Senate', () => {
    expect(leftBar({
      parliament: 'us_senate', elections: senateElections(),
      features: senateFeatures({ features: ['predict', 'pollTracker'] }),
    })).toEqual(['2026 Election', 'Current Senate', 'Predict 2028', 'Poll tracker', '2024 Election', '2022 Election']);
  });

  it('keeps the extras right after the anchor when the chamber election is not next to it', () => {
    const elections = [
      election('2026-us-senate', '2026 Election', 'us_senate'),
      election('2024-us-senate', '2024 Election', 'us_senate'),
      election('current-senate', 'Current Senate', 'us_senate'),
    ];
    expect(leftBar({ parliament: 'us_senate', elections, features: senateFeatures() }))
      .toEqual(['2026 Election', 'Predict 2028', '2024 Election', 'Current Senate']);
  });

  it('keeps the extras after the anchor when the anchor is followed by an unrelated election', () => {
    const elections = [
      election('2028-x', '2028 Election', 'p'),
      election('a', 'Other', 'p'),
      election('b', 'Another', 'p'),
    ];
    expect(leftBar({
      parliament: 'p', elections,
      features: { features: ['predict', 'pollTracker'], predictAnchorElectionId: '2028-x', nextElectionYear: 2030 },
    })).toEqual(['2028 Election', 'Predict 2030', 'Poll tracker', 'Other', 'Another']);
  });

  it('is unchanged for a Westminster-style list led by a forecast anchor', () => {
    const elections = [
      election('current-prediction', 'Prediction', 'westminster'),
      election('current-parliament', 'Current Parliament', 'westminster'),
      election('2024-general', '2024 General Election', 'westminster'),
    ];
    expect(leftBar({
      parliament: 'westminster', elections,
      features: { features: ['predict', 'pollTracker'], predictAnchorElectionId: 'current-prediction', nextElectionYear: 2029 },
    })).toEqual(['Prediction', 'Predict 2029', 'Poll tracker', 'Current Parliament', '2024 General Election']);
  });

  it('appends the extras at the end when the anchor is missing or hidden', () => {
    const elections = [election('a', 'A', 'p'), election('b', 'B', 'p')];
    expect(leftBar({
      parliament: 'p', elections,
      features: { features: ['predict'], predictAnchorElectionId: 'gone', nextElectionYear: 2030 },
    })).toEqual(['A', 'B', 'Predict 2030']);
    expect(leftBar({
      parliament: 'p', elections,
      features: { features: ['predict'], predictAnchorElectionId: 'a', hiddenElectionIds: ['a'], nextElectionYear: 2030 },
    })).toEqual(['B', 'Predict 2030']);
  });

  it('adds nothing for a parliament without the features', () => {
    expect(leftBar({
      parliament: 'p', elections: [election('a', 'A', 'p')],
      features: { features: [], predictAnchorElectionId: 'a' },
    })).toEqual(['A']);
  });
});

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { Seat, ElectionSummary, manifest, state, buildRouteSearchParams } from '../state.js';

// Restores the manifest singleton to a fully-zeroed, hydrated baseline so a configuring
// block can't leak party/region/file/feature lookups into a later block. init() does
// Object.assign + re-hydrate, so every key a block might set must be listed here to be
// cleared. Run before AND after every test for order-independence.
function resetManifest() {
  manifest.init({ parties: [], mapModes: {}, elections: [], files: {}, parliamentFeatures: {}, partyKeyAliases: {} });
}

beforeEach(resetManifest);
afterEach(resetManifest);

// ─── Seat.matchesPrimaryFilters ──────────────────────────────────────────────
describe('Seat.matchesPrimaryFilters', () => {
  const openFilter = { party: 'all', region: 'all', secondParty: 'all', majorityMin: 0, majorityMax: 100, gainsOnly: false, upcoming: 'all' };
  // 30% majority (margin 300 / turnout 1000); second place is conservative.
  const makeSeat = () => new Seat({ seat: 'A', region: 'london', winner: 'labour', votes: { labour: 600, conservative: 300, libdems: 100 } });

  it('passes a seat when no filters are active', () => {
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, openFilter, null)).toBe(true);
  });

  it('filters by winning party', () => {
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, party: 'labour' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, party: 'conservative' }, null)).toBe(false);
  });

  it('normalises a legacy "other" winner to "others" for the party filter', () => {
    const seat = makeSeat();
    seat.winner = 'other';
    expect(Seat.matchesPrimaryFilters(seat, null, { ...openFilter, party: 'others' }, null)).toBe(true);
  });

  it('filters by region', () => {
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, region: 'london' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, region: 'scotland' }, null)).toBe(false);
  });

  it('filters by majority range', () => {
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, majorityMin: 40 }, null)).toBe(false);
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, majorityMax: 20 }, null)).toBe(false);
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, majorityMin: 10, majorityMax: 50 }, null)).toBe(true);
  });

  it('filters by second-place party', () => {
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, secondParty: 'conservative' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, secondParty: 'libdems' }, null)).toBe(false);
  });

  it('gainsOnly with a by-election set matches on seat name membership', () => {
    const filter = { ...openFilter, gainsOnly: true };
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, filter, new Set(['A']))).toBe(true);
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, filter, new Set(['B']))).toBe(false);
  });

  it('gainsOnly without a by-election set falls back to gainFromParty (comparison winner)', () => {
    const filter = { ...openFilter, gainsOnly: true };
    expect(Seat.matchesPrimaryFilters(makeSeat(), { winner: 'conservative' }, filter, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(makeSeat(), { winner: 'labour' }, filter, null)).toBe(false);
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, filter, null)).toBe(false);
  });

  it('multi-member: party and cycle filters match per member, including split seats', () => {
    const bothR = new Seat({
      seat: 'Alabama', region: 'eastsouthcentral', winner: 'republican',
      members: [{ party: 'republican', up: 2026 }, { party: 'republican', up: 2028 }],
    });
    const split = new Seat({
      seat: 'Maine', region: 'newengland', winner: 'split',
      members: [{ party: 'independent', up: 2026 }, { party: 'republican', up: 2030 }],
    });
    // Cycle filter: the seat is visible when any member is up that year.
    expect(Seat.matchesPrimaryFilters(bothR, null, { ...openFilter, upcoming: '2026' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(bothR, null, { ...openFilter, upcoming: '2030' }, null)).toBe(false);
    expect(Seat.matchesPrimaryFilters(bothR, null, { ...openFilter, upcoming: 'all' }, null)).toBe(true);
    // Party filter matches per member, so the split seat passes the 'independent' filter (it
    // holds one) — a combined-winner test would wrongly exclude it.
    expect(Seat.matchesPrimaryFilters(split, null, { ...openFilter, party: 'independent' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(split, null, { ...openFilter, party: 'republican' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(split, null, { ...openFilter, party: 'democrat' }, null)).toBe(false);
    // The cycle filter only applies to multi-member chambers; single-member seats ignore it.
    expect(Seat.matchesPrimaryFilters(makeSeat(), null, { ...openFilter, upcoming: '2026' }, null)).toBe(true);
  });
});

// ─── Seat.choroplethValue ────────────────────────────────────────────────────
describe('Seat.choroplethValue', () => {
  const seat = { turnout: 1000, votes: { labour: 500, conservative: 500 } };
  const comparison = { turnout: 1000, votes: { labour: 400, conservative: 600 } };

  it('returns null for a delta metric when no comparison seat is available', () => {
    expect(Seat.choroplethValue(seat, null, true, 'labour')).toBe(null);
  });

  it('returns the vote-share delta against the comparison seat', () => {
    expect(Seat.choroplethValue(seat, comparison, true, 'labour')).toBe(10);
  });

  it('returns the absolute vote share when not a delta metric', () => {
    expect(Seat.choroplethValue(seat, null, false, 'labour')).toBe(50);
  });
});

// ─── Seat.fromRaw numeric-id ingest (the pf-results-v4 path) ──────────────────
describe('Seat.fromRaw with numeric manifest refs', () => {
  beforeEach(() => {
    manifest.init({
      parties: [
        { id: 1, key: 'snp', name: 'SNP' },
        { id: 2, key: 'labour', name: 'Labour' },
      ],
      mapModes: { 1: { regions: [{ id: 10, name: 'Glasgow' }] } },
    });
  });
  afterEach(resetManifest);

  it('resolves integer region, winner, and party-vote refs through the manifest', () => {
    const seat = Seat.fromRaw({ n: 'X', r: 10, w: 1, p: [[1, 600], [2, 400]] });
    expect(seat.region).toBe('glasgow');
    expect(seat.winner).toBe('snp');
    expect(seat.votes).toEqual({ snp: 600, labour: 400 });
  });

  it('falls back to "unknown" for an unmapped numeric region id (incl. r=0)', () => {
    expect(Seat.fromRaw({ n: 'X', r: 0, w: 1, p: [[1, 10]] }).region).toBe('unknown');
    expect(Seat.fromRaw({ n: 'X', r: 99, w: 1, p: [[1, 10]] }).region).toBe('unknown');
  });

  it('stringifies an unknown numeric party id rather than dropping it', () => {
    expect(Seat.fromRaw({ n: 'X', r: 10, w: 99, p: [[1, 10]] }).winner).toBe('99');
  });
});

// ─── Manifest.resolvePartyRef ────────────────────────────────────────────────
describe('manifest.resolvePartyRef', () => {
  beforeEach(() => { manifest.init({ parties: [{ id: 1, key: 'snp', name: 'SNP' }], partyKeyAliases: { uup: 'uu', reformuk: 'reform', liberaldemocrats: 'libdems' } }); });
  afterEach(resetManifest);

  it('resolves a numeric id to the party key', () => {
    expect(manifest.resolvePartyRef(1)).toBe('snp');
  });

  it('stringifies an unknown numeric id', () => {
    expect(manifest.resolvePartyRef(42)).toBe('42');
  });

  it('folds known string aliases onto canonical keys', () => {
    expect(manifest.resolvePartyRef('uup')).toBe('uu');
    expect(manifest.resolvePartyRef('Reform UK')).toBe('reform');
    expect(manifest.resolvePartyRef('Liberal Democrats')).toBe('libdems');
  });

  it('passes through an unknown string key and maps empty input to "others"', () => {
    expect(manifest.resolvePartyRef('labour')).toBe('labour');
    expect(manifest.resolvePartyRef('')).toBe('others');
    expect(manifest.resolvePartyRef(null)).toBe('others');
  });
});

// ─── Manifest.resolveElectionFiles ───────────────────────────────────────────
describe('manifest.resolveElectionFiles', () => {
  beforeEach(() => {
    manifest.init({
      files: { elections: {
        mapsById: { 5: 'maps/x.topo.json' },
        electionsById: { e1: 'results/e1.json', e0: 'results/e0.json' },
      } },
    });
  });
  afterEach(resetManifest);

  it('resolves map and data files, with no comparison by default', () => {
    expect(manifest.resolveElectionFiles({ id: 'e1', mapId: 5 })).toEqual({
      mapFile: 'maps/x.topo.json', dataFile: 'results/e1.json', comparisonDataFile: null,
    });
  });

  it('resolves the comparison data file when configured', () => {
    expect(manifest.resolveElectionFiles({ id: 'e1', mapId: 5, comparisonElectionId: 'e0' }).comparisonDataFile)
      .toBe('results/e0.json');
  });

  it('throws when the map file is missing', () => {
    expect(() => manifest.resolveElectionFiles({ id: 'e1', mapId: 99 })).toThrow(/Missing file configuration/);
  });

  it('throws when the data file is missing', () => {
    expect(() => manifest.resolveElectionFiles({ id: 'eX', mapId: 5 })).toThrow(/Missing file configuration/);
  });
});

// ─── Manifest.buildRegionLabelLookup ─────────────────────────────────────────
describe('manifest.buildRegionLabelLookup', () => {
  beforeEach(() => {
    manifest.init({ mapModes: { 1: { regions: [
      { id: 1, name: 'Glasgow' }, { id: 2, name: 'North East' }, { id: 3, name: '' },
    ] } } });
  });
  afterEach(resetManifest);

  it('maps normalised region keys to display labels, skipping empty names', () => {
    const lookup = manifest.buildRegionLabelLookup('1');
    expect(lookup.get('glasgow')).toBe('Glasgow');
    expect(lookup.get('northeast')).toBe('North East');
    expect(lookup.size).toBe(2);
  });

  it('returns an empty map for an unknown mapId', () => {
    expect(manifest.buildRegionLabelLookup('99').size).toBe(0);
  });
});

// ─── ElectionSummary.summarize (modes / dedupe / other-fold) ──────────────────
describe('ElectionSummary.summarize', () => {
  const findParty = (data, key) => data.parties.find((p) => p.party === key);
  // The multi-member branch reads state.currentElection.multiMember; clear it after each test.
  afterEach(() => { state.currentElection = null; });

  it('all mode: counts list seats but excludes their votes (separate ballot)', () => {
    const seats = [
      { seat: 'Glasgow Central', region: 'glasgow', winner: 'labour', votes: { labour: 600, conservative: 400 } },
      { seat: 'Glasgow List 1', region: 'glasgow', winner: 'snp', votes: { snp: 1000 } },
    ];
    const data = ElectionSummary.summarize(seats, 'all');
    expect(findParty(data, 'snp')).toMatchObject({ seats: 1, votes: 0 });
    expect(findParty(data, 'labour')).toMatchObject({ seats: 1, votes: 600 });
    expect(data.totalVotes).toBe(1000);
  });

  it('constituency mode: skips list seats entirely but totalSeats stays the chamber size', () => {
    const seats = [
      { seat: 'Glasgow Central', region: 'glasgow', winner: 'labour', votes: { labour: 600, conservative: 400 } },
      { seat: 'Glasgow List 1', region: 'glasgow', winner: 'snp', votes: { snp: 1000 } },
    ];
    const data = ElectionSummary.summarize(seats, 'constituency');
    expect(findParty(data, 'snp')).toBeUndefined();
    expect(data.totalSeats).toBe(2);
  });

  it('list mode: counts each (region, party) vote total once despite duplicated list seats', () => {
    const seats = [
      { seat: 'Glasgow List 1', region: 'glasgow', winner: 'snp', votes: { snp: 1000 } },
      { seat: 'Glasgow List 2', region: 'glasgow', winner: 'snp', votes: { snp: 1000 } },
    ];
    const data = ElectionSummary.summarize(seats, 'list');
    expect(findParty(data, 'snp')).toMatchObject({ seats: 2, votes: 1000 });
    expect(data.totalVotes).toBe(1000);
  });

  it('folds the legacy "other" key into "others" for both seats and votes', () => {
    const seats = [{ seat: 'A', region: 'r', winner: 'other', votes: { other: 500, labour: 500 } }];
    const data = ElectionSummary.summarize(seats, 'all');
    expect(findParty(data, 'others')).toMatchObject({ seats: 1, votes: 500 });
    expect(findParty(data, 'other')).toBeUndefined();
  });

  it('multi-member: tallies all members by default but only matching ones when filtering', () => {
    state.currentElection = { multiMember: true };
    const seats = [
      { seat: 'Alabama', members: [{ party: 'republican', up: 2026 }, { party: 'republican', up: 2028 }] },
      { seat: 'Arizona', members: [{ party: 'democrat', up: 2026 }, { party: 'republican', up: 2030 }] },
    ];
    // No filter: every member counts (4 senators across the two states).
    expect(ElectionSummary.summarize(seats, 'all').totalSeats).toBe(4);
    // 2026 cycle: only the member up that year in each state (one R, one D).
    const cycle = ElectionSummary.summarize(seats, 'all', { upcoming: '2026' });
    expect(cycle.totalSeats).toBe(2);
    expect(findParty(cycle, 'republican')).toMatchObject({ seats: 1 });
    expect(findParty(cycle, 'democrat')).toMatchObject({ seats: 1 });
    // Party filter: counts that party's senators wherever they sit, incl. the split Arizona
    // seat (1 D) — not just states the party fully holds. Republicans: 2 (Alabama) + 1 (AZ) = 3.
    const dem = ElectionSummary.summarize(seats, 'all', { party: 'democrat' });
    expect(dem.totalSeats).toBe(1);
    expect(findParty(dem, 'democrat')).toMatchObject({ seats: 1 });
    expect(ElectionSummary.summarize(seats, 'all', { party: 'republican' }).totalSeats).toBe(3);
  });
});

// ─── ElectionSummary subtitle (majority vs hung) ──────────────────────────────
describe('ElectionSummary subtitle text', () => {
  // The file-level resetManifest hooks already zero the manifest, so labelParty returns
  // the raw party key here.

  const seatsWon = (party, n) => Array.from({ length: n }, (_, i) => ({ seat: `${party}-${i}`, region: 'r', winner: party, votes: { [party]: 1 } }));

  it('reports an overall majority with the standard "majority of N" figure', () => {
    const summary = new ElectionSummary([...seatsWon('labour', 2), ...seatsWon('conservative', 1)], 'Test', 'all');
    expect(summary.text).toBe('Test · labour majority: 1');
  });

  it('reports a hung parliament when the leader is exactly on the half-line', () => {
    const summary = new ElectionSummary([...seatsWon('labour', 2), ...seatsWon('conservative', 2)], 'Test', 'all');
    expect(summary.text).toContain('Hung parliament');
    expect(summary.text).toContain('with 2 seats');
  });

  it('rounds the majority correctly for an odd-sized chamber', () => {
    // 4 of 5 seats: threshold 2.5, majority = round(2 × (4 − 2.5)) = 3.
    const summary = new ElectionSummary([...seatsWon('labour', 4), ...seatsWon('conservative', 1)], 'Test', 'all');
    expect(summary.text).toBe('Test · labour majority: 3');
  });

  it('leaves text null when no election name is supplied', () => {
    expect(new ElectionSummary(seatsWon('labour', 1), null, 'all').text).toBe(null);
  });
});

// ─── ElectionSummary.summarizeByRegion ───────────────────────────────────────
describe('ElectionSummary.summarizeByRegion', () => {
  it('aggregates seats and votes per region, falling back for missing region/winner', () => {
    const seats = [
      { region: 'glasgow', winner: 'snp', votes: { snp: 600, labour: 400 } },
      { region: 'glasgow', winner: 'labour', votes: { snp: 300, labour: 700 } },
      { region: 'lothian', winner: 'snp', votes: { snp: 500 } },
      { region: undefined, winner: undefined, votes: {} },
    ];
    const byRegion = ElectionSummary.summarizeByRegion(seats);
    expect(byRegion.get('glasgow')).toEqual({
      seatsByParty: { snp: 1, labour: 1 },
      votesByParty: { snp: 900, labour: 1100 },
    });
    expect(byRegion.get('lothian').seatsByParty).toEqual({ snp: 1 });
    expect(byRegion.get('unknown').seatsByParty).toEqual({ others: 1 });
  });
});

// ─── buildRouteSearchParams ──────────────────────────────────────────────────
describe('buildRouteSearchParams', () => {
  let savedElection;
  let savedPredictModel;

  beforeEach(() => {
    globalThis.window = { location: { search: '' } };
    savedElection = state.currentElection;
    savedPredictModel = state.predictModel;
    state.currentElection = { id: 'e1' };
    state.predictModel = null;
  });

  afterEach(() => {
    delete globalThis.window;
    state.currentElection = savedElection;
    state.predictModel = savedPredictModel;
  });

  it('polltracker view drops the election and predict params', () => {
    globalThis.window.location.search = '?election=e1&predict=abc';
    const params = buildRouteSearchParams('polltracker');
    expect(params.get('view')).toBe('polltracker');
    expect(params.has('election')).toBe(false);
    expect(params.has('predict')).toBe(false);
  });

  it('election view sets the election id and removes any predict payload', () => {
    globalThis.window.location.search = '?predict=stale';
    const params = buildRouteSearchParams('election');
    expect(params.get('view')).toBe('election');
    expect(params.get('election')).toBe('e1');
    expect(params.has('predict')).toBe(false);
  });

  it('predict view with an empty serialized payload removes the predict param', () => {
    state.predictModel = { serialize: () => '' };
    globalThis.window.location.search = '?predict=old';
    expect(buildRouteSearchParams('predict').has('predict')).toBe(false);
  });

  it('predict view embeds the serialized payload when present', () => {
    state.predictModel = { serialize: () => 'XYZ' };
    expect(buildRouteSearchParams('predict').get('predict')).toBe('XYZ');
  });

  it('omits the election param when currentElection has no id', () => {
    state.currentElection = { id: null };
    expect(buildRouteSearchParams('election').has('election')).toBe(false);
  });
});

describe('AppState.shouldShowCountdown', () => {
  let saved;

  beforeEach(() => {
    manifest.init({
      parties: [], mapModes: {}, elections: [], files: {},
      parliamentFeatures: {
        // Holyrood has a confirmed upcoming date; Westminster's is not yet set (null).
        holyrood: { nextElectionDate: '2031-05-01', nextElectionLabel: 'Holyrood election' },
        westminster: { nextElectionDate: null, nextElectionLabel: 'UK general election' },
      },
    });
    saved = { view: state.view, parliament: state.currentParliament, election: state.currentElection };
    state.currentParliament = 'holyrood';
    state.view = 'predict';
    state.currentElection = { id: 'e1', model: true };
  });

  afterEach(() => {
    state.view = saved.view;
    state.currentParliament = saved.parliament;
    state.currentElection = saved.election;
  });

  it('shows on a parliament predict view when an upcoming date is configured', () => {
    expect(state.shouldShowCountdown()).toBe(true);
  });

  it('shows when viewing the prediction election (model) outside predict mode', () => {
    state.view = 'election';
    expect(state.shouldShowCountdown()).toBe(true);
  });

  it('hides on a non-prediction election view', () => {
    state.view = 'election';
    state.currentElection = { id: 'e1', model: false };
    expect(state.shouldShowCountdown()).toBe(false);
  });

  it('hides in poll tracker view even for a prediction', () => {
    state.view = 'polltracker';
    expect(state.shouldShowCountdown()).toBe(false);
  });

  it('hides when the current parliament has no upcoming date set', () => {
    state.currentParliament = 'westminster';
    expect(state.shouldShowCountdown()).toBe(false);
  });
});

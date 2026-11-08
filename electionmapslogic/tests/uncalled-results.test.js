import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { Seat, ElectionData, ElectionSummary, manifest, state } from '../state.js';

// Results-payload status fields (`c`, `pc`, `sp`, `rd`, top-level `specials`) and the
// whole-comparison totals fix, exercised through the real state classes.

function resetManifest() {
  manifest.init({
    parties: [], mapModes: {}, elections: [], defaultElection: null, files: {},
    parliamentFeatures: {}, partyKeyAliases: {}, misc: {},
  });
}

beforeEach(resetManifest);
afterEach(() => {
  resetManifest();
  state.currentElection = null;
  state.electionData = null;
  state.comparisonElectionData = null;
  state.mapConfig = null;
  state.resetFilters();
});

const rawSeat = (n, w, votes, extra = {}) => ({
  n, r: 'region', w, p: Object.entries(votes).map(([party, total]) => [party, total]), ...extra,
});
const findParty = (data, key) => data.parties.find((p) => p.party === key);

describe('Seat status fields: parsing', () => {
  it('fromRaw decodes c, pc, sp and rd', () => {
    const seat = Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 600, republican: 400 }, { c: false, pc: 62.5, sp: true, rd: 'rcv-final' }));
    expect(seat.called).toBe(false);
    expect(seat.pctCounted).toBe(62.5);
    expect(seat.sameParty).toBe(true);
    expect(seat.round).toBe('rcv-final');
    expect(seat.winner).toBe('democrat');
  });

  it('an older payload seat is called with no status fields', () => {
    const seat = Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 600 }));
    expect(seat.called).toBe(true);
    expect(seat.pctCounted).toBeNull();
    expect(seat.sameParty).toBe(false);
    expect(seat.round).toBeNull();
    expect(seat.senateClass).toBeNull();
    expect(seat.fillKind()).toBe('called');
    expect(seat.statusNotes()).toEqual({ uncalledTag: '', samePartyNote: '', roundNote: '' });
  });

  it('only c === false marks a seat uncalled', () => {
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { c: true })).called).toBe(true);
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { c: 0 })).called).toBe(true);
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { c: false })).called).toBe(false);
  });

  it('constructor defaults: called, no pc, no sp, no rd', () => {
    const seat = new Seat({ seat: 'A', winner: 'labour', votes: { labour: 1 } });
    expect(seat).toMatchObject({ called: true, pctCounted: null, sameParty: false, round: null });
  });

  it('cloning with new Seat(seat) preserves the status fields', () => {
    const original = Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 600 }, { c: false, pc: 12, sp: true, rd: 'runoff', cl: 2 }));
    const clone = new Seat(original);
    expect(clone).toMatchObject({ called: false, pctCounted: 12, sameParty: true, round: 'runoff', senateClass: 2 });
  });

  it('pc of 0 is kept (nothing counted yet), a non-number is null', () => {
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { c: false, pc: 0 })).pctCounted).toBe(0);
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { c: false, pc: 'x' })).pctCounted).toBeNull();
  });
});

describe('uncalled seat winner resolution', () => {
  it('an uncalled seat with w keeps the leader and is provisional', () => {
    const seat = Seat.fromRaw(rawSeat('A', 'republican', { republican: 51, democrat: 49 }, { c: false }));
    expect(seat.winner).toBe('republican');
    expect(seat.fillKind()).toBe('provisional');
  });

  it('an uncalled seat without w stays unresolved and neutral, not Others', () => {
    const seat = Seat.fromRaw({ n: 'A', r: 'r', p: [], c: false });
    expect(seat.winner).toBeNull();
    expect(seat.fillKind()).toBe('neutral');
    // A clone keeps it unresolved too.
    expect(new Seat(seat).winner).toBeNull();
  });

  it('a called seat without w still defaults to Others, as before', () => {
    const seat = Seat.fromRaw({ n: 'A', r: 'r', p: [] });
    expect(seat.winner).toBe('others');
    expect(seat.fillKind()).toBe('called');
  });

  it('an uncalled seat has no gain or hold label', () => {
    const seat = Seat.fromRaw(rawSeat('A', 'republican', { republican: 51 }, { c: false }));
    expect(seat.gainFromParty('democrat')).toBeNull();
    const called = Seat.fromRaw(rawSeat('A', 'republican', { republican: 51 }));
    expect(called.gainFromParty('democrat')).toBe('democrat');
  });

  it('an uncalled seat with a leader is not hidden by the leading-party filter, a leaderless one is', () => {
    const open = { party: 'all', region: 'all', secondParty: 'all', majorityMin: 0, majorityMax: 100, gainsOnly: false, upcoming: 'all' };
    const led = Seat.fromRaw(rawSeat('A', 'republican', { republican: 51, democrat: 49 }, { c: false }));
    const leaderless = Seat.fromRaw({ n: 'B', r: 'r', p: [], c: false });
    expect(Seat.matchesPrimaryFilters(led, null, { ...open, party: 'republican' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(leaderless, null, { ...open, party: 'others' }, null)).toBe(false);
    expect(Seat.matchesPrimaryFilters(leaderless, null, open, null)).toBe(true);
  });
});

describe('Seat#statusNotes (popup tags)', () => {
  it('UNCALLED with the counted percentage', () => {
    const seat = Seat.fromRaw(rawSeat('A', 'republican', { republican: 1 }, { c: false, pc: 62.5 }));
    expect(seat.statusNotes().uncalledTag).toBe('UNCALLED · 62.5% counted');
  });

  it('whole percentages drop the decimal, and 0 is shown', () => {
    expect(Seat.fromRaw(rawSeat('A', 'republican', { republican: 1 }, { c: false, pc: 80 })).statusNotes().uncalledTag).toBe('UNCALLED · 80% counted');
    expect(Seat.fromRaw(rawSeat('A', 'republican', { republican: 1 }, { c: false, pc: 0 })).statusNotes().uncalledTag).toBe('UNCALLED · 0% counted');
  });

  it('just UNCALLED when the counted share is unknown', () => {
    expect(Seat.fromRaw(rawSeat('A', 'republican', { republican: 1 }, { c: false })).statusNotes().uncalledTag).toBe('UNCALLED');
  });

  it('same-party and decisive-round notes', () => {
    const notes = Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { sp: true, rd: 'rcv-final' })).statusNotes();
    expect(notes.samePartyNote).toBe('Same-party contest: votes combined');
    expect(notes.roundNote).toBe('Decided by ranked-choice final round');
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { rd: 'runoff' })).statusNotes().roundNote).toBe('Decided in a runoff');
  });

  it('an unrecognised round code (including inherited object keys) gives no label', () => {
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { rd: 'constructor' })).statusNotes().roundNote).toBe('');
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { rd: 'other' })).statusNotes().roundNote).toBe('');
  });

  it('a called seat has no UNCALLED tag even when pc is present', () => {
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 1 }, { pc: 50 })).statusNotes().uncalledTag).toBe('');
  });
});

describe('summarize: uncalled seats', () => {
  const mix = () => [
    Seat.fromRaw(rawSeat('A', 'republican', { republican: 60, democrat: 40 })),
    Seat.fromRaw(rawSeat('B', 'democrat', { democrat: 60, republican: 40 })),
    Seat.fromRaw(rawSeat('C', 'republican', { republican: 51, democrat: 49 }, { c: false })),
    Seat.fromRaw({ n: 'D', r: 'r', p: [], c: false }),
  ];

  it('credits no party for uncalled seats and counts them separately', () => {
    const data = ElectionSummary.summarize(mix(), 'all');
    expect(findParty(data, 'republican').seats).toBe(1);
    expect(findParty(data, 'democrat').seats).toBe(1);
    expect(data.uncalled).toBe(2);
    expect(data.totalSeats).toBe(4);
  });

  it('a leaderless uncalled seat creates no Others row', () => {
    const data = ElectionSummary.summarize(mix(), 'all');
    expect(findParty(data, 'others')).toBeUndefined();
  });

  it('uncalled votes still count toward vote totals', () => {
    const data = ElectionSummary.summarize(mix(), 'all');
    expect(findParty(data, 'republican').votes).toBe(60 + 40 + 51);
  });

  it('no uncalled seats in an older payload', () => {
    const data = ElectionSummary.summarize([Seat.fromRaw(rawSeat('A', 'republican', { republican: 1 }))], 'all');
    expect(data.uncalled).toBe(0);
  });

  it('counts uncalled electoral votes on EV-tally maps', () => {
    manifest.init({ mapModes: { 9: { tally: 'electoralVotes' } } });
    state.currentElection = { mapId: 9 };
    const seats = [
      Seat.fromRaw(rawSeat('Texas', 'republican', { republican: 1 }, { ev: 40 })),
      Seat.fromRaw(rawSeat('Ohio', 'republican', { republican: 1 }, { ev: 17, c: false })),
    ];
    const data = ElectionSummary.summarize(seats, 'all');
    expect(data.uncalled).toBe(17);
    expect(findParty(data, 'republican').seats).toBe(40);
    expect(data.totalSeats).toBe(57);
  });

  it('uncalled seats are excluded from the per-region seat counts', () => {
    const byRegion = ElectionSummary.summarizeByRegion(mix());
    expect(byRegion.get('region').seatsByParty).toEqual({ republican: 1, democrat: 1 });
  });
});

describe('subtitle with uncalled seats', () => {
  const won = (party, count, extra = {}) => Array.from({ length: count }, (_, i) =>
    Seat.fromRaw(rawSeat(`${party}${i}`, party, { [party]: 1 }, extra)));

  it('a majority secured by called seats is stated, with the uncalled count', () => {
    const summary = new ElectionSummary([...won('labour', 4), ...won('conservative', 1, { c: false })], 'Test', 'all');
    expect(summary.text).toBe('Test · labour majority: 3 · 1 uncalled');
  });

  it('is undecided, not hung, while uncalled seats could still give the leader a majority', () => {
    const summary = new ElectionSummary([...won('labour', 2), ...won('conservative', 2), ...won('libdems', 2, { c: false })], 'Test', 'all');
    expect(summary.text).toMatch(/^Test · Majority undecided - largest party .* with 2 seats · 2 uncalled$/);
  });

  it('stays hung when the uncalled seats cannot change that', () => {
    const summary = new ElectionSummary([...won('labour', 2), ...won('conservative', 2), ...won('libdems', 2), ...won('green', 1, { c: false })], 'Test', 'all');
    expect(summary.text).toMatch(/^Test · Hung parliament - largest party .* with 2 seats · 1 uncalled$/);
  });

  it('names no party before any votes are counted', () => {
    const empty = Array.from({ length: 3 }, (_, i) => Seat.fromRaw(rawSeat(`s${i}`, undefined, {}, { c: false })));
    expect(new ElectionSummary(empty, 'Test', 'all').text).toBe('Test · 3 uncalled');
  });

  it('subtitle is unchanged without uncalled seats', () => {
    expect(new ElectionSummary([...won('labour', 2), ...won('conservative', 1)], 'Test', 'all').text).toBe('Test · labour majority: 1');
  });
});

describe('Senate specials', () => {
  const payload = () => ({
    seats: [
      rawSeat('Florida', 'republican', { republican: 55, democrat: 45 }),
      rawSeat('Ohio', 'democrat', { democrat: 52, republican: 48 }),
      rawSeat('Texas', 'republican', { republican: 60, democrat: 40 }),
    ],
    specials: [
      { n: 'Florida', cl: 3, w: 'democrat', p: [['democrat', 51], ['republican', 49]] },
      { n: 'Ohio', cl: 3, c: false, pc: 40, p: [] },
    ],
  });

  it('parses specials into a Map by seat key, with the regular seat region', () => {
    manifest.init({ regionsById: new Map() });
    const data = new ElectionData({ ...payload(), seats: payload().seats.map((s) => ({ ...s, r: 'south' })) });
    expect(data.specials.size).toBe(2);
    const florida = data.specials.get('florida');
    expect(florida).toBeInstanceOf(Seat);
    expect(florida).toMatchObject({ senateClass: 3, winner: 'democrat', region: 'south', called: true });
    const ohio = data.specials.get('ohio');
    expect(ohio).toMatchObject({ called: false, pctCounted: 40, winner: null });
    expect(ohio.statusNotes().uncalledTag).toBe('UNCALLED · 40% counted');
  });

  it('a payload without specials has an empty Map', () => {
    expect(new ElectionData({ seats: payload().seats }).specials.size).toBe(0);
    expect(new ElectionData({ seats: [] }).specials.size).toBe(0);
  });

  it('specials are counted in the chamber summary alongside the regular seats', () => {
    const data = new ElectionData(payload());
    const totals = data.summary.data;
    expect(totals.totalSeats).toBe(5);
    expect(findParty(totals, 'republican').seats).toBe(2);
    expect(findParty(totals, 'democrat').seats).toBe(2); // Ohio regular + Florida special
    expect(totals.uncalled).toBe(1); // Ohio special
    expect(findParty(totals, 'democrat').votes).toBe(45 + 52 + 40 + 51);
  });

  it('the filtered totals count specials on their own result', () => {
    state.currentElection = { mapId: 1, byElectionSeats: null };
    state.electionData = new ElectionData(payload());
    state.mapConfig = { voteTotalsViews: [{ id: 'all' }] };
    state.applyMapFilters();
    state.recomputeVoteTotalsForMode();
    expect(state.filteredSeatsSummary.totalSeats).toBe(5);

    // Party filter: Florida's regular seat is Republican, but its special went Democratic.
    state.mapFilters.party = 'democrat';
    state.applyMapFilters();
    state.recomputeVoteTotalsForMode();
    expect(state.filteredSeatsSummary.totalSeats).toBe(2); // Ohio regular + Florida special
    expect(findParty(state.filteredSeatsSummary, 'democrat').seats).toBe(2);
  });

  it('the party filter lists a party that only appears in a special', () => {
    manifest.init({ regionsById: new Map() });
    state.electionData = new ElectionData({
      seats: [rawSeat('Florida', 'republican', { republican: 1 })],
      specials: [{ n: 'Florida', cl: 3, w: 'democrat', p: [['democrat', 51]] }],
    });
    const values = state.mapControlParties().map((o) => o.value);
    expect(values).toContain('democrat');
  });

  it('voteShareRows lists the special popup rows largest first with candidate names', () => {
    const data = new ElectionData({
      seats: [rawSeat('Florida', 'republican', { republican: 1 })],
      specials: [{ n: 'Florida', cl: 3, w: 'democrat', p: [['republican', 49, 'Rep Name'], ['democrat', 51, 'Dem Name']] }],
    });
    const rows = data.specials.get('florida').voteShareRows();
    expect(rows.map((r) => r.party)).toEqual(['democrat', 'republican']);
    expect(rows[0]).toMatchObject({ pct: 51, label: 'Dem Name' });
  });
});

describe('comparison totals', () => {
  // The current election has 3 districts; the comparison has 4. Key "d" exists only in the
  // comparison (a district that disappeared at reapportionment) and "c" only in the current.
  const current = () => new ElectionData({
    seats: [
      rawSeat('a', 'republican', { republican: 60, democrat: 40 }),
      rawSeat('b', 'republican', { republican: 55, democrat: 45 }),
      rawSeat('c', 'democrat', { democrat: 70, republican: 30 }),
    ],
  });
  const comparison = () => new ElectionData({
    seats: [
      rawSeat('a', 'democrat', { democrat: 60, republican: 40 }),
      rawSeat('b', 'republican', { republican: 55, democrat: 45 }),
      rawSeat('d', 'democrat', { democrat: 70, republican: 30 }),
      rawSeat('e', 'democrat', { democrat: 70, republican: 30 }),
    ],
  });

  const load = (cur, cmp) => {
    state.currentElection = { mapId: 1, byElectionSeats: null };
    state.mapConfig = { voteTotalsViews: [{ id: 'all' }] };
    state.electionData = cur;
    state.comparisonElectionData = cmp;
    state.applyMapFilters();
    state.recomputeVoteTotalsForMode();
  };

  it('with no filter, totals the whole comparison election, not just the matching keys', () => {
    load(current(), comparison());
    const cmp = state.filteredSeatsComparisonSummary;
    expect(cmp.totalSeats).toBe(4);
    expect(findParty(cmp, 'democrat').seats).toBe(3); // a, d, e
    expect(findParty(cmp, 'republican').seats).toBe(1); // b
    // Seat change per party: R 2 vs 1, D 1 vs 3.
    expect(findParty(state.filteredSeatsSummary, 'republican').seats - findParty(cmp, 'republican').seats).toBe(1);
    expect(findParty(state.filteredSeatsSummary, 'democrat').seats - findParty(cmp, 'democrat').seats).toBe(-2);
  });

  it('reapportionment-sized example: 435 seats against 428 matching keys totals all of the comparison', () => {
    const mk = (n, party) => rawSeat(n, party, { [party]: 1 });
    const comparisonSeats = [];
    for (let i = 0; i < 428; i += 1) comparisonSeats.push(mk(`s${i}`, i < 213 ? 'republican' : 'democrat'));
    for (let i = 0; i < 7; i += 1) comparisonSeats.push(mk(`gone${i}`, 'democrat'));
    const currentSeats = [];
    for (let i = 0; i < 428; i += 1) currentSeats.push(mk(`s${i}`, i < 213 ? 'republican' : 'democrat'));
    for (let i = 0; i < 7; i += 1) currentSeats.push(mk(`new${i}`, 'republican'));
    load(new ElectionData({ seats: currentSeats }), new ElectionData({ seats: comparisonSeats.slice(0, 435) }));
    expect(state.filteredSeatsComparisonSummary.totalSeats).toBe(435);
    // Districts missing from the comparison are simply not matched: the new seats show no
    // gain/hold label because their key has no comparison seat.
    expect(state.comparisonElectionData.seatsByKey.get('new0')).toBeUndefined();
  });

  it('with a filter active, keeps the key-matched comparison seats', () => {
    load(current(), comparison());
    state.mapFilters.party = 'republican';
    state.applyMapFilters();
    state.recomputeVoteTotalsForMode();
    const cmp = state.filteredSeatsComparisonSummary;
    // Visible current seats a and b; their comparison counterparts only.
    expect(cmp.totalSeats).toBe(2);
    expect(findParty(cmp, 'democrat').seats).toBe(1);
    expect(findParty(cmp, 'republican').seats).toBe(1);
  });

  it('same seat set (same-map comparison): identical to the key-matched total', () => {
    const seatsA = [rawSeat('a', 'labour', { labour: 5 }), rawSeat('b', 'labour', { labour: 5 })];
    const seatsB = [rawSeat('a', 'tory', { tory: 5 }), rawSeat('b', 'labour', { labour: 5 })];
    load(new ElectionData({ seats: seatsA }), new ElectionData({ seats: seatsB }));
    const unfiltered = state.filteredSeatsComparisonSummary;
    // Force the key-matched path by recomputing the old way.
    const keyMatched = ElectionSummary.summarize(state.mapSeatsVisible.comparisonSeats, 'all');
    expect(unfiltered).toEqual(keyMatched);
  });

  describe('seat change leaves out uncalled seats', () => {
    // Comparison: R holds a, b, c; D holds d. Current mid-count: a is uncalled, the rest are
    // called, and "z" is a new district the comparison does not have.
    const cmpSeats = () => new ElectionData({
      seats: [
        rawSeat('a', 'republican', { republican: 1 }), rawSeat('b', 'republican', { republican: 1 }),
        rawSeat('c', 'republican', { republican: 1 }), rawSeat('d', 'democrat', { democrat: 1 }),
        rawSeat('gone', 'republican', { republican: 1 }),
      ],
    });
    const curSeats = (extra = []) => new ElectionData({
      seats: [
        rawSeat('a', 'democrat', { democrat: 51, republican: 49 }, { c: false }),
        rawSeat('b', 'democrat', { democrat: 1 }), rawSeat('c', 'republican', { republican: 1 }),
        rawSeat('d', 'democrat', { democrat: 1 }), rawSeat('z', 'democrat', { democrat: 1 }),
        ...extra,
      ],
    });
    const seatChange = (party) =>
      findParty(state.filteredSeatsSummary, party).seats - (findParty(state.filteredSeatsComparisonSummary, party)?.seats ?? 0);

    it('compares called seats only, and still counts comparison districts missing from the current election', () => {
      load(curSeats(), cmpSeats());
      // Called now: D b, d, z; R c. Comparison without the uncalled district a: R b, c, gone; D d.
      expect(findParty(state.filteredSeatsSummary, 'democrat').seats).toBe(3);
      expect(seatChange('democrat')).toBe(2); // 3 - 1: the uncalled seat is not a gain
      expect(seatChange('republican')).toBe(-2); // 1 - 3: the uncalled seat is not a loss
      expect(state.filteredSeatsSummary.uncalled).toBe(1);
    });

    it('leaves the vote totals alone', () => {
      load(curSeats(), cmpSeats());
      expect(findParty(state.filteredSeatsComparisonSummary, 'republican').votes).toBe(4); // a, b, c, gone: nothing left out
    });

    it('applies on the filtered path too', () => {
      load(curSeats(), cmpSeats());
      state.mapFilters.party = 'democrat';
      state.applyMapFilters();
      state.recomputeVoteTotalsForMode();
      // Visible: D-called b, d, z, plus uncalled a (its leader is D). Comparison counterparts of
      // b, d (z has none); a is skipped as uncalled.
      expect(findParty(state.filteredSeatsComparisonSummary, 'republican').seats).toBe(1); // b
      expect(findParty(state.filteredSeatsComparisonSummary, 'democrat').seats).toBe(1); // d
    });

    it('is unchanged when no seat is uncalled', () => {
      const called = new ElectionData({ seats: [rawSeat('a', 'democrat', { democrat: 1 }), rawSeat('b', 'republican', { republican: 1 })] });
      load(called, cmpSeats());
      const plain = ElectionSummary.summarize(state.comparisonElectionData.currentSeats, 'all');
      expect(state.filteredSeatsComparisonSummary).toEqual(plain);
    });

    it('reapportionment still sums: whole comparison counted apart from the uncalled districts', () => {
      const mk = (n, party, extra = {}) => rawSeat(n, party, { [party]: 1 }, extra);
      const cmp = [...Array.from({ length: 428 }, (_, i) => mk(`s${i}`, i < 213 ? 'republican' : 'democrat')),
        ...Array.from({ length: 7 }, (_, i) => mk(`gone${i}`, 'democrat'))];
      const cur = [...Array.from({ length: 428 }, (_, i) => mk(`s${i}`, i < 213 ? 'republican' : 'democrat', i < 10 ? { c: false } : {})),
        ...Array.from({ length: 7 }, (_, i) => mk(`new${i}`, 'republican'))];
      load(new ElectionData({ seats: cur }), new ElectionData({ seats: cmp }));
      // 10 uncalled R-leading districts are out of both sides; 7 gone D districts stay in the comparison.
      expect(findParty(state.filteredSeatsComparisonSummary, 'republican').seats).toBe(203);
      expect(findParty(state.filteredSeatsComparisonSummary, 'democrat').seats).toBe(215 + 7);
      expect(seatChange('republican')).toBe(203 + 7 - 203); // current R: 203 called + 7 new
      expect(seatChange('democrat')).toBe(215 - 222);
    });
  });

  describe('US Senate compares matching states only', () => {
    // 2020 comparison: states a, b, c plus x (not up in 2026) and AZ (2020's special sits in the
    // regular file); its own folded special for b must be ignored. 2026 current: a, b, c plus FL and
    // OH, whose off-cycle specials are not in the 2020 file at all, and a folded special for c.
    const cmpFile = () => new ElectionData({
      seats: [
        rawSeat('a', 'republican', { republican: 60, democrat: 40 }),
        rawSeat('b', 'republican', { republican: 55, democrat: 45 }),
        rawSeat('c', 'democrat', { democrat: 70, republican: 30 }),
        rawSeat('x', 'democrat', { democrat: 80, republican: 20 }),
        rawSeat('arizona', 'democrat', { democrat: 51, republican: 49 }),
      ],
      specials: [rawSeat('b', 'democrat', { democrat: 90, republican: 10 }, { cl: 3 })],
    });
    const curFile = (extraSeats = []) => new ElectionData({
      seats: [
        rawSeat('a', 'democrat', { democrat: 52, republican: 48 }),
        rawSeat('b', 'republican', { republican: 55, democrat: 45 }),
        rawSeat('c', 'democrat', { democrat: 70, republican: 30 }),
        rawSeat('florida', 'republican', { republican: 100 }),
        rawSeat('ohio', 'republican', { republican: 100 }),
        ...extraSeats,
      ],
      specials: [rawSeat('c', 'republican', { republican: 100 }, { cl: 3 })],
    });
    const loadSenate = (cur, cmp, type = 'us_senate') => {
      state.currentElection = { mapId: 1, type, byElectionSeats: null };
      state.mapConfig = { voteTotalsViews: [{ id: 'all' }] };
      state.electionData = cur;
      state.comparisonElectionData = cmp;
      state.applyMapFilters();
      state.recomputeVoteTotalsForMode();
    };
    const seatDelta = (party) => (findParty(state.filteredSeatsDeltaSummary, party)?.seats ?? 0)
      - (findParty(state.filteredSeatsComparisonSummary, party)?.seats ?? 0);

    it('compares only states up in both elections, leaving specials and unmatched states out', () => {
      loadSenate(curFile(), cmpFile());
      const cmp = state.filteredSeatsComparisonSummary;
      expect(cmp.totalSeats).toBe(3); // a, b, c: not x, arizona or the folded special for b
      expect(findParty(cmp, 'republican').seats).toBe(2);
      expect(findParty(cmp, 'democrat').seats).toBe(1);
      expect(findParty(cmp, 'republican').votes).toBe(145);
      const delta = state.filteredSeatsDeltaSummary;
      expect(delta.totalSeats).toBe(3); // not florida, ohio or the folded special for c
      expect(findParty(delta, 'democrat').seats).toBe(2);
      expect(seatDelta('democrat')).toBe(1);
      expect(seatDelta('republican')).toBe(-1);
      // Votes: matched current R 133 / D 167 against matched comparison R 145 / D 155.
      expect(findParty(delta, 'republican').votes).toBe(48 + 55 + 30);
      expect(findParty(delta, 'democrat').votes).toBe(52 + 45 + 70);
    });

    it('still displays every seat, including the off-cycle ones, in the chamber tally', () => {
      loadSenate(curFile(), cmpFile());
      const shown = state.filteredSeatsSummary;
      expect(shown.totalSeats).toBe(6); // a, b, c, florida, ohio + the folded special
      expect(findParty(shown, 'republican').seats).toBe(4); // b, florida, ohio, special c
      expect(findParty(shown, 'republican').votes).toBe(48 + 55 + 30 + 100 + 100 + 100);
    });

    it('matching also holds with a filter active, and ignores the comparison specials', () => {
      loadSenate(curFile(), cmpFile());
      state.mapFilters.party = 'republican';
      state.applyMapFilters();
      state.recomputeVoteTotalsForMode();
      // Visible: b, florida, ohio (leading R). Matched: b only.
      expect(state.filteredSeatsComparisonSummary.totalSeats).toBe(1);
      expect(state.filteredSeatsDeltaSummary.totalSeats).toBe(1);
      expect(findParty(state.filteredSeatsComparisonSummary, 'republican').seats).toBe(1);
    });

    it('an uncalled Senate seat is left out of the seat change on both sides', () => {
      const cur = curFile();
      cur.currentSeats[0] = Seat.fromRaw(rawSeat('a', 'democrat', { democrat: 52, republican: 48 }, { c: false }));
      cur.seatsByKey = ElectionData.buildSeatIndex(cur.currentSeats);
      loadSenate(cur, cmpFile());
      // a is uncalled: out of the comparison's R tally (a) and not credited to D now.
      expect(findParty(state.filteredSeatsComparisonSummary, 'republican').seats).toBe(1); // b
      expect(findParty(state.filteredSeatsComparisonSummary, 'democrat').seats).toBe(1); // c
      expect(seatDelta('democrat')).toBe(0); // c held, a not counted
      expect(seatDelta('republican')).toBe(0); // b held, a not counted
      expect(state.filteredSeatsSummary.uncalled).toBe(1);
      // Its votes still count.
      expect(findParty(state.filteredSeatsDeltaSummary, 'democrat').votes).toBe(52 + 45 + 70);
    });

    it('a state with no comparison counterpart contributes nothing to the delta basis', () => {
      loadSenate(curFile([rawSeat('alaska', 'democrat', { democrat: 1 })]), cmpFile());
      expect(state.filteredSeatsDeltaSummary.totalSeats).toBe(3);
      expect(seatDelta('democrat')).toBe(1);
    });

    it('an off-cycle seat carrying a previous result is not matched by state key', () => {
      // x exists in the comparison file, but this x is a special with its own previous result.
      const offCycle = rawSeat('x', 'republican', { republican: 60, democrat: 40 }, { pv: { w: 'democrat', p: [['democrat', 55], ['republican', 45]] } });
      loadSenate(curFile([offCycle]), cmpFile());
      expect(state.filteredSeatsDeltaSummary.totalSeats).toBe(3); // a, b, c only
      expect(state.filteredSeatsComparisonSummary.totalSeats).toBe(3);
      expect(seatDelta('democrat')).toBe(1);
      expect(seatDelta('republican')).toBe(-1);
    });

    it('the House (and every other type) keeps the whole comparison election and no delta basis', () => {
      loadSenate(curFile(), cmpFile(), 'us_house');
      expect(state.filteredSeatsDeltaSummary).toBeNull();
      const cmp = state.filteredSeatsComparisonSummary;
      expect(cmp.totalSeats).toBe(6); // 5 seats + the folded special
      expect(findParty(cmp, 'democrat').seats).toBe(4); // c, x, arizona and the folded special b
    });

    it('has no delta basis without a comparison election', () => {
      loadSenate(curFile(), null);
      expect(state.filteredSeatsDeltaSummary).toBeNull();
      expect(state.filteredSeatsComparisonSummary).toBeNull();
    });
  });

  it('is null when there is no comparison election', () => {
    load(current(), null);
    expect(state.filteredSeatsComparisonSummary).toBeNull();
  });

  it('hasActiveFilters tracks every default', () => {
    expect(state.hasActiveFilters()).toBe(false);
    state.mapFilters.majorityMax = 50;
    expect(state.hasActiveFilters()).toBe(true);
    state.resetFilters();
    state.mapFilters.upcoming = '2026';
    expect(state.hasActiveFilters()).toBe(true);
  });
});

describe('Seat.candidateRows: a party repeated in p (export override)', () => {
  // Alaska 2026 after the export override: Peltola leads as an individual, but the two
  // Republican rows (Sullivan + Heikes) out-total him as a party.
  const alaska = () => rawSeat('Alaska', 'democrat', {}, {
    p: [['democrat', 102962, 'Mary Peltola'], ['republican', 102325, 'Dan Sullivan'],
      ['republican', 4846, 'Gerald Heikes'], ['others', 3600, 'Daniel Sullivan']],
    c: false, pc: 74.1,
  });

  it('keeps the candidate rows in payload order and sums the party votes', () => {
    const seat = Seat.fromRaw(alaska());
    expect(seat.candidateRows).toEqual([
      { party: 'democrat', votes: 102962, name: 'Mary Peltola' },
      { party: 'republican', votes: 102325, name: 'Dan Sullivan' },
      { party: 'republican', votes: 4846, name: 'Gerald Heikes' },
      { party: 'others', votes: 3600, name: 'Daniel Sullivan' },
    ]);
    expect(seat.votes).toEqual({ democrat: 102962, republican: 107171, others: 3600 });
    expect(seat.turnout).toBe(213733);
    expect(seat.candidates.republican).toBe('Dan Sullivan');
  });

  it('majorityStats uses the top two candidates, not the top two party totals', () => {
    const seat = Seat.fromRaw(alaska());
    const { pct, raw } = seat.majorityStats();
    expect(raw).toBe(637); // Peltola 102,962 - Sullivan 102,325, not R 107,171 - D 102,962
    expect(pct).toBeCloseTo((637 / 213733) * 100, 6);
    // Unchanged for a seat without candidate rows.
    expect(Seat.fromRaw(rawSeat('A', 'republican', { republican: 107171, democrat: 102962, others: 3600 })).majorityStats().raw).toBe(4209);
  });

  it('the second-place filter ranks candidates: republican shows Alaska, democrat hides it', () => {
    const open = { party: 'all', region: 'all', secondParty: 'all', majorityMin: 0, majorityMax: 100, gainsOnly: false, upcoming: 'all' };
    const seat = Seat.fromRaw(alaska());
    expect(Seat.matchesPrimaryFilters(seat, null, { ...open, secondParty: 'republican' }, null)).toBe(true);
    expect(Seat.matchesPrimaryFilters(seat, null, { ...open, secondParty: 'democrat' }, null)).toBe(false);
    // The majority filter reads the 0.30% candidate margin, not the 1.97% party margin.
    expect(Seat.matchesPrimaryFilters(seat, null, { ...open, majorityMin: 1 }, null)).toBe(false);
    expect(Seat.matchesPrimaryFilters(seat, null, { ...open, majorityMax: 0.5 }, null)).toBe(true);
  });

  it('survives a clone', () => {
    const seat = Seat.fromRaw(alaska());
    expect(new Seat(seat).candidateRows).toEqual(seat.candidateRows);
  });

  it('is null for a seat with no repeated party', () => {
    expect(Seat.fromRaw(rawSeat('A', 'democrat', { democrat: 2, republican: 1 })).candidateRows).toBeNull();
    expect(Seat.fromRaw({ n: 'A', r: 'r', w: 'democrat', p: [['democrat', 2, 'X'], ['republican', 1, 'Y']] }).candidateRows).toBeNull();
    expect(new Seat({ seat: 'A', winner: 'democrat', votes: { democrat: 1 } }).candidateRows).toBeNull();
  });

  it('an uncalled seat with w democrat fills as provisional Democrat; party totals stay summed', () => {
    const seat = Seat.fromRaw(alaska());
    expect(seat.winner).toBe('democrat');
    expect(seat.fillKind()).toBe('provisional');
    const data = ElectionSummary.summarize([seat], 'all');
    expect(data.uncalled).toBe(1);
    expect(findParty(data, 'democrat').seats ?? 0).toBe(0);
    expect(findParty(data, 'republican').votes).toBe(107171);
    expect(findParty(data, 'democrat').votes).toBe(102962);
  });
});

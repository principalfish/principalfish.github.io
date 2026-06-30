import { describe, it, expect, beforeEach } from 'vitest';
import {
  buildBaselineShares,
  projectSeatUniformSwing,
  dhondtAllocate,
  FPTPPredict,
  AMSPredict,
} from '../features/predict.js';
import { state, Seat } from '../state.js';
import { base64urlEncode } from '../utils.js';

// A bare aggregate config matching the shape #buildAggregateConfig produces: a key plus
// an isMember predicate. Tests build these inline rather than going through the manifest.
const aggregateOf = (key, members) => ({
  key,
  label: key,
  isMember: (rk) => members.includes(String(rk || '').toLowerCase()),
});

describe('buildBaselineShares', () => {
  it('computes per-region party shares against regional turnout', () => {
    const seats = [
      { region: 'london', turnout: 1000, votes: { labour: 600, conservative: 400 } },
      { region: 'london', turnout: 1000, votes: { labour: 400, conservative: 600 } },
    ];
    const shares = buildBaselineShares(seats, ['labour', 'conservative']);
    expect(shares.get('london').get('labour')).toBe(50);
    expect(shares.get('london').get('conservative')).toBe(50);
  });

  it('skips seats with non-positive turnout', () => {
    const seats = [
      { region: 'london', turnout: 0, votes: { labour: 999 } },
      { region: 'london', turnout: 1000, votes: { labour: 700, conservative: 300 } },
    ];
    const shares = buildBaselineShares(seats, ['labour', 'conservative']);
    expect(shares.get('london').get('labour')).toBe(70);
  });

  it('ignores parties outside modelledPartyKeys', () => {
    const seats = [{ region: 'london', turnout: 1000, votes: { labour: 500, green: 500 } }];
    const shares = buildBaselineShares(seats, ['labour']);
    expect(shares.get('london').get('labour')).toBe(50);
    expect(shares.get('london').has('green')).toBe(false);
  });

  it('accumulates a synthetic aggregate row across member regions', () => {
    const seats = [
      { region: 'london', turnout: 1000, votes: { labour: 600, conservative: 400 } },
      { region: 'southeast', turnout: 1000, votes: { labour: 200, conservative: 800 } },
      { region: 'scotland', turnout: 1000, votes: { labour: 100, conservative: 900 } },
    ];
    const agg = aggregateOf('england', ['london', 'southeast']);
    const shares = buildBaselineShares(seats, ['labour', 'conservative'], agg);
    // England = london + southeast only (scotland excluded): labour 800/2000 = 40%.
    expect(shares.get('england').get('labour')).toBe(40);
    expect(shares.get('england').get('conservative')).toBe(60);
    expect(shares.has('scotland')).toBe(true);
  });

  it('trims the smallest non-zero party when rounding pushes a region above 100', () => {
    // 33.6% each rounds to 34/34/34 = 102; the first smallest non-zero party is trimmed by 2.
    const seats = [{ region: 'r', turnout: 1000, votes: { a: 336, b: 336, c: 336 } }];
    const shares = buildBaselineShares(seats, ['a', 'b', 'c']);
    const sum = shares.get('r').get('a') + shares.get('r').get('b') + shares.get('r').get('c');
    expect(sum).toBe(100);
    expect(shares.get('r').get('a')).toBe(32);
  });

  it('keeps trimming across parties when the overshoot exceeds the smallest party', () => {
    // a/b/c round 0.7 -> 1 each, d rounds 98.7 -> 99: sum 102, overshoot 2 > smallest (1),
    // so the single-party trim is not enough — the loop must zero two parties to reach 100.
    const seats = [{ region: 'r', turnout: 1000, votes: { a: 7, b: 7, c: 7, d: 987 } }];
    const shares = buildBaselineShares(seats, ['a', 'b', 'c', 'd']);
    const parties = shares.get('r');
    const sum = parties.get('a') + parties.get('b') + parties.get('c') + parties.get('d');
    expect(sum).toBe(100);
  });
});

describe('projectSeatUniformSwing', () => {
  it('applies a positive swing and lifts the winning party', () => {
    const seat = { seat: 'A', region: 'london', winner: 'conservative', votes: { labour: 450, conservative: 550 }, turnout: 1000 };
    const swings = new Map([['london', new Map([['labour', 20]])]]);
    const projected = projectSeatUniformSwing(seat, swings, ['labour', 'conservative']);
    expect(projected.winner).toBe('labour');
    expect(projected.votes.labour).toBeGreaterThan(projected.votes.conservative);
  });

  it('redistributes the residual share to non-modelled parties pro rata', () => {
    const seat = { seat: 'A', region: 'london', winner: 'labour', votes: { labour: 500, conservative: 300, green: 200 }, turnout: 1000 };
    // labour swing -20 frees 20pp of residual; green is the only non-modelled party so absorbs it all.
    const swings = new Map([['london', new Map([['labour', -20]])]]);
    const projected = projectSeatUniformSwing(seat, swings, ['labour', 'conservative']);
    expect(Math.round(projected.votes.green)).toBe(400);
    expect(projected.winner).toBe('green');
  });

  it('assigns the residual to "others" when no non-modelled party exists', () => {
    const seat = { seat: 'A', region: 'london', winner: 'labour', votes: { labour: 500, conservative: 500 }, turnout: 1000 };
    const swings = new Map([['london', new Map([['labour', -20]])]]);
    const projected = projectSeatUniformSwing(seat, swings, ['labour', 'conservative']);
    expect(Math.round(projected.votes.others)).toBe(200);
    expect(projected.winner).toBe('conservative');
  });

  it('falls back to the aggregate swing for a sub-region with no direct entry', () => {
    const seat = { seat: 'A', region: 'london', winner: 'conservative', votes: { labour: 400, conservative: 600 }, turnout: 1000 };
    const swings = new Map([['england', new Map([['labour', 30]])]]);
    const agg = aggregateOf('england', ['london']);
    const projected = projectSeatUniformSwing(seat, swings, ['labour', 'conservative'], agg);
    // labour 40% + aggregate 30 = 70% -> labour now leads.
    expect(projected.winner).toBe('labour');
  });

  it('returns an unaltered clone for a zero-turnout seat', () => {
    const seat = { seat: 'A', region: 'london', winner: 'labour', votes: {}, turnout: 0 };
    const projected = projectSeatUniformSwing(seat, new Map(), ['labour']);
    expect(projected.winner).toBe('labour');
    expect(projected.turnout).toBe(0);
  });

  it('clamps the residual to zero and re-scales when modelled shares exceed 100%', () => {
    const seat = { seat: 'A', region: 'london', winner: 'labour', votes: { labour: 500, conservative: 500 }, turnout: 1000 };
    // labour 50% + 60 swing = 110%, conservative 50% -> tracked sum 160% -> otherShare clamped to 0.
    const swings = new Map([['london', new Map([['labour', 60]])]]);
    const projected = projectSeatUniformSwing(seat, swings, ['labour', 'conservative']);
    expect(projected.votes.others).toBeUndefined();
    // Re-scaled back down to turnout; party shares re-normalise to 100%.
    expect(projected.turnout).toBeCloseTo(1000, 6);
    expect(Seat.voteSharePct(projected, 'labour') + Seat.voteSharePct(projected, 'conservative')).toBeCloseTo(100, 6);
    expect(projected.winner).toBe('labour');
  });

  it('preserves turnout (projected votes sum to baseSeat.turnout)', () => {
    const seat = { seat: 'A', region: 'london', winner: 'labour', votes: { labour: 400, conservative: 400, libdems: 200 }, turnout: 1000 };
    const swings = new Map([['london', new Map([['labour', 10], ['conservative', -10]])]]);
    const projected = projectSeatUniformSwing(seat, swings, ['labour', 'conservative']);
    expect(projected.turnout).toBeCloseTo(1000, 6);
  });
});

describe('dhondtAllocate', () => {
  it('allocates seats by descending quotient', () => {
    const votes = new Map([['a', 100], ['b', 80], ['c', 30]]);
    expect(dhondtAllocate(votes, 3)).toEqual(['a', 'b', 'a']);
  });

  it('deducts constituency wins from the divisor so list seats top up', () => {
    const votes = new Map([['a', 100], ['b', 80], ['c', 30]]);
    const constWins = new Map([['a', 2]]);
    expect(dhondtAllocate(votes, 3, constWins)).toEqual(['b', 'b', 'a']);
  });

  it('skips parties with non-positive votes', () => {
    const votes = new Map([['a', 0], ['b', 50]]);
    expect(dhondtAllocate(votes, 2)).toEqual(['b', 'b']);
  });

  it('returns an empty array when no party has positive votes', () => {
    // The all-skipped path: bestParty stays null every iteration, so nothing is pushed.
    // #allocateListSeats maps these missing winners to Seats with winner: null.
    expect(dhondtAllocate(new Map([['a', 0], ['b', -5]]), 2)).toEqual([]);
  });

  it('returns an empty array when zero seats are requested', () => {
    expect(dhondtAllocate(new Map([['a', 100]]), 0)).toEqual([]);
  });
});

describe('FPTPPredict serialize / deserialize', () => {
  const config = {
    modelledPartyKeys: ['labour', 'conservative'],
    gridSections: [{ id: 'gb', columnKeys: ['labour', 'conservative'], regionKeys: ['london'] }],
  };

  beforeEach(() => {
    // The model reads its baseline + region labels from the global state singleton.
    state.comparisonElectionData = {
      currentSeats: [
        { seat: 'A', region: 'london', winner: 'labour', votes: { labour: 600, conservative: 400 }, turnout: 1000 },
      ],
    };
    state.currentRegionLabelsByKey = new Map([['london', 'London']]);
  });

  it('round-trips an entered override that differs from baseline', () => {
    const model = new FPTPPredict(2029, config);
    model.setShare('london', 'labour', 70);
    const payload = model.serialize();
    expect(payload).not.toBe('');

    const restored = new FPTPPredict(2029, config);
    restored.deserialize(payload);
    expect(restored.getShare('london', 'labour')).toBe(70);
    // An untouched party still reads through to its baseline.
    expect(restored.getShare('london', 'conservative')).toBe(40);
  });

  it('serializes to an empty payload when no input differs from baseline', () => {
    const model = new FPTPPredict(2029, config);
    expect(model.serialize()).toBe('');
  });

  it('clearShare reverts a cell to baseline and prunes the empty region map', () => {
    const model = new FPTPPredict(2029, config);
    model.setShare('london', 'labour', 70);
    expect(model.getShare('london', 'labour')).toBe(70);

    model.clearShare('london', 'labour');
    // Baseline labour share is 600/1000 = 60; getShare must fall back to it, not to 0.
    expect(model.getShare('london', 'labour')).toBe(60);
    expect(model.currentInputMap().has('london')).toBe(false);
  });

  it('deserialize tolerates a payload whose `r` is not an array', () => {
    const payload = base64urlEncode(JSON.stringify({ e: 0, r: 'not-an-array' }));
    const model = new FPTPPredict(2029, config);
    expect(() => model.deserialize(payload)).not.toThrow();
    expect(model.currentInputMap().size).toBe(0);
  });

  it('ignores overrides for regions outside the grid', () => {
    const model = new FPTPPredict(2029, config);
    model.setShare('london', 'labour', 65);
    const payload = model.serialize();

    state.currentRegionLabelsByKey = new Map([['london', 'London']]);
    const restored = new FPTPPredict(2029, { ...config, gridSections: [{ id: 'gb', columnKeys: ['labour', 'conservative'], regionKeys: [] }] });
    restored.deserialize(payload);
    // 'london' is no longer a valid region in this config, so the override is dropped.
    expect(restored.currentInputMap().has('london')).toBe(false);
  });
});

describe('PredictModel.setAggregateExpanded', () => {
  const config = {
    modelledPartyKeys: ['labour', 'conservative'],
    aggregate: { key: 'england', label: 'England', excludeRegions: ['scotland'] },
    gridSections: [{ id: 'gb', columnKeys: ['labour', 'conservative'], containsAggregate: true, extraRegionKeys: ['scotland'] }],
  };

  beforeEach(() => {
    state.comparisonElectionData = { currentSeats: [] };
    state.currentRegionLabelsByKey = new Map([
      ['northeastengland', 'North East'],
      ['northwestengland', 'North West'],
      ['scotland', 'Scotland'],
    ]);
  });

  it('propagates the aggregate input onto empty member sub-regions and clears the aggregate', () => {
    const model = new FPTPPredict(2029, config);
    model.setShare('england', 'labour', 55);
    model.setAggregateExpanded(true);

    const input = model.currentInputMap();
    expect(input.has('england')).toBe(false);
    expect(input.get('northeastengland').get('labour')).toBe(55);
    expect(input.get('northwestengland').get('labour')).toBe(55);
    // 'scotland' is excluded from the aggregate, so it is not seeded.
    expect(input.has('scotland')).toBe(false);
  });

  it('drops member sub-region inputs on collapse', () => {
    const model = new FPTPPredict(2029, config);
    model.aggregateExpanded = true;
    model.setShare('northeastengland', 'labour', 40);
    model.setAggregateExpanded(false);
    expect(model.currentInputMap().has('northeastengland')).toBe(false);
  });
});

describe('AMSPredict.project (two-pass constituency + D\'Hondt list)', () => {
  const config = {
    modelledPartyKeys: ['snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform'],
    aggregate: { key: 'scotland', label: 'Scotland', excludeRegions: [] },
    tabs: [{ key: 'constituency', label: 'Constituency' }, { key: 'list', label: 'List' }],
    gridSections: [{ id: 'holyrood', columnKeys: ['snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform'], containsAggregate: true }],
  };

  // 3 constituency seats + 7 list seats, all in Glasgow. List seats duplicate the regional
  // list total across every seat (as the real data does).
  const listVotes = { snp: 5000, labour: 4000, conservative: 2000, scottishgreens: 1500, libdems: 1000 };
  const buildSeats = () => [
    new Seat({ seat: 'Glasgow A', region: 'glasgow', winner: 'snp', votes: { snp: 600, labour: 400 } }),
    new Seat({ seat: 'Glasgow B', region: 'glasgow', winner: 'snp', votes: { snp: 550, labour: 450 } }),
    new Seat({ seat: 'Glasgow C', region: 'glasgow', winner: 'labour', votes: { labour: 600, snp: 400 } }),
    ...Array.from({ length: 7 }, (_, i) => new Seat({ seat: `Glasgow List ${i + 1}`, region: 'glasgow', winner: 'snp', votes: { ...listVotes } })),
  ];

  beforeEach(() => {
    state.comparisonElectionData = { currentSeats: buildSeats() };
    state.currentRegionLabelsByKey = new Map([['glasgow', 'Glasgow']]);
  });

  it('returns the untouched baseline when no input differs (zero-swing short-circuit)', () => {
    const model = new AMSPredict(2026, config);
    const projected = model.project();
    expect(projected).toHaveLength(10);
    expect(projected.map((s) => s.winner)).toEqual(state.comparisonElectionData.currentSeats.map((s) => s.winner));
  });

  it('runs both passes and preserves the seat split when an input drives a swing', () => {
    const model = new AMSPredict(2026, config);
    // A constituency input bypasses the short-circuit and exercises the FPTP pass. Collapsed,
    // input is entered on the 'scotland' aggregate and falls through to its member regions.
    model.setActiveTab('constituency');
    model.setShare('scotland', 'labour', 70);
    const projected = model.project();

    const consts = projected.filter((s) => !Seat.isList(s));
    const lists = projected.filter((s) => Seat.isList(s));
    expect(consts).toHaveLength(3);
    expect(lists).toHaveLength(7);
    // Labour's lifted constituency share should now carry the formerly-SNP seats.
    expect(consts.every((s) => s.winner === 'labour')).toBe(true);
    // The D'Hondt list pass still assigns a winner to every list seat.
    expect(lists.every((s) => s.winner)).toBe(true);
  });

  it('does not apply constituency swings to list votes when the list ballot is untouched', () => {
    const model = new AMSPredict(2026, config);
    // Big labour swing on the constituency ballot only; the list ballot is left at baseline.
    model.setActiveTab('constituency');
    model.setShare('scotland', 'labour', 70);
    const lists = model.project().filter((s) => Seat.isList(s));

    // List votes must stay at their baseline distribution (SNP 5000 > Labour 4000). Under the
    // old const->list swing fallback the labour swing would have leaked in and flipped this.
    expect(lists.every((s) => s.votes.snp > s.votes.labour)).toBe(true);
  });

  it('folds list seats to "others" when a region has no list votes (D\'Hondt returns nothing)', () => {
    // Edge: list seats with no votes -> #allocateListSeats hits dhondtAllocate([]) -> winners[idx]
    // is undefined -> `winners[idx] || null`, and the Seat constructor then resolves null to 'others'.
    state.comparisonElectionData = { currentSeats: [
      new Seat({ seat: 'Glasgow A', region: 'glasgow', winner: 'snp', votes: { snp: 600, labour: 400 } }),
      new Seat({ seat: 'Glasgow List 1', region: 'glasgow', winner: 'snp', votes: {} }),
      new Seat({ seat: 'Glasgow List 2', region: 'glasgow', winner: 'snp', votes: {} }),
    ] };
    const model = new AMSPredict(2026, config);
    model.setActiveTab('constituency');
    model.setShare('scotland', 'labour', 70); // bypass the zero-swing short-circuit
    const lists = model.project().filter((s) => Seat.isList(s));
    expect(lists).toHaveLength(2);
    expect(lists.every((s) => s.winner === 'others')).toBe(true);
  });
});

describe('AMSPredict.validate (checks every ballot, not just the active tab)', () => {
  const config = {
    modelledPartyKeys: ['snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform'],
    aggregate: { key: 'scotland', label: 'Scotland', excludeRegions: [] },
    tabs: [{ key: 'constituency', label: 'Constituency' }, { key: 'list', label: 'List' }],
    gridSections: [{ id: 'holyrood', columnKeys: ['snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform'], containsAggregate: true }],
  };

  beforeEach(() => {
    state.comparisonElectionData = {
      currentSeats: [
        new Seat({ seat: 'Glasgow A', region: 'glasgow', winner: 'snp', votes: { snp: 600, labour: 400 } }),
        new Seat({ seat: 'Glasgow List 1', region: 'glasgow', winner: 'snp', votes: { snp: 5000, labour: 4000 } }),
      ],
    };
    state.currentRegionLabelsByKey = new Map([['glasgow', 'Glasgow']]);
  });

  it('flags an over-100% row entered on the inactive (list) ballot', () => {
    const model = new AMSPredict(2026, config);
    model.setActiveTab('list');
    model.setShare('scotland', 'snp', 60);
    model.setShare('scotland', 'labour', 50); // list sum = 110%
    // Switch back to constituency: the offending row is now on the inactive tab.
    model.setActiveTab('constituency');

    const invalid = model.validate();
    expect(invalid.length).toBeGreaterThan(0);
    // The offending ballot's label is appended so the Submit alert names the tab.
    expect(invalid.some((r) => /\(List\)/.test(r.regionLabel))).toBe(true);
  });
});

describe('AMSPredict serialize / deserialize', () => {
  const config = {
    modelledPartyKeys: ['snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform'],
    aggregate: { key: 'scotland', label: 'Scotland', excludeRegions: [] },
    tabs: [{ key: 'constituency', label: 'Constituency' }, { key: 'list', label: 'List' }],
    gridSections: [{ id: 'holyrood', columnKeys: ['snp', 'labour', 'conservative', 'libdems', 'scottishgreens', 'reform'], containsAggregate: true }],
  };

  beforeEach(() => {
    state.comparisonElectionData = {
      currentSeats: [
        new Seat({ seat: 'Glasgow A', region: 'glasgow', winner: 'snp', votes: { snp: 600, labour: 400 } }),
        new Seat({ seat: 'Glasgow List 1', region: 'glasgow', winner: 'snp', votes: { snp: 5000, labour: 4000, conservative: 2000 } }),
      ],
    };
    state.currentRegionLabelsByKey = new Map([['glasgow', 'Glasgow']]);
  });

  it('round-trips constituency and list overrides into the correct ballots', () => {
    const model = new AMSPredict(2026, config);
    model.setActiveTab('constituency');
    model.setShare('scotland', 'labour', 60);
    model.setActiveTab('list');
    model.setShare('scotland', 'snp', 45);
    const payload = model.serialize();
    expect(payload).not.toBe('');

    const restored = new AMSPredict(2026, config);
    restored.deserialize(payload);
    restored.setActiveTab('constituency');
    expect(restored.getShare('scotland', 'labour')).toBe(60);
    restored.setActiveTab('list');
    expect(restored.getShare('scotland', 'snp')).toBe(45);
  });

  it('ignores a payload missing the AMS discriminator (h !== 1)', () => {
    // An FPTP-shaped payload (no `h`, 3-tuple overrides) must not load into an AMS model.
    const fptpPayload = base64urlEncode(JSON.stringify({ e: 0, r: [['scotland', 'labour', 60]] }));
    const model = new AMSPredict(2026, config);
    model.deserialize(fptpPayload);
    expect(model.inputMaps().every((m) => m.size === 0)).toBe(true);
  });
});

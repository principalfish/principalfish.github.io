import { describe, it, expect, beforeEach } from 'vitest';
import {
  buildBaselineShares,
  projectSeatUniformSwing,
  dhondtAllocate,
  WestminsterPredict,
  HolyroodPredict,
} from '../scripts/predict.js';
import { state, Seat } from '../scripts/state.js';
import { base64urlEncode } from '../scripts/utils.js';

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
});

describe('WestminsterPredict serialize / deserialize', () => {
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
    const model = new WestminsterPredict(2029, config);
    model.setShare('london', 'labour', 70);
    const payload = model.serialize();
    expect(payload).not.toBe('');

    const restored = new WestminsterPredict(2029, config);
    restored.deserialize(payload);
    expect(restored.getShare('london', 'labour')).toBe(70);
    // An untouched party still reads through to its baseline.
    expect(restored.getShare('london', 'conservative')).toBe(40);
  });

  it('serializes to an empty payload when no input differs from baseline', () => {
    const model = new WestminsterPredict(2029, config);
    expect(model.serialize()).toBe('');
  });

  it('ignores overrides for regions outside the grid', () => {
    const model = new WestminsterPredict(2029, config);
    model.setShare('london', 'labour', 65);
    const payload = model.serialize();

    state.currentRegionLabelsByKey = new Map([['london', 'London']]);
    const restored = new WestminsterPredict(2029, { ...config, gridSections: [{ id: 'gb', columnKeys: ['labour', 'conservative'], regionKeys: [] }] });
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
    const model = new WestminsterPredict(2029, config);
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
    const model = new WestminsterPredict(2029, config);
    model.aggregateExpanded = true;
    model.setShare('northeastengland', 'labour', 40);
    model.setAggregateExpanded(false);
    expect(model.currentInputMap().has('northeastengland')).toBe(false);
  });
});

describe('HolyroodPredict.project (two-pass constituency + D\'Hondt list)', () => {
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
    const model = new HolyroodPredict(2026, config);
    const projected = model.project();
    expect(projected).toHaveLength(10);
    expect(projected.map((s) => s.winner)).toEqual(state.comparisonElectionData.currentSeats.map((s) => s.winner));
  });

  it('runs both passes and preserves the seat split when an input drives a swing', () => {
    const model = new HolyroodPredict(2026, config);
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
});

describe('HolyroodPredict serialize / deserialize', () => {
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
    const model = new HolyroodPredict(2026, config);
    model.setActiveTab('constituency');
    model.setShare('scotland', 'labour', 60);
    model.setActiveTab('list');
    model.setShare('scotland', 'snp', 45);
    const payload = model.serialize();
    expect(payload).not.toBe('');

    const restored = new HolyroodPredict(2026, config);
    restored.deserialize(payload);
    restored.setActiveTab('constituency');
    expect(restored.getShare('scotland', 'labour')).toBe(60);
    restored.setActiveTab('list');
    expect(restored.getShare('scotland', 'snp')).toBe(45);
  });

  it('ignores a payload missing the Holyrood discriminator (h !== 1)', () => {
    // A Westminster-shaped payload (no `h`, 3-tuple overrides) must not load into Holyrood.
    const wmPayload = base64urlEncode(JSON.stringify({ e: 0, r: [['scotland', 'labour', 60]] }));
    const model = new HolyroodPredict(2026, config);
    model.deserialize(wmPayload);
    expect(model.constInput.size).toBe(0);
    expect(model.listInput.size).toBe(0);
  });
});

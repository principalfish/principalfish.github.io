import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { parsePollTrackerData } from '../features/polltracker.js';
import { manifest } from '../state.js';
import { DEFAULT_PARTY_COLOUR } from '../utils.js';

describe('parsePollTrackerData', () => {
  // Fully specify every key so init()'s Object.assign + re-hydrate leaves no leakage from
  // (or into) another test file's manifest configuration.
  const emptyManifest = { parties: [], mapModes: {}, elections: [], files: {}, parliamentFeatures: {} };
  beforeEach(() => {
    manifest.init({ ...emptyManifest, parties: [
      { id: 1, key: 'lab', name: 'Labour', colour: '#E4003B' },
      { id: 2, key: 'con', name: 'Con', colour: '#0087DC' },
    ] });
  });
  afterEach(() => { manifest.init({ ...emptyManifest }); });

  it('returns empty structures for empty input', () => {
    const { timeline, seriesByParty } = parsePollTrackerData([]);
    expect(timeline).toEqual([]);
    expect(seriesByParty.size).toBe(0);
  });

  it("breaks Westminster ranges and chances on identity changes, even with equal central values", () => {
    const point = (day, segment) => ({
      as_of_date: `2026-06-0${day}`, segment_id: segment, hung: 0.5,
      parties: { 1: { s: 300, v: 40, sl: 200, sh: 400, pm: 0.5 } },
    });
    const parsed = parsePollTrackerData([
      point(1, "config-a/kernel-a"), point(3, "config-a/kernel-a"),
      point(4, "config-b/kernel-a"), point(5, "config-b/kernel-b"),
    ]);
    expect(parsed.timeline.filter((entry) => entry.boundary).map((entry) => entry.dateKey))
      .toEqual(["2026-06-04", "2026-06-05"]);
    expect(parsed.seriesByParty.get("1").seats).toEqual([300, 300, 300, null, 300, null, 300]);
    expect(parsed.seriesByParty.get("1").seatLo).toEqual([200, 200, 200, null, 200, null, 200]);
    expect(parsed.hung).toEqual([0.5, 0.5, 0.5, null, 0.5, null, 0.5]);
  });

  it.each([undefined, null, "", " ", 42])(
    "keeps uncertainty whose segment identity is unknown (%j) continuous through gaps", (segment) => {
      const point = (day, sl) => ({
        as_of_date: `2026-06-0${day}`, segment_id: segment, hung: 1,
        parties: { 1: { s: 300, v: 40, sl, sh: 400 } },
      });
      const parsed = parsePollTrackerData([point(1, 200), point(3, 210)]);
      expect(parsed.timeline.some((entry) => entry.boundary)).toBe(false);
      expect(parsed.seriesByParty.get("1").seatLo).toEqual([200, 200, 210]);
      expect(parsed.hung).toEqual([1, 1, 1]);
    },
  );

  it("treats legacy rows with uncertainty but no segment ids as one continuous segment", () => {
    // The shape of history written before segment ids existed: every run has hung, none an id.
    const parsed = parsePollTrackerData([1, 2, 5, 9].map((day, index) => ({
      as_of_date: `2026-06-0${day}`, hung: 0.6,
      parties: { 1: { s: 300 + index, v: 40, sl: 200 + index, sh: 400, vl: 35, vh: 45, pm: 0.2 } },
    })));
    expect(parsed.timeline.some((entry) => entry.boundary)).toBe(false);
    expect(parsed.timeline).toHaveLength(9);
    expect(parsed.seriesByParty.get("1").seats).toEqual([300, 301, 301, 301, 302, 302, 302, 302, 303]);
    expect(parsed.seriesByParty.get("1").seatLo).toEqual([200, 201, 201, 201, 202, 202, 202, 202, 203]);
    expect(parsed.seriesByParty.get("1").pMaj).toEqual(Array(9).fill(0.2));
    expect(parsed.hung).toEqual(Array(9).fill(0.6));
  });

  // Each sequence is one trends.py can export (with differing central values): it keeps the
  // first known id after unknown ones and every known change (data/tests/test_model_trends.py
  // cross-checks this rule).
  it.each([
    ["known then unknown", ["a", undefined], []],
    ["unknown then known", [undefined, "a"], []],
    ["known, unknown, same known", ["a", undefined, "a"], []],
    ["known, unknown, different known", ["a", undefined, "b"], ["2026-06-03"]],
    ["unknown, known, different known", [undefined, "a", "b"], ["2026-06-03"]],
    ["known, different known, unknown", ["a", "b", undefined], ["2026-06-02"]],
  ])("breaks mixed known/unknown segment ids only on a known change (%s)", (_, segments, breaks) => {
    const parsed = parsePollTrackerData(segments.map((segment, index) => ({
      as_of_date: `2026-06-0${index + 1}`, segment_id: segment, hung: 0.5,
      parties: { 1: { s: 300, v: 40, sl: 200, sh: 400 } },
    })));
    expect(parsed.timeline.filter((entry) => entry.boundary).map((entry) => entry.dateKey))
      .toEqual(breaks);
  });

  it.each([undefined, "a"])("breaks where uncertainty appears or disappears (segment %j)", (segment) => {
    const parsed = parsePollTrackerData([
      { as_of_date: "2026-06-01", segment_id: segment, parties: { 1: { s: 300, v: 40 } } },
      { as_of_date: "2026-06-02", segment_id: segment, hung: 0.5, parties: { 1: { s: 300, v: 40, sl: 200, sh: 400 } } },
      { as_of_date: "2026-06-04", segment_id: segment, hung: 0.5, parties: { 1: { s: 310, v: 41, sl: 210, sh: 410 } } },
      { as_of_date: "2026-06-05", segment_id: segment, parties: { 1: { s: 310, v: 41 } } },
    ]);
    expect(parsed.timeline.filter((entry) => entry.boundary).map((entry) => entry.dateKey))
      .toEqual(["2026-06-02", "2026-06-05"]);
    expect(parsed.seriesByParty.get("1").seats).toEqual([300, null, 300, 300, 310, null, 310]);
    expect(parsed.seriesByParty.get("1").seatLo).toEqual([null, null, 200, 200, 210, null, null]);
    expect(parsed.hung).toEqual([null, null, 0.5, 0.5, 0.5, null, null]);
  });

  it("breaks once after a central-only election-day point, then keeps the uncertain runs continuous", () => {
    // trends.py's shape: the unpolled election result has no hung or segment_id key.
    const uncertain = (date, s) => ({
      as_of_date: date, segment_id: "a", hung: 0.5, parties: { 1: { s, v: 40, sl: s - 100, sh: s + 100 } },
    });
    const parsed = parsePollTrackerData([
      { as_of_date: "2024-07-05", parties: { 1: { s: 411, v: 34 } } },
      uncertain("2024-07-08", 300),
      uncertain("2024-07-10", 310),
    ]);
    expect(parsed.timeline.filter((entry) => entry.boundary).map((entry) => entry.dateKey))
      .toEqual(["2024-07-08"]);
    expect(parsed.seriesByParty.get("1").seats).toEqual([411, 411, 411, null, 300, 300, 310]);
    expect(parsed.seriesByParty.get("1").seatLo).toEqual([null, null, null, null, 200, 200, 210]);
    expect(parsed.hung).toEqual([null, null, null, null, 0.5, 0.5, 0.5]);
  });

  it("keeps each run's uniform-swing seats beside its simulated mean, and the election-day result", () => {
    const uncertain = (date, s, sm) => ({
      as_of_date: date, segment_id: "a", hung: 0.5,
      parties: { 1: { s, v: 40, sl: s - 100, sh: s + 100, sm } },
    });
    const parsed = parsePollTrackerData([
      { as_of_date: "2024-07-05", parties: { 1: { s: 411, v: 34 } } },
      uncertain("2024-07-08", 300, 297.4),
      uncertain("2024-07-10", 310, 305.0),
    ]);
    const labour = parsed.seriesByParty.get("1");
    // Both lines are kept: the view draws `seats` unticked and the mean where it has one ticked.
    expect(labour.seats).toEqual([411, 411, 411, null, 300, 300, 310]);
    expect(labour.seatMean).toEqual([null, null, null, null, 297.4, 297.4, 305]);
    // The band is still the stored 5th-95th percentile range.
    expect(labour.seatLo).toEqual([null, null, null, null, 200, 200, 210]);
    expect(labour.latestSeats).toBe(310);
  });

  it("carries a mean forward with its seats when a run lacks the party", () => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "a", as_of_date: "2024-01-01", hung: 0.5, parties: { 1: { s: 300, v: 40, sm: 297.4 } } },
      { segment_id: "a", as_of_date: "2024-01-02", hung: 0.5, parties: { 2: { s: 10, v: 5, sm: 9.6 } } },
      { segment_id: "a", as_of_date: "2024-01-03", hung: 0.5, parties: { 1: { s: 310, v: 41 } } },
    ]);
    // The 2nd carries Labour's seats and mean together; the 3rd's run has no mean.
    expect(seriesByParty.get("1").seats).toEqual([300, 300, 310]);
    expect(seriesByParty.get("1").seatMean).toEqual([297.4, 297.4, null]);
    expect(seriesByParty.get("2").seatMean).toEqual([null, 9.6, 9.6]);
  });

  it.each([[undefined], [null], [-1], ["297"], [NaN]])(
    "falls back to the central seats for a run whose mean is %s", (sm) => {
      const { seriesByParty } = parsePollTrackerData([
        { segment_id: "a", as_of_date: "2024-01-01", hung: 0.5, parties: { 1: { s: 300, v: 40, sm } } },
      ]);
      expect(seriesByParty.get("1").seats).toEqual([300]);
      expect(seriesByParty.get("1").seatMean).toEqual([null]);
    },
  );

  it("skips every break with segmentBreaks off, so the series carry through a changed run", () => {
    const parsed = parsePollTrackerData([
      { as_of_date: "2026-06-01", parties: { 1: { s: 300, v: 40 } } },
      { as_of_date: "2026-06-03", segment_id: "a", hung: 0.5, parties: { 2: { s: 10, v: 5, sl: 5, sh: 15 } } },
      { as_of_date: "2026-06-05", segment_id: "b", hung: 0.5, parties: { 1: { s: 310, v: 41, sl: 210, sh: 410 } } },
    ], { segmentBreaks: false });
    expect(parsed.timeline.map((entry) => entry.dateKey))
      .toEqual(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]);
    const party = parsed.seriesByParty.get("1");
    expect(party.seats).toEqual([300, 300, 300, 300, 310]);
    expect(party.votePct).toEqual([40, 40, 40, 40, 41]);
    expect(party.latestSeats).toBe(310);
  });

  it("keeps central-only Westminster history continuous without any segment metadata", () => {
    const parsed = parsePollTrackerData([
      { as_of_date: "2026-06-01", parties: { 1: { s: 300, v: 40 } } },
      { as_of_date: "2026-06-03", parties: { 1: { s: 310, v: 41 } } },
    ]);
    expect(parsed.seriesByParty.get("1").seats).toEqual([300, 300, 310]);
    expect(parsed.timeline.some((entry) => entry.boundary)).toBe(false);
  });

  it('does not expand a single-date timeline', () => {
    const { timeline, seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: '2024-01-01', parties: { 1: { s: 300, v: 40 } } },
    ]);
    expect(timeline.map((t) => t.dateKey)).toEqual(['2024-01-01']);
    expect(timeline[0].dateValue).toBeInstanceOf(Date);
    expect(seriesByParty.get('1')).toMatchObject({
      partyName: 'Labour', colour: '#E4003B', seats: [300], votePct: [40], latestSeats: 300,
    });
  });

  it('fills every calendar day between the first and last reading and carries values forward', () => {
    const { timeline, seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: '2024-01-01', parties: { 1: { s: 300, v: 40 } } },
      { segment_id: "fixture-model", as_of_date: '2024-01-04', parties: { 1: { s: 320, v: 42 } } },
    ]);
    expect(timeline.map((t) => t.dateKey)).toEqual(['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04']);
    // Reading on the 1st, carried across the 2nd/3rd, new reading on the 4th.
    expect(seriesByParty.get('1').seats).toEqual([300, 300, 300, 320]);
    expect(seriesByParty.get('1').latestSeats).toBe(320);
  });

  it('leaves nulls before a party\'s first reading', () => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: '2024-01-01', parties: { 1: { s: 300, v: 40 } } },
      { segment_id: "fixture-model", as_of_date: '2024-01-04', parties: { 1: { s: 320, v: 42 }, 2: { s: 100, v: 20 } } },
    ]);
    expect(seriesByParty.get('2').seats).toEqual([null, null, null, 100]);
    expect(seriesByParty.get('2').votePct).toEqual([null, null, null, 20]);
  });

  it('falls back to the party id and default colour for parties missing from the manifest', () => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: '2024-01-01', parties: { 99: { s: 5, v: 1 } } },
    ]);
    expect(seriesByParty.get('99')).toMatchObject({ partyName: '99', colour: DEFAULT_PARTY_COLOUR });
  });

  it('drops party readings with non-finite seats or vote share', () => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: '2024-01-01', parties: { 1: { s: NaN, v: 40 }, 2: { s: 200, v: 30 } } },
    ]);
    expect(seriesByParty.has('1')).toBe(false);
    expect(seriesByParty.get('2').seats).toEqual([200]);
  });

  it("carries optional data across calendar gaps, breaking on an explicit ordinary run", () => {
    const { seriesByParty, hung } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: "2024-01-01", parties: { 1: { s: 300, v: 40 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-03", hung: 0.7, parties: { 1: { s: 310, v: 41, vl: 35, vh: 45, pm: 0.3, pl: 0.9 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-05", parties: { 1: { s: 320, v: 42 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-07", hung: 0, parties: { 1: { s: 400, v: 48, vl: 40, vh: 55, pm: 1, pl: 1 } } },
    ]);
    // Uncertainty appears on the 3rd and 7th and disappears on the 5th: a break before each.
    expect(seriesByParty.get("1").seats).toEqual([300, 300, null, 310, 310, null, 320, 320, null, 400]);
    expect(seriesByParty.get("1").voteLo).toEqual([null, null, null, 35, 35, null, null, null, null, 40]);
    expect(seriesByParty.get("1").voteHi).toEqual([null, null, null, 45, 45, null, null, null, null, 55]);
    expect(seriesByParty.get("1").pMaj).toEqual([null, null, null, 0.3, 0.3, null, null, null, null, 1]);
    expect(seriesByParty.get("1").pLargest).toEqual([null, null, null, 0.9, 0.9, null, null, null, null, 1]);
    expect(hung).toEqual([null, null, null, 0.7, 0.7, null, null, null, null, 0]);
  });

  it("decodes compact omitted zero odds only when the run and party have uncertainty", () => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: "2024-01-01", hung: 0, parties: {
        1: { s: 0, v: 1, vl: 0, vh: 2 }, 2: { s: 20, v: 10 },
      } },
      { segment_id: "fixture-model", as_of_date: "2024-01-02", parties: { 1: { s: 0, v: 1, vl: 0, vh: 2 } } },
    ]);
    // The second run has no uncertainty, so a boundary point precedes it.
    expect(seriesByParty.get("1").pMaj).toEqual([0, null, null]);
    expect(seriesByParty.get("1").pLargest).toEqual([0, null, null]);
    expect(seriesByParty.get("2").pMaj).toEqual([null, null, null]);
  });

  it("clears a missing party's optional data even if its central value carries forward", () => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: "2024-01-01", hung: 1, parties: { 1: { s: 10, v: 4, vl: 1, vh: 8 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-02", hung: 1, parties: { 2: { s: 20, v: 10, vl: 5, vh: 15 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-04", hung: 1, parties: {} },
    ]);
    expect(seriesByParty.get("1").seats).toEqual([10, 10, 10, 10]);
    expect(seriesByParty.get("1").voteLo).toEqual([1, null, null, null]);
    expect(seriesByParty.get("2").pMaj).toEqual([null, 0, 0, null]);
  });

  it("carries seat bounds through calendar gaps and clears them on legacy or missing-party runs", () => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: "2024-01-01", parties: { 1: { s: 300, v: 40 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-02", hung: 0.5, parties: { 1: { s: 310, v: 41, sl: 220, sh: 376, vl: 30, vh: 50 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-04", hung: 0.5, parties: { 1: { s: 320, v: 42, vl: 31, vh: 51 } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-05", hung: 0.5, parties: { 2: { s: 0, v: 1, sl: 0, sh: 2, vl: 0, vh: 3 } } },
    ]);
    // Uncertainty appears on the 2nd, so a boundary point precedes it.
    expect(seriesByParty.get("1").seatLo).toEqual([null, null, 220, 220, null, null]);
    expect(seriesByParty.get("1").seatHi).toEqual([null, null, 376, 376, null, null]);
    expect(seriesByParty.get("1").voteLo).toEqual([null, null, 30, 30, 31, null]);
    expect(seriesByParty.get("2").seatLo).toEqual([null, null, null, null, null, 0]);
    expect(seriesByParty.get("2").seatHi).toEqual([null, null, null, null, null, 2]);
  });

  it.each([{ sl: -1, sh: 2 }, { sl: 2, sh: 1 }, { sl: 0, sh: 2.5 }, { sl: false, sh: 2 }, { sl: 0 }])(
    "ignores malformed seat bounds %j without losing valid vote bands", (fields) => {
      const { seriesByParty } = parsePollTrackerData([
        { segment_id: "fixture-model", as_of_date: "2024-01-01", hung: 1, parties: { 1: { s: 300, v: 40, vl: 30, vh: 50, ...fields } } },
      ]);
      expect(seriesByParty.get("1").seatLo).toEqual([null]);
      expect(seriesByParty.get("1").seatHi).toEqual([null]);
      expect(seriesByParty.get("1").voteLo).toEqual([30]);
    },
  );

  it.each([
    { vl: 10, vh: 5 }, { vl: -1, vh: 5 }, { vl: 0, vh: 101 },
    { vl: "0", vh: 5 }, { vl: NaN, vh: 5 }, { vl: 0 },
  ])("ignores invalid optional vote ranges %j", (fields) => {
    const { seriesByParty } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: "2024-01-01", hung: 0.5, parties: { 1: { s: 300, v: 40, ...fields } } },
    ]);
    expect(seriesByParty.get("1").voteLo).toEqual([null]);
    expect(seriesByParty.get("1").voteHi).toEqual([null]);
    expect(seriesByParty.get("1").seats).toEqual([300]);
  });

  it.each([null, "0", -0.1, 1.1, NaN, Infinity])("does not turn invalid probabilities %j into zero", (value) => {
    const { seriesByParty, hung } = parsePollTrackerData([
      { segment_id: "fixture-model", as_of_date: "2024-01-01", hung: 0.5, parties: { 1: { s: 300, v: 40, vl: 35, vh: 45, pm: value, pl: value } } },
      { segment_id: "fixture-model", as_of_date: "2024-01-02", hung: value, parties: { 1: { s: 301, v: 40, vl: 35, vh: 45 } } },
    ]);
    // An invalid hung means the second run has no uncertainty, so a boundary precedes it.
    expect(seriesByParty.get("1").pMaj).toEqual([null, null, null]);
    expect(seriesByParty.get("1").pLargest).toEqual([null, null, null]);
    expect(hung).toEqual([0.5, null, null]);
  });
});

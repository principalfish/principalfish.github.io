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

  it('does not expand a single-date timeline', () => {
    const { timeline, seriesByParty } = parsePollTrackerData([
      { as_of_date: '2024-01-01', parties: { 1: { s: 300, v: 40 } } },
    ]);
    expect(timeline.map((t) => t.dateKey)).toEqual(['2024-01-01']);
    expect(timeline[0].dateValue).toBeInstanceOf(Date);
    expect(seriesByParty.get('1')).toMatchObject({
      partyName: 'Labour', colour: '#E4003B', seats: [300], votePct: [40], latestSeats: 300,
    });
  });

  it('fills every calendar day between the first and last reading and carries values forward', () => {
    const { timeline, seriesByParty } = parsePollTrackerData([
      { as_of_date: '2024-01-01', parties: { 1: { s: 300, v: 40 } } },
      { as_of_date: '2024-01-04', parties: { 1: { s: 320, v: 42 } } },
    ]);
    expect(timeline.map((t) => t.dateKey)).toEqual(['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04']);
    // Reading on the 1st, carried across the 2nd/3rd, new reading on the 4th.
    expect(seriesByParty.get('1').seats).toEqual([300, 300, 300, 320]);
    expect(seriesByParty.get('1').latestSeats).toBe(320);
  });

  it('leaves nulls before a party\'s first reading', () => {
    const { seriesByParty } = parsePollTrackerData([
      { as_of_date: '2024-01-01', parties: { 1: { s: 300, v: 40 } } },
      { as_of_date: '2024-01-04', parties: { 1: { s: 320, v: 42 }, 2: { s: 100, v: 20 } } },
    ]);
    expect(seriesByParty.get('2').seats).toEqual([null, null, null, 100]);
    expect(seriesByParty.get('2').votePct).toEqual([null, null, null, 20]);
  });

  it('falls back to the party id and default colour for parties missing from the manifest', () => {
    const { seriesByParty } = parsePollTrackerData([
      { as_of_date: '2024-01-01', parties: { 99: { s: 5, v: 1 } } },
    ]);
    expect(seriesByParty.get('99')).toMatchObject({ partyName: '99', colour: DEFAULT_PARTY_COLOUR });
  });

  it('drops party readings with non-finite seats or vote share', () => {
    const { seriesByParty } = parsePollTrackerData([
      { as_of_date: '2024-01-01', parties: { 1: { s: NaN, v: 40 }, 2: { s: 200, v: 30 } } },
    ]);
    expect(seriesByParty.has('1')).toBe(false);
    expect(seriesByParty.get('2').seats).toEqual([200]);
  });

  it("carries optional data across calendar gaps, clearing it on an explicit ordinary run", () => {
    const { seriesByParty, hung } = parsePollTrackerData([
      { as_of_date: "2024-01-01", parties: { 1: { s: 300, v: 40 } } },
      { as_of_date: "2024-01-03", hung: 0.7, parties: { 1: { s: 310, v: 41, vl: 35, vh: 45, pm: 0.3, pl: 0.9 } } },
      { as_of_date: "2024-01-05", parties: { 1: { s: 320, v: 42 } } },
      { as_of_date: "2024-01-07", hung: 0, parties: { 1: { s: 400, v: 48, vl: 40, vh: 55, pm: 1, pl: 1 } } },
    ]);
    expect(seriesByParty.get("1").voteLo).toEqual([null, null, 35, 35, null, null, 40]);
    expect(seriesByParty.get("1").voteHi).toEqual([null, null, 45, 45, null, null, 55]);
    expect(seriesByParty.get("1").pMaj).toEqual([null, null, 0.3, 0.3, null, null, 1]);
    expect(seriesByParty.get("1").pLargest).toEqual([null, null, 0.9, 0.9, null, null, 1]);
    expect(hung).toEqual([null, null, 0.7, 0.7, null, null, 0]);
  });

  it("decodes compact omitted zero odds only when the run and party have uncertainty", () => {
    const { seriesByParty } = parsePollTrackerData([
      { as_of_date: "2024-01-01", hung: 0, parties: {
        1: { s: 0, v: 1, vl: 0, vh: 2 }, 2: { s: 20, v: 10 },
      } },
      { as_of_date: "2024-01-02", parties: { 1: { s: 0, v: 1, vl: 0, vh: 2 } } },
    ]);
    expect(seriesByParty.get("1").pMaj).toEqual([0, null]);
    expect(seriesByParty.get("1").pLargest).toEqual([0, null]);
    expect(seriesByParty.get("2").pMaj).toEqual([null, null]);
  });

  it("clears a missing party's optional data even if its central value carries forward", () => {
    const { seriesByParty } = parsePollTrackerData([
      { as_of_date: "2024-01-01", hung: 1, parties: { 1: { s: 10, v: 4, vl: 1, vh: 8 } } },
      { as_of_date: "2024-01-02", hung: 1, parties: { 2: { s: 20, v: 10, vl: 5, vh: 15 } } },
      { as_of_date: "2024-01-04", parties: {} },
    ]);
    expect(seriesByParty.get("1").seats).toEqual([10, 10, 10, 10]);
    expect(seriesByParty.get("1").voteLo).toEqual([1, null, null, null]);
    expect(seriesByParty.get("2").pMaj).toEqual([null, 0, 0, null]);
  });

  it("carries seat bounds through calendar gaps and clears them on legacy or missing-party runs", () => {
    const { seriesByParty } = parsePollTrackerData([
      { as_of_date: "2024-01-01", parties: { 1: { s: 300, v: 40 } } },
      { as_of_date: "2024-01-02", hung: 0.5, parties: { 1: { s: 310, v: 41, sl: 220, sh: 376, vl: 30, vh: 50 } } },
      { as_of_date: "2024-01-04", hung: 0.5, parties: { 1: { s: 320, v: 42, vl: 31, vh: 51 } } },
      { as_of_date: "2024-01-05", hung: 0.5, parties: { 2: { s: 0, v: 1, sl: 0, sh: 2, vl: 0, vh: 3 } } },
    ]);
    expect(seriesByParty.get("1").seatLo).toEqual([null, 220, 220, null, null]);
    expect(seriesByParty.get("1").seatHi).toEqual([null, 376, 376, null, null]);
    expect(seriesByParty.get("1").voteLo).toEqual([null, 30, 30, 31, null]);
    expect(seriesByParty.get("2").seatLo).toEqual([null, null, null, null, 0]);
    expect(seriesByParty.get("2").seatHi).toEqual([null, null, null, null, 2]);
  });

  it.each([{ sl: -1, sh: 2 }, { sl: 2, sh: 1 }, { sl: 0, sh: 2.5 }, { sl: false, sh: 2 }, { sl: 0 }])(
    "ignores malformed seat bounds %j without losing valid vote bands", (fields) => {
      const { seriesByParty } = parsePollTrackerData([
        { as_of_date: "2024-01-01", hung: 1, parties: { 1: { s: 300, v: 40, vl: 30, vh: 50, ...fields } } },
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
      { as_of_date: "2024-01-01", hung: 0.5, parties: { 1: { s: 300, v: 40, ...fields } } },
    ]);
    expect(seriesByParty.get("1").voteLo).toEqual([null]);
    expect(seriesByParty.get("1").voteHi).toEqual([null]);
    expect(seriesByParty.get("1").seats).toEqual([300]);
  });

  it.each([null, "0", -0.1, 1.1, NaN, Infinity])("does not turn invalid probabilities %j into zero", (value) => {
    const { seriesByParty, hung } = parsePollTrackerData([
      { as_of_date: "2024-01-01", hung: 0.5, parties: { 1: { s: 300, v: 40, vl: 35, vh: 45, pm: value, pl: value } } },
      { as_of_date: "2024-01-02", hung: value, parties: { 1: { s: 301, v: 40, vl: 35, vh: 45 } } },
    ]);
    expect(seriesByParty.get("1").pMaj).toEqual([null, null]);
    expect(seriesByParty.get("1").pLargest).toEqual([null, null]);
    expect(hung).toEqual([0.5, null]);
  });
});

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { parsePollTrackerData } from '../scripts/polltracker.js';
import { manifest } from '../scripts/state.js';
import { DEFAULT_PARTY_COLOUR } from '../scripts/utils.js';

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
});

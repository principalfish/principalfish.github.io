import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ElectionData, manifest, Seat, state } from '../state.js';
import { buildSenateChamber, regularSenateClass } from '../features/senate-chamber.js';
import { seatLookupKey } from '../utils.js';

const readData = (file) => JSON.parse(readFileSync(new URL(`../../uselectionmaps/data/${file}`, import.meta.url), 'utf8'));
const manifestData = readData('map-modes.json');
const chamberData = readData('results/senate-current.json');
const forecastData = readData('results/us-senate-forecast.json');
const specialClasses = new Map([['florida', 3], ['ohio', 3]]);

describe('buildSenateChamber', () => {
  beforeEach(() => manifest.init(manifestData));

  it('merges the saved forecast into all 100 members, carrying over every uncontested class', () => {
    const chamber = new ElectionData(chamberData).currentSeats;
    const contested = new ElectionData(forecastData).currentSeats;
    const result = buildSenateChamber(chamber, contested, specialClasses, 2);
    const forecastByState = new Map(contested.map((seat) => [seatLookupKey(seat.seat), seat.winner]));

    expect(contested).toHaveLength(35);
    expect(result).toHaveLength(50);
    expect(result.reduce((sum, seat) => sum + seat.members.length, 0)).toBe(100);
    result.forEach((seat, index) => {
      const seatKey = seatLookupKey(seat.seat);
      const projectedParty = forecastByState.get(seatKey);
      const contestedClass = specialClasses.get(seatKey) ?? 2;
      seat.members.forEach((member, memberIndex) => {
        const original = chamber[index].members[memberIndex];
        if (original.class === contestedClass && projectedParty) {
          expect(member.party).toBe(projectedParty);
          expect(member.name).toBe(`${manifest.labelParty(projectedParty)} (projected)`);
        } else {
          expect(member).toEqual(original);
        }
      });
      expect(seat.votes).toEqual({});
      expect(seat.turnout).toBe(0);
    });
  });

  it('replaces Class 3 in Florida and Ohio while retaining their Class 1 senators', () => {
    const chamber = new ElectionData(chamberData).currentSeats;
    const contested = ['Florida', 'Ohio'].map((seat) => new Seat({ seat, winner: 'democrat', votes: {} }));
    const result = buildSenateChamber(chamber, contested, specialClasses);

    ['Florida', 'Ohio'].forEach((name) => {
      const original = chamber.find((seat) => seat.seat === name);
      const projected = result.find((seat) => seat.seat === name);
      expect(projected.members.find((member) => member.class === 3).party).toBe('democrat');
      expect(projected.members.find((member) => member.class === 1)).toEqual(
        original.members.find((member) => member.class === 1),
      );
      expect(projected.winner).toBe('split');
    });
    expect(result.find((seat) => seat.seat === 'Alabama').members).toEqual(
      chamber.find((seat) => seat.seat === 'Alabama').members,
    );
  });

  it('recomputes shared-party and split map colours for the regular class', () => {
    const chamber = new ElectionData(chamberData).currentSeats;
    const contested = [
      new Seat({ seat: 'Alabama', winner: 'democrat', votes: {} }),
      new Seat({ seat: 'Maine', winner: 'independent', votes: {} }),
    ];
    const result = buildSenateChamber(chamber, contested, new Map(), 2);
    expect(result.find((seat) => seat.seat === 'Alabama').winner).toBe('split');
    expect(result.find((seat) => seat.seat === 'Maine').winner).toBe('independent');
  });

  it('carries every member over unchanged when the regular class is unknown', () => {
    const chamber = new ElectionData(chamberData).currentSeats;
    const contested = [
      new Seat({ seat: 'Alabama', winner: 'democrat', votes: {} }),
      new Seat({ seat: 'Ohio', winner: 'democrat', votes: {} }),
    ];
    const result = buildSenateChamber(chamber, contested, specialClasses);
    // No regular class: Alabama is untouched, while Ohio's special Class-3 seat still applies.
    expect(result.find((seat) => seat.seat === 'Alabama').members).toEqual(
      chamber.find((seat) => seat.seat === 'Alabama').members,
    );
    const ohio = result.find((seat) => seat.seat === 'Ohio');
    expect(ohio.members.find((member) => member.class === 3).party).toBe('democrat');
    expect(ohio.members.find((member) => member.class === 2)).toBeUndefined();
    const plain = buildSenateChamber(chamber, contested);
    expect(plain.map((seat) => seat.members)).toEqual(chamber.map((seat) => seat.members));
  });

  it('creates independent members without mutating the chamber or contested seats', () => {
    const chamber = new ElectionData(chamberData).currentSeats;
    const contested = new ElectionData(forecastData).currentSeats;
    const beforeChamber = JSON.stringify(chamber);
    const beforeContested = JSON.stringify(contested);
    const result = buildSenateChamber(chamber, contested, specialClasses);
    expect(JSON.stringify(chamber)).toBe(beforeChamber);
    expect(JSON.stringify(contested)).toBe(beforeContested);
    result[0].members.forEach((member, index) => expect(member).not.toBe(chamber[0].members[index]));
    result[0].members[0].party = 'independent';
    expect(JSON.stringify(chamber)).toBe(beforeChamber);
  });

  it('falls back to the contested seats when the chamber snapshot is unavailable', () => {
    const contested = new ElectionData(forecastData).currentSeats;
    expect(buildSenateChamber([], contested, specialClasses)).toBe(contested);
  });
});

describe('regularSenateClass', () => {
  const withCycle = (cycle) => manifest.init({
    mapModes: { 23: { name: 'us-senate-2024', regions: [], ...(cycle ? { senateClassNextElection: cycle } : {}) } },
  });
  beforeEach(() => { state.currentElection = { id: 'x', mapId: 23 }; });
  afterEach(() => { state.currentElection = null; });

  it('is the class whose cycle lands on the year, for next and past cycles alike', () => {
    withCycle({ 1: 2030, 2: 2026, 3: 2028 });
    expect([2026, 2028, 2030, 2032, 2034, 2036].map(regularSenateClass)).toEqual([2, 3, 1, 2, 3, 1]);
    // An export made after 2026 has rolled Class 2 on, but still resolves a past 2026 cycle.
    withCycle({ 1: 2030, 2: 2032, 3: 2028 });
    expect(regularSenateClass(2026)).toBe(2);
  });

  it('is null when no class votes that year or the map has no cycle', () => {
    withCycle({ 1: 2030, 2: 2026, 3: 2028 });
    expect(regularSenateClass(2027)).toBeNull();
    expect(regularSenateClass('soon')).toBeNull();
    withCycle(null);
    expect(regularSenateClass(2026)).toBeNull();
  });
});

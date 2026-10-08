import { manifest, Seat } from '../state.js';
import { seatLookupKey } from '../utils.js';

/**
 * Merges contested winners into the current Senate while carrying over other members.
 * The regular 2026 election replaces Class 2; special elections specify another class.
 * State colours represent a shared party or a split delegation, and chamber seats have
 * no vote totals because carried-over senators did not stand in this election.
 * @param {Seat[]} chamberSeats
 * @param {Seat[]} contestedSeats
 * @param {Map<string, number>} [specialClassBySeat] - Normalised state key → contested class.
 * @returns {Seat[]}
 */
export function buildSenateChamber(chamberSeats, contestedSeats, specialClassBySeat = new Map()) {
  if (!chamberSeats.length) return contestedSeats;
  const winnerByState = new Map();
  contestedSeats.forEach((seat) => winnerByState.set(seatLookupKey(seat.seat), seat.winner));

  return chamberSeats.map((seat) => {
    const seatKey = seatLookupKey(seat.seat);
    const projectedWinner = winnerByState.get(seatKey);
    const contestedClass = specialClassBySeat.get(seatKey) ?? 2;
    const members = (seat.members || []).map((member) => {
      if (Number(member?.class) !== contestedClass || !projectedWinner) return { ...member };
      return { ...member, party: projectedWinner, name: `${manifest.labelParty(projectedWinner)} (projected)` };
    });
    const parties = members.map((member) => member.party);
    const winner = parties.length && parties.every((party) => party === parties[0]) ? parties[0] : 'split';
    return new Seat({ seat: seat.seat, region: seat.region, winner, members, votes: {} });
  });
}

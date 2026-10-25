import { manifest, Seat } from '../state.js';
import { seatLookupKey } from '../utils.js';

/**
 * Merges contested winners into the current Senate while carrying over other members.
 * The saved regular class is replaced; special elections specify another class.
 * State colours represent a shared party or a split delegation, and chamber seats have
 * no vote totals because carried-over senators did not stand in this election.
 * @param {Seat[]} chamberSeats
 * @param {Seat[]} contestedSeats
 * @param {Map<string, number>} [specialClassBySeat] - Normalised state key → contested class.
 * @param {number} [regularClass] - Saved regular class; legacy 2026 defaults to Class 2.
 * @returns {Seat[]}
 */
export function buildSenateChamber(chamberSeats, contestedSeats, specialClassBySeat = new Map(), regularClass = 2) {
  if (!chamberSeats.length) return contestedSeats;
  const winnerByState = new Map();
  contestedSeats.forEach((seat) => winnerByState.set(seatLookupKey(seat.seat), seat.winner));

  return chamberSeats.map((seat) => {
    const seatKey = seatLookupKey(seat.seat);
    const projectedWinner = winnerByState.get(seatKey);
    const contestedClass = specialClassBySeat.get(seatKey) ?? regularClass;
    const members = (seat.members || []).map((member) => {
      if (Number(member?.class) !== contestedClass || !projectedWinner) return { ...member, forecastStatus: "holdover" };
      return { ...member, forecastStatus: "contested", party: projectedWinner, name: `${manifest.labelParty(projectedWinner)} (projected)` };
    });
    const parties = members.map((member) => member.party);
    const winner = parties.length && parties.every((party) => party === parties[0]) ? parties[0] : 'split';
    return new Seat({ seat: seat.seat, region: seat.region, winner, members, votes: {} });
  });
}

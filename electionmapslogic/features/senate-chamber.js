import { activeMapMode, manifest, Seat } from '../state.js';
import { seatLookupKey } from '../utils.js';

// A Senate term: each class votes once every six years.
const SENATE_TERM_YEARS = 6;

/**
 * The Senate class a regular election in `year` contests, read from the active map's
 * `senateClassNextElection` (class → next-up year, generated at export). Each class votes
 * every six years, so the class is the one whose next-up year is a whole number of terms
 * from `year`; this holds for a past cycle as well as the next one, whatever year the
 * manifest was exported in.
 * @param {number|string} year
 * @returns {number|null} The class, or null when the map carries no cycle or no class
 *   votes in `year`.
 */
export function regularSenateClass(year) {
  const cycle = activeMapMode().senateClassNextElection || {};
  const target = Number(year);
  if (!Number.isInteger(target)) return null;
  const match = Object.entries(cycle).find(([, upYear]) => (
    Number.isInteger(Number(upYear)) && (target - Number(upYear)) % SENATE_TERM_YEARS === 0
  ));
  return match ? Number(match[0]) : null;
}

/**
 * Merges contested winners into the current Senate while carrying over other members.
 * The regular election replaces the members of `regularClass`; special elections specify
 * another class. With no `regularClass` (null: the cycle's class is unknown) only the
 * specials replace anyone and every other member carries over unchanged.
 * State colours represent a shared party or a split delegation, and chamber seats have
 * no vote totals because carried-over senators did not stand in this election. A member whose
 * race is uncalled (`c: false`, no party) is left out of the state's colour: a state with one
 * called and one uncalled member takes the called member's party (solid); a state with both
 * uncalled is drawn uncalled and neutral, neither a party's nor "split". A projection replaces
 * an uncalled member of the contested class with the projected winner like any other.
 * @param {Seat[]} chamberSeats
 * @param {Seat[]} contestedSeats
 * @param {Map<string, number>} [specialClassBySeat] - Normalised state key → contested class.
 * @param {number|null} [regularClass=null] - Class the regular election contests.
 * @returns {Seat[]}
 */
export function buildSenateChamber(chamberSeats, contestedSeats, specialClassBySeat = new Map(), regularClass = null) {
  if (!chamberSeats.length) return contestedSeats;
  const winnerByState = new Map();
  contestedSeats.forEach((seat) => winnerByState.set(seatLookupKey(seat.seat), seat.winner));

  return chamberSeats.map((seat) => {
    const seatKey = seatLookupKey(seat.seat);
    const projectedWinner = winnerByState.get(seatKey);
    const contestedClass = specialClassBySeat.get(seatKey) ?? regularClass;
    const members = (seat.members || []).map((member) => {
      if (contestedClass == null || Number(member?.class) !== contestedClass || !projectedWinner) return { ...member };
      // The projected winner is a settled member: drop the uncalled flag and count.
      const settled = { ...member, party: projectedWinner, name: `${manifest.labelParty(projectedWinner)} (projected)` };
      delete settled.c;
      delete settled.pc;
      return settled;
    });
    // Only called members colour the state; with none called it is uncalled and neutral.
    const parties = members.filter((member) => !Seat.isUncalledMember(member)).map((member) => member.party);
    const uncalled = parties.length === 0;
    const winner = parties.every((party) => party === parties[0]) ? parties[0] : 'split';
    return new Seat({
      seat: seat.seat,
      region: seat.region,
      winner: uncalled ? undefined : winner,
      called: !uncalled,
      members,
      votes: {},
    });
  });
}

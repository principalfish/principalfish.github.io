import { ElectionData, manifest, page } from "../state.js";
import { fetchJson } from "../files.js";
import { seatLookupKey } from "../utils.js";

/**
 * Loads only the named seats, once per baseline election in this call. Failed groups
 * contribute no rows, allowing the interactive predictor to keep its partial baseline.
 * @param {Array<{seat: string, baselineElectionId?: string}>} specials
 * @returns {Promise<import("../state.js").Seat[]>}
 */
export async function loadSenateSpecialBaseline(specials) {
  const specialsByBaselineId = new Map();
  specials.forEach((special) => {
    const baselineId = special?.baselineElectionId;
    if (!baselineId) return;
    if (!specialsByBaselineId.has(baselineId)) specialsByBaselineId.set(baselineId, []);
    specialsByBaselineId.get(baselineId).push(special);
  });

  const seats = [];
  for (const [baselineId, entries] of specialsByBaselineId) {
    const baselineElection = manifest.getElectionFromId(baselineId);
    if (!baselineElection) continue;
    const wanted = new Set(entries.map((special) => seatLookupKey(special?.seat)));
    try {
      const { dataFile } = manifest.resolveElectionFiles(baselineElection);
      const baselineData = await fetchJson(`${page.dataBase || "data"}/${dataFile}`);
      seats.push(...ElectionData.normalizeSeats(baselineData)
        .filter((seat) => wanted.has(seatLookupKey(seat.seat))));
    } catch (error) {
      console.error("Senate special baseline load failed", error);
    }
  }
  return seats;
}

import { manifest } from '../state.js';
import { DEFAULT_PARTY_COLOUR } from '../utils.js';
import { WESTMINSTER_DESCRIPTOR } from "../model-descriptors.js";
import { houseTrackerEntries, senateTrackerEntries } from "./polltracker-us.js";

function probability(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? value : null;
}

function optionalFields(party, hasUncertainty) {
  const lo = party?.vl;
  const hi = party?.vh;
  const hasBand = typeof lo === "number" && Number.isFinite(lo) && typeof hi === "number"
    && Number.isFinite(hi) && lo >= 0 && hi <= 100 && lo <= hi;
  const seatLo = party?.sl;
  const seatHi = party?.sh;
  const hasSeatBand = Number.isInteger(seatLo) && Number.isInteger(seatHi)
    && seatLo >= 0 && seatLo <= seatHi;
  // The compact export omits zero odds, but only runs with hung carry uncertainty.
  const odds = (key) => hasUncertainty && (hasBand || hasSeatBand)
    ? (Object.hasOwn(party, key) ? probability(party[key]) : 0) : null;
  return {
    voteLo: hasBand ? lo : null, voteHi: hasBand ? hi : null,
    seatLo: hasSeatBand ? seatLo : null, seatHi: hasSeatBand ? seatHi : null,
    pMaj: odds("pm"), pLargest: odds("pl"),
  };
}

/**
 * Parses the poll tracker JSON into a chart-ready timeline and per-party series.
 *
 * Input shape (one entry per model run; the writer guarantees one entry per as_of_date):
 *   [{ election_id, election_name, as_of_date: "YYYY-MM-DD",
 *      parties: { "<partyId>": { s: <seats>, v: <votePct>, vl?, vh?, sl?, sh?, pm?, pl? } }, hung? }]
 *
 * Pipeline:
 *   1. Flatten — explode each entry into one row per party: { partyKey, asOfDate, seats, votePct }.
 *   2. Index — bucket rows by date (timeline) and by (party, date) for the carry-forward step.
 *   3. Sort — order timeline ascending by date (lexicographic on ISO YYYY-MM-DD == chronological).
 *   4. Expand — fill in every calendar day between the first and last date, even days with no model run.
 *      Gives the chart a uniform daily x-axis instead of one tick per sparse data point.
 *   5. Carry forward — for each party, walk the dense timeline and emit (seats, votePct) for every day,
 *      reusing the last known value on days with no data so chart lines stay continuous through gaps.
 *      Days before a party's first reading remain null.
 *      Optional uncertainty clears at each explicit run unless supplied there; it carries
 *      forward only through calendar days with no run. Probabilities remain on a 0–1 scale.
 *
 * @param {Array} data - Parsed JSON array from the poll tracker data file.
 * @returns {{
 *   timeline: Array<{dateKey: string, dateValue: Date}>,
 *   seriesByParty: Map<string, {
 *     partyKey: string,
 *     partyName: string,
 *     colour: string,
 *     seats: Array<number|null>,
 *     votePct: Array<number|null>,
 *     voteLo: Array<number|null>,
 *     voteHi: Array<number|null>,
 *     seatLo: Array<number|null>,
 *     seatHi: Array<number|null>,
 *     pMaj: Array<number|null>,
 *     pLargest: Array<number|null>,
 *     latestSeats: number
 *   }>,
 *   hung: Array<number|null>
 * }} Chart-ready timeline and per-party series.
 */
export function parsePollTrackerData(data) {
  const us = !Array.isArray(data) ? houseTrackerEntries(data) ?? senateTrackerEntries(data) : null;
  const descriptor = us?.descriptor ?? WESTMINSTER_DESCRIPTOR;
  const senate = descriptor.model === "senate";
  data = us?.entries ?? (Array.isArray(data) ? data : []);
  const partiesById = manifest.partiesById;

  // 1. Flatten: one row per (entry, party).
  const rows = [];
  const runsByDate = new Map();
  for (const entry of data) {
    const asOfDate = String(entry.as_of_date || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(asOfDate)
        || !Number.isFinite(new Date(`${asOfDate}T00:00:00Z`).getTime())) continue;
    const hung = probability(entry.hung);
    const residual = us ? probability(entry.residual) : hung;
    runsByDate.set(asOfDate, { hung, residual, segment: entry.segment_id ?? null,
      senateOutcomes: entry.senateOutcomes ?? null });
    for (const [partyIdStr, pdata] of Object.entries(entry.parties || {})) {
      const partyId = Number(partyIdStr);
      const seats = Number(pdata.s);
      const votePct = senate ? null : Number(pdata.v);
      if (!Number.isFinite(partyId) || !Number.isFinite(seats)
        || (!senate && !Number.isFinite(votePct))) continue;
      rows.push({
        partyKey: String(partyId),
        asOfDate,
        seats,
        votePct,
        ...optionalFields(pdata, residual !== null),
        ...(us ? { pLargest: null } : {}),
        ...(senate ? { pMaj: null } : {}),
      });
    }
  }

  // 2. Index by date and by (party, date).
  const timelineByDateKey = new Map(Array.from(runsByDate.keys(), (dateKey) => [dateKey, { dateKey }]));
  const byParty = new Map();

  rows.forEach((row) => {
    const dateKey = row.asOfDate;
    timelineByDateKey.set(dateKey, { dateKey });
    if (!byParty.has(row.partyKey)) byParty.set(row.partyKey, new Map());
    byParty.get(row.partyKey).set(dateKey, row);
  });

  // 3. Sort: ISO date strings sort chronologically as plain strings.
  const sortedDates = Array.from(timelineByDateKey.values())
    .sort((a, b) => a.dateKey.localeCompare(b.dateKey));

  // 4. Expand: produce one entry per calendar day from first to last date inclusive.
  // UTC throughout to avoid timezone drift bumping a date to the previous/next day.
  const parseIsoDate = (value) => new Date(`${value}T00:00:00Z`);
  const formatIsoDate = (value) => {
    const year = value.getUTCFullYear();
    const month = String(value.getUTCMonth() + 1).padStart(2, '0');
    const day = String(value.getUTCDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  let timeline = (() => {
    // Single point or empty: nothing to expand, just attach dateValue.
    if (sortedDates.length <= 1) {
      return sortedDates.map((entry) => ({ dateKey: entry.dateKey, dateValue: parseIsoDate(entry.dateKey) }));
    }
    // Walk day-by-day from earliest to latest date.
    const start = parseIsoDate(sortedDates[0].dateKey);
    const end = parseIsoDate(sortedDates[sortedDates.length - 1].dateKey);
    const entries = [];
    const current = new Date(start.getTime());
    while (current.getTime() <= end.getTime()) {
      const iso = formatIsoDate(current);
      entries.push({ dateKey: iso, dateValue: new Date(current.getTime()) });
      current.setUTCDate(current.getUTCDate() + 1);
    }
    return entries;
  })();
  if (us) {
    let previous;
    timeline = timeline.flatMap((entry) => {
      const current = runsByDate.get(entry.dateKey);
      const boundary = current && previous !== undefined
        && (!current.segment || !previous || current.segment !== previous);
      if (current) previous = current.segment;
      return boundary ? [{ ...entry, boundary: true,
        dateValue: new Date(entry.dateValue.getTime() - 1) }, entry] : [entry];
    });
  }

  // 5. Carry forward: for each party, walk the dense timeline producing parallel seats/votePct arrays
  // with the last known value reused on no-data days. Days before the party's first reading stay null.
  const seriesByParty = new Map();
  byParty.forEach((rowsByDateKey, partyKey) => {
    const seats = [];
    const votePct = [];
    const optional = { voteLo: [], voteHi: [], seatLo: [], seatHi: [], pMaj: [], pLargest: [] };
    let lastOptional = optionalFields(null, false);
    let lastSeats = null;
    let lastVotePct = null;
    let lastContext = null;
    timeline.forEach((entry) => {
      const row = entry.boundary ? null : rowsByDateKey.get(entry.dateKey);
      if (!entry.boundary && runsByDate.has(entry.dateKey)) {
        lastContext = runsByDate.get(entry.dateKey).segment;
      }
      if (entry.boundary) {
        lastSeats = null;
        lastVotePct = null;
        lastOptional = optionalFields(null, false);
      }
      if (row) {
        lastSeats = Number(row.seats || 0);
        lastVotePct = senate ? null : Number(row.votePct || 0);
      }
      if (senate && !row && runsByDate.has(entry.dateKey)) {
        lastSeats = null;
        lastVotePct = null;
      }
      // Calendar gaps carry forward; an explicit run clears any missing optional values.
      if (!entry.boundary && runsByDate.has(entry.dateKey)) lastOptional = row ?? optionalFields(null, false);
      if (us && !runsByDate.has(entry.dateKey) && !lastContext) {
        lastSeats = null;
        lastVotePct = null;
        lastOptional = optionalFields(null, false);
      }
      for (const key of Object.keys(optional)) optional[key].push(lastOptional[key]);
      seats.push(lastSeats);
      votePct.push(lastVotePct);
    });

    // Resolve display name + colour from the manifest using the numeric party id.
    const manifestParty = partiesById?.get(Number(partyKey));
    seriesByParty.set(partyKey, {
      partyKey,
      partyName: manifestParty?.name || partyKey,
      colour: manifestParty?.colour || DEFAULT_PARTY_COLOUR,
      seats,
      votePct,
      ...optional,
      latestSeats: lastSeats ?? 0,
      ...(senate ? { noVotes: true } : {}),
    });
  });

  let lastHung = null;
  let lastResidual = null;
  let lastSegment = null;
  const residual = [];
  const hung = timeline.map((entry) => {
    const run = entry.boundary ? null : runsByDate.get(entry.dateKey);
    if (entry.boundary) {
      lastHung = null;
      lastResidual = null;
    } else if (run) {
      lastHung = run.hung;
      lastResidual = run.residual;
      lastSegment = run.segment;
    } else if (us && !lastSegment) lastResidual = null;
    residual.push(lastResidual);
    return lastHung;
  });
  const senateChances = { control: { democratic: [], republican: [] },
    majority: { democratic: [], republican: [] }, tied: [], unresolved: [] };
  if (senate) {
    let last = null;
    timeline.forEach((entry) => {
      const run = runsByDate.get(entry.dateKey);
      if (entry.boundary || (run && !run.segment)) last = null;
      else if (run) last = run.senateOutcomes;
      for (const caucus of ["democratic", "republican"]) {
        senateChances.control[caucus].push(last?.control[caucus] ?? null);
        senateChances.majority[caucus].push(last?.majority[caucus] ?? null);
      }
      senateChances.tied.push(last?.tied ?? null);
      senateChances.unresolved.push(last?.residual ?? null);
    });
  }
  return { timeline, seriesByParty, hung, ...(us ? { residual, descriptor } : {}),
    ...(senate ? { senateChances, hasRosterOnly: data.some((entry) => entry.rosterOnly) } : {}) };
}

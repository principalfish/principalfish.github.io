import { HOUSE_DESCRIPTOR } from "../model-descriptors.js";
import { decodeHouseSummary, validUsContext } from "../us-uncertainty.js";

/** Adapt the explicit House envelope without reinterpreting any legacy tally. */
export function houseTrackerEntries(document) {
  if (document?.schema !== "pf-us-model-trends-v1" || document.model !== "house"
    || !Array.isArray(document.points)) return null;
  const entries = [];
  for (const point of document.points) {
    const seats = point?.metrics?.house_seats;
    const votes = point?.metrics?.national_vote_share;
    if (seats?.unit !== "seats" || seats.domain !== "party" || seats.total !== 435
      || votes?.unit !== "percent" || votes.domain !== "party" || votes.total !== 100) continue;
    const known = validUsContext(point.context)
      && point.context.as_of_date === point.as_of_date;
    const segment = known && typeof point.segment_id === "string" && point.segment_id
      ? point.segment_id + JSON.stringify([
        point.context.map_id, point.context.target_cycle, point.context.matchup_id,
        point.context.field_id, point.context.baseline_id, point.context.central_options_id,
        point.context.comparability_id,
      ]) : null;
    let decoded = segment && point.summary ? decodeHouseSummary(point.summary, String) : null;
    if (decoded && Object.entries(seats.central || {}).some(([party, value]) => (
      point.summary.metrics.house_seats.central[party] !== value
      || Math.abs(point.summary.metrics.national_vote_share.central[party] - votes.central[party]) > 0.051
    ))) decoded = null;
    const parties = {};
    for (const [party, value] of Object.entries(seats.central || {})) {
      if (!Number.isInteger(value) || value < 0 || value > 435
        || typeof votes.central?.[party] !== "number" || !Number.isFinite(votes.central[party])
        || votes.central[party] < 0 || votes.central[party] > 100) continue;
      const optional = decoded?.parties[party];
      parties[party] = {
        s: value, v: votes.central[party],
        ...(optional ? { sl: optional.s[0], sh: optional.s[2],
          vl: optional.v[0], vh: optional.v[1], pm: optional.pm } : {}),
      };
    }
    if (!Object.keys(parties).length) continue;
    entries.push({ ...point, parties, segment_id: segment,
      residual: decoded?.residual ?? null, house: true });
  }
  return { entries, descriptor: HOUSE_DESCRIPTOR };
}

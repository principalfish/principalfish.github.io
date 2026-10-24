import { HOUSE_DESCRIPTOR, SENATE_DESCRIPTOR, PRESIDENT_DESCRIPTOR } from "../model-descriptors.js";
import { decodeHouseSummary, decodeSenateSummary, decodePresidentSummary, presidentDisplay, validUsContext } from "../us-uncertainty.js";

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
    const known = validUsContext(point.context) && point.context.model === "house"
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

/** A historical contested field is never relabelled as a 100-seat chamber. */
export function senateTrackerEntries(document) {
  if (document?.schema !== "pf-us-model-trends-v1" || document.model !== "senate"
    || !Array.isArray(document.points)) return null;
  const entries = [];
  for (const point of document.points) {
    const seats = point?.metrics?.senate_full_seats;
    const known = validUsContext(point.context) && point.context.model === "senate"
      && point.context.as_of_date === point.as_of_date;
    const segment = known && typeof point.segment_id === "string" && point.segment_id
      ? point.segment_id + JSON.stringify([
        point.context.map_id, point.context.target_cycle, point.context.matchup_id,
        point.context.field_id, point.context.baseline_id, point.context.central_options_id,
        point.context.comparability_id, point.context.roster_id, point.context.control_policy_id,
      ]) : null;
    const basis = point.central_basis;
    const asOf = new Date(`${point.as_of_date}T00:00:00Z`);
    const rosterOnly = point.context == null && point.segment_id == null && point.summary == null
      && basis?.schema === "pf-us-senate-central-roster-v1"
      && Number.isInteger(basis.map_id) && basis.map_id > 0
      && basis.as_of_date === point.as_of_date
      && Number.isFinite(asOf.getTime()) && asOf.toISOString().slice(0, 10) === point.as_of_date
      && Number.isInteger(basis.target_cycle) && basis.target_cycle > 0 && basis.target_cycle % 2 === 0
      && ["roster_id", "field_id"].every((key) => typeof basis[key] === "string" && basis[key].trim());
    const parties = {};
    const central = seats?.central;
    const valid = (segment || rosterOnly) && seats?.unit === "seats" && seats.domain === "party" && seats.total === 100
      && central && Object.keys(central).length && Object.entries(central).every(([id, value]) => (
        /^[1-9]\d*$/.test(id) && Number.isInteger(value) && value >= 0 && value <= 100
      )) && Object.values(central).reduce((sum, value) => sum + value, 0) === 100;
    let decoded = segment && valid && point.summary ? decodeSenateSummary(point.summary, String) : null;
    if (decoded && (Object.keys(central).length !== Object.keys(decoded.full.central).length
      || Object.entries(central).some(([party, value]) => decoded.full.central[party] !== value))) decoded = null;
    if (valid) {
      for (const [party, value] of Object.entries(central)) {
        const optional = decoded?.full.parties[party];
        parties[party] = { s: value, v: null,
          ...(optional ? { sl: optional.s[0], sh: optional.s[2] } : {}) };
      }
    }
    // Keep unsupported dates as explicit gaps, clearing earlier totals and odds.
    entries.push({ ...point, parties, segment_id: valid ? segment : null,
      residual: decoded?.residual ?? null, senateOutcomes: decoded,
      rosterOnly: Boolean(valid && rosterOnly) });
  }
  return { entries, descriptor: SENATE_DESCRIPTOR };
}

/** Legacy e is an EV central projection; legacy s is only a count of map units. */
export function presidentTrackerEntries(document) {
  if (document?.schema !== "pf-us-model-trends-v1" || document.model !== "president"
    || !Array.isArray(document.points)) return null;
  const entries = [];
  for (const point of document.points) {
    const legacy = point.metrics == null;
    const electoral = legacy ? { unit: "electoral_votes", domain: "party", total: 538,
      central: Object.fromEntries(Object.entries(point.parties || {}).map(([id, row]) => [id, row.e])) }
      : point.metrics.electoral_votes;
    const votes = legacy ? { unit: "percent", domain: "party", total: 100,
      central: Object.fromEntries(Object.entries(point.parties || {}).map(([id, row]) => [id, row.v])) }
      : point.metrics.national_vote_share;
    const ids = Object.keys(electoral?.central || {});
    const valid = electoral?.unit === "electoral_votes" && electoral.domain === "party" && electoral.total === 538
      && votes?.unit === "percent" && votes.domain === "party" && votes.total === 100
      && ids.length && ids.length === Object.keys(votes.central || {}).length
      && ids.every((id) => /^[1-9]\d*$/.test(id) && Number.isInteger(electoral.central[id])
        && electoral.central[id] >= 0 && electoral.central[id] <= 538
        && typeof votes.central[id] === "number" && Number.isFinite(votes.central[id])
        && votes.central[id] >= 0 && votes.central[id] <= 100)
      && ids.reduce((sum, id) => sum + electoral.central[id], 0) === 538
      && Math.abs(ids.reduce((sum, id) => sum + votes.central[id], 0) - 100) <= 0.051 * ids.length;
    const display = presidentDisplay(point.display, point.context);
    const known = valid && display && validUsContext(point.context) && point.context.model === "president"
      && point.context.as_of_date === point.as_of_date;
    const segment = known && typeof point.segment_id === "string" && point.segment_id.trim()
      ? point.segment_id + JSON.stringify([
        point.context.map_id, point.context.target_cycle, point.context.matchup_id,
        point.context.field_id, point.context.baseline_id, point.context.central_options_id,
        point.context.comparability_id, point.context.electoral_allocation_id, display.matchup,
      ]) : null;
    let decoded = segment && point.summary ? decodePresidentSummary(point.summary, String) : null;
    if (decoded && (ids.length !== Object.keys(decoded.parties).length
      || ids.some((id) => point.summary.metrics.electoral_votes.central[id] !== electoral.central[id]
        || Math.abs(point.summary.metrics.national_vote_share.central[id] - votes.central[id]) > 0.051))) decoded = null;
    const parties = {};
    if (valid) {
      for (const id of ids) {
        const optional = decoded?.parties[id];
        parties[id] = { s: electoral.central[id], v: votes.central[id],
          ...(optional ? { sl: optional.s[0], sh: optional.s[2],
            vl: optional.v[0], vh: optional.v[1], pm: optional.pm } : {}) };
      }
    }
    entries.push({ ...point, parties, segment_id: segment,
      display: segment ? display : null, residual: decoded?.residual ?? null });
  }
  return { entries, descriptor: PRESIDENT_DESCRIPTOR };
}

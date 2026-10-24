import { HOUSE_DESCRIPTOR, SENATE_DESCRIPTOR } from "./model-descriptors.js";

const finite = (value, max) => typeof value === "number" && Number.isFinite(value)
  && value >= 0 && value <= max;
const ids = (value) => value && typeof value === "object" && !Array.isArray(value)
  ? Object.keys(value) : [];
const sameIds = (left, right) => left.length === right.length
  && left.every((key) => right.includes(key));

function range(value, length, max, integer = false) {
  return Array.isArray(value) && value.length === length
    && value.every((v, index) => finite(v, max) && (!integer || Number.isInteger(v))
      && (index === 0 || value[index - 1] <= v));
}

export function validUsContext(context) {
  const date = new Date(`${context?.as_of_date}T00:00:00Z`);
  return ["house", "senate"].includes(context?.model) && Number.isInteger(context.map_id) && context.map_id > 0
    && Number.isInteger(context.target_cycle) && context.target_cycle > 0
    && context.target_cycle % 2 === 0
    && /^\d{4}-\d{2}-\d{2}$/.test(context.as_of_date)
    && Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === context.as_of_date
    && ["matchup_id", "field_id", "baseline_id", "central_options_id", "comparability_id"]
      .every((key) => typeof context[key] === "string" && context[key].trim())
    && (context.model !== "senate" || ["roster_id", "control_policy_id"]
      .every((key) => typeof context[key] === "string" && context[key].trim()));
}

/** The strict House core is shared by current-result and trend adapters. */
export function decodeHouseSummary(summary, resolveParty) {
  const seats = summary?.metrics?.house_seats;
  const votes = summary?.metrics?.national_vote_share;
  const parties = ids(seats?.central);
  const majority = summary?.outcomes?.majority;
  if (!Number.isInteger(summary?.simulations) || summary.simulations <= 0
    || !finite(summary.interval, 1) || summary.interval === 0 || summary.interval === 1
    || seats?.unit !== "seats" || seats.domain !== "party" || seats.total !== 435
    || votes?.unit !== "percent" || votes.domain !== "party" || votes.total !== 100
    || !parties.length || !parties.every((id) => /^[1-9]\d*$/.test(id))
    || !sameIds(parties, ids(seats.ranges)) || !sameIds(parties, ids(votes.central))
    || !sameIds(parties, ids(votes.ranges)) || majority?.domain !== "party"
    || !sameIds(parties, ids(majority.probabilities))) return null;
  const output = {};
  for (const id of parties) {
    if (!Number.isInteger(seats.central[id]) || !finite(seats.central[id], 435)
      || !range(seats.ranges[id], 3, 435, true) || !finite(votes.central[id], 100)
      || !range(votes.ranges[id], 3, 100) || !finite(majority.probabilities[id], 1)) return null;
    output[resolveParty(id)] = {
      s: [...seats.ranges[id]], v: [votes.ranges[id][0], votes.ranges[id][2]],
      pm: majority.probabilities[id], pl: null,
    };
  }
  const residual = summary.outcomes.no_majority;
  if (parties.reduce((sum, id) => sum + seats.central[id], 0) !== 435
    || Math.abs(parties.reduce((sum, id) => sum + votes.central[id], 0) - 100) > 0.005 * parties.length + 1e-8
    || !finite(residual, 1)
    || Math.abs(parties.reduce((sum, id) => sum + majority.probabilities[id], residual) - 1) > 1e-8) return null;
  return { parties: output, residual };
}

/** Raw parties count seats; control and majority belong to the separate caucus domain. */
export function decodeSenateSummary(summary, resolveParty) {
  if (!Number.isInteger(summary?.simulations) || summary.simulations <= 0
    || !finite(summary.interval, 1) || summary.interval === 0 || summary.interval === 1
    || !sameIds(ids(summary.metrics), ["senate_contested_seats", "senate_full_seats"])) return null;
  const blocks = {};
  for (const key of ["senate_contested_seats", "senate_full_seats"]) {
    const metric = summary.metrics[key];
    const parties = ids(metric?.central);
    const total = metric?.total;
    if (metric?.unit !== "seats" || metric.domain !== "party" || !Number.isInteger(total)
      || (key === "senate_full_seats" ? total !== 100 : total < 1 || total > 100)
      || !parties.length || !parties.every((id) => /^[1-9]\d*$/.test(id))
      || !sameIds(parties, ids(metric.ranges))
      || parties.reduce((sum, id) => sum + metric.central[id], 0) !== total) return null;
    const output = {};
    const central = {};
    for (const id of parties) {
      if (!Number.isInteger(metric.central[id]) || !finite(metric.central[id], total)
        || !range(metric.ranges[id], 3, total, true)) return null;
      output[resolveParty(id)] = { s: [...metric.ranges[id]], v: null, pm: null, pl: null };
      central[resolveParty(id)] = metric.central[id];
    }
    blocks[key] = { parties: output, central };
  }
  const outcomes = summary.outcomes;
  const caucuses = ["democratic", "republican"];
  for (const key of ["control", "majority"]) {
    const row = outcomes?.[key];
    if (row?.domain !== "caucus" || !sameIds(ids(row.probabilities), caucuses)
      || !caucuses.every((id) => finite(row.probabilities[id], 1))) return null;
  }
  if (!["unresolved_control", "no_caucus_majority", "tied_caucuses"]
    .every((key) => finite(outcomes[key], 1))
    || Math.abs(caucuses.reduce((sum, id) => sum + outcomes.control.probabilities[id], outcomes.unresolved_control) - 1) > 1e-8
    || Math.abs(caucuses.reduce((sum, id) => sum + outcomes.majority.probabilities[id], outcomes.no_caucus_majority) - 1) > 1e-8
    || outcomes.tied_caucuses > outcomes.no_caucus_majority + 1e-8) return null;
  return { contested: blocks.senate_contested_seats, full: blocks.senate_full_seats,
    control: { ...outcomes.control.probabilities }, majority: { ...outcomes.majority.probabilities },
    residual: outcomes.unresolved_control, tied: outcomes.tied_caucuses,
    noMajority: outcomes.no_caucus_majority };
}

function decodeRegions(extension, resolveParty, regionsById, currentSeats) {
  if (extension == null) return {};
  if (extension.schema !== "pf-us-public-regions-v1") return null;
  const result = {};
  for (const [id, block] of Object.entries(extension.regions || {})) {
    const region = regionsById.get(Number(id));
    if (!region || !Array.isArray(block?.members) || !block.members.length
      || !block.members.every((name) => typeof name === "string" && name.length)
      || new Set(block.members).size !== block.members.length) return null;
    if (currentSeats) {
      const actual = currentSeats.filter((seat) => seat.region === region).map((seat) => seat.seat).sort();
      if (JSON.stringify(actual) !== JSON.stringify([...block.members].sort())) continue;
    }
    const parties = {};
    for (const [party, row] of Object.entries(block.parties || {})) {
      if (!/^[1-9]\d*$/.test(party) || !range(row?.s, 3, block.members.length, true)
        || !range(row?.v, 2, 100)) return null;
      parties[resolveParty(party)] = { s: [...row.s], v: [...row.v], pm: null, pl: null };
    }
    if (!Object.keys(parties).length) return null;
    result[region] = { parties, residual: null };
  }
  return result;
}

export function decodeUsUncertainty(payload, identity, resolveParty, regionsById, options) {
  if (!["house", "senate"].includes(options.model) || payload?.schema !== "pf-us-public-uncertainty-v1"
    || payload.model !== options.model || payload.context?.model !== options.model
    || payload.uncertaintyId !== identity || !identity
    || !validUsContext(payload.context) || typeof payload.result_identity !== "string"
    || (options.mapId != null && payload.context.map_id !== Number(options.mapId))) return null;
  if (options.model === "senate") {
    const decoded = decodeSenateSummary(payload.summary, resolveParty);
    if (!decoded || payload.regional_bands != null) return null;
    return { ...payload, interval: payload.summary.interval, simulations: payload.summary.simulations,
      national: decoded.contested, full: decoded.full, outcomes: decoded, regions: {},
      descriptor: SENATE_DESCRIPTOR,
      basis: `Nowcast: if an election were held today. ${payload.summary.interval * 100}% ranges and caucus-control probabilities from ${payload.summary.simulations.toLocaleString("en-US")} simulations, conditional on current polls, the saved roster and control policy, and the model's error assumptions.`,
    };
  }
  const national = decodeHouseSummary(payload.summary, resolveParty);
  if (!national) return null;
  const regions = decodeRegions(payload.regional_bands, resolveParty, regionsById, options.seats);
  if (regions === null) return null;
  return {
    ...payload, interval: payload.summary.interval, simulations: payload.summary.simulations,
    national, regions, descriptor: HOUSE_DESCRIPTOR,
    basis: `Nowcast: if an election were held today. ${payload.summary.interval * 100}% ranges and probabilities from ${payload.summary.simulations.toLocaleString("en-US")} simulations, conditional on current polls and the model's error assumptions.`,
  };
}

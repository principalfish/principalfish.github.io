// Published uncertainty is optional. Keep decoding and display rules independent of the DOM.

function finite(value, min = 0, max = Infinity) {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
}

function validRange(values, length, max = Infinity) {
  return Array.isArray(values) && values.length === length
    && values.every((value, index) => finite(value, 0, max)
      && (index === 0 || value >= values[index - 1]));
}

/** Normalize compact seat rows, or defensively copy an already normalized party map. */
export function decodeSeatUncertainty(value, resolveParty) {
  const entries = Array.isArray(value)
    ? value.map((row) => Array.isArray(row) ? [row[0], row.slice(1)] : [])
    : Object.entries(value || {});
  const result = {};
  for (const [party, band] of entries) {
    if (party == null || !Array.isArray(band) || band.length !== 3
        || !validRange(band.slice(0, 2), 2, 100) || !finite(band[2], 0, 1)) continue;
    result[resolveParty(party)] = [...band];
  }
  return result;
}

/** Decode public identifiers once, when a matching summary arrives. */
export function decodeUncertainty(payload, identity, resolveParty, regionsById) {
  if (!identity || payload?.schema !== "pf-uncertainty-v1"
      || payload.uncertaintyId !== identity || !finite(payload.interval, 0, 1)
      || payload.interval === 0 || !Number.isInteger(payload.simulations)
      || payload.simulations <= 0 || !payload.national?.parties) return null;
  function decodeBlock(block) {
    const parties = {};
    for (const [id, row] of Object.entries(block?.parties || {})) {
      if (!validRange(row?.v, 2, 100) || !validRange(row?.s, 3)) continue;
      parties[resolveParty(id)] = {
        v: [...row.v], s: [...row.s],
        pm: finite(row.pm, 0, 1) ? row.pm : null,
        pl: finite(row.pl, 0, 1) ? row.pl : null,
      };
    }
    return { parties, hung: finite(block?.hung, 0, 1) ? block.hung : null };
  }
  const national = decodeBlock(payload.national);
  if (!Object.keys(national.parties).length) return null;
  const regions = {};
  for (const [id, block] of Object.entries(payload.regions || {})) {
    const region = regionsById.get(Number(id));
    if (region) regions[region] = decodeBlock(block);
  }
  return { ...payload, national, regions };
}

/** Only the saved model result can use its uncertainty, never a user-created scenario. */
export function activeUncertainty(state) {
  return state.view === "election" && state.currentElection?.model
    ? state.electionData?.uncertainty ?? null : null;
}

/** Whole-country and exactly-one-region totals have stored ranges; arbitrary slices do not. */
export function uncertaintyForTotals(state) {
  const summary = activeUncertainty(state);
  const filters = state.mapFilters;
  if (!summary || state.voteTotals.mode !== "all" || filters.party !== "all"
      || filters.secondParty !== "all" || filters.majorityMin !== 0
      || filters.majorityMax !== 100 || filters.gainsOnly || filters.upcoming !== "all"
      || typeof filters.region !== "string") return null;
  return filters.region === "all" ? summary.national : summary.regions[filters.region] ?? null;
}

export function uncertaintyNumber(value) {
  return Number(value.toFixed(2)).toString();
}

export function uncertaintyRange(values, suffix = "") {
  if (!Array.isArray(values) || values.length < 2) return "—";
  return `${uncertaintyNumber(values[0])}–${uncertaintyNumber(values.at(-1))}${suffix}`;
}

/** Text only: the caller escapes it or assigns textContent. */
export function seatUncertaintyText(band) {
  if (!band) return "";
  return `(${uncertaintyRange(band.slice(0, 2))}%) · win ${uncertaintyNumber(band[2] * 100)}%`;
}

export function uncertaintyHeadline(summary, labelParty) {
  if (!summary) return "";
  const leaders = Object.entries(summary.national.parties)
    .filter(([, row]) => row.pl !== null && row.pm !== null)
    .sort((a, b) => b[1].pl - a[1].pl || labelParty(a[0]).localeCompare(labelParty(b[0])));
  if (!leaders.length || summary.national.hung === null) return "";
  const [party, row] = leaders[0];
  return `${labelParty(party)} largest party ${uncertaintyNumber(row.pl * 100)}% · `
    + `${labelParty(party)} majority ${uncertaintyNumber(row.pm * 100)}% · `
    + `Hung ${uncertaintyNumber(summary.national.hung * 100)}% (if an election were held today)`;
}
